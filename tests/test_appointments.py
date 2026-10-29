"""Appointments: settings, slots, booking rules, the doctor's decisions, offline mode, reminders, statistics, erasure."""

import uuid
from datetime import UTC, datetime, timedelta

from app.core.db import SessionLocal, utcnow
from app.models import Appointment, Notification
from app.services import appointments as appts
from app.services import email
from tests.conftest import auth
from tests.test_accounts_links import ready_professional, signup

ALL_DAY = [{"day": d, "start": "00:00", "end": "23:30"} for d in range(7)]


def _doctor(client, addr="dr.cal@example.com", **settings):
    d = ready_professional(client, "doctor", addr, "Dr. Calendar")
    body = {"enabled": True, "timezone": "UTC", "slotMin": 30, "weeklyHours": ALL_DAY, "meetingLink": "https://meet.example.com/dr"}
    r = client.put("/v1/me/calendar", json={**body, **settings}, headers=auth(d))
    assert r.status_code == 200, r.text
    return d


def _link(client, patient, doctor):
    link = client.post("/v1/links", json={"targetId": doctor["user"]["uniqueId"]}, headers=auth(patient)).json()
    assert client.post(f"/v1/links/{link['id']}/approve", headers=auth(doctor)).json()["status"] == "active"


def _calendar(client, patient, doctor):
    r = client.get(f"/v1/doctors/{doctor['user']['uniqueId']}/calendar", headers=auth(patient))
    assert r.status_code == 200, r.text
    return r.json()


def _book(client, patient, doctor, slot, mode="online", **kw):
    return client.post(
        "/v1/appointments",
        json={"doctorId": doctor["user"]["uniqueId"], "startsAt": slot["startsAt"], "mode": mode, **kw},
        headers=auth(patient),
    )


def _decide(client, who, appt_id, action, **kw):
    return client.post(f"/v1/appointments/{appt_id}/decision", json={"action": action, **kw}, headers=auth(who))


def _move(appt_id: str, start: datetime) -> None:
    """Put an appointment in the past/future (tests can't wait for real time)."""
    db = SessionLocal()
    a = db.get(Appointment, uuid.UUID(appt_id))
    a.starts_at, a.ends_at = start, start + timedelta(minutes=30)
    db.commit()
    db.close()


def test_settings_validation_and_bookable_hint(client):
    d = ready_professional(client, "doctor", "dr.set@example.com", "Dr. Settings")
    s = client.get("/v1/me/calendar", headers=auth(d)).json()
    assert s["enabled"] is False and s["bookable"] is False and "weekly hours" in s["hint"]
    bad = [
        ({"enabled": True}, "no_hours"),
        ({"slotMin": 7}, "bad_slot_length"),
        ({"windowDays": 90}, "bad_window"),
        ({"timezone": "Mars/Base"}, "bad_timezone"),
        ({"weeklyHours": [{"day": 0, "start": "10:00", "end": "09:00"}]}, "bad_hours"),
        ({"weeklyHours": [{"day": 0, "start": "10:00", "end": "12:00"}, {"day": 0, "start": "11:00", "end": "13:00"}]}, "bad_hours"),
        ({"meetingLink": "http://insecure.example.com"}, "bad_meeting_link"),
        ({"online": False, "inPerson": False}, "no_appointment_type"),
    ]
    for body, code in bad:
        r = client.put("/v1/me/calendar", json=body, headers=auth(d))
        assert r.status_code == 400 and r.json()["error"]["code"] == code, (body, r.text)
    r = client.put("/v1/me/calendar", json={"enabled": True, "weeklyHours": ALL_DAY[:1]}, headers=auth(d))
    assert r.json()["bookable"] is True and r.json()["hint"] is None
    patient = signup(client, "patient", "pat.set@example.com", "Pat Settings")
    assert client.get("/v1/me/calendar", headers=auth(patient)).status_code == 403


