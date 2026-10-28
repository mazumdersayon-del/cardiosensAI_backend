"""CardiosensAI devices over MQTT: pairing, configuration and ECG snapshots (docs/device-protocol.md)

- factory_devices: manufacturing registry (admin CSV import); broker-login hash + encrypted signing key, never the secret.
- device_pairings: account asks, device confirms with a button press; device_configs: snapshot schedule, quiet hours,
  assigned patient (doctor/clinic devices), live ECG on request; devices.factory_device_id links a paired device.
- device_outbox / device_nonces: messages for the bridge to publish; message ids seen (replay protection).
- ecg_snapshots (+ ecg_snapshot_labels): 20 s 12-lead snapshots, fixed format, research consent at capture; doctors'
  SCP-ECG labels. New tables only: no existing data changes.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-28 23:51:34.094579
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "device_outbox",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("device_uid", sa.String(length=64), nullable=False),
        sa.Column("topic", sa.String(length=120), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("retain", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_device_outbox_unsent", "device_outbox", ["sent_at", "id"], unique=False)
    op.create_table(
        "factory_devices",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("device_uid", sa.String(length=64), nullable=False),
        sa.Column("serial", sa.String(length=64), nullable=False),
        sa.Column("model_id", sa.Uuid(), nullable=False),
        sa.Column("batch", sa.String(length=64), nullable=True),
        sa.Column("is_test", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("mqtt_password_hash", sa.String(length=200), nullable=False),
        sa.Column("sign_key_enc", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("blocked_reason", sa.String(length=300), nullable=True),
        sa.Column("blocked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("firmware", sa.String(length=40), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("rejected_count", sa.Integer(), nullable=False),
        sa.Column("last_rejected_code", sa.String(length=40), nullable=True),
        sa.Column("last_rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("imported_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["imported_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["model_id"],
            ["device_models.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("device_uid"),
    )
    op.create_table(
        "device_nonces",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("factory_device_id", sa.Uuid(), nullable=False),
        sa.Column("mid", sa.String(length=32), nullable=False),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["factory_device_id"], ["factory_devices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("factory_device_id", "mid", name="uq_device_nonces_mid"),
    )
    op.create_index("ix_device_nonces_seen", "device_nonces", ["seen_at"], unique=False)
    op.create_table(
        "device_pairings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("factory_device_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column("display_name", sa.String(length=80), nullable=True),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("device_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["factory_device_id"], ["factory_devices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_device_pairings_factory_status", "device_pairings", ["factory_device_id", "status"], unique=False)
    op.create_table(
        "ecg_snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("device_id", sa.Uuid(), nullable=True),
        sa.Column("factory_device_id", sa.Uuid(), nullable=True),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_s", sa.Float(), nullable=False),
        sa.Column("sample_rate_hz", sa.Integer(), nullable=False),
        sa.Column("leads", sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("sample_format", sa.String(length=16), nullable=False),
        sa.Column("uv_per_count", sa.Float(), nullable=False),
        sa.Column("storage_key", sa.String(length=200), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("quality", sa.String(length=12), nullable=False),
        sa.Column("quality_detail", sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"), nullable=False),
        sa.Column("average_hr", sa.SmallInteger(), nullable=True),
        sa.Column("acquisition", sa.String(length=12), nullable=False),
        sa.Column("trigger", sa.String(length=12), server_default="scheduled", nullable=False),
        sa.Column("firmware", sa.String(length=40), nullable=True),
        sa.Column("interval_min", sa.SmallInteger(), nullable=True),
        sa.Column("research_consent", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["factory_device_id"], ["factory_devices.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["patient_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("factory_device_id", "seq", name="uq_ecg_snapshots_device_seq"),
    )
    op.create_index("ix_ecg_snapshots_patient_time", "ecg_snapshots", ["patient_id", "recorded_at"], unique=False)
    op.create_table(
        "ecg_snapshot_labels",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("doctor_id", sa.Uuid(), nullable=False),
        sa.Column("scp_codes", sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"), nullable=False),
        sa.Column("signal_quality", sa.String(length=12), nullable=True),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["doctor_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["snapshot_id"], ["ecg_snapshots.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("snapshot_id", "doctor_id", name="uq_ecg_snapshot_labels_doctor"),
    )
    op.create_index(op.f("ix_ecg_snapshot_labels_snapshot_id"), "ecg_snapshot_labels", ["snapshot_id"], unique=False)
    op.create_table(
        "device_configs",
        sa.Column("device_id", sa.Uuid(), nullable=False),
        sa.Column("factory_device_id", sa.Uuid(), nullable=False),
        sa.Column("paired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("interval_min", sa.SmallInteger(), nullable=False),
        sa.Column("quiet_start", sa.String(length=5), nullable=True),
        sa.Column("quiet_end", sa.String(length=5), nullable=True),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("assigned_patient_id", sa.Uuid(), nullable=True),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("live_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("live_requested_by", sa.Uuid(), nullable=True),
        sa.Column("live_session_id", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["assigned_patient_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["factory_device_id"], ["factory_devices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["live_requested_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["live_session_id"], ["ecg_live_sessions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("device_id"),
    )
    op.create_index(op.f("ix_device_configs_factory_device_id"), "device_configs", ["factory_device_id"], unique=False)
    op.add_column("devices", sa.Column("factory_device_id", sa.Uuid(), nullable=True))
    op.create_index(op.f("ix_devices_factory_device_id"), "devices", ["factory_device_id"], unique=False)
    op.create_foreign_key("fk_devices_factory_device", "devices", "factory_devices", ["factory_device_id"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    op.drop_constraint("fk_devices_factory_device", "devices", type_="foreignkey")
    op.drop_index(op.f("ix_devices_factory_device_id"), table_name="devices")
    op.drop_column("devices", "factory_device_id")
    op.drop_index(op.f("ix_device_configs_factory_device_id"), table_name="device_configs")
    op.drop_table("device_configs")
    op.drop_index(op.f("ix_ecg_snapshot_labels_snapshot_id"), table_name="ecg_snapshot_labels")
    op.drop_table("ecg_snapshot_labels")
    op.drop_index("ix_ecg_snapshots_patient_time", table_name="ecg_snapshots")
    op.drop_table("ecg_snapshots")
    op.drop_index("ix_device_pairings_factory_status", table_name="device_pairings")
    op.drop_table("device_pairings")
    op.drop_index("ix_device_nonces_seen", table_name="device_nonces")
    op.drop_table("device_nonces")
    op.drop_table("factory_devices")
    op.drop_index("ix_device_outbox_unsent", table_name="device_outbox")
    op.drop_table("device_outbox")
