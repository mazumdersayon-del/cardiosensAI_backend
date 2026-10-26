"""12-lead ECG, live ECG sessions, medical documents with comments, and the doctor dashboard summary."""

import base64
import struct
from datetime import timedelta

from app.core.db import SessionLocal, utcnow
from app.models import AuditEvent, EcgLiveSession
from app.services.ecg import STANDARD_12
from app.services.ecg_synth import Synth12
from tests.conftest import auth, iso, login, now, reading, register_device
from tests.test_accounts_links import ready_professional, signup

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64 + b"\xff\xd9"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def patient_with_device(client, addr="p@example.com"):
    p = signup(client, "patient", addr, "Asha Rao")
    dev = register_device(client, p, "cardiosensai-wearable-dev", "WR-77")
    return p, dev


def linked(client, p, role="doctor", addr="d@example.com", name="Dr. Sen"):
    pro = ready_professional(client, role, addr, name)
    link = client.post("/v1/links", json={"targetId": p["user"]["uniqueId"]}, headers=auth(pro)).json()
    assert client.post(f"/v1/links/{link['id']}/approve", headers=auth(p)).json()["status"] == "active"
    return pro


# ---------------------------------------------------------------- 12-lead uploads


def test_12_lead_upload_and_samples(client):
    p, dev = patient_with_device(client)
    raw = Synth12(fs=250).raw(250 * 2)  # 2 s, 12 interleaved leads
    body = {
        "id": "ecg-12-1",
        "deviceId": dev["id"],
        "startedAt": iso(now() - timedelta(minutes=5)),
        "lead": "12-lead",
        "leads": list(STANDARD_12),
        "sampleRateHz": 250,
        "calibration": {"mmPerMv": 10, "mmPerSec": 25},
        "quality": "good",
        "acquisition": "delayed",
        "samplesBase64": base64.b64encode(raw).decode(),
    }
    r = client.post("/v1/ecg-recordings", json=body, headers=auth(p))
    assert r.status_code == 200, r.text
    rec = r.json()["recording"]
    assert rec["leads"] == list(STANDARD_12) and rec["sampleCount"] == 500 and rec["durationS"] == 2.0 and rec["lead"] == "12-lead"
    s = client.get(f"/v1/ecg-recordings/{rec['id']}/samples", headers=auth(p))
    assert s.headers["x-leads"] == ",".join(STANDARD_12) and s.content == raw

    bad = dict(body, id="ecg-12-2", leads=["I", "I"])
    assert client.post("/v1/ecg-recordings", json=bad, headers=auth(p)).json()["error"]["code"] == "invalid_leads"
    bad = dict(body, id="ecg-12-3", samplesBase64=base64.b64encode(raw[:-2]).decode())  # not whole frames
    assert client.post("/v1/ecg-recordings", json=bad, headers=auth(p)).json()["error"]["code"] == "invalid_samples"


# ---------------------------------------------------------------- live ECG


def _chunk(seq, raw):
    return {"seq": seq, "samplesBase64": base64.b64encode(raw).decode(), "averageHr": 72}


