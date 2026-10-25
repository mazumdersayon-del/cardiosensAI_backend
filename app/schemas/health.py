from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, Field

from app.schemas.common import CamelModel

Vital = Literal["hr", "spo2", "bp", "resp", "temp", "glucose"]


# ---------- Devices ----------


class DeviceModelOut(CamelModel):
    id: str
    code: str
    name: str
    manufacturer: str
    kind: str
    capabilities: list[str]
    integration: str
    payload_schema_version: str
    regulatory_note: str | None
    active: bool


class DeviceIn(CamelModel):
    model_code: str = Field(max_length=64)
    serial: str = Field(min_length=1, max_length=64)
    display_name: str | None = Field(default=None, max_length=80)
    client_ref: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9_.:\-]+$")
    ble_identifier: str | None = Field(default=None, max_length=64)
    firmware: str | None = Field(default=None, max_length=40)


class DeviceOut(CamelModel):
    id: str
    model_code: str
    model_name: str
    kind: str
    capabilities: list[str]
    serial: str
    client_ref: str | None
    display_name: str
    firmware: str | None
    battery_pct: int | None
    status: str
    registered_at: datetime
    last_seen_at: datetime | None
    last_sync_at: datetime | None
    last_sync_error: str | None
    last_sync_error_at: datetime | None


class DeviceStatusIn(CamelModel):
    battery_pct: int | None = Field(default=None, ge=0, le=100)
    firmware: str | None = Field(default=None, max_length=40)
    last_sync_at: AwareDatetime | None = None
    sync_error: str | None = Field(default=None, max_length=200, description="Set when a sync attempt failed; omit/null on success")


# ---------- Measurements (wire format identical to the app's Measurement type) ----------


class ProvenanceIn(CamelModel):
    device_id: str | None = None
    sensor: str | None = Field(default=None, max_length=80)
    acquisition: Literal["live", "delayed", "manual", "simulated"]
    quality: Literal["good", "poor", "unknown"]
    quality_flags: list[str] = Field(default_factory=list, max_length=20)


class GlucoseMetaIn(CamelModel):
    sample_type: str = Field(max_length=40)
    sample_location: str = Field(max_length=40)
    is_control_solution: bool = False
    sequence: int | None = Field(default=None, ge=0, le=65535)


class MeasurementIn(CamelModel):
    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:\-]+$")
    vital: Vital
    measured_at: AwareDatetime
    received_at: AwareDatetime
    provenance: ProvenanceIn
    unit: str
    value: float | None = None
    systolic: int | None = None
    diastolic: int | None = None
    mean_arterial: int | None = None
    pulse_rate: int | None = None
    glucose_meta: GlucoseMetaIn | None = None
    sync: str | None = None  # client-side state; ignored


class BatchIn(CamelModel):
    # Items are validated one by one so one bad reading doesn't reject the whole batch.
    measurements: list[dict] = Field(max_length=100)


class BatchItemResult(CamelModel):
    id: str
    status: Literal["accepted", "duplicate", "rejected"]
    reason: str | None = None


class BatchOut(CamelModel):
    results: list[BatchItemResult]


class ProvenanceOut(CamelModel):
    device_id: str | None
    sensor: str | None
    acquisition: str
    quality: str
    quality_flags: list[str]


class MeasurementOut(CamelModel):
    id: str  # the app's id
    server_id: str
    vital: str
    unit: str
    value: float | None = None
    systolic: int | None = None
    diastolic: int | None = None
    mean_arterial: int | None = None
    pulse_rate: int | None = None
    measured_at: str  # ISO-8601 in the original UTC offset
    received_at: str
    ingested_at: datetime
    provenance: ProvenanceOut
    glucose_meta: dict | None = None


class MeasurementPage(CamelModel):
    items: list[MeasurementOut]
    next_cursor: str | None
    history_limited_from: datetime | None = Field(default=None, description="Set when a sharing grant limits how far back you can see")


class LatestOut(CamelModel):
    latest: dict[str, MeasurementOut | None]
    shared_vitals: list[str]


# ---------- ECG ----------


class Calibration(CamelModel):
    mm_per_mv: float = Field(gt=0, le=40)
    mm_per_sec: float = Field(gt=0, le=100)


class Segment(CamelModel):
    from_sec: float = Field(ge=0)
    to_sec: float = Field(ge=0)


class EcgIn(CamelModel):
    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:\-]+$")
    device_id: str
    started_at: AwareDatetime
    lead: str = Field(max_length=16)
    sample_rate_hz: int = Field(ge=50, le=2000)
    calibration: Calibration
    average_hr: int | None = Field(default=None, ge=20, le=300)
    poor_quality_segments: list[Segment] = Field(default_factory=list, max_length=200)
    quality: Literal["good", "poor", "unknown"]
    acquisition: Literal["live", "delayed"]
    samples_base64: str = Field(description="Little-endian int16 samples in microvolts, base64-encoded")


class EcgOut(CamelModel):
    id: str
    client_id: str
    device_id: str | None
    started_at: str
    duration_s: float
    lead: str
    sample_rate_hz: int
    sample_count: int
    sample_format: str
    calibration: Calibration
    average_hr: int | None
    poor_quality_segments: list[Segment]
    quality: str
    acquisition: str
    sha256: str
    created_at: datetime


class EcgCreatedOut(CamelModel):
    status: Literal["accepted", "duplicate"]
    recording: EcgOut
