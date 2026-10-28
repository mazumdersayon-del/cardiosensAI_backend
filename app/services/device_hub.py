"""CardiosensAI devices: pairing, configuration, snapshots and live ECG over MQTT (docs/device-protocol.md).

The API and the bridge share this module. Everything a device sends arrives through `handle_uplink` (called by
the bridge for each MQTT message); everything for a device is queued in `device_outbox` inside the caller's
transaction and published by the bridge. Device state is always sent whole (retained), so a device that was
offline or rebooted catches up from one message.

Defences against bad or forged data, in order: the broker only lets a device log in with the password derived
from its factory secret and only publish on its own topic; every message is signed with the device's key and
carries a fresh timestamp and a never-reused id; the device must be paired and the patient link live; and every
snapshot must pass the exact format and lead-consistency checks in `ecg_quality`.
"""

import base64
import binascii
import hashlib
import logging
import os
import re
import shlex
import subprocess
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import devicekeys
from app.core.config import get_settings
from app.core.db import utcnow
from app.core.ecg_leads import STANDARD_12
from app.core.errors import ApiError, conflict, forbidden, not_found
from app.core.security import constant_time_equal
from app.core.storage import get_storage
from app.models import (
    Consent,
    Device,
    DeviceConfig,
    DeviceNonce,
    DeviceOutbox,
    DevicePairing,
    EcgLiveSession,
    EcgSnapshot,
    FactoryDevice,
    User,
)
from app.services import audit
from app.services.accounts import can_view_patients
from app.services.authz import active_grant_for
from app.services.ecg import finalize
from app.services.ecg_quality import FRAMES, FS, NLEADS, SNAPSHOT_S, SnapshotRejected, check_live_chunk, check_snapshot
from app.services.notifications import notify

log = logging.getLogger("cardiosensai.devices")

PAIR_WINDOW_S = 120  # the user has 2 minutes to press the device's button
LIVE_REQUEST_S = 60  # "Live" asks for one 20 s recording now; the device must deliver it within a minute
CLOCK_SKEW_S = 5 * 60  # message timestamps must be within 5 minutes of the server clock
MAX_BACKLOG = timedelta(days=7)  # buffered snapshots older than this are refused
MAX_SNAPSHOTS_PER_HOUR = 120  # a device flushing its 48-snapshot buffer stays well under this
INTERVALS = (0, 5, 15, 30, 60, 120, 240, 360, 720, 1440)  # minutes; 0 = off
HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
MID = re.compile(r"^[0-9a-f]{16,32}$")


def up_topic(uid: str) -> str:
    return f"cs/d/{uid}/up"


def state_topic(uid: str) -> str:
    return f"cs/d/{uid}/down/state"


def ack_topic(uid: str) -> str:
    return f"cs/d/{uid}/down/ack"


def _ts(dt: datetime) -> int:
    return int(dt.timestamp())


def _key(fd: FactoryDevice) -> bytes:
    return devicekeys.decrypt_key(fd.sign_key_enc, fd.device_uid)


def _queue(db: Session, fd: FactoryDevice, topic: str, message: dict, retain: bool) -> None:
    body = {"v": 1, "uid": fd.device_uid, "ts": _ts(utcnow()), **message}
    db.add(DeviceOutbox(device_uid=fd.device_uid, topic=topic, payload=devicekeys.seal(_key(fd), body).decode(), retain=retain))


# ---------------------------------------------------------------- lookups


def active_device(db: Session, fd: FactoryDevice) -> Device | None:
    return db.scalar(select(Device).where(Device.factory_device_id == fd.id, Device.status == "active"))


def pending_pairing(db: Session, fd: FactoryDevice, now: datetime | None = None) -> DevicePairing | None:
    now = now or utcnow()
    return db.scalar(
        select(DevicePairing)
        .where(DevicePairing.factory_device_id == fd.id, DevicePairing.status == "pending", DevicePairing.expires_at > now)
        .order_by(DevicePairing.created_at.desc())
    )


def test_units_allowed() -> bool:
    return get_settings().test_streams_allowed


