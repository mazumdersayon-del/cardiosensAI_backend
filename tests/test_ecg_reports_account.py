import base64
import struct
from datetime import timedelta
from urllib.parse import urlparse

from tests.conftest import auth, iso, login, now, reading, register_device


def ecg_body(device_id: str, cid: str = "ecg_1", seconds: int = 10, fs: int = 250, offset: int = 0) -> dict:
    samples = [((i * 37 + offset) % 400) - 200 for i in range(seconds * fs)]
    return {
        "id": cid,
        "deviceId": device_id,
        "startedAt": iso(now() - timedelta(hours=1)),
        "lead": "Lead I",
        "sampleRateHz": fs,
        "calibration": {"mmPerMv": 10, "mmPerSec": 25},
        "averageHr": 72,
        "poorQualitySegments": [{"fromSec": 2, "toSec": 4}],
        "quality": "poor",
        "acquisition": "live",
        "samplesBase64": base64.b64encode(struct.pack(f"<{len(samples)}h", *samples)).decode(),
    }


def setup(client):
    t = login(client, "+919844444441", name="Ananya")
    wear = register_device(client, t, "cardiosensai-wearable-dev", "WR-7")
    cuff = register_device(client, t, "ble-bp-standard", "BP-7")
    ts = now() - timedelta(hours=23)  # within the 24 h grace before device registration
    items = [reading(f"hr{i}", "hr", wear["id"], ts + timedelta(minutes=10 * i), value=70 + i % 5) for i in range(30)]
    items.append(reading("bp1", "bp", cuff["id"], ts, systolic=126, diastolic=81))
    client.post("/v1/measurements:batch", json={"measurements": items}, headers=auth(t))
    return t, wear, cuff


def test_ecg_upload_is_idempotent_and_samples_round_trip(client):
    t, wear, cuff = setup(client)
    body = ecg_body(wear["id"])
    r1 = client.post("/v1/ecg-recordings", json=body, headers=auth(t))
    assert r1.status_code == 200 and r1.json()["status"] == "accepted"
    assert r1.json()["recording"]["startedAt"].endswith("+05:30")
    assert client.post("/v1/ecg-recordings", json=body, headers=auth(t)).json()["status"] == "duplicate"
    assert client.post("/v1/ecg-recordings", json=ecg_body(wear["id"], offset=5), headers=auth(t)).status_code == 400
    # A BP cuff isn't approved to record ECG.
    assert client.post("/v1/ecg-recordings", json=ecg_body(cuff["id"], cid="ecg_2"), headers=auth(t)).status_code == 400
    rid = r1.json()["recording"]["id"]
    raw = client.get(f"/v1/ecg-recordings/{rid}/samples", headers=auth(t))
    assert raw.status_code == 200 and raw.content == base64.b64decode(body["samplesBase64"])
    assert raw.headers["x-sample-rate-hz"] == "250"


def test_ecg_hidden_from_viewer_without_ecg_permission(client):
    t, wear, _ = setup(client)
    rid = client.post("/v1/ecg-recordings", json=ecg_body(wear["id"]), headers=auth(t)).json()["recording"]["id"]
    g = client.post(
        "/v1/grants",
        json={"granteeName": "Rohan", "granteeContact": "r@example.org", "role": "family", "vitals": ["hr"], "history": "all"},
        headers=auth(t),
    ).json()
    fam = login(client, "+919844444442", "caregiver")
    client.post("/v1/invites/accept", json={"token": g["inviteToken"]}, headers=auth(fam))
    assert client.get(f"/v1/ecg-recordings/{rid}/samples", headers=auth(fam)).status_code == 403


def _download_path(url: str) -> str:
    return urlparse(url).path


