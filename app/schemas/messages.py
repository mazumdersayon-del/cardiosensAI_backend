from datetime import datetime
from typing import Literal

from pydantic import Field

from app.schemas.common import CamelModel


class PartnerOut(CamelModel):
    user_id: str = Field(description="Use as {party} in /v1/conversations/{party}")
    unique_id: str | None
    full_name: str
    role: Literal["patient", "doctor"]


class MessageOut(CamelModel):
    id: str
    mine: bool
    body: str
    created_at: datetime
    read_at: datetime | None = Field(description="When the other side opened it")


class ConversationOut(CamelModel):
    partner: PartnerOut
    can_send: bool = Field(description="False once the link has ended: history only")
    unread: int
    last_at: datetime | None
    last_preview: str | None = Field(description="Start of the newest message")
    last_mine: bool = False


class ThreadOut(CamelModel):
    partner: PartnerOut
    can_send: bool
    messages: list[MessageOut] = Field(description="Oldest first; up to 100 (use ?before= for older)")


class MessageIn(CamelModel):
    body: str = Field(min_length=1, max_length=2000)


class UnreadOut(CamelModel):
    unread: int
