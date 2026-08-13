import json
import os
from datetime import datetime

import httpx
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.security import OAuth2PasswordRequestForm
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlmodel import Session, select

from auth import (
    create_access_token,
    get_current_admin,
    get_current_manager,
    get_current_user,
    hash_password,
    verify_password,
)
from database.session import create_db_and_tables, get_session
from models.document import Document, DocumentUpdate
from models.user import User, UserCreate, UserResponse
from services.weather import get_weather

load_dotenv()


app = FastAPI(title="SendIt API", version="1.0.0")


UPLOAD_DIR = "uploads"

os.makedirs(UPLOAD_DIR, exist_ok=True)


MAX_FILE_SIZE = int(os.getenv("MAX_UPLOAD_SIZE", 5 * 1024 * 1024))

ALLOWED_EXTENSIONS = [".pdf", ".jpg", ".jpeg", ".png", ".docx"]


limiter = Limiter(key_func=get_remote_address)

app.state.limiter = limiter

app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.on_event("startup")
def startup():
    create_db_and_tables()


# ============================================================
# AUTHENTICATION
# ============================================================


@app.post("/register", response_model=UserResponse)
def register(user_data: UserCreate, session: Session = Depends(get_session)):

    existing_username = session.exec(
        select(User).where(User.username == user_data.username)
    ).first()

    if existing_username:
        raise HTTPException(status_code=400, detail="Username already exists")

    existing_email = session.exec(
        select(User).where(User.email == user_data.email)
    ).first()

    if existing_email:
        raise HTTPException(status_code=400, detail="Email already exists")

    if user_data.role not in ["admin", "manager", "staff"]:
        raise HTTPException(status_code=400, detail="Invalid role")

    user = User(
        username=user_data.username,
        email=user_data.email,
        hashed_password=hash_password(user_data.password),
        full_name=user_data.full_name,
        role=user_data.role,
    )

    session.add(user)
    session.commit()
    session.refresh(user)

    return user


@app.post("/login")
def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    session: Session = Depends(get_session),
):

    user = session.exec(select(User).where(User.username == form_data.username)).first()

    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Incorrect username or password")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="User is inactive")

    user.last_login = datetime.utcnow()

    session.add(user)
    session.commit()

    token = create_access_token({"sub": user.username})

    return {"access_token": token, "token_type": "bearer"}


@app.get("/me")
def get_me(current_user: User = Depends(get_current_user)):
    return current_user


# ============================================================
# FILE UPLOAD
# ============================================================


@app.post("/documents/upload")
@limiter.limit("10/hour")
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    city: str = Form(...),
    description: str | None = Form(None),
    country: str = Form("Kenya"),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):

    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename is required")

    file_extension = os.path.splitext(file.filename)[1].lower()

    if file_extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=(
                "File type not allowed. " f"Allowed: {', '.join(ALLOWED_EXTENSIONS)}"
            ),
        )

    contents = await file.read()

    file_size = len(contents)

    if file_size > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=400,
            detail=(
                "File too large. "
                f"Maximum size is "
                f"{MAX_FILE_SIZE // (1024 * 1024)} MB"
            ),
        )

    # Versioning
    existing_documents = session.exec(
        select(Document).where(
            Document.original_filename == file.filename,
            Document.uploader_id == current_user.id,
        )
    ).all()

    version = len(existing_documents) + 1

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    safe_name = file.filename.replace(" ", "_")

    safe_filename = f"{timestamp}_" f"{current_user.id}_" f"v{version}_" f"{safe_name}"

    file_path = os.path.join(UPLOAD_DIR, safe_filename)

    with open(file_path, "wb") as buffer:
        buffer.write(contents)

    document = Document(
        filename=safe_filename,
        original_filename=file.filename,
        file_size=file_size,
        file_type=(file.content_type or "application/octet-stream"),
        city=city,
        country=country,
        description=description,
        uploader_id=current_user.id,
        file_path=file_path,
        status="processing",
        version=version,
    )

    session.add(document)
    session.commit()
    session.refresh(document)

    try:

        weather_data = await get_weather(city, country)

        if weather_data:
            document.weather_data = json.dumps(weather_data)

            document.weather_fetched_at = datetime.utcnow()

            document.status = "enriched"

        else:
            document.status = "uploaded"

    except Exception as e:

        print(f"Weather API error: {e}")

        document.status = "uploaded"

    document.updated_at = datetime.utcnow()

    session.add(document)
    session.commit()
    session.refresh(document)

    return {
        "message": "Document uploaded successfully",
        "document_id": document.id,
        "filename": document.original_filename,
        "version": document.version,
        "status": document.status,
    }


# ============================================================
# DOCUMENT LIST
# ============================================================


@app.get("/documents")
@limiter.limit("30/minute")
def list_documents(
    request: Request,
    status: str | None = None,
    city: str | None = None,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):

    query = select(Document)

    if current_user.role not in ["admin", "manager"]:
        query = query.where(Document.uploader_id == current_user.id)

    if status:
        query = query.where(Document.status == status)

    if city:
        query = query.where(Document.city == city)

    return session.exec(query).all()


# ============================================================
# SEARCH
# ============================================================


@app.get("/documents/search")
@limiter.limit("20/minute")
def search_documents(
    request: Request,
    q: str | None = None,
    city: str | None = None,
    status: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):

    query = select(Document)

    if current_user.role not in ["admin", "manager"]:
        query = query.where(Document.uploader_id == current_user.id)

    if q:
        query = query.where(Document.original_filename.contains(q))

    if city:
        query = query.where(Document.city == city)

    if status:
        query = query.where(Document.status == status)

    if date_from:
        query = query.where(Document.uploaded_at >= date_from)

    if date_to:
        query = query.where(Document.uploaded_at <= date_to)

    return session.exec(query).all()


