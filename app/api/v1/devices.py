"""Device models (what each model is approved to measure) and devices (Account → Devices).

Two kinds of device:
- Bluetooth devices the mobile app pairs and uploads for (patients; `POST /devices` with the app's `clientRef`).
- CardiosensAI devices connected over the internet (MQTT, docs/device-protocol.md): patients, doctors and clinics
  pair one by device ID + serial number, confirmed by pressing the device's button (`POST /devices/pair`), then set
  its snapshot schedule. A doctor's or clinic's device records for the linked patient it is assigned to. Linked
  doctors and clinics can also change the schedule of a patient's device and ask it for a live ECG.
"""

import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import RequestMeta, get_current_user, request_meta, require_patient
from app.core.db import get_db, utcnow
from app.core.errors import bad_request, conflict, forbidden, not_found
from app.core.ratelimit import limiter
from app.models import Device, DeviceConfig, DeviceModel, DevicePairing, FactoryDevice, User
from app.schemas.fleet import DeviceConfigIn, LiveIn, PairIn, PairingOut
from app.schemas.health import DeviceConfigOut, DeviceIn, DeviceModelOut, DeviceOut, DeviceStatusIn
from app.services import audit, device_hub
from app.services.authz import get_access, resolve_patient_id
from app.services.notifications import notify_patient_and_watchers
from app.services.serializers import device_out

router = APIRouter(tags=["devices"])


def _model_out(m: DeviceModel) -> DeviceModelOut:
    return DeviceModelOut(
        id=str(m.id),
        code=m.code,
        name=m.name,
        manufacturer=m.manufacturer,
        kind=m.kind,
        capabilities=list(m.capabilities),
        integration=m.integration,
        payload_schema_version=m.payload_schema_version,
        regulatory_note=m.regulatory_note,
        active=m.active,
    )


def require_owner(user: User = Depends(get_current_user)) -> User:
    """Patients, doctors and clinics own devices; staff accounts don't."""
    if user.role not in ("patient", "doctor", "clinic"):
        raise forbidden("Devices belong to patient, doctor and clinic accounts")
    return user


def _audit_patient(user: User):
    return user.id if user.role == "patient" else None


