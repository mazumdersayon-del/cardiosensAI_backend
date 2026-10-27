"""Links between 16-digit IDs: patient↔doctor, patient↔clinic, doctor↔clinic.

- Either side can ask, by typing the other's ID. The asking side counts as having approved.
- A link becomes active only when both sides approve, so the patient approves every patient link.
- A clinic can ask a patient to link with one of its own doctors (the doctor's side is pre-approved).
- Either side can end an active link at any time. For patient links this revokes the access
  grant at once, including report download links already issued.
- Doctors and clinics must be verified and use two-step sign-in before they can ask or approve.
"""

import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import RequestMeta, get_current_user, request_meta
from app.core.db import get_db, utcnow
from app.core.errors import bad_request, conflict, forbidden, not_found
from app.core.ids import normalize_unique_id, role_of
from app.core.ratelimit import limiter
from app.models import AccessGrant, AuditEvent, CareLink, EcgLiveSession, EcgRecording, Measurement, Organization, ProfessionalProfile, User
from app.models.link import LINK_SIDES
from app.schemas.links import ClinicDoctorOut, EcgBriefOut, LatestValueOut, LinkedPatientOut, LinkOut, LinkRequestIn, PartyOut
from app.services import audit
from app.services.accounts import account_status, can_view_patients, organization_of
from app.services.authz import SHAREABLE, VITALS
from app.services.ecg import live_state
from app.services.notifications import notify

router = APIRouter(tags=["links"])

OPEN = ("pending", "active")


# ---------------------------------------------------------------- parties


def _side(user: User) -> str:
    if user.role not in ("patient", "doctor", "clinic"):
        raise forbidden("Staff accounts can't link to patients, doctors or clinics")
    return user.role


def _party_user(db: Session, uid: str) -> User | None:
    u = db.scalar(select(User).where(User.unique_id == uid, User.role == role_of(uid)))
    return u if u is not None and u.status == "active" else None


def _party_org(db: Session, uid: str) -> Organization | None:
    return db.scalar(select(Organization).where(Organization.unique_id == uid))


def _org_owner(db: Session, org: Organization) -> User | None:
    owner = db.get(User, org.owner_user_id) if org.owner_user_id else None
    return owner if owner is not None and owner.status == "active" else None


def _user_party(db: Session, u: User) -> PartyOut:
    if u.role == "doctor":
        prof = db.get(ProfessionalProfile, u.id)
        return PartyOut(
            unique_id=u.unique_id,
            role="doctor",
            name=u.full_name,
            verified=prof is not None and prof.verification_status == "verified",
            detail=prof.specialty if prof else None,
        )
    return PartyOut(unique_id=u.unique_id, role="patient", name=u.full_name)


def _org_party(o: Organization) -> PartyOut:
    return PartyOut(unique_id=o.unique_id, role="clinic", name=o.name, verified=o.verification_status == "verified", detail=o.city)


def _my_condition(user: User):  # noqa: ANN202
    if user.role == "patient":
        return CareLink.patient_id == user.id
    if user.role == "doctor":
        return CareLink.doctor_id == user.id
    return CareLink.organization_id == user.organization_id


def _is_party(user: User, link: CareLink) -> bool:
    if user.role == "patient":
        return link.patient_id == user.id
    if user.role == "doctor":
        return link.doctor_id == user.id
    return user.role == "clinic" and user.organization_id is not None and link.organization_id == user.organization_id


def _ok_attr(side: str) -> str:
    return {"patient": "patient_ok_at", "doctor": "doctor_ok_at", "clinic": "clinic_ok_at"}[side]


def _other_side(link: CareLink, me: str) -> str:
    a, b = LINK_SIDES[link.kind]
    return b if a == me else a


def _other_party(db: Session, link: CareLink, me: str) -> PartyOut:
    other = _other_side(link, me)
    if other == "clinic":
        return _org_party(db.get(Organization, link.organization_id))
    return _user_party(db, db.get(User, link.patient_id if other == "patient" else link.doctor_id))


