"""Appointments: the doctor's calendar (weekly hours, offline mode, requests, statistics) and patients' bookings.

Rules in services/appointments.py. Booking never gives a doctor access to health data; only a link does.
"""

import uuid
from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import RequestMeta, get_current_user, request_meta
from app.core.db import get_db, utcnow
from app.core.errors import bad_request, forbidden, not_found
from app.core.ids import normalize_unique_id
from app.core.ratelimit import limiter
from app.models import Appointment, CareLink, DoctorOfflinePeriod, DoctorSchedule, ProfessionalProfile, User
from app.schemas.appointments import (
    AppointmentOut,
    AppointmentStats,
    BookIn,
    CalendarSettingsIn,
    CalendarSettingsOut,
    CalendarWeekOut,
    DecisionIn,
    DoctorCalendarOut,
    GoOfflineIn,
    OfflinePeriodIn,
    OfflinePeriodOut,
    OfflineStatus,
    PersonOut,
    SlotOut,
    WalkInIn,
    WeeklyHours,
)
from app.services import accounts, audit, care_links
from app.services import appointments as appts

router = APIRouter(tags=["appointments"])


def _doctor(user: User) -> User:
    if user.role != "doctor":
        raise forbidden("The appointment calendar is for doctor accounts")
    return user


def _period(p: DoctorOfflinePeriod) -> OfflinePeriodOut:
    return OfflinePeriodOut(id=str(p.id), starts_at=p.starts_at, ends_at=p.ends_at, kind=p.kind, reason=p.reason)


def _offline(db: Session, doctor: User, now: datetime) -> OfflineStatus:
    until = appts.offline_until(db, doctor.id, now)
    periods = appts.offline_periods(db, doctor.id, now, now + timedelta(days=400))
    return OfflineStatus(offline=until is not None, until=until, periods=[_period(p) for p in periods])


def _settings(db: Session, doctor: User, now: datetime) -> CalendarSettingsOut:
    s = appts.schedule(db, doctor)
    hint = None
    if not accounts.can_view_patients(db, doctor):
        hint = "Patients can book once CardiosensAI has verified your registration and two-step sign-in is on."
    elif not s.weekly_hours:
        hint = "Add your weekly hours, then turn bookings on."
    elif not s.enabled:
        hint = "Bookings are off: patients can't see your slots."
    return CalendarSettingsOut(
        enabled=s.enabled,
        timezone=s.timezone,
        slot_min=s.slot_min,
        window_days=s.window_days,
        weekly_hours=[WeeklyHours(**h) for h in s.weekly_hours or []],
        online=s.online,
        in_person=s.in_person,
        meeting_link=s.meeting_link,
        bookable=hint is None,
        hint=hint,
        offline=_offline(db, doctor, now),
    )


def _out(db: Session, a: Appointment, viewer: User) -> AppointmentOut:
    doctor = db.get(User, a.doctor_id)
    patient = db.get(User, a.patient_id) if a.patient_id else None
    prof = db.get(ProfessionalProfile, a.doctor_id)
    link = care_links.doctor_link(db, patient.id, a.doctor_id) if patient else None
    linked = bool(link and link.status == "active" and viewer.id == a.doctor_id)
    return AppointmentOut(
        id=str(a.id),
        starts_at=a.starts_at,
        ends_at=a.ends_at,
        mode=a.mode,
        status=a.status,
        source=a.source,
        reason=a.reason,
        doctor_note=a.doctor_note,
        meeting_link=a.meeting_link if a.status == "accepted" and a.mode == "online" else None,
        doctor=PersonOut(unique_id=doctor.unique_id if doctor else None, full_name=(doctor.full_name if doctor else None) or "Doctor"),
        patient=PersonOut(
            unique_id=patient.unique_id if patient else None,
            full_name=(patient.full_name if patient else a.guest_name) or "Patient",
        ),
        patient_linked=linked,
        patient_record_id=str(patient.id) if linked and patient else None,
        link_pending=bool(link and link.status == "pending"),
        practice=prof.practice if prof else None,
        created_at=a.created_at,
    )


# ---------------------------------------------------------------- doctor: settings and offline mode


