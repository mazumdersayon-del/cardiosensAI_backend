"""Email + password sign-in, 16-digit IDs, and patient/doctor/clinic links."""

import re
from datetime import timedelta

import pyotp

from app.core.ids import format_unique_id, luhn_check_digit, new_unique_id, normalize_unique_id
from app.services import email
from tests.conftest import auth, enable_mfa, login, make_admin, now, reading, upload_licence

PW = "heart-rhythm-2026"
DOCTOR = {"registrationNumber": "WBMC-55821", "council": "West Bengal Medical Council", "specialty": "Cardiology"}
CLINIC = {"name": "Salt Lake Heart Clinic", "address": "AD-12, Sector 1", "city": "Kolkata", "registrationNumber": "CE-WB-0091",
          "phone": "+913340001111"}  # fmt: skip


def _token_from_last_email(to: str) -> str:  # password-reset links
    msg = next(m for m in reversed(email.outbox) if m.to == to.lower())
    return re.search(r"token=([A-Za-z0-9_\-]+)", msg.body).group(1)


def phone_for(addr: str) -> str:
    return f"+9197{abs(hash(addr.lower())) % 10**8:08d}"


def register(client, role: str, addr: str | None, name: str = "Test User", **extra) -> dict:
    """Start a sign-up (mobile number always, email when given); returns the signup id and the dev codes."""
    body = {"role": role, "phone": phone_for(addr or name), "password": PW, "fullName": name, **extra}
    if addr:
        body["email"] = addr
    if role == "doctor":
        body.setdefault("doctor", DOCTOR)
    if role == "clinic":
        body.setdefault("clinic", CLINIC)
    r = client.post("/v1/auth/register", json=body)
    assert r.status_code == 202, r.text
    return r.json()


def signup(client, role: str, addr: str | None, name: str = "Test User", **extra) -> dict:
    """Sign up and enter the codes: the account is created, activated and signed in."""
    s = register(client, role, addr, name, **extra)
    r = client.post(
        "/v1/auth/register/verify", json={"signupId": s["signupId"], "phoneCode": s["devPhoneCode"], "emailCode": s["devEmailCode"]}
    )
    assert r.status_code == 200, r.text
    return r.json()


def ready_professional(client, role: str, addr: str, name: str) -> dict:
    """A doctor or clinic that admin verified and that uses two-step sign-in."""
    t = signup(client, role, addr, name)
    upload_licence(client, t)
    adm = make_admin(client, f"+9198{abs(hash(addr)) % 10**8:08d}")
    if role == "doctor":
        r = client.post(f"/v1/admin/professionals/{t['user']['id']}/verify", json={"decision": "verified"}, headers=auth(adm))
    else:
        r = client.post(f"/v1/admin/organizations/{t['user']['organizationId']}/verify", json={"decision": "verified"}, headers=auth(adm))
    assert r.status_code == 204, r.text
    enable_mfa(client, t)
    return t


# ---------------------------------------------------------------- IDs


def test_unique_ids_have_role_digit_and_valid_check_digit():
    for role, prefix in (("patient", "1"), ("doctor", "2"), ("clinic", "3"), ("admin", "9")):
        uid = new_unique_id(role)
        assert len(uid) == 16 and uid[0] == prefix and normalize_unique_id(uid) == uid
    uid = new_unique_id("patient")
    assert normalize_unique_id(format_unique_id(uid)) == uid  # spaces allowed when typed
    typo = uid[:5] + str((int(uid[5]) + 1) % 10) + uid[6:]
    assert normalize_unique_id(typo) is None  # single-digit typo caught
    assert luhn_check_digit("7992739871") == "3"  # textbook example: 79927398713
    assert normalize_unique_id("4000000000000002") is None  # unknown role digit


# ---------------------------------------------------------------- sign-up and sign-in


