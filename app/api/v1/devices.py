"""Device models (what each model is approved to measure) and the patient's registered devices."""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import RequestMeta, get_current_user, request_meta, require_patient
from app.core.db import get_db, utcnow
from app.core.errors import bad_request, conflict, not_found
from app.models import Device, DeviceModel, User
from app.schemas.health import DeviceIn, DeviceModelOut, DeviceOut, DeviceStatusIn
from app.services import audit
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


@router.get("/device-models", response_model=list[DeviceModelOut], summary="Supported device models and what each one measures")
def list_models(_: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[DeviceModelOut]:
    return [_model_out(m) for m in db.scalars(select(DeviceModel).where(DeviceModel.active.is_(True)).order_by(DeviceModel.name))]


@router.get("/devices", response_model=list[DeviceOut])
def list_devices(user: User = Depends(require_patient), db: Session = Depends(get_db)) -> list[DeviceOut]:
    rows = db.scalars(select(Device).where(Device.patient_id == user.id, Device.status == "active").order_by(Device.registered_at))
    return [device_out(d) for d in rows]


@router.post("/devices", response_model=DeviceOut, status_code=201, summary="Register a device to my account (ownership)")
def register_device(
    body: DeviceIn, user: User = Depends(require_patient), db: Session = Depends(get_db), meta: RequestMeta = Depends(request_meta)
) -> DeviceOut:
    model = db.scalar(select(DeviceModel).where(DeviceModel.code == body.model_code, DeviceModel.active.is_(True)))
    if model is None:
        raise bad_request("unsupported_model", "This device model isn't supported")
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
        db, actor=user, action="device.register", patient_id=user.id, resource_type="device", resource_id=d.id, detail=model.name, meta=meta
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


@router.delete("/devices/{device_id}", status_code=204, summary="Remove a device (its past readings are kept)")
def remove_device(
    device_id: str, user: User = Depends(require_patient), db: Session = Depends(get_db), meta: RequestMeta = Depends(request_meta)
) -> None:
    d = _own_device(db, user, device_id)
    d.status = "retired"
    d.retired_at = utcnow()
    audit.record(db, actor=user, action="device.remove", patient_id=user.id, resource_type="device", resource_id=d.id, meta=meta)
    db.commit()
