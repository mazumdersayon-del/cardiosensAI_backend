"""Turning an approved link into an access grant, and the link a patient asks for while booking.

The link endpoints live in api/v1/links.py; appointments reuse these so a booking can carry a link request
that the doctor approves by accepting the appointment.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import RequestMeta
from app.core.db import utcnow
from app.models import AccessGrant, CareLink, Organization, User
from app.services import audit
from app.services.accounts import account_status
from app.services.authz import SHAREABLE
from app.services.notifications import notify

OPEN = ("pending", "active")


def org_owner(db: Session, org: Organization) -> User | None:
    owner = db.get(User, org.owner_user_id) if org.owner_user_id else None
    return owner if owner is not None and owner.status == "active" else None


def grantee(db: Session, link: CareLink) -> tuple[User, str] | None:
    if link.kind == "patient_doctor":
        return db.get(User, link.doctor_id), "doctor"
    if link.kind == "patient_clinic":
        org = db.get(Organization, link.organization_id)
        owner = org_owner(db, org) if org else None
        return (owner, "clinic") if owner else None
    return None


def activate(db: Session, link: CareLink, meta: RequestMeta | None, actor: User) -> None:
    now = utcnow()
    link.status = "active"
    link.decided_at = now
    g = grantee(db, link)
    if g is not None:
        who, role = g
        grant = AccessGrant(
            patient_id=link.patient_id,
            grantee_user_id=who.id,
            grantee_name=(db.get(Organization, link.organization_id).name if role == "clinic" else who.full_name) or "Doctor",
            grantee_contact=who.unique_id or "",
            role=role,
            vitals=list(SHAREABLE),
            history="all",
            downloads_allowed=True,
            status="active",
            accepted_at=now,
        )
        db.add(grant)
        db.flush()
        link.grant_id = grant.id
    audit.record(
        db,
        actor=actor,
        action="link.activate",
        patient_id=link.patient_id,
        resource_type="link",
        resource_id=link.id,
        detail=link.kind,
        meta=meta,
        grant_id=link.grant_id,
    )


def doctor_link(db: Session, patient_id: uuid.UUID, doctor_id: uuid.UUID) -> CareLink | None:
    """The pending or active patient↔doctor link between them, if any."""
    return db.scalar(
        select(CareLink)
        .where(
            CareLink.kind == "patient_doctor",
            CareLink.patient_id == patient_id,
            CareLink.doctor_id == doctor_id,
            CareLink.status.in_(OPEN),
        )
        .limit(1)
    )


def request_at_booking(db: Session, patient: User, doctor: User, meta: RequestMeta | None) -> CareLink | None:
    """The patient ticked 'also link with this doctor': a pending request the patient has approved.

    Skipped (None) when they're already linked or asked, or the patient can't link yet (contact not confirmed).
    """
    if doctor_link(db, patient.id, doctor.id) is not None or not account_status(db, patient).can_link:
        return None
    now = utcnow()
    link = CareLink(kind="patient_doctor", requested_by=patient.id, patient_id=patient.id, doctor_id=doctor.id, patient_ok_at=now)
    db.add(link)
    db.flush()
    audit.record(
        db,
        actor=patient,
        action="link.request",
        patient_id=patient.id,
        resource_type="link",
        resource_id=link.id,
        detail=link.kind,
        meta=meta,
    )
    notify(
        db,
        user_id=doctor.id,
        patient_id=patient.id,
        kind="sharing",
        title="New link request",
        body=f"{patient.full_name or 'A patient'} ({patient.unique_id}) asked to link with you when booking. "
        "Accepting the appointment approves it.",
        link="/links",
        dedupe_key=f"link-request:{link.id}:doctor",
    )
    return link


def approve_with_appointment(db: Session, patient: User, doctor: User, meta: RequestMeta | None) -> bool:
    """The doctor accepted an appointment: approve the patient's own pending request, if there is one."""
    link = doctor_link(db, patient.id, doctor.id)
    if link is None or link.status != "pending" or link.patient_ok_at is None or not account_status(db, doctor).can_link:
        return False
    link.doctor_ok_at = utcnow()
    activate(db, link, meta, doctor)
    notify(
        db,
        user_id=patient.id,
        patient_id=patient.id,
        kind="sharing",
        title="Link approved",
        body=f"{doctor.full_name or 'Your doctor'} is now linked with you.",
        link="/links",
        dedupe_key=f"link-active:{link.id}",
    )
    return True
