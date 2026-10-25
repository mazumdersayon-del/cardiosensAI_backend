import uuid

from sqlalchemy.orm import Session

from app.api.deps import RequestMeta
from app.core.observability import request_id_var
from app.models import AuditEvent, User


def record(
    db: Session,
    *,
    actor: User | None,
    action: str,
    patient_id: uuid.UUID | None = None,
    resource_type: str | None = None,
    resource_id: str | uuid.UUID | None = None,
    detail: str | None = None,
    meta: RequestMeta | None = None,
    grant_id: uuid.UUID | None = None,
) -> None:
    """Add an audit event to the current transaction. `detail` must never contain health values."""
    db.add(
        AuditEvent(
            actor_user_id=actor.id if actor else None,
            actor_role=actor.role if actor else None,
            patient_id=patient_id,
            action=action,
            resource_type=resource_type,
            resource_id=str(resource_id) if resource_id else None,
            detail=(detail or None) and detail[:300],
            ip=meta.ip if meta else None,
            request_id=request_id_var.get() if request_id_var.get() != "-" else (meta.request_id if meta else None),
            grant_id=grant_id,
        )
    )
