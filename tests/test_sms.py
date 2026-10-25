"""Sign-in code delivery when no SMS provider is configured."""

from app.core import config


def test_no_sms_provider_is_reported_as_unavailable(client, monkeypatch):
    monkeypatch.setattr(config.get_settings(), "sms_provider", "none")
    r = client.post("/v1/auth/otp/request", json={"phone": "+919822222221"})
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "sms_unavailable"
