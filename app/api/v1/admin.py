"""Administration for CardiosensAI staff.

Admins manage accounts, device models, professional verification and operations — but
these endpoints never return health values, and admin accounts are refused by every
health-data endpoint (see services/authz.py).
"""

import uuid
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import Field
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.api.deps import RequestMeta, request_meta, require_admin
from app.api.v1.audit import audit_out
from app.api.v1.devices import _model_out
from app.core.db import get_db, utcnow
from app.core.errors import bad_request, conflict, not_found
from app.models import AuditEvent, Device, DeviceModel, IngestionRejection, ProfessionalProfile, RefreshToken, Report, User
from app.schemas.common import CamelModel
from app.schemas.health import DeviceModelOut
from app.schemas.sharing import AuditOut
from app.services import audit

router = APIRouter(prefix="/admin", tags=["admin"])


class AdminUserOut(CamelModel):
    id: str
    role: str
    status: str
    phone_masked: str
    full_name: str | None
    created_at: str
    device_count: int
    professional_status: str | None = None


class UserStatusIn(CamelModel):
    status: Literal["active", "suspended"]
    reason: str = Field(min_length=3, max_length=200)


class VerifyIn(CamelModel):
    decision: Literal["verified", "rejected"]
    note: str | None = Field(default=None, max_length=200)


class DeviceModelIn(CamelModel):
    code: str = Field(pattern=r"^[a-z0-9][a-z0-9\-]{2,63}$")
    name: str = Field(max_length=120)
    manufacturer: str = Field(max_length=120)
    kind: Literal["wearable", "bp_cuff", "glucose_meter"]
    capabilities: list[Literal["hr", "spo2", "bp", "resp", "temp", "glucose", "ecg"]] = Field(min_length=1)
    integration: Literal["ble_standard", "ble_vendor", "cloud_api"]
    payload_schema_version: str = "1"
    regulatory_note: str | None = Field(default=None, max_length=300)
    active: bool = True


class OpsOut(CamelModel):
    window_hours: int
    rejections_by_reason: dict[str, int]
    devices_with_sync_errors: int
    reports_failed: int
    users_by_role: dict[str, int]


def _mask(phone: str) -> str:
    return phone if phone.startswith("deleted:") else phone[:3] + "•" * max(0, len(phone) - 7) + phone[-4:]


