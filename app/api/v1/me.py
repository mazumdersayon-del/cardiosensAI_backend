"""The signed-in user's own profile, consents, professional verification, data export and deletion."""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session

from app.api.deps import RequestMeta, get_current_user, request_meta
from app.core.db import get_db, utcnow
from app.core.errors import bad_request, not_found
from app.core.storage import get_storage
from app.models import (
    AccessGrant,
    CareLink,
    Consent,
    Device,
    EcgRecording,
    Invite,
    Measurement,
    Notification,
    Organization,
    ProfessionalProfile,
    RefreshToken,
    Report,
    User,
)
from app.schemas.auth import ConsentIn, ConsentOut, OrganizationIn, OrganizationOut, ProfessionalIn, ProfessionalOut, UserOut, UserUpdateIn
from app.services import audit
from app.services.serializers import device_out, ecg_out, grant_out, measurement_out, organization_out, user_out

router = APIRouter(prefix="/me", tags=["me"])

ALLOWED_PREFERENCES = {"glucoseUnit": {"mg/dL", "mmol/L"}, "tempUnit": {"°C", "°F"}}
NOTIFICATION_KEYS = {"device", "data", "sharing", "health"}


@router.get("", response_model=UserOut)
def get_me(user: User = Depends(get_current_user)) -> UserOut:
    return user_out(user)