def test_who_can_book_linked_or_listed_only(client):
    d = _doctor(client)
    stranger = signup(client, "patient", "stranger@example.com", "Stranger")
    assert client.get(f"/v1/doctors/{d['user']['uniqueId']}/calendar", headers=auth(stranger)).status_code == 404  # not linked, not listed
    assert client.get("/v1/appointments/doctors", headers=auth(stranger)).json() == []
    client.put("/v1/me/doctor-listing", json={"listed": True}, headers=auth(d))
    cal = _calendar(client, stranger, d)
    assert cal["bookable"] and cal["slots"] and cal["limitReached"] is False
    first = datetime.fromisoformat(cal["slots"][0]["startsAt"])
    assert first >= utcnow() + appts.NOTICE and len(cal["slots"]) <= 14 * 48  # 1 h notice, 14-day window
    linked = signup(client, "patient", "linked@example.com", "Linked")
    client.put("/v1/me/doctor-listing", json={"listed": False}, headers=auth(d))
    _link(client, linked, d)
    assert [x["uniqueId"] for x in client.get("/v1/appointments/doctors", headers=auth(linked)).json()] == [d["user"]["uniqueId"]]
    assert _calendar(client, linked, d)["bookable"]
    assert client.get(f"/v1/doctors/{d['user']['uniqueId']}/calendar", headers=auth(stranger)).status_code == 404
    client.put("/v1/me/calendar", json={"enabled": False}, headers=auth(d))
    cal = _calendar(client, linked, d)
    assert not cal["bookable"] and cal["slots"] == []


def test_book_accept_reminder_complete_and_notifications(client):
    d = _doctor(client)
    p = signup(client, "patient", "pat.book@example.com", "Pat Book")
    _link(client, p, d)
    slots = _calendar(client, p, d)["slots"]
    r = _book(client, p, d, slots[3], reason="Palpitations at night")
    assert r.status_code == 201, r.text
    a = r.json()
    assert a["status"] == "requested" and a["meetingLink"] is None and a["patient"]["fullName"] == "Pat Book"
    assert any("New appointment request" in m.subject for m in email.outbox)
    # the slot is taken now, for everyone
    assert slots[3]["startsAt"] not in [x["startsAt"] for x in _calendar(client, p, d)["slots"]]
    other = signup(client, "patient", "pat.other@example.com", "Pat Other")
    _link(client, other, d)
    assert _book(client, other, d, slots[3]).json()["error"]["code"] == "slot_taken"
    # the doctor sees the request with the patient's note; the patient can't decide
    reqs = client.get("/v1/appointments", params={"status": "requested"}, headers=auth(d)).json()
    assert [x["id"] for x in reqs] == [a["id"]] and reqs[0]["reason"] == "Palpitations at night" and reqs[0]["patientLinked"]
    assert _decide(client, p, a["id"], "accept").status_code == 403
    acc = _decide(client, d, a["id"], "accept").json()
    assert acc["status"] == "accepted" and acc["meetingLink"] == "https://meet.example.com/dr"
    assert _decide(client, d, a["id"], "accept").json()["error"]["code"] == "not_requested"
    assert _decide(client, d, a["id"], "complete").json()["error"]["code"] == "not_started"
    mine = client.get("/v1/appointments", headers=auth(p)).json()
    assert mine[0]["status"] == "accepted" and mine[0]["meetingLink"] and mine[0]["doctor"]["fullName"] == "Dr. Calendar"
    # reminder once, within the hour
    soon = utcnow() + timedelta(minutes=30)
    _move(a["id"], soon)
    db = SessionLocal()
    assert appts.send_reminders(db, utcnow()) == 1
    assert appts.send_reminders(db, utcnow()) == 0
    db.commit()
    titles = {n.title for n in db.query(Notification).all()}
    db.close()
    assert {"New appointment request", "Appointment accepted", "Appointment reminder"} <= titles
    # after the start: completed counts as served
    _move(a["id"], utcnow() - timedelta(hours=1))
    assert _decide(client, d, a["id"], "complete").json()["status"] == "completed"
    stats = client.get("/v1/me/appointment-stats", headers=auth(d)).json()
    assert stats["ranges"]["all"]["served"] == 1 and stats["ranges"]["all"]["servedOnline"] == 1 and stats["ranges"]["7d"]["patients"] == 1
    assert client.get("/v1/me/appointment-stats", headers=auth(p)).status_code == 403


