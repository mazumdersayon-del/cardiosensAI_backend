"""Measurement ingestion: validate each reading on its own, then store it exactly once.

Per item the outcome is one of:
  accepted  — stored
  duplicate — this id was already stored with the same content (safe retry)
  rejected  — refused, with a reason; the app keeps it locally and doesn't retry

Rules (clinical-safety and data-integrity):
- Units must be the canonical unit for the vital sign; values must be physiologically plausible.
- Timestamps must carry a UTC offset and not be in the future (small clock skew allowed).
- Device readings must come from a device registered to this patient whose model is approved
  for that vital sign. Manual entries must not claim a device.
- Readings dated before the device was registered to this patient are refused — a second-hand
  meter's memory can contain someone else's results.
- Simulated readings and glucose control-solution tests are never stored as health data.
- Reusing an id for different content is refused (idempotency keys must be stable).
"""

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import utcnow
from app.models import Device, IngestionRejection, Measurement, User
from app.schemas.common import offset_minutes, to_utc
from app.schemas.health import BatchItemResult, MeasurementIn

CANONICAL_UNIT = {"hr": "bpm", "spo2": "%", "resp": "breaths/min", "temp": "°C", "glucose": "mg/dL", "bp": "mmHg"}
RANGES: dict[str, tuple[float, float]] = {
    "hr": (20, 300),
    "spo2": (50, 100),
    "resp": (2, 80),
    "temp": (25, 45),
    "glucose": (10, 1000),
}


@dataclass
class Rejection(Exception):
    code: str
    reason: str


def _hash(m: MeasurementIn) -> str:
    """Content hash over everything that defines the reading (not client-side sync state)."""
    body = m.model_dump(mode="json", exclude={"sync", "received_at"})
    body["measured_at"] = to_utc(m.measured_at).isoformat()
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _check_values(m: MeasurementIn) -> None:
    if m.unit != CANONICAL_UNIT[m.vital]:
        raise Rejection("wrong_unit", f"{m.vital} must be sent in {CANONICAL_UNIT[m.vital]}")
    if m.vital == "bp":
        if m.systolic is None or m.diastolic is None:
            raise Rejection("missing_value", "Blood pressure needs systolic and diastolic values")
        if not (40 <= m.systolic <= 300 and 20 <= m.diastolic <= 200 and m.diastolic < m.systolic):
            raise Rejection("implausible_value", "Blood pressure values are outside the plausible range")
        if m.pulse_rate is not None and not (20 <= m.pulse_rate <= 300):
            raise Rejection("implausible_value", "Pulse rate is outside the plausible range")
        if m.mean_arterial is not None and not (m.diastolic <= m.mean_arterial <= m.systolic):
            raise Rejection("implausible_value", "Mean arterial pressure must lie between diastolic and systolic")
        if m.value is not None:
            raise Rejection("unexpected_field", "Blood pressure readings use systolic/diastolic, not value")
    else:
        if m.value is None:
            raise Rejection("missing_value", "Reading has no value")
        lo, hi = RANGES[m.vital]
        if not (lo <= m.value <= hi):
            raise Rejection("implausible_value", f"{m.vital} value is outside the plausible range")
        if any(v is not None for v in (m.systolic, m.diastolic, m.mean_arterial, m.pulse_rate)):
            raise Rejection("unexpected_field", "Blood pressure fields are only allowed on bp readings")
    if m.vital == "glucose" and m.glucose_meta and m.glucose_meta.is_control_solution:
        raise Rejection("control_solution", "Control-solution tests are not health data")
    if m.vital != "glucose" and m.glucose_meta is not None:
        raise Rejection("unexpected_field", "glucoseMeta is only allowed on glucose readings")


def _check_time(m: MeasurementIn, now: datetime) -> None:
    skew = timedelta(seconds=get_settings().future_skew_s)
    if to_utc(m.measured_at) > now + skew:
        raise Rejection("future_timestamp", "Measurement time is in the future — check the device clock")
    if to_utc(m.received_at) + skew < to_utc(m.measured_at):
        raise Rejection("received_before_measured", "Received time is earlier than measurement time")
    if to_utc(m.measured_at) < now - timedelta(days=5 * 365):
        raise Rejection("too_old", "Measurement is older than five years")