def test_register_verify_login_for_each_role(client):
    p = signup(client, "patient", "Asha@Example.com", "Asha Rao", dateOfBirth="1980-04-02")
    assert p["user"]["role"] == "patient" and p["user"]["uniqueId"].startswith("1") and p["user"]["email"] == "asha@example.com"
    assert p["user"]["patientCode"].startswith("CS-P-") and p["user"]["phoneVerified"] and p["user"]["emailVerified"]
    assert p["newAccount"] is True and p["user"]["phone"] == phone_for("asha@example.com")
    d = signup(client, "doctor", "dr.sen@example.com", "Dr. Sen")
    assert d["user"]["uniqueId"].startswith("2")
    c = signup(client, "clinic", "desk@saltlakeheart.in", "Ritu Das")
    assert c["user"]["uniqueId"].startswith("3")
    org = client.get("/v1/me/organization", headers=auth(c)).json()
    assert org["uniqueId"] == c["user"]["uniqueId"] and org["verificationStatus"] == "pending"
    st = client.get("/v1/auth/status", headers=auth(d)).json()
    assert st["canLink"] is False and set(st["missing"]) == {"upload_licence", "verification", "enable_mfa"}


def test_signup_needs_both_codes_and_does_not_leak_accounts(client):
    st = register(client, "patient", "new@example.com", "Nisha Rao")
    assert st["phoneMasked"].endswith(phone_for("new@example.com")[-4:]) and st["emailMasked"].startswith("ne")
    code_mail = email.outbox[-1]
    assert code_mail.to == "new@example.com" and st["devEmailCode"] in code_mail.body and "http" not in code_mail.body  # a code, no link
    body = {"signupId": st["signupId"], "phoneCode": st["devPhoneCode"]}
    assert client.post("/v1/auth/register/verify", json=body).json()["error"]["code"] == "email_code_required"
    wrong = "000000" if st["devEmailCode"] != "000000" else "111111"
    assert client.post("/v1/auth/register/verify", json={**body, "emailCode": wrong}).json()["error"]["code"] == "invalid_code"
    # Nothing exists until the codes are entered.
    assert client.post("/v1/auth/login", json={"email": "new@example.com", "password": PW}).status_code == 401
    r = client.post("/v1/auth/register/verify", json={**body, "emailCode": st["devEmailCode"]})
    assert r.status_code == 200 and r.json()["user"]["emailVerified"] is True
    assert client.post("/v1/auth/register/verify", json={**body, "emailCode": st["devEmailCode"]}).status_code == 400  # single use

    # The same number or address again: same answer, but the owner is told instead of getting a code.
    before = len(email.outbox)
    again = register(client, "doctor", "NEW@example.com", "Someone Else")
    assert again["devPhoneCode"] is None and again["devEmailCode"] is None
    assert len(email.outbox) == before + 1 and "already have one" in email.outbox[-1].body
    assert (
        client.post(
            "/v1/auth/register/verify", json={"signupId": again["signupId"], "phoneCode": "123456", "emailCode": "123456"}
        ).status_code
        == 400
    )
    # Confirmation links are gone.
    assert client.post("/v1/auth/email/verify", json={"token": "x" * 30}).status_code in (404, 405)


def test_signup_without_email_and_resend(client):
    st = register(client, "patient", None, "Phone Only")
    assert st["emailMasked"] is None and st["devEmailCode"] is None
    fresh = client.post("/v1/auth/register/resend", json={"signupId": st["signupId"]}).json()
    assert fresh["signupId"] == st["signupId"]
    old = client.post("/v1/auth/register/verify", json={"signupId": st["signupId"], "phoneCode": st["devPhoneCode"]})
    assert old.status_code == 400 or st["devPhoneCode"] == fresh["devPhoneCode"]  # the old code stops working
    r = client.post("/v1/auth/register/verify", json={"signupId": st["signupId"], "phoneCode": fresh["devPhoneCode"]})
    assert r.status_code == 200 and r.json()["user"]["email"] is None and r.json()["user"]["phoneVerified"] is True
    # Later the account adds an email, confirmed with a code.
    t = r.json()
    c = client.post("/v1/me/contact/code", json={"kind": "email", "value": "later@example.com"}, headers=auth(t)).json()
    assert c["devCode"] in email.outbox[-1].body
    me = client.post("/v1/me/contact/verify", json={"challengeId": c["challengeId"], "code": c["devCode"]}, headers=auth(t)).json()
    assert me["email"] == "later@example.com" and me["emailVerified"] is True


