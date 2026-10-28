"""Staff: the factory registry of CardiosensAI devices (/admin/devices). No health data.

Import: a CSV from manufacturing with `device_uid,serial,model_code,secret[,batch,test]`, where `secret` is the
device's 32-byte factory secret as 64 hex characters. The secret itself is never stored or shown again (only the
derived broker-login hash and the encrypted signing key). A file is imported whole or not at all.
Block: a lost, stolen or faulty device loses its broker login and any pairing at once.
"""

import csv
import io
import logging
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.deps import RequestMeta, request_meta, require_admin
from app.core.db import get_db
from app.core.errors import bad_request, not_found
from app.models import Device, DeviceModel, FactoryDevice, User
from app.schemas.fleet import BlockIn, FactoryDeviceOut, ImportErrorOut, ImportOut
from app.services import audit, device_hub

router = APIRouter(prefix="/admin", tags=["admin"])
log = logging.getLogger("cardiosensai.devices")

MAX_CSV_BYTES = 2 * 1024 * 1024
MAX_ROWS = 5000
COLUMNS = {"device_uid", "serial", "model_code", "secret"}


def _out(db: Session, fd: FactoryDevice) -> FactoryDeviceOut:
    owner = db.execute(
        select(User.unique_id, User.role)
        .join(Device, Device.patient_id == User.id)
        .where(Device.factory_device_id == fd.id, Device.status == "active")
    ).first()
    return FactoryDeviceOut(
        id=str(fd.id),
        device_uid=fd.device_uid,
        serial=fd.serial,
        model_code=fd.model.code,
        batch=fd.batch,
        is_test=fd.is_test,
        status=fd.status,
        blocked_reason=fd.blocked_reason,
        firmware=fd.firmware,
        last_seen_at=fd.last_seen_at,
        paired_to=owner[0] if owner else None,
        paired_role=owner[1] if owner else None,
        rejected_count=fd.rejected_count,
        last_rejected_code=fd.last_rejected_code,
        last_rejected_at=fd.last_rejected_at,
        created_at=fd.created_at,
    )


def _sync(db: Session) -> bool:
    try:
        device_hub.sync_broker_logins(db)
        return True
    except OSError:
        log.exception("couldn't write the MQTT password file")
        return False


@router.get("/factory-devices", response_model=list[FactoryDeviceOut], summary="CardiosensAI devices from manufacturing")
def list_factory_devices(
    q: str | None = Query(default=None, max_length=64),
    status: Literal["available", "blocked", "all"] = "all",
    limit: int = Query(default=200, ge=1, le=1000),
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[FactoryDeviceOut]:
    stmt = select(FactoryDevice)
    if status != "all":
        stmt = stmt.where(FactoryDevice.status == status)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(FactoryDevice.device_uid.ilike(like), FactoryDevice.serial.ilike(like), FactoryDevice.batch.ilike(like)))
    return [_out(db, fd) for fd in db.scalars(stmt.order_by(FactoryDevice.created_at.desc()).limit(limit))]


@router.post("/factory-devices/import", response_model=ImportOut, summary="Import a factory CSV (all rows or none)")
def import_factory_devices(
    file: Annotated[UploadFile, File(description="CSV: device_uid,serial,model_code,secret[,batch,test]")],
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    meta: RequestMeta = Depends(request_meta),
) -> ImportOut:
    data = file.file.read(MAX_CSV_BYTES + 1)
    if len(data) > MAX_CSV_BYTES:
        raise bad_request("file_too_large", "The CSV is larger than 2 MB: split the batch")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise bad_request("bad_csv", "The file must be UTF-8 CSV") from exc
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or not {c.strip() for c in reader.fieldnames} >= COLUMNS:
        raise bad_request("bad_csv", "The first line must name the columns: device_uid,serial,model_code,secret (batch and test optional)")
    models = {m.code: m.id for m in db.scalars(select(DeviceModel).where(DeviceModel.active.is_(True)))}
    errors: list[ImportErrorOut] = []
    seen: set[str] = set()
    count = 0
    for line, row in enumerate(reader, start=2):
        if line - 1 > MAX_ROWS:
            raise bad_request("bad_csv", f"At most {MAX_ROWS} devices per file")
        row = {(k or "").strip(): (v or "") for k, v in row.items()}
        uid = row.get("device_uid", "").strip()
        try:
            if uid in seen:
                raise ValueError("device_uid appears twice in the file")
            seen.add(uid)
            device_hub.import_factory_row(db, admin, row, models)  # checks everything before adding anything
            count += 1
        except ValueError as e:
            errors.append(ImportErrorOut(line=line, device_uid=uid or None, error=str(e)))
    if errors or count == 0:
        db.rollback()
        if not errors:
            errors.append(ImportErrorOut(line=0, device_uid=None, error="The file has no devices"))
        return ImportOut(imported=0, errors=errors, broker_updated=False)
    audit.record(db, actor=admin, action="admin.devices_import", detail=f"{count} devices from {(file.filename or 'csv')[:60]}", meta=meta)
    db.commit()
    return ImportOut(imported=count, errors=[], broker_updated=_sync(db))


def _fd(db: Session, fd_id: uuid.UUID) -> FactoryDevice:
    fd = db.get(FactoryDevice, fd_id)
    if fd is None:
        raise not_found("Device")
    return fd


@router.post("/factory-devices/{fd_id}/block", response_model=FactoryDeviceOut, summary="Block a device (lost, stolen, faulty)")
def block_device(
    fd_id: uuid.UUID,
    body: BlockIn,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    meta: RequestMeta = Depends(request_meta),
) -> FactoryDeviceOut:
    fd = _fd(db, fd_id)
    device_hub.block(db, fd, admin, body.reason.strip())
    audit.record(db, actor=admin, action="admin.device_block", resource_type="device", resource_id=fd.id, detail=fd.device_uid, meta=meta)
    db.commit()
    _sync(db)
    return _out(db, fd)


@router.post("/factory-devices/{fd_id}/unblock", response_model=FactoryDeviceOut, summary="Allow a blocked device again")
def unblock_device(
    fd_id: uuid.UUID, admin: User = Depends(require_admin), db: Session = Depends(get_db), meta: RequestMeta = Depends(request_meta)
) -> FactoryDeviceOut:
    fd = _fd(db, fd_id)
    device_hub.unblock(db, fd)
    audit.record(db, actor=admin, action="admin.device_unblock", resource_type="device", resource_id=fd.id, detail=fd.device_uid, meta=meta)
    db.commit()
    _sync(db)
    return _out(db, fd)
