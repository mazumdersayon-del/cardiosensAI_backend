"""Appointments: slots from a doctor's weekly hours, offline periods, booking, the doctor's decisions and statistics.

Rules (models/appointment.py has the data):
- Who may book: a patient with an active link to the doctor, or any patient when the doctor is in the Doctors list. The
  doctor must be able to see patients (verified, two-step sign-in, active) and have bookings switched on. Booking an
  appointment never gives the doctor access to health data; only a link does.
- Slots: every `slot_min` minutes inside the weekly hours (doctor's time zone), `window_days` ahead, starting at least
  NOTICE after now. A slot held by a requested or accepted appointment is taken (unique index). Offline periods remove
  online slots only; in-person slots stay bookable.
- A request the doctor hasn't answered when its slot starts expires. At most MAX_OPEN open appointments per patient
  and doctor. After the visit the doctor marks it completed (a patient served) or no-show.
- Each step tells the other side in the app and by email; accepted appointments get a reminder REMIND_BEFORE ahead
  (app/scripts/appointment_reminders.py: in the app, by email and, once a template is set, by SMS).
"""

import uuid
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.core.errors import ApiError, bad_request, conflict, forbidden, not_found
from app.models import Appointment, CareLink, DoctorOfflinePeriod, DoctorSchedule, ProfessionalProfile, User
from app.models.appointment import OPEN_STATUSES
from app.services import accounts, email, notifications, sms

NOTICE = timedelta(hours=1)
MAX_OPEN = 2
DEFAULT_OFFLINE = timedelta(hours=2)
MAX_OFFLINE = timedelta(days=60)
REMIND_BEFORE = timedelta(hours=1)
SLOT_MINUTES = (10, 15, 20, 30, 45, 60)
WINDOW_DAYS = (7, 14, 30)
DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


# ---------------------------------------------------------------- settings


def schedule(db: Session, doctor: User) -> DoctorSchedule:
    """The doctor's settings (defaults, not saved, until the doctor saves them)."""
    s = db.get(DoctorSchedule, doctor.id)
    if s is None:
        s = DoctorSchedule(
            user_id=doctor.id,
            enabled=False,
            timezone="Asia/Kolkata",
            slot_min=15,
            window_days=14,
            weekly_hours=[],
            online=True,
            in_person=True,
        )
    return s


def zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ApiError(400, "bad_timezone", "Unknown time zone") from exc


def _hm(v: str) -> time:
    try:
        h, m = v.split(":")
        return time(int(h), int(m))
    except ValueError as exc:
        raise bad_request("bad_hours", f"{v!r} isn't a time (HH:MM)") from exc


def clean_hours(rows: list[dict]) -> list[dict]:
    """Weekly hours: day 0-6 (Monday = 0), start < end, no overlaps on a day. Sorted."""
    out: list[dict] = []
    for r in rows:
        day, start, end = int(r["day"]), _hm(r["start"]), _hm(r["end"])
        if not 0 <= day <= 6:
            raise bad_request("bad_hours", "Day must be 0 (Monday) to 6 (Sunday)")
        if start >= end:
            raise bad_request("bad_hours", f"{DAYS[day]}: the start ({r['start']}) must be before the end ({r['end']})")
        out.append({"day": day, "start": start.strftime("%H:%M"), "end": end.strftime("%H:%M")})
    out.sort(key=lambda r: (r["day"], r["start"]))
    for a, b in zip(out, out[1:], strict=False):
        if a["day"] == b["day"] and b["start"] < a["end"]:
            raise bad_request("bad_hours", f"{DAYS[a['day']]}: {a['start']}–{a['end']} and {b['start']}–{b['end']} overlap")
    return out