def test_sign_in_with_a_code_by_email_or_sms_or_with_the_password(client):
    signup(client, "patient", "code@example.com", "Code User")
    phone = phone_for("code@example.com")
    by_mail = client.post("/v1/auth/login/start", json={"identifier": " Code@Example.com "}).json()
    assert by_mail["channel"] == "email" and by_mail["destinationMasked"].startswith("co") and by_mail["devCode"] in email.outbox[-1].body
    r = client.post("/v1/auth/login/verify", json={"challengeId": by_mail["challengeId"], "code": by_mail["devCode"]})
    assert r.status_code == 200 and r.json()["user"]["email"] == "code@example.com"
    assert client.post("/v1/auth/login/verify", json={"challengeId": by_mail["challengeId"], "code": by_mail["devCode"]}).status_code == 400

    spaced = f"{phone[:3]} {phone[3:8]} {phone[8:]}"
    by_sms = client.post("/v1/auth/login/start", json={"identifier": spaced}).json()
    assert by_sms["channel"] == "sms" and by_sms["destinationMasked"].endswith(phone[-4:])
    wrong = "000000" if by_sms["devCode"] != "000000" else "111111"
    for _ in range(5):
        client.post("/v1/auth/login/verify", json={"challengeId": by_sms["challengeId"], "code": wrong})
    assert (
        client.post("/v1/auth/login/verify", json={"challengeId": by_sms["challengeId"], "code": by_sms["devCode"]}).status_code == 400
    )  # 5 tries
    by_sms = client.post("/v1/auth/login/start", json={"identifier": phone}).json()
    assert client.post("/v1/auth/login/verify", json={"challengeId": by_sms["challengeId"], "code": by_sms["devCode"]}).status_code == 200

    # Unknown addresses get the same answer, and no code is sent.
    before = len(email.outbox)
    ghost = client.post("/v1/auth/login/start", json={"identifier": "ghost@example.com"})
    assert ghost.status_code == 200 and ghost.json()["devCode"] is None and len(email.outbox) == before
    assert client.post("/v1/auth/login/start", json={"identifier": "98765"}).json()["error"]["code"] == "invalid_identifier"

    # "Use password instead": email or mobile number with the password.
    assert client.post("/v1/auth/login", json={"identifier": phone, "password": PW}).status_code == 200
    assert client.post("/v1/auth/login", json={"identifier": "code@example.com", "password": PW}).status_code == 200
    assert client.post("/v1/auth/login", json={"identifier": phone, "password": "wrong-password-1"}).status_code == 401


