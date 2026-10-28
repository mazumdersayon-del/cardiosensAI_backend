import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, UniqueConstraint, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, JSONType, UTCDateTime, utcnow


class DeviceModel(Base):
    """A supported device model and exactly which measurements it is approved to provide."""

    __tablename__ = "device_models"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(64), unique=True)  # e.g. ble-bp-standard
    name: Mapped[str] = mapped_column(String(120))
    manufacturer: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(24))  # wearable | bp_cuff | glucose_meter
    capabilities: Mapped[list] = mapped_column(JSONType, default=list)  # e.g. ["bp"] or ["hr","spo2","ecg"]
    integration: Mapped[str] = mapped_column(String(24))  # ble_standard | ble_vendor | cloud_api
    payload_schema_version: Mapped[str] = mapped_column(String(16), default="1")
    regulatory_note: Mapped[str | None] = mapped_column(String(300))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Device(Base):
    __tablename__ = "devices"
    __table_args__ = (
        # One active owner per physical device (model + serial) — ownership verification.
        Index(
            "uq_devices_active_serial",
            "model_id",
            "serial",
            unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
        UniqueConstraint("patient_id", "client_ref", name="uq_devices_patient_client_ref"),
        Index(
            "uq_devices_active_uid",
            "device_uid",
            unique=True,
            postgresql_where=text("status = 'active' AND device_uid IS NOT NULL"),
            sqlite_where=text("status = 'active' AND device_uid IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    # The owner. Usually the patient whose readings it takes; for a doctor's or clinic's device (Account → Devices) it is
    # that doctor/clinic login, and it records for linked patients through the device stream.
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # The device's own unique id (printed on it / in its firmware); with the serial it identifies the device
    # to the device stream. Unique among active devices.
    device_uid: Mapped[str | None] = mapped_column(String(64))
    model_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("device_models.id"))
    serial: Mapped[str] = mapped_column(String(64))
    # The id the mobile app gave this device locally; lets the app keep sending its own ids.
    client_ref: Mapped[str | None] = mapped_column(String(64))
    display_name: Mapped[str] = mapped_column(String(80))
    ble_identifier: Mapped[str | None] = mapped_column(String(64))
    firmware: Mapped[str | None] = mapped_column(String(40))
    battery_pct: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | retired
    registered_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_sync_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_sync_error: Mapped[str | None] = mapped_column(String(200))
    last_sync_error_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    retired_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    model: Mapped[DeviceModel] = relationship(lazy="joined")
