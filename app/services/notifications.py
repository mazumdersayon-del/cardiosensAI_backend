"""Creates in-app notifications. Technical notices (device, sync) are kept separate from clinical alerts.

Clinical (threshold) alerts are intentionally not generated here: alert rules must be
clinically reviewed first. The `health_alert` kind exists so they can be added later.
"""

import uuid

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.models import AccessGrant, Notification, User


def notify(
    db: Session,
    *,
    user_id: uuid.UUID,
    kind: str,
    title: str,
    body: str,
    link: str | None = None,
    patient_id: uuid.UUID | None = None,
    dedupe_key: str | None = None,
) -> None:
    """Add a notification; a repeated dedupe_key for the same user is silently ignored."""
    recipient = db.get(User, user_id)
    if recipient is None or recipient.status != "active":
        return
    prefs = (recipient.preferences or {}).get("notifications", {})
    pref_key = {
        "device": "device",
        "sync": "device",
        "data": "data",
        "sharing": "sharing",
        "access": "sharing",
        "health_alert": "health",
    }.get(kind)
    if pref_key and prefs.get(pref_key) is False:
        return
    if dedupe_key and db.scalar(select(Notification.id).where(Notification.user_id == user_id, Notification.dedupe_key == dedupe_key)):
        return
    try:
        with db.begin_nested():
            db.add(
                Notification(
                    user_id=user_id, kind=kind, title=title[:160], body=body[:500], link=link, patient_id=patient_id, dedupe_key=dedupe_key
                )
            )
    except IntegrityError:
        pass  # concurrent duplicate


def notify_patient_and_watchers(
    db: Session, *, patient: User, kind: str, title_self: str, title_others: str, body: str, link: str | None, dedupe_key: str
) -> None:
    """Device problems go to the patient and to grantees who opted into device notices."""
    notify(db, user_id=patient.id, patient_id=patient.id, kind=kind, title=title_self, body=body, link=link, dedupe_key=dedupe_key)
    now = utcnow()
    grants = db.scalars(
        select(AccessGrant).where(
            AccessGrant.patient_id == patient.id,
            AccessGrant.status == "active",
            AccessGrant.device_notices.is_(True),
            AccessGrant.grantee_user_id.is_not(None),
            or_(AccessGrant.expires_at.is_(None), AccessGrant.expires_at > now),
        )
    )
    for g in grants:
        if g.grantee_user_id is None:
            continue
        notify(
            db, user_id=g.grantee_user_id, patient_id=patient.id, kind=kind, title=title_others, body=body, link=None, dedupe_key=dedupe_key
        )