def test_google_sign_up_and_sign_in(client, monkeypatch):
    from app.core.config import get_settings
    from app.services import google

    monkeypatch.setattr(get_settings(), "google_client_id", "web-client.apps.googleusercontent.com")
    asha, old = "google-id-token-for-asha", "google-id-token-for-old-user"
    who = {asha: google.GoogleIdentity(sub="g-111", email="asha.g@example.com", name="Asha G"),
           old: google.GoogleIdentity(sub="g-222", email="old@example.com", name="Old User")}  # fmt: skip
    monkeypatch.setattr(google, "verify_id_token", lambda tok: who[tok])
    assert client.get("/v1/auth/options").json() == {"googleClientId": "web-client.apps.googleusercontent.com", "smsAvailable": True}

    r = client.post("/v1/auth/google", json={"credential": asha})
    assert r.status_code == 404 and r.json()["error"]["code"] == "no_account"
    # Sign up with Google: no password, the email is Google's (verified); only the mobile number gets a code.
    body = {"role": "patient", "phone": "+919700000111", "fullName": "Asha G", "googleCredential": asha}
    st = client.post("/v1/auth/register", json=body).json()
    assert st["emailMasked"] is None and st["devEmailCode"] is None
    t = client.post("/v1/auth/register/verify", json={"signupId": st["signupId"], "phoneCode": st["devPhoneCode"]}).json()
    assert t["user"]["email"] == "asha.g@example.com" and t["user"]["emailVerified"] and t["user"]["googleLinked"]
    r = client.post("/v1/auth/google", json={"credential": asha, "webSession": True})
    assert r.status_code == 200 and r.json()["refreshToken"] is None and r.json()["user"]["id"] == t["user"]["id"]
    assert client.post("/v1/auth/register", json=body).json()["error"]["code"] == "already_registered"
    # No password yet: one can be set without a current password.
    assert client.post("/v1/auth/password/change", json={"newPassword": "google-user-pass-2026"}, headers=auth(t)).status_code == 204

    # An existing password account with the same verified address gets linked on first Google sign-in.
    signup(client, "patient", "old@example.com", "Old User")
    r = client.post("/v1/auth/google", json={"credential": old})
    assert r.status_code == 200 and r.json()["user"]["googleLinked"] is True


def test_password_policy_and_required_fields(client):
    base = {"role": "patient", "phone": "+919700000001", "email": "a@example.com", "fullName": "A B"}
    assert client.post("/v1/auth/register", json={**base, "password": "short"}).json()["error"]["code"] == "weak_password"
    assert client.post("/v1/auth/register", json={**base, "password": "Password123"}).json()["error"]["code"] == "weak_password"
    assert client.post("/v1/auth/register", json=base).json()["error"]["code"] == "password_required"
    assert client.post("/v1/auth/register", json={**base, "role": "doctor", "password": PW}).json()["error"]["code"] == "missing_details"
    no_phone = {k: v for k, v in base.items() if k != "phone"}
    assert client.post("/v1/auth/register", json={**no_phone, "password": PW}).status_code == 422  # mobile number is required


def test_lockout_after_five_failures(client):
    signup(client, "patient", "lock@example.com")
    for _ in range(5):
        r = client.post("/v1/auth/login", json={"email": "lock@example.com", "password": "wrong-password-1"})
        assert r.status_code == 401
    r = client.post("/v1/auth/login", json={"email": "lock@example.com", "password": PW})
    assert r.status_code == 429 and r.json()["error"]["code"] == "account_locked"
    # Unknown accounts get the same 401 as a wrong password.
    r = client.post("/v1/auth/login", json={"email": "nobody@example.com", "password": PW})
    assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_credentials"


def test_forgot_and_reset_password_signs_out_everywhere(client):
    t = signup(client, "patient", "reset@example.com")
    assert client.post("/v1/auth/password/forgot", json={"email": "nobody@example.com"}).status_code == 204
    assert client.post("/v1/auth/password/forgot", json={"email": "reset@example.com"}).status_code == 204
    token = _token_from_last_email("reset@example.com")
    assert client.post("/v1/auth/password/reset", json={"token": token, "password": "new-sinus-rhythm-9"}).status_code == 200
    assert client.post("/v1/auth/password/reset", json={"token": token, "password": "new-sinus-rhythm-9"}).status_code == 400  # single use
    assert client.post("/v1/auth/refresh", json={"refreshToken": t["refreshToken"]}).status_code == 401
    assert client.post("/v1/auth/login", json={"email": "reset@example.com", "password": PW}).status_code == 401
    assert client.post("/v1/auth/login", json={"email": "reset@example.com", "password": "new-sinus-rhythm-9"}).status_code == 200


