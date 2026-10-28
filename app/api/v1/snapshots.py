"""ECG snapshots from CardiosensAI devices (20 s, 12-lead, on the schedule set under Account → Devices).

Same access rules as ECG recordings (`get_access(..., need="ecg")`, history window of the grant). Linked doctors
label snapshots with SCP-ECG statements, as in PTB-XL; the labels and the capture-time research consent are what
a future dataset export will use.
"""

import hashlib
import uuid

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import AwareDatetime
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api.deps import RequestMeta, get_current_user, request_meta
from app.core.db import get_db, utcnow
from app.core.errors import ApiError, bad_request, forbidden, not_found
from app.core.storage import get_storage
from app.models import Device, EcgSnapshot, EcgSnapshotLabel, User
from app.schemas.fleet import ScpCodeOut, SnapshotLabelIn, SnapshotLabelOut, SnapshotOut
from app.services import audit
from app.services.authz import AccessContext, get_access, resolve_patient_id
from app.services.scp_codes import SCP_CODES

router = APIRouter(tags=["ecg snapshots"])


def _labels(db: Session, snapshot_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[SnapshotLabelOut]]:
    out: dict[uuid.UUID, list[SnapshotLabelOut]] = {}
    if not snapshot_ids:
        return out
    rows = db.execute(
        select(EcgSnapshotLabel, User.full_name)
        .join(User, User.id == EcgSnapshotLabel.doctor_id)
        .where(EcgSnapshotLabel.snapshot_id.in_(snapshot_ids))
        .order_by(EcgSnapshotLabel.updated_at)
    )
    for lab, name in rows:
        out.setdefault(lab.snapshot_id, []).append(
            SnapshotLabelOut(
                doctor_id=str(lab.doctor_id),
                doctor_name=name or "Doctor",
                scp_codes=dict(lab.scp_codes),
                signal_quality=lab.signal_quality,
                note=lab.note,
                updated_at=lab.updated_at,
            )
        )
    return out


def _out(s: EcgSnapshot, device_name: str | None, labels: list[SnapshotLabelOut]) -> SnapshotOut:
    return SnapshotOut(
        id=str(s.id),
        patient_id=str(s.patient_id),
        device_name=device_name,
        recorded_at=s.recorded_at,
        duration_s=s.duration_s,
        sample_rate_hz=s.sample_rate_hz,
        leads=list(s.leads),
        quality=s.quality,
        quality_detail=dict(s.quality_detail or {}),
        average_hr=s.average_hr,
        acquisition=s.acquisition,
        trigger=s.trigger,
        labels=labels,
    )


@router.get("/ecg-snapshots/scp-codes", response_model=list[ScpCodeOut], summary="SCP-ECG statements doctors can label with")
def scp_codes(_: User = Depends(get_current_user)) -> list[ScpCodeOut]:
    return [ScpCodeOut(code=c, description=d, group=g) for c, (d, g) in SCP_CODES.items()]