def _link_out(db: Session, user: User, link: CareLink) -> LinkOut:
    me = _side(user)
    via = None
    requester = db.get(User, link.requested_by)
    if link.kind == "patient_doctor" and requester is not None and requester.role == "clinic":
        org = organization_of(db, requester)
        via = _org_party(org) if org else None
    return LinkOut(
        id=str(link.id),
        kind=link.kind,
        status=link.status,
        other=_other_party(db, link, me),
        via_clinic=via,
        direction="outgoing" if link.requested_by == user.id else "incoming",
        awaiting_me=link.status == "pending" and getattr(link, _ok_attr(me)) is None,
        created_at=link.created_at,
        decided_at=link.decided_at,
        ended_at=link.ended_at,
    )


def _require_can_link(db: Session, user: User) -> None:
    st = account_status(db, user)
    if st.can_link:
        return
    reasons = {
        "verify_contact": "confirm your mobile number or email address",
        "verification": "wait for CardiosensAI to verify your registration",
        "enable_mfa": "turn on two-step sign-in",
    }
    raise forbidden("Before linking, " + " and ".join(reasons[m] for m in st.missing if m in reasons) + ".")


def _counterparty_ids(db: Session, link: CareLink, side: str) -> list[uuid.UUID]:
    """User ids to notify for one side of a link."""
    if side == "patient":
        return [link.patient_id]
    if side == "doctor":
        return [link.doctor_id]
    org = db.get(Organization, link.organization_id)
    owner = _org_owner(db, org) if org else None
    return [owner.id] if owner else []


# ---------------------------------------------------------------- grants


def _grantee(db: Session, link: CareLink) -> tuple[User, str] | None:
    if link.kind == "patient_doctor":
        return db.get(User, link.doctor_id), "doctor"
    if link.kind == "patient_clinic":
        org = db.get(Organization, link.organization_id)
        owner = _org_owner(db, org) if org else None
        return (owner, "clinic") if owner else None
    return None


