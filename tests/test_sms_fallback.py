"""SMS down (MSG91 failing or not set up): sign-up goes on with the email code, sign-in points to email or password."""

from app.core import config
from app.services import email, sms
from tests.conftest import auth
from tests.test_sms import _msg91_on

PASSWORD = "heart-rhythm-2026"


def _register(client, phone: str, addr: str | None = None, name: str = "Asha Rao"):
    body = {"role": "patient", "phone": phone, "password": PASSWORD, "fullName": name, **({"email": addr} if addr else {})}
    return client.post("/v1/auth/register", json=body)


def _msg91_failing(monkeypatch):
    sent = _msg91_on(monkeypatch)
    monkeypatch.setattr(sms, "_post", lambda url, data, headers: sent.append((url, data, headers)) or {"type": "error", "message": "down"})
    return sent


def test_sign_up_without_sms_uses_the_email_code_and_leaves_the_number_unconfirmed(client, monkeypatch):
    monkeypatch.setattr(config.get_settings(), "sms_provider", "none")
    st = _register(client, "+919811111111", "asha@example.com").json()
    assert st["phoneCodeSent"] is False and st["devPhoneCode"] is None and st["devEmailCode"] in email.outbox[-1].body
    r = client.post("/v1/auth/register/verify", json={"signupId": st["signupId"], "emailCode": st["devEmailCode"]})
    assert r.status_code == 200, r.text
    tokens = r.json()
    me = client.get("/v1/me", headers=auth(tokens)).json()
    assert me["phone"] == "+919811111111" and me["phoneVerified"] is False and me["emailVerified"] is True
    status = client.get("/v1/auth/status", headers=auth(tokens)).json()
    assert status["phoneVerified"] is False and status["canLink"] is True  # the confirmed email is enough to use the account


def test_sign_up_without_sms_and_without_email_asks_for_an_email(client, monkeypatch):
    monkeypatch.setattr(config.get_settings(), "sms_provider", "none")
    r = _register(client, "+919811111112")
    assert r.status_code == 503 and r.json()["error"]["code"] == "sms_unavailable" and "email" in r.json()["error"]["message"]


def test_msg91_failing_during_sign_up_falls_back_to_email_and_marks_sms_down(client, monkeypatch):
    sent = _msg91_failing(monkeypatch)
    st = _register(client, "+919811111113", "ravi@example.com", "Ravi Kumar").json()
    assert len(sent) == 1 and st["phoneCodeSent"] is False and st["devPhoneCode"] is None
    assert sms.available() is False and client.get("/v1/auth/options").json()["smsAvailable"] is False
    r = client.post("/v1/auth/register/verify", json={"signupId": st["signupId"], "emailCode": st["devEmailCode"]})
    assert r.status_code == 200
    # while SMS counts as down, the next sign-up doesn't wait on MSG91 at all
    st2 = _register(client, "+919811111114", "mina@example.com", "Mina Das").json()
    assert st2["phoneCodeSent"] is False and len(sent) == 1
    assert _register(client, "+919811111115").status_code == 503  # no email: nothing to fall back to


def test_resend_switches_to_email_only_when_msg91_fails(client, monkeypatch):
    sent = _msg91_on(monkeypatch)
    st = _register(client, "+919811111116", "lata@example.com", "Lata Sen").json()
    assert st["phoneCodeSent"] is True and len(sent) == 1
    body = {"signupId": st["signupId"], "emailCode": st["devEmailCode"]}
    assert client.post("/v1/auth/register/verify", json=body).json()["error"]["code"] == "phone_code_required"
    monkeypatch.setattr(sms, "_post", lambda *_: {"type": "error", "message": "down"})
    again = client.post("/v1/auth/register/resend", json={"signupId": st["signupId"]}).json()
    assert again["phoneCodeSent"] is False and again["devPhoneCode"] is None
    r = client.post("/v1/auth/register/verify", json={"signupId": st["signupId"], "emailCode": again["devEmailCode"]})
    assert r.status_code == 200


def test_number_is_confirmed_later_once_sms_works(client, monkeypatch):
    monkeypatch.setattr(config.get_settings(), "sms_provider", "none")
    st = _register(client, "+919811111117", "neha@example.com", "Neha Roy").json()
    tokens = client.post("/v1/auth/register/verify", json={"signupId": st["signupId"], "emailCode": st["devEmailCode"]}).json()
    assert client.post("/v1/me/contact/code", json={"kind": "phone", "value": "+919811111117"}, headers=auth(tokens)).status_code == 503

    sent = _msg91_on(monkeypatch)  # SMS is back
    c = client.post("/v1/me/contact/code", json={"kind": "phone", "value": "+919811111117"}, headers=auth(tokens)).json()
    assert f"otp={c['devCode']}" in sent[-1][0]
    r = client.post("/v1/me/contact/verify", json={"challengeId": c["challengeId"], "code": c["devCode"]}, headers=auth(tokens))
    assert r.status_code == 200 and r.json()["phoneVerified"] is True
    locked = client.post("/v1/me/contact/code", json={"kind": "phone", "value": "+919811111118"}, headers=auth(tokens))
    assert locked.json()["error"]["code"] == "phone_locked"  # confirmed numbers are changed by support only


