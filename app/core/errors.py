"""Consistent JSON errors: {"error": {"code": "...", "message": "..."}}.

Messages are written for the client and never echo health values or internal details.
"""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status = status
        self.code = code
        self.message = message


def not_found(what: str = "Resource") -> ApiError:
    # Also used when the caller has no permission, so existence isn't revealed.
    return ApiError(404, "not_found", f"{what} not found")


def forbidden(message: str = "You don't have permission to do this") -> ApiError:
    return ApiError(403, "forbidden", message)


def bad_request(code: str, message: str) -> ApiError:
    return ApiError(400, code, message)


def conflict(code: str, message: str) -> ApiError:
    return ApiError(409, code, message)


def unauthorized(message: str = "Sign in required") -> ApiError:
    return ApiError(401, "unauthorized", message)


def too_many(message: str = "Too many requests. Try again later.") -> ApiError:
    return ApiError(429, "rate_limited", message)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if exc.status == 401 else None
        return JSONResponse(status_code=exc.status, content={"error": {"code": exc.code, "message": exc.message}}, headers=headers)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"error": {"code": "http_error", "message": str(exc.detail)}})

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Report which fields failed, but not the submitted values (they may be health data).
        fields = [{"loc": [str(p) for p in e.get("loc", [])], "msg": e.get("msg", "")} for e in exc.errors()]
        return JSONResponse(
            status_code=422, content={"error": {"code": "validation_error", "message": "Invalid request", "fields": fields}}
        )