def test_web_session_uses_httponly_cookie(client):
    signup(client, "patient", "web@example.com")
    r = client.post("/v1/auth/login", json={"email": "web@example.com", "password": PW, "webSession": True})
    assert r.status_code == 200 and r.json()["refreshToken"] is None
    cookie = r.headers["set-cookie"]
    assert "cs_refresh=" in cookie and "HttpOnly" in cookie and "SameSite=strict" in cookie and "Path=/v1/auth" in cookie
    # The cookie alone is not enough: the X-CS-Web header must be present too (CSRF guard).
    assert client.post("/v1/auth/refresh").status_code == 401
    r = client.post("/v1/auth/refresh", headers={"X-CS-Web": "1"})
    assert r.status_code == 200 and r.json()["refreshToken"] is None and r.json()["user"]["email"] == "web@example.com"
    assert client.post("/v1/auth/logout", headers={"X-CS-Web": "1"}).status_code == 204
    assert client.post("/v1/auth/refresh", headers={"X-CS-Web": "1"}).status_code == 401


def test_mfa_login_with_email(client):
    t = signup(client, "patient", "mfa@example.com")
    secret = enable_mfa(client, t)
    r = client.post("/v1/auth/login", json={"email": "mfa@example.com", "password": PW})
    assert r.json()["mfaRequired"] is True
    r = client.post("/v1/auth/mfa/verify", json={"mfaToken": r.json()["mfaToken"], "code": pyotp.TOTP(secret).now(), "webSession": True})
    assert r.status_code == 200 and r.json()["refreshToken"] is None


def test_doctors_cannot_turn_off_mfa(client):
    d = signup(client, "doctor", "d@example.com")
    secret = enable_mfa(client, d)
    r = client.post("/v1/auth/mfa/disable", json={"code": pyotp.TOTP(secret).now()}, headers=auth(d))
    assert r.status_code == 403


# ---------------------------------------------------------------- legacy accounts


def test_admin_moves_phone_account_to_email(client):
    app_user = login(client, "+919811112222", "caregiver", "Old App User")
    assert app_user["user"]["role"] == "patient" and app_user["user"]["uniqueId"].startswith("1")  # caregiver folded into patient
    adm = make_admin(client)
    users = client.get("/v1/admin/users", params={"q": app_user["user"]["uniqueId"]}, headers=auth(adm)).json()["items"]
    assert users[0]["hasPassword"] is False
    r = client.post(f"/v1/admin/users/{app_user['user']['id']}/email", json={"email": "old.user@example.com"}, headers=auth(adm))
    assert r.status_code == 204
    assert "set a password" in email.outbox[-1].body
    token = _token_from_last_email("old.user@example.com")
    assert client.post("/v1/auth/password/reset", json={"token": token, "password": PW}).status_code == 200
    r = client.post("/v1/auth/login", json={"email": "old.user@example.com", "password": PW})
    assert r.status_code == 200 and r.json()["user"]["id"] == app_user["user"]["id"] and r.json()["user"]["emailVerified"] is True


def test_admin_console_needs_mfa(client):
    t = login(client, "+919833333300")
    from tests.conftest import set_role

    set_role(t["user"]["id"], "admin")
    t = login(client, "+919833333300")
    r = client.get("/v1/admin/users", headers=auth(t))
    assert r.status_code == 403 and "two-step" in r.json()["error"]["message"]


# ---------------------------------------------------------------- links


def _patient_with_reading(client, addr: str) -> dict:
    p = signup(client, "patient", addr, "Asha Rao")
    item = reading("m-hr-1", "hr", None, now() - timedelta(minutes=5), value=72)
    r = client.post("/v1/measurements:batch", json={"measurements": [item]}, headers=auth(p))
    assert r.json()["results"][0]["status"] == "accepted", r.text
    return p


