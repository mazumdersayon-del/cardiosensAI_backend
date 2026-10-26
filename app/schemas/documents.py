from datetime import datetime
from typing import Literal

from pydantic import Field

from app.schemas.common import CamelModel

CATEGORIES = ("lab", "prescription", "imaging", "discharge", "ecg", "other")


class DocumentFileOut(CamelModel):
    id: str
    filename: str
    content_type: Literal["application/pdf", "image/jpeg"]
    size_bytes: int


class CommentOut(CamelModel):
    id: str
    author_name: str | None
    author_role: str | None
    body: str
    created_at: datetime
    mine: bool


class DocumentOut(CamelModel):
    id: str
    patient_id: str
    title: str
    category: str
    document_date: str | None
    note: str | None
    uploader_name: str | None
    uploader_role: str
    created_at: datetime
    files: list[DocumentFileOut]
    comments: list[CommentOut]
    can_delete: bool
    can_comment: bool


class CommentIn(CamelModel):
    body: str = Field(min_length=1, max_length=4000)


class FileLinkOut(CamelModel):
    url: str
    expires_in_s: int
