"""CardiosensAI devices over MQTT (docs/device-protocol.md): factory import, pairing by button press, signed and
replay-proof messages, ECG snapshots and their checks, configuration, doctor devices, live ECG on request, unpairing.

The broker is not needed: messages go straight into `device_hub.handle_uplink`, exactly as the bridge passes them.
"""

import base64
import hashlib
import io
import os
import time
import uuid

import pytest

from app.core import config, devicekeys
from app.core.db import SessionLocal
from app.models import DeviceOutbox, FactoryDevice
from app.services import device_hub
from app.services.ecg_synth import Synth12
from tests.conftest import auth, make_admin
from tests.test_accounts_links import ready_professional, signup

MODEL = "cardiosensai-12lead-dev"


@pytest.fixture(autouse=True)
def _broker_file(tmp_path, monkeypatch):
    monkeypatch.setattr(config.get_settings(), "mqtt_passwd_file", str(tmp_path / "passwd"))
    monkeypatch.setattr(config.get_settings(), "mqtt_password", "bridge-pass")


class Unit:
    """A device as the firmware behaves: signs every message with the key derived from its factory secret."""

    def __init__(self, uid: str, serial: str):
        self.uid, self.serial = uid, serial
        self.secret = os.urandom(32)
        self.key = devicekeys.sign_key(self.secret)
        self.seq = 0

    def row(self, test: bool = False) -> str:
        return f"{self.uid},{self.serial},{MODEL},{self.secret.hex()},b1,{'1' if test else '0'}\n"

    def wire(self, t: str, ts: int | None = None, key: bytes | None = None, **body) -> bytes:
        msg = {"v": 1, "t": t, "uid": self.uid, "ts": ts if ts is not None else int(time.time()), "mid": uuid.uuid4().hex, **body}
        return devicekeys.seal(key or self.key, msg)

    def deliver(self, wire: bytes) -> str:
        with SessionLocal() as db:
            return device_hub.handle_uplink(db, self.uid, wire)

    def send(self, t: str, **body) -> str:
        return self.deliver(self.wire(t, **body))

    def downlinks(self, kind: str) -> list[dict]:
        with SessionLocal() as db:
            rows = db.query(DeviceOutbox).filter(DeviceOutbox.device_uid == self.uid).order_by(DeviceOutbox.id).all()
            out = [devicekeys.open_sealed(self.key, r.payload.encode()) for r in rows]
        return [m for m in out if m["t"] == kind]

    def state(self) -> dict:
        return self.downlinks("state")[-1]

    def ack(self) -> dict:
        return self.downlinks("ack")[-1]

    def snapshot(self, at_ms: int, raw: bytes | None = None, seq: int | None = None, **extra) -> str:
        if seq is None:
            self.seq += 1
            seq = self.seq
        raw = raw if raw is not None else Synth12(fs=500, hr=72, seed=seq).raw(10_000)
        body = {"seq": seq, "recordedAt": at_ms, "fs": 500, "leads": list(Synth12().leads), "uvPerCount": 1}
        return self.send("snapshot", samples=base64.b64encode(raw).decode(), **body, **extra)


def _import(client, adm, *units: Unit, test: bool = False):
    csv = "device_uid,serial,model_code,secret,batch,test\n" + "".join(u.row(test) for u in units)
    return client.post(
        "/v1/admin/factory-devices/import", files={"file": ("batch.csv", io.BytesIO(csv.encode()), "text/csv")}, headers=auth(adm)
    )


def _pair(client, t, unit: Unit, serial: str | None = None):
    return client.post(
        "/v1/devices/pair", json={"deviceUid": unit.uid, "serial": serial or unit.serial, "displayName": "Home monitor"}, headers=auth(t)
    )


def _paired(client, t, unit: Unit) -> dict:
    p = _pair(client, t, unit)
    assert p.status_code == 201, p.text
    assert unit.send("pair_accept", pairing=unit.state()["pairing"]["id"]) == "ok"
    return client.get(f"/v1/devices/pairings/{p.json()['id']}", headers=auth(t)).json()["device"]


def _ms(offset_s: float = 0) -> int:
    return int((time.time() + offset_s) * 1000)


