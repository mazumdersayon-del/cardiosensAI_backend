"""Patients' Doctors list: verified doctors who chose to be listed, with their public profile and picture."""

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import RequestMeta, get_current_user, request_meta
from app.core.db import get_db
from app.core.errors import bad_request, forbidden, not_found
from app.core.ids import normalize_unique_id
from app.core.ratelimit import limiter
from app.core.storage import get_storage
from app.models import CareLink, PatientProfile, ProfessionalProfile, User
from app.schemas.doctors import DoctorCardOut, DoctorListingIn, DoctorListingOut, DoctorOut, DoctorPage
from app.services import audit, pincodes

router = APIRouter(tags=["doctors"])


def _listed():
    """Doctors patients may see: opted in, verified by staff, active, on two-step sign-in."""
    return (
        select(User, ProfessionalProfile)
        .join(ProfessionalProfile, ProfessionalProfile.user_id == User.id)
        .where(
            User.role == "doctor",
            User.status == "active",
            User.mfa_enabled.is_(True),
            ProfessionalProfile.verification_status == "verified",
            ProfessionalProfile.listed.is_(True),
        )
    )


def _avatar_version(u: User) -> str | None:
    return str(int(u.avatar_updated_at.timestamp())) if u.avatar_key and u.avatar_updated_at else None


def _card(u: User, p: ProfessionalProfile) -> DoctorCardOut:
    return DoctorCardOut(
        unique_id=u.unique_id or "",
        full_name=u.full_name or "Doctor",
        specialty=p.specialty,
        degrees=p.degrees,
        experience_years=p.experience_years,
        practice=p.practice,
        city=p.city,
        avatar_version=_avatar_version(u),
        pin_code=p.pin_code,
    )


def _patient(user: User) -> User:
    if user.role != "patient":
        raise forbidden("The Doctors list is for patient accounts")
    return user


def _find(db: Session, unique_id: str) -> tuple[User, ProfessionalProfile]:
    uid = normalize_unique_id(unique_id)
    row = db.execute(_listed().where(User.unique_id == uid)).first() if uid else None
    if row is None:
        raise not_found("Doctor")
    return row[0], row[1]