@router.get("/me/calendar", response_model=CalendarSettingsOut, summary="Doctors: my booking settings and offline status")
def get_calendar(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> CalendarSettingsOut:
    return _settings(db, _doctor(user), utcnow())


@router.put("/me/calendar", response_model=CalendarSettingsOut, summary="Doctors: change my booking settings")
def put_calendar(
    body: CalendarSettingsIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    meta: RequestMeta = Depends(request_meta),
) -> CalendarSettingsOut:
    _doctor(user)
    changes = body.model_dump(exclude_unset=True)
    changes = {k: v for k, v in changes.items() if v is not None or k == "meeting_link"}
    s = appts.update_schedule(db, user, changes)
    audit.record(db, actor=user, action="calendar.settings_update", detail="bookings on" if s.enabled else "bookings off", meta=meta)
    db.commit()
    return _settings(db, user, utcnow())


@router.post("/me/calendar/offline", response_model=OfflineStatus, summary="Doctors: go offline now (no online bookings; default 2 h)")
def go_offline(
    body: GoOfflineIn, user: User = Depends(get_current_user), db: Session = Depends(get_db), meta: RequestMeta = Depends(request_meta)
) -> OfflineStatus:
    now = utcnow()
    p = appts.go_offline(db, _doctor(user), now, body.until)
    audit.record(db, actor=user, action="calendar.offline", detail=f"until {p.ends_at.isoformat(timespec='minutes')}", meta=meta)
    db.commit()
    return _offline(db, user, now)


@router.delete("/me/calendar/offline", response_model=OfflineStatus, summary="Doctors: go online now (ends the current offline period)")
def go_online(
    user: User = Depends(get_current_user), db: Session = Depends(get_db), meta: RequestMeta = Depends(request_meta)
) -> OfflineStatus:
    now = utcnow()
    appts.go_online(db, _doctor(user), now)
    audit.record(db, actor=user, action="calendar.online", meta=meta)
    db.commit()
    return _offline(db, user, now)


@router.post(
    "/me/calendar/offline-periods", response_model=OfflinePeriodOut, status_code=201, summary="Doctors: schedule an offline period"
)
def add_offline_period(
    body: OfflinePeriodIn, user: User = Depends(get_current_user), db: Session = Depends(get_db), meta: RequestMeta = Depends(request_meta)
) -> OfflinePeriodOut:
    p = appts.add_offline_period(db, _doctor(user), body.starts_at, body.ends_at, body.reason, utcnow())
    audit.record(db, actor=user, action="calendar.offline_scheduled", meta=meta)
    db.commit()
    return _period(p)


@router.delete("/me/calendar/offline-periods/{period_id}", status_code=204, summary="Doctors: remove an offline period")
def delete_offline_period(
    period_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db), meta: RequestMeta = Depends(request_meta)
) -> None:
    p = db.get(DoctorOfflinePeriod, period_id)
    if p is None or p.doctor_id != _doctor(user).id:
        raise not_found("Offline period")
    now = utcnow()
    if p.starts_at <= now:
        p.ends_at = max(now, p.starts_at)  # started: end it now (keeps the history)
    else:
        db.delete(p)
    audit.record(db, actor=user, action="calendar.offline_removed", meta=meta)
    db.commit()


