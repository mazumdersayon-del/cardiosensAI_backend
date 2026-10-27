"""Sign-up and sign-in for every role (web and app).

- Sign-up: mobile number required, email optional. A one-time code goes to each; entering them creates the
  account, activated and signed in. No confirmation links. "Sign up with Google" fills in a verified email.
- Sign-in: email or mobile number → a code by email or SMS (POST /login/start, /login/verify), or the
  password instead (POST /login), or Google (POST /google). Doctors, clinics and staff then enter a TOTP code.
- Rotating refresh tokens. /otp/* is the older app's phone sign-in, kept for installed builds.

Web sessions keep the refresh token in an httpOnly, SameSite=Strict cookie scoped to /v1/auth and the
access token in memory only. The app keeps receiving the refresh token in the response body.
"""

import re
import uuid
from datetime import date, timedelta

import jwt
import pyotp
from email_validator import EmailNotValidError, validate_email
from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import false, func, or_, select, update
from sqlalchemy.orm import Session

from app.api.deps import BLOCKED, RequestMeta, blocked_error, get_current_user, request_meta
from app.core.config import get_settings
from app.core.db import get_db, utcnow
from app.core.errors import ApiError, bad_request, conflict, forbidden, unauthorized
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
    E164,
    AccountStatusOut,
    AuthOptionsOut,
    EmailIn,
    GoogleIn,
    LoginCodeIn,
    LoginIn,
    LoginStartIn,
    LoginStartOut,
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
    SignupIdIn,
    SignupOut,
    SignupVerifyIn,
    TokenOut,
)
from app.services import audit, email, google, otp, sms
from app.services.accounts import account_status, allocate_unique_id, consume_action_token, is_activated, issue_action_token
from app.services.email import mask_email
from app.services.serializers import user_out
from app.services.sms import mask_phone, send_otp

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
    user.last_active_at = utcnow()
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


@router.post("/otp/request", response_model=OtpRequestOut, deprecated=True, summary="Older app builds: SMS code sign-in")
def request_otp(body: OtpRequestIn, db: Session = Depends(get_db), meta: RequestMeta = Depends(request_meta)) -> OtpRequestOut:
    s = get_settings()
    limiter.hit(f"otp:phone:{body.phone}", s.rate_limit_otp_per_phone, s.rate_limit_window_s)
    limiter.hit(f"otp:ip:{meta.ip}", s.rate_limit_otp_per_ip, s.rate_limit_window_s)
    code = new_otp()
    challenge = OtpChallenge(
        id=uuid.uuid4(),
        purpose="legacy",
        channel="sms",
        phone=body.phone,
        code_hash="",
        expires_at=utcnow() + timedelta(seconds=s.otp_ttl_s),
    )
    challenge.code_hash = hash_otp(challenge.id, body.phone, code)
    db.add(challenge)
    db.commit()
    send_otp(body.phone, code)
    expose = s.dev_expose_otp and s.env in ("development", "test")
    return OtpRequestOut(challenge_id=str(challenge.id), expires_in_s=s.otp_ttl_s, dev_code=code if expose else None)