def test_factory_import_is_staff_only_all_or_nothing_and_keeps_no_secret(client, tmp_path):
    adm = make_admin(client)
    p = signup(client, "patient", "imp.p@example.com", "Imp Patient")
    a, b = Unit("CS12-IMP-0001", "SN-1"), Unit("CS12-IMP-0002", "SN-2")
    assert _import(client, p, a).status_code == 403
    bad = "device_uid,serial,model_code,secret\n" + a.row().rsplit(",", 2)[0] + "\nCS12-IMP-0003,SN-3,nope,00\n"
    res = client.post(
        "/v1/admin/factory-devices/import", files={"file": ("x.csv", io.BytesIO(bad.encode()), "text/csv")}, headers=auth(adm)
    )
    assert res.json()["imported"] == 0 and {e["line"] for e in res.json()["errors"]} == {3}
    res = _import(client, adm, a, b).json()
    assert res == {"imported": 2, "errors": [], "brokerUpdated": True}
    assert _import(client, adm, a).json()["errors"][0]["error"] == "device_uid already imported"
    with SessionLocal() as db:
        fd = db.query(FactoryDevice).filter_by(device_uid=a.uid).one()
        assert a.secret.hex() not in str(vars(fd)) and devicekeys.decrypt_key(fd.sign_key_enc, a.uid) == a.key
    logins = dict(line.split(":", 1) for line in (tmp_path / "passwd").read_text().split())
    assert set(logins) == {"cs-server", a.uid, b.uid}
    _, _, it, salt, h = logins[a.uid].split("$")
    expect = hashlib.pbkdf2_hmac("sha512", devicekeys.mqtt_password(a.secret).encode(), base64.b64decode(salt), int(it), dklen=64)
    assert base64.b64decode(h) == expect  # what Mosquitto will check the device's derived password against
    assert len(client.get("/v1/admin/factory-devices", headers=auth(adm)).json()) == 2


def test_power_on_hello_then_pairing_needs_the_button_press(client):
    adm = make_admin(client)
    unit = Unit("CS12-PAIR-0001", "SN-P1")
    _import(client, adm, unit)
    p = signup(client, "patient", "pair.p@example.com", "Pair Patient")
    other = signup(client, "patient", "pair.o@example.com", "Other Patient")
    assert unit.send("hello", fw="1.0.0") == "ok" and unit.state()["state"] == "unpaired"
    assert _pair(client, p, unit, serial="SN-WRONG").json()["error"]["code"] == "no_matching_device"
    pr = _pair(client, p, unit).json()
    assert pr["status"] == "pending" and unit.state()["state"] == "pairing"
    assert client.get("/v1/devices", headers=auth(p)).json() == []  # nothing until the button is pressed
    assert unit.send("pair_accept", pairing=str(uuid.uuid4())) == "bad_pairing"
    assert unit.send("pair_accept", pairing=unit.state()["pairing"]["id"]) == "ok"
    st = unit.state()
    assert st["state"] == "paired" and st["config"]["recording"] and st["config"]["intervalMin"] == 60 and st["config"]["fs"] == 500
    dev = client.get(f"/v1/devices/pairings/{pr['id']}", headers=auth(p)).json()
    assert dev["status"] == "accepted" and dev["device"]["connected"] and dev["device"]["config"]["canEdit"]
    assert _pair(client, other, unit).json()["error"]["code"] == "device_owned"
    assert _pair(client, p, unit).json()["error"]["code"] == "already_paired"
    assert any(n["title"].endswith("is paired") for n in client.get("/v1/notifications", headers=auth(p)).json())


def test_forged_replayed_and_stale_messages_are_refused(client):
    adm = make_admin(client)
    unit = Unit("CS12-SEC-0001", "SN-S1")
    _import(client, adm, unit)
    w = unit.wire("hello")
    assert unit.deliver(w) == "ok"
    assert unit.deliver(w) == "replayed"
    assert unit.deliver(unit.wire("hello", key=os.urandom(32))) == "bad_signature"
    tampered = bytearray(unit.wire("hello", fw="1.0"))
    tampered[-3] ^= 1
    assert unit.deliver(bytes(tampered)) == "bad_signature"
    assert unit.deliver(b"not a message") == "bad_envelope"
    assert unit.send("hello", ts=int(time.time()) - 900) == "clock_skew"
    assert unit.send("format_disk") == "unknown_type"
    assert unit.send("snapshot", seq=1) == "not_paired"
    with SessionLocal() as db:
        assert db.query(FactoryDevice).filter_by(device_uid=unit.uid).one().rejected_count == 7
    assert unit.ack()["ok"] is False and unit.ack()["code"] == "not_paired"


