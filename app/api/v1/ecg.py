"""ECG recordings: metadata in PostgreSQL, raw samples in object storage, always behind a permission check."""

import base64
import binascii
import hashlib
import uuid

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import AwareDatetime
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import RequestMeta, get_current_user, request_meta, require_patient
from app.core.config import get_settings
from app.core.db import get_db, utcnow
from app.core.errors import ApiError, bad_request, not_found
from app.core.storage import get_storage
from app.models import Device, EcgRecording, User
from app.schemas.common import offset_minutes, to_utc
from app.schemas.health import EcgCreatedOut, EcgIn, EcgOut
from app.services import audit
from app.services.authz import AccessContext, get_access, resolve_patient_id
from app.services.serializers import ecg_out

router = APIRouter(tags=["ecg"])


@router.post("/ecg-recordings", response_model=EcgCreatedOut, summary="Upload an ECG recording (idempotent on id)")
def upload_ecg(
    body: EcgIn, user: User = Depends(require_patient), db: Session = Depends(get_db), meta: RequestMeta = Depends(request_meta)
) -> EcgCreatedOut:
    s = get_settings()
    try:
        raw = base64.b64decode(body.samples_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise bad_request("invalid_samples", "samplesBase64 is not valid base64") from exc
    if len(raw) == 0 or len(raw) % 2 != 0:
        raise bad_request("invalid_samples", "Samples must be 16-bit little-endian integers")
    count = len(raw) // 2
    if count > s.max_ecg_samples:
        raise bad_request("recording_too_long", "Recording exceeds the maximum length")
    duration = count / body.sample_rate_hz
    for seg in body.poor_quality_segments:
        if seg.to_sec <= seg.from_sec or seg.to_sec > duration + 0.01:
            raise bad_request("invalid_segment", "Poor-quality segments must lie within the recording")
    digest = hashlib.sha256(raw).hexdigest()

    existing = db.scalar(select(EcgRecording).where(EcgRecording.patient_id == user.id, EcgRecording.client_id == body.id))
    if existing is not None:
        if existing.sha256 != digest:
            raise bad_request("id_conflict", "This id was already used for a different recording")
        return EcgCreatedOut(status="duplicate", recording=ecg_out(existing))

    device = None
    try:
        device = db.get(Device, uuid.UUID(body.device_id))
    except ValueError:
        device = db.scalar(
            select(Device).where(Device.patient_id == user.id, Device.client_ref == body.device_id, Device.status == "active")
        )
    if device is None or device.patient_id != user.id or device.status != "active":
        raise bad_request("unknown_device", "Device isn't registered to this account")
    if "ecg" not in device.model.capabilities:
        raise bad_request("unsupported_vital", f"{device.model.name} isn't approved to record ECG")
    if to_utc(body.started_at) > utcnow():
        raise bad_request("future_timestamp", "Recording start is in the future")

    rec_id = uuid.uuid4()
    key = f"ecg/{user.id}/{rec_id}.i16"
    get_storage().put(key, raw, "application/octet-stream")
    rec = EcgRecording(
        id=rec_id,
        patient_id=user.id,
        client_id=body.id,
        device_id=device.id,
        started_at=to_utc(body.started_at),
        utc_offset_min=offset_minutes(body.started_at),
        lead=body.lead,
        sample_rate_hz=body.sample_rate_hz,
        sample_count=count,
        mm_per_mv=body.calibration.mm_per_mv,
        mm_per_sec=body.calibration.mm_per_sec,
        average_hr=body.average_hr,
        poor_quality_segments=[seg.model_dump() for seg in body.poor_quality_segments],
        quality=body.quality,
        acquisition=body.acquisition,
        storage_key=key,
        sha256=digest,
    )
    db.add(rec)
    audit.record(db, actor=user, action="ecg.upload", patient_id=user.id, resource_type="ecg", resource_id=rec.id, meta=meta)
    db.commit()
    return EcgCreatedOut(status="accepted", recording=ecg_out(rec))


@router.get("/patients/{patient_id}/ecg-recordings", response_model=list[EcgOut])
def list_ecg(
    patient_id: str,
    from_: AwareDatetime | None = Query(default=None, alias="from"),
    to: AwareDatetime | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    meta: RequestMeta = Depends(request_meta),
) -> list[EcgOut]:
    pid = resolve_patient_id(user, patient_id)
    ctx = get_access(db, user, pid, need="ecg")
    stmt = select(EcgRecording).where(EcgRecording.patient_id == pid)
    start = ctx.clamp_from(from_)
    if start is not None:
        stmt = stmt.where(EcgRecording.started_at >= start)
    if to is not None:
        stmt = stmt.where(EcgRecording.started_at < to)
    rows = db.scalars(stmt.order_by(EcgRecording.started_at.desc()).limit(limit))
    if ctx.via == "grant":
        audit.record(
            db,
            actor=user,
            action="ecg.list",
            patient_id=pid,
            detail="ECG recordings",
            meta=meta,
            grant_id=ctx.grant.id if ctx.grant else None,
        )
        db.commit()
    return [ecg_out(r) for r in rows]


def _get_recording(db: Session, user: User, recording_id: uuid.UUID) -> tuple[EcgRecording, AccessContext]:
    rec = db.get(EcgRecording, recording_id)
    if rec is None:
        raise not_found("Recording")
    ctx = get_access(db, user, rec.patient_id, need="ecg")
    floor = ctx.history_floor
    if floor is not None and rec.started_at < floor:
        raise not_found("Recording")
    return rec, ctx


@router.get("/ecg-recordings/{recording_id}", response_model=EcgOut)
def get_ecg(recording_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> EcgOut:
    rec, _ = _get_recording(db, user, recording_id)
    return ecg_out(rec)


@router.get(
    "/ecg-recordings/{recording_id}/samples",
    summary="Raw waveform (int16 little-endian, microvolts)",
    response_class=Response,
    responses={200: {"content": {"application/octet-stream": {}}}},
)
def get_samples(
    recording_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    meta: RequestMeta = Depends(request_meta),
) -> Response:
    rec, ctx = _get_recording(db, user, recording_id)
    data = get_storage().get(rec.storage_key)
    if hashlib.sha256(data).hexdigest() != rec.sha256:
        raise ApiError(500, "integrity_error", "Stored waveform failed its integrity check")
    if ctx.via == "grant":
        audit.record(
            db,
            actor=user,
            action="ecg.read",
            patient_id=rec.patient_id,
            resource_type="ecg",
            resource_id=rec.id,
            detail="ECG waveform",
            meta=meta,
        )
        db.commit()
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={
            "X-Sample-Rate-Hz": str(rec.sample_rate_hz),
            "X-Sample-Format": rec.sample_format,
            "X-Calibration-mm-per-mV": str(rec.mm_per_mv),
            "X-Calibration-mm-per-s": str(rec.mm_per_sec),
            "X-Content-SHA256": rec.sha256,
            "Cache-Control": "private, no-store",
        },
    )
