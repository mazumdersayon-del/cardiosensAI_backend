"""ORM → response model conversion (kept in one place so field mapping stays consistent)."""

from app.core.db import utcnow
from app.models import AccessGrant, Device, EcgRecording, Measurement, Notification, Organization, Report, User
from app.schemas.auth import OrganizationOut, UserOut
from app.schemas.common import iso_with_offset
from app.schemas.health import Calibration, DeviceOut, EcgOut, MeasurementOut, ProvenanceOut, Segment
from app.schemas.sharing import GrantOut, NotificationOut, ReportOut


def user_out(u: User) -> UserOut:
    return UserOut(
        id=str(u.id),
        unique_id=u.unique_id,
        role=u.role,
        phone=u.phone,
        email=u.email,
        email_verified=u.email_verified_at is not None,
        phone_verified=u.phone_verified_at is not None,
        google_linked=u.google_sub is not None,
        has_password=u.password_hash is not None,
        organization_id=str(u.organization_id) if u.organization_id else None,
        full_name=u.full_name,
        date_of_birth=u.date_of_birth,
        emergency_contact=u.emergency_contact,
        patient_code=u.patient_code,
        preferences=u.preferences or {},
        mfa_enabled=u.mfa_enabled,
        created_at=u.created_at,
    )


def device_out(d: Device) -> DeviceOut:
    return DeviceOut(
        id=str(d.id),
        model_code=d.model.code,
        model_name=d.model.name,
        kind=d.model.kind,
        capabilities=list(d.model.capabilities),
        serial=d.serial,
        client_ref=d.client_ref,
        display_name=d.display_name,
        ble_identifier=d.ble_identifier,
        firmware=d.firmware,
        battery_pct=d.battery_pct,
        status=d.status,
        registered_at=d.registered_at,
        last_seen_at=d.last_seen_at,
        last_sync_at=d.last_sync_at,
        last_sync_error=d.last_sync_error,
        last_sync_error_at=d.last_sync_error_at,
    )


def measurement_out(m: Measurement) -> MeasurementOut:
    return MeasurementOut(
        id=m.client_id,
        server_id=str(m.id),
        vital=m.vital,
        unit=m.unit,
        value=m.value,
        systolic=m.systolic,
        diastolic=m.diastolic,
        mean_arterial=m.mean_arterial,
        pulse_rate=m.pulse_rate,
        measured_at=iso_with_offset(m.measured_at, m.utc_offset_min),
        received_at=iso_with_offset(m.received_at, m.utc_offset_min),
        ingested_at=m.ingested_at,
        provenance=ProvenanceOut(
            device_id=str(m.device_id) if m.device_id else None,
            sensor=m.sensor,
            acquisition=m.acquisition,
            quality=m.quality,
            quality_flags=list(m.quality_flags or []),
        ),
        glucose_meta=m.glucose_meta,
    )


def grant_status(g: AccessGrant) -> str:
    if g.status in ("active", "pending") and g.expires_at is not None and g.expires_at <= utcnow():
        return "expired"
    return g.status


def grant_out(g: AccessGrant) -> GrantOut:
    return GrantOut(
        id=str(g.id),
        patient_id=str(g.patient_id),
        grantee_user_id=str(g.grantee_user_id) if g.grantee_user_id else None,
        grantee_name=g.grantee_name,
        grantee_contact=g.grantee_contact,
        role=g.role,
        vitals=list(g.vitals),
        history=g.history,
        downloads_allowed=g.downloads_allowed,
        device_notices=g.device_notices,
        health_alerts=g.health_alerts,
        expires_at=g.expires_at,
        status=grant_status(g),
        created_at=g.created_at,
        accepted_at=g.accepted_at,
        revoked_at=g.revoked_at,
    )


def ecg_out(e: EcgRecording) -> EcgOut:
    return EcgOut(
        id=str(e.id),
        client_id=e.client_id,
        device_id=str(e.device_id) if e.device_id else None,
        started_at=iso_with_offset(e.started_at, e.utc_offset_min),
        duration_s=round(e.sample_count / e.sample_rate_hz, 3),  # sample_count = frames
        lead=e.lead,
        leads=list(e.leads or [e.lead]),
        sample_rate_hz=e.sample_rate_hz,
        sample_count=e.sample_count,
        sample_format=e.sample_format,
        calibration=Calibration(mm_per_mv=e.mm_per_mv, mm_per_sec=e.mm_per_sec),
        average_hr=e.average_hr,
        poor_quality_segments=[Segment(**s) for s in (e.poor_quality_segments or [])],
        quality=e.quality,
        acquisition=e.acquisition,
        sha256=e.sha256,
        created_at=e.created_at,
    )


def notification_out(n: Notification) -> NotificationOut:
    return NotificationOut(
        id=str(n.id),
        kind=n.kind,
        title=n.title,
        body=n.body,
        link=n.link,
        patient_id=str(n.patient_id) if n.patient_id else None,
        created_at=n.created_at,
        read_at=n.read_at,
    )


def report_out(r: Report) -> ReportOut:
    return ReportOut(
        id=str(r.id),
        patient_id=str(r.patient_id),
        status=r.status,
        params=r.params,
        pages=r.pages,
        size_bytes=r.size_bytes,
        error=r.error,
        created_at=r.created_at,
        completed_at=r.completed_at,
    )


def organization_out(o: Organization) -> OrganizationOut:
    return OrganizationOut(
        id=str(o.id),
        unique_id=o.unique_id,
        name=o.name,
        address=o.address,
        city=o.city,
        registration_number=o.registration_number,
        phone=o.phone,
        verification_status=o.verification_status,
        verified_at=o.verified_at,
        review_note=o.review_note,
    )
