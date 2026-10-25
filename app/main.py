from fastapi import APIRouter, Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.v1 import admin, audit, auth, devices, ecg, me, measurements, notifications, reports, sharing
from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import install_error_handlers
from app.core.observability import RequestContextMiddleware, configure_logging
from app.schemas.common import ErrorResponse

DESCRIPTION = """
API for the CardiosensAI connected health monitoring platform.

* **Patients** upload readings from their devices, review history and ECG recordings, share selected data and create PDF reports.
* **Family members, caregivers and doctors** see only what a patient explicitly shared, within the history window the patient chose.
* **Administrators** manage accounts, device models and operations, and never receive health values.

Every reading keeps its device, acquisition mode (live, synced later, manual), quality and original UTC offset.
Uploads are idempotent: the reading's `id` is its idempotency key.

Not a diagnostic service. No clinical validation or medical-device certification is claimed.
"""


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging()
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description=DESCRIPTION,
        responses={
            400: {"model": ErrorResponse},
            401: {"model": ErrorResponse},
            403: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            429: {"model": ErrorResponse},
        },
        docs_url="/docs" if settings.env != "production" else None,
        redoc_url="/redoc" if settings.env != "production" else None,
    )
    app.add_middleware(RequestContextMiddleware)
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
            allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        )
    install_error_handlers(app)

    v1 = APIRouter(prefix="/v1")
    for r in (
        auth.router,
        me.router,
        audit.router,
        devices.router,
        measurements.router,
        ecg.router,
        sharing.router,
        notifications.router,
        reports.router,
        admin.router,
    ):
        v1.include_router(r)
    app.include_router(v1)

    @app.get("/healthz", tags=["ops"], include_in_schema=False)
    def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/readyz", tags=["ops"], include_in_schema=False)
    def readyz(db: Session = Depends(get_db)) -> dict:
        db.execute(text("select 1"))
        return {"status": "ready"}

    return app


app = create_app()
