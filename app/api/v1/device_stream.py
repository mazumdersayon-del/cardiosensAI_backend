"""Device stream: a registered device sends readings and live 12-lead ECG straight to a patient's record.

The device identifies itself with its device ID + serial number + the 16-digit ID of the account it is registered
to (Account → Devices) and gets a short-lived stream token. A patient's device records for that patient; a doctor's or
clinic's device records only for a patient with an active link (grant) to that doctor/clinic, which must be verified
and on two-step sign-in. Test streams (synthetic data, `../Test_Scripts/stream_device.py`) are stored labelled "simulated"
and are allowed only where `Settings.test_streams_allowed` (development by default, never on by default in production).
"""

import uuid

from fastapi import APIRouter, Depends, Header
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import RequestMeta, request_meta
from app.api.v1.ecg import append_chunk, live_out, start_session
from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import ApiError, bad_request, conflict, forbidden, not_found
from app.core.ids import normalize_unique_id, role_of
from app.core.ratelimit import limiter
from app.core.security import constant_time_equal, sign_payload, verify_signed
from app.models import Device, EcgLiveSession, Organization, User
from app.schemas.device_stream import StreamStartIn, StreamStartOut
from app.schemas.health import BatchIn, BatchOut, EcgLiveChunkIn, EcgLiveOut, EcgLiveStartIn, EcgLiveStopIn
from app.services import audit
from app.services.accounts import can_view_patients
from app.services.authz import active_grant_for
from app.services.ecg import finalize
from app.services.ingestion import ingest_batch

router = APIRouter(prefix="/device-stream", tags=["device stream"])

TOKEN_TTL_S = 6 * 3600
NO_MATCH = "No registered device matches this device ID, serial number and account ID. Register it under Account → Devices first"


class Stream:
    def __init__(self, device: Device, patient: User, owner: User, simulated: bool):
        self.device, self.patient, self.owner, self.simulated = device, patient, owner, simulated


def _active_user(db: Session, unique_id: str | None, role: str | None = None) -> User | None:
    uid = normalize_unique_id(unique_id or "")
    if uid is None:
        return None
    u = db.scalar(select(User).where(User.unique_id == uid, User.status == "active"))
    if u is None and role_of(uid) == "clinic":  # a clinic's ID is its organization's; the device belongs to its login
        org = db.scalar(select(Organization).where(Organization.unique_id == uid))
        u = db.get(User, org.owner_user_id) if org and org.owner_user_id else None
    return u if u is not None and u.status == "active" and (role is None or u.role == role) else None


def _check_link(db: Session, owner: User, patient: User) -> None:
    if owner.id == patient.id:
        return
    if not can_view_patients(db, owner):
        raise forbidden("This doctor or clinic account isn't verified with two-step sign-in yet, so its devices can't record for patients")
    if active_grant_for(db, owner, patient.id) is None:
        raise not_found("Linked patient")


@router.post("/session", response_model=StreamStartOut, summary="Device: start a stream (device ID + serial + account ID)")
def start_stream(body: StreamStartIn, db: Session = Depends(get_db), meta: RequestMeta = Depends(request_meta)) -> StreamStartOut:
    limiter.hit(f"devstream-ip:{meta.ip}", 60, 3600)
    limiter.hit(f"devstream-uid:{body.device_uid.lower()}", 30, 3600)
    if body.simulated and not get_settings().test_streams_allowed:
        raise forbidden("Test streams (synthetic data) are switched off on this server")
    device = db.scalar(select(Device).where(Device.device_uid == body.device_uid, Device.status == "active"))
    owner = _active_user(db, body.account_id)
    if device is None or owner is None or device.patient_id != owner.id or not constant_time_equal(device.serial, body.serial):
        raise ApiError(404, "no_matching_device", NO_MATCH)
    if owner.role == "patient":
        if body.patient_id and normalize_unique_id(body.patient_id) != owner.unique_id:
            raise bad_request("patient_mismatch", "A patient's device records only for that patient")
        patient = owner
    else:
        patient = _active_user(db, body.patient_id, role="patient")
        if patient is None:
            raise bad_request("patient_required", "This device belongs to a doctor or clinic: give the 16-digit ID of a linked patient")
        _check_link(db, owner, patient)
    audit.record(
        db,
        actor=owner,
        action="device.stream_start",
        patient_id=patient.id,
        resource_type="device",
        resource_id=device.id,
        detail=("test stream" if body.simulated else "stream") + f" · {device.display_name}",
        meta=meta,
    )
    db.commit()
    token = sign_payload(
        {"typ": "device_stream", "d": str(device.id), "p": str(patient.id), "o": str(owner.id), "sim": body.simulated}, TOKEN_TTL_S
    )
    return StreamStartOut(
        token=token,
        expires_in_s=TOKEN_TTL_S,
        device_id=str(device.id),
        device_name=device.display_name,
        capabilities=list(device.model.capabilities),
        patient_name=patient.full_name or "Patient",
        owner_role=owner.role,
        simulated=body.simulated,
    )


