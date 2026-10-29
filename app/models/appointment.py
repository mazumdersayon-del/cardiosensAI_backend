"""Doctor appointments: weekly hours, offline periods and bookings (services/appointments.py).

A patient books a slot online (video, link from the doctor) or in person; the doctor accepts or declines it. A request
still unanswered when its slot starts expires. Offline periods block online bookings only. After the visit the doctor
marks it completed (counts as a patient served) or no-show.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, JSONType, UTCDateTime, utcnow

OPEN_STATUSES = ("requested", "accepted")  # hold the slot


class DoctorSchedule(Base):
    """A doctor's booking settings. `weekly_hours`: [{"day": 0-6 (Mon=0), "start": "HH:MM", "end": "HH:MM"}] in `timezone`."""

    __tablename__ = "doctor_schedules"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)  # patients can book
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Kolkata")
    slot_min: Mapped[int] = mapped_column(Integer, default=15)
    window_days: Mapped[int] = mapped_column(Integer, default=14)
    weekly_hours: Mapped[list] = mapped_column(JSONType, default=list)
    online: Mapped[bool] = mapped_column(Boolean, default=True)  # offers video appointments
    in_person: Mapped[bool] = mapped_column(Boolean, default=True)  # offers visits at the practice
    meeting_link: Mapped[str | None] = mapped_column(String(300))  # default video link, shown once accepted
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class DoctorOfflinePeriod(Base):
    """No online bookings from `starts_at` to `ends_at` ("Go offline" now, or a scheduled period such as a holiday)."""

    __tablename__ = "doctor_offline_periods"
    __table_args__ = (Index("ix_doctor_offline_periods_doctor_end", "doctor_id", "ends_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    doctor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    starts_at: Mapped[datetime] = mapped_column(UTCDateTime)
    ends_at: Mapped[datetime] = mapped_column(UTCDateTime)
    kind: Mapped[str] = mapped_column(String(12))  # now | scheduled
    reason: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Appointment(Base):
    __tablename__ = "appointments"
    __table_args__ = (
        Index("ix_appointments_doctor_start", "doctor_id", "starts_at"),
        Index("ix_appointments_patient_start", "patient_id", "starts_at"),
        # One open booking per slot start
        Index(
            "uq_appointments_doctor_open_slot",
            "doctor_id",
            "starts_at",
            unique=True,
            postgresql_where=text("status IN ('requested', 'accepted')"),
            sqlite_where=text("status IN ('requested', 'accepted')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    doctor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    patient_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))  # None: walk-in without an account
    guest_name: Mapped[str | None] = mapped_column(String(120))  # walk-in visitor without an account
    starts_at: Mapped[datetime] = mapped_column(UTCDateTime)
    ends_at: Mapped[datetime] = mapped_column(UTCDateTime)
    mode: Mapped[str] = mapped_column(String(12))  # online | in_person
    # requested | accepted | declined | cancelled | expired | completed | no_show
    status: Mapped[str] = mapped_column(String(12), default="requested")
    source: Mapped[str] = mapped_column(String(12), default="booking")  # booking (patient) | walk_in (doctor)
    reason: Mapped[str | None] = mapped_column(String(300))  # the patient's note to the doctor
    doctor_note: Mapped[str | None] = mapped_column(String(300))  # with an accept / decline / cancel
    meeting_link: Mapped[str | None] = mapped_column(String(300))  # online: set when accepted
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    cancelled_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reminder_sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
