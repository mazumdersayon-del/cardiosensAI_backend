"""Admin portal: doctor/clinic verification with licence documents, account deletion with a grace period, statistics."""

import csv
import io
import uuid
from datetime import timedelta

from app.core.db import SessionLocal, utcnow
from app.models import Measurement, User
from app.services import email
from app.services.erasure import purge_due
from tests.conftest import PDF, auth, enable_mfa, make_admin, upload_licence
from tests.test_accounts_links import PW, _patient_with_reading, ready_professional, signup


def _path(url: str) -> str:
    return "/v1/" + url.split("/v1/", 1)[1]


def _link(client, p: dict, d: dict) -> None:
    """Doctor/clinic d asks, patient p approves."""
    client.post("/v1/links", json={"targetId": p["user"]["uniqueId"]}, headers=auth(d))
    incoming = client.get("/v1/links", headers=auth(p)).json()
    assert client.post(f"/v1/links/{incoming[0]['id']}/approve", headers=auth(p)).json()["status"] == "active"


# ---------------------------------------------------------------- verification


def test_licence_upload_rules_and_who_can_open_it(client):
    d = signup(client, "doctor", "d@example.com", "Dr. Sen")
    p = signup(client, "patient", "p@example.com")
    st = client.get("/v1/me/verification", headers=auth(d)).json()
    assert st == {"status": "pending", "reviewNote": None, "files": [], "maxFiles": 3, "canResubmit": False}
    assert client.get("/v1/me/verification", headers=auth(p)).status_code == 400  # patients aren't verified

    bad = client.post("/v1/me/verification/files", files=[("files", ("x.pdf", b"MZ\x90\x00", "application/pdf"))], headers=auth(d))
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "unsupported_file"  # checked by content, not by name
    out = client.post(
        "/v1/me/verification/files",
        files=[("files", ("cert.pdf", PDF, "application/pdf")), ("files", ("id.jpg", b"\xff\xd8\xff\xe0jpeg", "image/jpeg"))],
        headers=auth(d),
    ).json()
    assert [f["contentType"] for f in out["files"]] == ["application/pdf", "image/jpeg"]
    upload_licence(client, d)
    over = client.post("/v1/me/verification/files", files=[("files", ("4.pdf", PDF, "application/pdf"))], headers=auth(d))
    assert over.status_code == 400 and over.json()["error"]["code"] == "file_count"

    fid = out["files"][0]["id"]
    url = client.post(f"/v1/me/verification/files/{fid}/link", headers=auth(d)).json()["url"]
    r = client.get(_path(url))
    assert r.status_code == 200 and r.content == PDF and r.headers["cache-control"] == "private, no-store"
    assert client.post(f"/v1/me/verification/files/{fid}/link", headers=auth(p)).status_code == 404  # not theirs

    adm = make_admin(client)
    url = client.post(f"/v1/admin/verification-files/{fid}/link", headers=auth(adm)).json()["url"]
    assert client.get(_path(url)).content == PDF
    events = client.get("/v1/admin/audit-events", params={"action": "admin.licence_view"}, headers=auth(adm)).json()
    assert len(events) == 1
    assert client.post(f"/v1/admin/verification-files/{fid}/link", headers=auth(d)).status_code == 403

    left = client.delete(f"/v1/me/verification/files/{fid}", headers=auth(d)).json()
    assert len(left["files"]) == 2


