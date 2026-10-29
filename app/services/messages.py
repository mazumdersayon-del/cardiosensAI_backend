"""Messages between a patient and a doctor they're linked with (models/message.py).

- Either side writes while the patient↔doctor link is active (doctors must be able to see patients: verified,
  two-step sign-in). After the link ends the history stays readable but nobody can write.
- The other side gets one in-app notice per unread streak, without the text (message text is health data).
- A doctor opening a conversation is recorded in the patient's access log (at most hourly).
"""

import uuid
from datetime import timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.api.deps import RequestMeta
from app.core.db import utcnow
from app.core.errors import bad_request, forbidden, not_found
from app.core.ids import normalize_unique_id
from app.models import AuditEvent, CareLink, Message, User
from app.services import accounts, audit, notifications

MAX_BODY = 2000
PAGE = 100


def _active_link(db: Session, patient_id: uuid.UUID, doctor_id: uuid.UUID) -> bool:
    return (
        db.scalar(
            select(CareLink.id)
            .where(
                CareLink.kind == "patient_doctor",
                CareLink.patient_id == patient_id,
                CareLink.doctor_id == doctor_id,
                CareLink.status == "active",
            )
            .limit(1)
        )
        is not None
    )


def _has_history(db: Session, patient_id: uuid.UUID, doctor_id: uuid.UUID) -> bool:
    return db.scalar(select(Message.id).where(Message.patient_id == patient_id, Message.doctor_id == doctor_id).limit(1)) is not None


def pair(db: Session, user: User, other: User) -> tuple[uuid.UUID, uuid.UUID]:
    """(patient_id, doctor_id) of a conversation between user and other."""
    if {user.role, other.role} != {"patient", "doctor"}:
        raise not_found("Conversation")
    return (user.id, other.id) if user.role == "patient" else (other.id, user.id)


def find_party(db: Session, user: User, ref: str) -> User:
    """The other side, by user id or 16-digit ID. 404 unless they are linked now or have written before."""
    if user.role not in ("patient", "doctor"):
        raise forbidden("Messages are between patients and their doctors")
    other = None
    uid = normalize_unique_id(ref)
    if uid:
        other = db.scalar(select(User).where(User.unique_id == uid))
    else:
        try:
            other = db.get(User, uuid.UUID(ref))
        except ValueError:
            other = None
    if other is None or other.id == user.id or other.status != "active":
        raise not_found("Conversation")
    p, d = pair(db, user, other)
    if not (_active_link(db, p, d) or _has_history(db, p, d)):
        raise not_found("Conversation")
    return other


def can_send(db: Session, user: User, other: User) -> bool:
    p, d = pair(db, user, other)
    doctor = user if user.role == "doctor" else other
    return _active_link(db, p, d) and accounts.can_view_patients(db, doctor)


def send(db: Session, user: User, other: User, body: str, meta: RequestMeta | None) -> Message:
    text = (body or "").strip()
    if not text:
        raise bad_request("empty", "Write a message first")
    if len(text) > MAX_BODY:
        raise bad_request("too_long", f"Messages can be at most {MAX_BODY} characters")
    if not can_send(db, user, other):
        raise forbidden("You can message each other only while you're linked")
    p, d = pair(db, user, other)
    waiting = db.scalar(
        select(Message.id)
        .where(Message.patient_id == p, Message.doctor_id == d, Message.sender_id == user.id, Message.read_at.is_(None))
        .limit(1)
    )
    m = Message(patient_id=p, doctor_id=d, sender_id=user.id, body=text)
    db.add(m)
    db.flush()
    audit.record(db, actor=user, action="message.send", patient_id=p, resource_type="message", resource_id=m.id, meta=meta)
    if waiting is None:  # first unread one: tell them once
        notifications.notify(
            db,
            user_id=other.id,
            patient_id=p,
            kind="sharing",
            title="New message",
            body=f"{user.full_name or ('Your patient' if user.role == 'patient' else 'Your doctor')} sent you a message.",
            link=f"/messages/{user.id}" if other.role == "patient" else f"/patients/{p}#messages",
            dedupe_key=f"msg:{m.id}",
        )
    return m


def read(db: Session, user: User, other: User, before=None) -> list[Message]:  # noqa: ANN001
    """Newest PAGE messages (oldest first); marks the other side's as read."""
    p, d = pair(db, user, other)
    q = select(Message).where(Message.patient_id == p, Message.doctor_id == d)
    if before is not None:
        q = q.where(Message.created_at < before)
    rows = list(db.scalars(q.order_by(Message.created_at.desc()).limit(PAGE)))
    rows.reverse()
    db.execute(
        update(Message)
        .where(Message.patient_id == p, Message.doctor_id == d, Message.sender_id == other.id, Message.read_at.is_(None))
        .values(read_at=utcnow())
    )
    if user.role == "doctor":
        recent = db.scalar(
            select(AuditEvent.id).where(
                AuditEvent.actor_user_id == user.id,
                AuditEvent.patient_id == p,
                AuditEvent.action == "message.view",
                AuditEvent.at >= utcnow() - timedelta(hours=1),
            )
        )
        if recent is None:
            audit.record(db, actor=user, action="message.view", patient_id=p, detail="Messages")
    return rows


def unread_from(db: Session, user: User, other_ids: list[uuid.UUID] | None = None) -> dict[uuid.UUID, int]:
    """Unread messages to `user`, per sender."""
    mine = Message.patient_id == user.id if user.role == "patient" else Message.doctor_id == user.id
    q = select(Message.sender_id, func.count()).where(mine, Message.sender_id != user.id, Message.read_at.is_(None))
    if other_ids is not None:
        q = q.where(Message.sender_id.in_(other_ids))
    return dict(db.execute(q.group_by(Message.sender_id)).all())


def partners(db: Session, user: User) -> list[User]:
    """Patients: linked doctors and anyone written with before. Doctors: patients written with."""
    ids: set[uuid.UUID] = set()
    if user.role == "patient":
        ids |= set(
            db.scalars(
                select(CareLink.doctor_id).where(
                    CareLink.kind == "patient_doctor", CareLink.patient_id == user.id, CareLink.status == "active"
                )
            )
        )
        ids |= set(db.scalars(select(Message.doctor_id).where(Message.patient_id == user.id).distinct()))
    elif user.role == "doctor":
        ids |= set(db.scalars(select(Message.patient_id).where(Message.doctor_id == user.id).distinct()))
    rows = [db.get(User, i) for i in ids]
    return [u for u in rows if u is not None and u.status == "active"]


def last_message(db: Session, user: User, other: User) -> Message | None:
    p, d = pair(db, user, other)
    return db.scalar(select(Message).where(Message.patient_id == p, Message.doctor_id == d).order_by(Message.created_at.desc()).limit(1))
