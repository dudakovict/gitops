import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from .models import DocumentStatus


class DocumentOut(BaseModel):
    id: uuid.UUID
    filename: str
    size_bytes: int
    status: DocumentStatus
    page_count: int | None
    chunk_count: int | None
    error: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class DocumentList(BaseModel):
    items: list[DocumentOut]


class AskIn(BaseModel):
    question: str = Field(min_length=3, max_length=4000)
