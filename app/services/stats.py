"""Platform statistics for the admin portal: counts of accounts and activity, never health values.

Periods are rolling windows ending now (UTC). "Active" = signed in or refreshed a session
(users.last_active_at). Growth buckets are UTC days (≤ 90 days) or ISO weeks starting Monday.
"""

from datetime import date, datetime, timedelta
from typing import Literal

from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.models import (
    AccessGrant,
    CareLink,
    Device,
    Document,
    EcgLiveSession,
    EcgRecording,
    Measurement,
    Organization,
    ProfessionalProfile,
    Report,
    User,
    VerificationFile,
)
from app.schemas.common import CamelModel

ROLES = ("patient", "doctor", "clinic", "admin")
PUBLIC = ("patient", "doctor", "clinic")
STATUSES = ("active", "suspended", "deleting", "deleted")
VSTATUSES = ("pending", "verified", "rejected", "revoked")


class UserStats(CamelModel):
    total: int  # accounts that aren't erased
    by_role: dict[str, dict[str, int]]  # role -> status -> count (incl. "deleted" = erased)
    email_verified: dict[str, int]
    mfa_enabled: dict[str, int]


class VerificationStats(CamelModel):
    doctors: dict[str, int]  # pending | verified | rejected | revoked | awaitingDocuments
    clinics: dict[str, int]


class GrowthPoint(CamelModel):
    start: date
    patient: int
    doctor: int
    clinic: int


class GrowthStats(CamelModel):
    bucket: Literal["day", "week"]
    points: list[GrowthPoint]
    new_in_period: dict[str, int]


class ActivityStats(CamelModel):
    active_day: dict[str, int]
    active_week: dict[str, int]
    active_month: dict[str, int]
    active_in_period: dict[str, int]
    patients_with_readings: int  # uploaded at least one reading in the period
    patients_with_ecg: int  # recorded at least one ECG in the period
    patients_with_devices: int  # have an active device now


class Volume(CamelModel):
    total: int
    period: int


class VolumeStats(CamelModel):
    measurements: Volume
    ecg_recordings: Volume
    live_ecg_sessions: Volume
    documents: Volume
    reports: Volume
    devices: Volume
    care_links: Volume  # active links now / links requested in the period
    family_shares: Volume  # active family/caregiver shares now / created in the period


class StatsOut(CamelModel):
    generated_at: datetime
    days: int
    users: UserStats
    verification: VerificationStats
    growth: GrowthStats
    activity: ActivityStats
    volume: VolumeStats


def _count(db: Session, stmt) -> int:  # noqa: ANN001
    return db.scalar(stmt) or 0


def _by_role(db: Session, *conds) -> dict[str, int]:  # noqa: ANN002
    rows = dict(db.execute(select(User.role, func.count()).where(User.status != "deleted", *conds).group_by(User.role)).all())
    return {r: rows.get(r, 0) for r in ROLES}


def _active_since(db: Session, since: datetime) -> dict[str, int]:
    return _by_role(db, User.last_active_at >= since)


def _verification(rows, with_files: set) -> dict[str, int]:  # noqa: ANN001
    out = {s: 0 for s in VSTATUSES} | {"awaitingDocuments": 0}
    for status, owner in rows:
        out[status] = out.get(status, 0) + 1
        if status == "pending" and owner not in with_files:
            out["awaitingDocuments"] += 1
    return out


def _bucket_start(d: date, bucket: str) -> date:
    return d - timedelta(days=d.weekday()) if bucket == "week" else d