def stream_ctx(authorization: str | None = Header(default=None), db: Session = Depends(get_db)) -> Stream:
    """Every call re-checks the device is still registered and (for doctor/clinic devices) the link is still active."""
    token = (authorization or "").removeprefix("Bearer ").strip()
    body = verify_signed(token) if token else None
    if not body or body.get("typ") != "device_stream":
        raise ApiError(401, "invalid_token", "Start a new stream: the stream token is missing, invalid or expired")
    device = db.get(Device, uuid.UUID(body["d"]))
    patient = db.get(User, uuid.UUID(body["p"]))
    owner = db.get(User, uuid.UUID(body["o"]))
    if device is None or device.status != "active" or owner is None or device.patient_id != owner.id or owner.status != "active":
        raise ApiError(401, "device_deregistered", "This device is no longer registered")
    if patient is None or patient.status != "active":
        raise not_found("Patient")
    _check_link(db, owner, patient)
    simulated = bool(body.get("sim"))
    if simulated and not get_settings().test_streams_allowed:
        raise forbidden("Test streams (synthetic data) are switched off on this server")
    return Stream(device, patient, owner, simulated)


@router.post("/measurements", response_model=BatchOut, summary="Device: send readings (same rules as /measurements:batch)")
def stream_measurements(body: BatchIn, s: Stream = Depends(stream_ctx), db: Session = Depends(get_db)) -> BatchOut:
    if len(body.measurements) > get_settings().max_batch_size:
        raise bad_request("batch_too_large", f"Send at most {get_settings().max_batch_size} readings per request")
    limiter.hit(f"devstream-ingest:{s.device.id}", 600, 60)
    results = ingest_batch(db, s.patient, body.measurements, stream=s.device, simulated=s.simulated)
    db.commit()
    return BatchOut(results=results)


def _own_session(db: Session, s: Stream, session_id: uuid.UUID) -> EcgLiveSession:
    sess = db.get(EcgLiveSession, session_id, with_for_update=True)
    if sess is None or sess.patient_id != s.patient.id or sess.device_id != s.device.id:
        raise not_found("Live session")
    return sess


@router.post("/ecg-live", response_model=EcgLiveOut, status_code=201, summary="Device: start a live ECG")
def stream_ecg_start(
    body: EcgLiveStartIn, s: Stream = Depends(stream_ctx), db: Session = Depends(get_db), meta: RequestMeta = Depends(request_meta)
) -> EcgLiveOut:
    limiter.hit(f"devstream-ecg:{s.device.id}", 30, 3600)
    if "ecg" not in s.device.model.capabilities:
        raise bad_request("unsupported_vital", f"{s.device.model.name} isn't approved to record ECG")
    sess = start_session(db, s.patient, s.device, body, acquisition="simulated" if s.simulated else "live")
    audit.record(
        db, actor=s.owner, action="ecg.live_start", patient_id=s.patient.id, resource_type="ecg_live", resource_id=sess.id, meta=meta
    )
    db.commit()
    return live_out(sess)


@router.post("/ecg-live/{session_id}/chunks", response_model=EcgLiveOut, summary="Device: append frames (idempotent on seq)")
def stream_ecg_chunk(
    session_id: uuid.UUID, body: EcgLiveChunkIn, s: Stream = Depends(stream_ctx), db: Session = Depends(get_db)
) -> EcgLiveOut:
    return append_chunk(db, _own_session(db, s, session_id), body)


@router.post("/ecg-live/{session_id}/stop", response_model=EcgLiveOut, summary="Device: stop; the live ECG becomes a recording")
def stream_ecg_stop(
    session_id: uuid.UUID,
    body: EcgLiveStopIn | None = None,
    s: Stream = Depends(stream_ctx),
    db: Session = Depends(get_db),
    meta: RequestMeta = Depends(request_meta),
) -> EcgLiveOut:
    sess = _own_session(db, s, session_id)
    if sess.status != "live":
        raise conflict("session_ended", "This live ECG has already ended")
    rec = finalize(db, sess, quality=(body.quality if body else "unknown"))
    audit.record(
        db,
        actor=s.owner,
        action="ecg.live_stop",
        patient_id=s.patient.id,
        resource_type="ecg",
        resource_id=rec.id if rec else sess.id,
        meta=meta,
    )
    db.commit()
    return live_out(sess)