def _activate(db: Session, link: CareLink, meta: RequestMeta, actor: User) -> None:
    now = utcnow()
    link.status = "active"
    link.decided_at = now
    g = _grantee(db, link)
    if g is not None:
        grantee, role = g
        grant = AccessGrant(
            patient_id=link.patient_id,
            grantee_user_id=grantee.id,
            grantee_name=(db.get(Organization, link.organization_id).name if role == "clinic" else grantee.full_name) or "Doctor",
            grantee_contact=grantee.unique_id or "",
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


# ---------------------------------------------------------------- endpoints


@router.get("/directory/{unique_id}", response_model=PartyOut, summary="Look up an ID before sending a link request")
def lookup(unique_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> PartyOut:
    """Name and role only. Patients can be looked up only by verified doctors and clinics."""
    _side(user)
    limiter.hit(f"lookup:{user.id}", 60, 3600)
    uid = normalize_unique_id(unique_id)
    if uid is None:
        raise bad_request("invalid_id", "That isn't a valid 16-digit CardiosensAI ID. Check the digits")
    role = role_of(uid)
    if role == "clinic":
        org = _party_org(db, uid)
        if org is None or _org_owner(db, org) is None:
            raise not_found("Account")
        return _org_party(org)
    if role == "patient" and not can_view_patients(db, user):
        raise not_found("Account")
    target = _party_user(db, uid) if role in ("patient", "doctor") else None
    if target is None:
        raise not_found("Account")
    return _user_party(db, target)


@router.post("/links", response_model=LinkOut, status_code=201, summary="Ask to link with a patient, doctor or clinic by their ID")
def request_link(
    body: LinkRequestIn, user: User = Depends(get_current_user), db: Session = Depends(get_db), meta: RequestMeta = Depends(request_meta)
) -> LinkOut:
    me = _side(user)
    _require_can_link(db, user)
    limiter.hit(f"link:{user.id}", 30, 3600)
    uid = normalize_unique_id(body.target_id)
    if uid is None:
        raise bad_request("invalid_id", "That isn't a valid 16-digit CardiosensAI ID. Check the digits")
    them = role_of(uid)
    if uid == user.unique_id:
        raise bad_request("self_link", "That's your own ID")
    pair = {me, them}
    kind = {frozenset(("patient", "doctor")): "patient_doctor", frozenset(("patient", "clinic")): "patient_clinic",
            frozenset(("doctor", "clinic")): "doctor_clinic"}.get(frozenset(pair))  # fmt: skip
    if kind is None:
        raise bad_request("invalid_pair", "Patients link with doctors and clinics; doctors link with patients and clinics")

    link = CareLink(kind=kind, requested_by=user.id)
    setattr(link, _ok_attr(me), utcnow())
    if me == "patient":
        link.patient_id = user.id
    elif me == "doctor":
        link.doctor_id = user.id
    else:
        link.organization_id = user.organization_id

    if them == "clinic":
        org = _party_org(db, uid)
        if org is None or _org_owner(db, org) is None:
            raise not_found("Account")
        link.organization_id = org.id
    else:
        target = _party_user(db, uid)
        if target is None:
            raise not_found("Account")
        if them == "patient":
            link.patient_id = target.id
        else:
            link.doctor_id = target.id

    if body.on_behalf_of_doctor_id:
        # A clinic asks a patient to link with one of the clinic's own doctors.
        if me != "clinic" or them != "patient":
            raise bad_request("invalid_request", "Only clinics can ask a patient on a doctor's behalf")
        duid = normalize_unique_id(body.on_behalf_of_doctor_id)
        doctor = _party_user(db, duid) if duid and role_of(duid) == "doctor" else None
        if doctor is None:
            raise not_found("Doctor")
        works_here = db.scalar(
            select(CareLink.id).where(
                CareLink.kind == "doctor_clinic",
                CareLink.status == "active",
                CareLink.doctor_id == doctor.id,
                CareLink.organization_id == user.organization_id,
            )
        )
        if works_here is None:
            raise forbidden("That doctor isn't linked to your clinic")
        link.kind = "patient_doctor"
        link.organization_id = None
        link.doctor_id = doctor.id
        link.doctor_ok_at = utcnow()
        link.clinic_ok_at = None

    dup = db.scalar(
        select(CareLink.id).where(
            CareLink.kind == link.kind,
            CareLink.status.in_(OPEN),
            CareLink.patient_id == link.patient_id if link.patient_id else CareLink.patient_id.is_(None),
            CareLink.doctor_id == link.doctor_id if link.doctor_id else CareLink.doctor_id.is_(None),
            CareLink.organization_id == link.organization_id if link.organization_id else CareLink.organization_id.is_(None),
        )
    )
    if dup is not None:
        raise conflict("link_exists", "There is already a pending or active link with this account")

    db.add(link)
    db.flush()
    audit.record(
        db,
        actor=user,
        action="link.request",
        patient_id=link.patient_id,
        resource_type="link",
        resource_id=link.id,
        detail=link.kind,
        meta=meta,
    )
    who = (organization_of(db, user).name if me == "clinic" else user.full_name) or "Someone"
    for side in LINK_SIDES[link.kind]:
        if getattr(link, _ok_attr(side)) is None:
            for uid_ in _counterparty_ids(db, link, side):
                notify(
                    db,
                    user_id=uid_,
                    patient_id=link.patient_id,
                    kind="sharing",
                    title="New link request",
                    body=f"{who} ({user.unique_id}) asked to link with you. Approve or decline it under Care team.",
                    link="/links",
                    dedupe_key=f"link-request:{link.id}:{side}",
                )
    db.commit()
    return _link_out(db, user, link)


@router.get("/links", response_model=list[LinkOut], summary="My links and link requests")
def list_links(include_closed: bool = False, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[LinkOut]:
    _side(user)
    if user.role == "clinic" and user.organization_id is None:
        return []
    stmt = select(CareLink).where(_my_condition(user))
    if not include_closed:
        stmt = stmt.where(CareLink.status.in_(OPEN))
    return [_link_out(db, user, link) for link in db.scalars(stmt.order_by(CareLink.created_at.desc()).limit(500))]


def _my_link(db: Session, user: User, link_id: uuid.UUID) -> CareLink:
    link = db.get(CareLink, link_id, with_for_update=True)
    if link is None or not _is_party(user, link):
        raise not_found("Link")
    return link


@router.post("/links/{link_id}/approve", response_model=LinkOut, summary="Approve a link request")
def approve_link(
    link_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db), meta: RequestMeta = Depends(request_meta)
) -> LinkOut:
    me = _side(user)
    _require_can_link(db, user)
    link = _my_link(db, user, link_id)
    if link.status != "pending":
        raise bad_request("not_pending", "This request is no longer pending")
    if getattr(link, _ok_attr(me)) is None:
        setattr(link, _ok_attr(me), utcnow())
    if all(getattr(link, _ok_attr(side)) is not None for side in LINK_SIDES[link.kind]):
        _activate(db, link, meta, user)
        for uid_ in _counterparty_ids(db, link, _other_side(link, me)):
            notify(
                db,
                user_id=uid_,
                patient_id=link.patient_id,
                kind="sharing",
                title="Link approved",
                body=f"{_other_party(db, link, _other_side(link, me)).name or 'Your contact'} is now linked with you.",
                link="/links",
                dedupe_key=f"link-active:{link.id}",
            )
    db.commit()
    return _link_out(db, user, link)


@router.post("/links/{link_id}/decline", response_model=LinkOut, summary="Decline or cancel a pending request")
def decline_link(
    link_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db), meta: RequestMeta = Depends(request_meta)
) -> LinkOut:
    _side(user)
    link = _my_link(db, user, link_id)
    if link.status != "pending":
        raise bad_request("not_pending", "This request is no longer pending")
    link.status = "declined"
    link.decided_at = utcnow()
    audit.record(
        db,
        actor=user,
        action="link.decline",
        patient_id=link.patient_id,
        resource_type="link",
        resource_id=link.id,
        detail=link.kind,
        meta=meta,
    )
    db.commit()
    return _link_out(db, user, link)


@router.post("/links/{link_id}/end", response_model=LinkOut, summary="End an active link (patient data access stops at once)")
def end_link(
    link_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db), meta: RequestMeta = Depends(request_meta)
) -> LinkOut:
    me = _side(user)
    link = _my_link(db, user, link_id)
    if link.status != "active":
        raise bad_request("not_active", "This link isn't active")
    now = utcnow()
    link.status = "ended"
    link.ended_at = now
    link.ended_by = user.id
    if link.grant_id:
        g = db.get(AccessGrant, link.grant_id, with_for_update=True)
        if g is not None and g.status != "revoked":
            g.status = "revoked"
            g.revoked_at = now
    audit.record(
        db,
        actor=user,
        action="link.end",
        patient_id=link.patient_id,
        resource_type="link",
        resource_id=link.id,
        detail=link.kind,
        meta=meta,
        grant_id=link.grant_id,
    )
    for uid_ in _counterparty_ids(db, link, _other_side(link, me)):
        notify(
            db,
            user_id=uid_,
            patient_id=link.patient_id,
            kind="sharing",
            title="Link ended",
            body="A link with your account was ended. Access to shared data has stopped.",
            link="/links",
            dedupe_key=f"link-ended:{link.id}",
        )
    db.commit()
    return _link_out(db, user, link)


