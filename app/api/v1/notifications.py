import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.db import get_db, utcnow
from app.core.errors import not_found
from app.models import Notification, User
from app.schemas.sharing import NotificationOut
from app.services.serializers import notification_out

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_model=list[NotificationOut])
def list_notifications(
    unread_only: bool = Query(default=False, alias="unreadOnly"),
    limit: int = Query(default=50, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[NotificationOut]:
    stmt = select(Notification).where(Notification.user_id == user.id)
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    return [notification_out(n) for n in db.scalars(stmt.order_by(Notification.created_at.desc()).limit(limit))]


@router.post("/{notification_id}/read", response_model=NotificationOut)
def mark_read(notification_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> NotificationOut:
    n = db.get(Notification, notification_id)
    if n is None or n.user_id != user.id:
        raise not_found("Notification")
    if n.read_at is None:
        n.read_at = utcnow()
        db.commit()
    return notification_out(n)


@router.post("/read-all", status_code=204)
def mark_all_read(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> None:
    db.execute(update(Notification).where(Notification.user_id == user.id, Notification.read_at.is_(None)).values(read_at=utcnow()))
    db.commit()
