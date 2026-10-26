"""multi-lead (12-lead) ECG, live ECG sessions, patient documents with comments

- ecg_recordings.leads: channel names in storage order; existing single-lead recordings get [lead].
- ecg_live_sessions / ecg_live_chunks: streaming ECG while it is being recorded.
- documents / document_files / document_comments: PDF and JPEG uploads by patients and linked
  doctors/clinics, with doctors' comments.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-26 18:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.add_column("ecg_recordings", sa.Column("leads", JSON, nullable=True))
    rec = sa.table("ecg_recordings", sa.column("id", sa.Uuid()), sa.column("lead", sa.String()), sa.column("leads", JSON))
    conn = op.get_bind()
    for rid, lead in conn.execute(sa.select(rec.c.id, rec.c.lead)).all():
        conn.execute(rec.update().where(rec.c.id == rid).values(leads=[lead]))
    op.alter_column("ecg_recordings", "leads", existing_type=JSON, nullable=False)

    op.create_table(
        "ecg_live_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("device_id", sa.Uuid(), nullable=True),
        sa.Column("leads", JSON, nullable=False),
        sa.Column("sample_rate_hz", sa.Integer(), nullable=False),
        sa.Column("mm_per_mv", sa.Float(), nullable=False),
        sa.Column("mm_per_sec", sa.Float(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("utc_offset_min", sa.SmallInteger(), nullable=False),
        sa.Column("last_chunk_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("frames", sa.Integer(), nullable=False),
        sa.Column("average_hr", sa.SmallInteger(), nullable=True),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("recording_id", sa.Uuid(), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["patient_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["recording_id"], ["ecg_recordings.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ecg_live_patient_status", "ecg_live_sessions", ["patient_id", "status"])
    op.create_table(
        "ecg_live_chunks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("start_frame", sa.Integer(), nullable=False),
        sa.Column("frames", sa.Integer(), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["ecg_live_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "seq", name="uq_ecg_live_chunk_seq"),
    )
    op.create_index("ix_ecg_live_chunks_session_id", "ecg_live_chunks", ["session_id"])

    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("uploaded_by", sa.Uuid(), nullable=True),
        sa.Column("uploader_role", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("category", sa.String(length=24), nullable=False),
        sa.Column("document_date", sa.String(length=10), nullable=True),
        sa.Column("note", sa.String(length=1000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["patient_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["uploaded_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_documents_patient_created", "documents", ["patient_id", "created_at"])
    op.create_table(
        "document_files",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("filename", sa.String(length=200), nullable=False),
        sa.Column("content_type", sa.String(length=40), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.String(length=200), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_document_files_document_id", "document_files", ["document_id"])
    op.create_table(
        "document_comments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("author_id", sa.Uuid(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_document_comments_doc_created", "document_comments", ["document_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_document_comments_doc_created", table_name="document_comments")
    op.drop_table("document_comments")
    op.drop_index("ix_document_files_document_id", table_name="document_files")
    op.drop_table("document_files")
    op.drop_index("ix_documents_patient_created", table_name="documents")
    op.drop_table("documents")
    op.drop_index("ix_ecg_live_chunks_session_id", table_name="ecg_live_chunks")
    op.drop_table("ecg_live_chunks")
    op.drop_index("ix_ecg_live_patient_status", table_name="ecg_live_sessions")
    op.drop_table("ecg_live_sessions")
    op.drop_column("ecg_recordings", "leads")
