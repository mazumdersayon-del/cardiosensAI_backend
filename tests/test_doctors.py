"""Patients' Doctors list: only verified doctors who chose to be listed, editable by the doctor without re-verification."""

from tests.conftest import auth
from tests.test_accounts_links import ready_professional, signup

LISTING = {
    "listed": True,
    "degrees": "MBBS, MD, DM (Cardiology)",
    "experienceYears": 12,
    "languages": "English, Hindi",
    "practice": "City Heart Hospital",
    "city": "Pune",
    "about": "Heart rhythm problems.",
}


def test_listed_verified_doctor_is_shown_to_patients_with_profile_picture_and_link_state(client):
    doc = ready_professional(client, "doctor", "dr.list@example.com", "Dr. Listed")
    hidden = ready_professional(client, "doctor", "dr.hidden@example.com", "Dr. Not Listed")
    patient = signup(client, "patient", "pat.list@example.com", "Pat Ient")

    r = client.put("/v1/me/doctor-listing", json=LISTING, headers=auth(doc))
    assert r.status_code == 200 and r.json()["visible"] is True and r.json()["hiddenReason"] is None
    client.put("/v1/me/doctor-listing", json={**LISTING, "listed": False}, headers=auth(hidden))
    me = client.get("/v1/me", headers=auth(doc)).json()
    assert client.get("/v1/me/professional-profile", headers=auth(doc)).json()["verificationStatus"] == "verified"  # not re-verified

    page = client.get("/v1/doctors", headers=auth(patient)).json()
    assert page["total"] == 1 and [d["fullName"] for d in page["items"]] == ["Dr. Listed"]
    card = page["items"][0]
    assert card["degrees"] == LISTING["degrees"] and card["experienceYears"] == 12 and card["avatarVersion"] is None
    assert client.get("/v1/doctors?q=pune", headers=auth(patient)).json()["total"] == 1
    assert client.get("/v1/doctors?q=dermatology", headers=auth(patient)).json()["total"] == 0

    uid = me["uniqueId"]
    detail = client.get(f"/v1/doctors/{uid}", headers=auth(patient)).json()
    assert detail["about"] == "Heart rhythm problems." and detail["linkStatus"] == "none" and detail["council"]
    assert client.get(f"/v1/doctors/{hidden['user']['uniqueId']}", headers=auth(patient)).status_code == 404

    jpeg = b"\xff\xd8\xff\xe0" + b"0" * 100
    client.put("/v1/me/avatar", files={"file": ("a.jpg", jpeg, "image/jpeg")}, headers=auth(doc))
    pic = client.get(f"/v1/doctors/{uid}/avatar", headers=auth(patient))
    assert pic.status_code == 200 and pic.content == jpeg
    assert client.get("/v1/doctors", headers=auth(patient)).json()["items"][0]["avatarVersion"]

    assert client.post("/v1/links", json={"targetId": uid}, headers=auth(patient)).status_code == 201
    assert client.get(f"/v1/doctors/{uid}", headers=auth(patient)).json()["linkStatus"] == "pending"


def test_only_patients_browse_and_unverified_doctors_stay_hidden(client):
    doc = signup(client, "doctor", "dr.new@example.com", "Dr. New")
    r = client.put("/v1/me/doctor-listing", json=LISTING, headers=auth(doc))
    assert r.status_code == 200 and r.json()["visible"] is False and "verified" in r.json()["hiddenReason"]
    patient = signup(client, "patient", "pat.two@example.com", "Pat Two")
    assert client.get("/v1/doctors", headers=auth(patient)).json()["total"] == 0
    assert client.get("/v1/doctors", headers=auth(doc)).status_code == 403
    assert client.put("/v1/me/doctor-listing", json=LISTING, headers=auth(patient)).json()["error"]["code"] == "not_a_doctor_account"
    assert client.put("/v1/me/doctor-listing", json={**LISTING, "experienceYears": 200}, headers=auth(doc)).status_code == 422