def test_live_ecg_stream_view_and_stop(client):
    p, dev = patient_with_device(client)
    doc = linked(client, p)
    pid = p["user"]["id"]
    assert client.get(f"/v1/patients/{pid}/ecg-live", headers=auth(doc)).json() == {"live": None, "lastRecording": None}

    synth = Synth12(fs=250)
    sess = client.post("/v1/ecg-live", json={"deviceId": dev["id"], "leads": list(STANDARD_12), "sampleRateHz": 250}, headers=auth(p))
    assert sess.status_code == 201, sess.text
    sid = sess.json()["id"]
    first, second = synth.raw(250), synth.raw(250)
    assert client.post(f"/v1/ecg-live/{sid}/chunks", json=_chunk(0, first), headers=auth(p)).json()["frames"] == 250
    assert client.post(f"/v1/ecg-live/{sid}/chunks", json=_chunk(1, second), headers=auth(p)).json()["frames"] == 500
    assert client.post(f"/v1/ecg-live/{sid}/chunks", json=_chunk(1, second), headers=auth(p)).json()["frames"] == 500  # retry ignored

    st = client.get(f"/v1/patients/{pid}/ecg-live", headers=auth(doc)).json()
    assert st["live"]["state"] == "live" and st["live"]["frames"] == 500 and st["live"]["averageHr"] == 72
    r = client.get(f"/v1/ecg-live/{sid}/samples", params={"fromFrame": 250}, headers=auth(doc))
    assert r.headers["x-from-frame"] == "250" and r.headers["x-frames"] == "250" and r.content == second
    r = client.get(f"/v1/ecg-live/{sid}/samples", params={"fromFrame": 300}, headers=auth(doc))
    assert r.content == second[50 * 24 :]  # mid-chunk start: 50 frames x 12 leads x 2 bytes skipped

    feed = client.get("/v1/linked-patients", headers=auth(doc)).json()[0]
    assert feed["ecgLive"] == "live"

    stop = client.post(f"/v1/ecg-live/{sid}/stop", json={"quality": "good"}, headers=auth(p)).json()
    assert stop["state"] == "stopped" and stop["recordingId"]
    assert client.get(f"/v1/ecg-live/{sid}/samples", headers=auth(doc)).status_code == 410
    st = client.get(f"/v1/patients/{pid}/ecg-live", headers=auth(doc)).json()
    assert st["live"] is None and st["lastRecording"]["id"] == stop["recordingId"] and st["lastRecording"]["acquisition"] == "live"
    full = client.get(f"/v1/ecg-recordings/{stop['recordingId']}/samples", headers=auth(doc))
    assert full.content == first + second and full.headers["x-leads"] == ",".join(STANDARD_12)
    assert client.post(f"/v1/ecg-live/{sid}/chunks", json=_chunk(2, first), headers=auth(p)).status_code == 409

    db = SessionLocal()
    views = db.query(AuditEvent).filter(AuditEvent.action == "ecg.live_view").count()
    db.close()
    assert views == 1  # many polls, one access-log entry


def test_live_ecg_stalls_then_is_saved_after_two_minutes(client):
    p, dev = patient_with_device(client)
    sess = client.post("/v1/ecg-live", json={"deviceId": dev["id"], "leads": ["I", "II"], "sampleRateHz": 250}, headers=auth(p)).json()
    raw = struct.pack("<500h", *([100] * 500))  # 250 frames x 2 leads
    client.post(f"/v1/ecg-live/{sess['id']}/chunks", json=_chunk(0, raw), headers=auth(p))

    db = SessionLocal()
    s = db.get(EcgLiveSession, __import__("uuid").UUID(sess["id"]))
    s.last_chunk_at = utcnow() - timedelta(seconds=20)
    db.commit()
    assert client.get("/v1/patients/me/ecg-live", headers=auth(p)).json()["live"]["state"] == "stalled"
    s.last_chunk_at = utcnow() - timedelta(minutes=3)
    db.commit()
    db.close()
    st = client.get("/v1/patients/me/ecg-live", headers=auth(p)).json()
    assert st["live"] is None and st["lastRecording"]["leads"] == ["I", "II"] and st["lastRecording"]["sampleCount"] == 250


