"""Sign-in: email + password (web and app) or SMS one-time code (app), TOTP second factor, rotating refresh tokens.

Web sessions keep the refresh token in an httpOnly, SameSite=Strict cookie scoped to /v1/auth and the
access token in memory only. The app keeps receiving the refresh token in the response body.
"""

import uuid
from datetime import timedelta

import jwt
import pyotp
from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.api.deps import RequestMeta, get_current_user, request_meta
from app.core.config import get_settings
from app.core.db import get_db, utcnow
from app.core.errors import ApiError, bad_request, forbidden, unauthorized
from app.core.passwords import check_policy, hash_password, needs_rehash, verify_password
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
from app.models import Organization, OtpChallenge, ProfessionalProfile, RefreshToken, User
from app.schemas.auth import (
    AccountStatusOut,
    EmailIn,
    LoginIn,
    MessageOut,
    MfaCodeIn,
    MfaRequiredOut,
    MfaSetupOut,
    MfaVerifyIn,
    OptionalRefreshIn,
    OtpRequestIn,
    OtpRequestOut,
    OtpVerifyIn,
    PasswordChangeIn,
    PasswordResetIn,
    RegisterIn,
    TokenIn,
    TokenOut,
)
from app.services import audit, email
from app.services.accounts import account_status, allocate_unique_id, consume_action_token, issue_action_token
from app.services.serializers import user_out
from app.services.sms import send_otp

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_refresh_cookie(response: Response, token: str) -> None:
    s = get_settings()
    response.set_cookie(
        s.refresh_cookie_name,
        token,
        max_age=s.refresh_token_ttl_s,
        path="/v1/auth",
        httponly=True,
        secure=s.secure_cookies,
        samesite="strict",
    )


def _clear_refresh_cookie(response: Response) -> None:
    s = get_settings()
    response.delete_cookie(s.refresh_cookie_name, path="/v1/auth", httponly=True, secure=s.secure_cookies, samesite="strict")


def _respond_tokens(out: TokenOut, response: Response, web_session: bool) -> TokenOut:
    if web_session and out.refresh_token:
        _set_refresh_cookie(response, out.refresh_token)
        out.refresh_token = None
    return out


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
        role = "patient" if body.account_type == "caregiver" else body.account_type
        user = User(
            phone=ch.phone,
            role=role,
            unique_id=allocate_unique_id(db, role),
            full_name=body.full_name,
            preferences={"glucoseUnit": "mg/dL", "tempUnit": "°C"},
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
def verify_mfa(body: MfaVerifyIn, response: Response, db: Session = Depends(get_db), meta: RequestMeta = Depends(request_meta)) -> TokenOut:
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
    return _respond_tokens(out, response, body.web_session)


def _refresh_token_from(request: Request, body: OptionalRefreshIn | None) -> tuple[str | None, bool]:
    """Returns (token, came_from_cookie). Cookie use needs the X-CS-Web header as a CSRF guard on top of SameSite."""
    if body is not None and body.refresh_token:
        return body.refresh_token, False
    cookie = request.cookies.get(get_settings().refresh_cookie_name)
    if cookie and request.headers.get("x-cs-web") == "1":
        return cookie, True
    return None, False


@router.post("/refresh", response_model=TokenOut, summary="Exchange a refresh token for new tokens (rotation)")
def refresh(
    request: Request,
    response: Response,
    body: OptionalRefreshIn | None = None,
    db: Session = Depends(get_db),
    meta: RequestMeta = Depends(request_meta),
) -> TokenOut:
    token, web = _refresh_token_from(request, body)
    if token is None:
        raise unauthorized("Session expired")
    tok = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == sha256_hex(token)).with_for_update())
    if tok is None:
        if web:
            _clear_refresh_cookie(response)
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
    return _respond_tokens(out, response, web)


@router.post("/logout", status_code=204, summary="End this session")
def logout(request: Request, response: Response, body: OptionalRefreshIn | None = None, db: Session = Depends(get_db)) -> None:
    token, web = _refresh_token_from(request, body)
    if web:
        _clear_refresh_cookie(response)
    tok = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == sha256_hex(token))) if token else None
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
    uri = pyotp.TOTP(secret).provisioning_uri(name=user.email or user.phone or user.unique_id or str(user.id), issuer_name="CardiosensAI")
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
    if user.role in ("doctor", "clinic", "admin"):
        raise forbidden("Two-step sign-in is required for doctor, clinic and staff accounts")
    user.mfa_enabled = False
    user.mfa_secret = None
    audit.record(db, actor=user, action="auth.mfa_disable", meta=meta)
    db.commit()