def test_snapshots_are_checked_stored_apart_and_carry_research_consent(client):
    adm = make_admin(client)
    unit = Unit("CS12-SNAP-0001", "SN-N1")
    _import(client, adm, unit)
    p = signup(client, "patient", "snap.p@example.com", "Snap Patient")
    assert client.post("/v1/me/consents", json={"kind": "research", "version": "1"}, headers=auth(p)).status_code == 201
    _paired(client, p, unit)
    raw = Synth12(fs=500, hr=72, seed=9).raw(10_000)
    assert unit.snapshot(_ms(), raw=raw, seq=7) == "ok" and unit.ack()["quality"] == "good"
    assert unit.snapshot(_ms(), raw=raw, seq=7) == "ok" and unit.ack()["duplicate"] is True  # resent after a lost ack
    assert unit.snapshot(_ms(5)) == "overlapping"
    assert unit.snapshot(_ms(40), raw=os.urandom(240_000)) == "inconsistent_leads"  # garbage from a bugged sender
    assert unit.snapshot(_ms(40), raw=raw[:-24]) == "wrong_length"
    assert unit.snapshot(_ms(-3600)) == "before_pairing"
    assert unit.snapshot(_ms(3600)) == "future_snapshot"
    assert unit.send("snapshot", seq=99, recordedAt=_ms(40), fs=250, leads=["I"], uvPerCount=1, samples="") == "bad_format"
    assert unit.snapshot(_ms(40), leadOff=["V3"]) == "ok"
    snaps = client.get("/v1/patients/me/ecg-snapshots", headers=auth(p)).json()
    assert len(snaps) == 2 and snaps[1]["averageHr"] == 72 and snaps[0]["qualityDetail"] in ({}, {"leadOff": ["V3"]})
    assert client.get("/v1/patients/me/ecg-recordings", headers=auth(p)).json() == []  # kept apart from recordings
    first = snaps[1]
    assert client.get(f"/v1/ecg-snapshots/{first['id']}/samples", headers=auth(p)).content == raw
    with SessionLocal() as db:
        from app.models import EcgSnapshot

        assert all(s.research_consent and s.acquisition == "device" for s in db.query(EcgSnapshot).all())


def test_config_by_owner_or_linked_doctor_and_doctor_labels(client):
    adm = make_admin(client)
    unit = Unit("CS12-CFG-0001", "SN-C1")
    _import(client, adm, unit)
    p = signup(client, "patient", "cfg.p@example.com", "Cfg Patient")
    doc = ready_professional(client, "doctor", "cfg.dr@example.com", "Dr. Cfg")
    stranger = ready_professional(client, "doctor", "cfg.x@example.com", "Dr. Stranger")
    dev = _paired(client, p, unit)
    d_uid = client.get("/v1/me", headers=auth(doc)).json()["uniqueId"]
    link = client.post("/v1/links", json={"targetId": d_uid}, headers=auth(p)).json()
    assert client.post(f"/v1/links/{link['id']}/approve", headers=auth(doc)).status_code == 200

    cfg = {"intervalMin": 15, "quietStart": "23:00", "quietEnd": "06:00", "timezone": "Asia/Kolkata"}
    r = client.put(f"/v1/devices/{dev['id']}/config", json=cfg, headers=auth(p))
    assert r.status_code == 200 and r.json()["config"]["intervalMin"] == 15
    st = unit.state()["config"]
    assert (st["intervalMin"], st["quietStart"], st["quietEnd"], st["utcOffsetMin"]) == (15, "23:00", "06:00", 330)
    assert client.put(f"/v1/devices/{dev['id']}/config", json={"intervalMin": 7}, headers=auth(p)).json()["error"]["code"] == "bad_interval"
    assert client.put(f"/v1/devices/{dev['id']}/config", json={"quietStart": "23:00"}, headers=auth(p)).status_code == 400
    pid = client.get("/v1/me", headers=auth(p)).json()["id"]
    seen = client.get(f"/v1/patients/{pid}/devices", headers=auth(doc)).json()
    assert seen[0]["config"]["canEdit"] and not seen[0]["config"]["canAssign"]
    assert client.put(f"/v1/devices/{dev['id']}/config", json={"intervalMin": 30}, headers=auth(doc)).status_code == 200
    assert client.put(f"/v1/devices/{dev['id']}/config", json={"intervalMin": 5}, headers=auth(stranger)).status_code == 404
    assert any(a["action"] == "device.config" for a in client.get("/v1/me/access-log", headers=auth(p)).json())

    assert unit.snapshot(_ms()) == "ok"
    snap = client.get(f"/v1/patients/{pid}/ecg-snapshots", headers=auth(doc)).json()[0]
    label = {"scpCodes": {"NORM": 100, "SR": 0}, "signalQuality": "good"}
    r = client.put(f"/v1/ecg-snapshots/{snap['id']}/label", json=label, headers=auth(doc))
    assert r.status_code == 200 and r.json()["labels"][0]["scpCodes"] == {"NORM": 100, "SR": 0}
    assert client.put(f"/v1/ecg-snapshots/{snap['id']}/label", json=label, headers=auth(p)).status_code == 403
    assert client.put(f"/v1/ecg-snapshots/{snap['id']}/label", json={"scpCodes": {"MADEUP": 50}}, headers=auth(doc)).status_code == 400
    assert client.get(f"/v1/ecg-snapshots/{snap['id']}", headers=auth(stranger)).status_code == 404
    assert len(client.get("/v1/ecg-snapshots/scp-codes", headers=auth(doc)).json()) > 60


