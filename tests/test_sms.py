"""MSG91 delivery, exercised against a local stand-in for the MSG91 API."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import pytest
from pydantic import ValidationError

from app.core import config
from app.core.config import Settings


class FakeMsg91:
    def __init__(self, reply: dict):
        self.reply = reply
        self.requests: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802 - http.server API
                url = urlparse(self.path)
                outer.requests.append({"path": url.path, "query": parse_qs(url.query), "authkey": self.headers.get("authkey")})
                body = json.dumps(outer.reply).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


@pytest.fixture
def msg91(monkeypatch):
    fake = FakeMsg91({"type": "success", "request_id": "abc123"})
    s = config.get_settings()
    monkeypatch.setattr(s, "sms_provider", "msg91")
    monkeypatch.setattr(s, "msg91_auth_key", "test-auth-key")
    monkeypatch.setattr(s, "msg91_template_id", "tmpl-1")
    monkeypatch.setattr(s, "msg91_base_url", fake.url)
    monkeypatch.setattr(s, "sms_timeout_s", 5.0)
    yield fake
    fake.close()


def test_code_is_sent_through_msg91(client, msg91):
    r = client.post("/v1/auth/otp/request", json={"phone": "+919822222221"})
    assert r.status_code == 200, r.text
    [sent] = msg91.requests
    assert sent["path"] == "/api/v5/otp"
    assert sent["authkey"] == "test-auth-key"
    q = {k: v[0] for k, v in sent["query"].items()}
    assert q["template_id"] == "tmpl-1"
    assert q["mobile"] == "919822222221"
    assert q["otp"] == r.json()["devCode"]  # the code we send is the one we verify
    assert q["otp_length"] == "6" and q["otp_expiry"] == "5"


def test_msg91_error_is_reported_as_unavailable(client, msg91):
    msg91.reply = {"type": "error", "message": "Template ID entered is incomplete / incorrect"}
    r = client.post("/v1/auth/otp/request", json={"phone": "+919822222222"})
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "sms_unavailable"


def test_msg91_unreachable_is_reported_as_unavailable(client, msg91, monkeypatch):
    monkeypatch.setattr(config.get_settings(), "msg91_base_url", "http://127.0.0.1:9")
    r = client.post("/v1/auth/otp/request", json={"phone": "+919822222223"})
    assert r.status_code == 503


def test_msg91_needs_credentials():
    with pytest.raises(ValidationError):
        Settings(sms_provider="msg91")
    ok = Settings(
        env="production",
        jwt_secret="x" * 40,
        signing_key="y" * 40,
        dev_expose_otp=False,
        sms_provider="msg91",
        msg91_auth_key="k",
        msg91_template_id="t",
    )
    assert ok.sms_provider == "msg91"