def test_unverified_doctor_cannot_link_or_see_patients(client):
    p = _patient_with_reading(client, "p@example.com")
    d = signup(client, "doctor", "d@example.com", "Dr. Sen")
    r = client.post("/v1/links", json={"targetId": p["user"]["uniqueId"]}, headers=auth(d))
    assert r.status_code == 403 and "verify" in r.json()["error"]["message"]
    # Patient asks; the doctor still can't approve until verified with two-step sign-in.
    link = client.post("/v1/links", json={"targetId": d["user"]["uniqueId"]}, headers=auth(p)).json()
    assert client.post(f"/v1/links/{link['id']}/approve", headers=auth(d)).status_code == 403
    assert client.get(f"/v1/directory/{p['user']['uniqueId']}", headers=auth(d)).status_code == 404


def test_doctor_requests_patient_approves_then_patient_ends(client):
    p = _patient_with_reading(client, "p@example.com")
    pid = p["user"]["id"]
    d = ready_professional(client, "doctor", "d@example.com", "Dr. Sen")
    card = client.get(f"/v1/directory/{format_unique_id(p['user']['uniqueId'])}", headers=auth(d)).json()
    assert card == {"uniqueId": p["user"]["uniqueId"], "role": "patient", "name": "Asha Rao", "verified": None, "detail": None}

    r = client.post("/v1/links", json={"targetId": p["user"]["uniqueId"]}, headers=auth(d))
    assert r.status_code == 201 and r.json()["status"] == "pending" and r.json()["direction"] == "outgoing"
    assert client.post("/v1/links", json={"targetId": p["user"]["uniqueId"]}, headers=auth(d)).status_code == 409
    assert client.get(f"/v1/patients/{pid}/measurements", headers=auth(d)).status_code == 404  # nothing before approval

    incoming = client.get("/v1/links", headers=auth(p)).json()
    assert incoming[0]["awaitingMe"] is True and incoming[0]["other"]["verified"] is True and incoming[0]["other"]["name"] == "Dr. Sen"
    assert client.post(f"/v1/links/{incoming[0]['id']}/approve", headers=auth(p)).json()["status"] == "active"

    assert client.get(f"/v1/patients/{pid}/measurements", headers=auth(d)).json()["items"][0]["value"] == 72
    listed = client.get("/v1/linked-patients", headers=auth(d)).json()
    assert listed[0]["uniqueId"] == p["user"]["uniqueId"] and listed[0]["lastMeasurementAt"] is not None

    assert client.post(f"/v1/links/{incoming[0]['id']}/end", headers=auth(p)).json()["status"] == "ended"
    assert client.get(f"/v1/patients/{pid}/measurements", headers=auth(d)).status_code == 404
    assert client.get("/v1/linked-patients", headers=auth(d)).json() == []


def test_patient_requests_declined_by_doctor(client):
    p = signup(client, "patient", "p@example.com")
    d = ready_professional(client, "doctor", "d@example.com", "Dr. Sen")
    link = client.post("/v1/links", json={"targetId": d["user"]["uniqueId"]}, headers=auth(p)).json()
    assert link["awaitingMe"] is False
    assert client.post(f"/v1/links/{link['id']}/decline", headers=auth(d)).json()["status"] == "declined"
    # Declined requests don't block a new one.
    assert client.post("/v1/links", json={"targetId": d["user"]["uniqueId"]}, headers=auth(p)).status_code == 201


