from datetime import timedelta

from app.core.db import SessionLocal
from app.models import AccessGrant
from tests.conftest import auth, login, now, reading, register_device


def patient_with_data(client):
    t = login(client, "+919833333331", name="Ananya")
    wear = register_device(client, t, "cardiosensai-wearable-dev", "WR-9")
    cuff = register_device(client, t, "ble-bp-standard", "BP-9")
    ts = now() - timedelta(minutes=20)
    items = [
        reading("hr1", "hr", wear["id"], ts, value=71),
        reading("spo1", "spo2", wear["id"], ts, value=97),
        reading("bp1", "bp", cuff["id"], ts, systolic=122, diastolic=79),
    ]
    r = client.post("/v1/measurements:batch", json={"measurements": items}, headers=auth(t))
    assert all(x["status"] == "accepted" for x in r.json()["results"])
    return t


def invite(client, t, role="family", vitals=("hr", "bp"), history="from_now", **kw):
    body = {
        "granteeName": "Someone",
        "granteeContact": "someone@example.org",
        "role": role,
        "vitals": list(vitals),
        "history": history,
        **kw,
    }
    r = client.post("/v1/grants", json=body, headers=auth(t))
    assert r.status_code == 201, r.text
    return r.json()


def test_viewer_sees_only_shared_vitals_within_history_window(client):
    t = patient_with_data(client)
    pid = client.get("/v1/me", headers=auth(t)).json()["id"]
    g = invite(client, t, history="all")
    fam = login(client, "+919833333332", "caregiver")
    assert client.post("/v1/invites/accept", json={"token": g["inviteToken"]}, headers=auth(fam)).status_code == 200

    hr = client.get(f"/v1/patients/{pid}/measurements?vital=hr", headers=auth(fam))
    assert hr.status_code == 200 and len(hr.json()["items"]) == 1
    assert client.get(f"/v1/patients/{pid}/measurements?vital=spo2", headers=auth(fam)).status_code == 403
    all_shared = client.get(f"/v1/patients/{pid}/measurements", headers=auth(fam)).json()["items"]
    assert {i["vital"] for i in all_shared} == {"hr", "bp"}
    latest = client.get(f"/v1/patients/{pid}/measurements/latest", headers=auth(fam)).json()
    assert set(latest["latest"]) == {"hr", "bp"}


def test_from_now_grant_hides_older_readings(client):
    t = patient_with_data(client)  # readings are 20 minutes old
    pid = client.get("/v1/me", headers=auth(t)).json()["id"]
    g = invite(client, t, history="from_now")
    fam = login(client, "+919833333333", "caregiver")
    client.post("/v1/invites/accept", json={"token": g["inviteToken"]}, headers=auth(fam))
    page = client.get(f"/v1/patients/{pid}/measurements?vital=hr", headers=auth(fam)).json()
    assert page["items"] == [] and page["historyLimitedFrom"] is not None


def test_strangers_and_pending_invitees_cannot_see_the_patient(client):
    t = patient_with_data(client)
    pid = client.get("/v1/me", headers=auth(t)).json()["id"]
    invite(client, t)  # not accepted
    stranger = login(client, "+919833333334", "caregiver")
    r = client.get(f"/v1/patients/{pid}/measurements", headers=auth(stranger))
    assert r.status_code == 404  # existence isn't revealed
    assert client.get("/v1/shared-with-me", headers=auth(stranger)).json() == []


def test_revocation_is_immediate_and_invites_are_single_use(client):
    t = patient_with_data(client)
    pid = client.get("/v1/me", headers=auth(t)).json()["id"]
    g = invite(client, t, history="all")
    fam = login(client, "+919833333335", "caregiver")
    other = login(client, "+919833333336", "caregiver")
    client.post("/v1/invites/accept", json={"token": g["inviteToken"]}, headers=auth(fam))
    assert client.post("/v1/invites/accept", json={"token": g["inviteToken"]}, headers=auth(other)).status_code == 400
    assert client.get(f"/v1/patients/{pid}/measurements", headers=auth(fam)).status_code == 200
    r = client.post(f"/v1/grants/{g['grant']['id']}/revoke", headers=auth(t))
    assert r.json()["status"] == "revoked"
    assert client.get(f"/v1/patients/{pid}/measurements", headers=auth(fam)).status_code == 404
    assert any(n["title"] == "Access ended" for n in client.get("/v1/notifications", headers=auth(fam)).json())


