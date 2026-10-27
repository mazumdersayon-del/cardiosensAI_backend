import uuid
from dataclasses import dataclass

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import ApiError, forbidden, unauthorized
from app.core.security import decode_token
from app.models import User

bearer = HTTPBearer(auto_error=False)


def get_current_user(creds: HTTPAuthorizationCredentials | None = Depends(bearer), db: Session = Depends(get_db)) -> User:
    if creds is None or creds.scheme.lower() != "bearer":
        raise unauthorized()
    try:
        payload = decode_token(creds.credentials, "access")
        user_id = uuid.UUID(payload["sub"])
    except (jwt.PyJWTError, ValueError, KeyError) as exc:
        raise unauthorized("Session expired or invalid") from exc
    user = db.get(User, user_id)
    if user is None or user.status == "deleted":
        raise unauthorized("Session expired or invalid")
    if user.status in BLOCKED:
        raise blocked_error(user)
    return user


BLOCKED = ("suspended", "deleting")


def blocked_error(user: User) -> ApiError:
    """Why a suspended or to-be-deleted account can't sign in or call the API."""
    if user.status == "deleting":
        return ApiError(
            403, "account_deleting", "This account has been closed and will be deleted. Contact CardiosensAI support if this is a mistake."
        )
    return ApiError(403, "suspended", "This account is suspended. Contact CardiosensAI support.")


def require_roles(*roles: str):
    def _dep(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise forbidden()
        return user

    return _dep


require_patient = require_roles("patient")


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise forbidden()
    if not user.mfa_enabled:
        raise forbidden("Staff accounts must turn on two-step sign-in before using the admin console")
    return user


@dataclass
class RequestMeta:
    ip: str | None
    request_id: str | None
    user_agent: str | None


def request_meta(request: Request) -> RequestMeta:
    # Behind a proxy, configure uvicorn --proxy-headers / --forwarded-allow-ips so client.host is the real IP.
    return RequestMeta(
        ip=request.client.host if request.client else None,
        request_id=request.headers.get("x-request-id"),
        user_agent=(request.headers.get("user-agent") or "")[:200] or None,
    )
