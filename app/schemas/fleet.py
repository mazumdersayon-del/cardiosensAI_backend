from datetime import datetime
from typing import Literal

from pydantic import Field

from app.schemas.common import CamelModel
from app.schemas.health import DeviceOut


class PairIn(CamelModel):
    device_uid: str = Field(min_length=6, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    serial: str = Field(min_length=1, max_length=64)
    display_name: str | None = Field(default=None, max_length=80)


class PairingOut(CamelModel):
    id: str
    device_uid: str
    status: Literal["pending", "accepted", "expired", "cancelled"]
    expires_at: datetime
    device: DeviceOut | None = None


class DeviceConfigIn(CamelModel):
    """Only the fields sent are changed."""

    interval_min: int | None = Field(default=None, description="0 (off), 5, 15, 30, 60, 120, 240, 360, 720 or 1440")
    quiet_start: str | None = Field(default=None, description='"HH:MM", with quietEnd; both null = no quiet hours')
    quiet_end: str | None = None
    timezone: str | None = Field(default=None, max_length=64)
    assigned_patient_id: str | None = Field(default=None, description="Doctor/clinic devices: a linked patient's id, or null")


class LiveIn(CamelModel):
    on: bool = True


class SnapshotLabelIn(CamelModel):
    scp_codes: dict[str, int] = Field(description="SCP-ECG statement -> likelihood 0-100 (as in PTB-XL)")
    signal_quality: Literal["good", "acceptable", "poor"] | None = None
    note: str | None = Field(default=None, max_length=500)


class SnapshotLabelOut(CamelModel):
    doctor_id: str
    doctor_name: str
    scp_codes: dict[str, int]
    signal_quality: str | None
    note: str | None
    updated_at: datetime


class SnapshotOut(CamelModel):
    id: str
    patient_id: str
    device_name: str | None
    recorded_at: datetime
    duration_s: float
    sample_rate_hz: int
    leads: list[str]
    quality: str
    quality_detail: dict
    average_hr: int | None
    acquisition: str
    trigger: str = Field(description="scheduled, or live (someone pressed Live)")
    labels: list[SnapshotLabelOut] = []


class ScpCodeOut(CamelModel):
    code: str
    description: str
    group: str


class FactoryDeviceOut(CamelModel):
    id: str
    device_uid: str
    serial: str
    model_code: str
    batch: str | None
    is_test: bool
    status: str
    blocked_reason: str | None
    firmware: str | None
    last_seen_at: datetime | None
    paired_to: str | None = Field(description="The owner's 16-digit ID while paired")
    paired_role: str | None
    rejected_count: int
    last_rejected_code: str | None
    last_rejected_at: datetime | None
    created_at: datetime


class ImportErrorOut(CamelModel):
    line: int
    device_uid: str | None
    error: str


class ImportOut(CamelModel):
    imported: int
    errors: list[ImportErrorOut]
    broker_updated: bool


class BlockIn(CamelModel):
    reason: str = Field(min_length=3, max_length=300)
