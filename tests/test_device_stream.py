"""Account → Devices (register / deregister by device ID + serial, for patients, doctors and clinics) and the device stream."""

import base64
import uuid

from app.core import config
from app.services.ecg_synth import Synth12
from tests.conftest import auth, iso, now
from tests.test_accounts_links import ready_professional, signup

MODEL = "cardiosensai-12lead-dev"


def _register(client, t, uid, serial):
    return client.post(
        "/v1/devices", json={"modelCode": MODEL, "serial": serial, "deviceUid": uid, "displayName": "Ward monitor"}, headers=auth(t)
    )


def _start(client, uid, serial, account, patient=None, simulated=True):
    body = {"deviceUid": uid, "serial": serial, "accountId": account, "patientId": patient, "simulated": simulated}
    return client.post("/v1/device-stream/session", json=body)


def _reading(vital, device_id, **kw):
    t = iso(now())
    return {
        "id": f"s-{vital}-{uuid.uuid4().hex[:12]}",
        "vital": vital,
        "measuredAt": t,
        "receivedAt": t,
        "provenance": {"deviceId": device_id, "acquisition": "simulated", "quality": "good"},
        **kw,
    }


def test_register_and_deregister_by_device_id_and_serial(client):
    p = signup(client, "patient", "dev.p@example.com", "Dev Patient")
    other = signup(client, "patient", "dev.o@example.com", "Other Patient")
    r = _register(client, p, "CS12-TEST-0001", "SN-1")
    assert r.status_code == 201 and r.json()["deviceUid"] == "CS12-TEST-0001" and "ecg" in r.json()["capabilities"]
    assert _register(client, p, "CS12-TEST-0001", "SN-1").status_code == 201  # same device again: same row
    assert len(client.get("/v1/devices", headers=auth(p)).json()) == 1
    assert _register(client, other, "CS12-TEST-0001", "SN-2").json()["error"]["code"] == "device_owned"
    assert _register(client, p, "CS12-TEST-9999", "SN-1").json()["error"]["code"] == "device_uid_mismatch"
    assert _register(client, p, "bad id!", "SN-3").status_code == 422
    dev = client.get("/v1/devices", headers=auth(p)).json()[0]
    assert client.delete(f"/v1/devices/{dev['id']}", headers=auth(p)).status_code == 204
    assert client.get("/v1/devices", headers=auth(p)).json() == []
    assert _register(client, other, "CS12-TEST-0001", "SN-1").status_code == 201  # free again after deregistering


def test_patient_device_streams_readings_and_12_lead_ecg_labelled_simulated(client):
    p = signup(client, "patient", "stream.p@example.com", "Stream Patient")
    dev = _register(client, p, "CS12-TEST-0002", "SN-22").json()
    uid = client.get("/v1/me", headers=auth(p)).json()["uniqueId"]
    assert _start(client, "CS12-TEST-0002", "SN-wrong", uid).status_code == 404
    s = _start(client, "CS12-TEST-0002", "SN-22", uid)
    assert s.status_code == 200, s.text
    tok = {"Authorization": f"Bearer {s.json()['token']}"}

    items = [_reading("hr", dev["id"], unit="bpm", value=74), _reading("bp", dev["id"], unit="mmHg", systolic=120, diastolic=80)]
    items.append(
        _reading(
            "glucose", dev["id"], unit="mg/dL", value=104, glucoseMeta={"sampleType": "capillary whole blood", "sampleLocation": "finger"}
        )
    )
    res = client.post("/v1/device-stream/measurements", json={"measurements": items}, headers=tok).json()["results"]
    assert [r["status"] for r in res] == ["accepted"] * 3, res
    latest = client.get("/v1/patients/me/measurements/latest", headers=auth(p)).json()
    assert "hr" in str(latest) and "simulated" in str(latest)

    synth = Synth12(fs=250, hr=74)
    sess = client.post(
        "/v1/device-stream/ecg-live", json={"deviceId": dev["id"], "leads": list(synth.leads), "sampleRateHz": 250}, headers=tok
    ).json()
    for seq in range(2):
        body = {"seq": seq, "samplesBase64": base64.b64encode(synth.raw(250)).decode(), "averageHr": 74}
        assert client.post(f"/v1/device-stream/ecg-live/{sess['id']}/chunks", json=body, headers=tok).status_code == 200
    assert client.get("/v1/patients/me/ecg-live", headers=auth(p)).json()["live"]["leads"] == list(synth.leads)
    out = client.post(f"/v1/device-stream/ecg-live/{sess['id']}/stop", json={"quality": "good"}, headers=tok).json()
    rec = client.get(f"/v1/ecg-recordings/{out['recordingId']}", headers=auth(p)).json()
    assert rec["lead"] == "12-lead" and rec["durationS"] == 2.0 and rec["acquisition"] == "simulated"

    client.delete(f"/v1/devices/{dev['id']}", headers=auth(p))  # deregistered: the token stops working
    assert client.post("/v1/device-stream/measurements", json={"measurements": items}, headers=tok).status_code == 401


