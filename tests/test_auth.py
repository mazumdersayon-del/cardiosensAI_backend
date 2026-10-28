import pyotp
import pytest
from pydantic import ValidationError

from app.core.config import Settings
from tests.conftest import auth, login


def test_first_sign_in_creates_patient_with_code(client):
    t = login(client, "+919811111111", name="Asha")
    assert t["newAccount"] is True
    assert t["user"]["role"] == "patient"
    assert t["user"]["patientCode"].startswith("CS-P-")
    me = client.get("/v1/me", headers=auth(t)).json()
    assert me["fullName"] == "Asha"


def test_wrong_code_is_rejected_and_attempts_are_limited(client):
    body = client.post("/v1/auth/otp/request", json={"phone": "+919811111112"}).json()
    for _ in range(5):
        r = client.post(
            "/v1/auth/otp/verify", json={"challengeId": body["challengeId"], "code": "000000" if body["devCode"] != "000000" else "111111"}
        )
        assert r.status_code == 400
    # Even the right code fails once attempts are used up.
    r = client.post("/v1/auth/otp/verify", json={"challengeId": body["challengeId"], "code": body["devCode"]})
    assert r.status_code == 400


def test_code_cannot_be_reused(client):
    body = client.post("/v1/auth/otp/request", json={"phone": "+919811111113"}).json()
    ok = client.post("/v1/auth/otp/verify", json={"challengeId": body["challengeId"], "code": body["devCode"]})
    assert ok.status_code == 200
    again = client.post("/v1/auth/otp/verify", json={"challengeId": body["challengeId"], "code": body["devCode"]})
    assert again.status_code == 400


def test_otp_requests_are_rate_limited(client):
    for _ in range(5):
        assert client.post("/v1/auth/otp/request", json={"phone": "+919811111114"}).status_code == 200
    r = client.post("/v1/auth/otp/request", json={"phone": "+919811111114"})
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "rate_limited"


def test_refresh_rotation_and_reuse_detection(client):
    t = login(client, "+919811111115")
    r1 = client.post("/v1/auth/refresh", json={"refreshToken": t["refreshToken"]})
    assert r1.status_code == 200
    new = r1.json()
    # Re-using the old token looks like theft: it fails AND kills the newer token in the same family.
    assert client.post("/v1/auth/refresh", json={"refreshToken": t["refreshToken"]}).status_code == 401
    assert client.post("/v1/auth/refresh", json={"refreshToken": new["refreshToken"]}).status_code == 401


def test_mfa_flow(client):
    t = login(client, "+919811111116")
    setup = client.post("/v1/auth/mfa/setup", headers=auth(t)).json()
    totp = pyotp.TOTP(setup["secret"])
    assert client.post("/v1/auth/mfa/enable", json={"code": totp.now()}, headers=auth(t)).status_code == 204
    body = client.post("/v1/auth/otp/request", json={"phone": "+919811111116"}).json()
    step1 = client.post("/v1/auth/otp/verify", json={"challengeId": body["challengeId"], "code": body["devCode"]}).json()
    assert step1["mfaRequired"] is True and "accessToken" not in step1
    step2 = client.post("/v1/auth/mfa/verify", json={"mfaToken": step1["mfaToken"], "code": totp.now()})
    assert step2.status_code == 200 and step2.json()["accessToken"]


def test_requests_without_token_are_unauthorised(client):
    r = client.get("/v1/me")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"


def test_production_refuses_unsafe_settings():
    with pytest.raises(ValidationError):
        Settings(env="production")
    with pytest.raises(ValidationError):
        Settings(env="production", jwt_secret="x" * 40, signing_key="y" * 40, dev_expose_otp=True, sms_provider="none")
    safe = {
        "jwt_secret": "x" * 40,
        "signing_key": "y" * 40,
        "device_keys_secret": "z" * 40,
        "mqtt_password": "m" * 30,
        "sms_provider": "none",
        "dev_expose_otp": False,
    }
    with pytest.raises(ValidationError):  # dev device-key secret
        Settings(env="production", email_provider="smtp", smtp_host="smtp.example.com", **{**safe, "device_keys_secret": "dev-only-x" * 4})
    with pytest.raises(ValidationError):
        Settings(env="production", **safe)  # console email would log password-reset links
    with pytest.raises(ValidationError):
        Settings(env="production", email_provider="smtp", **safe)  # no SMTP host
    ok = Settings(env="production", email_provider="smtp", smtp_host="smtp.example.com", **safe)
    assert ok.env == "production"


def test_otp_not_exposed_outside_dev(client, monkeypatch):
    from app.core import config

    monkeypatch.setattr(config.get_settings(), "env", "staging")
    body = client.post("/v1/auth/otp/request", json={"phone": "+919811111117"}).json()
    assert body["devCode"] is None
