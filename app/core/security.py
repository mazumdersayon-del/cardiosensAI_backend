"""Tokens, one-time codes and signed links.

- Access tokens: short-lived JWT (HS256).
- Refresh tokens: opaque random strings, stored only as SHA-256 hashes, rotated on every use.
  Re-using an old refresh token revokes the whole token family (theft detection).
- OTP codes and invite tokens are stored hashed, never in plain text.
- Report download links: HMAC-signed, short-lived, bound to one user and one report.
"""

import base64
import hashlib
import hmac
import json
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

from app.core.config import get_settings


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def new_otp() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_otp(challenge_id: uuid.UUID, phone: str, code: str) -> str:
    key = get_settings().signing_key.encode()
    return hmac.new(key, f"otp|{challenge_id}|{phone}|{code}".encode(), hashlib.sha256).hexdigest()


def constant_time_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


@dataclass(frozen=True)
class AccessClaims:
    user_id: uuid.UUID
    role: str
    jti: str


def create_access_token(user_id: uuid.UUID, role: str) -> str:
    s = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "role": role,
        "typ": "access",
        "iss": s.jwt_issuer,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=s.access_token_ttl_s)).timestamp()),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, s.jwt_secret, algorithm="HS256")


def create_mfa_token(user_id: uuid.UUID) -> str:
    s = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "typ": "mfa",
        "iss": s.jwt_issuer,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=s.mfa_token_ttl_s)).timestamp()),
    }
    return jwt.encode(payload, s.jwt_secret, algorithm="HS256")


def decode_token(token: str, expected_type: str) -> dict:
    s = get_settings()
    payload = jwt.decode(token, s.jwt_secret, algorithms=["HS256"], issuer=s.jwt_issuer, options={"require": ["exp", "sub", "typ"]})
    if payload.get("typ") != expected_type:
        raise jwt.InvalidTokenError("Wrong token type")
    return payload


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def sign_payload(payload: dict, ttl_s: int) -> str:
    body = dict(payload, exp=int(datetime.now(UTC).timestamp()) + ttl_s)
    raw = _b64(json.dumps(body, separators=(",", ":"), sort_keys=True).encode())
    sig = _b64(hmac.new(get_settings().signing_key.encode(), raw.encode(), hashlib.sha256).digest())
    return f"{raw}.{sig}"


def verify_signed(token: str) -> dict | None:
    try:
        raw, sig = token.split(".", 1)
    except ValueError:
        return None
    expected = _b64(hmac.new(get_settings().signing_key.encode(), raw.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(expected, sig):
        return None
    try:
        body = json.loads(_unb64(raw))
    except (ValueError, json.JSONDecodeError):
        return None
    if int(body.get("exp", 0)) < int(datetime.now(UTC).timestamp()):
        return None
    return body