def utc_offset_min(tz: str, at: datetime | None = None) -> int:
    try:
        off = (at or utcnow()).astimezone(ZoneInfo(tz)).utcoffset()
    except ZoneInfoNotFoundError:
        return 0
    return int(off.total_seconds() // 60) if off else 0


def recording_patient(db: Session, device: Device, cfg: DeviceConfig) -> User | None:
    """Whose record this device writes to right now, or None (unassigned, link ended, account not active)."""
    owner = db.get(User, device.patient_id)
    if owner is None or owner.status != "active":
        return None
    if owner.role == "patient":
        return owner
    if cfg.assigned_patient_id is None or not can_view_patients(db, owner):
        return None
    patient = db.get(User, cfg.assigned_patient_id)
    if patient is None or patient.status != "active" or active_grant_for(db, owner, patient.id) is None:
        return None
    return patient


def research_consent(db: Session, patient_id: uuid.UUID) -> bool:
    c = db.scalar(select(Consent).where(Consent.user_id == patient_id, Consent.kind == "research").order_by(Consent.granted_at.desc()))
    return c is not None and c.withdrawn_at is None


# ---------------------------------------------------------------- state sent to the device


def build_state(db: Session, fd: FactoryDevice) -> dict:
    now = utcnow()
    if fd.status == "blocked":
        return {"state": "blocked"}
    device = active_device(db, fd)
    if device is None:
        p = pending_pairing(db, fd, now)
        if p is not None:
            return {"state": "pairing", "pairing": {"id": str(p.id), "expires": _ts(p.expires_at)}}
        return {"state": "unpaired"}
    cfg = db.get(DeviceConfig, device.id)
    patient = recording_patient(db, device, cfg) if cfg else None
    live = cfg is not None and patient is not None and cfg.live_until is not None and cfg.live_until > now
    since = max(cfg.paired_at, cfg.assigned_at or cfg.paired_at) if cfg else now
    return {
        "state": "paired",
        "config": {
            "recording": patient is not None,
            "intervalMin": cfg.interval_min if cfg else 0,
            "quietStart": cfg.quiet_start if cfg else None,
            "quietEnd": cfg.quiet_end if cfg else None,
            "tz": cfg.timezone if cfg else "UTC",
            "utcOffsetMin": utc_offset_min(cfg.timezone) if cfg else 0,
            "snapshotS": SNAPSHOT_S,
            "fs": FS,
            "leads": list(STANDARD_12),
            "uvPerCount": 1,
            "since": _ts(since),
        },
        # Live: record one snapshot now, streaming it second by second while it records (trigger "live").
        "live": {"requested": _ts(cfg.live_until) - LIVE_REQUEST_S, "until": _ts(cfg.live_until), "captureS": SNAPSHOT_S} if live else None,
    }


def queue_state(db: Session, fd: FactoryDevice) -> None:
    fd.state_version += 1
    _queue(db, fd, state_topic(fd.device_uid), {"t": "state", "ver": fd.state_version, **build_state(db, fd)}, retain=True)


def queue_ack(db: Session, fd: FactoryDevice, ref: str | None, ok: bool, code: str | None = None, **extra: object) -> None:
    _queue(db, fd, ack_topic(fd.device_uid), {"t": "ack", "ref": ref, "ok": ok, "code": code, **extra}, retain=False)


# ---------------------------------------------------------------- broker logins


def sync_broker_logins(db: Session) -> Path:
    """Write the Mosquitto password file: the bridge's login plus every device that isn't blocked.

    Mosquitto re-reads it when it changes (systemd path unit on the server, SIGHUP locally). A blocked or unknown
    device simply has no login, so the broker refuses its connection.
    """
    s = get_settings()
    lines = []
    if s.mqtt_password:
        lines.append(f"{s.mqtt_username}:{devicekeys.mosquitto_hash(s.mqtt_password)}")
    for uid, h in db.execute(select(FactoryDevice.device_uid, FactoryDevice.mqtt_password_hash).where(FactoryDevice.status != "blocked")):
        lines.append(f"{uid}:{h}")
    path = Path(s.mqtt_passwd_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not _changed_hashes(path, lines):
        return path  # same logins: leave the file (and Mosquitto) alone
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n")
    os.chmod(tmp, 0o640)
    os.replace(tmp, path)
    if s.mqtt_reload_command:
        subprocess.run(shlex.split(s.mqtt_reload_command), check=False, timeout=10, capture_output=True)  # noqa: S603 - from settings
    return path


def _changed_hashes(path: Path, lines: list[str]) -> bool:
    """Device hashes are fixed per device; the bridge's own is re-salted each time, so compare it by password."""
    current = dict(x.split(":", 1) for x in path.read_text().split())
    if set(current) != {x.split(":", 1)[0] for x in lines}:
        return True
    for line in lines:
        user, h = line.split(":", 1)
        if user == get_settings().mqtt_username:
            if not devicekeys.mosquitto_check(get_settings().mqtt_password, current.get(user, "")):
                return True
        elif current.get(user) != h:
            return True
    return False


# ---------------------------------------------------------------- provisioning (admin CSV)


def import_factory_row(db: Session, admin: User, row: dict[str, str], models: dict[str, uuid.UUID]) -> FactoryDevice:
    uid = (row.get("device_uid") or "").strip()
    serial = (row.get("serial") or "").strip()
    if not devicekeys.UID_RE.fullmatch(uid):
        raise ValueError("device_uid must be 6-64 letters, digits, - or _")
    if not 1 <= len(serial) <= 64:
        raise ValueError("serial is required (at most 64 characters)")
    model_id = models.get((row.get("model_code") or "").strip())
    if model_id is None:
        raise ValueError("unknown model_code")
    secret = devicekeys.parse_secret((row.get("secret") or "").strip())
    if db.scalar(select(FactoryDevice.id).where(FactoryDevice.device_uid == uid)) is not None:
        raise ValueError("device_uid already imported")
    fd = FactoryDevice(
        device_uid=uid,
        serial=serial,
        model_id=model_id,
        batch=(row.get("batch") or "").strip()[:64] or None,
        is_test=(row.get("test") or "").strip().lower() in ("1", "true", "yes"),
        mqtt_password_hash=devicekeys.mosquitto_hash(devicekeys.mqtt_password(secret)),
        sign_key_enc=devicekeys.encrypt_key(devicekeys.sign_key(secret), uid),
        imported_by=admin.id,
    )
    db.add(fd)
    db.flush()
    queue_state(db, fd)
    return fd


def block(db: Session, fd: FactoryDevice, admin: User, reason: str) -> None:
    fd.status, fd.blocked_reason, fd.blocked_at = "blocked", reason, utcnow()
    device = active_device(db, fd)
    if device is not None:
        unpair(db, device, actor=admin, why="blocked")
    db.execute(
        update(DevicePairing).where(DevicePairing.factory_device_id == fd.id, DevicePairing.status == "pending").values(status="cancelled")
    )
    queue_state(db, fd)


def unblock(db: Session, fd: FactoryDevice) -> None:
    fd.status, fd.blocked_reason, fd.blocked_at = "available", None, None
    queue_state(db, fd)


# ---------------------------------------------------------------- pairing (Account → Devices)


NO_SUCH_DEVICE = "No CardiosensAI device matches this device ID and serial number. Check the label on the device"


def request_pairing(db: Session, user: User, uid: str, serial: str, display_name: str | None) -> DevicePairing:
    fd = db.scalar(select(FactoryDevice).where(FactoryDevice.device_uid == uid.strip()))
    if fd is None or not constant_time_equal(fd.serial, serial.strip()):
        raise not_found_device()
    if fd.status == "blocked":
        raise conflict("device_blocked", "This device has been blocked by CardiosensAI. Contact support")
    if fd.is_test and not test_units_allowed():
        raise forbidden("This is a test unit and can't be used on this server")
    device = active_device(db, fd)
    if device is not None:
        if device.patient_id == user.id:
            raise conflict("already_paired", "This device is already paired with your account")
        raise conflict(
            "device_owned",
            "This device is paired with another account. Its owner can remove it, or hold the device's button for 10 seconds to reset it",
        )
    now = utcnow()
    db.execute(
        update(DevicePairing).where(DevicePairing.factory_device_id == fd.id, DevicePairing.status == "pending").values(status="cancelled")
    )
    p = DevicePairing(
        factory_device_id=fd.id,
        requested_by=user.id,
        display_name=(display_name or "").strip() or None,
        expires_at=now + timedelta(seconds=PAIR_WINDOW_S),
    )
    db.add(p)
    db.flush()
    queue_state(db, fd)
    return p


def not_found_device() -> ApiError:
    return ApiError(404, "no_matching_device", NO_SUCH_DEVICE)


def cancel_pairing(db: Session, p: DevicePairing) -> None:
    if p.status == "pending":
        p.status, p.decided_at = "cancelled", utcnow()
        queue_state(db, db.get(FactoryDevice, p.factory_device_id))


def pairing_state(p: DevicePairing) -> str:
    if p.status == "pending" and p.expires_at <= utcnow():
        return "expired"
    return p.status


def _accept_pairing(db: Session, fd: FactoryDevice, pairing_id: str) -> Device:
    try:
        pid = uuid.UUID(pairing_id)
    except (TypeError, ValueError) as exc:
        raise _Reject("bad_pairing", "Unknown pairing") from exc
    p = db.get(DevicePairing, pid, with_for_update=True)
    now = utcnow()
    if p is None or p.factory_device_id != fd.id or p.status != "pending":
        raise _Reject("bad_pairing", "No pairing request is waiting")
    if p.expires_at <= now:
        p.status = "expired"
        raise _Reject("pairing_expired", "The pairing request expired")
    owner = db.get(User, p.requested_by)
    if owner is None or owner.status != "active" or active_device(db, fd) is not None:
        p.status = "cancelled"
        raise _Reject("bad_pairing", "The pairing can't be completed")
    device = Device(
        patient_id=owner.id,
        device_uid=fd.device_uid,
        factory_device_id=fd.id,
        model_id=fd.model_id,
        serial=fd.serial,
        display_name=p.display_name or fd.model.name,
        firmware=fd.firmware,
        last_seen_at=now,
    )
    db.add(device)
    db.flush()
    db.add(DeviceConfig(device_id=device.id, factory_device_id=fd.id, paired_at=now, updated_by=owner.id, updated_at=now))
    p.status, p.decided_at, p.device_id = "accepted", now, device.id
    audit.record(
        db,
        actor=owner,
        action="device.paired",
        patient_id=owner.id if owner.role == "patient" else None,
        resource_type="device",
        resource_id=device.id,
        detail=device.display_name,
    )
    notify(
        db,
        user_id=owner.id,
        kind="device",
        title=f"{device.display_name} is paired",
        body="It now takes ECG snapshots on the schedule set under Account → Devices.",
        link="/settings#devices",
    )
    return device


def unpair(db: Session, device: Device, *, actor: User | None, why: str) -> None:
    """End a pairing: the device stops recording at once; its past snapshots and readings are kept."""
    cfg = db.get(DeviceConfig, device.id)
    if cfg is not None and cfg.live_session_id:
        sess = db.get(EcgLiveSession, cfg.live_session_id)
        if sess is not None and sess.status == "live":
            finalize(db, sess)
    if cfg is not None:
        db.delete(cfg)
    device.status, device.retired_at = "retired", utcnow()
    owner = db.get(User, device.patient_id)
    audit.record(
        db,
        actor=actor,
        action="device.unpaired",
        patient_id=device.patient_id if owner is not None and owner.role == "patient" else None,
        resource_type="device",
        resource_id=device.id,
        detail=f"{device.display_name} · {why}",
    )
    if why in ("reset", "blocked") and owner is not None:
        notify(
            db,
            user_id=owner.id,
            kind="device",
            title=f"{device.display_name} was unpaired",
            body="The device was reset with its button." if why == "reset" else "CardiosensAI blocked this device. Contact support.",
            link="/settings#devices",
        )
    fd = db.get(FactoryDevice, device.factory_device_id) if device.factory_device_id else None
    if fd is not None:
        db.flush()
        queue_state(db, fd)


# ---------------------------------------------------------------- configuration and live ECG


def config_editors(db: Session, device: Device, user: User) -> bool:
    """Owner, or (for a patient's device) a doctor or clinic with an active link to that patient."""
    if device.patient_id == user.id:
        return True
    if user.role not in ("doctor", "clinic"):
        return False
    owner = db.get(User, device.patient_id)
    return owner is not None and owner.role == "patient" and active_grant_for(db, user, owner.id) is not None


def set_config(db: Session, device: Device, cfg: DeviceConfig, user: User, changes: dict) -> None:
    if "interval_min" in changes:
        if changes["interval_min"] not in INTERVALS:
            raise ApiError(400, "bad_interval", "Choose one of the listed intervals")
        cfg.interval_min = changes["interval_min"]
    if "quiet_start" in changes or "quiet_end" in changes:
        qs, qe = changes.get("quiet_start"), changes.get("quiet_end")
        if (qs is None) != (qe is None) or (qs is not None and (not HHMM.fullmatch(qs) or not HHMM.fullmatch(qe) or qs == qe)):
            raise ApiError(400, "bad_quiet_hours", "Quiet hours need a start and a different end time (HH:MM), or neither")
        cfg.quiet_start, cfg.quiet_end = qs, qe
    if "timezone" in changes:
        try:
            ZoneInfo(changes["timezone"])
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ApiError(400, "bad_timezone", "Unknown time zone") from exc
        cfg.timezone = changes["timezone"]
    if "assigned_patient_id" in changes:
        owner = db.get(User, device.patient_id)
        if owner is None or owner.role == "patient" or user.id != owner.id:
            raise forbidden("Only the doctor or clinic that owns this device can assign it to a patient")
        pid = changes["assigned_patient_id"]
        if pid is not None:
            patient = db.get(User, pid)
            if patient is None or patient.role != "patient" or active_grant_for(db, owner, patient.id) is None:
                raise not_found("Linked patient")
        if pid != cfg.assigned_patient_id:
            cfg.assigned_patient_id, cfg.assigned_at = pid, utcnow()
            cfg.live_until = None
    cfg.updated_by, cfg.updated_at = user.id, utcnow()
    fd = db.get(FactoryDevice, cfg.factory_device_id)
    queue_state(db, fd)


def request_live(db: Session, device: Device, cfg: DeviceConfig, user: User, on: bool) -> None:
    if on:
        if recording_patient(db, device, cfg) is None:
            raise conflict("not_recording", "This device isn't recording for a patient right now")
        cfg.live_until, cfg.live_requested_by = utcnow() + timedelta(seconds=LIVE_REQUEST_S), user.id
    else:
        cfg.live_until = None
        if cfg.live_session_id:
            sess = db.get(EcgLiveSession, cfg.live_session_id)
            if sess is not None and sess.status == "live":
                finalize(db, sess)
    queue_state(db, db.get(FactoryDevice, cfg.factory_device_id))


# ---------------------------------------------------------------- messages from devices


class _Reject(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def handle_uplink(db: Session, uid: str, wire: bytes) -> str:
    """Process one MQTT message from device `uid` (the broker login). Returns "ok" or the rejection code.

    Commits. Never raises for bad input: a rejected message is counted on the device and answered with an ack.
    """
    fd = db.scalar(select(FactoryDevice).where(FactoryDevice.device_uid == uid))
    if fd is None:
        log.warning("message from unknown device login %s", uid[:64])
        return "unknown_device"
    ref = None
    try:
        msg = devicekeys.open_sealed(_key(fd), wire)
        ref = msg.get("mid") if isinstance(msg.get("mid"), str) else None
        _check_envelope(db, fd, msg)
        result = _dispatch(db, fd, msg)
    except devicekeys.BadMessage as e:
        db.rollback()
        return _rejected(db, fd, None, e.code, "message signature or format is invalid")
    except (_Reject, SnapshotRejected) as e:
        db.rollback()
        return _rejected(db, fd, ref, e.code, str(e))
    except ApiError as e:
        db.rollback()
        return _rejected(db, fd, ref, e.code, e.message)
    fd.last_seen_at = utcnow()
    if result is not None:
        queue_ack(db, fd, ref, True, None, **result)
    db.commit()
    return "ok"


def _rejected(db: Session, fd: FactoryDevice, ref: str | None, code: str, message: str) -> str:
    fd = db.get(FactoryDevice, fd.id)
    fd.rejected_count += 1
    fd.last_rejected_code, fd.last_rejected_at = code[:40], utcnow()
    queue_ack(db, fd, ref, False, code, message=message[:200])
    db.commit()
    log.info("device %s message rejected: %s", fd.device_uid, code)
    return code


def _check_envelope(db: Session, fd: FactoryDevice, msg: dict) -> None:
    if msg.get("v") != 1 or msg.get("uid") != fd.device_uid:
        raise _Reject("bad_envelope", "Wrong protocol version or device ID")
    ts = msg.get("ts")
    if not isinstance(ts, int) or abs(ts - _ts(utcnow())) > CLOCK_SKEW_S:
        raise _Reject("clock_skew", "The device clock is more than 5 minutes off: sync it (SNTP) and resend")
    mid = msg.get("mid")
    if not isinstance(mid, str) or not MID.fullmatch(mid):
        raise _Reject("bad_envelope", "mid must be 16-32 lowercase hex characters")
    if fd.status == "blocked":
        raise _Reject("device_blocked", "This device is blocked")
    if fd.is_test and not test_units_allowed():
        raise _Reject("test_unit_refused", "Test units aren't accepted on this server")
    db.add(DeviceNonce(factory_device_id=fd.id, mid=mid))
    try:
        db.flush()
    except IntegrityError as exc:
        raise _Reject("replayed", "This message was already received") from exc


def _dispatch(db: Session, fd: FactoryDevice, msg: dict) -> dict | None:
    t = msg.get("t")
    if t == "hello":
        fw = msg.get("fw")
        if isinstance(fw, str):
            fd.firmware = fw[:40]
        queue_state(db, fd)  # the device's starting point: unpaired, pairing, paired + config, or blocked
        return None
    if t == "status":
        device = active_device(db, fd)
        if device is not None:
            device.last_seen_at = utcnow()
            if isinstance(msg.get("battery"), int) and 0 <= msg["battery"] <= 100:
                device.battery_pct = msg["battery"]
            if isinstance(msg.get("fw"), str):
                device.firmware = fd.firmware = msg["fw"][:40]
        return None
    if t == "pair_accept":
        _accept_pairing(db, fd, msg.get("pairing"))
        db.flush()
        queue_state(db, fd)
        return {"result": "paired"}
    if t == "reset":
        device = active_device(db, fd)
        if device is not None:
            unpair(db, device, actor=None, why="reset")
        else:
            queue_state(db, fd)
        return {"result": "reset"}
    if t == "snapshot":
        return _snapshot(db, fd, msg)
    if t == "live":
        _live_chunk(db, fd, msg)
        return None
    if t == "live_end":
        device, cfg, _ = _paired(db, fd)
        if cfg.live_session_id:
            sess = db.get(EcgLiveSession, cfg.live_session_id)
            if sess is not None and sess.status == "live":
                finalize(db, sess)
        return None
    raise _Reject("unknown_type", "Unknown message type")


def _paired(db: Session, fd: FactoryDevice) -> tuple[Device, DeviceConfig, User]:
    device = active_device(db, fd)
    cfg = db.get(DeviceConfig, device.id) if device else None
    if device is None or cfg is None:
        raise _Reject("not_paired", "This device isn't paired with an account")
    patient = recording_patient(db, device, cfg)
    if patient is None:
        raise _Reject("not_recording", "This device isn't assigned to a linked patient")
    return device, cfg, patient


def _decode_samples(value: object) -> bytes:
    if not isinstance(value, str):
        raise _Reject("bad_samples", "samples must be base64 text")
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise _Reject("bad_samples", "samples is not valid base64") from exc


def _snapshot(db: Session, fd: FactoryDevice, msg: dict) -> dict:
    device, cfg, patient = _paired(db, fd)
    seq = msg.get("seq")
    if not isinstance(seq, int) or not 0 <= seq < 2**31:
        raise _Reject("bad_snapshot", "seq must be a non-negative integer")
    if db.scalar(select(EcgSnapshot.id).where(EcgSnapshot.factory_device_id == fd.id, EcgSnapshot.seq == seq)) is not None:
        return {"seq": seq, "duplicate": True}  # already stored: the device may drop it from its buffer
    if msg.get("fs") != FS or msg.get("leads") != list(STANDARD_12) or msg.get("uvPerCount") != 1:
        raise _Reject("bad_format", "Snapshots must be 500 Hz, the 12 standard leads in order, 1 µV per count")
    rec_ms = msg.get("recordedAt")
    if not isinstance(rec_ms, int):
        raise _Reject("bad_snapshot", "recordedAt must be Unix time in milliseconds")
    recorded = datetime.fromtimestamp(rec_ms / 1000, tz=UTC)
    now = utcnow()
    since = max(cfg.paired_at, cfg.assigned_at or cfg.paired_at)
    if recorded > now + timedelta(seconds=CLOCK_SKEW_S):
        raise _Reject("future_snapshot", "recordedAt is in the future")
    if recorded < now - MAX_BACKLOG:
        raise _Reject("too_old", "Snapshots older than 7 days aren't accepted")
    if recorded < since - timedelta(seconds=60):
        raise _Reject("before_pairing", "This snapshot was taken before the device was paired or assigned to this patient")
    window = timedelta(seconds=SNAPSHOT_S)
    overlap = db.scalar(
        select(EcgSnapshot.id).where(
            EcgSnapshot.factory_device_id == fd.id, EcgSnapshot.recorded_at > recorded - window, EcgSnapshot.recorded_at < recorded + window
        )
    )
    if overlap is not None:
        raise _Reject("overlapping", "Another snapshot already covers this time")
    recent = db.scalar(
        select(func.count())
        .select_from(EcgSnapshot)
        .where(EcgSnapshot.factory_device_id == fd.id, EcgSnapshot.received_at > now - timedelta(hours=1))
    )
    if recent >= MAX_SNAPSHOTS_PER_HOUR:
        raise _Reject("rate_limited", "Too many snapshots this hour")
    trigger = msg.get("trigger", "scheduled")
    if trigger not in ("scheduled", "live"):
        raise _Reject("bad_snapshot", 'trigger must be "scheduled" or "live"')
    if trigger == "live" and (cfg.live_until is None or cfg.live_until < now - timedelta(seconds=30)):
        raise _Reject("live_not_requested", "Nobody asked for a live recording (or it timed out)")
    lead_off = msg.get("leadOff") or []
    if not isinstance(lead_off, list) or not all(isinstance(x, str) for x in lead_off):
        raise _Reject("bad_snapshot", "leadOff must be a list of lead names")
    raw = _decode_samples(msg.get("samples"))
    q = check_snapshot(raw, lead_off)
    snap = EcgSnapshot(
        patient_id=patient.id,
        device_id=device.id,
        factory_device_id=fd.id,
        seq=seq,
        recorded_at=recorded,
        received_at=now,
        duration_s=float(SNAPSHOT_S),
        sample_rate_hz=FS,
        leads=list(STANDARD_12),
        sample_count=FRAMES,
        storage_key="",
        sha256=hashlib.sha256(raw).hexdigest(),
        quality=q.quality,
        quality_detail=q.detail,
        average_hr=q.average_hr,
        acquisition="simulated" if fd.is_test else "device",
        trigger=trigger,
        firmware=fd.firmware,
        interval_min=cfg.interval_min,
        research_consent=research_consent(db, patient.id),
    )
    db.add(snap)
    db.flush()
    snap.storage_key = f"snapshots/{patient.id}/{snap.id}.i16"
    get_storage().put(snap.storage_key, raw, "application/octet-stream")
    device.last_seen_at = device.last_sync_at = now
    if trigger == "live":  # the live recording is complete: end its preview and the request
        _end_live(db, cfg)
        queue_state(db, fd)
    return {"seq": seq, "quality": q.quality}


def _end_live(db: Session, cfg: DeviceConfig) -> None:
    """Clear the live request and end its preview session (a device's live preview is never kept: the snapshot is)."""
    cfg.live_until = None
    if cfg.live_session_id:
        sess = db.get(EcgLiveSession, cfg.live_session_id)
        if sess is not None and sess.status == "live":
            finalize(db, sess)


def _live_chunk(db: Session, fd: FactoryDevice, msg: dict) -> None:
    from app.api.v1.ecg import append_chunk, start_session  # the API module owns the live-ECG rules
    from app.schemas.health import EcgLiveChunkIn, EcgLiveStartIn

    device, cfg, patient = _paired(db, fd)
    now = utcnow()
    if cfg.live_until is None or cfg.live_until < now - timedelta(seconds=10):
        ended = db.get(EcgLiveSession, cfg.live_session_id) if cfg.live_session_id else None
        if ended is not None and ended.ended_at is not None and ended.ended_at > now - timedelta(seconds=10):
            return  # a chunk already on its way when the live ECG was stopped: drop it quietly
        raise _Reject("live_not_requested", "Nobody asked for a live ECG (or it timed out)")
    seq = msg.get("seq")
    if not isinstance(seq, int) or not 0 <= seq <= 100_000:
        raise _Reject("bad_live", "seq must be a non-negative integer")
    raw = _decode_samples(msg.get("samples"))
    if not raw or len(raw) % (2 * NLEADS) or len(raw) // (2 * NLEADS) > 2 * FS:
        raise _Reject("bad_live", "A live chunk is up to 2 s of 12-lead int16 frames")
    check_live_chunk(raw)
    sess = db.get(EcgLiveSession, cfg.live_session_id) if cfg.live_session_id else None
    if sess is not None and sess.status == "live" and seq > 0 and sess.frames + len(raw) // (2 * NLEADS) > (SNAPSHOT_S + 5) * FS:
        raise _Reject("bad_live", "A live recording is 20 seconds")
    if sess is None or sess.status != "live" or seq == 0 and sess.frames > 0:
        sess = start_session(
            db,
            patient,
            device,
            EcgLiveStartIn(device_id=str(device.id), leads=list(STANDARD_12), sample_rate_hz=FS),
            acquisition="simulated" if fd.is_test else "live",
        )
        cfg.live_session_id = sess.id
    hr = msg.get("hr") if isinstance(msg.get("hr"), int) and 20 <= msg["hr"] <= 300 else None
    append_chunk(db, sess, EcgLiveChunkIn(seq=seq, samples_base64=base64.b64encode(raw).decode(), average_hr=hr))


# ---------------------------------------------------------------- housekeeping (bridge, every few seconds)


def housekeeping(db: Session) -> None:
    """Expire pairings and finished live requests (and tell the devices), drop old replay ids."""
    now = utcnow()
    for p in db.scalars(select(DevicePairing).where(DevicePairing.status == "pending", DevicePairing.expires_at <= now)):
        p.status = "expired"
        queue_state(db, db.get(FactoryDevice, p.factory_device_id))
    for cfg in db.scalars(select(DeviceConfig).where(DeviceConfig.live_until.is_not(None), DeviceConfig.live_until <= now)):
        cfg.live_until = None
        if cfg.live_session_id:
            sess = db.get(EcgLiveSession, cfg.live_session_id)
            if sess is not None and sess.status == "live":
                finalize(db, sess)
        queue_state(db, db.get(FactoryDevice, cfg.factory_device_id))
    db.execute(delete(DeviceNonce).where(DeviceNonce.seen_at < now - timedelta(days=1)))
    db.execute(delete(DeviceOutbox).where(DeviceOutbox.sent_at.is_not(None), DeviceOutbox.sent_at < now - timedelta(days=1)))
    db.commit()
