import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, UTCDateTime, utcnow

# Which two parties a link joins. Both must approve; the requester's side approves on request.
LINK_KINDS = ("patient_doctor", "patient_clinic", "doctor_clinic")
LINK_SIDES = {"patient_doctor": ("patient", "doctor"), "patient_clinic": ("patient", "clinic"), "doctor_clinic": ("doctor", "clinic")}


class CareLink(Base):
    """A link between two 16-digit IDs: patient↔doctor, patient↔clinic or doctor↔clinic.

    A patient link that becomes active creates an AccessGrant (full access) for the doctor or
    the clinic's login; ending the link revokes that grant, so services/authz.py stays the
    single gate for health data.
    """

    __tablename__ = "care_links"
    __table_args__ = (
        Index("ix_links_patient", "patient_id"),
        Index("ix_links_doctor", "doctor_id"),
        Index("ix_links_org", "organization_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(16))
    patient_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    doctor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    organization_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(12), default="pending")  # pending | active | declined | ended
    requested_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    patient_ok_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    doctor_ok_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    clinic_ok_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    grant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("access_grants.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    ended_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
