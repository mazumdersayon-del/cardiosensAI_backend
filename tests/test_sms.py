"""SMS delivery: providers, and what happens when none is configured."""

import pytest

from app.core import config
from app.core.errors import ApiError


def test_no_sms_provider_is_reported_as_unavailable(client, monkeypatch):
    monkeypatch.setattr(config.get_settings(), "sms_provider", "none")
    r = client.post("/v1/auth/otp/request", json={"phone": "+919822222221"})
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "sms_unavailable"


def test_sign_up_and_sms_sign_in_need_a_provider(client, monkeypatch):
    monkeypatch.setattr(config.get_settings(), "sms_provider", "none")
    body = {"role": "patient", "phone": "+919822222222", "password": "heart-rhythm-2026", "fullName": "No Sms"}
    assert client.post("/v1/auth/register", json=body).json()["error"]["code"] == "sms_unavailable"
    assert client.post("/v1/auth/login/start", json={"identifier": "+919822222222"}).status_code == 503
    assert client.get("/v1/auth/options").json()["smsAvailable"] is False


def _msg91_on(monkeypatch, answer: dict | None = None) -> list[tuple[str, bytes, dict]]:
    """SMS_PROVIDER=msg91 with the HTTP call captured; returns the list of requests made."""
    from app.services import sms

    s = config.get_settings()
    sent: list[tuple[str, bytes, dict]] = []

    def post(url, data, headers):
        sent.append((url, data, headers))
        return answer if answer is not None else {"type": "success", "request_id": "r1"}

    monkeypatch.setattr(sms, "_post", post)
    monkeypatch.setattr(s, "sms_provider", "msg91")
    monkeypatch.setattr(s, "msg91_auth_key", "key")
    monkeypatch.setattr(s, "msg91_otp_template_id", "tpl-otp")
    monkeypatch.setattr(s, "msg91_notice_template_id", "")
    return sent


def test_msg91_otp_request(monkeypatch):
    from app.services import sms

    sent = _msg91_on(monkeypatch)
    sms.send_otp("+919812345678", "654321")
    url, _, headers = sent[-1]
    assert url.startswith("https://control.msg91.com/api/v5/otp?") and headers["authkey"] == "key"
    for part in ("template_id=tpl-otp", "mobile=919812345678", "otp=654321", "otp_length=6", "otp_expiry=10"):
        assert part in url
    n = len(sent)
    sms.send_already_registered("+919812345678")  # no notice template: skipped quietly
    assert len(sent) == n
    monkeypatch.setattr(config.get_settings(), "msg91_notice_template_id", "tpl-notice")
    sms.send_already_registered("+919812345678")
    url, data, _ = sent[-1]
    assert url.endswith("/flow/") and b"tpl-notice" in data and b"919812345678" in data


def test_msg91_errors_are_503(monkeypatch):
    from app.services import sms

    _msg91_on(monkeypatch, {"type": "error", "message": "Invalid authkey"})  # MSG91 answers HTTP 200 with this
    with pytest.raises(ApiError) as exc:
        sms.send_otp("+919812345678", "111111")
    assert exc.value.code == "sms_unavailable"
    sms.send_already_registered("+919812345678")  # best effort: never raises

    def down(*_):
        raise OSError("down")

    monkeypatch.setattr(sms, "_post", down)
    with pytest.raises(ApiError):
        sms.send_otp("+919812345678", "111111")


