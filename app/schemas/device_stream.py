from pydantic import Field

from app.schemas.common import CamelModel


class StreamStartIn(CamelModel):
    device_uid: str = Field(min_length=6, max_length=64)
    serial: str = Field(min_length=1, max_length=64)
    account_id: str = Field(description="16-digit ID of the account the device is registered to (patient, doctor or clinic)")
    patient_id: str | None = Field(default=None, description="Doctor/clinic devices: the linked patient to record for")
    simulated: bool = Field(
        default=False, description="Test stream (synthetic data): stored labelled simulated; allowed only where enabled"
    )


class StreamStartOut(CamelModel):
    token: str = Field(description="Bearer token for the stream endpoints; valid for expiresInS seconds")
    expires_in_s: int
    device_id: str
    device_name: str
    capabilities: list[str]
    patient_name: str
    owner_role: str
    simulated: bool
