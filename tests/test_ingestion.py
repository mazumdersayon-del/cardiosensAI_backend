import uuid
from datetime import timedelta

from app.core.db import SessionLocal
from app.models import Device
from tests.conftest import auth, iso, login, now, reading, register_device


def setup_patient(client):
    t = login(client, "+919822222221")
    wear = register_device(client, t, "cardiosensai-wearable-dev", "WR-1", client_ref="dev_local_wrist")
    cuff = register_device(client, t, "ble-bp-standard", "BP-1")
    return t, wear, cuff


def post(client, t, items):
    r = client.post("/v1/measurements:batch", json={"measurements": items}, headers=auth(t))
    assert r.status_code == 200, r.text
    return {x["id"]: x for x in r.json()["results"]}


def test_accept_then_duplicate_then_conflict(client):
    t, wear, _ = setup_patient(client)
    m = reading("m_1", "hr", wear["id"], now() - timedelta(minutes=5), value=72)
    assert post(client, t, [m])["m_1"]["status"] == "accepted"
    # Retry after a timeout: same id, same content, different receivedAt → duplicate, not a second row.
    m2 = dict(m, receivedAt=iso(now()))
    assert post(client, t, [m2])["m_1"]["status"] == "duplicate"
    conflict = dict(m, value=99)
    res = post(client, t, [conflict])["m_1"]
    assert res["status"] == "rejected" and "different" in res["reason"]
    page = client.get("/v1/patients/me/measurements?vital=hr", headers=auth(t)).json()
    assert len(page["items"]) == 1 and page["items"][0]["value"] == 72


def test_each_item_gets_its_own_outcome(client):
    t, wear, cuff = setup_patient(client)
    ts = now() - timedelta(minutes=10)
    items = [
        reading("ok_bp", "bp", cuff["id"], ts, systolic=128, diastolic=82, meanArterial=97, pulseRate=70),
        reading("ok_manual", "temp", None, ts, value=36.8),
        reading("ok_client_ref", "spo2", "dev_local_wrist", ts, value=97),  # the app's own device id
        reading("bad_unit", "temp", wear["id"], ts, value=98.6, unit="°F"),
        reading("bad_range", "hr", wear["id"], ts, value=900),
        reading("bad_bp", "bp", cuff["id"], ts, systolic=80, diastolic=120),
        reading("future", "hr", wear["id"], now() + timedelta(hours=2), value=70),
        reading("unknown_device", "hr", "00000000-0000-0000-0000-000000000000", ts, value=70),
        reading("cuff_hr", "hr", cuff["id"], ts, value=70),  # cuff model isn't approved for heart rate
        reading(
            "manual_with_device",
            "temp",
            wear["id"],
            ts,
            value=36.7,
            provenance={"deviceId": wear["id"], "acquisition": "manual", "quality": "unknown"},
        ),
        reading("sim", "hr", wear["id"], ts, value=70, provenance={"deviceId": wear["id"], "acquisition": "simulated", "quality": "good"}),
        {"id": "garbage", "vital": "hr"},
    ]
    res = post(client, t, items)
    assert {k for k, v in res.items() if v["status"] == "accepted"} == {"ok_bp", "ok_manual", "ok_client_ref"}
    for k in ["bad_unit", "bad_range", "bad_bp", "future", "unknown_device", "cuff_hr", "manual_with_device", "sim", "garbage"]:
        assert res[k]["status"] == "rejected", k
        assert res[k]["reason"], k


def test_glucose_control_solution_and_preregistration_readings_are_refused(client):
    t = login(client, "+919822222222")
    meter = register_device(client, t, "ble-glucose-standard", "GL-1")
    ts = now() - timedelta(hours=1)
    control = reading(
        "ctrl",
        "glucose",
        meter["id"],
        ts,
        value=120,
        glucoseMeta={"sampleType": "Control solution", "sampleLocation": "Control solution", "isControlSolution": True},
    )
    old = reading(
        "old",
        "glucose",
        meter["id"],
        now() - timedelta(days=10),
        value=101,
        provenance={"deviceId": meter["id"], "acquisition": "delayed", "quality": "good"},
    )
    good = reading(
        "good",
        "glucose",
        meter["id"],
        ts,
        value=104,
        glucoseMeta={"sampleType": "Capillary whole blood", "sampleLocation": "Finger", "isControlSolution": False, "sequence": 7},
    )
    res = post(client, t, [control, old, good])
    assert res["ctrl"]["status"] == "rejected"
    # A second-hand meter's memory may hold someone else's results — refuse readings from before registration.
    assert res["old"]["status"] == "rejected" and "before this device was registered" in res["old"]["reason"]
    assert res["good"]["status"] == "accepted"


