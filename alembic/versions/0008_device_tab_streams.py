"""Device tab and device streams

- devices.device_uid: the device's own unique id; unique among active devices (partial index uq_devices_active_uid).
  devices.patient_id is the owner: a patient, or a doctor/clinic login for their own equipment.
- ecg_live_sessions.acquisition: "live", or "simulated" for test streams (the recording keeps it).

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-28 23:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ACTIVE_UID = sa.text("status = 'active' AND device_uid IS NOT NULL")


def upgrade() -> None:
    op.add_column("devices", sa.Column("device_uid", sa.String(length=64), nullable=True))
    op.create_index("uq_devices_active_uid", "devices", ["device_uid"], unique=True, postgresql_where=ACTIVE_UID, sqlite_where=ACTIVE_UID)
    op.add_column("ecg_live_sessions", sa.Column("acquisition", sa.String(length=12), nullable=False, server_default="live"))


def downgrade() -> None:
    op.drop_column("ecg_live_sessions", "acquisition")
    op.drop_index("uq_devices_active_uid", table_name="devices")
    op.drop_column("devices", "device_uid")
