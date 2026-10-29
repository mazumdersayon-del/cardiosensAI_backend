"""Patient details (services/patient_profile.py) and PIN code lookup (services/pincodes.py)."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import RequestMeta, get_current_user, request_meta
from app.core.db import get_db, utcnow
from app.core.errors import bad_request, forbidden, not_found
from app.core.ratelimit import limiter
from app.models import PatientProfile, User
from app.models.patient_profile import CONDITIONS
from app.schemas.patient_profile import ConditionOption, PatientProfileIn, PatientProfileOut, PinOut
from app.services import audit, pincodes
from app.services import patient_profile as profiles
from app.services.authz import get_access, resolve_patient_id

router = APIRouter(tags=["patient profile"])

CONDITION_LABELS = {
    "hypertension": "High blood pressure",
    "diabetes": "Diabetes",
    "heart_disease": "Heart disease (angina, heart attack, stents, bypass)",
    "heart_failure": "Heart failure",
    "arrhythmia": "Irregular heartbeat (e.g. atrial fibrillation)",
    "stroke": "Stroke",
    "high_cholesterol": "High cholesterol",
    "kidney_disease": "Kidney disease",
    "thyroid": "Thyroid problem",
    "asthma_copd": "Asthma / COPD",
}


def _out(p: PatientProfile, *, street: bool, reminder: bool = False) -> PatientProfileOut:
    return PatientProfileOut(
        height_cm=p.height_cm,
        weight_kg=p.weight_kg,
        weight_at=p.weight_at,
        bmi=profiles.bmi(p),
        sex=p.sex,
        blood_group=p.blood_group,
        address_line1=p.address_line1 if street else None,
        address_line2=p.address_line2 if street else None,
        city=p.city,
        district=p.district,
        state=p.state,
        pin_code=p.pin_code,
        pin_known=pincodes.lookup(p.pin_code) is not None,
        share_address=bool(p.share_address),
        conditions=list(p.conditions or []),
        conditions_other=p.conditions_other,
        allergies=p.allergies,
        medicines=p.medicines,
        smoking=p.smoking,
        updated_at=p.updated_at,
        needs_address=profiles.needs_address(p),
        show_reminder=reminder,
    )


def _patient(user: User) -> User:
    if user.role != "patient":
        raise forbidden("Only patient accounts have these details")
    return user


@router.get("/me/patient-profile", response_model=PatientProfileOut, summary="Patients: my height, weight, address, background")
def get_mine(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> PatientProfileOut:
    p = profiles.get(db, _patient(user))
    return _out(p, street=True, reminder=profiles.show_reminder(p, utcnow()))


@router.put("/me/patient-profile", response_model=PatientProfileOut, summary="Patients: change my details (only the fields sent)")
def put_mine(
    body: PatientProfileIn, user: User = Depends(get_current_user), db: Session = Depends(get_db), meta: RequestMeta = Depends(request_meta)
) -> PatientProfileOut:
    p = profiles.update(db, _patient(user), {k: getattr(body, k) for k in body.model_fields_set})
    audit.record(db, actor=user, action="profile.details", patient_id=user.id, detail=", ".join(sorted(body.model_fields_set)), meta=meta)
    db.commit()
    return _out(p, street=True, reminder=profiles.show_reminder(p, utcnow()))


@router.post("/me/patient-profile/remind-later", response_model=PatientProfileOut, summary="Patients: hide the address reminder for a day")
def remind_later(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> PatientProfileOut:
    p = profiles.snooze(db, _patient(user))
    db.commit()
    return _out(p, street=True, reminder=False)


@router.get("/me/patient-profile/conditions", response_model=list[ConditionOption], summary="Condition codes and labels")
def conditions(_: User = Depends(get_current_user)) -> list[ConditionOption]:
    return [ConditionOption(code=c, label=CONDITION_LABELS[c]) for c in CONDITIONS]


@router.get("/patients/{patient_id}/profile", response_model=PatientProfileOut, summary="Linked doctors and clinics: a patient's details")
def patient_details(patient_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> PatientProfileOut:
    pid = resolve_patient_id(user, patient_id)
    ctx = get_access(db, user, pid)
    if ctx.via == "grant" and (ctx.grant is None or ctx.grant.role not in ("doctor", "clinic")):
        raise not_found("Patient")
    p = profiles.get(db, ctx.patient)
    if ctx.via == "grant":
        audit.record(db, actor=user, action="profile.view", patient_id=pid, detail="Patient details", grant_id=ctx.grant.id)
        db.commit()
    return _out(p, street=ctx.via == "self" or bool(p.share_address))


@router.get("/pincodes/{pin}", response_model=PinOut, summary="District and state of an Indian PIN code")
def pin_lookup(pin: str, user: User = Depends(get_current_user)) -> PinOut:
    limiter.hit(f"pin:{user.id}", 300, 3600)
    try:
        clean = pincodes.clean(pin)
    except ValueError as exc:
        raise bad_request("bad_pin", str(exc)) from exc
    known = pincodes.lookup(clean)
    if known is None:
        raise not_found("PIN code")
    return PinOut(pin=known.pin, district=known.district, state=known.state, place=known.place)