# ============================================================
# GET ONE DOCUMENT
# ============================================================


@app.get("/documents/{document_id}")
@limiter.limit("30/minute")
def get_document(
    request: Request,
    document_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):

    document = session.get(Document, document_id)

    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    if (
        current_user.role not in ["admin", "manager"]
        and document.uploader_id != current_user.id
    ):
        raise HTTPException(status_code=403, detail="Access denied")

    return document


# ============================================================
# UPDATE DOCUMENT
# ============================================================


@app.put("/documents/{document_id}")
def update_document(
    document_id: int,
    document_data: DocumentUpdate,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):

    document = session.get(Document, document_id)

    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    if (
        current_user.role not in ["admin", "manager"]
        and document.uploader_id != current_user.id
    ):
        raise HTTPException(status_code=403, detail="Access denied")

    if document_data.city is not None:
        document.city = document_data.city

    if document_data.country is not None:
        document.country = document_data.country

    if document_data.description is not None:
        document.description = document_data.description

    document.updated_at = datetime.utcnow()

    session.add(document)
    session.commit()
    session.refresh(document)

    return document


# ============================================================
# DELETE DOCUMENT
# ============================================================


@app.delete("/documents/{document_id}")
def delete_document(
    document_id: int,
    current_user: User = Depends(get_current_manager),
    session: Session = Depends(get_session),
):

    document = session.get(Document, document_id)

    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    if os.path.exists(document.file_path):
        os.remove(document.file_path)

    session.delete(document)
    session.commit()

    return {"message": "Document deleted successfully"}


# ============================================================
# MANUAL WEATHER ENRICHMENT
# ============================================================


@app.post("/documents/{document_id}/enrich")
@limiter.limit("5/minute")
async def enrich_document(
    request: Request,
    document_id: int,
    current_user: User = Depends(get_current_manager),
    session: Session = Depends(get_session),
):

    document = session.get(Document, document_id)

    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    if document.status == "enriched":
        return {"message": "Document already enriched"}

    weather_data = await get_weather(document.city, document.country)

    if weather_data and "error" not in weather_data:

        document.weather_data = json.dumps(weather_data)

        document.weather_fetched_at = datetime.utcnow()

        document.status = "enriched"
        document.updated_at = datetime.utcnow()

        session.add(document)
        session.commit()

        return {"message": "Document enriched successfully", "weather": weather_data}

    document.status = "failed"

    session.add(document)
    session.commit()

    raise HTTPException(status_code=500, detail="Failed to enrich document")


# ============================================================
# GET WEATHER
# ============================================================


@app.get("/documents/{document_id}/weather")
@limiter.limit("10/minute")
def get_document_weather(
    request: Request,
    document_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):

    document = session.get(Document, document_id)

    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    if (
        current_user.role not in ["admin", "manager"]
        and document.uploader_id != current_user.id
    ):
        raise HTTPException(status_code=403, detail="Access denied")

    if not document.weather_data:
        raise HTTPException(status_code=404, detail="No weather data available")

    return {
        "document_id": document.id,
        "city": document.city,
        "country": document.country,
        "weather": json.loads(document.weather_data),
    }


# ============================================================
# HEALTH CHECK
# ============================================================


@app.get("/")
def root():
    return {"message": "Welcome to SendIt API", "status": "running"}


# ============================================================
# WEBHOOK NOTIFICATIONS
# ============================================================

webhooks = []


@app.post("/webhooks/register")
def register_webhook(
    webhook_url: str, event_type: str, current_user: User = Depends(get_current_admin)
):
    if event_type not in ["document.enriched", "document.uploaded"]:
        raise HTTPException(status_code=400, detail="Invalid event type")

    webhook = {
        "webhook_url": webhook_url,
        "event_type": event_type,
        "registered_by": current_user.username,
    }

    webhooks.append(webhook)

    return {"message": "Webhook registered successfully", "webhook": webhook}


async def send_webhook_notification(event_type: str, document: Document):
    payload = {
        "event": event_type,
        "document_id": document.id,
        "filename": document.original_filename,
        "status": document.status,
        "city": document.city,
    }

    async with httpx.AsyncClient() as client:
        for webhook in webhooks:
            if webhook["event_type"] == event_type:
                try:
                    await client.post(webhook["webhook_url"], json=payload, timeout=5)
                except Exception as e:
                    print(f"Webhook failed: {e}")


# ============================================================
# MONITORING AND LOGGING - LAB 10
# ============================================================

import logging
import platform
import time
from logging.handlers import RotatingFileHandler

import psutil

start_time = time.time()

LOG_FILE = os.getenv("LOG_FILE", "app.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        RotatingFileHandler(LOG_FILE, maxBytes=10485760, backupCount=5),
        logging.StreamHandler(),
    ],
)

logger = logging.getLogger(__name__)


@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "version": "1.0.0",
        "uptime": time.time() - start_time,
        "system": {
            "platform": platform.platform(),
            "python": platform.python_version(),
        },
    }


@app.get("/metrics")
def get_metrics(current_user: User = Depends(get_current_admin)):
    return {
        "cpu_percent": psutil.cpu_percent(),
        "memory_percent": psutil.virtual_memory().percent,
        "disk_usage": psutil.disk_usage("/").percent,
    }


@app.middleware("http")
async def log_requests(request: Request, call_next):
    request_start = time.time()

    response = await call_next(request)

    process_time = time.time() - request_start

    logger.info(
        f"{request.method} {request.url.path} - "
        f"Status: {response.status_code} - "
        f"Time: {process_time:.3f}s"
    )

    return response
