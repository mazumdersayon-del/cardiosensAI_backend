from pydantic import Field

from app.schemas.common import CamelModel


class DoctorListingIn(CamelModel):
    listed: bool = False
    degrees: str | None = Field(default=None, max_length=160)
    experience_years: int | None = Field(default=None, ge=0, le=70)
    languages: str | None = Field(default=None, max_length=160)
    practice: str | None = Field(default=None, max_length=160)
    city: str | None = Field(default=None, max_length=80)
    pin_code: str | None = Field(default=None, max_length=10, description="Practice PIN code (patients' 'Near me'); kept when not sent")
    about: str | None = Field(default=None, max_length=800)


class DoctorListingOut(DoctorListingIn):
    specialty: str | None
    visible: bool = Field(description="Patients can see this doctor in their Doctors list now")
    hidden_reason: str | None = Field(default=None, description="Why not, when listed but not visible")


class DoctorCardOut(CamelModel):
    unique_id: str
    full_name: str
    specialty: str | None
    degrees: str | None
    experience_years: int | None
    practice: str | None
    city: str | None
    avatar_version: str | None
    pin_code: str | None = None
    distance_km: float | None = Field(default=None, description="Near me: about how far the practice is from the patient's PIN code")


class DoctorOut(DoctorCardOut):
    languages: str | None
    about: str | None
    council: str
    verified_at: str | None
    link_status: str | None = Field(description="This patient's link with the doctor: pending, active or none")


class DoctorPage(CamelModel):
    items: list[DoctorCardOut]
    total: int
    near: str | None = Field(default=None, description="Near me: the patient's PIN code and place used, e.g. '560001 · Bengaluru'")
    near_problem: str | None = Field(default=None, description="Near me couldn't be used: why (no PIN code yet, unknown PIN)")
