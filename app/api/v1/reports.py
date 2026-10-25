"""PDF reports: generated after a permission check, delivered only through short-lived signed links."""

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from fastapi.responses import Response
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import RequestMeta, get_current_user, request_meta
from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import ApiError, bad_request, forbidden, not_found
from app.core.ratelimit import limiter
from app.core.security import sign_payload, verify_signed
from app.core.storage import get_storage
from app.models import Report, User
from app.schemas.sharing import DownloadLinkOut, ReportIn, ReportOut
from app.services import audit
from app.services.authz import VITALS, get_access, resolve_patient_id
from app.services.report_pdf import generate_report
from app.services.serializers import report_out

router = APIRouter(prefix="/reports", tags=["reports"])


class ReportCreateIn(ReportIn):
    utc_offset_min: int = Field(default=330, ge=-720, le=840, description="Time zone used for day boundaries (default IST)")


def _check_scope(db: Session, user: User, patient_id: uuid.UUID, vitals: list[str], include_ecg: bool):  # noqa: ANN202
    ctx = get_access(db, user, patient_id, for_download=True)
    missing = [v for v in vitals if v not in ctx.allowed]
    if missing or (include_ecg and "ecg" not in ctx.allowed):
        raise forbidden("The report includes data that hasn't been shared with you")
    return ctx


@router.post("", response_model=ReportOut, status_code=202, summary="Request a PDF report (generated in the background)")
def create_report(
    body: ReportCreateIn,
    background: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    meta: RequestMeta = Depends(request_meta),
) -> ReportOut:
    if body.to_date < body.from_date:
        raise bad_request("invalid_range", "End date is before start date")
    if (body.to_date - body.from_date).days > 366:
        raise bad_request("range_too_long", "Reports can cover at most one year")
    limiter.hit(f"report:{user.id}", 20, 3600)
    pid = resolve_patient_id(user, body.patient_id)
    ctx = _check_scope(db, user, pid, body.vitals, body.include_ecg)
    params = {
        "fromDate": body.from_date.isoformat(),
        "toDate": body.to_date.isoformat(),
        "vitals": [v for v in VITALS if v in set(body.vitals)],  # app display order
        "includeEcg": body.include_ecg,
        "includeTables": body.include_tables,
        "includeName": body.include_name,
        "utcOffsetMin": body.utc_offset_min,
        "historyFloor": ctx.history_floor.isoformat() if ctx.history_floor else None,
    }
    r = Report(patient_id=pid, requested_by=user.id, params=params)
    db.add(r)
    db.flush()
    audit.record(
        db,
        actor=user,
        action="report.create",
        patient_id=pid,
        resource_type="report",
        resource_id=r.id,
        detail=f"{params['fromDate']} to {params['toDate']}: {', '.join(params['vitals'])}",
        meta=meta,
        grant_id=ctx.grant.id if ctx.grant else None,
    )
    db.commit()
    background.add_task(generate_report, r.id)
    return report_out(r)


@router.get("", response_model=list[ReportOut], summary="Reports I requested")
def list_reports(
    patient_id: str | None = Query(default=None, alias="patientId"), user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[ReportOut]:
    stmt = select(Report).where(Report.requested_by == user.id, Report.status != "deleted")
    if patient_id:
        stmt = stmt.where(Report.patient_id == resolve_patient_id(user, patient_id))
    return [report_out(r) for r in db.scalars(stmt.order_by(Report.created_at.desc()).limit(100))]


def _own_report(db: Session, user: User, report_id: uuid.UUID) -> Report:
    r = db.get(Report, report_id)
    if r is None or r.requested_by != user.id or r.status == "deleted":
        raise not_found("Report")
    return r


@router.get("/{report_id}", response_model=ReportOut)
def get_report(report_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> ReportOut:
    return report_out(_own_report(db, user, report_id))


@router.post("/{report_id}/download-link", response_model=DownloadLinkOut, summary="Get a short-lived, single-user download link")
def download_link(report_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> DownloadLinkOut:
    s = get_settings()
    r = _own_report(db, user, report_id)
    if r.status != "ready":
        raise bad_request("not_ready", "The report isn't ready yet")
    _check_scope(db, user, r.patient_id, r.params["vitals"], r.params["includeEcg"])
    token = sign_payload({"t": "report", "r": str(r.id), "u": str(user.id)}, s.report_link_ttl_s)
    return DownloadLinkOut(url=f"{s.api_base_url.rstrip('/')}/v1/reports/download/{token}", expires_in_s=s.report_link_ttl_s)


@router.get(
    "/download/{token}",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}}},
    summary="Download via signed link (permission re-checked on every download)",
)
def download(token: str, db: Session = Depends(get_db), meta: RequestMeta = Depends(request_meta)) -> Response:
    gone = ApiError(410, "link_expired", "This download link has expired. Request a new one in the app.")
    body = verify_signed(token)
    if body is None or body.get("t") != "report":
        raise gone
    try:
        r = db.get(Report, uuid.UUID(body["r"]))
        user = db.get(User, uuid.UUID(body["u"]))
    except (KeyError, ValueError) as exc:
        raise gone from exc
    if r is None or user is None or user.status != "active" or r.status != "ready" or not r.storage_key or r.requested_by != user.id:
        raise gone
    # Revoking a grant must stop downloads too, even for links issued before the revocation.
    _check_scope(db, user, r.patient_id, r.params["vitals"], r.params["includeEcg"])
    data = get_storage().get(r.storage_key)
    audit.record(db, actor=user, action="report.download", patient_id=r.patient_id, resource_type="report", resource_id=r.id, meta=meta)
    db.commit()
    name = f"CardiosensAI_Report_{r.params['fromDate']}_{r.params['toDate']}.pdf"
    return Response(
        content=data,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "private, no-store"},
    )
