"""Patient details (height, weight, address with PIN, background), who sees them, the reminder, and Near me."""

import uuid

from app.core.db import SessionLocal
from app.models import AuditEvent, PatientProfile, User
from app.services import pincodes
from app.services.erasure import erase_account
from tests.conftest import auth
from tests.test_accounts_links import ready_professional, signup
from tests.test_appointments import _link


def test_pin_table_and_distance():
    kolkata, salt_lake, delhi = pincodes.lookup("700016"), pincodes.lookup("700091"), pincodes.lookup("110001")
    assert kolkata.state == "West Bengal" and delhi.state == "Delhi" and len(pincodes._table()) > 19000
    # GeoNames positions are coarse (often the district centre): Salt Lake comes out ~45 km from Park Street
    assert pincodes.km(kolkata, salt_lake) < 60 and 1200 < pincodes.km(kolkata, delhi) < 1400
    assert pincodes.clean(" 560 001 ") == "560001" and pincodes.clean("") is None
    for bad in ("056001", "12345", "1234567", "abcdef"):
        try:
            pincodes.clean(bad)
        except ValueError:
            continue
        raise AssertionError(bad)


def test_patient_adds_details_and_reminder_until_pin(client):
    p = signup(client, "patient", "pat.prof@example.com", "Pat Profile")
    me = client.get("/v1/me/patient-profile", headers=auth(p)).json()
    assert me["needsAddress"] and me["showReminder"] and me["bmi"] is None and me["conditions"] == []
    # "Later" hides the reminder for a day, it still needs an address
    me = client.post("/v1/me/patient-profile/remind-later", headers=auth(p)).json()
    assert me["needsAddress"] and not me["showReminder"]
    assert not client.get("/v1/me/patient-profile", headers=auth(p)).json()["showReminder"]
    body = {"heightCm": 170, "weightKg": 72.25, "sex": "female", "bloodGroup": "O+", "smoking": "never"}
    me = client.put("/v1/me/patient-profile", json=body, headers=auth(p)).json()
    assert me["bmi"] == 25.0 and me["weightKg"] == 72.2 and me["weightAt"] and me["needsAddress"]
    # address: district and state come from the PIN
    addr = {"addressLine1": "12 Park Street", "city": "Kolkata", "pinCode": "700 016"}
    me = client.put("/v1/me/patient-profile", json=addr, headers=auth(p)).json()
    assert me["pinCode"] == "700016" and me["pinKnown"] and me["state"] == "West Bengal" and me["district"]
    assert not me["needsAddress"] and not me["showReminder"] and me["heightCm"] == 170  # other fields kept
    me = client.put(
        "/v1/me/patient-profile", json={"conditions": ["hypertension", "diabetes"], "allergies": "Penicillin"}, headers=auth(p)
    ).json()
    assert me["conditions"] == ["hypertension", "diabetes"] and me["allergies"] == "Penicillin"
    # validation
    bad = [{"heightCm": 400}, {"weightKg": 0}, {"pinCode": "012345"}, {"conditions": ["flu"]}, {"bloodGroup": "C+"}]
    for b in bad:
        assert client.put("/v1/me/patient-profile", json=b, headers=auth(p)).status_code in (400, 422), b
    assert client.put("/v1/me/patient-profile", json={"sex": ""}, headers=auth(p)).json()["sex"] is None
    assert client.get("/v1/pincodes/700016", headers=auth(p)).json()["state"] == "West Bengal"
    assert client.get("/v1/pincodes/999999", headers=auth(p)).status_code == 404
    # doctors can't have one
    d = ready_professional(client, "doctor", "dr.prof@example.com", "Dr. Prof")
    assert client.get("/v1/me/patient-profile", headers=auth(d)).status_code == 403


