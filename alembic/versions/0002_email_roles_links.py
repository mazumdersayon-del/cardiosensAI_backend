"""email + password sign-in, clinic organisations, 16-digit IDs, care links

Keeps every existing account and its data:
- every user gets a 16-digit ID (role digit + 14 random + Luhn check digit);
- "caregiver" accounts become "patient" accounts (their existing sharing grants keep working);
- phone becomes optional, email becomes unique (lower-cased; a later duplicate is cleared and logged);
- existing users have no password. An admin sets their email in the admin console, which sends a
  set-password link (POST /v1/admin/users/{id}/email). Phone sign-in keeps working for the app.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-26 15:00:00
"""

import secrets
import uuid
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PREFIX = {"patient": "1", "doctor": "2", "clinic": "3", "admin": "9"}


def _luhn(body: str) -> str:
    total = 0
    for i, ch in enumerate(reversed(body)):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return str((10 - total % 10) % 10)


def _new_id(role: str, taken: set[str]) -> str:
    while True:
        body = PREFIX.get(role, "1") + f"{secrets.randbelow(10**14):014d}"
        uid = body + _luhn(body)
        if uid not in taken:
            taken.add(uid)
            return uid


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("unique_id", sa.String(length=16), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("address", sa.String(length=300), nullable=True),
        sa.Column("city", sa.String(length=80), nullable=True),
        sa.Column("registration_number", sa.String(length=60), nullable=True),
        sa.Column("phone", sa.String(length=20), nullable=True),
        sa.Column("owner_user_id", sa.Uuid(), nullable=True),
        sa.Column("verification_status", sa.String(length=16), nullable=False),
        sa.Column("verified_by", sa.Uuid(), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="SET NULL", name="fk_organizations_owner"),
        sa.ForeignKeyConstraint(["verified_by"], ["users.id"], name="fk_organizations_verified_by"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("unique_id"),
    )

    op.add_column("users", sa.Column("unique_id", sa.String(length=16), nullable=True))
    op.add_column("users", sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("password_hash", sa.String(length=200), nullable=True))
    op.add_column("users", sa.Column("failed_logins", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("users", sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("organization_id", sa.Uuid(), nullable=True))
    op.create_foreign_key("fk_users_organization", "users", "organizations", ["organization_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_users_organization_id", "users", ["organization_id"])
    op.alter_column("users", "phone", existing_type=sa.String(length=20), nullable=True)

    conn = op.get_bind()
    users = sa.table(
        "users",
        sa.column("id", sa.Uuid()),
        sa.column("role", sa.String()),
        sa.column("email", sa.String()),
        sa.column("unique_id", sa.String()),
        sa.column("patient_code", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )

    # caregiver -> patient (patients need a report code)
    codes = {c for (c,) in conn.execute(sa.select(users.c.patient_code).where(users.c.patient_code.is_not(None)))}
    for (uid,) in conn.execute(sa.select(users.c.id).where(users.c.role == "caregiver")).all():
        code = None
        while code is None or code in codes:
            code = f"CS-P-{uuid.uuid4().int % 10_000_000:07d}"
        codes.add(code)
        conn.execute(users.update().where(users.c.id == uid).values(role="patient", patient_code=code))

    # e-mail: lower-case, keep the oldest account per address
    seen: set[str] = set()
    rows = conn.execute(sa.select(users.c.id, users.c.email).where(users.c.email.is_not(None)).order_by(users.c.created_at)).all()
    for uid, email in rows:
        addr = email.strip().lower()
        if not addr or addr in seen:
            print(f"0002: cleared duplicate email on user {uid}")  # noqa: T201
            conn.execute(users.update().where(users.c.id == uid).values(email=None))
            continue
        seen.add(addr)
        if addr != email:
            conn.execute(users.update().where(users.c.id == uid).values(email=addr))

    taken: set[str] = set()
    for uid, role in conn.execute(sa.select(users.c.id, users.c.role)).all():
        conn.execute(users.update().where(users.c.id == uid).values(unique_id=_new_id(role, taken)))

    op.create_unique_constraint("uq_users_unique_id", "users", ["unique_id"])
    op.create_unique_constraint("uq_users_email", "users", ["email"])

    op.create_table(
        "action_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("purpose", sa.String(length=20), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_action_tokens_user_purpose", "action_tokens", ["user_id", "purpose"])

    op.create_table(
        "care_links",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=True),
        sa.Column("doctor_id", sa.Uuid(), nullable=True),
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column("patient_ok_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("doctor_ok_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("clinic_ok_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("grant_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_by", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(["patient_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["doctor_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["grant_id"], ["access_grants.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["ended_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_links_patient", "care_links", ["patient_id"])
    op.create_index("ix_links_doctor", "care_links", ["doctor_id"])
    op.create_index("ix_links_org", "care_links", ["organization_id"])


def downgrade() -> None:
    op.drop_index("ix_links_org", table_name="care_links")
    op.drop_index("ix_links_doctor", table_name="care_links")
    op.drop_index("ix_links_patient", table_name="care_links")
    op.drop_table("care_links")
    op.drop_index("ix_action_tokens_user_purpose", table_name="action_tokens")
    op.drop_table("action_tokens")
    op.drop_constraint("uq_users_email", "users", type_="unique")
    op.drop_constraint("uq_users_unique_id", "users", type_="unique")
    # Email-only accounts have no phone and can't exist under 0001's NOT NULL phone. Refuse rather than delete people.
    if op.get_bind().execute(sa.text("SELECT count(*) FROM users WHERE phone IS NULL")).scalar():
        raise RuntimeError("Downgrade refused: email-only accounts exist. Restore from the pre-upgrade backup instead.")
    op.alter_column("users", "phone", existing_type=sa.String(length=20), nullable=False)
    op.drop_index("ix_users_organization_id", table_name="users")
    op.drop_constraint("fk_users_organization", "users", type_="foreignkey")
    for col in ("organization_id", "locked_until", "failed_logins", "password_hash", "email_verified_at", "unique_id"):
        op.drop_column("users", col)
    op.drop_table("organizations")
