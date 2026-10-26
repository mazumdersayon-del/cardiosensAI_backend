import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, ForeignKey, Index, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, JSONType, UTCDateTime, utcnow

# Account types. A "patient" owns health data; doctors and clinics only see what a patient approved.
# "admin" is CardiosensAI staff. ("caregiver" existed before 0002 and was folded into "patient".)
ROLES = ("patient", "doctor", "clinic", "admin")
PUBLIC_ROLES = ("patient", "doctor", "clinic")


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    # 16-digit CardiosensAI ID (see app/core/ids.py). A clinic's login carries its organisation's ID.
    unique_id: Mapped[str | None] = mapped_column(String(16), unique=True)
    phone: Mapped[str | None] = mapped_column(String(20), unique=True)  # E.164, e.g. +919812345678 (app sign-in)
    email: Mapped[str | None] = mapped_column(String(254), unique=True)  # stored lower-case
    email_verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    password_hash: Mapped[str | None] = mapped_column(String(200))
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(UTCDateTime)
    role: Mapped[str] = mapped_column(String(16), default="patient")
    organization_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("organizations.id", ondelete="SET NULL"), index=True)
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


class Organization(Base):
    """A clinic. Has its own 16-digit ID; in v1 one login (the owner) acts for it.

    Kept apart from `users` so clinic staff accounts can be added later without a schema change.
    """

    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    unique_id: Mapped[str] = mapped_column(String(16), unique=True)
    kind: Mapped[str] = mapped_column(String(16), default="clinic")
    name: Mapped[str] = mapped_column(String(160))
    address: Mapped[str | None] = mapped_column(String(300))
    city: Mapped[str | None] = mapped_column(String(80))
    registration_number: Mapped[str | None] = mapped_column(String(60))  # clinical-establishment licence / registration
    phone: Mapped[str | None] = mapped_column(String(20))
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL", use_alter=True, name="fk_organizations_owner")
    )
    verification_status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | verified | rejected
    verified_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", use_alter=True, name="fk_organizations_verified_by"))
    verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ProfessionalProfile(Base):
    """Doctors must be verified by CardiosensAI staff before they can see any patient data."""

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
