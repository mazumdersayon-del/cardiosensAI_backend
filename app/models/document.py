import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, UTCDateTime, utcnow


class Document(Base):
    """A medical document (lab report, prescription, scan...) about one patient, uploaded by the
    patient or by a linked doctor/clinic. Holds one or more files (PDF or JPEG) and doctors' comments.
    """

    __tablename__ = "documents"
    __table_args__ = (Index("ix_documents_patient_created", "patient_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    uploader_role: Mapped[str] = mapped_column(String(16))  # patient | doctor | clinic
    title: Mapped[str] = mapped_column(String(160))
    category: Mapped[str] = mapped_column(String(24), default="other")  # lab | prescription | imaging | discharge | ecg | other
    document_date: Mapped[str | None] = mapped_column(String(10))  # YYYY-MM-DD on the document itself
    note: Mapped[str | None] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class DocumentFile(Base):
    __tablename__ = "document_files"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(200))
    content_type: Mapped[str] = mapped_column(String(40))  # application/pdf | image/jpeg
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str | None] = mapped_column(String(200))
    position: Mapped[int] = mapped_column(Integer, default=0)


class DocumentComment(Base):
    __tablename__ = "document_comments"
    __table_args__ = (Index("ix_document_comments_doc_created", "document_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    author_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