def update_schedule(db: Session, doctor: User, changes: dict) -> DoctorSchedule:
    s = db.get(DoctorSchedule, doctor.id)
    if s is None:
        s = schedule(db, doctor)
        db.add(s)
    if "timezone" in changes:
        zone(changes["timezone"])
    if "slot_min" in changes and changes["slot_min"] not in SLOT_MINUTES:
        raise bad_request("bad_slot_length", f"Slot length must be one of {', '.join(map(str, SLOT_MINUTES))} minutes")
    if "window_days" in changes and changes["window_days"] not in WINDOW_DAYS:
        raise bad_request("bad_window", f"Booking window must be one of {', '.join(map(str, WINDOW_DAYS))} days")
    if "weekly_hours" in changes:
        changes["weekly_hours"] = clean_hours(changes["weekly_hours"])
    if "meeting_link" in changes:
        changes["meeting_link"] = clean_link(changes["meeting_link"])
    for k, v in changes.items():
        setattr(s, k, v)
    if not (s.online or s.in_person):
        raise bad_request("no_appointment_type", "Offer online appointments, in-person visits or both")
    if s.enabled and not s.weekly_hours:
        raise bad_request("no_hours", "Add your weekly hours before turning bookings on")
    s.updated_at = utcnow()
    return s


def clean_link(v: str | None) -> str | None:
    v = (v or "").strip()
    if not v:
        return None
    if not v.startswith("https://") or any(c.isspace() for c in v) or len(v) > 300:
        raise bad_request("bad_meeting_link", "The meeting link must be an https:// address (Google Meet, Zoom, WhatsApp, ...)")
    return v


# ---------------------------------------------------------------- offline


def offline_periods(db: Session, doctor_id: uuid.UUID, start: datetime, end: datetime) -> list[DoctorOfflinePeriod]:
    return list(
        db.scalars(
            select(DoctorOfflinePeriod)
            .where(DoctorOfflinePeriod.doctor_id == doctor_id, DoctorOfflinePeriod.ends_at > start, DoctorOfflinePeriod.starts_at < end)
            .order_by(DoctorOfflinePeriod.starts_at)
        )
    )


def offline_until(db: Session, doctor_id: uuid.UUID, now: datetime) -> datetime | None:
    """When the doctor is offline now: until when (following periods that touch are joined)."""
    until = None
    for p in offline_periods(db, doctor_id, now, now + MAX_OFFLINE * 2):
        if p.starts_at <= (until or now):
            until = max(until or p.ends_at, p.ends_at)
    return until


def go_offline(db: Session, doctor: User, now: datetime, until: datetime | None) -> DoctorOfflinePeriod:
    until = until or now + DEFAULT_OFFLINE
    if until <= now + timedelta(minutes=5):
        raise bad_request("bad_offline_end", "Pick an end at least 5 minutes from now")
    if until > now + MAX_OFFLINE:
        raise bad_request("bad_offline_end", "Offline mode can last up to 60 days; schedule longer breaks as offline periods")
    p = DoctorOfflinePeriod(doctor_id=doctor.id, starts_at=now, ends_at=until, kind="now")
    db.add(p)
    return p


def go_online(db: Session, doctor: User, now: datetime) -> int:
    """End every offline period that covers now (also a scheduled one: the doctor is back early)."""
    res = db.execute(
        update(DoctorOfflinePeriod)
        .where(DoctorOfflinePeriod.doctor_id == doctor.id, DoctorOfflinePeriod.starts_at <= now, DoctorOfflinePeriod.ends_at > now)
        .values(ends_at=now)
    )
    return res.rowcount or 0


def add_offline_period(
    db: Session, doctor: User, starts_at: datetime, ends_at: datetime, reason: str | None, now: datetime
) -> DoctorOfflinePeriod:
    if ends_at <= starts_at:
        raise bad_request("bad_offline_period", "The end must be after the start")
    if ends_at <= now:
        raise bad_request("bad_offline_period", "That period is already over")
    if ends_at - starts_at > timedelta(days=366):
        raise bad_request("bad_offline_period", "An offline period can be at most a year")
    p = DoctorOfflinePeriod(
        doctor_id=doctor.id, starts_at=max(starts_at, now), ends_at=ends_at, kind="scheduled", reason=(reason or "").strip() or None
    )
    db.add(p)
    return p


def _covered(periods: list[DoctorOfflinePeriod], start: datetime, end: datetime) -> bool:
    return any(p.starts_at < end and p.ends_at > start for p in periods)


# ---------------------------------------------------------------- slots


