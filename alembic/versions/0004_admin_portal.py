"""admin portal: scheduled deletion, activity time, verification notes and licence files

- users.erase_after: an admin-deleted account (status "deleting") is erased after this time.
- users.last_active_at: last sign-in / session refresh, for statistics. Back-filled from the newest
  refresh token of each user.
- professional_profiles.review_note / organizations.review_note: reason given with a rejection or
  revocation, shown to the doctor or clinic.
- verification_files: licence / registration certificates uploaded by doctors and clinics.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-27 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("erase_after", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("professional_profiles", sa.Column("review_note", sa.String(length=300), nullable=True))
    op.add_column("organizations", sa.Column("review_note", sa.String(length=300), nullable=True))

    users = sa.table("users", sa.column("id", sa.Uuid()), sa.column("last_active_at", sa.DateTime(timezone=True)))
    tokens = sa.table("refresh_tokens", sa.column("user_id", sa.Uuid()), sa.column("created_at", sa.DateTime(timezone=True)))
    newest = sa.select(sa.func.max(tokens.c.created_at)).where(tokens.c.user_id == users.c.id).scalar_subquery()
    op.execute(users.update().values(last_active_at=newest))

    op.create_table(
        "verification_files",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("filename", sa.String(length=200), nullable=False),
        sa.Column("content_type", sa.String(length=40), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.String(length=200), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_verification_files_user_id"), "verification_files", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_verification_files_user_id"), table_name="verification_files")
    op.drop_table("verification_files")
    op.drop_column("organizations", "review_note")
    op.drop_column("professional_profiles", "review_note")
    op.drop_column("users", "last_active_at")
    op.drop_column("users", "erase_after")