@router.patch("", response_model=UserOut)
def update_me(body: UserUpdateIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> UserOut:
    data = body.model_dump(exclude_unset=True)
    if "preferences" in data and data["preferences"] is not None:
        prefs = dict(user.preferences or {})
        for k, v in data.pop("preferences").items():
            if k in ALLOWED_PREFERENCES and v in ALLOWED_PREFERENCES[k]:
                prefs[k] = v
            elif k == "notifications" and isinstance(v, dict):
                prefs["notifications"] = {nk: bool(nv) for nk, nv in v.items() if nk in NOTIFICATION_KEYS}
            else:
                raise bad_request("invalid_preference", f"Unsupported preference: {k}")
        user.preferences = prefs
    if "email" in data:
        new = (data.pop("email") or "").lower() or None
        if new != user.email:
            if user.email is not None:
                raise bad_request("email_locked", "The sign-in email can't be changed here. Contact CardiosensAI support")
            if new and db.scalar(select(User.id).where(func.lower(User.email) == new)) is not None:
                raise bad_request("email_taken", "This email address can't be used")
            user.email = new
            user.email_verified_at = None
    for field, value in data.items():
        setattr(user, field, value)
    db.commit()
    return user_out(user)


@router.get("/consents", response_model=list[ConsentOut])
def list_consents(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[ConsentOut]:
    rows = db.scalars(select(Consent).where(Consent.user_id == user.id).order_by(Consent.granted_at.desc()))
    return [ConsentOut(id=str(c.id), kind=c.kind, version=c.version, granted_at=c.granted_at, withdrawn_at=c.withdrawn_at) for c in rows]


@router.post("/consents", response_model=ConsentOut, status_code=201)
def give_consent(
    body: ConsentIn, user: User = Depends(get_current_user), db: Session = Depends(get_db), meta: RequestMeta = Depends(request_meta)
) -> ConsentOut:
    c = Consent(user_id=user.id, kind=body.kind, version=body.version)
    db.add(c)
    audit.record(db, actor=user, action="consent.give", patient_id=user.id, detail=f"{body.kind} v{body.version}", meta=meta)
    db.commit()
    return ConsentOut(id=str(c.id), kind=c.kind, version=c.version, granted_at=c.granted_at, withdrawn_at=None)


@router.post("/consents/{consent_id}/withdraw", response_model=ConsentOut)
def withdraw_consent(
    consent_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db), meta: RequestMeta = Depends(request_meta)
) -> ConsentOut:
    c = db.get(Consent, consent_id)
    if c is None or c.user_id != user.id:
        raise not_found("Consent")
    if c.withdrawn_at is None:
        c.withdrawn_at = utcnow()
        audit.record(db, actor=user, action="consent.withdraw", patient_id=user.id, detail=f"{c.kind} v{c.version}", meta=meta)
        db.commit()
    return ConsentOut(id=str(c.id), kind=c.kind, version=c.version, granted_at=c.granted_at, withdrawn_at=c.withdrawn_at)


@router.put("/professional-profile", response_model=ProfessionalOut, summary="Doctors: submit registration details for verification")
def submit_professional(
    body: ProfessionalIn, user: User = Depends(get_current_user), db: Session = Depends(get_db), meta: RequestMeta = Depends(request_meta)
) -> ProfessionalOut:
    if user.role != "doctor":
        raise bad_request("not_a_doctor_account", "Only doctor accounts can submit professional details")
    prof = db.get(ProfessionalProfile, user.id)
    if prof is None:
        prof = ProfessionalProfile(user_id=user.id, **body.model_dump())
        db.add(prof)
    else:
        for k, v in body.model_dump().items():
            setattr(prof, k, v)
        # Changing registration details sends the account back for verification.
        prof.verification_status = "pending"
        prof.verified_at = None
        prof.verified_by = None
        prof.submitted_at = utcnow()
    audit.record(db, actor=user, action="professional.submit", meta=meta)
    db.commit()
    return ProfessionalOut.model_validate(prof)


@router.get("/professional-profile", response_model=ProfessionalOut)
def get_professional(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> ProfessionalOut:
    prof = db.get(ProfessionalProfile, user.id)
    if prof is None:
        raise not_found("Professional profile")
    return ProfessionalOut.model_validate(prof)


@router.get("/organization", response_model=OrganizationOut, summary="Clinics: this clinic's details and verification status")
def get_organization(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> OrganizationOut:
    org = db.get(Organization, user.organization_id) if user.role == "clinic" and user.organization_id else None
    if org is None:
        raise not_found("Clinic")
    return organization_out(org)


@router.put("/organization", response_model=OrganizationOut, summary="Clinics: update details (sends the clinic back for verification)")
def update_organization(
    body: OrganizationIn, user: User = Depends(get_current_user), db: Session = Depends(get_db), meta: RequestMeta = Depends(request_meta)
) -> OrganizationOut:
    org = db.get(Organization, user.organization_id) if user.role == "clinic" and user.organization_id else None
    if org is None:
        raise not_found("Clinic")
    for k, v in body.model_dump().items():
        setattr(org, k, v)
    org.verification_status = "pending"
    org.verified_at = None
    org.verified_by = None
    audit.record(db, actor=user, action="organization.submit", resource_type="organization", resource_id=org.id, meta=meta)
    db.commit()
    return organization_out(org)


@router.get("/export", summary="Download a copy of my data (JSON)")
def export_my_data(
    user: User = Depends(get_current_user), db: Session = Depends(get_db), meta: RequestMeta = Depends(request_meta)
) -> dict:
    measurements = db.scalars(select(Measurement).where(Measurement.patient_id == user.id).order_by(Measurement.measured_at))
    out = {
        "exportedAt": utcnow().isoformat(),
        "profile": user_out(user).model_dump(by_alias=True, mode="json"),
        "consents": [
            ConsentOut(id=str(c.id), kind=c.kind, version=c.version, granted_at=c.granted_at, withdrawn_at=c.withdrawn_at).model_dump(
                by_alias=True, mode="json"
            )
            for c in db.scalars(select(Consent).where(Consent.user_id == user.id))
        ],
        "devices": [
            device_out(d).model_dump(by_alias=True, mode="json") for d in db.scalars(select(Device).where(Device.patient_id == user.id))
        ],
        "measurements": [measurement_out(m).model_dump(by_alias=True, mode="json") for m in measurements],
        "ecgRecordings": [
            ecg_out(e).model_dump(by_alias=True, mode="json")
            for e in db.scalars(select(EcgRecording).where(EcgRecording.patient_id == user.id))
        ],
        "sharing": [
            grant_out(g).model_dump(by_alias=True, mode="json")
            for g in db.scalars(select(AccessGrant).where(AccessGrant.patient_id == user.id))
        ],
        "note": "ECG waveforms can be downloaded individually from /v1/ecg-recordings/{id}/samples.",
    }
    audit.record(db, actor=user, action="account.export", patient_id=user.id, meta=meta)
    db.commit()
    return out


@router.delete("", status_code=204, summary="Delete my account and health data")
def delete_me(user: User = Depends(get_current_user), db: Session = Depends(get_db), meta: RequestMeta = Depends(request_meta)) -> None:
    """Removes health data and personal details and ends all sharing.

    Audit events are kept (without health values) where records of access must be retained.
    Copies that recipients already downloaded can't be recalled.
    """
    now = utcnow()
    storage = get_storage()
    keys = [k for k in db.scalars(select(EcgRecording.storage_key).where(EcgRecording.patient_id == user.id))]
    keys += [k for k in db.scalars(select(Report.storage_key).where(Report.patient_id == user.id)) if k]
    grant_ids = select(AccessGrant.id).where(AccessGrant.patient_id == user.id)
    db.execute(delete(Invite).where(Invite.grant_id.in_(grant_ids)))
    db.execute(
        update(AccessGrant)
        .where(AccessGrant.patient_id == user.id, AccessGrant.status != "revoked")
        .values(status="revoked", revoked_at=now)
    )
    db.execute(
        update(AccessGrant)
        .where(AccessGrant.grantee_user_id == user.id, AccessGrant.status != "revoked")
        .values(status="revoked", revoked_at=now)
    )
    db.execute(delete(Measurement).where(Measurement.patient_id == user.id))
    db.execute(delete(EcgRecording).where(EcgRecording.patient_id == user.id))
    db.execute(update(Report).where(Report.patient_id == user.id).values(status="deleted", storage_key=None))
    db.execute(update(Device).where(Device.patient_id == user.id, Device.status == "active").values(status="retired", retired_at=now))
    db.execute(delete(Notification).where(Notification.user_id == user.id))
    db.execute(update(RefreshToken).where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None)).values(revoked_at=now))
    # Consent records (no health data) are kept as evidence of what was agreed and when.
    db.execute(
        update(CareLink)
        .where(or_(CareLink.patient_id == user.id, CareLink.doctor_id == user.id), CareLink.status.in_(("pending", "active")))
        .values(status="ended", ended_at=now, ended_by=user.id)
    )
    if user.phone:
        user.phone = f"deleted:{user.id.hex[:12]}"
    user.email = user.full_name = user.emergency_contact = user.password_hash = None
    user.date_of_birth = None
    user.mfa_secret = None
    user.mfa_enabled = False
    user.preferences = {}
    user.status = "deleted"
    user.deleted_at = now
    audit.record(db, actor=user, action="account.delete", patient_id=user.id, meta=meta)
    db.commit()
    for k in keys:
        storage.delete(k)