def test_delayed_readings_after_registration_are_kept_with_original_time_and_offset(client):
    t, wear, _ = setup_patient(client)
    db = SessionLocal()
    d = db.get(Device, uuid.UUID(wear["id"]))
    d.registered_at = now() - timedelta(days=3)
    db.commit()
    db.close()
    when = now() - timedelta(days=2)
    m = reading("late", "hr", wear["id"], when, value=66, provenance={"deviceId": wear["id"], "acquisition": "delayed", "quality": "good"})
    assert post(client, t, [m])["late"]["status"] == "accepted"
    item = client.get("/v1/patients/me/measurements?vital=hr", headers=auth(t)).json()["items"][0]
    assert item["measuredAt"].endswith("+05:30")
    assert item["provenance"]["acquisition"] == "delayed"


def test_pagination_has_no_gaps_or_overlaps(client):
    t, wear, _ = setup_patient(client)
    base = now() - timedelta(hours=5)
    items = [reading(f"p{i}", "hr", wear["id"], base + timedelta(minutes=i), value=60 + i % 20) for i in range(50)]
    post(client, t, items)
    seen, cursor = [], None
    while True:
        url = "/v1/patients/me/measurements?vital=hr&limit=7" + (f"&cursor={cursor}" if cursor else "")
        page = client.get(url, headers=auth(t)).json()
        seen += [i["id"] for i in page["items"]]
        cursor = page["nextCursor"]
        if not cursor:
            break
    assert len(seen) == 50 and len(set(seen)) == 50
    assert seen[0] == "p49"


def test_latest_per_vital(client):
    t, wear, cuff = setup_patient(client)
    ts = now() - timedelta(minutes=30)
    post(client, t, [reading("a", "hr", wear["id"], ts, value=70), reading("b", "hr", wear["id"], ts + timedelta(minutes=5), value=75)])
    latest = client.get("/v1/patients/me/measurements/latest", headers=auth(t)).json()
    assert latest["latest"]["hr"]["value"] == 75
    assert latest["latest"]["glucose"] is None


def test_device_ownership_is_exclusive(client):
    t1 = login(client, "+919822222223")
    t2 = login(client, "+919822222224")
    register_device(client, t1, "ble-bp-standard", "SHARED-SERIAL")
    r = client.post("/v1/devices", json={"modelCode": "ble-bp-standard", "serial": "SHARED-SERIAL"}, headers=auth(t2))
    assert r.status_code == 409


def test_oversized_batch_is_refused(client):
    t, wear, _ = setup_patient(client)
    items = [reading(f"x{i}", "hr", wear["id"], now() - timedelta(minutes=i + 1), value=70) for i in range(101)]
    r = client.post("/v1/measurements:batch", json={"measurements": items}, headers=auth(t))
    assert r.status_code == 422


def test_sync_error_notifies_patient_and_opted_in_family(client):
    t, wear, cuff = setup_patient(client)
    fam = login(client, "+919822222225", "caregiver", "Rohan")
    g = client.post(
        "/v1/grants",
        json={
            "granteeName": "Rohan",
            "granteeContact": "+919822222225",
            "role": "family",
            "vitals": ["bp"],
            "history": "from_now",
            "deviceNotices": True,
        },
        headers=auth(t),
    ).json()
    client.post("/v1/invites/accept", json={"token": g["inviteToken"]}, headers=auth(fam))
    r = client.patch(f"/v1/devices/{cuff['id']}/status", json={"syncError": "Couldn't reach the cuff", "batteryPct": 21}, headers=auth(t))
    assert r.status_code == 200 and r.json()["lastSyncError"]
    mine = client.get("/v1/notifications", headers=auth(t)).json()
    theirs = client.get("/v1/notifications", headers=auth(fam)).json()
    assert any(n["kind"] == "device" for n in mine)
    assert any(n["kind"] == "device" and "not a health alert" in n["body"] for n in theirs)
