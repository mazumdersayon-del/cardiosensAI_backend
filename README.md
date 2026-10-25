# CardiosensAI — Backend API

FastAPI + PostgreSQL backend for the CardiosensAI connected health monitoring platform. It serves the patient mobile app now, and the doctor/caregiver dashboard and admin console later.

> **Status: 0.1, pre-production. Not for clinical use.** No clinical validation, legal compliance or medical-device certification is claimed. See [Before production](#before-production).

## What it does

| Area | Summary |
|---|---|
| **Sign-in** | SMS one-time code (stored hashed, 5 attempts, rate-limited), optional TOTP second factor, 15-minute access tokens, rotating refresh tokens with reuse detection |
| **Accounts** | Patient, caregiver, doctor and admin. Doctors are verified by staff before a doctor invite works |
| **Devices** | Device models declare exactly which vitals they may provide. One active owner per physical device (model + serial). The app's own device id (`clientRef`) is accepted |
| **Readings** | `POST /v1/measurements:batch` is idempotent and returns an outcome per item. Plausibility, unit, timestamp and device-capability checks run on every item. Simulated readings and glucose control-solution tests are refused. Readings dated before the device was registered are refused |
| **History** | Keyset-paginated queries that keep each reading's original UTC offset, plus latest-per-vital |
| **ECG** | Metadata in PostgreSQL. Raw int16 samples go to object storage with a SHA-256 integrity check, and are served only after a permission check |
| **Sharing** | Patient-created grants: chosen vitals, history window (`from_now`, `last_30_days`, `all`), downloads on/off, notices, expiry. Single-use hashed invite tokens. Revocation is immediate, including report links already issued |
| **Reports** | PDF built in the background (reportlab) and downloadable only through 10-minute signed links bound to one user. Permission is re-checked on every download |
| **Audit** | Append-only trail of logins, sharing changes, viewer reads, downloads and admin actions. Values are never logged. Patients see an access log |
| **Admin** | Users (masked phone numbers), suspension, professional verification, device models, ops dashboard and audit search. Admins get **no** health data |
| **Privacy** | Data export (JSON) and account deletion (health data removed; audit and consent records kept) |

## Quick start

```bash
cp .env.example .env            # then set JWT_SECRET and SIGNING_KEY
docker compose up --build       # PostgreSQL + API on http://localhost:8000 (runs migrations and seeds device models)
open http://localhost:8000/docs # interactive API docs
```

Without Docker:

```bash
python3.13 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
export DATABASE_URL=postgresql+psycopg://cardiosens:cardiosens@localhost:5432/cardiosens
alembic upgrade head
python -m app.scripts.seed --demo --admin +919800000009   # synthetic demo users and readings
uvicorn app.main:app --reload
```

With `DEV_EXPOSE_OTP=true` (development only), `POST /v1/auth/otp/request` returns the code as `devCode`. Demo accounts: patient `+919800000001`, doctor `+919800000002`, family member `+919800000003`.

### Tests

```bash
pytest                                                                   # temporary SQLite
TEST_DATABASE_URL=postgresql+psycopg://user:pass@localhost/test pytest   # PostgreSQL (what CI runs)
ruff check . && ruff format --check .
```

The 31 tests cover the sign-in flow, refresh-token theft detection, per-item ingestion outcomes, idempotency, pagination, sharing scope and history windows, revocation, grant expiry, doctor verification, admin isolation from health data, ECG integrity, signed report links and account deletion.

## Architecture

```mermaid
flowchart LR
  subgraph Devices
    BP[BP monitor<br/>BLE 0x1810]
    GL[Glucose meter<br/>BLE 0x1808]
    WR[CardiosensAI wearable]
  end
  App[Patient app<br/>React Native]
  Dash[Doctor / caregiver<br/>dashboard]
  Admin[Admin console]
  subgraph API[FastAPI — modular monolith]
    Auth[auth]
    Ingest[ingestion]
    Authz[authz<br/>grants + history window]
    Rep[reports<br/>background PDF]
    Aud[audit]
  end
  PG[(PostgreSQL<br/>users, grants, readings,<br/>ECG metadata, audit)]
  OS[(Object storage<br/>ECG waveforms, PDFs)]
  BP & GL & WR -- Bluetooth --> App
  App -- HTTPS /v1 --> API
  Dash -- HTTPS /v1 --> API
  Admin -- HTTPS /v1/admin --> API
  API --> PG
  API --> OS
```

**Data flow:**

1. The device sends a reading to the phone over BLE.
2. The app parses the standard profile and creates a `Measurement` with a stable `id`, which it queues offline.
3. The app sends `POST /v1/measurements:batch`.
4. The server validates each item and stores it once (unique on `patient_id + client_id`, plus a content hash).
5. Viewers read via `/v1/patients/{id}/measurements`. Every request passes through `services/authz.py`.
6. A report request returns `202`. A background job renders the PDF to object storage. A signed link streams it after a fresh permission check.

The service is a single deployable with clear modules (`api/`, `services/`, `models/`). Split it into separate services only when load or team structure calls for it.

## Data model

```mermaid
erDiagram
  users ||--o{ devices : owns
  device_models ||--o{ devices : "model of"
  users ||--o{ measurements : "patient of"
  devices ||--o{ measurements : "measured by"
  users ||--o{ ecg_recordings : "patient of"
  devices ||--o{ ecg_recordings : "recorded by"
  users ||--o{ access_grants : "grants (patient)"
  users ||--o{ access_grants : "receives (grantee)"
  access_grants ||--o{ invites : "single-use token"
  users ||--o| professional_profiles : "doctor verification"
  users ||--o{ consents : gives
  users ||--o{ reports : "about / requested by"
  users ||--o{ notifications : receives
  users ||--o{ refresh_tokens : sessions
  users {
    uuid id PK
    string phone UK
    string role "patient|caregiver|doctor|admin"
    string status "active|suspended|deleted"
    string patient_code UK
    jsonb preferences
  }
  device_models {
    uuid id PK
    string code UK
    jsonb capabilities "e.g. [bp] or [hr,spo2,ecg]"
    string integration "ble_standard|ble_vendor|cloud_api"
  }
  devices {
    uuid id PK
    uuid patient_id FK
    uuid model_id FK
    string serial "unique per model while active"
    string client_ref "app's local id"
    timestamptz registered_at
  }
  measurements {
    uuid id PK
    uuid patient_id FK
    string client_id "idempotency key (unique per patient)"
    string payload_hash
    string vital
    float value
    smallint systolic
    smallint diastolic
    timestamptz measured_at
    smallint utc_offset_min
    string acquisition "live|delayed|manual|simulated"
    string quality
    jsonb quality_flags
  }
  ecg_recordings {
    uuid id PK
    string storage_key "object storage"
    string sha256
    int sample_rate_hz
    float mm_per_mv
    jsonb poor_quality_segments
  }
  access_grants {
    uuid id PK
    uuid patient_id FK
    uuid grantee_user_id FK
    jsonb vitals
    string history "from_now|last_30_days|all"
    bool downloads_allowed
    timestamptz expires_at
    string status "pending|active|revoked"
  }
  audit_events {
    uuid id PK
    uuid actor_user_id
    uuid patient_id
    string action
    string detail "never health values"
  }
```

Also present: `otp_challenges`, `reports`, `ingestion_rejections` (reason codes only, for ops) and `notifications`.

**Indexes:** `measurements(patient_id, vital, measured_at)` for history queries, unique `(patient_id, client_id)` for idempotency, partial unique `(model_id, serial) WHERE status='active'` for device ownership, and `audit_events(patient_id, at)`.

**Migrations:** `alembic upgrade head`. CI applies the migrations, checks that they match the models (`alembic check`) and rolls them back.

**Retention (to be decided with legal review):**
- Readings: kept until the patient deletes the account.
- Ingestion rejections: can be pruned after 90 days.
- OTP challenges and expired refresh tokens: can be pruned daily.
- Audit events: kept per the retention period your counsel advises.

## Connecting the mobile app

The app's `apiClient.ts` already targets `POST /v1/measurements:batch`, and its `Measurement` JSON is accepted as is. To go live:

1. Set `expo.extra.apiBaseUrl` in the app's `app.json`.
2. Build sign-in on `/v1/auth/otp/request` and `/v1/auth/otp/verify`. Store tokens in `expo-secure-store` and refresh with `/v1/auth/refresh`. Wire the token into `HttpUploadClient`, which currently has a `TODO`.
3. When a device is paired, call `POST /v1/devices` with `clientRef` set to the app's local device id. Readings can then keep using the app's ids.
4. Report sync outcomes with `PATCH /v1/devices/{id}/status`.
5. Record onboarding consent with `POST /v1/me/consents` (`data_processing`).

The app's **simulated** readings are refused by design (`reason: Simulated readings are never stored as health data`).

## Configuration

All settings are environment variables. See `.env.example` and `app/core/config.py`. In `staging` and `production`, the app **refuses to start** with development secrets, `DEV_EXPOSE_OTP=true` or the console SMS sender.

## Before production

- **SMS provider:** plug a DLT-registered Indian SMS provider into `app/services/sms.py`. Add email delivery for invites.
- **Secrets:** use a secrets manager (e.g. AWS Secrets Manager or Parameter Store). Rotate `JWT_SECRET` and `SIGNING_KEY`. Encrypt the TOTP secret column at the application layer (KMS).
- **Infrastructure:**
  - Managed PostgreSQL with encryption at rest, PITR backups and tested restores.
  - S3 (`ap-south-1`) with SSE-KMS and public access blocked.
  - TLS everywhere.
  - Pick the region after a data-localisation review.
- **Scale:**
  - Move the rate limiter to Redis.
  - Move report generation to a job queue (e.g. arq or RQ on Redis).
  - Run several API replicas behind a load balancer.
- **Health alerts:** the `health_alert` notification type exists, but no threshold rules are implemented. Rules must be clinically reviewed, and must account for signal quality and stale data, before patient-facing use.
- **Regulatory and legal:** review the DPDP Act 2023 (consent notices, breach reporting, data-principal rights), CDSCO device classification, IEC 62304 (software lifecycle), ISO 14971 (risk) and ISO 27001/SOC 2 controls. Get a security assessment and penetration test.
- **Monitoring:** ship the JSON logs, add metrics and tracing (e.g. OpenTelemetry), and alert on ingestion rejections and report failures (`/v1/admin/ops`).

## Project layout

```
app/
  main.py                FastAPI app, middleware, router wiring
  core/                  config, db (UTC timestamps), security (JWT/OTP/signed links), errors, storage, rate limit, logging
  models/                SQLAlchemy models
  schemas/               Pydantic request/response models (camelCase on the wire)
  services/              authz, ingestion, report_pdf, notifications, audit, serializers, sms
  api/v1/                auth, me, devices, measurements, ecg, sharing, notifications, reports, audit, admin
  scripts/seed.py        device models, admin, synthetic demo data
alembic/                 migrations
docs/openapi.json        generated API spec
tests/                   pytest suite
```

## Licence

Proprietary — © CardiosensAI Private Limited. All rights reserved.