def test_sign_up_and_sign_in_send_codes_to_phone_and_email(client, monkeypatch):
    from app.services import email, sms

    sent = _msg91_on(monkeypatch)
    body = {"role": "patient", "phone": "+919833333333", "email": "both@example.com", "password": "heart-rhythm-2026", "fullName": "Both"}
    st = client.post("/v1/auth/register", json=body).json()
    assert f"otp={st['devPhoneCode']}" in sent[-1][0] and "mobile=919833333333" in sent[-1][0]
    assert email.outbox[-1].to == "both@example.com" and st["devEmailCode"] in email.outbox[-1].body
    r = client.post(
        "/v1/auth/register/verify", json={"signupId": st["signupId"], "phoneCode": st["devPhoneCode"], "emailCode": st["devEmailCode"]}
    )
    assert r.status_code == 200, r.text

    # sign-in by number: SMS (MSG91) to the number + a second code to the confirmed email; either works
    n, mails = len(sent), len(email.outbox)
    by_phone = client.post("/v1/auth/login/start", json={"identifier": "+91 98333 33333"}).json()
    assert by_phone["channel"] == "sms" and len(sent) == n + 1 and f"otp={by_phone['devCode']}" in sent[-1][0]
    assert (
        len(email.outbox) == mails + 1 and email.outbox[-1].to == "both@example.com" and by_phone["devOtherCode"] in email.outbox[-1].body
    )
    assert "both@" not in str(by_phone) and "b•" not in by_phone["destinationMasked"]  # the other contact isn't revealed
    r = client.post("/v1/auth/login/verify", json={"challengeId": by_phone["challengeId"], "code": by_phone["devOtherCode"]})
    assert r.status_code == 200 and r.json()["accessToken"]
    again = client.post("/v1/auth/login/verify", json={"challengeId": by_phone["challengeId"], "code": by_phone["devCode"]})
    assert again.status_code == 400  # one sign-in uses up both codes

    # sign-in by email: email code + SMS to the confirmed number; the SMS code works
    n, mails = len(sent), len(email.outbox)
    by_mail = client.post("/v1/auth/login/start", json={"identifier": "both@example.com"}).json()
    assert by_mail["channel"] == "email" and len(email.outbox) == mails + 1 and by_mail["devCode"] in email.outbox[-1].body
    assert len(sent) == n + 1 and f"otp={by_mail['devOtherCode']}" in sent[-1][0] and "mobile=919833333333" in sent[-1][0]
    wrong = "000000" if "000000" not in (by_mail["devCode"], by_mail["devOtherCode"]) else "999999"
    assert client.post("/v1/auth/login/verify", json={"challengeId": by_mail["challengeId"], "code": wrong}).status_code == 400
    r = client.post("/v1/auth/login/verify", json={"challengeId": by_mail["challengeId"], "code": by_mail["devOtherCode"]})
    assert r.status_code == 200

    # MSG91 down: the email code still goes out and still works
    monkeypatch.setattr(sms, "_post", lambda *_: {"type": "error", "message": "no balance"})
    by_mail = client.post("/v1/auth/login/start", json={"identifier": "both@example.com"})
    assert by_mail.status_code == 200
    r = client.post("/v1/auth/login/verify", json={"challengeId": by_mail.json()["challengeId"], "code": by_mail.json()["devCode"]})
    assert r.status_code == 200

    n, mails = len(sent), len(email.outbox)
    unknown = client.post("/v1/auth/login/start", json={"identifier": "nobody@example.com"}).json()
    assert unknown["devOtherCode"] is None and len(sent) == n and len(email.outbox) == mails  # same answer, nothing sent


def test_second_sign_in_code_only_goes_to_a_confirmed_contact(client, monkeypatch):
    from app.core.db import SessionLocal
    from app.models import User
    from app.services import email

    _msg91_on(monkeypatch)
    st = client.post(
        "/v1/auth/register", json={"role": "patient", "phone": "+919844444444", "password": "heart-rhythm-2026", "fullName": "Phone Only"}
    ).json()
    assert client.post("/v1/auth/register/verify", json={"signupId": st["signupId"], "phoneCode": st["devPhoneCode"]}).status_code == 200
    with SessionLocal() as db:  # an email nobody confirmed (e.g. a typo): it must not get sign-in codes
        u = db.query(User).filter(User.phone == "+919844444444").one()
        u.email, u.email_verified_at = "typo@example.com", None
        db.commit()
    mails = len(email.outbox)
    started = client.post("/v1/auth/login/start", json={"identifier": "+919844444444"}).json()
    assert started["devOtherCode"] is None and len(email.outbox) == mails
