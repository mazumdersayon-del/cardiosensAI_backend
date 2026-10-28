"""ECG recordings: metadata in PostgreSQL, raw samples in object storage, always behind a permission check."""

import base64
import binascii
import hashlib
import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import AwareDatetime
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import RequestMeta, get_current_user, request_meta, require_patient
from app.core.config import get_settings
from app.core.db import get_db, utcnow
from app.core.errors import ApiError, bad_request, conflict, not_found
from app.core.ratelimit import limiter
from app.core.storage import get_storage
from app.models import AuditEvent, Device, EcgLiveChunk, EcgLiveSession, EcgRecording, User
from app.schemas.common import offset_minutes, to_utc
from app.schemas.health import (
    Calibration,
    EcgCreatedOut,
    EcgIn,
    EcgLiveChunkIn,
    EcgLiveOut,
    EcgLiveStartIn,
    EcgLiveStatusOut,
    EcgLiveStopIn,
    EcgOut,
)
from app.services import audit
from app.services.authz import AccessContext, get_access, resolve_patient_id
from app.services.ecg import MAX_CHUNK_SECONDS, check_leads, close_abandoned, finalize, lead_label, live_state
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
    leads = check_leads(body.leads) if body.leads else [body.lead]
    if len(raw) == 0 or len(raw) % (2 * len(leads)) != 0:
        raise bad_request("invalid_samples", "Samples must be 16-bit little-endian integers, one per lead in every frame")
    count = len(raw) // (2 * len(leads))  # frames
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

    device = _device_for(db, user, body.device_id)
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
        lead=body.lead if len(leads) == 1 else lead_label(leads),
        leads=leads,
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
    summary="Raw waveform (int16 little-endian, microvolts; interleaved frames when there are several leads, see X-Leads)",
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
            "X-Leads": ",".join(rec.leads or [rec.lead]),
            "X-Sample-Format": rec.sample_format,
            "X-Calibration-mm-per-mV": str(rec.mm_per_mv),
            "X-Calibration-mm-per-s": str(rec.mm_per_sec),
            "X-Content-SHA256": rec.sha256,
            "Cache-Control": "private, no-store",
        },
    )


# ---------------------------------------------------------------- live ECG
#
# The device (via the app) starts a session, sends ~1 s chunks of interleaved int16 frames, and stops.
# Viewers poll the status and fetch frames after the last one they have. Polling (rather than a
# WebSocket) keeps nginx and the sandboxed service unchanged; at 12 leads x 500 Hz a chunk is ~12 KB.


def _device_for(db: Session, user: User, device_ref: str) -> Device:
    device = None
    try:
        device = db.get(Device, uuid.UUID(device_ref))
    except ValueError:
        device = db.scalar(select(Device).where(Device.patient_id == user.id, Device.client_ref == device_ref, Device.status == "active"))
    if device is None or device.patient_id != user.id or device.status != "active":
        raise bad_request("unknown_device", "Device isn't registered to this account")
    if "ecg" not in device.model.capabilities:
        raise bad_request("unsupported_vital", f"{device.model.name} isn't approved to record ECG")
    return device


def live_out(sess: EcgLiveSession) -> EcgLiveOut:
    return EcgLiveOut(
        id=str(sess.id),
        patient_id=str(sess.patient_id),
        state=live_state(sess),
        leads=list(sess.leads),
        sample_rate_hz=sess.sample_rate_hz,
        calibration=Calibration(mm_per_mv=sess.mm_per_mv, mm_per_sec=sess.mm_per_sec),
        started_at=sess.started_at,
        last_chunk_at=sess.last_chunk_at,
        frames=sess.frames,
        duration_s=round(sess.frames / sess.sample_rate_hz, 2),
        average_hr=sess.average_hr,
        recording_id=str(sess.recording_id) if sess.recording_id else None,
    )


