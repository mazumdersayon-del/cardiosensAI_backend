"""Administration for CardiosensAI staff (the /admin portal of the web app).

Admins verify doctors and clinics, delete accounts (30-day grace, then erased), see platform
statistics and manage device models — but these endpoints never return health values, and admin
accounts are refused by every health-data endpoint (see services/authz.py). Every change is audited.
"""

import csv
import io
import uuid
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import EmailStr, Field, model_validator
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from app.api.deps import RequestMeta, request_meta, require_admin
from app.api.v1.audit import audit_out
from app.api.v1.devices import _model_out
from app.api.v1.verification import VerificationFileOut, file_link, files_of
from app.core.config import get_settings
from app.core.db import get_db, utcnow
from app.core.errors import bad_request, conflict, forbidden, not_found
from app.core.storage import get_storage
from app.models import (
    AuditEvent,
    Device,
    DeviceModel,
    IngestionRejection,
    Organization,
    ProfessionalProfile,
    RefreshToken,
    Report,
    User,
    VerificationFile,
)
from app.schemas.common import CamelModel
from app.schemas.documents import FileLinkOut
from app.schemas.health import DeviceModelOut
from app.schemas.sharing import AuditOut
from app.services import audit, email, erasure, stats
from app.services.accounts import issue_action_token, licence_file_count
from app.services.email import mask_email

router = APIRouter(prefix="/admin", tags=["admin"])

UserStatus = Literal["active", "suspended", "deleting", "deleted", "all"]
VStatus = Literal["pending", "verified", "rejected", "revoked"]


class AdminUserOut(CamelModel):
    id: str
    unique_id: str | None
    role: str
    status: str
    phone_masked: str | None
    email_masked: str | None
    email_verified: bool
    has_password: bool
    mfa_enabled: bool
    full_name: str | None
    organization_name: str | None = None
    created_at: str
    last_active_at: str | None
    erase_after: str | None
    device_count: int
    verification_status: str | None = None


class AdminUserPage(CamelModel):
    items: list[AdminUserOut]
    total: int
    offset: int
    limit: int


class UserStatusIn(CamelModel):
    status: Literal["active", "suspended"]
    reason: str = Field(min_length=3, max_length=200)


class ReasonIn(CamelModel):
    reason: str = Field(min_length=3, max_length=200)


class RestoreIn(CamelModel):
    reason: str | None = Field(default=None, max_length=200)


class EraseIn(CamelModel):
    confirm_unique_id: str = Field(description="The account's 16-digit ID, typed again to confirm")


class DeletionOut(CamelModel):
    status: str
    erase_after: str | None


class VerifyIn(CamelModel):
    decision: Literal["verified", "rejected", "revoked"]
    note: str | None = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def _reason(self) -> "VerifyIn":
        if self.decision != "verified" and len((self.note or "").strip()) < 3:
            raise ValueError("Give a reason (it is sent to the doctor or clinic)")
        return self


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


class SetEmailIn(CamelModel):
    email: EmailStr


class ProfessionalQueueOut(CamelModel):
    user_id: str
    unique_id: str | None
    full_name: str | None
    email_masked: str | None
    account_status: str
    registration_number: str
    council: str
    specialty: str | None
    verification_status: str
    review_note: str | None
    submitted_at: str
    decided_at: str | None
    files: list[VerificationFileOut]


class OrganizationQueueOut(CamelModel):
    id: str
    unique_id: str
    name: str
    address: str | None
    city: str | None
    registration_number: str | None
    phone_masked: str | None
    owner_user_id: str | None
    owner_name: str | None
    owner_email_masked: str | None
    account_status: str | None
    verification_status: str
    review_note: str | None
    created_at: str
    decided_at: str | None
    files: list[VerificationFileOut]


def _mask(phone: str | None) -> str | None:
    if phone is None:
        return None
    return phone if phone.startswith("deleted:") else phone[:3] + "•" * max(0, len(phone) - 7) + phone[-4:]


