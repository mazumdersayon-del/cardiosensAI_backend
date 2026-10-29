"""Patient-doctor messages (services/messages.py)

- messages: one conversation per linked patient-doctor pair; read_at is set when the other side opens it.
New table only: no existing data changes.

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-29 19:55:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("doctor_id", sa.Uuid(), nullable=False),
        sa.Column("sender_id", sa.Uuid(), nullable=False),
        sa.Column("body", sa.String(length=2000), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["doctor_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["patient_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sender_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_messages_doctor_id", "messages", ["doctor_id"])
    op.create_index("ix_messages_patient_id", "messages", ["patient_id"])
    op.create_index("ix_messages_pair_created", "messages", ["patient_id", "doctor_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_messages_pair_created", table_name="messages")
    op.drop_index("ix_messages_patient_id", table_name="messages")
    op.drop_index("ix_messages_doctor_id", table_name="messages")
    op.drop_table("messages")
