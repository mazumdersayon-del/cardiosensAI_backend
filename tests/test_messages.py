import uuid

from app.core.db import SessionLocal
from app.models import AuditEvent, Message, Notification, User
from app.services.erasure import erase_account
from tests.conftest import auth
from tests.test_accounts_links import ready_professional, signup
from tests.test_appointments import _book, _calendar, _doctor, _link


def _send(client, who, party, body):
    return client.post(f"/v1/conversations/{party}/messages", json={"body": body}, headers=auth(who))


def test_patient_and_linked_doctor_message_each_other(client):
    d = ready_professional(client, "doctor", "dr.msg@example.com", "Dr. Message")
    p = signup(client, "patient", "pat.msg@example.com", "Pat Message")
    stranger = signup(client, "patient", "pat.stranger@example.com", "Pat Stranger")
    did, pid = d["user"]["uniqueId"], p["user"]["id"]
    # not linked: nobody can write or even see a conversation
    assert _send(client, p, did, "Hello").status_code == 404
    link = client.post("/v1/links", json={"targetId": did}, headers=auth(p)).json()
    client.post(f"/v1/links/{link['id']}/approve", headers=auth(d))
    # the patient sees the linked doctor before any message
    convs = client.get("/v1/conversations", headers=auth(p)).json()
    assert [c["partner"]["uniqueId"] for c in convs] == [did] and convs[0]["canSend"] and convs[0]["unread"] == 0
    assert _send(client, p, did, "   ").json()["error"]["code"] == "empty"
    r = _send(client, p, did, "My palpitations are back since last night")
    assert r.status_code == 201 and r.json()["mine"]
    _send(client, p, did, "Should I come in?")
    # one notice for the unread streak, without the text
    db = SessionLocal()
    notes = db.query(Notification).filter(Notification.title == "New message").all()
    assert len(notes) == 1 and "palpitations" not in notes[0].body and notes[0].link == f"/patients/{pid}#messages"
    db.close()
    assert client.get("/v1/conversations/unread", headers=auth(d)).json()["unread"] == 2
    lp = client.get("/v1/linked-patients", headers=auth(d)).json()
    assert lp[0]["unreadMessages"] == 2 and lp[0]["email"] == "pat.msg@example.com"
    # the doctor reads (logged) and answers; the patient's messages are now read
    t = client.get(f"/v1/conversations/{pid}", headers=auth(d)).json()
    assert [m["body"] for m in t["messages"]] == ["My palpitations are back since last night", "Should I come in?"]
    assert not t["messages"][0]["mine"] and t["canSend"]
    assert client.get("/v1/conversations/unread", headers=auth(d)).json()["unread"] == 0
    _send(client, d, pid, "Yes, book a visit for tomorrow")
    t = client.get(f"/v1/conversations/{did}", headers=auth(p)).json()
    assert t["messages"][0]["readAt"] is not None and t["messages"][-1]["body"].startswith("Yes")
    db = SessionLocal()
    assert db.query(AuditEvent).filter(AuditEvent.action == "message.view").count() == 1
    assert db.query(AuditEvent).filter(AuditEvent.action == "message.send").count() == 3
    db.close()
    # a stranger can't read it, by user id or 16-digit ID
    assert client.get(f"/v1/conversations/{did}", headers=auth(stranger)).status_code == 404
    assert client.get(f"/v1/conversations/{pid}", headers=auth(stranger)).status_code == 404
    # after the link ends: history readable, no new messages
    client.post(f"/v1/links/{link['id']}/end", headers=auth(p))
    t = client.get(f"/v1/conversations/{did}", headers=auth(p)).json()
    assert len(t["messages"]) == 3 and not t["canSend"]
    assert _send(client, p, did, "Hello again").status_code == 403
    # erasing either account erases the conversation
    db = SessionLocal()
    erase_account(db, db.get(User, uuid.UUID(pid)), actor=None, action="account.erase")
    db.commit()
    assert db.query(Message).count() == 0
    db.close()


def test_patient_list_shows_contact_and_next_appointment(client):
    d = _doctor(client, addr="dr.list@example.com")
    p = signup(client, "patient", "pat.list@example.com", "Pat List")
    _link(client, p, d)
    lp = client.get("/v1/linked-patients", headers=auth(d)).json()
    assert lp[0]["nextAppointment"] is None and lp[0]["email"] == "pat.list@example.com"
    slot = _calendar(client, p, d)["slots"][2]
    a = _book(client, p, d, slot).json()
    nxt = client.get("/v1/linked-patients", headers=auth(d)).json()[0]["nextAppointment"]
    assert nxt["id"] == a["id"] and nxt["status"] == "requested" and nxt["mode"] == "online"
    mine = client.get("/v1/appointments", params={"scope": "all", "with": p["user"]["id"]}, headers=auth(d)).json()
    assert [x["id"] for x in mine] == [a["id"]]
    other = signup(client, "patient", "pat.other2@example.com", "Pat Other")
    assert client.get("/v1/appointments", params={"scope": "all", "with": other["user"]["id"]}, headers=auth(d)).json() == []