def _iso(dt) -> str | None:  # noqa: ANN001
    return dt.isoformat() if dt else None


# ---------------------------------------------------------------- users


def _users_query(role: str | None, status: UserStatus | None, verification: VStatus | None, q: str | None):  # noqa: ANN202
    stmt = select(User)
    if role:
        stmt = stmt.where(User.role == role)
    if status is None:
        stmt = stmt.where(User.status != "deleted")
    elif status != "all":
        stmt = stmt.where(User.status == status)
    if verification:
        stmt = (
            stmt.outerjoin(ProfessionalProfile, ProfessionalProfile.user_id == User.id)
            .outerjoin(Organization, and_(User.role == "clinic", Organization.id == User.organization_id))
            .where(or_(ProfessionalProfile.verification_status == verification, Organization.verification_status == verification))
        )
    if q and q.strip():
        q = q.strip()
        digits = q.replace(" ", "").replace("-", "").lstrip("+")
        if digits.isdigit() and len(digits) >= 4:  # 16-digit ID (or its start) / last digits of a phone number
            stmt = stmt.where(or_(User.unique_id.startswith(digits), User.phone.endswith(digits)))
        else:
            like = f"%{q.lower()}%"
            stmt = stmt.where(or_(func.lower(User.full_name).like(like), func.lower(User.email).like(like)))
    return stmt


def _user_rows(db: Session, users: list[User]) -> list[AdminUserOut]:
    ids = [u.id for u in users]
    counts = (
        dict(
            db.execute(
                select(Device.patient_id, func.count())
                .where(Device.status == "active", Device.patient_id.in_(ids))
                .group_by(Device.patient_id)
            ).all()
        )
        if ids
        else {}
    )
    profs = (
        {p.user_id: p.verification_status for p in db.scalars(select(ProfessionalProfile).where(ProfessionalProfile.user_id.in_(ids)))}
        if ids
        else {}
    )
    org_ids = {u.organization_id for u in users if u.role == "clinic" and u.organization_id}
    orgs = {o.id: o for o in db.scalars(select(Organization).where(Organization.id.in_(org_ids)))} if org_ids else {}
    out = []
    for u in users:
        org = orgs.get(u.organization_id) if u.role == "clinic" and u.organization_id else None
        vstatus = (
            profs.get(u.id, "missing")
            if u.role == "doctor"
            else (org.verification_status if org else ("missing" if u.role == "clinic" else None))
        )
        out.append(
            AdminUserOut(
                id=str(u.id),
                unique_id=u.unique_id,
                role=u.role,
                status=u.status,
                phone_masked=_mask(u.phone),
                email_masked=mask_email(u.email) if u.email else None,
                email_verified=u.email_verified_at is not None,
                has_password=u.password_hash is not None,
                mfa_enabled=u.mfa_enabled,
                full_name=u.full_name,
                organization_name=org.name if org else None,
                created_at=u.created_at.isoformat(),
                last_active_at=_iso(u.last_active_at),
                erase_after=_iso(u.erase_after),
                device_count=counts.get(u.id, 0),
                verification_status=vstatus,
            )
        )
    return out


@router.get("/users", response_model=AdminUserPage, summary="Find accounts (no health data). Deleted accounts only with status=deleted|all")
def list_users(
    role: Literal["patient", "doctor", "clinic", "admin"] | None = None,
    status: UserStatus | None = None,
    verification: VStatus | None = None,
    q: str | None = Query(
        default=None, max_length=80, description="Name or email (contains), or digits: start of the 16-digit ID / end of the phone"
    ),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AdminUserPage:
    stmt = _users_query(role, status, verification, q)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    users = list(db.scalars(stmt.order_by(User.created_at.desc(), User.id).offset(offset).limit(limit)))
    return AdminUserPage(items=_user_rows(db, users), total=total, offset=offset, limit=limit)


def _csv(rows: list[list[object]], filename: str) -> Response:
    buf = io.StringIO()
    w = csv.writer(buf)
    for r in rows:
        # Neutralise spreadsheet formulas in user-entered text.
        w.writerow(["'" + v if isinstance(v, str) and v[:1] in ("=", "+", "-", "@") else v for v in r])
    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"},
    )


