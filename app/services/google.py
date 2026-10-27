"""Sign in with Google: verify the ID token from Google Identity Services (web) or Google Sign-In (app).

The token is a JWT signed by Google. It is checked against Google's published keys (cached), our client ids
(GOOGLE_CLIENT_ID + GOOGLE_EXTRA_CLIENT_IDS), the issuer and expiry. Only addresses Google has verified count.
"""

from dataclasses import dataclass

import jwt
from jwt import PyJWKClient

from app.core.config import get_settings
from app.core.errors import ApiError

CERTS_URL = "https://www.googleapis.com/oauth2/v3/certs"
ISSUERS = ("https://accounts.google.com", "accounts.google.com")

_jwks: PyJWKClient | None = None


@dataclass(frozen=True)
class GoogleIdentity:
    sub: str
    email: str
    name: str | None


def enabled() -> bool:
    return bool(get_settings().google_audiences)


def _client() -> PyJWKClient:
    global _jwks
    if _jwks is None:
        _jwks = PyJWKClient(CERTS_URL, cache_keys=True, lifespan=6 * 3600, timeout=10)
    return _jwks


def verify_id_token(token: str) -> GoogleIdentity:
    if not enabled():
        raise ApiError(400, "google_disabled", "Google sign-in isn't set up on this server")
    bad = ApiError(401, "google_invalid", "Google sign-in failed or expired. Try again.")
    try:
        key = _client().get_signing_key_from_jwt(token)
        claims = jwt.decode(token, key.key, algorithms=["RS256"], audience=get_settings().google_audiences, issuer=ISSUERS, leeway=30)
    except jwt.PyJWKClientConnectionError as exc:
        raise ApiError(503, "google_unreachable", "Couldn't reach Google to check the sign-in. Try again.") from exc
    except jwt.PyJWTError as exc:
        raise bad from exc
    if not claims.get("email") or claims.get("email_verified") is not True or not claims.get("sub"):
        raise ApiError(400, "google_email_unverified", "Your Google account has no verified email address")
    return GoogleIdentity(sub=str(claims["sub"]), email=str(claims["email"]).lower(), name=claims.get("name"))
