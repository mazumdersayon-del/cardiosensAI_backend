"""CardiosensAI's own connected devices (ESP32 12-lead monitors) over MQTT: docs/device-protocol.md.

FactoryDevice is the manufacturing registry (imported by staff from the factory CSV). A user pairs one to their
account (Account → Devices: device ID + serial, then a button press on the device), which creates an ordinary
`devices` row owned by that account plus its DeviceConfig. The bridge (app/devices/bridge.py) carries messages.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Index, Integer, SmallInteger, String, Text, UniqueConstraint, Uuid
from sqlalchemy import false as sa_false
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, UTCDateTime, utcnow
from app.models.device import DeviceModel


class FactoryDevice(Base):
    __tablename__ = "factory_devices"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    device_uid: Mapped[str] = mapped_column(String(64), unique=True)  # MQTT username and topic level
    serial: Mapped[str] = mapped_column(String(64))
    model_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("device_models.id"))
    batch: Mapped[str | None] = mapped_column(String(64))
    # Test units (simulators, bench rigs): their data is stored labelled "simulated" and only accepted where
    # Settings.test_streams_allowed.
    is_test: Mapped[bool] = mapped_column(Boolean, default=False, server_default=sa_false())
    mqtt_password_hash: Mapped[str] = mapped_column(String(200))  # Mosquitto $7$ hash of the derived MQTT password
    sign_key_enc: Mapped[str] = mapped_column(String(200))  # AES-GCM(DEVICE_KEYS_SECRET), bound to device_uid
    status: Mapped[str] = mapped_column(String(12), default="available")  # available | blocked
    blocked_reason: Mapped[str | None] = mapped_column(String(300))
    blocked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    firmware: Mapped[str | None] = mapped_column(String(40))
    last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # Sent with every downlink; the device ignores state older than the newest it has seen.
    state_version: Mapped[int] = mapped_column(Integer, default=0)
    rejected_count: Mapped[int] = mapped_column(Integer, default=0)  # messages refused (bad signature, bad data...)
    last_rejected_code: Mapped[str | None] = mapped_column(String(40))
    last_rejected_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    imported_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    model: Mapped[DeviceModel] = relationship(lazy="joined")


class DevicePairing(Base):
    """A pairing request: the account asked, the device must confirm with a button press before `expires_at`."""

    __tablename__ = "device_pairings"
    __table_args__ = (Index("ix_device_pairings_factory_status", "factory_device_id", "status"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    factory_device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("factory_devices.id", ondelete="CASCADE"))
    requested_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    display_name: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(12), default="pending")  # pending | accepted | expired | cancelled
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    device_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("devices.id", ondelete="SET NULL"))


class DeviceConfig(Base):
    """What a paired device does: snapshot interval, quiet hours, live ECG on request, and (for a doctor's or
    clinic's device) which linked patient it records for."""

    __tablename__ = "device_configs"

    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True)
    factory_device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("factory_devices.id", ondelete="CASCADE"), index=True)
    paired_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    interval_min: Mapped[int] = mapped_column(SmallInteger, default=60)  # 0 = snapshots off
    quiet_start: Mapped[str | None] = mapped_column(String(5))  # "HH:MM" local, no snapshots from here...
    quiet_end: Mapped[str | None] = mapped_column(String(5))  # ...until here
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Kolkata")
    assigned_patient_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    assigned_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    live_until: Mapped[datetime | None] = mapped_column(UTCDateTime)
    live_requested_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    live_session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ecg_live_sessions.id", ondelete="SET NULL"))
    updated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class DeviceOutbox(Base):
    """Messages for devices, written in the API's transaction and published by the bridge (at least once)."""

    __tablename__ = "device_outbox"
    __table_args__ = (Index("ix_device_outbox_unsent", "sent_at", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_uid: Mapped[str] = mapped_column(String(64))
    topic: Mapped[str] = mapped_column(String(120))
    payload: Mapped[str] = mapped_column(Text)  # sealed envelope (ASCII)
    retain: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class DeviceNonce(Base):
    """Message ids seen per device in the last day: a replayed message is refused even inside the time window."""

    __tablename__ = "device_nonces"
    __table_args__ = (
        UniqueConstraint("factory_device_id", "mid", name="uq_device_nonces_mid"),
        Index("ix_device_nonces_seen", "seen_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    factory_device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("factory_devices.id", ondelete="CASCADE"))
    mid: Mapped[str] = mapped_column(String(32))
    seen_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
