import base64
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import AwareDatetime
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import RequestMeta, get_current_user, request_meta, require_patient
from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import bad_request
from app.core.ratelimit import limiter
from app.models import Measurement, User
from app.schemas.health import BatchIn, BatchOut, LatestOut, MeasurementPage, Vital
from app.services import audit
from app.services.authz import VITALS, get_access, resolve_patient_id
from app.services.ingestion import ingest_batch
from app.services.serializers import measurement_out

router = APIRouter(tags=["measurements"])

VITAL_NAMES = {
    "hr": "heart rate",
    "spo2": "blood oxygen",
    "bp": "blood pressure",
    "resp": "respiration rate",
    "temp": "temperature",
    "glucose": "blood glucose",
}


@router.post(
    "/measurements:batch",
    response_model=BatchOut,
    summary="Upload readings (idempotent)",
    description=(
        "Up to 100 readings. Each reading's `id` is its idempotency key. "
        "Every item gets its own result, so one bad reading never blocks the rest."
    ),
)
def upload_batch(body: BatchIn, user: User = Depends(require_patient), db: Session = Depends(get_db)) -> BatchOut:
    s = get_settings()
    if len(body.measurements) > s.max_batch_size:
        raise bad_request("batch_too_large", f"Send at most {s.max_batch_size} readings per request")
    limiter.hit(f"ingest:{user.id}", 600, 60)
    results = ingest_batch(db, user, body.measurements)
    db.commit()
    return BatchOut(results=results)


def _encode_cursor(m: Measurement) -> str:
    return base64.urlsafe_b64encode(f"{m.measured_at.isoformat()}|{m.id}".encode()).decode()


def _decode_cursor(c: str) -> tuple[datetime, uuid.UUID]:
    try:
        ts, mid = base64.urlsafe_b64decode(c.encode()).decode().split("|")
        return datetime.fromisoformat(ts), uuid.UUID(mid)
    except (ValueError, UnicodeDecodeError) as exc:
        raise bad_request("invalid_cursor", "Invalid cursor") from exc


@router.get(
    "/patients/{patient_id}/measurements",
    response_model=MeasurementPage,
    summary="Reading history (newest first, paginated)",
    description="`patient_id` may be `me`. Viewers with a sharing grant only see the vital signs and history window the patient allowed.",
)
def list_measurements(
    patient_id: str,
    vital: Vital | None = None,
    from_: AwareDatetime | None = Query(default=None, alias="from"),
    to: AwareDatetime | None = None,
    limit: int = Query(default=200, ge=1, le=1000),
    cursor: str | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    meta: RequestMeta = Depends(request_meta),
) -> MeasurementPage:
    pid = resolve_patient_id(user, patient_id)
    ctx = get_access(db, user, pid, need=vital)
    allowed = [v for v in VITALS if v in ctx.allowed]
    start = ctx.clamp_from(from_)

    stmt = select(Measurement).where(Measurement.patient_id == pid, Measurement.vital.in_([vital] if vital else allowed))
    if start is not None:
        stmt = stmt.where(Measurement.measured_at >= start)
    if to is not None:
        stmt = stmt.where(Measurement.measured_at < to)
    if cursor:
        cts, cid = _decode_cursor(cursor)
        stmt = stmt.where(or_(Measurement.measured_at < cts, and_(Measurement.measured_at == cts, Measurement.id < cid)))
    rows = list(db.scalars(stmt.order_by(Measurement.measured_at.desc(), Measurement.id.desc()).limit(limit + 1)))
    next_cursor = _encode_cursor(rows[limit - 1]) if len(rows) > limit else None
    rows = rows[:limit]

    if ctx.via == "grant":
        audit.record(
            db,
            actor=user,
            action="measurements.read",
            patient_id=pid,
            detail=f"{VITAL_NAMES[vital] if vital else 'all shared vitals'} history",
            meta=meta,
            grant_id=ctx.grant.id if ctx.grant else None,
        )
        db.commit()
    return MeasurementPage(items=[measurement_out(m) for m in rows], next_cursor=next_cursor, history_limited_from=ctx.history_floor)


@router.get("/patients/{patient_id}/measurements/latest", response_model=LatestOut, summary="Newest reading per vital sign")
def latest(
    patient_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db), meta: RequestMeta = Depends(request_meta)
) -> LatestOut:
    pid = resolve_patient_id(user, patient_id)
    ctx = get_access(db, user, pid)
    allowed = [v for v in VITALS if v in ctx.allowed]
    floor = ctx.history_floor
    sub = select(Measurement.vital, func.max(Measurement.measured_at).label("mx")).where(
        Measurement.patient_id == pid, Measurement.vital.in_(allowed)
    )
    if floor is not None:
        sub = sub.where(Measurement.measured_at >= floor)
    sub = sub.group_by(Measurement.vital).subquery()
    rows = db.scalars(
        select(Measurement)
        .join(sub, and_(Measurement.vital == sub.c.vital, Measurement.measured_at == sub.c.mx))
        .where(Measurement.patient_id == pid)
        .order_by(Measurement.id)
    )
    latest_by: dict[str, Measurement] = {}
    for m in rows:
        latest_by.setdefault(m.vital, m)
    if ctx.via == "grant":
        audit.record(
            db,
            actor=user,
            action="measurements.read",
            patient_id=pid,
            detail="latest readings",
            meta=meta,
            grant_id=ctx.grant.id if ctx.grant else None,
        )
        db.commit()
    return LatestOut(
        latest={v: (measurement_out(latest_by[v]) if v in latest_by else None) for v in allowed}, shared_vitals=sorted(ctx.allowed)
    )
