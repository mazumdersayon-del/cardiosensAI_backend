"""Seed reference data, and optionally a demo dataset (synthetic only).

    python -m app.scripts.seed                      # device models only
    python -m app.scripts.seed --admin +919800000000
    python -m app.scripts.seed --demo               # demo patient, doctor, family member (refused in production)

Demo readings are stored with acquisition = "simulated" and reports built from them are watermarked.
"""

import argparse
import hashlib
import math
import random
import struct
import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import SessionLocal, utcnow
from app.core.passwords import hash_password
from app.core.storage import get_storage
from app.models import AccessGrant, CareLink, Device, DeviceModel, EcgRecording, Measurement, Organization, ProfessionalProfile, User
from app.services import email
from app.services.accounts import allocate_unique_id, issue_action_token
from app.services.ecg_synth import Synth12

DEVICE_MODELS = [
    {
        "code": "ble-bp-standard",
        "name": "Bluetooth blood pressure monitor (standard profile)",
        "manufacturer": "Any (Bluetooth Blood Pressure Profile 0x1810)",
        "kind": "bp_cuff",
        "capabilities": ["bp"],
        "integration": "ble_standard",
        "regulatory_note": "Use models already approved for sale in India; verify the standard profile on each model.",
    },
    {
        "code": "ble-glucose-standard",
        "name": "Bluetooth glucose meter (standard profile)",
        "manufacturer": "Any (Bluetooth Glucose Profile 0x1808)",
        "kind": "glucose_meter",
        "capabilities": ["glucose"],
        "integration": "ble_standard",
        "regulatory_note": "Glucometers are CDSCO Class C (IVD). Use licensed meters only.",
    },
    {
        "code": "cardiosensai-wearable-dev",
        "name": "CardiosensAI wearable (development)",
        "manufacturer": "CardiosensAI",
        "kind": "wearable",
        "capabilities": ["hr", "spo2", "resp", "temp", "ecg"],
        "integration": "ble_vendor",
        "regulatory_note": "In development. Not clinically validated or certified.",
    },
]


def seed_models(db: Session) -> dict[str, DeviceModel]:
    out = {}
    for spec in DEVICE_MODELS:
        m = db.scalar(select(DeviceModel).where(DeviceModel.code == spec["code"]))
        if m is None:
            m = DeviceModel(**spec)
            db.add(m)
        out[spec["code"]] = m
    db.flush()
    return out


DEMO_PASSWORD = "cardiosens-demo-2026"  # noqa: S105 - demo mode only (refused in production)
DEMO_TOTP_SECRET = "CARDIOSENSDEMOTOTPSECRETCODE2345"  # noqa: S105 - demo doctor/clinic two-step sign-in


def ensure_user(db: Session, phone: str, role: str, name: str, code: str | None = None, email: str | None = None) -> User:
    u = db.scalar(select(User).where(User.phone == phone))
    if u is None:
        u = User(
            phone=phone,
            role=role,
            unique_id=allocate_unique_id(db, role),
            full_name=name,
            patient_code=code,
            preferences={"glucoseUnit": "mg/dL", "tempUnit": "°C"},
        )
        db.add(u)
        db.flush()
    if u.phone and u.phone_verified_at is None:
        u.phone_verified_at = utcnow()
    if email and u.email is None:
        u.email = email
        u.email_verified_at = utcnow()
        u.password_hash = hash_password(DEMO_PASSWORD)
    return u


def add_demo_12lead(db: Session, patient: User) -> None:
    """Databases seeded before 12-lead support get the demo 12-lead recording added once."""
    if db.scalar(select(EcgRecording.id).where(EcgRecording.patient_id == patient.id, EcgRecording.client_id == "demo_ecg_12lead")):
        return
    wear = db.scalar(
        select(Device).join(DeviceModel).where(Device.patient_id == patient.id, DeviceModel.code == "cardiosensai-wearable-dev")
    )
    if wear is None:
        return
    synth = Synth12(fs=500, hr=74, seed=12)
    raw12 = synth.raw(500 * 10)
    rec12 = uuid.uuid4()
    key12 = f"ecg/{patient.id}/{rec12}.i16"
    get_storage().put(key12, raw12, "application/octet-stream")
    db.add(
        EcgRecording(
            id=rec12,
            patient_id=patient.id,
            client_id="demo_ecg_12lead",
            device_id=wear.id,
            started_at=utcnow() - timedelta(minutes=40),
            utc_offset_min=330,
            lead="12-lead",
            leads=list(synth.leads),
            sample_rate_hz=500,
            sample_count=500 * 10,
            mm_per_mv=10,
            mm_per_sec=25,
            average_hr=74,
            poor_quality_segments=[],
            quality="good",
            acquisition="simulated",
            storage_key=key12,
            sha256=hashlib.sha256(raw12).hexdigest(),
        )
    )
    print("Added the demo 12-lead ECG.")