def slot_times(s: DoctorSchedule, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    """Every slot of the weekly hours that starts in [start, end)."""
    tz = zone(s.timezone)
    step = timedelta(minutes=s.slot_min)
    out = []
    day: date = start.astimezone(tz).date() - timedelta(days=1)
    last: date = end.astimezone(tz).date()
    while day <= last:
        for r in s.weekly_hours or []:
            if r["day"] != day.weekday():
                continue
            t = datetime.combine(day, _hm(r["start"]), tzinfo=tz)
            stop = datetime.combine(day, _hm(r["end"]), tzinfo=tz)
            while t + step <= stop:
                if start <= t < end:
                    out.append((t.astimezone(ZoneInfo("UTC")), (t + step).astimezone(ZoneInfo("UTC"))))
                t += step
        day += timedelta(days=1)
    return sorted(out)


def taken(db: Session, doctor_id: uuid.UUID, start: datetime, end: datetime) -> list[Appointment]:
    return list(
        db.scalars(
            select(Appointment).where(
                Appointment.doctor_id == doctor_id,
                Appointment.status.in_(OPEN_STATUSES),
                Appointment.starts_at < end,
                Appointment.ends_at > start,
            )
        )
    )


def slots(db: Session, s: DoctorSchedule, start: datetime, end: datetime) -> list[dict]:
    """Slots with what's possible in each: online (not offline), in person, or taken."""
    busy = taken(db, s.user_id, start, end + timedelta(days=1))
    periods = offline_periods(db, s.user_id, start, end + timedelta(days=1))
    out = []
    for a, b in slot_times(s, start, end):
        is_taken = any(x.starts_at < b and x.ends_at > a for x in busy)
        offline = _covered(periods, a, b)
        out.append(
            {
                "starts_at": a,
                "ends_at": b,
                "taken": is_taken,
                "offline": offline,
                "online": s.online and not offline and not is_taken,
                "in_person": s.in_person and not is_taken,
            }
        )
    return out


def open_slots(db: Session, s: DoctorSchedule, now: datetime) -> list[dict]:
    """What a patient may book now."""
    return [x for x in slots(db, s, now + NOTICE, now + timedelta(days=s.window_days)) if x["online"] or x["in_person"]]


# ---------------------------------------------------------------- who may book


def linked(db: Session, patient_id: uuid.UUID, doctor_id: uuid.UUID) -> bool:
    return (
        db.scalar(
            select(CareLink.id)
            .where(
                CareLink.kind == "patient_doctor",
                CareLink.patient_id == patient_id,
                CareLink.doctor_id == doctor_id,
                CareLink.status == "active",
            )
            .limit(1)
        )
        is not None
    )


def bookable_reason(db: Session, patient: User, doctor: User) -> str | None:
    """Why this patient can't book this doctor now, or None."""
    if doctor.role != "doctor" or not accounts.can_view_patients(db, doctor):
        return "This doctor isn't taking appointments."
    s = db.get(DoctorSchedule, doctor.id)
    if s is None or not s.enabled or not s.weekly_hours:
        return "This doctor isn't taking appointments in CardiosensAI yet."
    if not linked(db, patient.id, doctor.id):
        prof = db.get(ProfessionalProfile, doctor.id)
        if prof is None or not prof.listed:
            return "Link with this doctor first."
    return None


def open_count(db: Session, patient_id: uuid.UUID, doctor_id: uuid.UUID, now: datetime) -> int:
    """The patient's requested or accepted future appointments with this doctor."""
    return len(
        db.scalars(
            select(Appointment.id).where(
                Appointment.doctor_id == doctor_id,
                Appointment.patient_id == patient_id,
                Appointment.status.in_(OPEN_STATUSES),
                Appointment.starts_at > now,
            )
        ).all()
    )


def find_doctor(db: Session, patient: User, unique_id: str) -> User:
    """A doctor the patient may at least see: linked, or in the Doctors list. Anything else is 404."""
    if patient.role != "patient":
        raise forbidden("Appointments are booked from patient accounts")
    d = db.scalar(select(User).where(User.unique_id == unique_id, User.role == "doctor", User.status == "active"))
    if d is None:
        raise not_found("Doctor")
    if not linked(db, patient.id, d.id):
        prof = db.get(ProfessionalProfile, d.id)
        if prof is None or not prof.listed or not accounts.can_view_patients(db, d):
            raise not_found("Doctor")
    return d


# ---------------------------------------------------------------- appointments


def expire_stale(db: Session, now: datetime, doctor_id: uuid.UUID | None = None, patient_id: uuid.UUID | None = None) -> None:
    """Requests nobody answered before their slot started."""
    q = update(Appointment).where(Appointment.status == "requested", Appointment.starts_at <= now)
    if doctor_id:
        q = q.where(Appointment.doctor_id == doctor_id)
    if patient_id:
        q = q.where(Appointment.patient_id == patient_id)
    db.execute(q.values(status="expired", decided_at=now))


def book(db: Session, patient: User, doctor: User, starts_at: datetime, mode: str, reason: str | None, now: datetime) -> Appointment:
    why = bookable_reason(db, patient, doctor)
    if why:
        raise conflict("not_bookable", why)
    s = schedule(db, doctor)
    if mode not in ("online", "in_person"):
        raise bad_request("bad_mode", "Choose online or in person")
    if starts_at < now + NOTICE:
        raise conflict("too_soon", "Book at least 1 hour ahead")
    if starts_at > now + timedelta(days=s.window_days):
        raise conflict("too_far", f"This doctor takes bookings up to {s.window_days} days ahead")
    match = [x for x in slots(db, s, starts_at, starts_at + timedelta(seconds=1)) if x["starts_at"] == starts_at]
    if not match:
        raise conflict("no_such_slot", "That time isn't one of the doctor's slots")
    slot = match[0]
    if slot["taken"]:
        raise conflict("slot_taken", "Someone booked this slot a moment ago. Pick another one.")
    if mode == "online" and not slot["online"]:
        raise conflict("offline", "The doctor isn't taking online appointments at that time. Pick another slot or an in-person visit.")
    if mode == "in_person" and not slot["in_person"]:
        raise conflict("no_in_person", "The doctor doesn't offer in-person visits")
    expire_stale(db, now, doctor_id=doctor.id, patient_id=patient.id)
    if open_count(db, patient.id, doctor.id, now) >= MAX_OPEN:
        raise conflict("too_many", f"You already have {MAX_OPEN} upcoming appointments with this doctor")
    a = Appointment(
        doctor_id=doctor.id,
        patient_id=patient.id,
        starts_at=slot["starts_at"],
        ends_at=slot["ends_at"],
        mode=mode,
        status="requested",
        reason=(reason or "").strip()[:300] or None,
    )
    try:
        with db.begin_nested():
            db.add(a)
            db.flush()
    except IntegrityError as exc:
        raise conflict("slot_taken", "Someone booked this slot a moment ago. Pick another one.") from exc
    tell(
        db,
        doctor,
        a,
        "New appointment request",
        f"{patient.full_name or 'A patient'} asks for {describe(a, s.timezone)}. Accept or decline it on your Home page.",
    )
    return a


def walk_in(
    db: Session, doctor: User, patient: User | None, name: str | None, starts_at: datetime, mode: str, now: datetime
) -> Appointment:
    """The doctor adds a visit (in person, or online arranged outside the app): accepted, or completed if it's past."""
    s = schedule(db, doctor)
    if mode not in ("online", "in_person"):
        raise bad_request("bad_mode", "Choose online or in person")
    if patient is None and not (name or "").strip():
        raise bad_request("name_required", "Enter the patient's name or 16-digit ID")
    clash = db.scalar(
        select(Appointment.id).where(
            Appointment.doctor_id == doctor.id,
            Appointment.starts_at == starts_at,
            Appointment.status.in_((*OPEN_STATUSES, "completed", "no_show")),
        )
    )
    if clash:
        raise conflict("slot_taken", "Another appointment already starts at that time")
    a = Appointment(
        doctor_id=doctor.id,
        patient_id=patient.id if patient else None,
        guest_name=None if patient else (name or "").strip()[:120],
        starts_at=starts_at,
        ends_at=starts_at + timedelta(minutes=s.slot_min),
        mode=mode,
        status="completed" if starts_at <= now else "accepted",
        source="walk_in",
        decided_at=now,
    )
    try:
        with db.begin_nested():
            db.add(a)
            db.flush()
    except IntegrityError as exc:
        raise conflict("slot_taken", "Another appointment already starts at that time") from exc
    return a


def get_for(db: Session, user: User, appointment_id: uuid.UUID) -> Appointment:
    a = db.get(Appointment, appointment_id)
    if a is None or user.id not in (a.doctor_id, a.patient_id):
        raise not_found("Appointment")
    return a


def decide(db: Session, doctor: User, a: Appointment, action: str, note: str | None, link: str | None, now: datetime) -> None:
    """The doctor: accept / decline a request; cancel; mark completed or no-show after the start."""
    if a.doctor_id != doctor.id:
        raise not_found("Appointment")
    expire_stale(db, now, doctor_id=doctor.id)
    db.refresh(a)
    s = schedule(db, doctor)
    note = (note or "").strip()[:300] or None
    patient = db.get(User, a.patient_id) if a.patient_id else None
    when = describe(a, s.timezone)
    if action in ("accept", "decline"):
        if a.status != "requested":
            raise conflict("not_requested", f"This appointment is {label(a.status)}")
        a.status = "accepted" if action == "accept" else "declined"
        a.doctor_note, a.decided_at = note, now
        if action == "accept" and a.mode == "online":
            a.meeting_link = clean_link(link) or s.meeting_link
        if patient:
            if action == "accept":
                body = f"{doctor.full_name} accepted your {when}." + (" The video link is in Appointments." if a.mode == "online" else "")
            else:
                body = f"{doctor.full_name} declined your {when}." + (f" Note: {note}" if note else "")
            tell(db, patient, a, "Appointment accepted" if action == "accept" else "Appointment declined", body)
    elif action == "cancel":
        cancel(db, doctor, a, note, now)
    elif action in ("complete", "no_show"):
        if a.status not in ("accepted", "completed", "no_show"):
            raise conflict("not_accepted", f"This appointment is {label(a.status)}")
        if a.starts_at > now:
            raise conflict("not_started", "You can mark it after it starts")
        a.status = "completed" if action == "complete" else "no_show"
    else:
        raise bad_request("bad_action", "Unknown action")


def cancel(db: Session, user: User, a: Appointment, note: str | None, now: datetime) -> None:
    if a.status not in OPEN_STATUSES:
        raise conflict("not_open", f"This appointment is {label(a.status)}")
    if a.starts_at <= now:
        raise conflict("started", "It has already started")
    a.status, a.cancelled_by, a.decided_at = "cancelled", user.id, now
    if user.id == a.doctor_id and (note or "").strip():
        a.doctor_note = note.strip()[:300]
    s = db.get(DoctorSchedule, a.doctor_id)
    when = describe(a, s.timezone if s else "Asia/Kolkata")
    other = db.get(User, a.patient_id if user.id == a.doctor_id else a.doctor_id) if a.patient_id else None
    if other:
        tell(db, other, a, "Appointment cancelled", f"{user.full_name or 'The patient'} cancelled the {when}.")


def label(status: str) -> str:
    return {"no_show": "marked no-show"}.get(status, status)


def describe(a: Appointment, tz: str) -> str:
    """'online appointment on Tue 4 Nov, 10:30 IST' in the doctor's time zone."""
    t = a.starts_at.astimezone(zone(tz))
    kind = "online appointment" if a.mode == "online" else "in-person visit"
    return f"{kind} on {t.strftime('%a')} {t.day} {t.strftime('%b, %H:%M')} {t.tzname()}"


def tell(db: Session, user: User, a: Appointment, title: str, body: str) -> None:
    """In the app and by email (best effort). Never health values: the patient's note isn't included."""
    link = "/" if user.id == a.doctor_id else "/appointments"
    notifications.notify(
        db, user_id=user.id, kind="appointment", title=title, body=body, link=link, dedupe_key=f"appt:{a.id}:{title}"[:120]
    )
    if user.email and user.email_verified_at and user.status == "active":
        email.send_appointment_notice(user.email, user.full_name, title, body, link)


# ---------------------------------------------------------------- reminders


def send_reminders(db: Session, now: datetime) -> int:
    """Accepted appointments starting within REMIND_BEFORE: remind both sides once."""
    expire_stale(db, now)
    due = db.scalars(
        select(Appointment).where(
            Appointment.status == "accepted",
            Appointment.reminder_sent_at.is_(None),
            Appointment.starts_at > now,
            Appointment.starts_at <= now + REMIND_BEFORE,
        )
    ).all()
    for a in due:
        a.reminder_sent_at = now
        doctor = db.get(User, a.doctor_id)
        patient = db.get(User, a.patient_id) if a.patient_id else None
        s = db.get(DoctorSchedule, a.doctor_id)
        when = describe(a, s.timezone if s else "Asia/Kolkata")
        if patient and patient.status == "active":
            tell(db, patient, a, "Appointment reminder", f"Your {when} with {doctor.full_name if doctor else 'your doctor'} starts soon.")
            if patient.phone and patient.phone_verified_at:
                t = a.starts_at.astimezone(zone(s.timezone if s else "Asia/Kolkata"))
                sms.send_appointment_reminder(
                    patient.phone, doctor.full_name if doctor else "your doctor", t.strftime("%d %b %H:%M ") + (t.tzname() or "")
                )
        if doctor and doctor.status == "active":
            who = patient.full_name if patient else (a.guest_name or "a patient")
            tell(db, doctor, a, "Appointment reminder", f"Your {when} with {who} starts soon.")
    return len(due)


# ---------------------------------------------------------------- statistics


def stats(db: Session, doctor: User, now: datetime) -> dict:
    expire_stale(db, now, doctor_id=doctor.id)
    tz = zone(schedule(db, doctor).timezone)
    rows = db.execute(
        select(Appointment.status, Appointment.mode, Appointment.starts_at, Appointment.patient_id, Appointment.guest_name).where(
            Appointment.doctor_id == doctor.id
        )
    ).all()
    today = datetime.combine(now.astimezone(tz).date(), time(0), tzinfo=tz)
    ranges = {"today": today, "7d": now - timedelta(days=7), "30d": now - timedelta(days=30), "all": None}
    out: dict = {"ranges": {}}
    for key, since in ranges.items():
        past = [r for r in rows if r.starts_at <= now and (since is None or r.starts_at >= since)]
        done = [r for r in past if r.status == "completed"]
        out["ranges"][key] = {
            "served": len(done),
            "served_online": sum(r.mode == "online" for r in done),
            "served_in_person": sum(r.mode == "in_person" for r in done),
            "patients": len({r.patient_id or r.guest_name for r in done}),
            "no_show": sum(r.status == "no_show" for r in past),
            "cancelled": sum(r.status == "cancelled" for r in past),
            "declined": sum(r.status == "declined" for r in past),
            "expired": sum(r.status == "expired" for r in past),
            "to_mark": sum(r.status == "accepted" for r in past),  # visits the doctor hasn't marked yet
        }
    future = [r for r in rows if r.starts_at > now]
    upcoming = [r for r in future if r.status == "accepted"]
    end_today = today + timedelta(days=1)
    out.update(
        pending=sum(r.status == "requested" for r in future),
        upcoming=len(upcoming),
        upcoming_online=sum(r.mode == "online" for r in upcoming),
        upcoming_in_person=sum(r.mode == "in_person" for r in upcoming),
        upcoming_today=sum(r.starts_at < end_today for r in upcoming),
        next_at=min((r.starts_at for r in upcoming), default=None),
    )
    return out


def visible_appointments(
    db: Session,
    user: User,
    start: datetime | None,
    end: datetime | None,
    statuses: list[str] | None,
    other_id: uuid.UUID | None = None,
) -> list[Appointment]:
    q = select(Appointment).where(or_(Appointment.doctor_id == user.id, Appointment.patient_id == user.id))
    if other_id:  # only those with this patient / doctor
        q = q.where(or_(Appointment.doctor_id == other_id, Appointment.patient_id == other_id))
    if start:
        q = q.where(Appointment.ends_at > start)
    if end:
        q = q.where(Appointment.starts_at < end)
    if statuses:
        q = q.where(Appointment.status.in_(statuses))
    return list(db.scalars(q.order_by(Appointment.starts_at).limit(500)))


def next_with(db: Session, doctor_id: uuid.UUID, patient_id: uuid.UUID, now: datetime) -> Appointment | None:
    """The next requested or accepted appointment of this patient with this doctor."""
    return db.scalar(
        select(Appointment)
        .where(
            Appointment.doctor_id == doctor_id,
            Appointment.patient_id == patient_id,
            Appointment.status.in_(OPEN_STATUSES),
            Appointment.ends_at > now,
        )
        .order_by(Appointment.starts_at)
        .limit(1)
    )
