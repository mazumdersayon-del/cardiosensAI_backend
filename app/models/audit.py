import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, UTCDateTime, utcnow


class AuditEvent(Base):
    """Append-only record of sensitive actions: access, sharing, downloads, permission changes.

    `detail` names what was touched ("blood pressure history"), never the values.
    No foreign key on patient_id/actor so the trail survives account deletion.
    """

    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_patient_at", "patient_id", "at"),
        Index("ix_audit_actor_at", "actor_user_id", "at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    actor_role: Mapped[str | None] = mapped_column(String(16))
    patient_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    action: Mapped[str] = mapped_column(String(48))  # e.g. measurements.read, grant.revoke, report.download
    resource_type: Mapped[str | None] = mapped_column(String(32))
    resource_id: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[str | None] = mapped_column(String(300))
    ip: Mapped[str | None] = mapped_column(String(45))
    request_id: Mapped[str | None] = mapped_column(String(64))
    grant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("access_grants.id", ondelete="SET NULL"))
