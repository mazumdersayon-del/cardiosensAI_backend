import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Index, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, JSONType, UTCDateTime, utcnow


class AccessGrant(Base):
    """A patient's explicit permission for one person to see specific data.

    Nothing is visible to a grantee unless an *active*, unexpired grant lists that
    vital sign (or "ecg"), within the history window chosen by the patient.
    """

    __tablename__ = "access_grants"
    __table_args__ = (
        Index("ix_grants_patient", "patient_id"),
        Index("ix_grants_grantee_status", "grantee_user_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    grantee_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    grantee_name: Mapped[str] = mapped_column(String(120))
    grantee_contact: Mapped[str] = mapped_column(String(254))
    role: Mapped[str] = mapped_column(
        String(16)
    )  # family | caregiver | doctor | clinic (clinic and link-created doctor grants come from care_links)
    vitals: Mapped[list] = mapped_column(JSONType, default=list)  # e.g. ["hr","bp","ecg"]
    history: Mapped[str] = mapped_column(String(16))  # from_now | last_30_days | all
    downloads_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    device_notices: Mapped[bool] = mapped_column(Boolean, default=False)
    health_alerts: Mapped[bool] = mapped_column(Boolean, default=False)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    status: Mapped[str] = mapped_column(String(12), default="pending")  # pending | active | revoked
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    accepted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class Invite(Base):
    """Single-use invite token for a grant. Only the SHA-256 of the token is stored."""

    __tablename__ = "invites"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    grant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("access_grants.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
