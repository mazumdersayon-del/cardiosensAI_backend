import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, JSONType, UTCDateTime, utcnow


class OtpChallenge(Base):
    """A one-time code sent by SMS or email (only an HMAC of it is stored).

    purpose "signup": the new account's details wait in `payload` (password already hashed) until the codes
    are entered; sign-up with an email sends two challenges in one `group_id` (the signup id). The account
    row is created only then. purpose "login": `user_id` is the account (None when nobody has that address,
    so the answer looks the same). purpose "add_email": a signed-in account confirming a new address.
    "legacy": the old app's phone sign-in (/otp/*).
    """

    __tablename__ = "otp_challenges"
    __table_args__ = (Index("ix_otp_phone_created", "phone", "created_at"), Index("ix_otp_group", "group_id"))

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    purpose: Mapped[str] = mapped_column(String(16), default="legacy")  # signup | login | add_email | legacy
    channel: Mapped[str] = mapped_column(String(8), default="sms")  # sms | email
    phone: Mapped[str | None] = mapped_column(String(20))
    email: Mapped[str | None] = mapped_column(String(254))
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE", name="fk_otp_challenges_user_id"))
    group_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    payload: Mapped[dict | None] = mapped_column(JSONType)
    code_hash: Mapped[str] = mapped_column(String(64))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    consumed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class RefreshToken(Base):
    """Opaque refresh tokens (hash only). Each login starts a family; reuse of a rotated token revokes the family."""

    __tablename__ = "refresh_tokens"
    __table_args__ = (Index("ix_refresh_family", "family_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    family_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    rotated_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    user_agent: Mapped[str | None] = mapped_column(String(200))


class ActionToken(Base):
    """Single-use emailed tokens (email verification, password reset). Only the SHA-256 is stored."""

    __tablename__ = "action_tokens"
    __table_args__ = (Index("ix_action_tokens_user_purpose", "user_id", "purpose"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    purpose: Mapped[str] = mapped_column(String(20))  # verify_email | reset_password
    email: Mapped[str] = mapped_column(String(254))  # the address the token was sent to
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