@router.get("/patients/{patient_id}/ecg-snapshots", response_model=list[SnapshotOut], summary="ECG snapshots, newest first")
def list_snapshots(
    patient_id: str,
    before: AwareDatetime | None = None,
    limit: int = Query(default=30, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    meta: RequestMeta = Depends(request_meta),
) -> list[SnapshotOut]:
    pid = resolve_patient_id(user, patient_id)
    ctx = get_access(db, user, pid, need="ecg")
    stmt = (
        select(EcgSnapshot, Device.display_name).outerjoin(Device, Device.id == EcgSnapshot.device_id).where(EcgSnapshot.patient_id == pid)
    )
    floor = ctx.history_floor
    if floor is not None:
        stmt = stmt.where(EcgSnapshot.recorded_at >= floor)
    if before is not None:
        stmt = stmt.where(EcgSnapshot.recorded_at < before)
    rows = list(db.execute(stmt.order_by(EcgSnapshot.recorded_at.desc()).limit(limit)))
    labels = _labels(db, [s.id for s, _ in rows])
    if ctx.via == "grant" and before is None:
        audit.record(db, actor=user, action="ecg.snapshots_list", patient_id=pid, detail="ECG snapshots", meta=meta, grant_id=ctx.grant.id)
        db.commit()
    return [_out(s, name, labels.get(s.id, [])) for s, name in rows]


def _get(db: Session, user: User, snapshot_id: uuid.UUID) -> tuple[EcgSnapshot, AccessContext]:
    s = db.get(EcgSnapshot, snapshot_id)
    if s is None:
        raise not_found("Snapshot")
    ctx = get_access(db, user, s.patient_id, need="ecg")
    if ctx.history_floor is not None and s.recorded_at < ctx.history_floor:
        raise not_found("Snapshot")
    return s, ctx


@router.get("/ecg-snapshots/{snapshot_id}", response_model=SnapshotOut)
def get_snapshot(snapshot_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> SnapshotOut:
    s, _ = _get(db, user, snapshot_id)
    device = db.get(Device, s.device_id) if s.device_id else None
    return _out(s, device.display_name if device else None, _labels(db, [s.id]).get(s.id, []))


@router.get(
    "/ecg-snapshots/{snapshot_id}/samples",
    summary="Raw waveform: int16 little-endian µV, interleaved frames in X-Leads order",
    response_class=Response,
    responses={200: {"content": {"application/octet-stream": {}}}},
)
def snapshot_samples(
    snapshot_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db), meta: RequestMeta = Depends(request_meta)
) -> Response:
    s, ctx = _get(db, user, snapshot_id)
    data = get_storage().get(s.storage_key)
    if hashlib.sha256(data).hexdigest() != s.sha256:
        raise ApiError(500, "integrity_error", "Stored waveform failed its integrity check")
    if ctx.via == "grant":
        audit.record(
            db, actor=user, action="ecg.snapshot_read", patient_id=s.patient_id, resource_type="ecg_snapshot", resource_id=s.id, meta=meta
        )
        db.commit()
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={
            "X-Sample-Rate-Hz": str(s.sample_rate_hz),
            "X-Leads": ",".join(s.leads),
            "X-Sample-Format": s.sample_format,
            "X-Calibration-mm-per-mV": "10",
            "X-Calibration-mm-per-s": "25",
            "X-Content-SHA256": s.sha256,
            "Cache-Control": "private, no-store",
        },
    )


def _labeller(db: Session, user: User, s: EcgSnapshot) -> None:
    if user.role != "doctor":
        raise forbidden("Only doctors label ECG snapshots")
    ctx = get_access(db, user, s.patient_id, need="ecg")
    if ctx.via != "grant":
        raise forbidden("Only doctors label ECG snapshots")


@router.put("/ecg-snapshots/{snapshot_id}/label", response_model=SnapshotOut, summary="A linked doctor's SCP-ECG labels for a snapshot")
def label_snapshot(
    snapshot_id: uuid.UUID,
    body: SnapshotLabelIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    meta: RequestMeta = Depends(request_meta),
) -> SnapshotOut:
    s, _ = _get(db, user, snapshot_id)
    _labeller(db, user, s)
    if not body.scp_codes or len(body.scp_codes) > 12:
        raise bad_request("bad_labels", "Choose 1 to 12 statements")
    for code, likelihood in body.scp_codes.items():
        if code not in SCP_CODES:
            raise bad_request("bad_labels", f"Unknown SCP-ECG statement: {code[:20]}")
        if not 0 <= likelihood <= 100:
            raise bad_request("bad_labels", "Likelihood is 0 to 100")
    lab = db.scalar(select(EcgSnapshotLabel).where(EcgSnapshotLabel.snapshot_id == s.id, EcgSnapshotLabel.doctor_id == user.id))
    if lab is None:
        lab = EcgSnapshotLabel(snapshot_id=s.id, doctor_id=user.id)
        db.add(lab)
    lab.scp_codes, lab.signal_quality, lab.note, lab.updated_at = dict(body.scp_codes), body.signal_quality, body.note, utcnow()
    audit.record(
        db, actor=user, action="ecg.snapshot_label", patient_id=s.patient_id, resource_type="ecg_snapshot", resource_id=s.id, meta=meta
    )
    db.commit()
    return get_snapshot(snapshot_id, user, db)


@router.delete("/ecg-snapshots/{snapshot_id}/label", status_code=204, summary="Remove my labels from a snapshot")
def unlabel_snapshot(snapshot_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> None:
    s, _ = _get(db, user, snapshot_id)
    _labeller(db, user, s)
    db.execute(delete(EcgSnapshotLabel).where(EcgSnapshotLabel.snapshot_id == s.id, EcgSnapshotLabel.doctor_id == user.id))
    db.commit()
