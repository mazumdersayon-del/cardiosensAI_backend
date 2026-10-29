"""Patient details: height, weight, sex, blood group, address with PIN, medical background (models/patient_profile.py)."""

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.core.errors import bad_request
from app.models import PatientProfile, User
from app.models.patient_profile import BLOOD_GROUPS, CONDITIONS, SEXES, SMOKING
from app.services import pincodes

TEXT = {
    "address_line1": 160,
    "address_line2": 160,
    "city": 80,
    "district": 80,
    "state": 80,
    "conditions_other": 300,
    "allergies": 300,
    "medicines": 500,
}
SNOOZE = timedelta(days=1)


def get(db: Session, user: User) -> PatientProfile:
    """The patient's profile (an unsaved empty one if they never added anything)."""
    return db.get(PatientProfile, user.id) or PatientProfile(user_id=user.id, conditions=[], share_address=False)


def bmi(p: PatientProfile) -> float | None:
    if not p.height_cm or not p.weight_kg:
        return None
    return round(p.weight_kg / (p.height_cm / 100) ** 2, 1)


def needs_address(p: PatientProfile) -> bool:
    return not p.pin_code


def show_reminder(p: PatientProfile, now: datetime) -> bool:
    return needs_address(p) and (p.reminder_snoozed_until is None or p.reminder_snoozed_until <= now)


def _num(v, lo: float, hi: float, what: str) -> float | None:  # noqa: ANN001
    if v is None:
        return None
    if not lo <= v <= hi:
        raise bad_request("out_of_range", f"{what} must be between {lo:g} and {hi:g}")
    return round(float(v), 1)


def update(db: Session, user: User, changes: dict) -> PatientProfile:
    """Apply only the fields sent. Empty strings clear a field."""
    p = db.get(PatientProfile, user.id)
    if p is None:
        p = PatientProfile(user_id=user.id, conditions=[], share_address=False)
        db.add(p)
    now = utcnow()
    for k, v in changes.items():
        if isinstance(v, str):
            v = v.strip() or None
        if k == "height_cm":
            p.height_cm = _num(v, 40, 250, "Height (cm)")
        elif k == "weight_kg":
            w = _num(v, 2, 350, "Weight (kg)")
            if w != p.weight_kg:
                p.weight_at = now if w is not None else None
            p.weight_kg = w
        elif k == "sex":
            if v is not None and v not in SEXES:
                raise bad_request("bad_sex", "Choose female, male, other or prefer not to say")
            p.sex = v
        elif k == "blood_group":
            if v is not None and v not in BLOOD_GROUPS:
                raise bad_request("bad_blood_group", f"Blood group must be one of {', '.join(BLOOD_GROUPS)}")
            p.blood_group = v
        elif k == "smoking":
            if v is not None and v not in SMOKING:
                raise bad_request("bad_smoking", "Choose never, former or current")
            p.smoking = v
        elif k == "conditions":
            codes = list(dict.fromkeys(v or []))
            if any(c not in CONDITIONS for c in codes):
                raise bad_request("bad_condition", "Unknown condition")
            p.conditions = codes
        elif k == "pin_code":
            try:
                p.pin_code = pincodes.clean(v)
            except ValueError as exc:
                raise bad_request("bad_pin", str(exc)) from exc
        elif k == "share_address":
            p.share_address = bool(v)
        elif k in TEXT:
            if v is not None and len(v) > TEXT[k]:
                raise bad_request("too_long", f"At most {TEXT[k]} characters")
            setattr(p, k, v)
    # District and state from the PIN when the patient left them empty.
    known = pincodes.lookup(p.pin_code)
    if known is not None:
        p.district = p.district or known.district or None
        p.state = p.state or known.state or None
    p.updated_at = now
    return p


def snooze(db: Session, user: User) -> PatientProfile:
    p = db.get(PatientProfile, user.id)
    if p is None:
        p = PatientProfile(user_id=user.id, conditions=[], share_address=False)
        db.add(p)
    p.reminder_snoozed_until = utcnow() + SNOOZE
    return p