def test_expired_grant_stops_access(client):
    t = patient_with_data(client)
    pid = client.get("/v1/me", headers=auth(t)).json()["id"]
    g = invite(client, t, history="all")
    fam = login(client, "+919833333337", "caregiver")
    client.post("/v1/invites/accept", json={"token": g["inviteToken"]}, headers=auth(fam))
    db = SessionLocal()
    grant = db.get(AccessGrant, __import__("uuid").UUID(g["grant"]["id"]))
    grant.expires_at = now() - timedelta(minutes=1)
    db.commit()
    db.close()
    assert client.get(f"/v1/patients/{pid}/measurements", headers=auth(fam)).status_code == 404
    assert client.get("/v1/grants", headers=auth(t)).json()[0]["status"] == "expired"


def test_doctor_invites_require_verification_by_admin(client):
    t = patient_with_data(client)
    pid = client.get("/v1/me", headers=auth(t)).json()["id"]
    g = invite(client, t, role="doctor", vitals=("hr", "bp", "spo2", "ecg"), history="all", downloadsAllowed=True)
    doc = login(client, "+919833333338", "doctor", "Dr. Iyer")
    assert client.post("/v1/invites/accept", json={"token": g["inviteToken"]}, headers=auth(doc)).status_code == 403
    client.put(
        "/v1/me/professional-profile", json={"registrationNumber": "KMC-12345", "council": "Karnataka Medical Council"}, headers=auth(doc)
    )

    from app.models import User

    adm = login(client, "+919833333339")
    db = SessionLocal()
    u = db.get(User, __import__("uuid").UUID(adm["user"]["id"]))
    u.role = "admin"
    db.commit()
    db.close()
    adm = login(client, "+919833333339")
    r = client.post(f"/v1/admin/professionals/{doc['user']['id']}/verify", json={"decision": "verified"}, headers=auth(adm))
    assert r.status_code == 204
    assert client.post("/v1/invites/accept", json={"token": g["inviteToken"]}, headers=auth(doc)).status_code == 200
    listed = client.get("/v1/shared-with-me", headers=auth(doc)).json()
    assert listed[0]["patientId"] == pid and listed[0]["lastMeasurementAt"] is not None

    # Admins administer the platform but get no health data — not even via the admin role.
    assert client.get(f"/v1/patients/{pid}/measurements", headers=auth(adm)).status_code == 404
    users = client.get("/v1/admin/users", headers=auth(adm)).json()
    assert all("•" in u["phoneMasked"] for u in users)
    assert all(
        set(u) <= {"id", "role", "status", "phoneMasked", "fullName", "createdAt", "deviceCount", "professionalStatus"} for u in users
    )
    # Patients and doctors can't use admin endpoints.
    assert client.get("/v1/admin/users", headers=auth(doc)).status_code == 403


def test_viewer_reads_appear_in_patient_access_log(client):
    t = patient_with_data(client)
    pid = client.get("/v1/me", headers=auth(t)).json()["id"]
    g = invite(client, t, history="all")
    fam = login(client, "+919833333340", "caregiver", "Rohan")
    client.post("/v1/invites/accept", json={"token": g["inviteToken"]}, headers=auth(fam))
    client.get(f"/v1/patients/{pid}/measurements?vital=bp", headers=auth(fam))
    log = client.get("/v1/me/access-log", headers=auth(t)).json()
    reads = [e for e in log if e["action"] == "measurements.read"]
    assert reads and reads[0]["actorName"] == "Rohan" and reads[0]["detail"] == "blood pressure history"
    # The audit trail names what was accessed, never the values.
    assert all("122" not in (e["detail"] or "") for e in log)
