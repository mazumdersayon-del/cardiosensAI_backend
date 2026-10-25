"""The single place that decides who may see a patient's health data.

Rules:
- A patient can see all of their own data.
- Anyone else needs an ACTIVE, UNEXPIRED grant from that patient that lists the
  requested vital sign (or "ecg"), and only sees data inside the grant's history window.
- Doctor grants also require the doctor's professional verification to still be valid.
- Administrators never get health data through their admin role.
- If there is no grant at all we answer "not found", so patient existence isn't revealed.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.core.errors import forbidden, not_found
from app.models import AccessGrant, ProfessionalProfile, User

VITALS = ("hr", "spo2", "bp", "resp", "temp", "glucose")
SHAREABLE = (*VITALS, "ecg")


@dataclass
class AccessContext:
    patient: User
    via: Literal["self", "grant"]
    grant: AccessGrant | None

    @property
    def allowed(self) -> set[str]:
        return set(SHAREABLE) if self.grant is None else set(self.grant.vitals) & set(SHAREABLE)

    @property
    def history_floor(self) -> datetime | None:
        """Earliest measurement time this viewer may see (None = no limit)."""
        g = self.grant
        if g is None or g.history == "all":
            return None
        if g.history == "last_30_days":
            return g.created_at - timedelta(days=30)
        return g.created_at  # from_now

    def clamp_from(self, requested: datetime | None) -> datetime | None:
        floor = self.history_floor
        if floor is None:
            return requested
        return floor if requested is None or requested < floor else requested


def resolve_patient_id(actor: User, raw: str) -> uuid.UUID:
    if raw == "me":
        return actor.id
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise not_found("Patient") from exc


def active_grant_for(db: Session, actor: User, patient_id: uuid.UUID, now: datetime | None = None) -> AccessGrant | None:
    now = now or utcnow()
    stmt = (
        select(AccessGrant)
        .where(
            AccessGrant.patient_id == patient_id,
            AccessGrant.grantee_user_id == actor.id,
            AccessGrant.status == "active",
            or_(AccessGrant.expires_at.is_(None), AccessGrant.expires_at > now),
        )
        .order_by(AccessGrant.created_at.desc())
    )
    for grant in db.scalars(stmt):
        if grant.role == "doctor" and not is_verified_professional(db, actor):
            continue
        return grant
    return None


def is_verified_professional(db: Session, user: User) -> bool:
    if user.role != "doctor":
        return False
    prof = db.get(ProfessionalProfile, user.id)
    return prof is not None and prof.verification_status == "verified"


def get_access(
    db: Session,
    actor: User,
    patient_id: uuid.UUID,
    need: str | None = None,
    *,
    for_download: bool = False,
) -> AccessContext:
    if actor.role == "admin":
        raise not_found("Patient")
    patient = db.get(User, patient_id)
    if patient is None or patient.role != "patient" or patient.status != "active":
        raise not_found("Patient")

    if patient.id == actor.id:
        ctx = AccessContext(patient=patient, via="self", grant=None)
    else:
        grant = active_grant_for(db, actor, patient_id)
        if grant is None:
            raise not_found("Patient")
        ctx = AccessContext(patient=patient, via="grant", grant=grant)

    if need is not None and need not in ctx.allowed:
        raise forbidden("This data hasn't been shared with you")
    if for_download and ctx.grant is not None and not ctx.grant.downloads_allowed:
        raise forbidden("The patient hasn't allowed downloads")
    return ctx