def test_reject_with_reason_resubmit_verify_then_revoke(client):
    p = _patient_with_reading(client, "p@example.com")
    d = signup(client, "doctor", "d@example.com", "Dr. Sen")
    enable_mfa(client, d)
    adm = make_admin(client)
    path = f"/v1/admin/professionals/{d['user']['id']}/verify"

    queue = client.get("/v1/admin/professionals", headers=auth(adm)).json()
    assert queue[0]["files"] == [] and queue[0]["emailMasked"].startswith("d•")
    assert client.post(path, json={"decision": "verified"}, headers=auth(adm)).json()["error"]["code"] == "licence_required"
    no_reason = client.post(path, json={"decision": "rejected"}, headers=auth(adm))
    assert no_reason.status_code == 422  # a reason is required (it is emailed)

    r = client.post(path, json={"decision": "rejected", "note": "Registration number not found in the NMC register"}, headers=auth(adm))
    assert r.status_code == 204
    assert email.outbox[-1].to == "d@example.com" and "not found in the NMC register" in email.outbox[-1].body
    st = client.get("/v1/auth/status", headers=auth(d)).json()
    assert st["verificationStatus"] == "rejected" and st["verificationNote"].startswith("Registration number")
    assert "upload_licence" in st["missing"]
    # A rejected account resubmits only with a document.
    assert client.post("/v1/me/verification/resubmit", headers=auth(d)).json()["error"]["code"] == "licence_required"
    upload_licence(client, d)
    assert client.get("/v1/me/verification", headers=auth(d)).json()["canResubmit"] is True
    again = client.post("/v1/me/verification/resubmit", headers=auth(d)).json()
    assert again["status"] == "pending" and again["reviewNote"] is None
    assert client.post(path, json={"decision": "revoked", "note": "x" * 5}, headers=auth(adm)).status_code == 409  # not verified yet

    assert client.post(path, json={"decision": "verified"}, headers=auth(adm)).status_code == 204
    assert "is verified" in email.outbox[-1].subject
    assert client.get("/v1/auth/status", headers=auth(d)).json()["canViewPatients"] is True
    _link(client, p, d)
    assert client.get(f"/v1/patients/{p['user']['id']}/measurements", headers=auth(d)).status_code == 200
    # Verified accounts keep their documents.
    fid = client.get("/v1/me/verification", headers=auth(d)).json()["files"][0]["id"]
    assert client.delete(f"/v1/me/verification/files/{fid}", headers=auth(d)).status_code == 409

    r = client.post(path, json={"decision": "revoked", "note": "Licence suspended by the council"}, headers=auth(adm))
    assert r.status_code == 204 and "revoked" in email.outbox[-1].subject
    assert client.get(f"/v1/patients/{p['user']['id']}/measurements", headers=auth(d)).status_code == 404  # access stops at once
    assert (
        client.get("/v1/admin/professionals", params={"status": "revoked"}, headers=auth(adm)).json()[0]["reviewNote"].startswith("Licence")
    )
    assert client.post("/v1/me/verification/resubmit", headers=auth(d)).json()["status"] == "pending"


def test_clinic_verification_uses_the_clinic_logins_documents(client):
    c = signup(client, "clinic", "desk@clinic.in", "Ritu Das")
    adm = make_admin(client)
    org_id = c["user"]["organizationId"]
    path = f"/v1/admin/organizations/{org_id}/verify"
    assert client.post(path, json={"decision": "verified"}, headers=auth(adm)).status_code == 409
    upload_licence(client, c, "ce-certificate.pdf")
    row = client.get("/v1/admin/organizations", headers=auth(adm)).json()[0]
    assert row["files"][0]["filename"] == "ce-certificate.pdf" and row["ownerEmailMasked"].startswith("de")
    assert client.post(path, json={"decision": "verified"}, headers=auth(adm)).status_code == 204
    assert client.get("/v1/me/organization", headers=auth(c)).json()["verificationStatus"] == "verified"
    assert email.outbox[-1].to == "desk@clinic.in" and "clinic" in email.outbox[-1].body


# ---------------------------------------------------------------- deletion


