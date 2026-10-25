import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, ForeignKey, Index, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, JSONType, UTCDateTime, utcnow

# Account types. A "patient" owns health data; caregivers and doctors only see what is shared with them.
ROLES = ("patient", "caregiver", "doctor", "admin")


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    phone: Mapped[str] = mapped_column(String(20), unique=True)  # E.164, e.g. +919812345678
    email: Mapped[str | None] = mapped_column(String(254))
    role: Mapped[str] = mapped_column(String(16), default="patient")
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | suspended | deleted
    full_name: Mapped[str | None] = mapped_column(String(120))
    date_of_birth: Mapped[date | None] = mapped_column(Date)
    emergency_contact: Mapped[str | None] = mapped_column(String(120))
    # Short, human-friendly identifier printed on reports (e.g. CS-P-0004812).
    patient_code: Mapped[str | None] = mapped_column(String(20), unique=True)
    preferences: Mapped[dict] = mapped_column(JSONType, default=dict)  # units, notification switches
    mfa_secret: Mapped[str | None] = mapped_column(String(64))
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class ProfessionalProfile(Base):
    """Doctors must be verified by CardiosensAI staff before they can accept a doctor invite."""

    __tablename__ = "professional_profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    registration_number: Mapped[str] = mapped_column(String(40))
    council: Mapped[str] = mapped_column(String(120))  # e.g. National Medical Commission / state council
    specialty: Mapped[str | None] = mapped_column(String(80))
    verification_status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | verified | rejected
    verified_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    submitted_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Consent(Base):
    """Explicit, versioned consent records. Withdrawal is recorded, never deleted."""

    __tablename__ = "consents"
    __table_args__ = (Index("ix_consents_user_kind", "user_id", "kind"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(32))  # data_processing | terms | research
    version: Mapped[str] = mapped_column(String(20))
    granted_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    withdrawn_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