def test_decline_cancel_expire_and_limits(client):
    d = _doctor(client)
    p = signup(client, "patient", "pat.lim@example.com", "Pat Limit")
    _link(client, p, d)
    slots = _calendar(client, p, d)["slots"]
    one = _book(client, p, d, slots[2]).json()
    two = _book(client, p, d, slots[4], mode="in_person").json()
    assert _book(client, p, d, slots[6]).json()["error"]["code"] == "too_many"
    assert _calendar(client, p, d)["limitReached"] is True
    assert _decide(client, d, one["id"], "decline", note="Please see the clinic instead").json()["status"] == "declined"
    assert _decide(client, p, two["id"], "cancel").json()["status"] == "cancelled"
    assert any("cancelled" in m.body for m in email.outbox)
    # unanswered request expires at its start
    three = _book(client, p, d, slots[6]).json()
    _move(three["id"], utcnow() - timedelta(minutes=1))
    past = client.get("/v1/appointments", params={"scope": "past"}, headers=auth(d)).json()
    assert {x["id"]: x["status"] for x in past}[three["id"]] == "expired"
    # rules on the time itself
    too_soon = {"startsAt": (utcnow() + timedelta(minutes=10)).isoformat()}
    assert _book(client, p, d, too_soon).json()["error"]["code"] == "too_soon"
    off_grid = {"startsAt": (datetime.fromisoformat(slots[8]["startsAt"]) + timedelta(minutes=7)).isoformat()}
    assert _book(client, p, d, off_grid).json()["error"]["code"] == "no_such_slot"
    far = {"startsAt": (utcnow() + timedelta(days=20)).replace(minute=0, second=0, microsecond=0).isoformat()}
    assert _book(client, p, d, far).json()["error"]["code"] == "too_far"


def test_offline_mode_blocks_online_only(client):
    d = _doctor(client)
    p = signup(client, "patient", "pat.off@example.com", "Pat Offline")
    _link(client, p, d)
    st = client.post("/v1/me/calendar/offline", json={}, headers=auth(d)).json()
    assert st["offline"] is True
    until = datetime.fromisoformat(st["until"])
    assert timedelta(hours=1, minutes=59) < until - utcnow() <= timedelta(hours=2)  # default 2 h
    cal = _calendar(client, p, d)
    assert cal["offlineUntil"]
    inside = [x for x in cal["slots"] if datetime.fromisoformat(x["startsAt"]) < until]
    assert inside and all(not x["online"] and x["inPerson"] for x in inside)
    assert _book(client, p, d, inside[0]).json()["error"]["code"] == "offline"
    assert _book(client, p, d, inside[0], mode="in_person").status_code == 201
    st = client.delete("/v1/me/calendar/offline", headers=auth(d)).json()
    assert st["offline"] is False
    assert any(x["online"] for x in _calendar(client, p, d)["slots"][:4])
    # scheduled period (a holiday): online slots inside it go
    start = (utcnow() + timedelta(days=2)).replace(hour=0, minute=0, second=0, microsecond=0)
    r = client.post(
        "/v1/me/calendar/offline-periods",
        json={"startsAt": start.isoformat(), "endsAt": (start + timedelta(days=1)).isoformat(), "reason": "Conference"},
        headers=auth(d),
    )
    assert r.status_code == 201, r.text
    day = [x for x in _calendar(client, p, d)["slots"] if start <= datetime.fromisoformat(x["startsAt"]) < start + timedelta(days=1)]
    assert day and not any(x["online"] for x in day)
    assert client.get("/v1/me/calendar", headers=auth(d)).json()["offline"]["periods"][0]["reason"] == "Conference"
    assert client.delete(f"/v1/me/calendar/offline-periods/{r.json()['id']}", headers=auth(d)).status_code == 204
    bad = {"until": (utcnow() + timedelta(days=90)).isoformat()}
    assert client.post("/v1/me/calendar/offline", json=bad, headers=auth(d)).json()["error"]["code"] == "bad_offline_end"