# ---------------------------------------------------------------- email + password


def _send_verification(db: Session, user: User) -> None:
    s = get_settings()
    token = issue_action_token(db, user, "verify_email", user.email, s.email_verify_ttl_s)
    db.commit()
    email.send_verification(user.email, user.full_name, token)


@router.post("/register", response_model=MessageOut, status_code=202, summary="Create a patient, doctor or clinic account")
def register(body: RegisterIn, db: Session = Depends(get_db), meta: RequestMeta = Depends(request_meta)) -> MessageOut:
    """Always answers the same way, so the form can't be used to find out who has an account.

    A new account must confirm its email address before it can sign in. If the address is already
    registered, its owner gets an email saying so instead.
    """
    limiter.hit(f"register:ip:{meta.ip}", 10, 3600)
    addr = body.email.lower()
    check_policy(body.password, email=addr)
    if body.role == "doctor" and body.doctor is None:
        raise bad_request("missing_details", "Doctors must give their registration number and medical council")
    if body.role == "clinic" and body.clinic is None:
        raise bad_request("missing_details", "Clinics must give the clinic's name, address and registration number")
    done = MessageOut(message="Check your email to confirm your address, then sign in.")
    if db.scalar(select(User.id).where(func.lower(User.email) == addr)) is not None:
        email.send_registration_attempt(addr)
        return done
    if body.phone and db.scalar(select(User.id).where(User.phone == body.phone)) is not None:
        raise bad_request("phone_taken", "This phone number belongs to an app account. Sign in there and add your email under Profile")

    user = User(
        role=body.role,
        email=addr,
        password_hash=hash_password(body.password),
        full_name=body.full_name,
        phone=body.phone,
        date_of_birth=body.date_of_birth if body.role == "patient" else None,
        preferences={"glucoseUnit": "mg/dL", "tempUnit": "°C"},
    )
    if body.role == "clinic":
        c = body.clinic
        org = Organization(
            unique_id=allocate_unique_id(db, "clinic"),
            name=c.name,
            address=c.address,
            city=c.city,
            registration_number=c.registration_number,
            phone=c.phone,
        )
        db.add(org)
        db.flush()
        user.organization_id = org.id
        user.unique_id = org.unique_id
    else:
        user.unique_id = allocate_unique_id(db, body.role)
    if body.role == "patient":
        user.patient_code = _next_patient_code(db)
    db.add(user)
    db.flush()
    if body.role == "clinic":
        org.owner_user_id = user.id
    if body.role == "doctor":
        db.add(ProfessionalProfile(user_id=user.id, **body.doctor.model_dump()))
    audit.record(db, actor=user, action="account.create", detail=f"{user.role} account (email)", meta=meta)
    db.commit()
    _send_verification(db, user)
    return done


@router.post("/email/verify", response_model=MessageOut, summary="Confirm an email address with the emailed link")
def verify_email(body: TokenIn, db: Session = Depends(get_db), meta: RequestMeta = Depends(request_meta)) -> MessageOut:
    row = consume_action_token(db, body.token, "verify_email")
    user = db.get(User, row.user_id) if row else None
    if row is None or user is None or user.status == "deleted" or (user.email or "").lower() != row.email:
        raise bad_request("invalid_token", "This link is invalid, used or expired. Sign in to get a new one.")
    user.email_verified_at = user.email_verified_at or utcnow()
    audit.record(db, actor=user, action="auth.email_verified", meta=meta)
    db.commit()
    return MessageOut(message="Email address confirmed. You can sign in now.")


@router.post("/email/resend", status_code=204, summary="Send the confirmation email again")
def resend_verification(body: EmailIn, db: Session = Depends(get_db), meta: RequestMeta = Depends(request_meta)) -> None:
    limiter.hit(f"verify:{body.email.lower()}", 3, 3600)
    limiter.hit(f"verify:ip:{meta.ip}", 20, 3600)
    user = db.scalar(select(User).where(func.lower(User.email) == body.email.lower()))
    if user is not None and user.status == "active" and user.email_verified_at is None:
        _send_verification(db, user)


