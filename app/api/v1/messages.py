"""Messages between a patient and a doctor they're linked with (services/messages.py)."""

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import RequestMeta, get_current_user, request_meta
from app.core.db import get_db
from app.core.ratelimit import limiter
from app.models import Message, User
from app.schemas.messages import ConversationOut, MessageIn, MessageOut, PartnerOut, ThreadOut, UnreadOut
from app.services import messages as msgs

router = APIRouter(tags=["messages"])


def _partner(u: User) -> PartnerOut:
    name = u.full_name or ("Doctor" if u.role == "doctor" else "Patient")
    return PartnerOut(user_id=str(u.id), unique_id=u.unique_id, full_name=name, role=u.role)


def _msg(m: Message, me: User) -> MessageOut:
    return MessageOut(id=str(m.id), mine=m.sender_id == me.id, body=m.body, created_at=m.created_at, read_at=m.read_at)


@router.get("/conversations", response_model=list[ConversationOut], summary="My conversations, newest first")
def conversations(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[ConversationOut]:
    if user.role not in ("patient", "doctor"):
        return []
    unread = msgs.unread_from(db, user)
    out = []
    for other in msgs.partners(db, user):
        last = msgs.last_message(db, user, other)
        out.append(
            ConversationOut(
                partner=_partner(other),
                can_send=msgs.can_send(db, user, other),
                unread=unread.get(other.id, 0),
                last_at=last.created_at if last else None,
                last_preview=last.body[:80] if last else None,
                last_mine=bool(last and last.sender_id == user.id),
            )
        )
    out.sort(key=lambda c: (c.last_at is not None, c.last_at or datetime.min), reverse=True)
    return out


@router.get("/conversations/unread", response_model=UnreadOut, summary="How many messages I haven't read")
def unread(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> UnreadOut:
    if user.role not in ("patient", "doctor"):
        return UnreadOut(unread=0)
    return UnreadOut(unread=sum(msgs.unread_from(db, user).values()))


@router.get("/conversations/{party}", response_model=ThreadOut, summary="Messages with a doctor or patient (marks theirs as read)")
def thread(
    party: str,
    before: datetime | None = Query(default=None, description="Older than this (paging)"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ThreadOut:
    other = msgs.find_party(db, user, party)
    rows = msgs.read(db, user, other, before)
    out = ThreadOut(partner=_partner(other), can_send=msgs.can_send(db, user, other), messages=[_msg(m, user) for m in rows])
    db.commit()
    return out


@router.post("/conversations/{party}/messages", response_model=MessageOut, status_code=201, summary="Send a message")
def send(
    party: str,
    body: MessageIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    meta: RequestMeta = Depends(request_meta),
) -> MessageOut:
    limiter.hit(f"message:{user.id}", 120, 3600)
    other = msgs.find_party(db, user, party)
    m = msgs.send(db, user, other, body.body, meta)
    db.commit()
    return _msg(m, user)