def test_linked_doctor_sees_details_street_only_if_shared(client):
    p = signup(client, "patient", "pat.share@example.com", "Pat Share")
    d = ready_professional(client, "doctor", "dr.share@example.com", "Dr. Share")
    other = ready_professional(client, "doctor", "dr.notlinked@example.com", "Dr. Not Linked")
    client.put(
        "/v1/me/patient-profile",
        json={"heightCm": 160, "addressLine1": "5 Lake Road", "pinCode": "700029", "bloodGroup": "B+"},
        headers=auth(p),
    )
    pid = p["user"]["id"]
    assert client.get(f"/v1/patients/{pid}/profile", headers=auth(d)).status_code == 404
    _link(client, p, d)
    seen = client.get(f"/v1/patients/{pid}/profile", headers=auth(d)).json()
    assert seen["bloodGroup"] == "B+" and seen["pinCode"] == "700029" and seen["addressLine1"] is None and seen["showReminder"] is False
    client.put("/v1/me/patient-profile", json={"shareAddress": True}, headers=auth(p))
    assert client.get(f"/v1/patients/{pid}/profile", headers=auth(d)).json()["addressLine1"] == "5 Lake Road"
    assert client.get(f"/v1/patients/{pid}/profile", headers=auth(other)).status_code == 404
    db = SessionLocal()
    assert db.query(AuditEvent).filter(AuditEvent.action == "profile.view").count() == 2
    erase_account(db, db.get(User, uuid.UUID(pid)), actor=None, action="account.erase")
    db.commit()
    assert db.query(PatientProfile).count() == 0
    db.close()


def _listed_doctor(client, addr, name, pin):
    d = ready_professional(client, "doctor", addr, name)
    body = {"listed": True, "city": "Somewhere", **({"pinCode": pin} if pin else {})}
    r = client.put("/v1/me/doctor-listing", json=body, headers=auth(d))
    assert r.status_code == 200, r.text
    return d


def test_near_me_lists_doctors_by_distance(client):
    near = _listed_doctor(client, "dr.near@example.com", "Dr. Near", "700029")  # Kolkata: same area as the patient
    mid = _listed_doctor(client, "dr.mid@example.com", "Dr. Mid", "700091")  # Salt Lake: ~45 km in the PIN table
    far = _listed_doctor(client, "dr.far@example.com", "Dr. Far", "110001")  # Delhi
    _listed_doctor(client, "dr.nopin@example.com", "Dr. No Pin", None)
    p = signup(client, "patient", "pat.near@example.com", "Pat Near")
    r = client.get("/v1/doctors", params={"nearKm": 25}, headers=auth(p)).json()
    assert r["items"] == [] and "PIN code" in r["nearProblem"]
    client.put("/v1/me/patient-profile", json={"pinCode": "700016"}, headers=auth(p))
    r = client.get("/v1/doctors", params={"nearKm": 25}, headers=auth(p)).json()
    assert [x["uniqueId"] for x in r["items"]] == [near["user"]["uniqueId"]] and r["near"].startswith("700016")
    assert r["items"][0]["distanceKm"] == 0
    r = client.get("/v1/doctors", params={"nearKm": 100}, headers=auth(p)).json()
    assert [x["uniqueId"] for x in r["items"]] == [near["user"]["uniqueId"], mid["user"]["uniqueId"]]
    assert r["items"][1]["distanceKm"] > 10
    r = client.get("/v1/doctors", params={"nearKm": 2000}, headers=auth(p))
    assert r.status_code == 422
    # without nearKm: everyone, as before
    assert client.get("/v1/doctors", headers=auth(p)).json()["total"] == 4
    # a doctor's listing keeps the PIN when an older app doesn't send it; bad PINs are refused
    client.put("/v1/me/doctor-listing", json={"listed": True, "city": "Delhi"}, headers=auth(far))
    assert client.get("/v1/me/doctor-listing", headers=auth(far)).json()["pinCode"] == "110001"
    assert client.put("/v1/me/doctor-listing", json={"listed": True, "pinCode": "0"}, headers=auth(far)).status_code in (400, 422)
