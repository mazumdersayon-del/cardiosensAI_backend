"""One-time codes by SMS or email: create, deliver, check.

Codes are 6 digits, valid OTP_TTL_S (10 min), at most OTP_MAX_ATTEMPTS tries each, single use. Only an HMAC
bound to the challenge id and destination is stored. With DEV_EXPOSE_OTP (development/test only) the code is
also returned by the API.
"""

import uuid
from datetime import timedelta

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import utcnow
from app.core.errors import ApiError
from app.core.security import constant_time_equal, hash_otp, new_otp
from app.models import OtpChallenge
from app.services import email as email_service
from app.services import sms

INVALID = ApiError(400, "invalid_code", "The code is incorrect or has expired")


def destination(ch: OtpChallenge) -> str:
    return (ch.phone if ch.channel == "sms" else ch.email) or ""


def create(
    db: Session,
    *,
    channel: str,
    purpose: str,
    phone: str | None = None,
    email: str | None = None,
    user_id: uuid.UUID | None = None,
    group_id: uuid.UUID | None = None,
    payload: dict | None = None,
) -> tuple[OtpChallenge, str]:
    s = get_settings()
    code = new_otp()
    ch = OtpChallenge(
        id=uuid.uuid4(),
        purpose=purpose,
        channel=channel,
        phone=phone,
        email=email,
        user_id=user_id,
        group_id=group_id,
        payload=payload,
        code_hash="",
        expires_at=utcnow() + timedelta(seconds=s.otp_ttl_s),
    )
    ch.code_hash = hash_otp(ch.id, destination(ch), code)
    db.add(ch)
    return ch, code


def renew(ch: OtpChallenge) -> str:
    """A new code for the same challenge (resend): old code stops working, attempts reset."""
    code = new_otp()
    ch.code_hash = hash_otp(ch.id, destination(ch), code)
    ch.attempts = 0
    ch.expires_at = utcnow() + timedelta(seconds=get_settings().otp_ttl_s)
    return code


def deliver(ch: OtpChallenge, code: str, name: str | None = None) -> None:
    """Send after the commit. SMS raises 503 when it can't be sent; email failures are logged."""
    if ch.channel == "sms":
        sms.send_otp(destination(ch), code)
    else:
        email_service.send_code(destination(ch), name, code, purpose=ch.purpose)


def dev_code(code: str) -> str | None:
    s = get_settings()
    return code if s.dev_expose_otp and s.env in ("development", "test") else None


def check(db: Session, ch: OtpChallenge | None, code: str, *, purpose: str) -> OtpChallenge:
    """Raise INVALID unless `code` is right for this live challenge. Counts the attempt either way; doesn't consume."""
    s = get_settings()
    if ch is None or ch.purpose != purpose or ch.consumed_at is not None or ch.expires_at <= utcnow() or ch.attempts >= s.otp_max_attempts:
        raise INVALID
    ch.attempts += 1
    if not constant_time_equal(ch.code_hash, hash_otp(ch.id, destination(ch), code)):
        db.commit()  # keep the attempt count
        raise INVALID
    return ch


def get(db: Session, raw_id: str) -> OtpChallenge | None:
    try:
        return db.get(OtpChallenge, uuid.UUID(raw_id), with_for_update=True)
    except ValueError:
        return None
