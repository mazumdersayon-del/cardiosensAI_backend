from datetime import datetime
from typing import Literal

from pydantic import Field

from app.schemas.common import CamelModel

Sex = Literal["female", "male", "other", "undisclosed"]
BloodGroup = Literal["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"]
Smoking = Literal["never", "former", "current"]


class PatientProfileIn(CamelModel):
    """Send only what changes; null or "" clears a field."""

    height_cm: float | None = Field(default=None, description="40–250")
    weight_kg: float | None = Field(default=None, description="2–350")
    sex: Sex | Literal[""] | None = None
    blood_group: BloodGroup | Literal[""] | None = None
    address_line1: str | None = Field(default=None, max_length=160)
    address_line2: str | None = Field(default=None, max_length=160)
    city: str | None = Field(default=None, max_length=80)
    district: str | None = Field(default=None, max_length=80)
    state: str | None = Field(default=None, max_length=80)
    pin_code: str | None = Field(default=None, max_length=10, description="6-digit Indian PIN code")
    share_address: bool | None = Field(default=None, description="Linked doctors and clinics also see the street lines")
    conditions: list[str] | None = Field(default=None, description="Codes from GET /v1/me/patient-profile (conditionOptions)")
    conditions_other: str | None = Field(default=None, max_length=300)
    allergies: str | None = Field(default=None, max_length=300)
    medicines: str | None = Field(default=None, max_length=500)
    smoking: Smoking | Literal[""] | None = None


class PatientProfileOut(CamelModel):
    height_cm: float | None
    weight_kg: float | None
    weight_at: datetime | None
    bmi: float | None
    sex: str | None
    blood_group: str | None
    address_line1: str | None = Field(description="Doctors: only when the patient shares the address")
    address_line2: str | None
    city: str | None
    district: str | None
    state: str | None
    pin_code: str | None
    pin_known: bool = Field(description="The PIN is in the PIN code table (distances work)")
    share_address: bool
    conditions: list[str]
    conditions_other: str | None
    allergies: str | None
    medicines: str | None
    smoking: str | None
    updated_at: datetime | None
    needs_address: bool = Field(description="No PIN code yet")
    show_reminder: bool = Field(default=False, description="Patient: show the 'add your address' reminder now")


class PinOut(CamelModel):
    pin: str
    district: str
    state: str
    place: str


class ConditionOption(CamelModel):
    code: str
    label: str
