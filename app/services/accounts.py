"""Account helpers shared by sign-up, the link API and admin tools.

Eligibility rules (one place, used by the API and the web app's status banner):
- Everyone needs a verified email address before they can request or approve links.
- Doctors and clinics also need CardiosensAI verification AND two-step sign-in (TOTP) before
  they can link to patients or see any patient data. Until then they can sign in and finish setup.
- Admins never link and never see health data.
"""

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.core.ids import new_unique_id
from app.core.security import new_token, sha256_hex
from app.models import ActionToken, Organization, ProfessionalProfile, User
from app.schemas.auth import AccountStatusOut


def allocate_unique_id(db: Session, role: str) -> str:
    for _ in range(20):
        uid = new_unique_id(role)
        taken = db.scalar(select(User.id).where(User.unique_id == uid)) or db.scalar(
            select(Organization.id).where(Organization.unique_id == uid)
        )
        if taken is None:
            return uid
    raise RuntimeError("Could not allocate a unique ID")


def issue_action_token(db: Session, user: User, purpose: str, email: str, ttl_s: int) -> str:
    """Create a single-use emailed token; any older unused token for the same purpose stops working."""
    now = utcnow()
    for old in db.scalars(
        select(ActionToken).where(ActionToken.user_id == user.id, ActionToken.purpose == purpose, ActionToken.used_at.is_(None))
    ):
        old.used_at = now
    token = new_token()
    db.add(
        ActionToken(user_id=user.id, purpose=purpose, email=email, token_hash=sha256_hex(token), expires_at=now + timedelta(seconds=ttl_s))
    )
    return token


def consume_action_token(db: Session, token: str, purpose: str) -> ActionToken | None:
    row = db.scalar(select(ActionToken).where(ActionToken.token_hash == sha256_hex(token)).with_for_update())
    if row is None or row.purpose != purpose or row.used_at is not None or row.expires_at <= utcnow():
        return None
    row.used_at = utcnow()
    return row


def organization_of(db: Session, user: User) -> Organization | None:
    return db.get(Organization, user.organization_id) if user.role == "clinic" and user.organization_id else None


def verification_status(db: Session, user: User) -> str | None:
    if user.role == "doctor":
        prof = db.get(ProfessionalProfile, user.id)
        return prof.verification_status if prof else "missing"
    if user.role == "clinic":
        org = organization_of(db, user)
        return org.verification_status if org else "missing"
    return None


def account_status(db: Session, user: User) -> AccountStatusOut:
    missing: list[str] = []
    email_ok = user.email_verified_at is not None
    if not email_ok:
        missing.append("verify_email")
    professional = user.role in ("doctor", "clinic")
    vstatus = verification_status(db, user)
    if professional:
        if vstatus != "verified":
            missing.append("verification")
        if not user.mfa_enabled:
            missing.append("enable_mfa")
    elif user.role == "admin" and not user.mfa_enabled:
        missing.append("enable_mfa")
    can_link = user.role != "admin" and user.status == "active" and not missing
    return AccountStatusOut(
        role=user.role,
        unique_id=user.unique_id,
        email_verified=email_ok,
        mfa_enabled=user.mfa_enabled,
        mfa_required=professional or user.role == "admin",
        verification_status=vstatus,
        can_link=can_link,
        can_view_patients=can_link and professional,
        missing=missing,
    )


def can_view_patients(db: Session, user: User) -> bool:
    """Doctors and clinics: verified by staff and using two-step sign-in."""
    if user.role not in ("doctor", "clinic") or user.status != "active" or not user.mfa_enabled:
        return False
    return verification_status(db, user) == "verified"
