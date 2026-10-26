"""Administration for CardiosensAI staff.

Admins manage accounts, device models, professional verification and operations — but
these endpoints never return health values, and admin accounts are refused by every
health-data endpoint (see services/authz.py).
"""

import uuid
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import EmailStr, Field
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.api.deps import RequestMeta, request_meta, require_admin
from app.api.v1.audit import audit_out
from app.api.v1.devices import _model_out
from app.core.config import get_settings
from app.core.db import get_db, utcnow
from app.core.errors import bad_request, conflict, not_found
from app.models import AuditEvent, Device, DeviceModel, IngestionRejection, Organization, ProfessionalProfile, RefreshToken, Report, User
from app.schemas.common import CamelModel
from app.schemas.health import DeviceModelOut
from app.schemas.sharing import AuditOut
from app.services import audit, email
from app.services.accounts import issue_action_token
from app.services.email import mask_email

router = APIRouter(prefix="/admin", tags=["admin"])


class AdminUserOut(CamelModel):
    id: str
    unique_id: str | None
    role: str
    status: str
    phone_masked: str | None
    email_masked: str | None
    email_verified: bool
    has_password: bool
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


def _mask(phone: str | None) -> str | None:
    if phone is None:
        return None
    return phone if phone.startswith("deleted:") else phone[:3] + "•" * max(0, len(phone) - 7) + phone[-4:]


class SetEmailIn(CamelModel):
    email: EmailStr


class ProfessionalQueueOut(CamelModel):
    user_id: str
    unique_id: str | None
    full_name: str | None
    registration_number: str
    council: str
    specialty: str | None
    verification_status: str
    submitted_at: str


class OrganizationQueueOut(CamelModel):
    id: str
    unique_id: str
    name: str
    address: str | None
    city: str | None
    registration_number: str | None
    phone_masked: str | None
    owner_name: str | None
    verification_status: str
    created_at: str


@router.get("/users", response_model=list[AdminUserOut])
def list_users(
    role: str | None = None,
    status: str | None = None,
    q: str | None = Query(default=None, max_length=40, description="Matches name, 16-digit ID or last digits of phone"),
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
        stmt = stmt.where(or_(User.full_name.ilike(f"%{q}%"), User.phone.like(f"%{q}"), User.unique_id == q.replace(" ", "")))
    users = list(db.scalars(stmt.order_by(User.created_at.desc()).limit(limit)))
    counts = dict(db.execute(select(Device.patient_id, func.count()).where(Device.status == "active").group_by(Device.patient_id)).all())
    profs = {p.user_id: p.verification_status for p in db.scalars(select(ProfessionalProfile))}
    return [
        AdminUserOut(
            id=str(u.id),
            unique_id=u.unique_id,
            role=u.role,
            status=u.status,
            phone_masked=_mask(u.phone),
            email_masked=mask_email(u.email) if u.email else None,
            email_verified=u.email_verified_at is not None,
            has_password=u.password_hash is not None,
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


@router.post("/users/{user_id}/email", status_code=204, summary="Set a user's sign-in email and send them a set-password link")
def set_user_email(
    user_id: uuid.UUID,
    body: SetEmailIn,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    meta: RequestMeta = Depends(request_meta),
) -> None:
    """Moves an existing phone-only (app) account to email sign-in. The address is confirmed when the link is used."""
    s = get_settings()
    u = db.get(User, user_id)
    if u is None or u.status == "deleted":
        raise not_found("User")
    addr = body.email.lower()
    other = db.scalar(select(User.id).where(func.lower(User.email) == addr))
    if other is not None and other != u.id:
        raise conflict("email_taken", "Another account already uses this email address")
    if u.email != addr:
        u.email = addr
        u.email_verified_at = None
    token = issue_action_token(db, u, "reset_password", addr, max(s.password_reset_ttl_s, 72 * 3600))
    audit.record(db, actor=admin, action="admin.user_set_email", resource_type="user", resource_id=u.id, detail=mask_email(addr), meta=meta)
    db.commit()
    email.send_password_reset(addr, u.full_name, token, first_time=u.password_hash is None)


@router.get("/professionals", response_model=list[ProfessionalQueueOut], summary="Doctors' registration details (verification queue)")
def list_professionals(
    status: Literal["pending", "verified", "rejected"] | None = "pending",
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[ProfessionalQueueOut]:
    stmt = select(ProfessionalProfile, User).join(User, User.id == ProfessionalProfile.user_id).where(User.status != "deleted")
    if status:
        stmt = stmt.where(ProfessionalProfile.verification_status == status)
    return [
        ProfessionalQueueOut(
            user_id=str(u.id),
            unique_id=u.unique_id,
            full_name=u.full_name,
            registration_number=p.registration_number,
            council=p.council,
            specialty=p.specialty,
            verification_status=p.verification_status,
            submitted_at=p.submitted_at.isoformat(),
        )
        for p, u in db.execute(stmt.order_by(ProfessionalProfile.submitted_at).limit(200)).all()
    ]


@router.get("/organizations", response_model=list[OrganizationQueueOut], summary="Clinics (verification queue)")
def list_organizations(
    status: Literal["pending", "verified", "rejected"] | None = "pending",
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[OrganizationQueueOut]:
    stmt = select(Organization)
    if status:
        stmt = stmt.where(Organization.verification_status == status)
    out = []
    for o in db.scalars(stmt.order_by(Organization.created_at).limit(200)):
        owner = db.get(User, o.owner_user_id) if o.owner_user_id else None
        out.append(
            OrganizationQueueOut(
                id=str(o.id),
                unique_id=o.unique_id,
                name=o.name,
                address=o.address,
                city=o.city,
                registration_number=o.registration_number,
                phone_masked=_mask(o.phone),
                owner_name=owner.full_name if owner else None,
                verification_status=o.verification_status,
                created_at=o.created_at.isoformat(),
            )
        )
    return out


@router.post("/organizations/{org_id}/verify", status_code=204, summary="Approve or reject a clinic")
def verify_organization(
    org_id: uuid.UUID,
    body: VerifyIn,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    meta: RequestMeta = Depends(request_meta),
) -> None:
    org = db.get(Organization, org_id)
    if org is None:
        raise not_found("Clinic")
    org.verification_status = body.decision
    org.verified_by = admin.id
    org.verified_at = utcnow()
    audit.record(
        db,
        actor=admin,
        action=f"admin.organization_{body.decision}",
        resource_type="organization",
        resource_id=org.id,
        detail=body.note or org.registration_number,
        meta=meta,
    )
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
