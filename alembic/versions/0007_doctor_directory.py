"""patients' Doctors list

- professional_profiles: listed (doctor opted in to appear in the patients' Doctors list), degrees,
  experience_years, languages, practice, city, about. Shown only for verified, active doctors with TOTP.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-28 22:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMNS = (
    ("degrees", sa.String(length=160)),
    ("experience_years", sa.Integer()),
    ("languages", sa.String(length=160)),
    ("practice", sa.String(length=160)),
    ("city", sa.String(length=80)),
    ("about", sa.String(length=800)),
)


def upgrade() -> None:
    op.add_column("professional_profiles", sa.Column("listed", sa.Boolean(), nullable=False, server_default=sa.false()))
    for name, type_ in COLUMNS:
        op.add_column("professional_profiles", sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    for name, _ in reversed(COLUMNS):
        op.drop_column("professional_profiles", name)
    op.drop_column("professional_profiles", "listed")
