"""Doctor appointments: weekly hours, offline periods and bookings (services/appointments.py)

- doctor_schedules: booking settings (on/off, time zone, slot length, window, weekly hours, online / in person, video link).
- doctor_offline_periods: no online bookings ("Go offline" now, or scheduled).
- appointments: requested -> accepted / declined / expired / cancelled -> completed / no_show; one open booking per slot.
New tables only: no existing data changes.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-29 18:10:08.156116
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "appointments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("doctor_id", sa.Uuid(), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=True),
        sa.Column("guest_name", sa.String(length=120), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("mode", sa.String(length=12), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("source", sa.String(length=12), nullable=False),
        sa.Column("reason", sa.String(length=300), nullable=True),
        sa.Column("doctor_note", sa.String(length=300), nullable=True),
        sa.Column("meeting_link", sa.String(length=300), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_by", sa.Uuid(), nullable=True),
        sa.Column("reminder_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["cancelled_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["doctor_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["patient_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_appointments_doctor_start", "appointments", ["doctor_id", "starts_at"], unique=False)
    op.create_index("ix_appointments_patient_start", "appointments", ["patient_id", "starts_at"], unique=False)
    op.create_index(
        "uq_appointments_doctor_open_slot",
        "appointments",
        ["doctor_id", "starts_at"],
        unique=True,
        postgresql_where=sa.text("status IN ('requested', 'accepted')"),
        sqlite_where=sa.text("status IN ('requested', 'accepted')"),
    )
    op.create_table(
        "doctor_offline_periods",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("doctor_id", sa.Uuid(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(length=12), nullable=False),
        sa.Column("reason", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["doctor_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_doctor_offline_periods_doctor_end", "doctor_offline_periods", ["doctor_id", "ends_at"], unique=False)
    op.create_table(
        "doctor_schedules",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("slot_min", sa.Integer(), nullable=False),
        sa.Column("window_days", sa.Integer(), nullable=False),
        sa.Column("weekly_hours", sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"), nullable=False),
        sa.Column("online", sa.Boolean(), nullable=False),
        sa.Column("in_person", sa.Boolean(), nullable=False),
        sa.Column("meeting_link", sa.String(length=300), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )


def downgrade() -> None:
    op.drop_table("doctor_schedules")
    op.drop_index("ix_doctor_offline_periods_doctor_end", table_name="doctor_offline_periods")
    op.drop_table("doctor_offline_periods")
    op.drop_index(
        "uq_appointments_doctor_open_slot",
        table_name="appointments",
        postgresql_where=sa.text("status IN ('requested', 'accepted')"),
        sqlite_where=sa.text("status IN ('requested', 'accepted')"),
    )
    op.drop_index("ix_appointments_patient_start", table_name="appointments")
    op.drop_index("ix_appointments_doctor_start", table_name="appointments")
    op.drop_table("appointments")