@router.get("/doctors", response_model=DoctorPage, summary="Patients: verified doctors who chose to be listed")
def list_doctors(
    q: str | None = Query(default=None, max_length=80, description="Name, specialty, degree, hospital or city"),
    near_km: int | None = Query(
        default=None, alias="nearKm", ge=1, le=500, description="Near me: practices within this many km of my PIN code, nearest first"
    ),
    limit: int = Query(default=20, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DoctorPage:
    _patient(user)
    query = _listed()
    if q and q.strip():
        like = f"%{q.strip().lower()}%"
        cols = (
            User.full_name,
            ProfessionalProfile.specialty,
            ProfessionalProfile.degrees,
            ProfessionalProfile.practice,
            ProfessionalProfile.city,
        )
        query = query.where(or_(*(func.lower(c).like(like) for c in cols)))
    if near_km is None:
        total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = db.execute(query.order_by(func.lower(User.full_name), User.unique_id).limit(limit).offset(offset)).all()
        return DoctorPage(items=[_card(u, p) for u, p in rows], total=total)

    # Near me: distance between PIN code locations (approximate), nearest first.
    mine = db.get(PatientProfile, user.id)
    here = pincodes.lookup(mine.pin_code if mine else None)
    if here is None:
        problem = (
            "Add your PIN code under Account → About you to see doctors near you."
            if not (mine and mine.pin_code)
            else (f"We don't have a location for PIN {mine.pin_code} yet, so we can't sort by distance.")
        )
        return DoctorPage(items=[], total=0, near_problem=problem)
    found = []
    for u, p in db.execute(query.where(ProfessionalProfile.pin_code.is_not(None))).all():
        there = pincodes.lookup(p.pin_code)
        if there is None:
            continue
        d = pincodes.km(here, there)
        if d <= near_km:
            found.append((d, u, p))
    found.sort(key=lambda x: (x[0], (x[1].full_name or "").lower()))
    items = []
    for d, u, p in found[offset : offset + limit]:
        card = _card(u, p)
        card.distance_km = round(d) if d >= 10 else round(d, 1)
        items.append(card)
    place = here.place or here.district
    return DoctorPage(items=items, total=len(found), near=f"{here.pin} · {place}" if place else here.pin)


@router.get("/doctors/{unique_id}", response_model=DoctorOut, summary="Patients: one listed doctor's profile")
def get_doctor(unique_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> DoctorOut:
    _patient(user)
    u, p = _find(db, unique_id)
    link = db.scalar(
        select(CareLink.status)
        .where(
            CareLink.kind == "patient_doctor",
            CareLink.patient_id == user.id,
            CareLink.doctor_id == u.id,
            CareLink.status.in_(("pending", "active")),
        )
        .limit(1)
    )
    return DoctorOut(
        **_card(u, p).model_dump(),
        languages=p.languages,
        about=p.about,
        council=p.council,
        verified_at=p.verified_at.date().isoformat() if p.verified_at else None,
        link_status=link or "none",
    )


@router.get("/doctors/{unique_id}/avatar", response_class=Response, summary="Patients: a listed doctor's picture")
def doctor_avatar(unique_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Response:
    _patient(user)
    limiter.hit(f"doctor-avatar:{user.id}", 600, 3600)
    u, _ = _find(db, unique_id)
    if not u.avatar_key:
        raise not_found("Profile picture")
    ctype = "image/png" if u.avatar_key.endswith(".png") else "image/jpeg"
    return Response(get_storage().get(u.avatar_key), media_type=ctype, headers={"Cache-Control": "private, max-age=3600"})


def _listing_out(db: Session, user: User, p: ProfessionalProfile) -> DoctorListingOut:
    reason = None
    if p.listed:
        if p.verification_status != "verified":
            reason = "Shown once CardiosensAI has verified your registration."
        elif not user.mfa_enabled:
            reason = "Shown once two-step sign-in is on."
        elif user.status != "active":
            reason = "Your account isn't active."
    return DoctorListingOut(
        listed=p.listed,
        degrees=p.degrees,
        experience_years=p.experience_years,
        languages=p.languages,
        practice=p.practice,
        city=p.city,
        pin_code=p.pin_code,
        about=p.about,
        specialty=p.specialty,
        visible=p.listed and reason is None,
        hidden_reason=reason,
    )


def _own_profile(db: Session, user: User) -> ProfessionalProfile:
    if user.role != "doctor":
        raise bad_request("not_a_doctor_account", "Only doctor accounts appear in the Doctors list")
    p = db.get(ProfessionalProfile, user.id)
    if p is None:
        raise bad_request("registration_missing", "Add your registration details first")
    return p


@router.get("/me/doctor-listing", response_model=DoctorListingOut, summary="Doctors: my profile in the patients' Doctors list")
def get_listing(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> DoctorListingOut:
    return _listing_out(db, user, _own_profile(db, user))


@router.put("/me/doctor-listing", response_model=DoctorListingOut, summary="Doctors: edit my listing and choose whether patients see it")
def put_listing(
    body: DoctorListingIn, user: User = Depends(get_current_user), db: Session = Depends(get_db), meta: RequestMeta = Depends(request_meta)
) -> DoctorListingOut:
    p = _own_profile(db, user)
    for k, v in body.model_dump(exclude={"pin_code"}).items():
        setattr(p, k, (v.strip() or None) if isinstance(v, str) else v)
    if "pin_code" in body.model_fields_set:  # older apps don't send it: keep what's there
        try:
            p.pin_code = pincodes.clean(body.pin_code)
        except ValueError as exc:
            raise bad_request("bad_pin", str(exc)) from exc
    audit.record(db, actor=user, action="doctor.listing_update", detail="listed" if p.listed else "not listed", meta=meta)
    db.commit()
    return _listing_out(db, user, p)