def test_live_ecg_rules(client):
    p, dev = patient_with_device(client)
    stranger = ready_professional(client, "doctor", "x@example.com", "Dr. X")
    assert (
        client.post("/v1/ecg-live", json={"deviceId": dev["id"], "leads": ["V7"], "sampleRateHz": 250}, headers=auth(p)).status_code == 400
    )
    sess = client.post("/v1/ecg-live", json={"deviceId": dev["id"], "leads": ["II"], "sampleRateHz": 250}, headers=auth(p)).json()
    too_long = struct.pack(f"<{250 * 11}h", *([0] * (250 * 11)))
    assert (
        client.post(f"/v1/ecg-live/{sess['id']}/chunks", json=_chunk(0, too_long), headers=auth(p)).json()["error"]["code"]
        == "chunk_too_long"
    )
    # No link: unrelated doctors can't see that a live ECG exists.
    assert client.get(f"/v1/patients/{p['user']['id']}/ecg-live", headers=auth(stranger)).status_code == 404
    assert client.get(f"/v1/ecg-live/{sess['id']}/samples", headers=auth(stranger)).status_code == 404
    # Starting again ends the previous session.
    client.post(f"/v1/ecg-live/{sess['id']}/chunks", json=_chunk(0, struct.pack("<250h", *([1] * 250))), headers=auth(p))
    again = client.post("/v1/ecg-live", json={"deviceId": dev["id"], "leads": ["II"], "sampleRateHz": 250}, headers=auth(p)).json()
    assert again["id"] != sess["id"]
    assert (
        client.post(f"/v1/ecg-live/{sess['id']}/chunks", json=_chunk(1, struct.pack("<250h", *([1] * 250))), headers=auth(p)).status_code
        == 409
    )


# ---------------------------------------------------------------- documents


def upload(client, tokens, patient="me", files=(("files", ("lab.pdf", PDF, "application/pdf")),), **form):
    data = {"title": "Lipid profile", "category": "lab", **form}
    return client.post(f"/v1/patients/{patient}/documents", data=data, files=list(files), headers=auth(tokens))


def test_patient_uploads_multiple_files_and_doctor_comments(client):
    p, _ = patient_with_device(client)
    pid = p["user"]["id"]
    doc = linked(client, p)
    files = (("files", ("lab report.pdf", PDF, "application/pdf")), ("files", ("../../scan.JPG", JPEG, "image/jpeg")))
    r = upload(client, p, files=files, documentDate="2026-10-20", note="Fasting sample")
    assert r.status_code == 201, r.text
    d = r.json()
    assert [f["contentType"] for f in d["files"]] == ["application/pdf", "image/jpeg"]
    assert [f["filename"] for f in d["files"]] == ["lab report.pdf", "scan.jpg"]  # path stripped, extension from content
    assert d["uploaderRole"] == "patient" and d["canDelete"] and not d["canComment"]

    listed = client.get(f"/v1/patients/{pid}/documents", headers=auth(doc)).json()
    assert listed[0]["id"] == d["id"] and listed[0]["canComment"] and not listed[0]["canDelete"]
    c = client.post(f"/v1/documents/{d['id']}/comments", json={"body": "LDL high, start statin."}, headers=auth(doc))
    assert c.status_code == 201 and c.json()["mine"]
    mine = client.get("/v1/patients/me/documents", headers=auth(p)).json()[0]
    assert mine["comments"][0]["body"] == "LDL high, start statin." and mine["comments"][0]["authorName"] == "Dr. Sen"
    assert client.post(f"/v1/documents/{d['id']}/comments", json={"body": "hi"}, headers=auth(p)).status_code == 403
    assert client.delete(f"/v1/documents/{d['id']}/comments/{c.json()['id']}", headers=auth(p)).status_code == 403
    notes = client.get("/v1/notifications", headers=auth(p)).json()
    assert any("commented" in n["title"] for n in notes)

    link = client.post(f"/v1/documents/{d['id']}/files/{d['files'][1]['id']}/link", headers=auth(doc)).json()
    f = client.get(link["url"])
    assert f.status_code == 200 and f.content == JPEG and f.headers["content-type"] == "image/jpeg"
    assert "sandbox" in f.headers["content-security-policy"]
    assert client.delete(f"/v1/documents/{d['id']}", headers=auth(doc)).status_code == 403  # not the uploader
    assert client.delete(f"/v1/documents/{d['id']}", headers=auth(p)).status_code == 204
    assert client.get(f"/v1/patients/{pid}/documents", headers=auth(doc)).json() == []


