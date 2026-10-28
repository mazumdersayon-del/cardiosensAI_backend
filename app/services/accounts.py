"""Account helpers shared by sign-up, the link API and admin tools.

Eligibility rules (one place, used by the API and the web app's status banner):
- Everyone needs a confirmed mobile number or email address (one-time code) before they can request or approve links.
- Doctors and clinics also need CardiosensAI verification AND two-step sign-in (TOTP) before
  they can link to patients or see any patient data. Until then they can sign in and finish setup.
- Admins never link and never see health data.
"""

import uuid
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.core.ids import new_unique_id
from app.core.security import new_token, sha256_hex
from app.models import ActionToken, Organization, ProfessionalProfile, User, VerificationFile
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


def licence_file_count(db: Session, user_id: uuid.UUID) -> int:
    """Licence files uploaded by a doctor or a clinic's login (needed before CardiosensAI can verify them)."""
    return db.scalar(select(func.count()).select_from(VerificationFile).where(VerificationFile.user_id == user_id)) or 0


def review_note(db: Session, user: User) -> str | None:
    if user.role == "doctor":
        prof = db.get(ProfessionalProfile, user.id)
        return prof.review_note if prof else None
    org = organization_of(db, user)
    return org.review_note if org else None


def verification_status(db: Session, user: User) -> str | None:
    if user.role == "doctor":
        prof = db.get(ProfessionalProfile, user.id)
        return prof.verification_status if prof else "missing"
    if user.role == "clinic":
        org = organization_of(db, user)
        return org.verification_status if org else "missing"
    return None


def is_activated(user: User) -> bool:
    """Proved a mobile number or email address with a one-time code (or Google, or a password-reset link)."""
    return user.phone_verified_at is not None or user.email_verified_at is not None


def phone_holder(db: Session, phone: str, *, confirmed_only: bool = False, exclude: uuid.UUID | None = None) -> User | None:
    """The account using this mobile number. A number saved while SMS was down stays unconfirmed until its code is entered."""
    stmt = select(User).where(User.phone == phone)
    if confirmed_only:
        stmt = stmt.where(User.phone_verified_at.is_not(None))
    if exclude is not None:
        stmt = stmt.where(User.id != exclude)
    return db.scalar(stmt)


def release_unconfirmed_phone(db: Session, phone: str, exclude: uuid.UUID | None = None) -> User | None:
    """Someone just proved this number with an SMS code: take it off an account that only saved it unconfirmed (e.g. a
    sign-up during an SMS outage that typed someone else's number). Returns that account, if any."""
    holder = phone_holder(db, phone, exclude=exclude)
    if holder is None or holder.phone_verified_at is not None:
        return None
    holder.phone = None
    db.flush()
    return holder


def account_status(db: Session, user: User) -> AccountStatusOut:
    missing: list[str] = []
    email_ok = user.email_verified_at is not None
    if not is_activated(user):
        missing.append("verify_contact")
    professional = user.role in ("doctor", "clinic")
    vstatus = verification_status(db, user)
    if professional:
        if vstatus != "verified" and licence_file_count(db, user.id) == 0:
            missing.append("upload_licence")
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
        phone_verified=user.phone_verified_at is not None,
        mfa_enabled=user.mfa_enabled,
        mfa_required=professional or user.role == "admin",
        verification_status=vstatus,
        verification_note=review_note(db, user) if vstatus in ("rejected", "revoked") else None,
        can_link=can_link,
        can_view_patients=can_link and professional,
        missing=missing,
    )


def can_view_patients(db: Session, user: User) -> bool:
    """Doctors and clinics: verified by staff and using two-step sign-in."""
    if user.role not in ("doctor", "clinic") or user.status != "active" or not user.mfa_enabled:
        return False
    return verification_status(db, user) == "verified"