def test_admin_deletes_with_grace_period_restore_and_erase(client):
    p = _patient_with_reading(client, "p@example.com")
    d = ready_professional(client, "doctor", "d@example.com", "Dr. Sen")
    _link(client, p, d)
    adm = make_admin(client)
    uid = p["user"]["id"]

    assert client.post(f"/v1/admin/users/{uid}/delete", json={"reason": "x"}, headers=auth(adm)).status_code == 422  # reason needed
    r = client.post(f"/v1/admin/users/{uid}/delete", json={"reason": "Duplicate account, user asked by phone"}, headers=auth(adm))
    assert r.status_code == 200 and r.json()["status"] == "deleting"
    assert email.outbox[-1].to == "p@example.com" and "erased permanently" in email.outbox[-1].body
    # Blocked at once: no sign-in, old sessions dead, invisible to the doctor.
    login = client.post("/v1/auth/login", json={"email": "p@example.com", "password": PW})
    assert login.status_code == 403 and login.json()["error"]["code"] == "account_deleting"
    assert client.get("/v1/me", headers=auth(p)).json()["error"]["code"] == "account_deleting"
    assert client.get(f"/v1/patients/{uid}/measurements", headers=auth(d)).status_code == 404
    assert client.get("/v1/linked-patients", headers=auth(d)).json() == []
    assert client.patch(f"/v1/admin/users/{uid}/status", json={"status": "active", "reason": "oops"}, headers=auth(adm)).status_code == 409

    # Restore: everything comes back (links and data were kept during the grace period).
    assert client.post(f"/v1/admin/users/{uid}/restore", json={}, headers=auth(adm)).json()["status"] == "active"
    assert "restored" in email.outbox[-1].subject
    assert client.post("/v1/auth/login", json={"email": "p@example.com", "password": PW}).status_code == 200
    assert client.get(f"/v1/patients/{uid}/measurements", headers=auth(d)).status_code == 200

    # Delete again and erase now (typed ID must match).
    client.post(f"/v1/admin/users/{uid}/delete", json={"reason": "Asked again in writing"}, headers=auth(adm))
    wrong = client.post(f"/v1/admin/users/{uid}/erase", json={"confirmUniqueId": "1" * 16}, headers=auth(adm))
    assert wrong.status_code == 400 and wrong.json()["error"]["code"] == "confirm_mismatch"
    typed = " ".join(p["user"]["uniqueId"][i : i + 4] for i in range(0, 16, 4))
    assert client.post(f"/v1/admin/users/{uid}/erase", json={"confirmUniqueId": typed}, headers=auth(adm)).status_code == 204
    with SessionLocal() as db:
        u = db.get(User, uuid.UUID(uid))
        assert u.status == "deleted" and u.email is None and u.full_name is None
        assert db.query(Measurement).filter(Measurement.patient_id == u.id).count() == 0
    assert client.get("/v1/links", params={"include_closed": True}, headers=auth(d)).json()[0]["status"] == "ended"
    assert client.post(f"/v1/admin/users/{uid}/restore", json={}, headers=auth(adm)).status_code == 404


def test_purge_job_erases_only_accounts_past_their_grace_period(client):
    adm = make_admin(client)
    a = signup(client, "patient", "a@example.com")
    b = signup(client, "patient", "b@example.com")
    for t in (a, b):
        assert (
            client.post(f"/v1/admin/users/{t['user']['id']}/delete", json={"reason": "Test account"}, headers=auth(adm)).status_code == 200
        )
    with SessionLocal() as db:
        db.get(User, uuid.UUID(a["user"]["id"])).erase_after = utcnow() - timedelta(minutes=1)
        db.commit()
        n, _ = purge_due(db)
        db.commit()
        assert n == 1
        assert db.get(User, uuid.UUID(a["user"]["id"])).status == "deleted"
        assert db.get(User, uuid.UUID(b["user"]["id"])).status == "deleting"


def test_admins_and_self_cannot_be_deleted_from_portal(client):
    adm = make_admin(client)
    other = make_admin(client, "+919800000001")
    assert client.post(f"/v1/admin/users/{adm['user']['id']}/delete", json={"reason": "Myself"}, headers=auth(adm)).status_code == 400
    assert client.post(f"/v1/admin/users/{other['user']['id']}/delete", json={"reason": "Colleague"}, headers=auth(adm)).status_code == 403
    p = signup(client, "patient", "p@example.com")
    assert client.post(f"/v1/admin/users/{p['user']['id']}/delete", json={"reason": "Not staff"}, headers=auth(p)).status_code == 403


def test_erasing_a_clinic_ends_its_links_and_anonymises_it(client):
    p = _patient_with_reading(client, "p@example.com")
    c = ready_professional(client, "clinic", "desk@clinic.in", "Ritu Das")
    d = ready_professional(client, "doctor", "d@example.com", "Dr. Sen")
    _link(client, p, c)
    link = client.post("/v1/links", json={"targetId": d["user"]["uniqueId"]}, headers=auth(c)).json()
    client.post(f"/v1/links/{link['id']}/approve", headers=auth(d))
    adm = make_admin(client)
    cid = c["user"]["id"]
    client.post(f"/v1/admin/users/{cid}/delete", json={"reason": "Clinic closed"}, headers=auth(adm))
    client.post(f"/v1/admin/users/{cid}/erase", json={"confirmUniqueId": c["user"]["uniqueId"]}, headers=auth(adm))
    closed = {"include_closed": True}
    assert {x["status"] for x in client.get("/v1/links", params=closed, headers=auth(d)).json()} == {"ended"}
    assert {x["status"] for x in client.get("/v1/links", params=closed, headers=auth(p)).json()} == {"ended"}
    orgs = client.get("/v1/admin/organizations", params={"status": "all"}, headers=auth(adm)).json()
    assert orgs == []  # its login is erased, so it no longer appears in the queue