def compute(db: Session, days: int) -> StatsOut:
    now = utcnow()
    since = now - timedelta(days=days)

    # ---- accounts
    by_role = {r: dict.fromkeys(STATUSES, 0) for r in ROLES}
    for role, status, n in db.execute(select(User.role, User.status, func.count()).group_by(User.role, User.status)).all():
        by_role.setdefault(role, dict.fromkeys(STATUSES, 0))[status] = n
    users = UserStats(
        total=sum(n for r in by_role.values() for s, n in r.items() if s != "deleted"),
        by_role=by_role,
        email_verified=_by_role(db, User.email_verified_at.is_not(None)),
        mfa_enabled=_by_role(db, User.mfa_enabled.is_(True)),
    )

    # ---- verification (accounts that still exist)
    live = User.status.in_(("active", "suspended"))
    with_files = set(db.scalars(select(distinct(VerificationFile.user_id))))
    doctors = db.execute(
        select(ProfessionalProfile.verification_status, ProfessionalProfile.user_id)
        .join(User, User.id == ProfessionalProfile.user_id)
        .where(live)
    ).all()
    clinics = db.execute(
        select(Organization.verification_status, Organization.owner_user_id).join(User, User.id == Organization.owner_user_id).where(live)
    ).all()
    verification = VerificationStats(doctors=_verification(doctors, with_files), clinics=_verification(clinics, with_files))

    # ---- growth
    bucket: Literal["day", "week"] = "day" if days <= 90 else "week"
    first = _bucket_start(since.date(), bucket)
    step = timedelta(days=7 if bucket == "week" else 1)
    points: dict[date, dict[str, int]] = {}
    d = first
    while d <= now.date():
        points[d] = dict.fromkeys(PUBLIC, 0)
        d += step
    new = dict.fromkeys(PUBLIC, 0)
    for role, created in db.execute(select(User.role, User.created_at).where(User.created_at >= since, User.role.in_(PUBLIC))).all():
        key = _bucket_start(created.date(), bucket)
        if key in points:
            points[key][role] += 1
        new[role] += 1
    growth = GrowthStats(bucket=bucket, points=[GrowthPoint(start=k, **v) for k, v in points.items()], new_in_period=new)

    # ---- activity
    activity = ActivityStats(
        active_day=_active_since(db, now - timedelta(days=1)),
        active_week=_active_since(db, now - timedelta(days=7)),
        active_month=_active_since(db, now - timedelta(days=30)),
        active_in_period=_active_since(db, since),
        patients_with_readings=_count(db, select(func.count(distinct(Measurement.patient_id))).where(Measurement.ingested_at >= since)),
        patients_with_ecg=_count(db, select(func.count(distinct(EcgRecording.patient_id))).where(EcgRecording.created_at >= since)),
        patients_with_devices=_count(db, select(func.count(distinct(Device.patient_id))).where(Device.status == "active")),
    )

    # ---- volume
    def vol(model, ts, *conds) -> Volume:  # noqa: ANN001
        total = _count(db, select(func.count()).select_from(model).where(*conds))
        period = _count(db, select(func.count()).select_from(model).where(ts >= since, *conds))
        return Volume(total=total, period=period)

    family = AccessGrant.role.in_(("family", "caregiver"))
    volume = VolumeStats(
        measurements=vol(Measurement, Measurement.ingested_at),
        ecg_recordings=vol(EcgRecording, EcgRecording.created_at),
        live_ecg_sessions=vol(EcgLiveSession, EcgLiveSession.started_at),
        documents=vol(Document, Document.created_at, Document.deleted_at.is_(None)),
        reports=vol(Report, Report.created_at, Report.status != "deleted"),
        devices=vol(Device, Device.registered_at, Device.status == "active"),
        care_links=Volume(
            total=_count(db, select(func.count()).select_from(CareLink).where(CareLink.status == "active")),
            period=_count(db, select(func.count()).select_from(CareLink).where(CareLink.created_at >= since)),
        ),
        family_shares=Volume(
            total=_count(db, select(func.count()).select_from(AccessGrant).where(family, AccessGrant.status == "active")),
            period=_count(db, select(func.count()).select_from(AccessGrant).where(family, AccessGrant.created_at >= since)),
        ),
    )
    return StatsOut(generated_at=now, days=days, users=users, verification=verification, growth=growth, activity=activity, volume=volume)


def as_rows(s: StatsOut) -> list[list[object]]:
    """Flatten for CSV: section, metric, key, value."""
    rows: list[list[object]] = [
        ["section", "metric", "key", "value"],
        ["meta", "generated_at", "", s.generated_at.isoformat()],
        ["meta", "days", "", s.days],
    ]
    rows.append(["users", "total", "", s.users.total])
    for role, by_status in s.users.by_role.items():
        rows += [["users", f"{role}", status, n] for status, n in by_status.items()]
    rows += [["users", "email_verified", role, n] for role, n in s.users.email_verified.items()]
    rows += [["users", "two_step_on", role, n] for role, n in s.users.mfa_enabled.items()]
    rows += [["verification", "doctors", k, n] for k, n in s.verification.doctors.items()]
    rows += [["verification", "clinics", k, n] for k, n in s.verification.clinics.items()]
    for p in s.growth.points:
        rows += [["growth", f"signups_per_{s.growth.bucket}", f"{p.start.isoformat()} {role}", getattr(p, role)] for role in PUBLIC]
    rows += [["growth", "new_in_period", role, n] for role, n in s.growth.new_in_period.items()]
    for label in ("active_day", "active_week", "active_month", "active_in_period"):
        rows += [["activity", label, role, n] for role, n in getattr(s.activity, label).items()]
    rows += [
        ["activity", "patients_with_readings", "period", s.activity.patients_with_readings],
        ["activity", "patients_with_ecg", "period", s.activity.patients_with_ecg],
        ["activity", "patients_with_devices", "now", s.activity.patients_with_devices],
    ]
    for name, v in s.volume:
        rows += [["volume", name, "total", v.total], ["volume", name, "period", v.period]]
    return rows
