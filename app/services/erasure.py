"""Account deletion: an admin schedules it (30-day grace, restorable); erasure removes the data.

- `schedule_deletion` (admin): status "deleting", signed out everywhere, invisible to links and
  sharing at once (everything checks status == "active"); `purge_due` erases it after `erase_after`.
- `erase_account` (self-delete, "erase now", or the purge job): removes health data, files and
  personal details, ends every link and share. The anonymised row stays so the 16-digit ID is never
  reused and the audit trail (no health values) still points somewhere. Consent records are kept as
  evidence of what was agreed. Copies others already downloaded can't be recalled.

Callers commit, then delete the returned storage keys (files go only after the database agrees).
"""

import uuid
from datetime import datetime, timedelta

from sqlalchemy import delete, or_, select, update
from sqlalchemy.orm import Session

from app.api.deps import RequestMeta
from app.core.db import utcnow
from app.models import (
    AccessGrant,
    ActionToken,
    CareLink,
    Device,
    DeviceConfig,
    DevicePairing,
    Document,
    DocumentComment,
    DocumentFile,
    EcgLiveChunk,
    EcgLiveSession,
    EcgRecording,
    EcgSnapshot,
    EcgSnapshotLabel,
    FactoryDevice,
    Invite,
    Measurement,
    Notification,
    Organization,
    ProfessionalProfile,
    RefreshToken,
    Report,
    User,
    VerificationFile,
)
from app.services import audit, device_hub

GRACE = timedelta(days=30)


def _end_sessions(db: Session, user_id: uuid.UUID, now: datetime) -> None:
    db.execute(update(RefreshToken).where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None)).values(revoked_at=now))


def schedule_deletion(db: Session, user: User, *, admin: User, reason: str, meta: RequestMeta | None) -> datetime:
    now = utcnow()
    user.status = "deleting"
    user.erase_after = now + GRACE
    _end_sessions(db, user.id, now)
    audit.record(db, actor=admin, action="admin.user_delete_scheduled", resource_type="user", resource_id=user.id, detail=reason, meta=meta)
    return user.erase_after


def restore(db: Session, user: User, *, admin: User, reason: str | None, meta: RequestMeta | None) -> None:
    user.status = "active"
    user.erase_after = None
    audit.record(db, actor=admin, action="admin.user_restored", resource_type="user", resource_id=user.id, detail=reason, meta=meta)


