"""A patient's own details beyond sign-up: body, address, background (services/patient_profile.py).

All optional; patients add them after sign-up (an optional "About you" step and a Home reminder until the PIN code
is saved). Linked doctors and clinics see everything except the street lines unless `share_address` is on.
The PIN code also drives "Near me" in the Doctors list (data/in_pincodes.csv.gz, GeoNames, CC BY 4.0).
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, JSONType, UTCDateTime, utcnow

SEXES = ("female", "male", "other", "undisclosed")
BLOOD_GROUPS = ("A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-")
SMOKING = ("never", "former", "current")
CONDITIONS = (
    "hypertension",
    "diabetes",
    "heart_disease",
    "heart_failure",
    "arrhythmia",
    "stroke",
    "high_cholesterol",
    "kidney_disease",
    "thyroid",
    "asthma_copd",
)


class PatientProfile(Base):
    __tablename__ = "patient_profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    height_cm: Mapped[float | None] = mapped_column(Float)
    weight_kg: Mapped[float | None] = mapped_column(Float)
    weight_at: Mapped[datetime | None] = mapped_column(UTCDateTime)  # when the weight was last changed
    sex: Mapped[str | None] = mapped_column(String(12))
    blood_group: Mapped[str | None] = mapped_column(String(3))
    address_line1: Mapped[str | None] = mapped_column(String(160))
    address_line2: Mapped[str | None] = mapped_column(String(160))
    city: Mapped[str | None] = mapped_column(String(80))
    district: Mapped[str | None] = mapped_column(String(80))
    state: Mapped[str | None] = mapped_column(String(80))
    pin_code: Mapped[str | None] = mapped_column(String(6), index=True)
    share_address: Mapped[bool] = mapped_column(Boolean, default=False)  # linked doctors see the street lines
    conditions: Mapped[list] = mapped_column(JSONType, default=list)  # codes from CONDITIONS
    conditions_other: Mapped[str | None] = mapped_column(String(300))
    allergies: Mapped[str | None] = mapped_column(String(300))
    medicines: Mapped[str | None] = mapped_column(String(500))
    smoking: Mapped[str | None] = mapped_column(String(8))
    reminder_snoozed_until: Mapped[datetime | None] = mapped_column(UTCDateTime)  # "Later" on the address reminder
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
