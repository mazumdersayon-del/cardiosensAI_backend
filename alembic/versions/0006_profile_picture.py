"""profile pictures

- users.avatar_key: storage key of the account's profile picture (JPEG or PNG, at most 512 KB; the web app
  resizes it to 320 px before upload). users.avatar_updated_at: when it last changed (cache key for clients).

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-28 21:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("avatar_key", sa.String(length=200), nullable=True))
    op.add_column("users", sa.Column("avatar_updated_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "avatar_updated_at")
    op.drop_column("users", "avatar_key")
