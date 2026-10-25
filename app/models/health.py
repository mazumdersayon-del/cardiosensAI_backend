import uuid
from datetime import datetime

from sqlalchemy import Float, ForeignKey, Index, Integer, SmallInteger, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, JSONType, UTCDateTime, utcnow


class Measurement(Base):
    """One vital-sign reading, exactly as recorded, with its provenance.

    `client_id` is the id the app generated; together with `patient_id` it is the
    idempotency key. `payload_hash` detects an id being reused for different content.
    Times are stored in UTC plus the original UTC offset, so the local time is never lost.
    """

    __tablename__ = "measurements"
    __table_args__ = (
        UniqueConstraint("patient_id", "client_id", name="uq_measurements_patient_client"),
        Index("ix_measurements_patient_vital_time", "patient_id", "vital", "measured_at"),
        Index("ix_measurements_device", "device_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    client_id: Mapped[str] = mapped_column(String(64))
    payload_hash: Mapped[str] = mapped_column(String(64))
    vital: Mapped[str] = mapped_column(String(12))  # hr | spo2 | bp | resp | temp | glucose
    unit: Mapped[str] = mapped_column(String(16))
    value: Mapped[float | None] = mapped_column(Float)
    systolic: Mapped[int | None] = mapped_column(SmallInteger)
    diastolic: Mapped[int | None] = mapped_column(SmallInteger)
    mean_arterial: Mapped[int | None] = mapped_column(SmallInteger)
    pulse_rate: Mapped[int | None] = mapped_column(SmallInteger)
    measured_at: Mapped[datetime] = mapped_column(UTCDateTime)
    utc_offset_min: Mapped[int] = mapped_column(SmallInteger)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime)  # when the phone/gateway got it
    ingested_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)  # when the server stored it
    device_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("devices.id", ondelete="SET NULL"))
    sensor: Mapped[str | None] = mapped_column(String(80))
    acquisition: Mapped[str] = mapped_column(String(12))  # live | delayed | manual | simulated
    quality: Mapped[str] = mapped_column(String(8))  # good | poor | unknown
    quality_flags: Mapped[list] = mapped_column(JSONType, default=list)
    glucose_meta: Mapped[dict | None] = mapped_column(JSONType)
    schema_version: Mapped[str] = mapped_column(String(8), default="1")


class EcgRecording(Base):
    """ECG metadata. The raw samples live in object storage (key + SHA-256), not in the database."""

    __tablename__ = "ecg_recordings"
    __table_args__ = (
        UniqueConstraint("patient_id", "client_id", name="uq_ecg_patient_client"),
        Index("ix_ecg_patient_time", "patient_id", "started_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    client_id: Mapped[str] = mapped_column(String(64))
    device_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("devices.id", ondelete="SET NULL"))
    started_at: Mapped[datetime] = mapped_column(UTCDateTime)
    utc_offset_min: Mapped[int] = mapped_column(SmallInteger)
    lead: Mapped[str] = mapped_column(String(16))
    sample_rate_hz: Mapped[int] = mapped_column(Integer)
    sample_count: Mapped[int] = mapped_column(Integer)
    sample_format: Mapped[str] = mapped_column(String(16), default="int16le_uv")
    mm_per_mv: Mapped[float] = mapped_column(Float)
    mm_per_sec: Mapped[float] = mapped_column(Float)
    average_hr: Mapped[int | None] = mapped_column(SmallInteger)
    poor_quality_segments: Mapped[list] = mapped_column(JSONType, default=list)
    quality: Mapped[str] = mapped_column(String(8))
    acquisition: Mapped[str] = mapped_column(String(12))
    storage_key: Mapped[str] = mapped_column(String(200))
    sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class IngestionRejection(Base):
    """Why readings were refused — for support and monitoring. Holds no health values."""

    __tablename__ = "ingestion_rejections"
    __table_args__ = (Index("ix_rejections_at", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    patient_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    device_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    client_id: Mapped[str | None] = mapped_column(String(64))
    reason_code: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