@router.get("/users.csv", response_class=Response, summary="The filtered account list as CSV (no health data)")
def export_users(
    role: Literal["patient", "doctor", "clinic", "admin"] | None = None,
    status: UserStatus | None = None,
    verification: VStatus | None = None,
    q: str | None = Query(default=None, max_length=80),
    full_emails: bool = Query(default=False, alias="fullEmails", description="Include full email addresses (audited)"),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    meta: RequestMeta = Depends(request_meta),
) -> Response:
    users = list(db.scalars(_users_query(role, status, verification, q).order_by(User.created_at.desc(), User.id).limit(20000)))
    rows: list[list[object]] = [
        [
            "unique_id",
            "role",
            "status",
            "name",
            "clinic",
            "email",
            "email_verified",
            "two_step",
            "verification",
            "devices",
            "created_at",
            "last_active_at",
            "erase_after",
        ]
    ]
    for u, out in zip(users, _user_rows(db, users), strict=True):
        rows.append(
            [
                out.unique_id or "",
                out.role,
                out.status,
                out.full_name or "",
                out.organization_name or "",
                (u.email or "") if full_emails else (out.email_masked or ""),
                "yes" if out.email_verified else "no",
                "yes" if out.mfa_enabled else "no",
                out.verification_status or "",
                out.device_count,
                out.created_at,
                out.last_active_at or "",
                out.erase_after or "",
            ]
        )
    audit.record(
        db, actor=admin, action="admin.users_export", detail=f"{len(users)} rows, {'full' if full_emails else 'masked'} emails", meta=meta
    )
    db.commit()
    return _csv(rows, f"cardiosensai-users-{utcnow():%Y%m%d}.csv")


def _target(db: Session, admin: User, user_id: uuid.UUID) -> User:
    u = db.get(User, user_id)
    if u is None or u.status == "deleted":
        raise not_found("User")
    if u.id == admin.id:
        raise bad_request("self_change", "You can't do this to your own account")
    if u.role == "admin":
        raise forbidden("Staff accounts can't be deleted from the portal. Use the server's seed/CLI script.")
    return u


@router.patch("/users/{user_id}/status", status_code=204, summary="Suspend (blocks sign-in, keeps data) or reactivate an account")
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
    if u.status == "deleting":
        raise conflict("deleting", "This account is scheduled for deletion. Restore it instead.")
    u.status = body.status
    if body.status == "suspended":
        db.execute(update(RefreshToken).where(RefreshToken.user_id == u.id, RefreshToken.revoked_at.is_(None)).values(revoked_at=utcnow()))
    audit.record(db, actor=admin, action=f"admin.user_{body.status}", resource_type="user", resource_id=u.id, detail=body.reason, meta=meta)
    db.commit()


@router.post("/users/{user_id}/delete", response_model=DeletionOut, summary="Delete an account: blocked now, erased after 30 days")
def delete_user(
    user_id: uuid.UUID,
    body: ReasonIn,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    meta: RequestMeta = Depends(request_meta),
) -> DeletionOut:
    u = _target(db, admin, user_id)
    if u.status == "deleting":
        raise conflict("already_deleting", "This account is already scheduled for deletion")
    when = erasure.schedule_deletion(db, u, admin=admin, reason=body.reason.strip(), meta=meta)
    to, name = u.email, u.full_name
    db.commit()
    if to:
        email.send_deletion_scheduled(to, name, f"{when:%d %b %Y}")
    return DeletionOut(status=u.status, erase_after=when.isoformat())