def start_session(db: Session, patient: User, device: Device, body: EcgLiveStartIn, acquisition: str = "live") -> EcgLiveSession:
    """Begin a live ECG for `patient` from `device`, ending any live ECG already running. Caller audits and commits."""
    leads = check_leads(body.leads)
    started = to_utc(body.started_at) if body.started_at else utcnow()
    if started > utcnow() + timedelta(minutes=5):
        raise bad_request("future_timestamp", "Recording start is in the future")
    for old in db.scalars(select(EcgLiveSession).where(EcgLiveSession.patient_id == patient.id, EcgLiveSession.status == "live")):
        finalize(db, old)
    sess = EcgLiveSession(
        patient_id=patient.id,
        device_id=device.id,
        leads=leads,
        sample_rate_hz=body.sample_rate_hz,
        mm_per_mv=body.calibration.mm_per_mv,
        mm_per_sec=body.calibration.mm_per_sec,
        started_at=started,
        utc_offset_min=offset_minutes(body.started_at) if body.started_at else 0,
        frames=0,
        status="live",
        acquisition=acquisition,
    )
    db.add(sess)
    db.flush()
    return sess


def append_chunk(db: Session, sess: EcgLiveSession, body: EcgLiveChunkIn) -> EcgLiveOut:
    if sess.status != "live":
        raise conflict("session_ended", "This live ECG has ended. Start a new one")
    if db.scalar(select(EcgLiveChunk.id).where(EcgLiveChunk.session_id == sess.id, EcgLiveChunk.seq == body.seq)) is not None:
        return live_out(sess)  # retry of a chunk already stored
    try:
        raw = base64.b64decode(body.samples_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise bad_request("invalid_samples", "samplesBase64 is not valid base64") from exc
    width = 2 * len(sess.leads)
    if not raw or len(raw) % width:
        raise bad_request("invalid_samples", "Samples must be whole frames of int16, one per lead")
    frames = len(raw) // width
    if frames > MAX_CHUNK_SECONDS * sess.sample_rate_hz:
        raise bad_request("chunk_too_long", f"Send at most {MAX_CHUNK_SECONDS} s per chunk")
    if sess.frames + frames > get_settings().max_ecg_samples:
        raise bad_request("recording_too_long", "Recording exceeds the maximum length. Stop and start a new one")
    db.add(EcgLiveChunk(session_id=sess.id, seq=body.seq, start_frame=sess.frames, frames=frames, data=raw))
    sess.frames += frames
    sess.last_chunk_at = utcnow()
    if body.average_hr is not None:
        sess.average_hr = body.average_hr
    db.commit()
    return live_out(sess)


def _own_live(db: Session, user: User, session_id: uuid.UUID) -> EcgLiveSession:
    sess = db.get(EcgLiveSession, session_id, with_for_update=True)
    if sess is None or sess.patient_id != user.id:
        raise not_found("Live session")
    return sess


@router.post("/ecg-live", response_model=EcgLiveOut, status_code=201, summary="Start a live ECG (ends any live ECG already running)")
def start_live(
    body: EcgLiveStartIn, user: User = Depends(require_patient), db: Session = Depends(get_db), meta: RequestMeta = Depends(request_meta)
) -> EcgLiveOut:
    limiter.hit(f"ecg-live:{user.id}", 30, 3600)
    sess = start_session(db, user, _device_for(db, user, body.device_id), body)
    audit.record(db, actor=user, action="ecg.live_start", patient_id=user.id, resource_type="ecg_live", resource_id=sess.id, meta=meta)
    db.commit()
    return live_out(sess)


@router.post("/ecg-live/{session_id}/chunks", response_model=EcgLiveOut, summary="Append frames to a live ECG (idempotent on seq)")
def add_live_chunk(
    session_id: uuid.UUID, body: EcgLiveChunkIn, user: User = Depends(require_patient), db: Session = Depends(get_db)
) -> EcgLiveOut:
    return append_chunk(db, _own_live(db, user, session_id), body)


@router.post("/ecg-live/{session_id}/stop", response_model=EcgLiveOut, summary="Stop a live ECG; it becomes a normal recording")
def stop_live(
    session_id: uuid.UUID,
    body: EcgLiveStopIn | None = None,
    user: User = Depends(require_patient),
    db: Session = Depends(get_db),
    meta: RequestMeta = Depends(request_meta),
) -> EcgLiveOut:
    sess = _own_live(db, user, session_id)
    rec = finalize(db, sess, quality=(body.quality if body else "unknown"))
    audit.record(
        db,
        actor=user,
        action="ecg.live_stop",
        patient_id=user.id,
        resource_type="ecg",
        resource_id=rec.id if rec else sess.id,
        meta=meta,
    )
    db.commit()
    return live_out(sess)


def _audit_live_view(db: Session, user: User, ctx: AccessContext, sess: EcgLiveSession, meta: RequestMeta) -> None:
    """Viewers poll every second; record one access-log entry per viewer per session, not one per poll."""
    if ctx.via != "grant":
        return
    seen = db.scalar(
        select(AuditEvent.id).where(
            AuditEvent.actor_user_id == user.id, AuditEvent.action == "ecg.live_view", AuditEvent.resource_id == str(sess.id)
        )
    )
    if seen is None:
        audit.record(
            db,
            actor=user,
            action="ecg.live_view",
            patient_id=sess.patient_id,
            resource_type="ecg_live",
            resource_id=sess.id,
            detail="Live ECG",
            meta=meta,
            grant_id=ctx.grant.id if ctx.grant else None,
        )


@router.get("/patients/{patient_id}/ecg-live", response_model=EcgLiveStatusOut, summary="Is an ECG being recorded right now?")
def live_status(
    patient_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db), meta: RequestMeta = Depends(request_meta)
) -> EcgLiveStatusOut:
    pid = resolve_patient_id(user, patient_id)
    ctx = get_access(db, user, pid, need="ecg")
    close_abandoned(db, pid)
    sess = db.scalar(
        select(EcgLiveSession)
        .where(EcgLiveSession.patient_id == pid, EcgLiveSession.status == "live")
        .order_by(EcgLiveSession.started_at.desc())
    )
    q = select(EcgRecording).where(EcgRecording.patient_id == pid)
    floor = ctx.history_floor
    if floor is not None:
        q = q.where(EcgRecording.started_at >= floor)
    last = db.scalar(q.order_by(EcgRecording.started_at.desc()).limit(1))
    if sess is not None:
        _audit_live_view(db, user, ctx, sess, meta)
    db.commit()
    return EcgLiveStatusOut(live=live_out(sess) if sess else None, last_recording=ecg_out(last) if last else None)


