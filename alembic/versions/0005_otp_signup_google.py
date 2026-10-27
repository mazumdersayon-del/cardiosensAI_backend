"""sign-up and sign-in with one-time codes (SMS / email), Google sign-in

- otp_challenges: purpose (signup | login | add_email | legacy), channel (sms | email), email, user_id,
  group_id (a sign-up's SMS + email codes) and payload (the pending sign-up). phone becomes optional.
  Existing rows are the old app's phone sign-in codes: purpose "legacy", channel "sms".
- users.phone_verified_at: back-filled with created_at for accounts that have a phone number, because
  until now a phone only got onto an account through an SMS code (app sign-in).
- users.google_sub: the Google account id of accounts that sign in with Google.

Email confirmation links are gone (codes replace them); old unused verify_email action tokens are marked used.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-27 14:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.add_column("otp_challenges", sa.Column("purpose", sa.String(length=16), nullable=False, server_default="legacy"))
    op.add_column("otp_challenges", sa.Column("channel", sa.String(length=8), nullable=False, server_default="sms"))
    op.add_column("otp_challenges", sa.Column("email", sa.String(length=254), nullable=True))
    op.add_column("otp_challenges", sa.Column("user_id", sa.Uuid(), nullable=True))
    op.add_column("otp_challenges", sa.Column("group_id", sa.Uuid(), nullable=True))
    op.add_column("otp_challenges", sa.Column("payload", JSON, nullable=True))
    op.alter_column("otp_challenges", "purpose", server_default=None)
    op.alter_column("otp_challenges", "channel", server_default=None)
    op.alter_column("otp_challenges", "phone", existing_type=sa.String(length=20), nullable=True)
    op.create_foreign_key("fk_otp_challenges_user_id", "otp_challenges", "users", ["user_id"], ["id"], ondelete="CASCADE")
    op.create_index("ix_otp_group", "otp_challenges", ["group_id"], unique=False)

    op.add_column("users", sa.Column("phone_verified_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("google_sub", sa.String(length=64), nullable=True))
    op.create_unique_constraint("uq_users_google_sub", "users", ["google_sub"])
    users = sa.table(
        "users",
        sa.column("phone", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("phone_verified_at", sa.DateTime(timezone=True)),
    )
    op.execute(
        users.update().where(users.c.phone.is_not(None), ~users.c.phone.like("deleted:%")).values(phone_verified_at=users.c.created_at)
    )

    tokens = sa.table("action_tokens", sa.column("purpose", sa.String()), sa.column("used_at", sa.DateTime(timezone=True)))
    op.execute(tokens.update().where(tokens.c.purpose == "verify_email", tokens.c.used_at.is_(None)).values(used_at=sa.func.now()))


def downgrade() -> None:
    op.drop_constraint("uq_users_google_sub", "users", type_="unique")
    op.drop_column("users", "google_sub")
    op.drop_column("users", "phone_verified_at")
    op.drop_index("ix_otp_group", table_name="otp_challenges")
    op.drop_constraint("fk_otp_challenges_user_id", "otp_challenges", type_="foreignkey")
    op.execute("DELETE FROM otp_challenges WHERE phone IS NULL")
    op.alter_column("otp_challenges", "phone", existing_type=sa.String(length=20), nullable=False)
    for col in ("payload", "group_id", "user_id", "email", "channel", "purpose"):
        op.drop_column("otp_challenges", col)