@router.post(
    "/otp/verify",
    response_model=TokenOut | MfaRequiredOut,
    deprecated=True,
    summary="Older app builds: verify the SMS code (creates a patient on first sign-in)",
)
def verify_otp(body: OtpVerifyIn, db: Session = Depends(get_db), meta: RequestMeta = Depends(request_meta)) -> TokenOut | MfaRequiredOut:
    s = get_settings()
    try:
        cid = uuid.UUID(body.challenge_id)
    except ValueError as exc:
        raise bad_request("invalid_code", "The code is incorrect or has expired") from exc
    ch = db.get(OtpChallenge, cid, with_for_update=True)
    invalid = ApiError(400, "invalid_code", "The code is incorrect or has expired")
    if ch is None or ch.purpose != "legacy" or ch.consumed_at is not None or ch.expires_at <= utcnow() or ch.attempts >= s.otp_max_attempts:
        raise invalid
    ch.attempts += 1
    if not constant_time_equal(ch.code_hash, hash_otp(ch.id, ch.phone, body.code)):
        db.commit()  # keep the attempt count
        raise invalid
    ch.consumed_at = utcnow()

    user = db.scalar(select(User).where(User.phone == ch.phone))
    if user is not None and user.status == "active":
        user.phone_verified_at = user.phone_verified_at or utcnow()
    new_account = False
    if user is None:
        role = "patient" if body.account_type == "caregiver" else body.account_type
        user = User(
            phone=ch.phone,
            phone_verified_at=utcnow(),
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
    elif user.status in BLOCKED:
        raise blocked_error(user)

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


# ---------------------------------------------------------------- sign-up (one-time codes)


@router.get("/options", response_model=AuthOptionsOut, summary="What the sign-in pages can offer (Google, SMS)")
def auth_options() -> AuthOptionsOut:
    s = get_settings()
    return AuthOptionsOut(google_client_id=s.google_client_id or None, sms_available=sms.available())


def _identifier(raw: str) -> tuple[str, str]:
    """("email", lower-case address) or ("phone", E.164) from what someone typed."""
    v = raw.strip()
    if "@" in v:
        try:
            return "email", validate_email(v, check_deliverability=False).normalized.lower()
        except EmailNotValidError as exc:
            raise bad_request("invalid_identifier", "That email address doesn't look right") from exc
    phone = re.sub(r"[\s\-().]", "", v)
    if re.fullmatch(E164, phone):
        return "phone", phone
    raise bad_request("invalid_identifier", "Enter your email address, or your mobile number with the country code (e.g. +91 98765 43210)")


def _user_by(db: Session, kind: str, value: str) -> User | None:
    cond = func.lower(User.email) == value if kind == "email" else User.phone == value
    return db.scalar(select(User).where(cond))


def _signup_out(primary: OtpChallenge, email_ch: OtpChallenge | None, codes: dict[str, str]) -> SignupOut:
    return SignupOut(
        signup_id=str(primary.id),
        phone_masked=mask_phone(primary.phone or ""),
        email_masked=mask_email(email_ch.email) if email_ch and email_ch.email else None,
        expires_in_s=get_settings().otp_ttl_s,
        dev_phone_code=otp.dev_code(codes["sms"]) if "sms" in codes else None,
        dev_email_code=otp.dev_code(codes["email"]) if "email" in codes else None,
    )


@router.post("/register", response_model=SignupOut, status_code=202, summary="Start sign-up: sends a code to the mobile number (and email)")
def register(body: RegisterIn, db: Session = Depends(get_db), meta: RequestMeta = Depends(request_meta)) -> SignupOut:
    """Nothing is created until the codes are entered (POST /auth/register/verify).

    The answer looks the same whether or not the number or address already has an account: an existing owner
    gets a notice ("someone tried to register…") instead of a code, so the sign-up can't complete.
    """
    limiter.hit(f"register:ip:{meta.ip}", 10, 3600)
    limiter.hit(f"register:phone:{body.phone}", 5, 3600)
    if not sms.available():
        raise sms.UNAVAILABLE
    if body.role == "doctor" and body.doctor is None:
        raise bad_request("missing_details", "Doctors must give their registration number and medical council")
    if body.role == "clinic" and body.clinic is None:
        raise bad_request("missing_details", "Clinics must give the clinic's name, address and registration number")

    google_sub = None
    addr = body.email.lower() if body.email else None
    if body.google_credential:
        ident = google.verify_id_token(body.google_credential)
        if db.scalar(select(User.id).where(or_(User.google_sub == ident.sub, func.lower(User.email) == ident.email))) is not None:
            raise conflict("already_registered", "This Google account already has a CardiosensAI account. Sign in with Google instead.")
        google_sub, addr = ident.sub, ident.email
    if google_sub is None:
        if not body.password:
            raise bad_request("password_required", "Choose a password")
        check_policy(body.password, email=addr)
    elif body.password:
        check_policy(body.password, email=addr)

    phone_taken = db.scalar(select(User.id).where(User.phone == body.phone)) is not None
    email_taken = addr is not None and google_sub is None and db.scalar(select(User.id).where(func.lower(User.email) == addr)) is not None
    payload = {
        "role": body.role,
        "full_name": body.full_name,
        "email": addr,
        "google_sub": google_sub,
        "password_hash": hash_password(body.password) if body.password else None,
        "date_of_birth": body.date_of_birth.isoformat() if body.date_of_birth and body.role == "patient" else None,
        "doctor": body.doctor.model_dump() if body.doctor else None,
        "clinic": body.clinic.model_dump() if body.clinic else None,
        "blocked": phone_taken or email_taken,
    }
    primary, sms_code = otp.create(db, channel="sms", purpose="signup", phone=body.phone, payload=payload)
    primary.group_id = primary.id
    email_ch, email_code = None, None
    if addr and google_sub is None:  # a Google address is already verified by Google
        email_ch, email_code = otp.create(db, channel="email", purpose="signup", email=addr, group_id=primary.id)
    db.commit()

    if phone_taken:
        sms.send_already_registered(body.phone)
    else:
        otp.deliver(primary, sms_code, body.full_name)
    if email_ch is not None:
        if email_taken:
            email.send_registration_attempt(addr)
        else:
            otp.deliver(email_ch, email_code, body.full_name)
    expose = {} if payload["blocked"] else {"sms": sms_code, **({"email": email_code} if email_ch else {})}
    return _signup_out(primary, email_ch, expose)


def _signup_parts(db: Session, signup_id: str) -> tuple[OtpChallenge, OtpChallenge | None]:
    primary = otp.get(db, signup_id)
    if primary is None or primary.purpose != "signup" or primary.channel != "sms" or primary.group_id != primary.id:
        raise otp.INVALID
    email_ch = db.scalar(select(OtpChallenge).where(OtpChallenge.group_id == primary.id, OtpChallenge.channel == "email").with_for_update())
    return primary, email_ch


@router.post("/register/resend", response_model=SignupOut, summary="Send new sign-up codes")
def register_resend(body: SignupIdIn, db: Session = Depends(get_db), meta: RequestMeta = Depends(request_meta)) -> SignupOut:
    limiter.hit(f"register-resend:{body.signup_id}", 3, 3600)
    limiter.hit(f"register:ip:{meta.ip}", 10, 3600)
    primary, email_ch = _signup_parts(db, body.signup_id)
    if primary.consumed_at is not None:
        raise otp.INVALID
    payload = primary.payload or {}
    codes = {"sms": otp.renew(primary)}
    if email_ch is not None:
        codes["email"] = otp.renew(email_ch)
    db.commit()
    if not payload.get("blocked"):
        otp.deliver(primary, codes["sms"], payload.get("full_name"))
        if email_ch is not None:
            otp.deliver(email_ch, codes["email"], payload.get("full_name"))
    return _signup_out(primary, email_ch, {} if payload.get("blocked") else codes)


@router.post(
    "/register/verify", response_model=TokenOut, summary="Finish sign-up with the codes: the account is created, activated and signed in"
)
def register_verify(
    body: SignupVerifyIn, response: Response, db: Session = Depends(get_db), meta: RequestMeta = Depends(request_meta)
) -> TokenOut:
    primary, email_ch = _signup_parts(db, body.signup_id)
    otp.check(db, primary, body.phone_code, purpose="signup")
    if email_ch is not None:
        if not body.email_code:
            raise bad_request("email_code_required", "Enter the code we sent to your email as well")
        otp.check(db, email_ch, body.email_code, purpose="signup")
    p = primary.payload or {}
    if p.get("blocked"):
        raise otp.INVALID
    taken = db.scalar(
        select(User.id).where(
            or_(
                User.phone == primary.phone,
                func.lower(User.email) == p["email"] if p.get("email") else false(),
                User.google_sub == p["google_sub"] if p.get("google_sub") else false(),
            )
        )
    )
    if taken is not None:
        raise conflict("already_registered", "This mobile number or email already has an account. Sign in instead.")

    now = utcnow()
    user = User(
        role=p["role"],
        phone=primary.phone,
        phone_verified_at=now,
        email=p.get("email"),
        email_verified_at=now if p.get("email") else None,
        google_sub=p.get("google_sub"),
        password_hash=p.get("password_hash"),
        full_name=p["full_name"],
        date_of_birth=date.fromisoformat(p["date_of_birth"]) if p.get("date_of_birth") else None,
        preferences={"glucoseUnit": "mg/dL", "tempUnit": "°C"},
    )
    org = None
    if user.role == "clinic":
        c = p["clinic"]
        org = Organization(
            unique_id=allocate_unique_id(db, "clinic"),
            name=c["name"],
            address=c["address"],
            city=c["city"],
            registration_number=c["registration_number"],
            phone=c["phone"],
        )
        db.add(org)
        db.flush()
        user.organization_id = org.id
        user.unique_id = org.unique_id
    else:
        user.unique_id = allocate_unique_id(db, user.role)
    if user.role == "patient":
        user.patient_code = _next_patient_code(db)
    db.add(user)
    db.flush()
    if org is not None:
        org.owner_user_id = user.id
    if user.role == "doctor":
        db.add(ProfessionalProfile(user_id=user.id, **p["doctor"]))
    for ch in (primary, email_ch):
        if ch is not None:
            ch.consumed_at = now
            ch.payload = None  # the password hash has done its job
    via = "google" if user.google_sub else ("mobile + email" if email_ch else "mobile")
    audit.record(db, actor=user, action="account.create", detail=f"{user.role} account ({via})", meta=meta)
    out = _issue_tokens(db, user, meta, new_account=True)
    audit.record(db, actor=user, action="auth.login", detail="sign-up", meta=meta)
    db.commit()
    return _respond_tokens(out, response, body.web_session)


# ---------------------------------------------------------------- sign-in


def _finish_sign_in(
    db: Session, user: User, response: Response, meta: RequestMeta, *, web_session: bool, how: str
) -> TokenOut | MfaRequiredOut:
    if user.mfa_enabled:
        db.commit()
        return MfaRequiredOut(mfa_token=create_mfa_token(user.id))
    out = _issue_tokens(db, user, meta)
    audit.record(db, actor=user, action="auth.login", detail=how, meta=meta)
    db.commit()
    return _respond_tokens(out, response, web_session)


@router.post("/login/start", response_model=LoginStartOut, summary="Sign-in step 1: email → code by email, mobile number → code by SMS")
def login_start(body: LoginStartIn, db: Session = Depends(get_db), meta: RequestMeta = Depends(request_meta)) -> LoginStartOut:
    """Same answer whether or not the address has an account (then no code is sent)."""
    s = get_settings()
    kind, value = _identifier(body.identifier)
    limiter.hit(f"login-code:{value}", s.rate_limit_otp_per_phone, s.rate_limit_window_s)
    limiter.hit(f"login-code:ip:{meta.ip}", s.rate_limit_otp_per_ip, s.rate_limit_window_s)
    if kind == "phone" and not sms.available():
        raise sms.UNAVAILABLE
    user = _user_by(db, kind, value)
    target = user if user is not None and user.status == "active" else None
    ch, code = otp.create(
        db,
        channel="email" if kind == "email" else "sms",
        purpose="login",
        phone=value if kind == "phone" else None,
        email=value if kind == "email" else None,
        user_id=target.id if target else None,
    )
    db.commit()
    if target is not None:
        otp.deliver(ch, code, target.full_name)
    return LoginStartOut(
        challenge_id=str(ch.id),
        channel=ch.channel,
        destination_masked=mask_email(value) if kind == "email" else mask_phone(value),
        expires_in_s=s.otp_ttl_s,
        dev_code=otp.dev_code(code) if target else None,
    )


@router.post("/login/verify", response_model=TokenOut | MfaRequiredOut, summary="Sign-in step 2: the one-time code")
def login_verify(
    body: LoginCodeIn, response: Response, db: Session = Depends(get_db), meta: RequestMeta = Depends(request_meta)
) -> TokenOut | MfaRequiredOut:
    ch = otp.check(db, otp.get(db, body.challenge_id), body.code, purpose="login")
    user = db.get(User, ch.user_id) if ch.user_id else None
    if user is None or user.status == "deleted":
        raise otp.INVALID
    if user.status in BLOCKED:
        db.commit()
        raise blocked_error(user)
    now = utcnow()
    ch.consumed_at = now
    # The code proves the number or address (older accounts may not have confirmed it yet).
    if ch.channel == "sms" and user.phone == ch.phone:
        user.phone_verified_at = user.phone_verified_at or now
    if ch.channel == "email" and (user.email or "").lower() == ch.email:
        user.email_verified_at = user.email_verified_at or now
    return _finish_sign_in(db, user, response, meta, web_session=body.web_session, how=f"code by {ch.channel}")


@router.post("/login", response_model=TokenOut | MfaRequiredOut, summary="Sign in with email or mobile number and password (all roles)")
def login(
    body: LoginIn, response: Response, db: Session = Depends(get_db), meta: RequestMeta = Depends(request_meta)
) -> TokenOut | MfaRequiredOut:
    s = get_settings()
    limiter.hit(f"login:ip:{meta.ip}", s.rate_limit_login_per_ip, s.rate_limit_window_s)
    invalid = ApiError(401, "invalid_credentials", "The email or mobile number, or the password, is incorrect")
    kind, value = _identifier(body.identifier or str(body.email))
    cond = func.lower(User.email) == value if kind == "email" else User.phone == value
    user = db.scalar(select(User).where(cond).with_for_update())
    now = utcnow()
    if user is None or user.status == "deleted" or user.password_hash is None:
        verify_password(None, body.password)
        raise invalid
    if user.locked_until is not None and user.locked_until > now:
        raise ApiError(429, "account_locked", "Too many failed attempts. Try again in 15 minutes, or sign in with a one-time code.")
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
    if user.status in BLOCKED:
        db.commit()
        raise blocked_error(user)
    if not is_activated(user):
        db.commit()
        raise ApiError(
            403,
            "not_activated",
            "This account isn't confirmed yet. Sign in with a one-time code instead: it confirms your email or number.",
        )
    return _finish_sign_in(db, user, response, meta, web_session=body.web_session, how=f"password ({kind})")


@router.post("/google", response_model=TokenOut | MfaRequiredOut, summary="Sign in with Google (an ID token from Google Identity Services)")
def google_sign_in(
    body: GoogleIn, response: Response, db: Session = Depends(get_db), meta: RequestMeta = Depends(request_meta)
) -> TokenOut | MfaRequiredOut:
    limiter.hit(f"google:ip:{meta.ip}", get_settings().rate_limit_login_per_ip, get_settings().rate_limit_window_s)
    ident = google.verify_id_token(body.credential)
    user = db.scalar(select(User).where(User.google_sub == ident.sub)) or db.scalar(
        select(User).where(func.lower(User.email) == ident.email)
    )
    if user is None or user.status == "deleted":
        raise ApiError(404, "no_account", f"There's no CardiosensAI account for {ident.email} yet. Create one first.")
    if user.status in BLOCKED:
        raise blocked_error(user)
    if user.google_sub is None:
        user.google_sub = ident.sub  # same verified address: link this Google account
        audit.record(db, actor=user, action="auth.google_linked", meta=meta)
    elif user.google_sub != ident.sub:
        raise conflict("google_mismatch", "This email address is linked to a different Google account")
    if (user.email or "").lower() == ident.email:
        user.email_verified_at = user.email_verified_at or utcnow()
    return _finish_sign_in(db, user, response, meta, web_session=body.web_session, how="google")


# ---------------------------------------------------------------- password reset and change


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
    if user.password_hash is not None and not verify_password(user.password_hash, body.current_password or ""):
        raise bad_request("invalid_credentials", "Current password is incorrect")
    check_policy(body.new_password, email=user.email)
    user.password_hash = hash_password(body.new_password)
    db.execute(update(RefreshToken).where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None)).values(revoked_at=utcnow()))
    audit.record(db, actor=user, action="auth.password_change", meta=meta)
    db.commit()


@router.get("/status", response_model=AccountStatusOut, summary="What this account still needs (email, verification, two-step sign-in)")
def status(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> AccountStatusOut:
    return account_status(db, user)
