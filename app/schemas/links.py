from datetime import date, datetime
from typing import Literal

from pydantic import Field

from app.schemas.common import CamelModel


class PartyOut(CamelModel):
    """Another account as seen across a link. Never includes contact details or health data."""

    unique_id: str
    role: Literal["patient", "doctor", "clinic"]
    name: str | None
    verified: bool | None = Field(default=None, description="Doctors and clinics: verified by CardiosensAI")
    detail: str | None = Field(default=None, description="Doctor's specialty or clinic's city")


class LinkRequestIn(CamelModel):
    target_id: str = Field(min_length=16, max_length=24, description="16-digit ID of the patient, doctor or clinic (spaces allowed)")
    on_behalf_of_doctor_id: str | None = Field(
        default=None, min_length=16, max_length=24, description="Clinics only: ask a patient to link with one of the clinic's doctors"
    )


class LinkOut(CamelModel):
    id: str
    kind: Literal["patient_doctor", "patient_clinic", "doctor_clinic"]
    status: Literal["pending", "active", "declined", "ended"]
    other: PartyOut
    via_clinic: PartyOut | None = Field(default=None, description="Set when a clinic requested this doctor link")
    direction: Literal["incoming", "outgoing"] = Field(description="incoming: the other side asked; outgoing: you asked")
    awaiting_me: bool
    created_at: datetime
    decided_at: datetime | None
    ended_at: datetime | None


class LinkedPatientOut(CamelModel):
    patient_id: str
    unique_id: str
    full_name: str | None
    date_of_birth: date | None
    patient_code: str | None
    linked_at: datetime | None
    last_measurement_at: datetime | None
    doctors: list[PartyOut] = Field(default_factory=list, description="Clinic view: the clinic's doctors who also follow this patient")


class ClinicDoctorOut(CamelModel):
    link_id: str
    doctor: PartyOut
    linked_at: datetime | None
    patient_count: int = Field(description="This clinic's patients that the doctor is also linked to")
