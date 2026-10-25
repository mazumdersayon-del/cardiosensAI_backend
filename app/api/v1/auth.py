"""Sign-in with a one-time code sent by SMS, optional TOTP second factor, rotating refresh tokens."""

import uuid
from datetime import timedelta

import jwt
import pyotp
from fastapi import APIRouter, Depends
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.api.deps import RequestMeta, get_current_user, request_meta
from app.core.config import get_settings
from app.core.db import get_db, utcnow
from app.core.errors import ApiError, bad_request, unauthorized
from app.core.ratelimit import limiter
from app.core.security import (
    constant_time_equal,
    create_access_token,
    create_mfa_token,
    decode_token,
    hash_otp,
    new_otp,
    new_token,
    sha256_hex,
)
from app.models import OtpChallenge, RefreshToken, User
from app.schemas.auth import (
    MfaCodeIn,
    MfaRequiredOut,
    MfaSetupOut,
    MfaVerifyIn,
    OtpRequestIn,
    OtpRequestOut,
    OtpVerifyIn,
    RefreshIn,
    TokenOut,
)
from app.services import audit
from app.services.serializers import user_out
from app.services.sms import send_otp

router = APIRouter(prefix="/auth", tags=["auth"])


def _issue_tokens(db: Session, user: User, meta: RequestMeta, family_id: uuid.UUID | None = None, new_account: bool = False) -> TokenOut:
    s = get_settings()
    refresh = new_token()
    db.add(
        RefreshToken(
            user_id=user.id,
            family_id=family_id or uuid.uuid4(),
            token_hash=sha256_hex(refresh),
            expires_at=utcnow() + timedelta(seconds=s.refresh_token_ttl_s),
            user_agent=meta.user_agent,
        )
    )
    return TokenOut(
        access_token=create_access_token(user.id, user.role),
        refresh_token=refresh,
        expires_in=s.access_token_ttl_s,
        user=user_out(user),
        new_account=new_account,
    )


def _next_patient_code(db: Session) -> str:
    # Random, non-sequential code for reports (avoids leaking account counts).
    for _ in range(10):
        code = f"CS-P-{uuid.uuid4().int % 10_000_000:07d}"
        if db.scalar(select(User.id).where(User.patient_code == code)) is None:
            return code
    raise RuntimeError("Could not allocate patient code")


@router.post("/otp/request", response_model=OtpRequestOut, summary="Send a one-time sign-in code by SMS")
def request_otp(body: OtpRequestIn, db: Session = Depends(get_db), meta: RequestMeta = Depends(request_meta)) -> OtpRequestOut:
    s = get_settings()
    limiter.hit(f"otp:phone:{body.phone}", s.rate_limit_otp_per_phone, s.rate_limit_window_s)
    limiter.hit(f"otp:ip:{meta.ip}", s.rate_limit_otp_per_ip, s.rate_limit_window_s)
    code = new_otp()
    challenge = OtpChallenge(id=uuid.uuid4(), phone=body.phone, code_hash="", expires_at=utcnow() + timedelta(seconds=s.otp_ttl_s))
    challenge.code_hash = hash_otp(challenge.id, body.phone, code)
    db.add(challenge)
    db.commit()
    send_otp(body.phone, code)
    expose = s.dev_expose_otp and s.env in ("development", "test")
    return OtpRequestOut(challenge_id=str(challenge.id), expires_in_s=s.otp_ttl_s, dev_code=code if expose else None)


@router.post("/otp/verify", response_model=TokenOut | MfaRequiredOut, summary="Verify the code; creates the account on first sign-in")
def verify_otp(body: OtpVerifyIn, db: Session = Depends(get_db), meta: RequestMeta = Depends(request_meta)) -> TokenOut | MfaRequiredOut:
    s = get_settings()
    try:
        cid = uuid.UUID(body.challenge_id)
    except ValueError as exc:
        raise bad_request("invalid_code", "The code is incorrect or has expired") from exc
    ch = db.get(OtpChallenge, cid, with_for_update=True)
    invalid = ApiError(400, "invalid_code", "The code is incorrect or has expired")
    if ch is None or ch.consumed_at is not None or ch.expires_at <= utcnow() or ch.attempts >= s.otp_max_attempts:
        raise invalid
    ch.attempts += 1
    if not constant_time_equal(ch.code_hash, hash_otp(ch.id, ch.phone, body.code)):
        db.commit()  # keep the attempt count
        raise invalid
    ch.consumed_at = utcnow()

    user = db.scalar(select(User).where(User.phone == ch.phone))
    new_account = False
    if user is None:
        user = User(
            phone=ch.phone, role=body.account_type, full_name=body.full_name, preferences={"glucoseUnit": "mg/dL", "tempUnit": "°C"}
        )
        if user.role == "patient":
            user.patient_code = _next_patient_code(db)
        db.add(user)
        db.flush()
        new_account = True
        audit.record(db, actor=user, action="account.create", detail=f"{user.role} account", meta=meta)
    elif user.status == "deleted":
        raise invalid
    elif user.status == "suspended":
        raise ApiError(403, "suspended", "This account is suspended. Contact CardiosensAI support.")

    if user.mfa_enabled:
        db.commit()
        return MfaRequiredOut(mfa_token=create_mfa_token(user.id))
    out = _issue_tokens(db, user, meta, new_account=new_account)
    audit.record(db, actor=user, action="auth.login", meta=meta)
    db.commit()
    return out