def erase_account(
    db: Session, user: User, *, actor: User | None, action: str, detail: str | None = None, meta: RequestMeta | None = None
) -> list[str]:
    """Erase one account (any role). Returns storage keys to delete after the commit."""
    now = utcnow()
    keys: list[str] = []

    # ---- health data the account owns (patients)
    keys += [k for k in db.scalars(select(EcgRecording.storage_key).where(EcgRecording.patient_id == user.id)) if k]
    keys += [k for k in db.scalars(select(Report.storage_key).where(Report.patient_id == user.id)) if k]
    keys += [k for k in db.scalars(select(EcgSnapshot.storage_key).where(EcgSnapshot.patient_id == user.id)) if k]
    doc_ids = select(Document.id).where(Document.patient_id == user.id)
    keys += [k for k in db.scalars(select(DocumentFile.storage_key).where(DocumentFile.document_id.in_(doc_ids))) if k]
    grant_ids = select(AccessGrant.id).where(AccessGrant.patient_id == user.id)
    db.execute(delete(Invite).where(Invite.grant_id.in_(grant_ids)))
    db.execute(
        update(AccessGrant)
        .where(or_(AccessGrant.patient_id == user.id, AccessGrant.grantee_user_id == user.id), AccessGrant.status != "revoked")
        .values(status="revoked", revoked_at=now)
    )
    db.execute(delete(Measurement).where(Measurement.patient_id == user.id))
    live_ids = select(EcgLiveSession.id).where(EcgLiveSession.patient_id == user.id)
    db.execute(delete(EcgLiveChunk).where(EcgLiveChunk.session_id.in_(live_ids)))
    db.execute(delete(EcgLiveSession).where(EcgLiveSession.patient_id == user.id))
    db.execute(delete(EcgRecording).where(EcgRecording.patient_id == user.id))
    db.execute(delete(DocumentComment).where(DocumentComment.document_id.in_(doc_ids)))
    db.execute(delete(DocumentFile).where(DocumentFile.document_id.in_(doc_ids)))
    db.execute(delete(Document).where(Document.patient_id == user.id))
    db.execute(update(Report).where(Report.patient_id == user.id).values(status="deleted", storage_key=None))
    snapshot_ids = select(EcgSnapshot.id).where(EcgSnapshot.patient_id == user.id)
    db.execute(delete(EcgSnapshotLabel).where(or_(EcgSnapshotLabel.snapshot_id.in_(snapshot_ids), EcgSnapshotLabel.doctor_id == user.id)))
    db.execute(delete(EcgSnapshot).where(EcgSnapshot.patient_id == user.id))
    # CardiosensAI devices: unpair (the device is told at once); doctors' devices assigned to this patient stop recording.
    for d in db.scalars(
        select(Device).where(Device.patient_id == user.id, Device.status == "active", Device.factory_device_id.is_not(None))
    ):
        device_hub.unpair(db, d, actor=actor, why="account erased")
    for cfg in db.scalars(select(DeviceConfig).where(DeviceConfig.assigned_patient_id == user.id)):
        cfg.assigned_patient_id, cfg.live_until = None, None
        device_hub.queue_state(db, db.get(FactoryDevice, cfg.factory_device_id))
    db.execute(delete(DevicePairing).where(DevicePairing.requested_by == user.id))
    db.execute(update(Device).where(Device.patient_id == user.id, Device.status == "active").values(status="retired", retired_at=now))

    # ---- links: as patient or doctor, and (clinic login) everything of the clinic
    link_party = or_(CareLink.patient_id == user.id, CareLink.doctor_id == user.id)
    org = db.get(Organization, user.organization_id) if user.role == "clinic" and user.organization_id else None
    if org is not None:
        link_party = or_(link_party, CareLink.organization_id == org.id)
    db.execute(
        update(CareLink)
        .where(link_party, CareLink.status.in_(("pending", "active")))
        .values(status="ended", ended_at=now, ended_by=user.id)
    )

    # ---- doctor / clinic registration
    keys += list(db.scalars(select(VerificationFile.storage_key).where(VerificationFile.user_id == user.id)))
    db.execute(delete(VerificationFile).where(VerificationFile.user_id == user.id))
    db.execute(delete(ProfessionalProfile).where(ProfessionalProfile.user_id == user.id))
    if org is not None:
        # Other logins of the clinic (future staff accounts) leave it.
        db.execute(update(User).where(User.organization_id == org.id, User.id != user.id).values(organization_id=None))
        org.name = "Deleted clinic"
        org.address = org.city = org.registration_number = org.phone = org.review_note = None
        org.verification_status = "revoked"

    # ---- the account itself
    db.execute(delete(Notification).where(Notification.user_id == user.id))
    db.execute(update(ActionToken).where(ActionToken.user_id == user.id, ActionToken.used_at.is_(None)).values(used_at=now))
    _end_sessions(db, user.id, now)
    if user.phone:
        user.phone = f"deleted:{user.id.hex[:12]}"
    user.email = user.full_name = user.emergency_contact = user.password_hash = None
    if user.avatar_key:
        keys.append(user.avatar_key)
    user.avatar_key = user.avatar_updated_at = None
    user.date_of_birth = None
    user.mfa_secret = None
    user.mfa_enabled = False
    user.preferences = {}
    user.status = "deleted"
    user.deleted_at = now
    user.erase_after = None
    audit.record(
        db,
        actor=actor,
        action=action,
        patient_id=user.id if user.role == "patient" else None,
        resource_type="user",
        resource_id=user.id,
        detail=detail,
        meta=meta,
    )
    return keys


def purge_due(db: Session, now: datetime | None = None) -> tuple[int, list[str]]:
    """Erase every account whose grace period is over. Returns (count, storage keys); caller commits."""
    now = now or utcnow()
    due = list(db.scalars(select(User).where(User.status == "deleting", User.erase_after.is_not(None), User.erase_after <= now)))
    keys: list[str] = []
    for u in due:
        keys += erase_account(db, u, actor=None, action="account.erase", detail="Scheduled deletion (grace period over)")
    return len(due), keys