def test_doctor_device_records_only_for_the_assigned_linked_patient(client):
    adm = make_admin(client)
    unit = Unit("CS12-DOC-0001", "SN-D1")
    _import(client, adm, unit)
    doc = ready_professional(client, "doctor", "ddev.dr@example.com", "Dr. Device")
    p = signup(client, "patient", "ddev.p@example.com", "Linked Patient")
    q = signup(client, "patient", "ddev.q@example.com", "Unlinked Patient")
    dev = _paired(client, doc, unit)
    assert unit.state()["config"]["recording"] is False
    assert unit.snapshot(_ms()) == "not_recording"
    q_id = client.get("/v1/me", headers=auth(q)).json()["id"]
    assert client.put(f"/v1/devices/{dev['id']}/config", json={"assignedPatientId": q_id}, headers=auth(doc)).status_code == 404
    d_uid = client.get("/v1/me", headers=auth(doc)).json()["uniqueId"]
    link = client.post("/v1/links", json={"targetId": d_uid}, headers=auth(p)).json()
    assert client.post(f"/v1/links/{link['id']}/approve", headers=auth(doc)).status_code == 200
    p_id = client.get("/v1/me", headers=auth(p)).json()["id"]
    r = client.put(f"/v1/devices/{dev['id']}/config", json={"assignedPatientId": p_id}, headers=auth(doc))
    assert r.json()["config"]["recordingFor"] == "Linked Patient" and unit.state()["config"]["recording"] is True
    assert unit.snapshot(_ms()) == "ok"
    assert len(client.get("/v1/patients/me/ecg-snapshots", headers=auth(p)).json()) == 1
    assert client.get("/v1/patients/me/ecg-snapshots", headers=auth(doc)).status_code == 404  # the doctor has none
    client.post(f"/v1/links/{link['id']}/end", headers=auth(p))
    assert unit.snapshot(_ms(30)) == "not_recording"  # link ended: nothing more reaches the patient


def test_live_records_one_snapshot_now(client):
    adm = make_admin(client)
    unit = Unit("CS12-LIVE-0001", "SN-L1")
    _import(client, adm, unit)
    p = signup(client, "patient", "live.p@example.com", "Live Patient")
    dev = _paired(client, p, unit)
    synth = Synth12(fs=500, hr=72, seed=7)
    raw = synth.raw(10_000)
    chunks = [base64.b64encode(raw[k * 12_000 : (k + 1) * 12_000]).decode() for k in range(20)]  # 1 s = 500 frames x 12 x 2 bytes
    assert unit.send("live", seq=0, samples=chunks[0]) == "live_not_requested"
    assert unit.snapshot(_ms(), trigger="live") == "live_not_requested"

    r = client.post(f"/v1/devices/{dev['id']}/live", json={"on": True}, headers=auth(p))
    live = unit.state()["live"]
    assert r.status_code == 200 and r.json()["config"]["liveUntil"] and live["captureS"] == 20 and live["until"] - live["requested"] == 60
    # the device streams the recording as it takes it...
    for seq in range(3):
        assert unit.send("live", seq=seq, samples=chunks[seq], hr=72) == "ok"
    preview = client.get("/v1/patients/me/ecg-live", headers=auth(p)).json()["live"]
    assert preview["leads"][0] == "I" and preview["durationS"] == 3
    bad = bytearray(base64.b64decode(chunks[3]))
    bad[2 * 2 : 2 * 2 + 2] = (12345).to_bytes(2, "little", signed=True)  # frame 0, lead III no longer = II - I
    for k in range(1, 6):
        off = (k * 12 + 2) * 2
        bad[off : off + 2] = (12345).to_bytes(2, "little", signed=True)
    assert unit.send("live", seq=3, samples=base64.b64encode(bytes(bad)).decode()) == "inconsistent_leads"
    # ...then uploads it whole as a snapshot, which ends the request and the preview
    assert unit.snapshot(_ms(-20), raw=raw, trigger="live") == "ok"
    assert unit.state()["live"] is None
    assert client.get("/v1/patients/me/ecg-live", headers=auth(p)).json()["live"] is None
    assert client.get("/v1/patients/me/ecg-recordings", headers=auth(p)).json() == []  # the preview isn't kept twice
    snaps = client.get("/v1/patients/me/ecg-snapshots", headers=auth(p)).json()
    assert [s["trigger"] for s in snaps] == ["live"]
    assert unit.send("live", seq=4, samples=chunks[4]) == "ok"  # already in flight: dropped quietly

    # stopping a request before it's delivered
    client.post(f"/v1/devices/{dev['id']}/live", json={"on": True}, headers=auth(p))
    assert client.post(f"/v1/devices/{dev['id']}/live", json={"on": False}, headers=auth(p)).status_code == 200
    assert unit.state()["live"] is None


