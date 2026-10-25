from datetime import date, datetime
from typing import Literal

from pydantic import EmailStr, Field

from app.schemas.common import CamelModel

E164 = r"^\+[1-9]\d{7,14}$"


class OtpRequestIn(CamelModel):
    phone: str = Field(pattern=E164, examples=["+919812345678"])


class OtpRequestOut(CamelModel):
    challenge_id: str
    expires_in_s: int
    dev_code: str | None = Field(default=None, description="Only returned when DEV_EXPOSE_OTP is on (development)")


class OtpVerifyIn(CamelModel):
    challenge_id: str
    code: str = Field(pattern=r"^\d{6}$")
    account_type: Literal["patient", "caregiver", "doctor"] = Field(default="patient", description="Used only when creating a new account")
    full_name: str | None = Field(default=None, max_length=120)


class UserOut(CamelModel):
    id: str
    role: str
    phone: str
    email: str | None
    full_name: str | None
    date_of_birth: date | None
    emergency_contact: str | None
    patient_code: str | None
    preferences: dict
    mfa_enabled: bool
    created_at: datetime


class TokenOut(CamelModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105 - not a secret
    expires_in: int
    user: UserOut
    new_account: bool = False


class MfaRequiredOut(CamelModel):
    mfa_required: Literal[True] = True
    mfa_token: str


class MfaVerifyIn(CamelModel):
    mfa_token: str
    code: str = Field(pattern=r"^\d{6}$")


class RefreshIn(CamelModel):
    refresh_token: str = Field(min_length=20, max_length=200)


class MfaSetupOut(CamelModel):
    secret: str
    otpauth_uri: str


class MfaCodeIn(CamelModel):
    code: str = Field(pattern=r"^\d{6}$")


class UserUpdateIn(CamelModel):
    full_name: str | None = Field(default=None, max_length=120)
    email: EmailStr | None = None
    date_of_birth: date | None = None
    emergency_contact: str | None = Field(default=None, max_length=120)
    preferences: dict | None = None


class ConsentIn(CamelModel):
    kind: Literal["data_processing", "terms", "research"]
    version: str = Field(max_length=20)


class ConsentOut(CamelModel):
    id: str
    kind: str
    version: str
    granted_at: datetime
    withdrawn_at: datetime | None


class ProfessionalIn(CamelModel):
    registration_number: str = Field(min_length=3, max_length=40)
    council: str = Field(min_length=2, max_length=120)
    specialty: str | None = Field(default=None, max_length=80)


class ProfessionalOut(CamelModel):
    registration_number: str
    council: str
    specialty: str | None
    verification_status: str
    submitted_at: datetime
    verified_at: datetime | None