@router.post("/login", response_model=TokenOut | MfaRequiredOut, summary="Sign in with email and password (all roles)")
def login(
    body: LoginIn, response: Response, db: Session = Depends(get_db), meta: RequestMeta = Depends(request_meta)
) -> TokenOut | MfaRequiredOut:
    s = get_settings()
    limiter.hit(f"login:ip:{meta.ip}", s.rate_limit_login_per_ip, s.rate_limit_window_s)
    invalid = ApiError(401, "invalid_credentials", "Email or password is incorrect")
    user = db.scalar(select(User).where(func.lower(User.email) == body.email.lower()).with_for_update())
    now = utcnow()
    if user is None or user.status == "deleted":
        verify_password(None, body.password)
        raise invalid
    if user.locked_until is not None and user.locked_until > now:
        raise ApiError(429, "account_locked", "Too many failed attempts. Try again in 15 minutes or reset your password.")
    if not verify_password(user.password_hash, body.password):
        user.failed_logins += 1
        if user.failed_logins >= s.login_max_failures:
            user.failed_logins = 0
            user.locked_until = now + timedelta(seconds=s.login_lockout_s)
            audit.record(db, actor=None, action="auth.locked", resource_type="user", resource_id=user.id, meta=meta)
        db.commit()
        raise invalid
    user.failed_logins = 0
    user.locked_until = None
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    if user.status == "suspended":
        db.commit()
        raise ApiError(403, "suspended", "This account is suspended. Contact CardiosensAI support.")
    if user.email_verified_at is None:
        db.commit()
        raise ApiError(403, "email_not_verified", "Confirm your email address first. We can send the link again.")
    if user.mfa_enabled:
        db.commit()
        return MfaRequiredOut(mfa_token=create_mfa_token(user.id))
    out = _issue_tokens(db, user, meta)
    audit.record(db, actor=user, action="auth.login", detail="email", meta=meta)
    db.commit()
    return _respond_tokens(out, response, body.web_session)


@router.post("/password/forgot", status_code=204, summary="Email a password-reset link (same answer whether or not the account exists)")
def forgot_password(body: EmailIn, db: Session = Depends(get_db), meta: RequestMeta = Depends(request_meta)) -> None:
    s = get_settings()
    limiter.hit(f"forgot:{body.email.lower()}", 3, 3600)
    limiter.hit(f"forgot:ip:{meta.ip}", 20, 3600)
    user = db.scalar(select(User).where(func.lower(User.email) == body.email.lower()))
    if user is None or user.status != "active":
        return
    token = issue_action_token(db, user, "reset_password", user.email, s.password_reset_ttl_s)
    audit.record(db, actor=None, action="auth.password_reset_request", resource_type="user", resource_id=user.id, meta=meta)
    db.commit()
    email.send_password_reset(user.email, user.full_name, token)


@router.post("/password/reset", response_model=MessageOut, summary="Set a new password with the emailed link")
def reset_password(body: PasswordResetIn, db: Session = Depends(get_db), meta: RequestMeta = Depends(request_meta)) -> MessageOut:
    row = consume_action_token(db, body.token, "reset_password")
    user = db.get(User, row.user_id) if row else None
    if row is None or user is None or user.status != "active" or (user.email or "").lower() != row.email:
        raise bad_request("invalid_token", "This link is invalid, used or expired. Ask for a new one.")
    check_policy(body.password, email=user.email)
    user.password_hash = hash_password(body.password)
    user.failed_logins = 0
    user.locked_until = None
    user.email_verified_at = user.email_verified_at or utcnow()  # the link proves the address
    db.execute(update(RefreshToken).where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None)).values(revoked_at=utcnow()))
    audit.record(db, actor=user, action="auth.password_reset", meta=meta)
    db.commit()
    return MessageOut(message="Password set. Sign in with your new password.")


@router.post("/password/change", status_code=204, summary="Change password (signs out other sessions)")
def change_password(
    body: PasswordChangeIn, user: User = Depends(get_current_user), db: Session = Depends(get_db), meta: RequestMeta = Depends(request_meta)
) -> None:
    limiter.hit(f"pwchange:{user.id}", 10, 3600)
    if not verify_password(user.password_hash, body.current_password):
        raise bad_request("invalid_credentials", "Current password is incorrect")
    check_policy(body.new_password, email=user.email)
    user.password_hash = hash_password(body.new_password)
    db.execute(update(RefreshToken).where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None)).values(revoked_at=utcnow()))
    audit.record(db, actor=user, action="auth.password_change", meta=meta)
    db.commit()


@router.get("/status", response_model=AccountStatusOut, summary="What this account still needs (email, verification, two-step sign-in)")
def status(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> AccountStatusOut:
    return account_status(db, user)