@router.get("/users", response_model=list[AdminUserOut])
def list_users(
    role: str | None = None,
    status: str | None = None,
    q: str | None = Query(default=None, max_length=40, description="Matches name or last digits of phone"),
    limit: int = Query(default=50, ge=1, le=200),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[AdminUserOut]:
    stmt = select(User)
    if role:
        stmt = stmt.where(User.role == role)
    if status:
        stmt = stmt.where(User.status == status)
    if q:
        stmt = stmt.where(or_(User.full_name.ilike(f"%{q}%"), User.phone.like(f"%{q}")))
    users = list(db.scalars(stmt.order_by(User.created_at.desc()).limit(limit)))
    counts = dict(db.execute(select(Device.patient_id, func.count()).where(Device.status == "active").group_by(Device.patient_id)).all())
    profs = {p.user_id: p.verification_status for p in db.scalars(select(ProfessionalProfile))}
    return [
        AdminUserOut(
            id=str(u.id),
            role=u.role,
            status=u.status,
            phone_masked=_mask(u.phone),
            full_name=u.full_name,
            created_at=u.created_at.isoformat(),
            device_count=counts.get(u.id, 0),
            professional_status=profs.get(u.id),
        )
        for u in users
    ]


@router.patch("/users/{user_id}/status", status_code=204)
def set_user_status(
    user_id: uuid.UUID,
    body: UserStatusIn,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    meta: RequestMeta = Depends(request_meta),
) -> None:
    u = db.get(User, user_id)
    if u is None or u.status == "deleted":
        raise not_found("User")
    if u.id == admin.id:
        raise bad_request("self_change", "You can't change your own status")
    u.status = body.status
    if body.status == "suspended":
        db.execute(update(RefreshToken).where(RefreshToken.user_id == u.id, RefreshToken.revoked_at.is_(None)).values(revoked_at=utcnow()))
    audit.record(db, actor=admin, action=f"admin.user_{body.status}", resource_type="user", resource_id=u.id, detail=body.reason, meta=meta)
    db.commit()


@router.post("/professionals/{user_id}/verify", status_code=204, summary="Approve or reject a doctor's registration")
def verify_professional(
    user_id: uuid.UUID,
    body: VerifyIn,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    meta: RequestMeta = Depends(request_meta),
) -> None:
    prof = db.get(ProfessionalProfile, user_id)
    if prof is None:
        raise not_found("Professional profile")
    prof.verification_status = body.decision
    prof.verified_by = admin.id
    prof.verified_at = utcnow()
    audit.record(
        db,
        actor=admin,
        action=f"admin.professional_{body.decision}",
        resource_type="user",
        resource_id=user_id,
        detail=body.note or prof.registration_number,
        meta=meta,
    )
    db.commit()


@router.get("/device-models", response_model=list[DeviceModelOut])
def admin_list_models(admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> list[DeviceModelOut]:
    return [_model_out(m) for m in db.scalars(select(DeviceModel).order_by(DeviceModel.name))]


@router.post("/device-models", response_model=DeviceModelOut, status_code=201)
def create_model(
    body: DeviceModelIn, admin: User = Depends(require_admin), db: Session = Depends(get_db), meta: RequestMeta = Depends(request_meta)
) -> DeviceModelOut:
    if db.scalar(select(DeviceModel.id).where(DeviceModel.code == body.code)):
        raise conflict("code_taken", "A model with this code already exists")
    m = DeviceModel(**body.model_dump())
    db.add(m)
    db.flush()
    audit.record(
        db, actor=admin, action="admin.device_model_create", resource_type="device_model", resource_id=m.id, detail=body.code, meta=meta
    )
    db.commit()
    return _model_out(m)


@router.put("/device-models/{code}", response_model=DeviceModelOut)
def update_model(
    code: str,
    body: DeviceModelIn,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    meta: RequestMeta = Depends(request_meta),
) -> DeviceModelOut:
    m = db.scalar(select(DeviceModel).where(DeviceModel.code == code))
    if m is None:
        raise not_found("Device model")
    if body.code != code:
        raise bad_request("code_immutable", "A model's code can't be changed")
    for k, v in body.model_dump().items():
        setattr(m, k, v)
    audit.record(
        db,
        actor=admin,
        action="admin.device_model_update",
        resource_type="device_model",
        resource_id=m.id,
        detail=f"{code}: capabilities {', '.join(body.capabilities)}",
        meta=meta,
    )
    db.commit()
    return _model_out(m)


@router.get("/ops", response_model=OpsOut, summary="Operational health: ingestion rejections, sync failures, report failures")
def ops(
    window_hours: int = Query(default=24, ge=1, le=24 * 30), admin: User = Depends(require_admin), db: Session = Depends(get_db)
) -> OpsOut:
    since = utcnow() - timedelta(hours=window_hours)
    rejections = dict(
        db.execute(
            select(IngestionRejection.reason_code, func.count())
            .where(IngestionRejection.created_at >= since)
            .group_by(IngestionRejection.reason_code)
        ).all()
    )
    sync_errors = (
        db.scalar(select(func.count()).select_from(Device).where(Device.status == "active", Device.last_sync_error_at >= since)) or 0
    )
    failed = db.scalar(select(func.count()).select_from(Report).where(Report.status == "failed", Report.created_at >= since)) or 0
    roles = dict(db.execute(select(User.role, func.count()).where(User.status != "deleted").group_by(User.role)).all())
    return OpsOut(
        window_hours=window_hours,
        rejections_by_reason=rejections,
        devices_with_sync_errors=sync_errors,
        reports_failed=failed,
        users_by_role=roles,
    )


@router.get("/audit-events", response_model=list[AuditOut], summary="Security audit trail (no health values)")
def admin_audit(
    action: str | None = None,
    actor_user_id: uuid.UUID | None = Query(default=None, alias="actorUserId"),
    limit: int = Query(default=100, ge=1, le=500),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    meta: RequestMeta = Depends(request_meta),
) -> list[AuditOut]:
    stmt = select(AuditEvent)
    if action:
        stmt = stmt.where(AuditEvent.action == action)
    if actor_user_id:
        stmt = stmt.where(AuditEvent.actor_user_id == actor_user_id)
    rows = list(db.scalars(stmt.order_by(AuditEvent.at.desc()).limit(limit)))
    audit.record(db, actor=admin, action="admin.audit_view", detail=action or "all", meta=meta)
    db.commit()
    return [audit_out(e, {}) for e in rows]