def test_unpair_by_owner_reset_button_and_staff_block(client, tmp_path):
    adm = make_admin(client)
    a, b = Unit("CS12-OFF-0001", "SN-O1"), Unit("CS12-OFF-0002", "SN-O2")
    _import(client, adm, a, b)
    p = signup(client, "patient", "off.p@example.com", "Off Patient")
    dev = _paired(client, p, a)
    assert client.delete(f"/v1/devices/{dev['id']}", headers=auth(p)).status_code == 204
    assert a.state()["state"] == "unpaired" and a.snapshot(_ms()) == "not_paired"

    _paired(client, p, a)
    assert a.send("reset") == "ok" and a.state()["state"] == "unpaired"
    assert client.get("/v1/devices", headers=auth(p)).json() == []
    assert any("unpaired" in n["title"] for n in client.get("/v1/notifications", headers=auth(p)).json())

    _paired(client, p, b)
    fd = next(x for x in client.get("/v1/admin/factory-devices", headers=auth(adm)).json() if x["deviceUid"] == b.uid)
    assert fd["pairedRole"] == "patient"
    r = client.post(f"/v1/admin/factory-devices/{fd['id']}/block", json={"reason": "reported stolen"}, headers=auth(adm))
    assert r.json()["status"] == "blocked" and r.json()["pairedTo"] is None
    assert b.state()["state"] == "blocked" and b.uid not in (tmp_path / "passwd").read_text()
    assert _pair(client, p, b).json()["error"]["code"] == "device_blocked"
    assert b.send("hello") == "device_blocked"
    client.post(f"/v1/admin/factory-devices/{fd['id']}/unblock", headers=auth(adm))
    assert b.uid in (tmp_path / "passwd").read_text()


def test_test_units_only_where_test_streams_are_on(client, monkeypatch):
    adm = make_admin(client)
    unit = Unit("CS12-TST-0001", "SN-T1")
    _import(client, adm, unit, test=True)
    p = signup(client, "patient", "tst.p@example.com", "Test Patient")
    _paired(client, p, unit)
    assert unit.snapshot(_ms()) == "ok"
    assert client.get("/v1/patients/me/ecg-snapshots", headers=auth(p)).json()[0]["acquisition"] == "simulated"
    monkeypatch.setattr(config.get_settings(), "device_test_streams", False)
    assert unit.snapshot(_ms(30)) == "test_unit_refused"
    other = Unit("CS12-TST-0002", "SN-T2")
    _import(client, adm, other, test=True)
    assert _pair(client, p, other).status_code == 403


def test_account_deletion_unpairs_and_erases_snapshots(client):
    adm = make_admin(client)
    unit = Unit("CS12-DEL-0001", "SN-X1")
    _import(client, adm, unit)
    p = signup(client, "patient", "del.p@example.com", "Del Patient")
    _paired(client, p, unit)
    assert unit.snapshot(_ms()) == "ok"
    with SessionLocal() as db:
        from app.models import EcgSnapshot

        key = db.query(EcgSnapshot).one().storage_key
    assert client.delete("/v1/me", headers=auth(p)).status_code in (200, 204)
    assert unit.state()["state"] == "unpaired" and unit.snapshot(_ms(30)) == "not_paired"
    with SessionLocal() as db:
        from app.models import EcgSnapshot

        assert db.query(EcgSnapshot).count() == 0
    from app.core.storage import get_storage

    with pytest.raises(FileNotFoundError):
        get_storage().get(key)
