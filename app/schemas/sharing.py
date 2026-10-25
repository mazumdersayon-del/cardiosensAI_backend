from datetime import date, datetime
from typing import Literal

from pydantic import AwareDatetime, Field, field_validator

from app.schemas.common import CamelModel

Shareable = Literal["hr", "spo2", "bp", "resp", "temp", "glucose", "ecg"]


class GrantIn(CamelModel):
    grantee_name: str = Field(min_length=2, max_length=120)
    grantee_contact: str = Field(min_length=5, max_length=254, description="Email or mobile number for the invite")
    role: Literal["family", "caregiver", "doctor"]
    vitals: list[Shareable] = Field(min_length=1)
    history: Literal["from_now", "last_30_days", "all"]
    downloads_allowed: bool = False
    device_notices: bool = False
    health_alerts: bool = False
    expires_at: AwareDatetime | None = None

    @field_validator("vitals")
    @classmethod
    def _unique(cls, v: list[str]) -> list[str]:
        return sorted(set(v))


class GrantOut(CamelModel):
    id: str
    patient_id: str
    grantee_user_id: str | None
    grantee_name: str
    grantee_contact: str
    role: str
    vitals: list[str]
    history: str
    downloads_allowed: bool
    device_notices: bool
    health_alerts: bool
    expires_at: datetime | None
    status: str  # pending | active | revoked | expired
    created_at: datetime
    accepted_at: datetime | None
    revoked_at: datetime | None


class GrantCreatedOut(CamelModel):
    grant: GrantOut
    invite_token: str = Field(description="Shown once. Send it to the invitee; only its hash is stored.")
    invite_url: str
    invite_expires_at: datetime


class InviteAcceptIn(CamelModel):
    token: str = Field(min_length=20, max_length=200)


class SharedPatientOut(CamelModel):
    patient_id: str
    full_name: str | None
    patient_code: str | None
    date_of_birth: date | None
    grant: GrantOut
    last_measurement_at: datetime | None = Field(description="Newest reading you're allowed to see — use it to spot stale data")


class NotificationOut(CamelModel):
    id: str
    kind: str
    title: str
    body: str
    link: str | None
    patient_id: str | None
    created_at: datetime
    read_at: datetime | None


class ReportIn(CamelModel):
    patient_id: str = "me"
    from_date: date
    to_date: date
    vitals: list[Literal["hr", "spo2", "bp", "resp", "temp", "glucose"]] = Field(min_length=1)
    include_ecg: bool = True
    include_tables: bool = False
    include_name: bool = True


class ReportOut(CamelModel):
    id: str
    patient_id: str
    status: str
    params: dict
    pages: int | None
    size_bytes: int | None
    error: str | None
    created_at: datetime
    completed_at: datetime | None


class DownloadLinkOut(CamelModel):
    url: str
    expires_in_s: int


class AuditOut(CamelModel):
    id: str
    at: datetime
    actor_user_id: str | None
    actor_role: str | None
    actor_name: str | None = None
    patient_id: str | None
    action: str
    resource_type: str | None
    resource_id: str | None
    detail: str | None
