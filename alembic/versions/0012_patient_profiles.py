"""Patient details and doctors' practice PIN code (services/patient_profile.py, services/pincodes.py)

- patient_profiles: height, weight, sex, blood group, address with PIN code, medical background, reminder snooze.
- professional_profiles.pin_code: practice PIN for the patients' "Near me" doctors list.
New table and one nullable column: no existing data changes.

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-29 20:20:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "patient_profiles",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("height_cm", sa.Float(), nullable=True),
        sa.Column("weight_kg", sa.Float(), nullable=True),
        sa.Column("weight_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sex", sa.String(length=12), nullable=True),
        sa.Column("blood_group", sa.String(length=3), nullable=True),
        sa.Column("address_line1", sa.String(length=160), nullable=True),
        sa.Column("address_line2", sa.String(length=160), nullable=True),
        sa.Column("city", sa.String(length=80), nullable=True),
        sa.Column("district", sa.String(length=80), nullable=True),
        sa.Column("state", sa.String(length=80), nullable=True),
        sa.Column("pin_code", sa.String(length=6), nullable=True),
        sa.Column("share_address", sa.Boolean(), nullable=False),
        sa.Column("conditions", sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"), nullable=False),
        sa.Column("conditions_other", sa.String(length=300), nullable=True),
        sa.Column("allergies", sa.String(length=300), nullable=True),
        sa.Column("medicines", sa.String(length=500), nullable=True),
        sa.Column("smoking", sa.String(length=8), nullable=True),
        sa.Column("reminder_snoozed_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_index("ix_patient_profiles_pin_code", "patient_profiles", ["pin_code"])
    op.add_column("professional_profiles", sa.Column("pin_code", sa.String(length=6), nullable=True))


def downgrade() -> None:
    op.drop_column("professional_profiles", "pin_code")
    op.drop_index("ix_patient_profiles_pin_code", table_name="patient_profiles")
    op.drop_table("patient_profiles")
