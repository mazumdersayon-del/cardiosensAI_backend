"""SMS delivery: providers, and what happens when none is configured."""

from app.core import config


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


def test_twilio_and_msg91_requests(monkeypatch):
    from app.services import sms

    s = config.get_settings()
    sent: list[tuple[str, bytes, dict]] = []
    monkeypatch.setattr(sms, "_post", lambda url, data, headers: sent.append((url, data, headers)))

    monkeypatch.setattr(s, "sms_provider", "twilio")
    monkeypatch.setattr(s, "twilio_account_sid", "AC123")
    monkeypatch.setattr(s, "twilio_auth_token", "secret")
    monkeypatch.setattr(s, "twilio_from", "MG456")
    sms.send_otp("+919812345678", "123456")
    url, data, headers = sent[-1]
    assert url.endswith("/Accounts/AC123/Messages.json") and b"MessagingServiceSid=MG456" in data and b"123456" in data
    assert headers["Authorization"].startswith("Basic ")

    monkeypatch.setattr(s, "sms_provider", "msg91")
    monkeypatch.setattr(s, "msg91_auth_key", "key")
    monkeypatch.setattr(s, "msg91_otp_template_id", "tpl-otp")
    sms.send_otp("+919812345678", "654321")
    url, _, headers = sent[-1]
    assert "template_id=tpl-otp" in url and "mobile=919812345678" in url and "otp=654321" in url and headers["authkey"] == "key"
    n = len(sent)
    sms.send_already_registered("+919812345678")  # no notice template: skipped quietly
    assert len(sent) == n

    def fail(*_):
        raise OSError("down")

    monkeypatch.setattr(sms, "_post", fail)
    try:
        sms.send_otp("+919812345678", "111111")
        raise AssertionError("expected 503")
    except Exception as exc:  # noqa: BLE001
        assert getattr(exc, "code", None) == "sms_unavailable"