def test_week_view_walk_in_and_stats(client):
    d = _doctor(client, slotMin=30)
    p = signup(client, "patient", "pat.week@example.com", "Pat Week")
    _link(client, p, d)
    today = utcnow().date()
    wk = client.get("/v1/me/calendar/week", params={"start": today.isoformat(), "days": 7}, headers=auth(d)).json()
    assert wk["timezone"] == "UTC" and len(wk["slots"]) == 7 * 47
    past = (utcnow() - timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
    r = client.post("/v1/me/appointments", json={"startsAt": past.isoformat(), "name": "Walk-in visitor"}, headers=auth(d))
    assert r.status_code == 201 and r.json()["status"] == "completed" and r.json()["patient"]["fullName"] == "Walk-in visitor"
    r = client.post("/v1/me/appointments", json={"startsAt": past.isoformat(), "patientId": p["user"]["uniqueId"]}, headers=auth(d))
    assert r.status_code == 409 and r.json()["error"]["code"] == "slot_taken"
    r = client.post(
        "/v1/me/appointments",
        json={"startsAt": (past + timedelta(hours=1)).isoformat(), "patientId": p["user"]["uniqueId"], "mode": "online"},
        headers=auth(d),
    )
    assert r.status_code == 201
    _decide(client, d, r.json()["id"], "no_show")
    stranger = signup(client, "patient", "pat.str@example.com", "Stranger")
    bad = client.post(
        "/v1/me/appointments", json={"startsAt": past.isoformat(), "patientId": stranger["user"]["uniqueId"]}, headers=auth(d)
    )
    assert bad.json()["error"]["code"] == "not_linked"
    s = client.get("/v1/me/appointment-stats", headers=auth(d)).json()
    assert s["ranges"]["all"]["served"] == 1 and s["ranges"]["all"]["servedInPerson"] == 1 and s["ranges"]["all"]["noShow"] == 1
    assert s["ranges"]["today"]["served"] == 0 and s["pending"] == 0


def test_erasure_removes_appointments(client):
    d = _doctor(client)
    p = signup(client, "patient", "pat.erase@example.com", "Pat Erase")
    _link(client, p, d)
    a = _book(client, p, d, _calendar(client, p, d)["slots"][1]).json()
    from app.models import User
    from app.services.erasure import erase_account

    db = SessionLocal()
    erase_account(db, db.get(User, uuid.UUID(p["user"]["id"])), actor=None, action="account.erase")
    db.commit()
    assert db.get(Appointment, uuid.UUID(a["id"])) is None
    db.close()
    assert client.get("/v1/appointments", headers=auth(d)).json() == []


def test_slot_times_follow_the_doctor_time_zone():
    from app.models import DoctorSchedule

    s = DoctorSchedule(
        user_id=uuid.uuid4(), timezone="Asia/Kolkata", slot_min=60, weekly_hours=[{"day": 0, "start": "10:00", "end": "12:00"}]
    )
    monday = datetime(2026, 11, 2, tzinfo=UTC)  # a Monday
    got = appts.slot_times(s, monday, monday + timedelta(days=1))
    assert [a.isoformat() for a, _ in got] == ["2026-11-02T04:30:00+00:00", "2026-11-02T05:30:00+00:00"]


def test_booking_can_ask_to_link_and_accepting_approves_it(client):
    d = _doctor(client)
    client.put("/v1/me/doctor-listing", json={"listed": True}, headers=auth(d))
    p = signup(client, "patient", "pat.linkbook@example.com", "Pat Linkbook")
    cal = _calendar(client, p, d)
    assert cal["linkStatus"] is None
    a = _book(client, p, d, cal["slots"][0], allowLink=True).json()
    assert _calendar(client, p, d)["linkStatus"] == "pending"
    reqs = client.get("/v1/appointments", params={"status": "requested"}, headers=auth(d)).json()
    assert reqs[0]["linkPending"] and not reqs[0]["patientLinked"] and reqs[0]["patientRecordId"] is None
    assert [x["status"] for x in client.get("/v1/links", headers=auth(p)).json()] == ["pending"]
    acc = _decide(client, d, a["id"], "accept").json()
    assert acc["patientLinked"] and acc["patientRecordId"] and not acc["linkPending"]
    assert [x["status"] for x in client.get("/v1/links", headers=auth(p)).json()] == ["active"]
    assert _calendar(client, p, d)["linkStatus"] == "active"
    month = client.get("/v1/me/calendar/week", params={"start": utcnow().date().isoformat(), "days": 42, "slots": "false"}, headers=auth(d))
    assert month.status_code == 200 and month.json()["slots"] == [] and len(month.json()["appointments"]) == 1
    assert client.get(f"/v1/patients/{acc['patientRecordId']}/documents", headers=auth(d)).status_code == 200
    # a second booking doesn't ask again; without the tick nothing is asked
    q = signup(client, "patient", "pat.nolink@example.com", "Pat Nolink")
    _book(client, q, d, _calendar(client, q, d)["slots"][0])
    assert client.get("/v1/links", headers=auth(q)).json() == []