def test_report_generation_and_signed_download(client):
    t, wear, _ = setup(client)
    client.post("/v1/ecg-recordings", json=ecg_body(wear["id"]), headers=auth(t))
    today = now().date()
    r = client.post(
        "/v1/reports",
        json={
            "fromDate": str(today - timedelta(days=6)),
            "toDate": str(today),
            "vitals": ["hr", "bp", "glucose"],
            "includeEcg": True,
            "includeTables": True,
        },
        headers=auth(t),
    )
    assert r.status_code == 202
    rid = r.json()["id"]
    rep = client.get(f"/v1/reports/{rid}", headers=auth(t)).json()  # TestClient runs background tasks before returning
    assert rep["status"] == "ready", rep
    assert rep["pages"] >= 2
    link = client.post(f"/v1/reports/{rid}/download-link", headers=auth(t)).json()
    pdf = client.get(_download_path(link["url"]))
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert pdf.headers["cache-control"].startswith("private")
    # Tampered links are refused.
    assert client.get(_download_path(link["url"]) + "x").status_code == 410


def test_revoking_access_kills_existing_report_links(client):
    t, _, _ = setup(client)
    pid = client.get("/v1/me", headers=auth(t)).json()["id"]
    g = client.post(
        "/v1/grants",
        json={
            "granteeName": "Dr. Iyer",
            "granteeContact": "d@example.org",
            "role": "family",
            "vitals": ["hr"],
            "history": "all",
            "downloadsAllowed": True,
        },
        headers=auth(t),
    ).json()
    fam = login(client, "+919844444443", "caregiver")
    client.post("/v1/invites/accept", json={"token": g["inviteToken"]}, headers=auth(fam))
    today = now().date()
    # Asking for more than was shared is refused.
    bad = client.post(
        "/v1/reports",
        json={"patientId": pid, "fromDate": str(today - timedelta(days=3)), "toDate": str(today), "vitals": ["hr", "bp"]},
        headers=auth(fam),
    )
    assert bad.status_code == 403
    rid = client.post(
        "/v1/reports",
        json={"patientId": pid, "fromDate": str(today - timedelta(days=3)), "toDate": str(today), "vitals": ["hr"], "includeEcg": False},
        headers=auth(fam),
    ).json()["id"]
    link = client.post(f"/v1/reports/{rid}/download-link", headers=auth(fam)).json()
    assert client.get(_download_path(link["url"])).status_code == 200
    client.post(f"/v1/grants/{g['grant']['id']}/revoke", headers=auth(t))
    assert client.get(_download_path(link["url"])).status_code in (403, 404)


def test_viewer_without_download_permission_cannot_create_reports(client):
    t, _, _ = setup(client)
    pid = client.get("/v1/me", headers=auth(t)).json()["id"]
    g = client.post(
        "/v1/grants",
        json={"granteeName": "Rohan", "granteeContact": "r@example.org", "role": "family", "vitals": ["hr"], "history": "all"},
        headers=auth(t),
    ).json()
    fam = login(client, "+919844444444", "caregiver")
    client.post("/v1/invites/accept", json={"token": g["inviteToken"]}, headers=auth(fam))
    today = now().date()
    r = client.post(
        "/v1/reports",
        json={"patientId": pid, "fromDate": str(today), "toDate": str(today), "vitals": ["hr"], "includeEcg": False},
        headers=auth(fam),
    )
    assert r.status_code == 403


def test_export_and_account_deletion(client):
    t, wear, _ = setup(client)
    client.post("/v1/ecg-recordings", json=ecg_body(wear["id"]), headers=auth(t))
    exported = client.get("/v1/me/export", headers=auth(t)).json()
    assert len(exported["measurements"]) == 31 and len(exported["ecgRecordings"]) == 1
    assert client.delete("/v1/me", headers=auth(t)).status_code == 204
    assert client.get("/v1/me", headers=auth(t)).status_code == 401
    # The same phone number now starts a brand-new, empty account.
    fresh = login(client, "+919844444441")
    assert fresh["newAccount"] is True
    assert client.get("/v1/patients/me/measurements", headers=auth(fresh)).json()["items"] == []