def seed_demo(db: Session, models: dict[str, DeviceModel]) -> None:
    rnd = random.Random(4812)  # noqa: S311 - synthetic demo data, not security
    now = utcnow()
    ist = 330
    patient = ensure_user(db, "+919800000001", "patient", "Ananya Rao (demo)", "CS-P-0004812", "patient@demo.cardiosens.ai")
    doctor = ensure_user(db, "+919800000002", "doctor", "Dr. Meera Iyer (demo)", email="doctor@demo.cardiosens.ai")
    family = ensure_user(db, "+919800000003", "patient", "Rohan Rao (demo)")
    doctor.mfa_secret, doctor.mfa_enabled = DEMO_TOTP_SECRET, True
    clinic_user = ensure_user(db, "+919800000004", "clinic", "Front desk (demo)", email="clinic@demo.cardiosens.ai")
    clinic_user.mfa_secret, clinic_user.mfa_enabled = DEMO_TOTP_SECRET, True
    if clinic_user.organization_id is None:
        org = Organization(
            unique_id=clinic_user.unique_id,
            name="Demo Heart Clinic",
            address="12 Park Street",
            city="Kolkata",
            registration_number="DEMO-CE-001",
            phone="+913340000000",
            owner_user_id=clinic_user.id,
            verification_status="verified",
            verified_at=now,
        )
        db.add(org)
        db.flush()
        clinic_user.organization_id = org.id
    if db.get(ProfessionalProfile, doctor.id) is None:
        db.add(
            ProfessionalProfile(
                user_id=doctor.id,
                registration_number="DEMO-12345",
                council="Demo Medical Council",
                verification_status="verified",
                verified_at=now,
            )
        )

    if db.scalar(select(Device).where(Device.patient_id == patient.id)) is not None:
        add_demo_12lead(db, patient)
        print("Demo data already present.")
        return
    reg = now - timedelta(days=40)
    wear = Device(
        patient_id=patient.id,
        model_id=models["cardiosensai-wearable-dev"].id,
        serial="DEMO-WR-0042",
        display_name="Wrist wearable",
        registered_at=reg,
        firmware="0.9.3",
        battery_pct=64,
    )
    cuff = Device(
        patient_id=patient.id,
        model_id=models["ble-bp-standard"].id,
        serial="DEMO-BP-0107",
        display_name="Blood pressure cuff",
        registered_at=reg,
        battery_pct=21,
    )
    db.add_all([wear, cuff])
    db.flush()

    rows: list[Measurement] = []

    def add(vital: str, unit: str, t, device: Device, **vals) -> None:  # noqa: ANN001, ANN003
        rows.append(
            Measurement(
                patient_id=patient.id,
                client_id=f"demo_{vital}_{int(t.timestamp())}",
                payload_hash="demo",
                vital=vital,
                unit=unit,
                measured_at=t,
                utc_offset_min=ist,
                received_at=t + timedelta(minutes=1),
                ingested_at=now,
                device_id=device.id,
                acquisition="simulated",
                quality="poor" if rnd.random() < 0.01 else "good",
                quality_flags=[],
                **vals,
            )
        )

    t = now - timedelta(days=30)
    while t < now:
        local_hour = (t + timedelta(minutes=ist)).hour
        if not (2 <= local_hour < 5 and (now - t).days < 1):  # overnight gap today: device off the wrist
            circ = math.cos((local_hour - 15) / 24 * 2 * math.pi)
            add("hr", "bpm", t, wear, value=round(70 + 6 * circ + rnd.gauss(0, 2.5)))
            add("spo2", "%", t, wear, value=min(100, round(97 + rnd.gauss(0, 0.7))))
            add("resp", "breaths/min", t, wear, value=round(15.5 + 1.2 * circ + rnd.gauss(0, 0.8)))
            add("temp", "°C", t, wear, value=round(36.6 + 0.2 * circ + rnd.gauss(0, 0.08), 1))
        t += timedelta(minutes=30)
    for d in range(30, 0, -1):
        if d % 4 == 2:
            continue
        for h in (7, 21):
            ts = (now - timedelta(days=d)).replace(hour=h - 5, minute=40, second=0, microsecond=0)
            s, di = round(124 + rnd.gauss(0, 5)), round(80 + rnd.gauss(0, 3))
            add(
                "bp",
                "mmHg",
                ts,
                cuff,
                systolic=s,
                diastolic=di,
                mean_arterial=round(di + (s - di) / 3),
                pulse_rate=round(70 + rnd.gauss(0, 4)),
            )
    db.add_all(rows)

    # One synthetic 30 s ECG (sum of Gaussian waves — not physiological).
    fs, secs, hr = 250, 30, 72
    samples = []
    waves = [(0.1, 0.025, 0.12), (0.215, 0.008, -0.1), (0.24, 0.009, 1.05), (0.265, 0.01, -0.22), (0.46, 0.04, 0.28)]
    for i in range(fs * secs):
        ph = (i / fs) % (60 / hr)
        mv = sum(a * math.exp(-((ph - m) ** 2) / (2 * sd * sd)) for m, sd, a in waves)
        samples.append(round(mv * 1000))
    raw = struct.pack(f"<{len(samples)}h", *samples)
    rec_id = uuid.uuid4()
    key = f"ecg/{patient.id}/{rec_id}.i16"
    get_storage().put(key, raw, "application/octet-stream")
    db.add(
        EcgRecording(
            id=rec_id,
            patient_id=patient.id,
            client_id="demo_ecg_1",
            device_id=wear.id,
            started_at=now - timedelta(hours=3),
            utc_offset_min=ist,
            lead="Lead I",
            leads=["I"],
            sample_rate_hz=fs,
            sample_count=len(samples),
            mm_per_mv=10,
            mm_per_sec=25,
            average_hr=hr,
            poor_quality_segments=[],
            quality="good",
            acquisition="simulated",
            storage_key=key,
            sha256=hashlib.sha256(raw).hexdigest(),
        )
    )

    # And a synthetic 10 s 12-lead ECG (interleaved frames), the newest recording.
    synth = Synth12(fs=500, hr=74, seed=12)
    raw12 = synth.raw(500 * 10)
    rec12 = uuid.uuid4()
    key12 = f"ecg/{patient.id}/{rec12}.i16"
    get_storage().put(key12, raw12, "application/octet-stream")
    db.add(
        EcgRecording(
            id=rec12,
            patient_id=patient.id,
            client_id="demo_ecg_12lead",
            device_id=wear.id,
            started_at=now - timedelta(minutes=40),
            utc_offset_min=ist,
            lead="12-lead",
            leads=list(synth.leads),
            sample_rate_hz=500,
            sample_count=500 * 10,
            mm_per_mv=10,
            mm_per_sec=25,
            average_hr=74,
            poor_quality_segments=[],
            quality="good",
            acquisition="simulated",
            storage_key=key12,
            sha256=hashlib.sha256(raw12).hexdigest(),
        )
    )

    db.add_all(
        [
            AccessGrant(
                patient_id=patient.id,
                grantee_user_id=doctor.id,
                grantee_name=doctor.full_name or "",
                grantee_contact="doctor@example.org",
                role="doctor",
                vitals=["hr", "spo2", "bp", "resp", "temp", "glucose", "ecg"],
                history="all",
                downloads_allowed=True,
                health_alerts=True,
                status="active",
                created_at=now - timedelta(days=20),
                accepted_at=now - timedelta(days=20),
            ),
            AccessGrant(
                patient_id=patient.id,
                grantee_user_id=family.id,
                grantee_name=family.full_name or "",
                grantee_contact="+919800000003",
                role="family",
                vitals=["hr", "spo2", "bp"],
                history="last_30_days",
                device_notices=True,
                status="active",
                created_at=now - timedelta(days=10),
                accepted_at=now - timedelta(days=10),
            ),
        ]
    )
    db.flush()
    doctor_grant = db.scalar(select(AccessGrant.id).where(AccessGrant.patient_id == patient.id, AccessGrant.grantee_user_id == doctor.id))
    clinic_grant = AccessGrant(
        patient_id=patient.id,
        grantee_user_id=clinic_user.id,
        grantee_name="Demo Heart Clinic",
        grantee_contact=clinic_user.unique_id,
        role="clinic",
        vitals=["hr", "spo2", "bp", "resp", "temp", "glucose", "ecg"],
        history="all",
        downloads_allowed=True,
        status="active",
        created_at=now - timedelta(days=12),
        accepted_at=now - timedelta(days=12),
    )
    db.add(clinic_grant)
    db.flush()
    t = now - timedelta(days=12)
    db.add_all(
        [
            CareLink(kind="patient_doctor", patient_id=patient.id, doctor_id=doctor.id, status="active", requested_by=patient.id,
                     patient_ok_at=t, doctor_ok_at=t, decided_at=t, grant_id=doctor_grant),
            CareLink(kind="patient_clinic", patient_id=patient.id, organization_id=clinic_user.organization_id, status="active",
                     requested_by=patient.id, patient_ok_at=t, clinic_ok_at=t, decided_at=t, grant_id=clinic_grant.id),
            CareLink(kind="doctor_clinic", doctor_id=doctor.id, organization_id=clinic_user.organization_id, status="active",
                     requested_by=clinic_user.id, doctor_ok_at=t, clinic_ok_at=t, decided_at=t),
        ]
    )  # fmt: skip
    print(f"Demo data: {len(rows)} simulated readings, 1 ECG.")
    print(f"  Sign in with password {DEMO_PASSWORD!r}:")
    print(f"  patient@demo.cardiosens.ai  {patient.unique_id}")
    print(f"  doctor@demo.cardiosens.ai   {doctor.unique_id}  (authenticator secret {DEMO_TOTP_SECRET})")
    print(f"  clinic@demo.cardiosens.ai   {clinic_user.unique_id}  (authenticator secret {DEMO_TOTP_SECRET})")
    print("  App (phone sign-in): patient +919800000001, doctor +919800000002, family +919800000003")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--admin", help="Phone number (E.164) to create or promote as an admin")
    parser.add_argument("--admin-email", help="Email address to create or promote as an admin; prints and emails a set-password link")
    parser.add_argument("--demo", action="store_true", help="Add synthetic demo users and readings")
    args = parser.parse_args()
    db = SessionLocal()
    try:
        models = seed_models(db)
        if args.admin:
            u = db.scalar(select(User).where(User.phone == args.admin))
            if u is None:
                u = User(phone=args.admin, phone_verified_at=utcnow(), role="admin", full_name="Administrator")
                db.add(u)
            else:
                u.role = "admin"
            if u.unique_id is None or not u.unique_id.startswith("9"):
                db.flush()
                u.unique_id = allocate_unique_id(db, "admin")
            print(f"Admin: {args.admin}")
        if args.admin_email:
            addr = args.admin_email.strip().lower()
            u = db.scalar(select(User).where(User.email == addr))
            if u is None:
                u = User(email=addr, role="admin", full_name="Administrator", unique_id=allocate_unique_id(db, "admin"), preferences={})
                db.add(u)
                db.flush()
            elif u.role != "admin":
                u.role = "admin"
                u.unique_id = allocate_unique_id(db, "admin")
            token = issue_action_token(db, u, "reset_password", addr, 72 * 3600)
            db.commit()
            link = f"{get_settings().web_base_url.rstrip('/')}/reset-password?token={token}"
            email.send_password_reset(addr, u.full_name, token, first_time=u.password_hash is None)
            print(f"Admin: {addr}. Set the password within 72 hours at:\n  {link}\nThen sign in and turn on two-step sign-in.")
        if args.demo:
            if get_settings().env == "production":
                raise SystemExit("Refusing to add demo data in production")
            seed_demo(db, models)
        db.commit()
        print(f"Device models: {', '.join(models)}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