@router.post("/mfa/verify", response_model=TokenOut, summary="Complete sign-in with an authenticator-app code")
def verify_mfa(body: MfaVerifyIn, db: Session = Depends(get_db), meta: RequestMeta = Depends(request_meta)) -> TokenOut:
    try:
        payload = decode_token(body.mfa_token, "mfa")
    except jwt.PyJWTError as exc:
        raise unauthorized("Sign-in expired. Start again.") from exc
    limiter.hit(f"mfa:{payload['sub']}", 10, 15 * 60)
    user = db.get(User, uuid.UUID(payload["sub"]))
    if user is None or user.status != "active" or not user.mfa_enabled or not user.mfa_secret:
        raise unauthorized()
    if not pyotp.TOTP(user.mfa_secret).verify(body.code, valid_window=1):
        raise bad_request("invalid_code", "The authenticator code is incorrect")
    out = _issue_tokens(db, user, meta)
    audit.record(db, actor=user, action="auth.login", detail="with second factor", meta=meta)
    db.commit()
    return out


@router.post("/refresh", response_model=TokenOut, summary="Exchange a refresh token for new tokens (rotation)")
def refresh(body: RefreshIn, db: Session = Depends(get_db), meta: RequestMeta = Depends(request_meta)) -> TokenOut:
    tok = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == sha256_hex(body.refresh_token)).with_for_update())
    if tok is None:
        raise unauthorized("Session expired")
    now = utcnow()
    if tok.rotated_at is not None or tok.revoked_at is not None:
        # A used or revoked token came back: treat as theft and end every session in this family.
        db.execute(
            update(RefreshToken).where(RefreshToken.family_id == tok.family_id, RefreshToken.revoked_at.is_(None)).values(revoked_at=now)
        )
        audit.record(db, actor=None, action="auth.refresh_reuse", resource_type="user", resource_id=tok.user_id, meta=meta)
        db.commit()
        raise unauthorized("Session expired")
    if tok.expires_at <= now:
        raise unauthorized("Session expired")
    user = db.get(User, tok.user_id)
    if user is None or user.status != "active":
        raise unauthorized("Session expired")
    tok.rotated_at = now
    out = _issue_tokens(db, user, meta, family_id=tok.family_id)
    db.commit()
    return out


@router.post("/logout", status_code=204, summary="End this session")
def logout(body: RefreshIn, db: Session = Depends(get_db)) -> None:
    tok = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == sha256_hex(body.refresh_token)))
    if tok is not None:
        db.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == tok.family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=utcnow())
        )
        db.commit()


@router.post("/logout-all", status_code=204, summary="Sign out of every device")
def logout_all(user: User = Depends(get_current_user), db: Session = Depends(get_db), meta: RequestMeta = Depends(request_meta)) -> None:
    db.execute(update(RefreshToken).where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None)).values(revoked_at=utcnow()))
    audit.record(db, actor=user, action="auth.logout_all", meta=meta)
    db.commit()


@router.post("/mfa/setup", response_model=MfaSetupOut, summary="Start authenticator-app setup")
def mfa_setup(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> MfaSetupOut:
    if user.mfa_enabled:
        raise bad_request("mfa_already_enabled", "Two-step sign-in is already on")
    secret = pyotp.random_base32()
    user.mfa_secret = secret
    db.commit()
    uri = pyotp.TOTP(secret).provisioning_uri(name=user.phone, issuer_name="CardiosensAI")
    return MfaSetupOut(secret=secret, otpauth_uri=uri)


@router.post("/mfa/enable", status_code=204, summary="Confirm authenticator-app setup")
def mfa_enable(
    body: MfaCodeIn, user: User = Depends(get_current_user), db: Session = Depends(get_db), meta: RequestMeta = Depends(request_meta)
) -> None:
    if not user.mfa_secret or not pyotp.TOTP(user.mfa_secret).verify(body.code, valid_window=1):
        raise bad_request("invalid_code", "The authenticator code is incorrect")
    user.mfa_enabled = True
    audit.record(db, actor=user, action="auth.mfa_enable", meta=meta)
    db.commit()


@router.post("/mfa/disable", status_code=204, summary="Turn off two-step sign-in")
def mfa_disable(
    body: MfaCodeIn, user: User = Depends(get_current_user), db: Session = Depends(get_db), meta: RequestMeta = Depends(request_meta)
) -> None:
    if not user.mfa_enabled or not user.mfa_secret or not pyotp.TOTP(user.mfa_secret).verify(body.code, valid_window=1):
        raise bad_request("invalid_code", "The authenticator code is incorrect")
    user.mfa_enabled = False
    user.mfa_secret = None
    audit.record(db, actor=user, action="auth.mfa_disable", meta=meta)
    db.commit()
