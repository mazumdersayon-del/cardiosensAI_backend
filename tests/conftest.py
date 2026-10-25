"""Test setup. Uses TEST_DATABASE_URL (PostgreSQL in CI) or a temporary SQLite file."""

import os
import tempfile
from datetime import UTC, datetime, timedelta, timezone

_tmp = tempfile.mkdtemp(prefix="cardiosens-test-")
os.environ["ENV"] = "test"
os.environ["DEV_EXPOSE_OTP"] = "true"
os.environ["STORAGE_LOCAL_PATH"] = os.path.join(_tmp, "storage")
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL", f"sqlite:///{os.path.join(_tmp, 'test.db')}")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

import app.models  # noqa: E402, F401
from app.core.db import Base, SessionLocal, get_engine  # noqa: E402
from app.core.ratelimit import limiter  # noqa: E402
from app.main import app  # noqa: E402
from app.scripts.seed import seed_models  # noqa: E402

IST = timezone(timedelta(minutes=330))


@pytest.fixture(scope="session", autouse=True)
def _schema():
    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture(autouse=True)
def _clean():
    engine = get_engine()
    with engine.begin() as conn:
        if engine.dialect.name == "postgresql":
            names = ", ".join(t.name for t in Base.metadata.sorted_tables)
            conn.execute(text(f"TRUNCATE {names} CASCADE"))
        else:
            for t in reversed(Base.metadata.sorted_tables):
                conn.execute(t.delete())
    db = SessionLocal()
    seed_models(db)
    db.commit()
    db.close()
    limiter.reset()
    yield


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def login(client: TestClient, phone: str, account_type: str = "patient", name: str | None = None) -> dict:
    r = client.post("/v1/auth/otp/request", json={"phone": phone})
    assert r.status_code == 200, r.text
    body = r.json()
    r = client.post(
        "/v1/auth/otp/verify",
        json={"challengeId": body["challengeId"], "code": body["devCode"], "accountType": account_type, "fullName": name},
    )
    assert r.status_code == 200, r.text
    return r.json()


def auth(tokens: dict) -> dict:
    return {"Authorization": f"Bearer {tokens['accessToken']}"}


def register_device(client: TestClient, tokens: dict, model: str, serial: str, client_ref: str | None = None) -> dict:
    r = client.post("/v1/devices", json={"modelCode": model, "serial": serial, "clientRef": client_ref}, headers=auth(tokens))
    assert r.status_code == 201, r.text
    return r.json()


def iso(dt: datetime) -> str:
    return dt.astimezone(IST).isoformat(timespec="seconds")


def now() -> datetime:
    return datetime.now(UTC)


def reading(mid: str, vital: str, device_id: str | None, when: datetime, **kw) -> dict:
    unit = {"hr": "bpm", "spo2": "%", "resp": "breaths/min", "temp": "°C", "glucose": "mg/dL", "bp": "mmHg"}[vital]
    body = {
        "id": mid,
        "vital": vital,
        "measuredAt": iso(when),
        "receivedAt": iso(when + timedelta(seconds=30)),
        "unit": unit,
        "provenance": {"deviceId": device_id, "acquisition": "live" if device_id else "manual", "quality": "good", "qualityFlags": []},
        "sync": "queued",
    }
    body.update(kw)
    return body
