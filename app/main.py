from fastapi import APIRouter, Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.v1 import admin, audit, auth, devices, documents, ecg, links, me, measurements, notifications, reports, sharing, verification
from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import install_error_handlers
from app.core.observability import RequestContextMiddleware, configure_logging
from app.schemas.common import ErrorResponse
from app.website import install_website

DESCRIPTION = """
API for the CardiosensAI connected health monitoring platform.

* **Patients, doctors and clinics** sign in with email and password and are identified by a 16-digit CardiosensAI ID.
* **Patients** upload readings from their devices, review history and ECG recordings, share selected data and create PDF reports.
* **Doctors and clinics** see a patient's data only after the patient approves a link, and only once CardiosensAI has verified them
  and they use two-step sign-in. Family members see only what a patient explicitly shared.
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
        version="0.2.0",
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
        documents.router,
        sharing.router,
        links.router,
        notifications.router,
        reports.router,
        verification.router,
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

    install_website(app)  # last: the site's catch-all mount must not shadow API routes
    return app


app = create_app()