@router.get("/device-models", response_model=list[DeviceModelOut], summary="Supported device models and what each one measures")
def list_models(_: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[DeviceModelOut]:
    return [_model_out(m) for m in db.scalars(select(DeviceModel).where(DeviceModel.active.is_(True)).order_by(DeviceModel.name))]


@router.get("/devices", response_model=list[DeviceOut])
def list_devices(user: User = Depends(require_owner), db: Session = Depends(get_db)) -> list[DeviceOut]:
    rows = db.scalars(select(Device).where(Device.patient_id == user.id, Device.status == "active").order_by(Device.registered_at))
    return [full_device_out(db, d, user) for d in rows]


@router.post("/devices", response_model=DeviceOut, status_code=201, summary="Register a Bluetooth device to my account (mobile app)")
def register_device(
    body: DeviceIn, user: User = Depends(require_patient), db: Session = Depends(get_db), meta: RequestMeta = Depends(request_meta)
) -> DeviceOut:
    model = db.scalar(select(DeviceModel).where(DeviceModel.code == body.model_code, DeviceModel.active.is_(True)))
    if model is None:
        raise bad_request("unsupported_model", "This device model isn't supported")
    if model.integration == "mqtt":
        raise bad_request("pair_instead", "CardiosensAI devices are added with their device ID and serial number (Account → Devices)")
    existing = db.scalar(select(Device).where(Device.model_id == model.id, Device.serial == body.serial, Device.status == "active"))
    if existing is not None:
        if existing.patient_id == user.id:
            # Re-pairing the same device: refresh identifiers, keep history.
            existing.client_ref = body.client_ref or existing.client_ref
            existing.ble_identifier = body.ble_identifier or existing.ble_identifier
            existing.firmware = body.firmware or existing.firmware
            db.commit()
            return device_out(existing)
        # Another account owns it. The owner must remove it first (or support transfers ownership).
        raise conflict("device_owned", "This device is registered to another account. Ask its owner to remove it, or contact support.")
    d = Device(
        patient_id=user.id,
        model_id=model.id,
        serial=body.serial,
        client_ref=body.client_ref,
        display_name=body.display_name or model.name,
        ble_identifier=body.ble_identifier,
        firmware=body.firmware,
    )
    db.add(d)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise conflict("device_conflict", "This device or app reference is already registered") from exc
    audit.record(
        db,
        actor=user,
        action="device.register",
        patient_id=_audit_patient(user),
        resource_type="device",
        resource_id=d.id,
        detail=model.name,
        meta=meta,
    )
    db.commit()
    db.refresh(d)
    return device_out(d)


def _own_device(db: Session, user: User, device_id: str) -> Device:
    d = None
    try:
        d = db.get(Device, uuid.UUID(device_id))
    except ValueError:
        d = db.scalar(select(Device).where(Device.patient_id == user.id, Device.client_ref == device_id))
    if d is None or d.patient_id != user.id or d.status != "active":
        raise not_found("Device")
    return d


@router.patch("/devices/{device_id}/status", response_model=DeviceOut, summary="Report battery, firmware and sync outcome from the app")
def update_status(device_id: str, body: DeviceStatusIn, user: User = Depends(require_patient), db: Session = Depends(get_db)) -> DeviceOut:
    d = _own_device(db, user, device_id)
    now = utcnow()
    d.last_seen_at = now
    if body.battery_pct is not None:
        d.battery_pct = body.battery_pct
    if body.firmware:
        d.firmware = body.firmware
    if body.sync_error:
        d.last_sync_error = body.sync_error
        d.last_sync_error_at = now
        notify_patient_and_watchers(
            db,
            patient=user,
            kind="device",
            title_self=f"{d.display_name} didn't sync",
            title_others=f"{user.full_name or 'Patient'}: {d.display_name} didn't sync",
            body="Device issue — not a health alert. Newer readings may still be on the device.",
            link="/devices",
            dedupe_key=f"sync-error:{d.id}:{now:%Y%m%d%H}",
        )
    else:
        d.last_sync_at = body.last_sync_at or now
        d.last_sync_error = None
        d.last_sync_error_at = None
    db.commit()
    return device_out(d)


@router.delete("/devices/{device_id}", status_code=204, summary="Deregister a device (its past readings are kept)")
def remove_device(
    device_id: str, user: User = Depends(require_owner), db: Session = Depends(get_db), meta: RequestMeta = Depends(request_meta)
) -> None:
    d = _own_device(db, user, device_id)
    if d.factory_device_id is not None:
        device_hub.unpair(db, d, actor=user, why="removed by owner")
    else:
        d.status = "retired"
        d.retired_at = utcnow()
        audit.record(
            db, actor=user, action="device.remove", patient_id=_audit_patient(user), resource_type="device", resource_id=d.id, meta=meta
        )
    db.commit()


# ---------------------------------------------------------------- CardiosensAI devices (MQTT)

ONLINE_WITHIN = timedelta(minutes=10)


def full_device_out(db: Session, d: Device, viewer: User) -> DeviceOut:
    out = device_out(d)
    if d.factory_device_id is None:
        return out
    fd = db.get(FactoryDevice, d.factory_device_id)
    cfg = db.get(DeviceConfig, d.id)
    out.connected = True
    out.online = bool(fd and fd.last_seen_at and fd.last_seen_at > utcnow() - ONLINE_WITHIN)
    if cfg is not None:
        patient = device_hub.recording_patient(db, d, cfg)
        assigned = db.get(User, cfg.assigned_patient_id) if cfg.assigned_patient_id else None
        owner_is_pro = viewer.id == d.patient_id and viewer.role in ("doctor", "clinic")
        out.config = DeviceConfigOut(
            interval_min=cfg.interval_min,
            intervals=list(device_hub.INTERVALS),
            quiet_start=cfg.quiet_start,
            quiet_end=cfg.quiet_end,
            timezone=cfg.timezone,
            paired_at=cfg.paired_at,
            assigned_patient_id=str(assigned.id) if assigned and owner_is_pro else None,
            assigned_patient_name=(assigned.full_name or "Patient") if assigned and owner_is_pro else None,
            recording=patient is not None,
            recording_for=(patient.full_name or "Patient") if patient is not None else None,
            live_until=cfg.live_until if cfg.live_until and cfg.live_until > utcnow() else None,
            can_edit=device_hub.config_editors(db, d, viewer),
            can_assign=owner_is_pro,
        )
    return out


def _pairing_out(db: Session, p: DevicePairing, user: User) -> PairingOut:
    fd = db.get(FactoryDevice, p.factory_device_id)
    device = db.get(Device, p.device_id) if p.device_id else None
    return PairingOut(
        id=str(p.id),
        device_uid=fd.device_uid,
        status=device_hub.pairing_state(p),
        expires_at=p.expires_at,
        device=full_device_out(db, device, user) if device is not None else None,
    )


@router.post("/devices/pair", response_model=PairingOut, status_code=201, summary="Pair a CardiosensAI device: then press its button")
def pair_device(
    body: PairIn, user: User = Depends(require_owner), db: Session = Depends(get_db), meta: RequestMeta = Depends(request_meta)
) -> PairingOut:
    limiter.hit(f"pair:{user.id}", 20, 3600)
    p = device_hub.request_pairing(db, user, body.device_uid, body.serial, body.display_name)
    audit.record(
        db,
        actor=user,
        action="device.pair_request",
        patient_id=_audit_patient(user),
        resource_type="device",
        resource_id=p.factory_device_id,
        detail=body.device_uid,
        meta=meta,
    )
    db.commit()
    return _pairing_out(db, p, user)


def _own_pairing(db: Session, user: User, pairing_id: uuid.UUID) -> DevicePairing:
    p = db.get(DevicePairing, pairing_id)
    if p is None or p.requested_by != user.id:
        raise not_found("Pairing")
    return p


@router.get("/devices/pairings/{pairing_id}", response_model=PairingOut, summary="Has the device confirmed the pairing yet?")
def pairing_status(pairing_id: uuid.UUID, user: User = Depends(require_owner), db: Session = Depends(get_db)) -> PairingOut:
    return _pairing_out(db, _own_pairing(db, user, pairing_id), user)


@router.delete("/devices/pairings/{pairing_id}", status_code=204, summary="Cancel a pairing request")
def cancel_pairing(pairing_id: uuid.UUID, user: User = Depends(require_owner), db: Session = Depends(get_db)) -> None:
    device_hub.cancel_pairing(db, _own_pairing(db, user, pairing_id))
    db.commit()


def _connected(db: Session, user: User, device_id: uuid.UUID) -> tuple[Device, DeviceConfig]:
    """A paired CardiosensAI device this user may configure: its owner, or a doctor/clinic linked to its patient."""
    d = db.get(Device, device_id)
    cfg = db.get(DeviceConfig, device_id) if d is not None else None
    if d is None or cfg is None or d.status != "active" or not device_hub.config_editors(db, d, user):
        raise not_found("Device")
    return d, cfg


def _audit_device(db: Session, user: User, d: Device, action: str, detail: str, meta: RequestMeta) -> None:
    owner = db.get(User, d.patient_id)
    audit.record(
        db,
        actor=user,
        action=action,
        patient_id=owner.id if owner is not None and owner.role == "patient" else None,
        resource_type="device",
        resource_id=d.id,
        detail=detail,
        meta=meta,
    )


@router.put("/devices/{device_id}/config", response_model=DeviceOut, summary="Snapshot schedule, quiet hours, assigned patient")
def update_config(
    device_id: uuid.UUID,
    body: DeviceConfigIn,
    user: User = Depends(require_owner),
    db: Session = Depends(get_db),
    meta: RequestMeta = Depends(request_meta),
) -> DeviceOut:
    d, cfg = _connected(db, user, device_id)
    changes = {k: getattr(body, k) for k in body.model_fields_set}
    if "assigned_patient_id" in changes and changes["assigned_patient_id"] is not None:
        try:
            changes["assigned_patient_id"] = uuid.UUID(changes["assigned_patient_id"])
        except ValueError as exc:
            raise not_found("Linked patient") from exc
    if "interval_min" in changes and changes["interval_min"] is None:
        changes.pop("interval_min")
    device_hub.set_config(db, d, cfg, user, changes)
    parts = []
    if "interval_min" in changes:
        parts.append(f"every {cfg.interval_min} min" if cfg.interval_min else "snapshots off")
    if "quiet_start" in changes or "quiet_end" in changes:
        parts.append(f"quiet {cfg.quiet_start}-{cfg.quiet_end}" if cfg.quiet_start else "no quiet hours")
    if "assigned_patient_id" in changes:
        parts.append("assigned patient changed")
    _audit_device(db, user, d, "device.config", "; ".join(parts) or "settings", meta)
    db.commit()
    return full_device_out(db, d, user)


@router.post("/devices/{device_id}/live", response_model=DeviceOut, summary="Ask the device for a live ECG (5 minutes) or stop it")
def device_live(
    device_id: uuid.UUID,
    body: LiveIn,
    user: User = Depends(require_owner),
    db: Session = Depends(get_db),
    meta: RequestMeta = Depends(request_meta),
) -> DeviceOut:
    d, cfg = _connected(db, user, device_id)
    limiter.hit(f"device-live:{d.id}", 60, 3600)
    device_hub.request_live(db, d, cfg, user, body.on)
    _audit_device(db, user, d, "device.live_request" if body.on else "device.live_stop", d.display_name, meta)
    db.commit()
    return full_device_out(db, d, user)


@router.get("/patients/{patient_id}/devices", response_model=list[DeviceOut], summary="CardiosensAI devices recording for this patient")
def patient_devices(patient_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[DeviceOut]:
    pid = resolve_patient_id(user, patient_id)
    get_access(db, user, pid, need="ecg")
    own = select(Device).where(Device.patient_id == pid, Device.status == "active", Device.factory_device_id.is_not(None))
    assigned = (
        select(Device)
        .join(DeviceConfig, DeviceConfig.device_id == Device.id)
        .where(DeviceConfig.assigned_patient_id == pid, Device.status == "active")
    )
    rows = list(db.scalars(own)) + [d for d in db.scalars(assigned) if d.patient_id == user.id]
    return [full_device_out(db, d, user) for d in rows]
