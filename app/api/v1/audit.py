from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_patient
from app.core.db import get_db
from app.models import AuditEvent, User
from app.schemas.sharing import AuditOut

router = APIRouter(tags=["audit"])


def audit_out(e: AuditEvent, names: dict) -> AuditOut:
    return AuditOut(
        id=str(e.id),
        at=e.at,
        actor_user_id=str(e.actor_user_id) if e.actor_user_id else None,
        actor_role=e.actor_role,
        actor_name=names.get(e.actor_user_id),
        patient_id=str(e.patient_id) if e.patient_id else None,
        action=e.action,
        resource_type=e.resource_type,
        resource_id=e.resource_id,
        detail=e.detail,
    )


@router.get("/me/access-log", response_model=list[AuditOut], summary="Who accessed or changed sharing of my data")
def my_access_log(
    limit: int = Query(default=100, ge=1, le=500), user: User = Depends(require_patient), db: Session = Depends(get_db)
) -> list[AuditOut]:
    rows = list(db.scalars(select(AuditEvent).where(AuditEvent.patient_id == user.id).order_by(AuditEvent.at.desc()).limit(limit)))
    actor_ids = {e.actor_user_id for e in rows if e.actor_user_id}
    names = (
        {u.id: ("You" if u.id == user.id else u.full_name) for u in db.scalars(select(User).where(User.id.in_(actor_ids)))}
        if actor_ids
        else {}
    )
    return [audit_out(e, names) for e in rows]
