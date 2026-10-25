"""Patient-controlled sharing: grants, single-use invites, revocation, and the viewer's list of patients."""

import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.api.deps import RequestMeta, get_current_user, request_meta, require_patient
from app.core.config import get_settings
from app.core.db import get_db, utcnow
from app.core.errors import ApiError, bad_request, forbidden, not_found
from app.core.ratelimit import limiter
from app.core.security import new_token, sha256_hex
from app.models import AccessGrant, Invite, Measurement, User
from app.schemas.sharing import GrantCreatedOut, GrantIn, GrantOut, InviteAcceptIn, SharedPatientOut
from app.services import audit
from app.services.authz import VITALS, is_verified_professional
from app.services.notifications import notify
from app.services.serializers import grant_out, grant_status

router = APIRouter(tags=["sharing"])

ROLE_WORD = {"family": "family member", "caregiver": "caregiver", "doctor": "doctor"}


@router.post("/grants", response_model=GrantCreatedOut, status_code=201, summary="Invite someone and choose exactly what they can see")
def create_grant(
    body: GrantIn, user: User = Depends(require_patient), db: Session = Depends(get_db), meta: RequestMeta = Depends(request_meta)
) -> GrantCreatedOut:
    s = get_settings()
    now = utcnow()
    if body.expires_at is not None and body.expires_at <= now:
        raise bad_request("invalid_expiry", "Expiry must be in the future")
    limiter.hit(f"grant:{user.id}", 30, 3600)
    grant = AccessGrant(patient_id=user.id, **body.model_dump())
    db.add(grant)
    db.flush()
    token = new_token()
    invite = Invite(grant_id=grant.id, token_hash=sha256_hex(token), expires_at=now + timedelta(seconds=s.invite_ttl_s))
    db.add(invite)
    audit.record(
        db,
        actor=user,
        action="grant.create",
        patient_id=user.id,
        resource_type="grant",
        resource_id=grant.id,
        detail=(
            f"Invited {body.grantee_name} as {ROLE_WORD[body.role]}: {', '.join(body.vitals)}; "
            f"history {body.history}; downloads {'on' if body.downloads_allowed else 'off'}"
        ),
        meta=meta,
        grant_id=grant.id,
    )
    db.commit()
    # Delivering the link (SMS/email) is the job of the messaging integration; the token is shown once here.
    return GrantCreatedOut(
        grant=grant_out(grant),
        invite_token=token,
        invite_url=f"{s.api_base_url.rstrip('/')}/invite/{token}",
        invite_expires_at=invite.expires_at,
    )


@router.get("/grants", response_model=list[GrantOut], summary="Everyone I've shared with")
def list_grants(user: User = Depends(require_patient), db: Session = Depends(get_db)) -> list[GrantOut]:
    rows = db.scalars(select(AccessGrant).where(AccessGrant.patient_id == user.id).order_by(AccessGrant.created_at.desc()))
    return [grant_out(g) for g in rows]


@router.post("/grants/{grant_id}/revoke", response_model=GrantOut, summary="Stop sharing immediately")
def revoke_grant(
    grant_id: uuid.UUID, user: User = Depends(require_patient), db: Session = Depends(get_db), meta: RequestMeta = Depends(request_meta)
) -> GrantOut:
    """Access ends at once: every read and every report download link re-checks the grant.

    Copies the person already downloaded can't be recalled.
    """
    g = db.get(AccessGrant, grant_id, with_for_update=True)
    if g is None or g.patient_id != user.id:
        raise not_found("Grant")
    if g.status != "revoked":
        was_pending = g.status == "pending"
        g.status = "revoked"
        g.revoked_at = utcnow()
        db.execute(update(Invite).where(Invite.grant_id == g.id, Invite.used_at.is_(None)).values(used_at=g.revoked_at))
        audit.record(
            db,
            actor=user,
            action="grant.cancel" if was_pending else "grant.revoke",
            patient_id=user.id,
            resource_type="grant",
            resource_id=g.id,
            detail=g.grantee_name,
            meta=meta,
            grant_id=g.id,
        )
        if g.grantee_user_id:
            notify(
                db,
                user_id=g.grantee_user_id,
                patient_id=user.id,
                kind="sharing",
                title="Access ended",
                body=f"{user.full_name or 'A patient'} stopped sharing their health data with you.",
                dedupe_key=f"revoked:{g.id}",
            )
        db.commit()
    return grant_out(g)


