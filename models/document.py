from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlmodel import Field, Relationship, SQLModel

if TYPE_CHECKING:
    from models.user import User


class Document(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)

    filename: str
    original_filename: str
    file_size: int
    file_type: str

    status: str = Field(default="uploaded", index=True)

    city: str = Field(index=True)
    country: str = Field(default="Kenya")

    weather_data: str | None = None
    weather_fetched_at: datetime | None = None

    description: str | None = None

    uploader_id: int = Field(foreign_key="user.id")
    uploader: Optional["User"] = Relationship(back_populates="documents")

    uploaded_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    file_path: str

    version: int = Field(default=1)


class DocumentCreate(SQLModel):
    city: str = Field(min_length=2, max_length=100)
    country: str = Field(default="Kenya", min_length=2, max_length=100)
    description: str | None = None


class DocumentUpdate(SQLModel):
    city: str | None = Field(default=None, min_length=2, max_length=100)

    country: str | None = Field(default=None, min_length=2, max_length=100)

    description: str | None = None