# ---------------------------------------------------------------- users list and statistics


def test_users_search_filters_pagination_and_csv(client):
    adm = make_admin(client)
    signup(client, "patient", "asha@example.com", "Asha Rao")
    d = signup(client, "doctor", "sen@example.com", "Dr. Sen")
    signup(client, "doctor", "iyer@example.com", "Dr. Iyer")

    page = client.get("/v1/admin/users", params={"role": "doctor", "limit": 1}, headers=auth(adm)).json()
    assert page["total"] == 2 and len(page["items"]) == 1 and page["items"][0]["verificationStatus"] == "pending"
    assert client.get("/v1/admin/users", params={"q": "ASHA@"}, headers=auth(adm)).json()["items"][0]["fullName"] == "Asha Rao"
    by_id = client.get("/v1/admin/users", params={"q": d["user"]["uniqueId"][:8]}, headers=auth(adm)).json()
    assert [u["fullName"] for u in by_id["items"]] == ["Dr. Sen"]
    pending = client.get("/v1/admin/users", params={"verification": "pending"}, headers=auth(adm)).json()
    assert {u["fullName"] for u in pending["items"]} == {"Dr. Sen", "Dr. Iyer"}

    r = client.get("/v1/admin/users.csv", params={"role": "doctor"}, headers=auth(adm))
    rows = list(csv.reader(io.StringIO(r.text)))
    assert r.headers["content-type"].startswith("text/csv") and rows[0][0] == "unique_id" and len(rows) == 3
    assert all("•" in row[5] for row in rows[1:])  # masked by default
    full = list(
        csv.reader(io.StringIO(client.get("/v1/admin/users.csv", params={"role": "doctor", "fullEmails": True}, headers=auth(adm)).text))
    )
    assert {row[5] for row in full[1:]} == {"sen@example.com", "iyer@example.com"}
    events = client.get("/v1/admin/audit-events", params={"action": "admin.users_export"}, headers=auth(adm)).json()
    assert len(events) == 2 and "full emails" in events[0]["detail"]
    assert client.get("/v1/admin/users.csv", headers=auth(d)).status_code == 403


def test_statistics_are_counts_only(client):
    adm = make_admin(client)
    p = _patient_with_reading(client, "p@example.com")
    d = ready_professional(client, "doctor", "d@example.com", "Dr. Sen")
    signup(client, "doctor", "new@example.com", "Dr. New")  # pending, no licence
    _link(client, p, d)

    s = client.get("/v1/admin/stats", params={"days": 7}, headers=auth(adm)).json()
    assert s["users"]["byRole"]["patient"]["active"] == 1 and s["users"]["byRole"]["doctor"]["active"] == 2
    assert s["verification"]["doctors"] == {"pending": 1, "verified": 1, "rejected": 0, "revoked": 0, "awaitingDocuments": 1}
    assert s["growth"]["bucket"] == "day" and len(s["growth"]["points"]) in (7, 8)
    assert s["growth"]["newInPeriod"] == {"patient": 1, "doctor": 2, "clinic": 0}
    assert s["activity"]["activeDay"]["patient"] == 1 and s["activity"]["patientsWithReadings"] == 1
    assert s["volume"]["measurements"] == {"total": 1, "period": 1} and s["volume"]["careLinks"]["total"] == 1
    assert "72" not in str(s["volume"]) and "value" not in str(s)  # no health values anywhere
    yearly = client.get("/v1/admin/stats", params={"days": 365}, headers=auth(adm)).json()
    assert yearly["growth"]["bucket"] == "week"

    rows = list(csv.reader(io.StringIO(client.get("/v1/admin/stats.csv", params={"days": 7}, headers=auth(adm)).text)))
    assert rows[0] == ["section", "metric", "key", "value"] and ["volume", "measurements", "total", "1"] in rows
    assert client.get("/v1/admin/stats", headers=auth(p)).status_code == 403