SUMMARY_VITALS = ("hr", "spo2", "bp")


def _summary(db: Session, viewer: User, grant: AccessGrant, patient_id: uuid.UUID) -> dict:
    """Newest readings, last ECG and live state for the dashboard. Logged in the patient's access log at most hourly per viewer."""
    latest: dict[str, LatestValueOut] = {}
    for vital in SUMMARY_VITALS:
        if vital not in grant.vitals:
            continue
        m = db.scalar(
            select(Measurement)
            .where(Measurement.patient_id == patient_id, Measurement.vital == vital)
            .order_by(Measurement.measured_at.desc())
            .limit(1)
        )
        if m is not None:
            latest[vital] = LatestValueOut(
                value=m.value, systolic=m.systolic, diastolic=m.diastolic, unit=m.unit, measured_at=m.measured_at
            )
    ecg = live = None
    if "ecg" in grant.vitals:
        rec = db.scalar(select(EcgRecording).where(EcgRecording.patient_id == patient_id).order_by(EcgRecording.started_at.desc()).limit(1))
        if rec is not None:
            ecg = EcgBriefOut(
                id=str(rec.id),
                started_at=rec.started_at,
                lead=rec.lead,
                lead_count=len(rec.leads or [rec.lead]),
                duration_s=round(rec.sample_count / rec.sample_rate_hz, 1),
                average_hr=rec.average_hr,
            )
        sess = db.scalar(select(EcgLiveSession).where(EcgLiveSession.patient_id == patient_id, EcgLiveSession.status == "live"))
        live = live_state(sess) if sess is not None else None
    if latest or ecg:
        recent = db.scalar(
            select(AuditEvent.id).where(
                AuditEvent.actor_user_id == viewer.id,
                AuditEvent.patient_id == patient_id,
                AuditEvent.action == "dashboard.summary",
                AuditEvent.at >= utcnow() - timedelta(hours=1),
            )
        )
        if recent is None:
            audit.record(db, actor=viewer, action="dashboard.summary", patient_id=patient_id, detail="Latest readings", grant_id=grant.id)
    return {"latest": latest, "latest_ecg": ecg, "ecg_live": live}


