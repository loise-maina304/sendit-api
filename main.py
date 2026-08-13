import json
import os
from datetime import datetime

import httpx
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
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


@app.get("/", response_class=HTMLResponse)
async def portfolio():
    return """
<!DOCTYPE html>
<html>
<head>
    <title>Loise Maina - Backend Development Portfolio</title>
    <style>
        body {
            font-family: 'Segoe UI', Arial, sans-serif;
            margin: 40px;
            background: #f5f5f5;
        }
        .container {
            max-width: 900px;
            margin: 0 auto;
            background: white;
            padding: 30px;
            border-radius: 10px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }
        h1 {
            color: #2c3e50;
            border-bottom: 3px solid #3498db;
            padding-bottom: 10px;
        }
        .student-info {
            background: #e8f4fd;
            padding: 15px;
            border-radius: 8px;
            margin: 20px 0;
        }
        .admission {
            font-size: 1.2em;
            color: #2980b9;
            font-weight: bold;
        }
        .assignment {
            margin: 12px 0;
            padding: 15px;
            background: #f8f9fa;
            border-radius: 8px;
            border-left: 4px solid #3498db;
        }
        .assignment:hover {
            background: #e8f4fd;
        }
        .assignment a {
            color: #0366d6;
            text-decoration: none;
            font-weight: 500;
            display: block;
        }
        .badge {
            display: inline-block;
            background: #3498db;
            color: white;
            padding: 2px 10px;
            border-radius: 12px;
            font-size: 0.8em;
            margin-right: 10px;
        }
        .lesson-topic {
            color: #7f8c8d;
            font-size: 0.9em;
            margin-top: 5px;
        }
        .footer {
            margin-top: 30px;
            text-align: center;
            color: #95a5a6;
            font-size: 0.9em;
            border-top: 1px solid #ecf0f1;
            padding-top: 20px;
        }
    </style>
</head>
<body>
<div class="container">

<h1>📚 Backend Development Portfolio</h1>

<div class="student-info">
    <p><strong>Student Name:</strong> Loise Maina</p>
    <p>🎓 <strong>Admission Number:</strong>
    <span class="admission">C027-01-0852/2024</span></p>
    <p>📧 <strong>Email:</strong> loise.maina24@students.dkut.ac.ke</p>
</div>

<h2>📝 Backend Assignments</h2>

<p style="color:#7f8c8d;">
    Click on any assignment to view the complete code on GitHub.
</p>

<div class="assignment">
<a href="https://github.com/loise-maina304/cit-backend-course" target="_blank">
<span class="badge">Lesson 1</span>
HTTP & Your First API
<div class="lesson-topic">— FastAPI + Uvicorn, HTTP Methods, Status Codes</div>
</a>
</div>

<div class="assignment">
<a href="https://github.com/loise-maina304/cit-backend-course" target="_blank">
<span class="badge">Lesson 2</span>
Docker - Packaging Your API
<div class="lesson-topic">— Containers, Dockerfiles, Docker Compose</div>
</a>
</div>

<div class="assignment">
<a href="https://github.com/loise-maina304/cit-backend-course" target="_blank">
<span class="badge">Lesson 3</span>
Routing, Parameters & Request Bodies
<div class="lesson-topic">— Path Parameters, Query Parameters, Pydantic Validation</div>
</a>
</div>

<div class="assignment">
<a href="https://github.com/loise-maina304/library-api" target="_blank">
<span class="badge">Lesson 4</span>
PostgreSQL & SQLModel – Your First Database
<div class="lesson-topic">— ORM, Database Migrations, SQLModel</div>
</a>
</div>

<div class="assignment">
<a href="https://github.com/loise-maina304/product-api" target="_blank">
<span class="badge">Lesson 5</span>
CRUD Operations
<div class="lesson-topic">— Create, Read, Update, Delete with Error Handling</div>
</a>
</div>

<div class="assignment">
<a href="https://github.com/loise-maina304/product-api" target="_blank">
<span class="badge">Lesson 6</span>
Error Handling & Validation
<div class="lesson-topic">— HTTPException, Custom Validators, Global Handlers</div>
</a>
</div>

<div class="assignment">
<a href="https://github.com/loise-maina304/clinicguard-api" target="_blank">
<span class="badge">Lesson 7</span>
User Authentication – JWT & Password Hashing
<div class="lesson-topic">— JWT Tokens, Password Hashing, Login/Register Endpoints</div>
</a>
</div>

<div class="assignment">
<a href="https://github.com/loise-maina304/clinicguard-api" target="_blank">
<span class="badge">Lesson 8</span>
Authorization & Rate Limiting
<div class="lesson-topic">— RBAC, Dependency Injection, Rate Limiting</div>
</a>
</div>

<div class="assignment">
<a href="https://github.com/loise-maina304/sendit-api" target="_blank">
<span class="badge">Lesson 9</span>
File Uploads & External APIs
<div class="lesson-topic">— File Validation, httpx, Environment Variables</div>
</a>
</div>

<div class="assignment">
<a href="https://github.com/loise-maina304/sendit-api" target="_blank">
<span class="badge">Lesson 10</span>
Testing & Deployment (Cloud)
<div class="lesson-topic">— Pytest, CI/CD, Render Deployment</div>
</a>
</div>

<div class="footer">
<p>📍 Deployed on Render | 📅 Last Updated: August 2026</p>
<p>Click any assignment link to view the source code on GitHub.</p>
</div>

</div>
</body>
</html>
"""

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