def test_doctor_uploads_for_patient_and_clinic_reads(client):
    p, _ = patient_with_device(client)
    pid = p["user"]["id"]
    doc = linked(client, p)
    clinic = linked(client, p, "clinic", "desk@clinic.in", "Ritu Das")
    r = upload(client, doc, patient=pid, title="Discharge summary", category="discharge")
    assert r.status_code == 201 and r.json()["uploaderRole"] == "doctor" and r.json()["uploaderName"] == "Dr. Sen"
    d = r.json()
    seen = client.get(f"/v1/patients/{pid}/documents", headers=auth(clinic)).json()[0]
    assert seen["id"] == d["id"] and not seen["canComment"] and not seen["canDelete"]  # clinics read; doctors comment
    assert client.post(f"/v1/documents/{d['id']}/comments", json={"body": "x"}, headers=auth(clinic)).status_code == 403
    assert client.get("/v1/patients/me/documents", headers=auth(p)).json()[0]["canDelete"]  # the patient can always remove


def test_document_upload_rules(client):
    p, _ = patient_with_device(client)
    assert upload(client, p, files=(("files", ("x.png", PNG, "image/png")),)).json()["error"]["code"] == "unsupported_file"
    fake = (("files", ("report.pdf", b"hello, not a pdf", "application/pdf")),)
    assert upload(client, p, files=fake).json()["error"]["code"] == "unsupported_file"
    assert upload(client, p, category="nonsense").json()["error"]["code"] == "invalid_category"
    eleven = tuple(("files", (f"f{i}.pdf", PDF, "application/pdf")) for i in range(11))
    assert upload(client, p, files=eleven).json()["error"]["code"] == "file_count"

    from app.core import config

    s = config.get_settings()
    old = s.max_document_file_bytes
    s.max_document_file_bytes = 100
    try:
        big = (("files", ("big.pdf", PDF + b"0" * 200, "application/pdf")),)
        assert upload(client, p, files=big).json()["error"]["code"] == "file_too_large"
    finally:
        s.max_document_file_bytes = old


def test_family_and_strangers_cannot_see_documents(client):
    p, _ = patient_with_device(client)
    upload(client, p)
    stranger = ready_professional(client, "doctor", "x@example.com", "Dr. X")
    assert client.get(f"/v1/patients/{p['user']['id']}/documents", headers=auth(stranger)).status_code == 404
    r = client.post(
        "/v1/grants",
        json={"granteeName": "Rohan", "granteeContact": "+919811110000", "role": "family", "vitals": ["hr"], "history": "all"},
        headers=auth(p),
    )
    fam = login(client, "+919811110000", name="Rohan")
    assert client.post("/v1/invites/accept", json={"token": r.json()["inviteToken"]}, headers=auth(fam)).status_code == 200
    assert client.get(f"/v1/patients/{p['user']['id']}/documents", headers=auth(fam)).status_code == 404


# ---------------------------------------------------------------- doctor dashboard summary


def test_dashboard_summary_has_latest_readings_and_ecg(client):
    p, dev = patient_with_device(client)
    items = [
        reading("hr-a", "hr", dev["id"], now() - timedelta(hours=2), value=70),
        reading("hr-b", "hr", dev["id"], now() - timedelta(minutes=10), value=76),
        reading("sp-a", "spo2", dev["id"], now() - timedelta(minutes=10), value=97),
    ]
    assert all(
        x["status"] == "accepted"
        for x in client.post("/v1/measurements:batch", json={"measurements": items}, headers=auth(p)).json()["results"]
    )
    doc = linked(client, p)
    row = client.get("/v1/linked-patients", headers=auth(doc)).json()[0]
    assert row["latest"]["hr"]["value"] == 76 and row["latest"]["spo2"]["value"] == 97 and "bp" not in row["latest"]
    assert row["latestEcg"] is None and row["ecgLive"] is None
    client.get("/v1/linked-patients", headers=auth(doc))
    db = SessionLocal()
    logged = db.query(AuditEvent).filter(AuditEvent.action == "dashboard.summary").count()
    db.close()
    assert logged == 1  # once an hour per viewer, not per refresh