def test_clinic_roster_and_request_on_doctor_behalf(client):
    p = _patient_with_reading(client, "p@example.com")
    d = ready_professional(client, "doctor", "d@example.com", "Dr. Sen")
    c = ready_professional(client, "clinic", "desk@clinic.in", "Ritu Das")

    # doctor <-> clinic: clinic asks, doctor approves
    link = client.post("/v1/links", json={"targetId": d["user"]["uniqueId"]}, headers=auth(c)).json()
    assert link["kind"] == "doctor_clinic"
    assert client.post(f"/v1/links/{link['id']}/approve", headers=auth(d)).json()["status"] == "active"
    roster = client.get("/v1/clinic/doctors", headers=auth(c)).json()
    assert roster[0]["doctor"]["uniqueId"] == d["user"]["uniqueId"] and roster[0]["patientCount"] == 0

    # patient <-> clinic, patient-initiated, clinic approves
    pc = client.post("/v1/links", json={"targetId": c["user"]["uniqueId"]}, headers=auth(p)).json()
    assert pc["kind"] == "patient_clinic" and pc["other"]["name"] == "Salt Lake Heart Clinic"
    assert client.post(f"/v1/links/{pc['id']}/approve", headers=auth(c)).json()["status"] == "active"
    # The clinic's link does NOT give its doctors access.
    assert client.get(f"/v1/patients/{p['user']['id']}/measurements", headers=auth(d)).status_code == 404
    assert client.get(f"/v1/patients/{p['user']['id']}/measurements", headers=auth(c)).status_code == 200

    # Clinic asks the patient to link with Dr. Sen; only the patient needs to approve.
    r = client.post("/v1/links", json={"targetId": p["user"]["uniqueId"], "onBehalfOfDoctorId": d["user"]["uniqueId"]}, headers=auth(c))
    assert r.status_code == 201, r.text
    mine = [x for x in client.get("/v1/links", headers=auth(p)).json() if x["status"] == "pending"]
    assert mine[0]["kind"] == "patient_doctor" and mine[0]["viaClinic"]["name"] == "Salt Lake Heart Clinic" and mine[0]["awaitingMe"]
    assert client.post(f"/v1/links/{mine[0]['id']}/approve", headers=auth(p)).json()["status"] == "active"
    assert client.get(f"/v1/patients/{p['user']['id']}/measurements", headers=auth(d)).status_code == 200

    patients = client.get("/v1/linked-patients", headers=auth(c)).json()
    assert [x["uniqueId"] for x in patients[0]["doctors"]] == [d["user"]["uniqueId"]]
    assert client.get("/v1/clinic/doctors", headers=auth(c)).json()[0]["patientCount"] == 1

    # Removing the doctor from the clinic ends that link only.
    assert client.post(f"/v1/links/{link['id']}/end", headers=auth(c)).json()["status"] == "ended"
    assert client.get("/v1/clinic/doctors", headers=auth(c)).json() == []


def test_clinic_cannot_request_for_doctor_outside_clinic(client):
    p = signup(client, "patient", "p@example.com")
    d = ready_professional(client, "doctor", "d@example.com", "Dr. Sen")
    c = ready_professional(client, "clinic", "desk@clinic.in", "Ritu Das")
    r = client.post("/v1/links", json={"targetId": p["user"]["uniqueId"], "onBehalfOfDoctorId": d["user"]["uniqueId"]}, headers=auth(c))
    assert r.status_code == 403


def test_invalid_pairs_and_ids(client):
    p = signup(client, "patient", "p@example.com")
    p2 = signup(client, "patient", "p2@example.com")
    assert client.post("/v1/links", json={"targetId": p2["user"]["uniqueId"]}, headers=auth(p)).json()["error"]["code"] == "invalid_pair"
    assert client.post("/v1/links", json={"targetId": "1234567812345678"}, headers=auth(p)).json()["error"]["code"] in (
        "invalid_id",
        "not_found",
    )
    assert client.post("/v1/links", json={"targetId": p["user"]["uniqueId"]}, headers=auth(p)).json()["error"]["code"] == "self_link"


def test_account_deletion_ends_links(client):
    p = signup(client, "patient", "p@example.com")
    d = ready_professional(client, "doctor", "d@example.com", "Dr. Sen")
    link = client.post("/v1/links", json={"targetId": d["user"]["uniqueId"]}, headers=auth(p)).json()
    client.post(f"/v1/links/{link['id']}/approve", headers=auth(d))
    assert client.delete("/v1/me", headers=auth(p)).status_code == 204
    assert client.get("/v1/links", headers=auth(d)).json() == []
