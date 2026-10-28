"""ECG snapshots: 20 s 12-lead recordings a paired device takes on its schedule (docs/device-protocol.md).

Kept apart from ECG recordings and in one fixed format (500 Hz, 12 leads in STANDARD_12 order, int16 at 1 µV per
count, 10 000 frames) so they can later be exported as a PTB-XL-style dataset. `research_consent` records whether
the patient had research consent when the snapshot was taken; only those snapshots may ever be exported.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, SmallInteger, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, JSONType, UTCDateTime, utcnow


class EcgSnapshot(Base):
    __tablename__ = "ecg_snapshots"
    __table_args__ = (
        UniqueConstraint("factory_device_id", "seq", name="uq_ecg_snapshots_device_seq"),  # one upload per snapshot
        Index("ix_ecg_snapshots_patient_time", "patient_id", "recorded_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    device_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("devices.id", ondelete="SET NULL"))
    factory_device_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("factory_devices.id", ondelete="SET NULL"))
    seq: Mapped[int] = mapped_column(Integer)  # the device's own snapshot counter
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime)  # first sample, device clock (SNTP)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    duration_s: Mapped[float] = mapped_column(Float)
    sample_rate_hz: Mapped[int] = mapped_column(Integer)
    leads: Mapped[list] = mapped_column(JSONType)
    sample_count: Mapped[int] = mapped_column(Integer)  # frames
    sample_format: Mapped[str] = mapped_column(String(16), default="int16le_uv")
    uv_per_count: Mapped[float] = mapped_column(Float, default=1.0)
    storage_key: Mapped[str] = mapped_column(String(200))
    sha256: Mapped[str] = mapped_column(String(64))
    # Automatic signal checks (no diagnosis): good | acceptable | poor, plus per-lead problems.
    quality: Mapped[str] = mapped_column(String(12))
    quality_detail: Mapped[dict] = mapped_column(JSONType, default=dict)  # {"leadOff": [...], "flat": [...], "saturated": [...]}
    average_hr: Mapped[int | None] = mapped_column(SmallInteger)
    acquisition: Mapped[str] = mapped_column(String(12), default="device")  # device | simulated (test unit)
    trigger: Mapped[str] = mapped_column(String(12), default="scheduled")  # scheduled | live (someone pressed Live)
    firmware: Mapped[str | None] = mapped_column(String(40))
    interval_min: Mapped[int | None] = mapped_column(SmallInteger)  # the schedule it was taken under
    research_consent: Mapped[bool] = mapped_column(Boolean, default=False)


class EcgSnapshotLabel(Base):
    """A linked doctor's reading of a snapshot: SCP-ECG statements (as in PTB-XL) with a likelihood each."""

    __tablename__ = "ecg_snapshot_labels"
    __table_args__ = (UniqueConstraint("snapshot_id", "doctor_id", name="uq_ecg_snapshot_labels_doctor"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ecg_snapshots.id", ondelete="CASCADE"), index=True)
    doctor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    scp_codes: Mapped[dict] = mapped_column(JSONType, default=dict)  # {"NORM": 100, "SR": 0}: code -> likelihood 0-100
    signal_quality: Mapped[str | None] = mapped_column(String(12))  # the doctor's view: good | acceptable | poor
    note: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