def _last_measurement(db: Session, patient_id: uuid.UUID) -> datetime | None:
    return db.scalar(select(func.max(Measurement.measured_at)).where(Measurement.patient_id == patient_id, Measurement.vital.in_(VITALS)))


@router.get("/linked-patients", response_model=list[LinkedPatientOut], summary="Doctor / clinic dashboard: patients who approved a link")
def linked_patients(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[LinkedPatientOut]:
    if user.role not in ("doctor", "clinic"):
        raise forbidden("Only doctor and clinic accounts have a patient list")
    if not can_view_patients(db, user):
        return []
    links = list(db.scalars(select(CareLink).where(_my_condition(user), CareLink.status == "active", CareLink.patient_id.is_not(None))))
    clinic_doctors: dict[uuid.UUID, User] = {}
    if user.role == "clinic":
        for dl in db.scalars(
            select(CareLink).where(
                CareLink.kind == "doctor_clinic", CareLink.status == "active", CareLink.organization_id == user.organization_id
            )
        ):
            d = db.get(User, dl.doctor_id)
            if d is not None and d.status == "active":
                clinic_doctors[d.id] = d
    out: list[LinkedPatientOut] = []
    for link in links:
        grant = db.get(AccessGrant, link.grant_id) if link.grant_id else None
        patient = db.get(User, link.patient_id)
        if grant is None or grant.status != "active" or patient is None or patient.status != "active":
            continue
        doctors: list[PartyOut] = []
        if clinic_doctors:
            for did in db.scalars(
                select(CareLink.doctor_id).where(
                    CareLink.kind == "patient_doctor",
                    CareLink.status == "active",
                    CareLink.patient_id == patient.id,
                    CareLink.doctor_id.in_(list(clinic_doctors)),
                )
            ):
                doctors.append(_user_party(db, clinic_doctors[did]))
        out.append(
            LinkedPatientOut(
                **_summary(db, user, grant, patient.id),
                patient_id=str(patient.id),
                unique_id=patient.unique_id,
                full_name=patient.full_name,
                date_of_birth=patient.date_of_birth,
                patient_code=patient.patient_code,
                linked_at=link.decided_at,
                last_measurement_at=_last_measurement(db, patient.id),
                doctors=doctors,
            )
        )
    db.commit()
    return out


@router.get("/clinic/doctors", response_model=list[ClinicDoctorOut], summary="Clinic: doctors linked to this clinic")
def clinic_doctors(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[ClinicDoctorOut]:
    if user.role != "clinic" or user.organization_id is None:
        raise forbidden("Only clinic accounts have a doctor roster")
    my_patients = select(CareLink.patient_id).where(
        CareLink.kind == "patient_clinic", CareLink.status == "active", CareLink.organization_id == user.organization_id
    )
    out: list[ClinicDoctorOut] = []
    for dl in db.scalars(
        select(CareLink)
        .where(CareLink.kind == "doctor_clinic", CareLink.status == "active", CareLink.organization_id == user.organization_id)
        .order_by(CareLink.decided_at.desc())
    ):
        d = db.get(User, dl.doctor_id)
        if d is None or d.status != "active":
            continue
        count = db.scalar(
            select(func.count()).where(
                CareLink.kind == "patient_doctor",
                CareLink.status == "active",
                CareLink.doctor_id == d.id,
                CareLink.patient_id.in_(my_patients),
            )
        )
        out.append(ClinicDoctorOut(link_id=str(dl.id), doctor=_user_party(db, d), linked_at=dl.decided_at, patient_count=count or 0))
    return out