def test_doctor_device_records_only_for_linked_patients(client):
    doc = ready_professional(client, "doctor", "stream.dr@example.com", "Dr. Stream")
    p = signup(client, "patient", "stream.lp@example.com", "Linked Patient")
    stranger = signup(client, "patient", "stream.sp@example.com", "Not Linked")
    assert _register(client, doc, "CS12-TEST-DR01", "SN-DR").status_code == 201
    d_uid = client.get("/v1/me", headers=auth(doc)).json()["uniqueId"]
    p_uid = client.get("/v1/me", headers=auth(p)).json()["uniqueId"]
    s_uid = client.get("/v1/me", headers=auth(stranger)).json()["uniqueId"]
    assert _start(client, "CS12-TEST-DR01", "SN-DR", d_uid).json()["error"]["code"] == "patient_required"
    assert _start(client, "CS12-TEST-DR01", "SN-DR", d_uid, s_uid).status_code == 404  # no link

    link = client.post("/v1/links", json={"targetId": d_uid}, headers=auth(p)).json()
    assert client.post(f"/v1/links/{link['id']}/approve", headers=auth(doc)).status_code == 200
    s = _start(client, "CS12-TEST-DR01", "SN-DR", d_uid, p_uid)
    assert s.status_code == 200 and s.json()["patientName"] == "Linked Patient" and s.json()["ownerRole"] == "doctor"
    dev_id = s.json()["deviceId"]
    tok = {"Authorization": f"Bearer {s.json()['token']}"}
    res = client.post("/v1/device-stream/measurements", json={"measurements": [_reading("spo2", dev_id, unit="%", value=97)]}, headers=tok)
    assert res.json()["results"][0]["status"] == "accepted"
    assert "spo2" in str(client.get("/v1/patients/me/measurements/latest", headers=auth(p)).json())

    client.post(f"/v1/links/{link['id']}/end", headers=auth(p))  # link ended: the stream stops at once
    assert (
        client.post(
            "/v1/device-stream/measurements", json={"measurements": [_reading("spo2", dev_id, unit="%", value=96)]}, headers=tok
        ).status_code
        == 404
    )


def test_test_streams_can_be_switched_off_and_the_normal_api_still_refuses_simulated(client, monkeypatch):
    p = signup(client, "patient", "stream.off@example.com", "Off Patient")
    dev = _register(client, p, "CS12-TEST-0003", "SN-33").json()
    uid = client.get("/v1/me", headers=auth(p)).json()["uniqueId"]
    monkeypatch.setattr(config.get_settings(), "device_test_streams", False)
    assert _start(client, "CS12-TEST-0003", "SN-33", uid).status_code == 403
    res = client.post("/v1/measurements:batch", json={"measurements": [_reading("hr", dev["id"], unit="bpm", value=70)]}, headers=auth(p))
    assert res.json()["results"][0]["status"] == "rejected"  # simulated readings never through the patient API
