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
    account_type: Literal["patient", "caregiver", "doctor"] = Field(
        default="patient",
        description="Used only when creating a new account. 'caregiver' is accepted from older app builds and creates a patient",
    )
    full_name: str | None = Field(default=None, max_length=120)


class UserOut(CamelModel):
    id: str
    unique_id: str | None = Field(description="16-digit CardiosensAI ID")
    role: str
    phone: str | None
    email: str | None
    email_verified: bool
    organization_id: str | None = None
    full_name: str | None
    date_of_birth: date | None
    emergency_contact: str | None
    patient_code: str | None
    preferences: dict
    mfa_enabled: bool
    created_at: datetime


class TokenOut(CamelModel):
    access_token: str
    refresh_token: str | None = Field(description="Omitted for web sessions, where it is set as an httpOnly cookie instead")
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
    web_session: bool = False


class RefreshIn(CamelModel):
    refresh_token: str = Field(min_length=20, max_length=200)


class OptionalRefreshIn(CamelModel):
    """Body for refresh/logout: the app sends the token; the web app sends nothing and relies on the cookie."""

    refresh_token: str | None = Field(default=None, min_length=20, max_length=200)


class DoctorSignupIn(CamelModel):
    registration_number: str = Field(min_length=3, max_length=40)
    council: str = Field(min_length=2, max_length=120)
    specialty: str | None = Field(default=None, max_length=80)


class ClinicSignupIn(CamelModel):
    name: str = Field(min_length=2, max_length=160)
    address: str = Field(min_length=3, max_length=300)
    city: str = Field(min_length=2, max_length=80)
    registration_number: str = Field(min_length=3, max_length=60)
    phone: str = Field(pattern=E164)


class RegisterIn(CamelModel):
    role: Literal["patient", "doctor", "clinic"]
    email: EmailStr
    password: str = Field(max_length=128)
    full_name: str = Field(min_length=2, max_length=120, description="For a clinic: the name of the person managing the account")
    phone: str | None = Field(default=None, pattern=E164)
    date_of_birth: date | None = Field(default=None, description="Patients")
    doctor: DoctorSignupIn | None = None
    clinic: ClinicSignupIn | None = None


class LoginIn(CamelModel):
    email: EmailStr
    password: str = Field(max_length=128)
    web_session: bool = Field(default=False, description="true: refresh token goes in an httpOnly cookie, not the response body")


class MessageOut(CamelModel):
    message: str


class TokenIn(CamelModel):
    token: str = Field(min_length=20, max_length=200)


class EmailIn(CamelModel):
    email: EmailStr


class PasswordResetIn(CamelModel):
    token: str = Field(min_length=20, max_length=200)
    password: str = Field(max_length=128)


class PasswordChangeIn(CamelModel):
    current_password: str = Field(max_length=128)
    new_password: str = Field(max_length=128)


class MfaSetupOut(CamelModel):
    secret: str
    otpauth_uri: str


class MfaCodeIn(CamelModel):
    code: str = Field(pattern=r"^\d{6}$")


class UserUpdateIn(CamelModel):
    full_name: str | None = Field(default=None, max_length=120)
    email: EmailStr | None = Field(default=None, description="Only accepted while the account has no sign-in email yet (app accounts)")
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


class OrganizationIn(CamelModel):
    name: str = Field(min_length=2, max_length=160)
    address: str = Field(min_length=3, max_length=300)
    city: str = Field(min_length=2, max_length=80)
    registration_number: str = Field(min_length=3, max_length=60)
    phone: str = Field(pattern=E164)


class OrganizationOut(CamelModel):
    id: str
    unique_id: str
    name: str
    address: str | None
    city: str | None
    registration_number: str | None
    phone: str | None
    verification_status: str
    verified_at: datetime | None


class AccountStatusOut(CamelModel):
    """What a signed-in account still needs before it can link to others or see patient data."""

    role: str
    unique_id: str | None
    email_verified: bool
    mfa_enabled: bool
    mfa_required: bool
    verification_status: str | None = Field(description="Doctors and clinics: pending | verified | rejected | missing")
    can_link: bool
    can_view_patients: bool
    missing: list[str]