@router.post("/users/{user_id}/restore", response_model=DeletionOut, summary="Cancel a scheduled deletion")
def restore_user(
    user_id: uuid.UUID,
    body: RestoreIn,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    meta: RequestMeta = Depends(request_meta),
) -> DeletionOut:
    u = _target(db, admin, user_id)
    if u.status != "deleting":
        raise conflict("not_deleting", "This account isn't scheduled for deletion")
    erasure.restore(db, u, admin=admin, reason=(body.reason or "").strip() or None, meta=meta)
    to, name = u.email, u.full_name
    db.commit()
    if to:
        email.send_account_restored(to, name)
    return DeletionOut(status=u.status, erase_after=None)


@router.post("/users/{user_id}/erase", status_code=204, summary="Erase a scheduled account now (irreversible)")
def erase_user(
    user_id: uuid.UUID,
    body: EraseIn,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    meta: RequestMeta = Depends(request_meta),
) -> None:
    u = _target(db, admin, user_id)
    if u.status != "deleting":
        raise conflict("not_deleting", "Delete the account first; it can be erased while it waits for its grace period")
    if "".join(c for c in body.confirm_unique_id if c.isdigit()) != (u.unique_id or "-"):
        raise bad_request("confirm_mismatch", "The 16-digit ID you typed doesn't match this account")
    keys = erasure.erase_account(db, u, actor=admin, action="admin.user_erased", detail="Erased before the grace period ended", meta=meta)
    db.commit()
    storage = get_storage()
    for k in keys:
        storage.delete(k)


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
    if u is None or u.status in ("deleted", "deleting"):
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


# ---------------------------------------------------------------- verification

LIVE_ACCOUNT = ("active", "suspended")


