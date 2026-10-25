"""Structured JSON logs and security headers.

Logs carry the request id, method, route template, status and latency only — never
request bodies, query strings or health values.
"""

import json
import logging
import time
import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
log = logging.getLogger("cardiosensai")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {"level": record.levelname, "msg": record.getMessage(), "logger": record.name, "request_id": request_id_var.get()}
        extra = getattr(record, "extra_fields", None)
        if isinstance(extra, dict):
            out.update(extra)
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out)


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)


def _templated_path(request: Request) -> str:
    """The URL path with parameter values replaced by their names, e.g. /v1/reports/download/{token}.

    Keeps ids and signed tokens out of logs.
    """
    path = request.url.path
    params = request.scope.get("path_params") or {}
    if not params and request.scope.get("route") is None:
        return "unmatched"
    for name, value in params.items():
        path = path.replace(f"/{value}", f"/{{{name}}}", 1)
    return path


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex
        rid = rid[:64]
        token = request_id_var.set(rid)
        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
        finally:
            path = _templated_path(request)
            log.info(
                "request",
                extra={
                    "extra_fields": {
                        "method": request.method,
                        "route": path,
                        "status": status,
                        "ms": round((time.perf_counter() - start) * 1000, 1),
                    }
                },
            )
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = rid
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = response.headers.get("Cache-Control", "no-store")
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response