def _resolve_device(
    db: Session, patient: User, m: MeasurementIn, cache: dict[str, Device | None], stream: Device | None = None, simulated: bool = False
) -> Device | None:
    acq = m.provenance.acquisition
    dev_ref = m.provenance.device_id
    if stream is not None:
        # Device stream: the server already identified the device (and, for a doctor's/clinic's device, the link).
        if acq != ("simulated" if simulated else "live") or dev_ref != str(stream.id):
            raise Rejection("unknown_device", "Readings from a device stream must name that device")
        if m.vital not in stream.model.capabilities:
            raise Rejection("unsupported_vital", f"{stream.model.name} isn't approved to measure {m.vital}")
        return stream
    if acq == "simulated":
        raise Rejection("simulated", "Simulated readings are never stored as health data")
    if acq == "manual":
        if dev_ref:
            raise Rejection("manual_with_device", "Manual entries must not reference a device")
        return None
    if not dev_ref:
        raise Rejection("unknown_device", "Device readings must name the device that took them")
    if dev_ref not in cache:
        device: Device | None = None
        try:
            device = db.get(Device, uuid.UUID(dev_ref))
        except ValueError:
            device = db.scalar(
                select(Device).where(Device.patient_id == patient.id, Device.client_ref == dev_ref, Device.status == "active")
            )
        cache[dev_ref] = device
    device = cache[dev_ref]
    if device is None or device.patient_id != patient.id or device.status != "active":
        raise Rejection("unknown_device", "Device isn't registered to this account")
    if m.vital not in device.model.capabilities:
        raise Rejection("unsupported_vital", f"{device.model.name} isn't approved to measure {m.vital}")
    grace = timedelta(seconds=get_settings().pre_registration_grace_s)
    if to_utc(m.measured_at) < device.registered_at - grace:
        raise Rejection("before_registration", "Reading is dated before this device was registered to you")
    return device


def ingest_batch(
    db: Session, patient: User, items: list[dict], stream: Device | None = None, simulated: bool = False
) -> list[BatchItemResult]:
    """`stream`: readings from a device stream (`api/v1/device_stream.py`); `simulated` only for test streams."""
    now = utcnow()
    results: list[BatchItemResult] = []
    device_cache: dict[str, Device | None] = {}
    seen_in_batch: dict[str, str] = {}
    touched_devices: set[uuid.UUID] = set()

    for raw in items:
        raw_id = str(raw.get("id", ""))[:64] if isinstance(raw, dict) else ""
        try:
            try:
                m = MeasurementIn.model_validate(raw)
            except ValidationError as exc:
                first = exc.errors()[0]
                field = ".".join(str(p) for p in first.get("loc", []))
                raise Rejection("invalid_format", f"Invalid field: {field}") from exc
            _check_values(m)
            _check_time(m, now)
            device = _resolve_device(db, patient, m, device_cache, stream, simulated)
            h = _hash(m)

            if m.id in seen_in_batch:
                results.append(
                    BatchItemResult(
                        id=m.id,
                        status="duplicate" if seen_in_batch[m.id] == h else "rejected",
                        reason=None if seen_in_batch[m.id] == h else "Same id sent twice with different content",
                    )
                )
                continue
            seen_in_batch[m.id] = h

            existing = db.scalar(
                select(Measurement.payload_hash).where(Measurement.patient_id == patient.id, Measurement.client_id == m.id)
            )
            if existing is not None:
                if existing == h:
                    results.append(BatchItemResult(id=m.id, status="duplicate"))
                    continue
                raise Rejection("id_conflict", "This id was already used for a different reading")

            row = Measurement(
                patient_id=patient.id,
                client_id=m.id,
                payload_hash=h,
                vital=m.vital,
                unit=m.unit,
                value=m.value,
                systolic=m.systolic,
                diastolic=m.diastolic,
                mean_arterial=m.mean_arterial,
                pulse_rate=m.pulse_rate,
                measured_at=to_utc(m.measured_at),
                utc_offset_min=offset_minutes(m.measured_at),
                received_at=to_utc(m.received_at),
                ingested_at=now,
                device_id=device.id if device else None,
                sensor=m.provenance.sensor,
                acquisition=m.provenance.acquisition,
                quality=m.provenance.quality,
                quality_flags=[f[:120] for f in m.provenance.quality_flags],
                glucose_meta=m.glucose_meta.model_dump(by_alias=True) if m.glucose_meta else None,
            )
            try:
                with db.begin_nested():
                    db.add(row)
            except IntegrityError:
                # Lost a race with a concurrent retry of the same reading.
                again = db.scalar(
                    select(Measurement.payload_hash).where(Measurement.patient_id == patient.id, Measurement.client_id == m.id)
                )
                results.append(
                    BatchItemResult(
                        id=m.id,
                        status="duplicate" if again == h else "rejected",
                        reason=None if again == h else "This id was already used for a different reading",
                    )
                )
                continue
            if device:
                touched_devices.add(device.id)
            results.append(BatchItemResult(id=m.id, status="accepted"))
        except Rejection as r:
            db.add(IngestionRejection(patient_id=patient.id, client_id=raw_id or None, reason_code=r.code))
            results.append(BatchItemResult(id=raw_id or "(missing id)", status="rejected", reason=r.reason))

    for dev_id in touched_devices:
        d = device_cache.get(str(dev_id)) or db.get(Device, dev_id)
        if d is not None:
            d.last_seen_at = now
    return results