@router.get(
    "/ecg-live/{session_id}/samples",
    summary="Frames of a live ECG from `fromFrame` on (int16 LE, interleaved). Headers say where the data starts",
    response_class=Response,
    responses={200: {"content": {"application/octet-stream": {}}}},
)
def live_samples(
    session_id: uuid.UUID,
    from_frame: int = Query(default=0, ge=0, alias="fromFrame"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    meta: RequestMeta = Depends(request_meta),
) -> Response:
    sess = db.get(EcgLiveSession, session_id)
    if sess is None:
        raise not_found("Live session")
    ctx = get_access(db, user, sess.patient_id, need="ecg")
    if sess.status != "live":
        raise ApiError(410, "session_ended", "This live ECG has ended; it is now in the recordings list")
    # Never send more than the last 10 s: a viewer that falls behind jumps forward instead of catching up slowly.
    start = max(from_frame, sess.frames - MAX_CHUNK_SECONDS * sess.sample_rate_hz, 0)
    chunks = db.scalars(
        select(EcgLiveChunk)
        .where(EcgLiveChunk.session_id == sess.id, EcgLiveChunk.start_frame + EcgLiveChunk.frames > start)
        .order_by(EcgLiveChunk.seq)
    )
    width = 2 * len(sess.leads)
    parts = []
    for c in chunks:
        skip = max(0, start - c.start_frame)
        parts.append(c.data[skip * width :])
    data = b"".join(parts)
    _audit_live_view(db, user, ctx, sess, meta)
    db.commit()
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={
            "X-From-Frame": str(start),
            "X-Frames": str(len(data) // width),
            "X-Total-Frames": str(sess.frames),
            "X-Leads": ",".join(sess.leads),
            "X-Sample-Rate-Hz": str(sess.sample_rate_hz),
            "Cache-Control": "private, no-store",
        },
    )