def test_an_unconfirmed_number_does_not_block_its_real_owner(client, monkeypatch):
    monkeypatch.setattr(config.get_settings(), "sms_provider", "none")
    st = _register(client, "+919811111119", "typo@example.com", "Typed Wrong").json()  # someone else's number
    squatter = client.post("/v1/auth/register/verify", json={"signupId": st["signupId"], "emailCode": st["devEmailCode"]}).json()

    _msg91_on(monkeypatch)
    owner = _register(client, "+919811111119", "owner@example.com", "Real Owner").json()
    assert owner["phoneCodeSent"] is True and owner["devPhoneCode"]  # not treated as taken
    r = client.post(
        "/v1/auth/register/verify",
        json={"signupId": owner["signupId"], "phoneCode": owner["devPhoneCode"], "emailCode": owner["devEmailCode"]},
    )
    assert r.status_code == 200
    assert client.get("/v1/me", headers=auth(r.json())).json()["phoneVerified"] is True
    assert client.get("/v1/me", headers=auth(squatter)).json()["phone"] is None  # the unconfirmed copy was released


def test_sign_in_by_number_while_sms_is_down_points_to_email_or_password(client, monkeypatch):
    _msg91_on(monkeypatch)
    st = _register(client, "+919811111120", "kiran@example.com", "Kiran Pal").json()
    r = client.post(
        "/v1/auth/register/verify", json={"signupId": st["signupId"], "phoneCode": st["devPhoneCode"], "emailCode": st["devEmailCode"]}
    )
    assert r.status_code == 200

    monkeypatch.setattr(sms, "_post", lambda *_: {"type": "error", "message": "down"})
    r = client.post("/v1/auth/login/start", json={"identifier": "+919811111120"})
    err = r.json()["error"]
    assert r.status_code == 503 and err["code"] == "sms_unavailable" and "email" in err["message"] and "password" in err["message"]
    # SMS now counts as down: unknown numbers get the same answer, without calling MSG91
    assert client.post("/v1/auth/login/start", json={"identifier": "+919800000002"}).status_code == 503

    # the two ways the message offers both work
    started = client.post("/v1/auth/login/start", json={"identifier": "kiran@example.com"}).json()
    assert started["channel"] == "email" and started["devOtherCode"] is None  # no SMS second code while it's down
    assert client.post("/v1/auth/login/verify", json={"challengeId": started["challengeId"], "code": started["devCode"]}).status_code == 200
    assert client.post("/v1/auth/login", json={"identifier": "+919811111120", "password": PASSWORD}).status_code == 200


def test_sign_up_with_a_registered_number_while_sms_is_down_says_so_after_the_email_code(client, monkeypatch, caplog):
    """Regression (production 2026-10-28): the email code arrived, verify said "incorrect or expired" and resend sent nothing."""
    monkeypatch.setattr(config.get_settings(), "sms_provider", "none")
    first = _register(client, "+919811111121", "first@example.com", "First Owner").json()
    assert (
        client.post("/v1/auth/register/verify", json={"signupId": first["signupId"], "emailCode": first["devEmailCode"]}).status_code == 200
    )

    mails = len(email.outbox)
    st = _register(client, "+919811111121", "second@example.com", "Second Person").json()
    assert st["phoneCodeSent"] is False and len(email.outbox) == mails + 1  # looks like any sign-up: an email code goes out
    code = email.outbox[-1].body.split("\n\n    ")[1][:6]
    r = client.post("/v1/auth/register/verify", json={"signupId": st["signupId"], "emailCode": code})
    assert r.status_code == 409 and r.json()["error"]["code"] == "already_registered"  # proved the email: tell them why

    again = client.post("/v1/auth/register/resend", json={"signupId": st["signupId"]})
    assert again.status_code == 200 and len(email.outbox) == mails + 2  # resend behaves like the first send


def test_one_time_codes_never_reach_the_log(client, monkeypatch, caplog):
    import logging

    monkeypatch.setattr(config.get_settings(), "email_provider", "smtp")
    monkeypatch.setattr(config.get_settings(), "smtp_host", "smtp.invalid")

    class FakeSMTP:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self, **k):
            pass

        def login(self, *a):
            pass

        def send_message(self, msg):
            pass

    import smtplib

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    with caplog.at_level(logging.INFO, logger="cardiosensai.email"):
        assert email.send_code("asha@example.com", "Asha", "482913", purpose="signup")
    logged = " ".join(str(getattr(r, "extra_fields", "")) + r.getMessage() for r in caplog.records)
    assert "email_sent" in logged and "482913" not in logged