@router.get("/me/calendar/week", response_model=CalendarWeekOut, summary="Doctors: slots, appointments and offline periods of some days")
def calendar_week(
    start: date = Query(description="First day (in the doctor's time zone)"),
    days: int = Query(default=7, ge=1, le=42, description="Up to 6 weeks (a month view)"),
    with_slots: bool = Query(default=True, alias="slots", description="false: appointments and offline periods only"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CalendarWeekOut:
    s = appts.schedule(db, _doctor(user))
    tz = appts.zone(s.timezone)
    a = datetime.combine(start, time(0), tzinfo=tz)
    b = a + timedelta(days=days)
    appts.expire_stale(db, utcnow(), doctor_id=user.id)
    db.commit()
    rows = list(
        db.scalars(
            select(Appointment)
            .where(Appointment.doctor_id == user.id, Appointment.starts_at < b, Appointment.ends_at > a)
            .order_by(Appointment.starts_at)
        )
    )
    return CalendarWeekOut(
        timezone=s.timezone,
        slots=[SlotOut(**x) for x in appts.slots(db, s, a, b)] if with_slots else [],
        appointments=[_out(db, x, user) for x in rows],
        offline_periods=[_period(p) for p in appts.offline_periods(db, user.id, a, b)],
    )


@router.post(
    "/me/appointments", response_model=AppointmentOut, status_code=201, summary="Doctors: add a visit (walk-in or arranged outside the app)"
)
def add_walk_in(
    body: WalkInIn, user: User = Depends(get_current_user), db: Session = Depends(get_db), meta: RequestMeta = Depends(request_meta)
) -> AppointmentOut:
    _doctor(user)
    patient = None
    if body.patient_id:
        uid = normalize_unique_id(body.patient_id)
        patient = db.scalar(select(User).where(User.unique_id == uid, User.role == "patient", User.status == "active")) if uid else None
        if patient is None or not appts.linked(db, patient.id, user.id):
            raise bad_request("not_linked", "Only patients linked with you can be added by ID; enter a name instead")
    a = appts.walk_in(db, user, patient, body.name, body.starts_at, body.mode, utcnow())
    audit.record(
        db,
        actor=user,
        action="appointment.walk_in",
        patient_id=patient.id if patient else None,
        resource_type="appointment",
        resource_id=a.id,
        meta=meta,
    )
    db.commit()
    return _out(db, a, user)


@router.get("/me/appointment-stats", response_model=AppointmentStats, summary="Doctors: appointment statistics")
def appointment_stats(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> AppointmentStats:
    out = appts.stats(db, _doctor(user), utcnow())
    db.commit()
    return AppointmentStats(**out)


# ---------------------------------------------------------------- both sides


@router.get("/appointments", response_model=list[AppointmentOut], summary="My appointments (doctor: with patients; patient: with doctors)")
def list_appointments(
    scope: str = Query(default="upcoming", pattern="^(upcoming|past|all)$"),
    status: list[str] | None = Query(default=None),
    with_user: uuid.UUID | None = Query(default=None, alias="with", description="Only those with this user id"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[AppointmentOut]:
    now = utcnow()
    appts.expire_stale(db, now, doctor_id=user.id if user.role == "doctor" else None, patient_id=user.id if user.role != "doctor" else None)
    db.commit()
    start, end = (now, None) if scope == "upcoming" else (None, now) if scope == "past" else (None, None)
    rows = appts.visible_appointments(db, user, start, end, status, with_user)
    if scope == "past":
        rows.reverse()
    return [_out(db, a, user) for a in rows]


@router.post(
    "/appointments/{appointment_id}/decision", response_model=AppointmentOut, summary="Accept, decline, cancel, or mark completed / no-show"
)
def decide(
    appointment_id: uuid.UUID,
    body: DecisionIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    meta: RequestMeta = Depends(request_meta),
) -> AppointmentOut:
    a = appts.get_for(db, user, appointment_id)
    now = utcnow()
    if user.id == a.doctor_id:
        appts.decide(db, user, a, body.action, body.note, body.meeting_link, now)
        patient = db.get(User, a.patient_id) if a.patient_id else None
        if body.action == "accept" and patient is not None:
            care_links.approve_with_appointment(db, patient, user, meta)
    elif body.action == "cancel":
        appts.cancel(db, user, a, None, now)
    else:
        raise forbidden("Only the doctor can do that")
    audit.record(
        db,
        actor=user,
        action=f"appointment.{body.action}",
        patient_id=a.patient_id,
        resource_type="appointment",
        resource_id=a.id,
        meta=meta,
    )
    db.commit()
    return _out(db, a, user)


# ---------------------------------------------------------------- patients: booking


@router.get("/appointments/doctors", response_model=list[PersonOut], summary="Patients: linked doctors who take bookings")
def bookable_doctors(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[PersonOut]:
    if user.role != "patient":
        return []
    rows = db.scalars(
        select(User)
        .join(CareLink, CareLink.doctor_id == User.id)
        .join(DoctorSchedule, DoctorSchedule.user_id == User.id)
        .where(
            CareLink.kind == "patient_doctor", CareLink.patient_id == user.id, CareLink.status == "active", DoctorSchedule.enabled.is_(True)
        )
        .order_by(User.full_name)
    ).unique()
    return [PersonOut(unique_id=d.unique_id, full_name=d.full_name or "Doctor") for d in rows if appts.bookable_reason(db, user, d) is None]


@router.get("/doctors/{unique_id}/calendar", response_model=DoctorCalendarOut, summary="Patients: a doctor's free slots")
def doctor_calendar(unique_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> DoctorCalendarOut:
    limiter.hit(f"doctor-calendar:{user.id}", 300, 3600)
    d = appts.find_doctor(db, user, normalize_unique_id(unique_id) or "")
    now = utcnow()
    reason = appts.bookable_reason(db, user, d)
    s = appts.schedule(db, d)
    prof = db.get(ProfessionalProfile, d.id)
    return DoctorCalendarOut(
        doctor=PersonOut(unique_id=d.unique_id, full_name=d.full_name or "Doctor"),
        bookable=reason is None,
        reason=reason,
        timezone=s.timezone,
        online=s.online,
        in_person=s.in_person,
        offline_until=appts.offline_until(db, d.id, now),
        practice=prof.practice if prof else None,
        city=prof.city if prof else None,
        window_days=s.window_days,
        limit_reached=appts.open_count(db, user.id, d.id, now) >= appts.MAX_OPEN,
        link_status=link.status if (link := care_links.doctor_link(db, user.id, d.id)) else None,
        slots=[
            SlotOut(starts_at=x["starts_at"], ends_at=x["ends_at"], online=x["online"], in_person=x["in_person"])
            for x in appts.open_slots(db, s, now)
        ]
        if reason is None
        else [],
    )


@router.post(
    "/appointments", response_model=AppointmentOut, status_code=201, summary="Patients: book a slot (the doctor accepts or declines)"
)
def book(
    body: BookIn, user: User = Depends(get_current_user), db: Session = Depends(get_db), meta: RequestMeta = Depends(request_meta)
) -> AppointmentOut:
    limiter.hit(f"appointment-book:{user.id}", 20, 3600)
    d = appts.find_doctor(db, user, normalize_unique_id(body.doctor_id) or "")
    a = appts.book(db, user, d, body.starts_at, body.mode, body.reason, utcnow())
    audit.record(db, actor=user, action="appointment.request", patient_id=user.id, resource_type="appointment", resource_id=a.id, meta=meta)
    if body.allow_link:
        care_links.request_at_booking(db, user, d, meta)
    db.commit()
    return _out(db, a, user)