@router.post("/invites/accept", response_model=GrantOut, summary="Accept an invite (as the signed-in family member, caregiver or doctor)")
def accept_invite(
    body: InviteAcceptIn, user: User = Depends(get_current_user), db: Session = Depends(get_db), meta: RequestMeta = Depends(request_meta)
) -> GrantOut:
    limiter.hit(f"invite:{user.id}", 20, 3600)
    invalid = ApiError(400, "invalid_invite", "This invite link is invalid, used or expired")
    inv = db.scalar(select(Invite).where(Invite.token_hash == sha256_hex(body.token)).with_for_update())
    if inv is None or inv.used_at is not None or inv.expires_at <= utcnow():
        raise invalid
    g = db.get(AccessGrant, inv.grant_id, with_for_update=True)
    if g is None or g.status != "pending" or grant_status(g) == "expired":
        raise invalid
    if g.patient_id == user.id:
        raise bad_request("own_invite", "You can't accept your own invite")
    if user.role == "admin":
        raise forbidden("Admin accounts can't receive patient data")
    if g.role == "doctor" and not is_verified_professional(db, user):
        raise forbidden("Doctor invites need a verified professional account. Submit your registration details first.")
    inv.used_at = utcnow()
    g.grantee_user_id = user.id
    g.status = "active"
    g.accepted_at = inv.used_at
    audit.record(
        db, actor=user, action="grant.accept", patient_id=g.patient_id, resource_type="grant", resource_id=g.id, meta=meta, grant_id=g.id
    )
    notify(
        db,
        user_id=g.patient_id,
        patient_id=g.patient_id,
        kind="sharing",
        title=f"{user.full_name or g.grantee_name} accepted your invite",
        body=f"They can now see: {', '.join(g.vitals)}.",
        link="/sharing",
        dedupe_key=f"accepted:{g.id}",
    )
    db.commit()
    return grant_out(g)


@router.get(
    "/shared-with-me", response_model=list[SharedPatientOut], summary="Patients who have shared data with me (doctor / caregiver dashboard)"
)
def shared_with_me(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[SharedPatientOut]:
    """There is deliberately no patient search: viewers only ever see patients who invited them."""
    if user.role == "admin":
        return []
    now = utcnow()
    grants = db.scalars(
        select(AccessGrant)
        .where(
            AccessGrant.grantee_user_id == user.id,
            AccessGrant.status == "active",
            or_(AccessGrant.expires_at.is_(None), AccessGrant.expires_at > now),
        )
        .order_by(AccessGrant.accepted_at.desc())
    )
    out: list[SharedPatientOut] = []
    verified = is_verified_professional(db, user)
    for g in grants:
        if g.role == "doctor" and not verified:
            continue
        patient = db.get(User, g.patient_id)
        if patient is None or patient.status != "active":
            continue
        vitals = [v for v in g.vitals if v in VITALS]
        floor = None if g.history == "all" else (g.created_at - timedelta(days=30) if g.history == "last_30_days" else g.created_at)
        q = select(func.max(Measurement.measured_at)).where(Measurement.patient_id == g.patient_id, Measurement.vital.in_(vitals))
        if floor is not None:
            q = q.where(Measurement.measured_at >= floor)
        out.append(
            SharedPatientOut(
                patient_id=str(patient.id),
                full_name=patient.full_name,
                patient_code=patient.patient_code,
                date_of_birth=patient.date_of_birth,
                grant=grant_out(g),
                last_measurement_at=db.scalar(q) if vitals else None,
            )
        )
    return out
