"""The signed-in user's own profile, contact details, consents, professional verification, data export and deletion."""

import re
import uuid

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import RequestMeta, get_current_user, request_meta
from app.core.config import get_settings
from app.core.db import get_db, utcnow
from app.core.errors import bad_request, conflict, not_found
from app.core.ratelimit import limiter
from app.core.storage import get_storage
from app.models import (
    AccessGrant,
    Consent,
    Device,
    EcgRecording,
    Measurement,
    Organization,
    ProfessionalProfile,
    User,
)
from app.schemas.auth import (
    E164,
    ConsentIn,
    ConsentOut,
    ContactCodeIn,
    ContactCodeOut,
    ContactVerifyIn,
    OrganizationIn,
    OrganizationOut,
    ProfessionalIn,
    ProfessionalOut,
    UserOut,
    UserUpdateIn,
)
from app.services import audit, otp, sms
from app.services.accounts import phone_holder, release_unconfirmed_phone
from app.services.email import mask_email
from app.services.erasure import erase_account
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
    for field, value in data.items():
        setattr(user, field, value)
    db.commit()
    return user_out(user)


AVATAR_MAX_BYTES = 512 * 1024
AVATAR_TYPES = {b"\xff\xd8\xff": ("image/jpeg", "jpg"), b"\x89PNG\r\n\x1a\n": ("image/png", "png")}


@router.put("/avatar", response_model=UserOut, summary="Set or replace my profile picture (JPEG or PNG, at most 512 KB)")
async def set_avatar(
    file: UploadFile = File(description="JPEG or PNG; the web app sends a 320 px square"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    meta: RequestMeta = Depends(request_meta),
) -> UserOut:
    limiter.hit(f"avatar:{user.id}", 30, 3600)
    data = await file.read(AVATAR_MAX_BYTES + 1)
    if len(data) > AVATAR_MAX_BYTES:
        raise bad_request("file_too_large", "The picture can be at most 512 KB")
    kind = next((v for sig, v in AVATAR_TYPES.items() if data.startswith(sig)), None)  # by content, not by name
    if kind is None:
        raise bad_request("unsupported_file", "Use a JPEG or PNG picture")
    storage = get_storage()
    old = user.avatar_key
    key = f"avatars/{user.id}/{uuid.uuid4()}.{kind[1]}"
    storage.put(key, data, kind[0])
    user.avatar_key = key
    user.avatar_updated_at = utcnow()
    audit.record(db, actor=user, action="account.avatar_set", resource_type="user", resource_id=user.id, meta=meta)
    db.commit()
    if old:
        storage.delete(old)
    return user_out(user)


@router.get("/avatar", response_class=Response, summary="My profile picture")
def get_avatar(user: User = Depends(get_current_user)) -> Response:
    if not user.avatar_key:
        raise not_found("Profile picture")
    ctype = "image/png" if user.avatar_key.endswith(".png") else "image/jpeg"
    return Response(get_storage().get(user.avatar_key), media_type=ctype, headers={"Cache-Control": "private, max-age=3600"})


@router.delete("/avatar", response_model=UserOut, summary="Remove my profile picture")
def delete_avatar(
    user: User = Depends(get_current_user), db: Session = Depends(get_db), meta: RequestMeta = Depends(request_meta)
) -> UserOut:
    old = user.avatar_key
    user.avatar_key = user.avatar_updated_at = None
    if old:
        audit.record(db, actor=user, action="account.avatar_removed", resource_type="user", resource_id=user.id, meta=meta)
    db.commit()
    if old:
        get_storage().delete(old)
    return user_out(user)


@router.post(
    "/contact/code",
    response_model=ContactCodeOut,
    summary="Add a mobile number or email, or confirm a mobile number saved while SMS was down: sends a code to it",
)
def contact_code(
    body: ContactCodeIn, user: User = Depends(get_current_user), db: Session = Depends(get_db), meta: RequestMeta = Depends(request_meta)
) -> ContactCodeOut:
    """Only while the account has none, or (mobile) has one that was never confirmed: a sign-up while SMS was down keeps the
    number unconfirmed, and it is confirmed (or corrected) here. Confirmed contacts aren't changed here (support does that)."""
    s = get_settings()
    limiter.hit(f"contact-code:{user.id}", 5, 3600)
    if body.kind == "email":
        value = body.value.strip().lower()
        if "@" not in value or len(value) < 6:
            raise bad_request("invalid_email", "That email address doesn't look right")
        if user.email is not None:
            raise bad_request("email_locked", "Your sign-in email can't be changed here. Contact CardiosensAI support")
        if db.scalar(select(User.id).where(func.lower(User.email) == value)) is not None:
            raise conflict("email_taken", "This email address can't be used")
        ch, code = otp.create(db, channel="email", purpose="add_email", email=value, user_id=user.id)
        masked = mask_email(value)
    else:
        value = body.value.replace(" ", "").replace("-", "")
        if not re.fullmatch(E164, value):
            raise bad_request("invalid_phone", "Enter the mobile number with its country code")
        if user.phone is not None and user.phone_verified_at is not None:
            raise bad_request("phone_locked", "Your mobile number can't be changed here. Contact CardiosensAI support")
        if not sms.available():
            raise sms.UNAVAILABLE
        if phone_holder(db, value, confirmed_only=True, exclude=user.id) is not None:
            raise conflict("phone_taken", "This mobile number can't be used")
        ch, code = otp.create(db, channel="sms", purpose="add_phone", phone=value, user_id=user.id)
        masked = sms.mask_phone(value)
    db.commit()
    otp.deliver(ch, code, user.full_name)
    return ContactCodeOut(challenge_id=str(ch.id), destination_masked=masked, expires_in_s=s.otp_ttl_s, dev_code=otp.dev_code(code))


@router.post("/contact/verify", response_model=UserOut, summary="Confirm the new mobile number or email with its code")
def contact_verify(
    body: ContactVerifyIn, user: User = Depends(get_current_user), db: Session = Depends(get_db), meta: RequestMeta = Depends(request_meta)
) -> UserOut:
    ch = otp.get(db, body.challenge_id)
    purpose = ch.purpose if ch is not None and ch.purpose in ("add_email", "add_phone") else "add_email"
    otp.check(db, ch if ch is not None and ch.user_id == user.id else None, body.code, purpose=purpose)
    now = utcnow()
    if ch.channel == "email":
        if user.email is not None or db.scalar(select(User.id).where(func.lower(User.email) == ch.email)) is not None:
            raise conflict("email_taken", "This email address can't be used")
        user.email, user.email_verified_at = ch.email, now
    else:
        if user.phone_verified_at is not None or phone_holder(db, ch.phone, confirmed_only=True, exclude=user.id) is not None:
            raise conflict("phone_taken", "This mobile number can't be used")
        released = release_unconfirmed_phone(db, ch.phone, exclude=user.id)  # the SMS code proves it is this person's
        if released is not None:
            audit.record(db, actor=released, action="account.phone_released", detail="number confirmed by another account", meta=meta)
        user.phone, user.phone_verified_at = ch.phone, now
    ch.consumed_at = now
    audit.record(db, actor=user, action=f"account.{ch.purpose}", meta=meta)
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
        prof.review_note = None
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
    org.review_note = None
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
    keys = erase_account(db, user, actor=user, action="account.delete", meta=meta)
    db.commit()
    storage = get_storage()
    for k in keys:
        storage.delete(k)