@router.get("/professionals", response_model=list[ProfessionalQueueOut], summary="Doctors' registration details (verification queue)")
def list_professionals(
    status: VStatus | Literal["all"] = "pending",
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[ProfessionalQueueOut]:
    stmt = select(ProfessionalProfile, User).join(User, User.id == ProfessionalProfile.user_id).where(User.status.in_(LIVE_ACCOUNT))
    if status != "all":
        stmt = stmt.where(ProfessionalProfile.verification_status == status)
    return [
        ProfessionalQueueOut(
            user_id=str(u.id),
            unique_id=u.unique_id,
            full_name=u.full_name,
            email_masked=mask_email(u.email) if u.email else None,
            account_status=u.status,
            registration_number=p.registration_number,
            council=p.council,
            specialty=p.specialty,
            verification_status=p.verification_status,
            review_note=p.review_note,
            submitted_at=p.submitted_at.isoformat(),
            decided_at=_iso(p.verified_at),
            files=files_of(db, u.id),
        )
        for p, u in db.execute(stmt.order_by(ProfessionalProfile.submitted_at).limit(500)).all()
    ]


@router.get("/organizations", response_model=list[OrganizationQueueOut], summary="Clinics (verification queue)")
def list_organizations(
    status: VStatus | Literal["all"] = "pending",
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[OrganizationQueueOut]:
    stmt = (
        select(Organization, User)
        .outerjoin(User, User.id == Organization.owner_user_id)
        .where(or_(User.id.is_(None), User.status.in_(LIVE_ACCOUNT)))
    )
    if status != "all":
        stmt = stmt.where(Organization.verification_status == status)
    return [
        OrganizationQueueOut(
            id=str(o.id),
            unique_id=o.unique_id,
            name=o.name,
            address=o.address,
            city=o.city,
            registration_number=o.registration_number,
            phone_masked=_mask(o.phone),
            owner_user_id=str(owner.id) if owner else None,
            owner_name=owner.full_name if owner else None,
            owner_email_masked=mask_email(owner.email) if owner and owner.email else None,
            account_status=owner.status if owner else None,
            verification_status=o.verification_status,
            review_note=o.review_note,
            created_at=o.created_at.isoformat(),
            decided_at=_iso(o.verified_at),
            files=files_of(db, owner.id) if owner else [],
        )
        for o, owner in db.execute(stmt.order_by(Organization.created_at).limit(500)).all()
    ]


def _check_transition(current: str, decision: str, files: int) -> None:
    allowed = {"verified": current != "verified", "rejected": current == "pending", "revoked": current == "verified"}
    if not allowed[decision]:
        raise conflict("invalid_transition", f"A {current} account can't be marked {decision}")
    if decision == "verified" and files == 0:
        raise conflict("licence_required", "No licence document uploaded yet. Ask them to upload it under Account before verifying.")


def _notify(to_user: User | None, *, clinic: bool, decision: str, note: str | None) -> None:
    if to_user is not None and to_user.email:
        email.send_verification_result(to_user.email, to_user.full_name, clinic=clinic, decision=decision, note=note)


@router.post("/organizations/{org_id}/verify", status_code=204, summary="Verify, reject (with reason) or revoke a clinic")
def verify_organization(
    org_id: uuid.UUID,
    body: VerifyIn,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    meta: RequestMeta = Depends(request_meta),
) -> None:
    org = db.get(Organization, org_id)
    owner = db.get(User, org.owner_user_id) if org is not None and org.owner_user_id else None
    if org is None or owner is None or owner.status not in LIVE_ACCOUNT:
        raise not_found("Clinic")
    _check_transition(org.verification_status, body.decision, licence_file_count(db, owner.id))
    note = (body.note or "").strip() or None
    org.verification_status = body.decision
    org.review_note = note if body.decision != "verified" else None
    org.verified_by = admin.id
    org.verified_at = utcnow()
    audit.record(
        db,
        actor=admin,
        action=f"admin.organization_{body.decision}",
        resource_type="organization",
        resource_id=org.id,
        detail=note or org.registration_number,
        meta=meta,
    )
    db.commit()
    _notify(owner, clinic=True, decision=body.decision, note=note)


@router.post("/professionals/{user_id}/verify", status_code=204, summary="Verify, reject (with reason) or revoke a doctor")
def verify_professional(
    user_id: uuid.UUID,
    body: VerifyIn,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    meta: RequestMeta = Depends(request_meta),
) -> None:
    prof = db.get(ProfessionalProfile, user_id)
    doctor = db.get(User, user_id)
    if prof is None or doctor is None or doctor.status not in LIVE_ACCOUNT:
        raise not_found("Professional profile")
    _check_transition(prof.verification_status, body.decision, licence_file_count(db, user_id))
    note = (body.note or "").strip() or None
    prof.verification_status = body.decision
    prof.review_note = note if body.decision != "verified" else None
    prof.verified_by = admin.id
    prof.verified_at = utcnow()
    audit.record(
        db,
        actor=admin,
        action=f"admin.professional_{body.decision}",
        resource_type="user",
        resource_id=user_id,
        detail=note or prof.registration_number,
        meta=meta,
    )
    db.commit()
    _notify(doctor, clinic=False, decision=body.decision, note=note)


@router.post("/verification-files/{file_id}/link", response_model=FileLinkOut, summary="Short-lived link to view a licence document")
def licence_link(file_id: uuid.UUID, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> FileLinkOut:
    f = db.get(VerificationFile, file_id)
    if f is None:
        raise not_found("File")
    return file_link(admin, f)


# ---------------------------------------------------------------- statistics


@router.get("/stats", response_model=stats.StatsOut, summary="Platform statistics: counts only, never health values")
def get_stats(
    days: int = Query(default=30, ge=1, le=365), admin: User = Depends(require_admin), db: Session = Depends(get_db)
) -> stats.StatsOut:
    return stats.compute(db, days)


@router.get("/stats.csv", response_class=Response, summary="The statistics as CSV")
def export_stats(
    days: int = Query(default=30, ge=1, le=365),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    meta: RequestMeta = Depends(request_meta),
) -> Response:
    rows = stats.as_rows(stats.compute(db, days))
    audit.record(db, actor=admin, action="admin.stats_export", detail=f"{days} days", meta=meta)
    db.commit()
    return _csv(rows, f"cardiosensai-stats-{days}d-{utcnow():%Y%m%d}.csv")


# ---------------------------------------------------------------- device models, operations, audit


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
