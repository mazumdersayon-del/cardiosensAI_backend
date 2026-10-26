# CardiosensAI — Backend API and Web App

FastAPI + PostgreSQL backend and React web app for the CardiosensAI connected health monitoring platform. One deployable serves:

- the **API** (`/v1`) used by the patient mobile app ([cardioSE-WEB](https://github.com/mazumdersayon-del/cardioSE-WEB)) and the web app;
- the **web app** (`/`): one sign-in page for patients, doctors and clinics, then a separate dashboard per role, plus a staff console.

Patients, doctors and clinics are identified and linked by a **16-digit CardiosensAI ID**.

> **Status: 0.2, pre-production. Not for clinical use.** No clinical validation, legal compliance or medical-device certification is claimed. See [Before production](#before-production).

**Contents:** [What it does](#what-it-does) · [Running locally](#running-locally-run_localsh) · [Architecture](#architecture) · [Data model](#data-model) · [API overview](#api-overview) · [Configuration](#configuration) · [Connecting the mobile app](#connecting-the-mobile-app) · [Deploying](#deploying-to-a-server-deploysh) · [nginx and HTTPS](#nginx-and-https-nginxsh) · [Dependencies](#dependencies) · [Before production](#before-production) · [Repository layout](#repository-layout)

## What it does

| Area | Summary |
|---|---|
| **Sign-in** | One login page for every role: email + password (argon2id, 10+ characters, common-password check, 5 failures lock the account for 15 min). Email confirmation and password reset by single-use emailed links. TOTP second factor, **required for doctors, clinics and staff**. 15-minute access tokens, rotating refresh tokens with reuse detection; the web app keeps its refresh token in an httpOnly SameSite=Strict cookie. The app's SMS one-time code still works |
| **Accounts** | Patient, doctor, clinic (self sign-up, role chosen on the form) and admin (CardiosensAI staff). Every account has a **16-digit ID**: role digit (1 patient, 2 doctor, 3 clinic, 9 staff) + 14 random digits + Luhn check digit. A clinic is an organisation with its own ID and, in v1, one login |
| **Links** | Patient↔doctor, patient↔clinic and doctor↔clinic, made by typing the other's 16-digit ID. Both sides approve (the requester's side on request), so the patient approves every patient link. An active patient link gives full, revocable access through an access grant; ending it stops access at once. A clinic's link doesn't extend to its doctors. A clinic can ask a patient to link with one of its doctors. Doctors and clinics must be verified by staff **and** use two-step sign-in before they can link or see anything |
| **Web app** | React SPA at `/`: the sign-in page, or the signed-in role's dashboard (patient home, doctor/clinic patient lists, clinic doctor roster, staff verification queue). Light and dark themes |
| **Devices** | Device models declare exactly which vitals they may provide. One active owner per physical device (model + serial). The app's own device id (`clientRef`) is accepted |
| **Readings** | `POST /v1/measurements:batch` is idempotent and returns an outcome per item. Plausibility, unit, timestamp and device-capability checks run on every item. Simulated readings and glucose control-solution tests are refused. Readings dated before the device was registered are refused |
| **History** | Keyset-paginated queries that keep each reading's original UTC offset, plus latest-per-vital |
| **ECG** | Metadata in PostgreSQL. Raw int16 samples go to object storage with a SHA-256 integrity check, and are served only after a permission check |
| **Sharing** | Patient-created grants: chosen vitals, history window (`from_now`, `last_30_days`, `all`), downloads on/off, notices, expiry. Single-use hashed invite tokens. Revocation is immediate, including report links already issued |
| **Reports** | PDF built in the background (reportlab) and downloadable only through 10-minute signed links bound to one user. Permission is re-checked on every download |
| **Audit** | Append-only trail of logins, sharing changes, viewer reads, downloads and admin actions. Values are never logged. Patients see an access log |
| **Admin** | Users (masked phone and email), suspension, doctor and clinic verification, moving app accounts to email sign-in, device models, ops dashboard and audit search. Requires two-step sign-in. Admins get **no** health data |
| **Privacy** | Data export (JSON) and account deletion (health data removed; audit and consent records kept) |

## Running locally (`run_local.sh`)

All three shell scripts live in `scripts/` and are run with `bash scripts/<name>.sh` from the repository root. They locate the repository themselves, so `bash /path/to/cardiosensAI_backend/scripts/<name>.sh` also works from any directory.

`scripts/run_local.sh` starts the whole stack on your machine with one command: PostgreSQL in Docker, the API and the web app, with demo accounts and data. It is for development only (development secrets, SMS codes returned by the API, emails printed instead of sent).

**Needs:** Docker (running, usable without sudo) and Node.js 20+. Python 3.13 is provided by [uv](https://docs.astral.sh/uv/); if `uv` isn't installed, the script downloads the standalone binary once into `var/tools/`.

```bash
bash scripts/run_local.sh            # http://localhost:8000  (API + built web app)
```

Then open http://localhost:8000 and sign in with a demo account:

| Account | Email | Password | Two-step code |
|---|---|---|---|
| Patient | `patient@demo.cardiosens.ai` | `cardiosens-demo-2026` | not needed |
| Doctor | `doctor@demo.cardiosens.ai` | `cardiosens-demo-2026` | `bash scripts/run_local.sh totp` |
| Clinic | `clinic@demo.cardiosens.ai` | `cardiosens-demo-2026` | `bash scripts/run_local.sh totp` |

The demo doctor and clinic are verified and already linked to the demo patient. Their authenticator key is `CARDIOSENSDEMOTOTPSECRETCODE2345`, so you can also add it to a real authenticator app. App (phone) sign-in demo numbers: patient `+919800000001`, doctor `+919800000002`, family member `+919800000003`.

### Commands and options

| Command | What it does |
|---|---|
| `bash scripts/run_local.sh` | Start everything on http://localhost:8000. Ctrl+C stops the API (the database keeps running) |
| `bash scripts/run_local.sh --dev` | Also run Vite on http://localhost:5173 with hot reload. Open that address while working on `web/`; it forwards `/v1` to the API. Emailed links then point to :5173 |
| `bash scripts/run_local.sh --reset` | Delete the local database and stored files first, then migrate and seed from scratch |
| `bash scripts/run_local.sh --admin-email you@example.com` | Also create a staff (admin) account. Its set-password link is printed in the terminal |
| `bash scripts/run_local.sh --no-demo` | Don't create the demo patient, doctor and clinic |
| `bash scripts/run_local.sh --port 8001` | Run the API on another port |
| `bash scripts/run_local.sh totp` | Print the current 6-digit code for the demo doctor and clinic |
| `bash scripts/run_local.sh stop` | Stop the database container (its data is kept) |
| `PG_PORT=55500 bash scripts/run_local.sh` | Use another host port for PostgreSQL (default 55433) |

Options can be combined, e.g. `bash scripts/run_local.sh --reset --dev --admin-email you@example.com`.

### What happens on each run

1. Starts (or creates) the `cardiosens-local-pg` container, PostgreSQL 17 on `127.0.0.1:55433`, data in the Docker volume `cardiosens-local-pgdata`.
2. Creates `.venv` with Python 3.13 and installs `requirements.txt` plus the dev tools from `pyproject.toml`, only when either file changed.
3. Runs `alembic upgrade head` and `app.scripts.seed --demo` (the seed is idempotent: a second run says "Demo data already present").
4. Installs `web/` dependencies when `package-lock.json` changed, and rebuilds `website/` when anything in `web/` is newer than the last build.
5. Starts uvicorn with `--reload` on `app/`: backend edits restart the API automatically.

The first run takes a few minutes (Docker image, Python, npm packages); later runs start in seconds.

### Emails and sign-up locally

Nothing is emailed. Confirmation, password-reset and set-password messages are printed in the terminal as `email_sent` log lines that include the link. To try sign-up: register at http://localhost:8000/register, copy the `/verify-email?token=...` link from the terminal into the browser, then sign in. A new doctor or clinic stays unverified until a staff account verifies it: start with `--admin-email`, open the printed set-password link, sign in, turn on two-step sign-in under Account, then use **Verification**.

### Local files

| Where | What |
|---|---|
| `.venv/` | Python virtualenv (git-ignored) |
| `var/tools/` | uv binary and Python 3.13 if the script downloaded them (git-ignored) |
| `var/storage/` | ECG waveforms and PDF reports (git-ignored, removed by `--reset`) |
| `web/node_modules/` | Web dependencies (git-ignored) |
| `website/` | Built web app (committed, rebuilt automatically when `web/` changes) |
| Docker volume `cardiosens-local-pgdata` | Database (removed by `--reset`) |

### Tests and checks

```bash
.venv/bin/pytest                                                   # backend tests (temporary SQLite)
TEST_DATABASE_URL=postgresql+psycopg://cardiosens:cardiosens@127.0.0.1:55433/cardiosens_test .venv/bin/pytest   # on PostgreSQL*
.venv/bin/ruff check . && .venv/bin/ruff format --check .
cd web && npm test && npm run typecheck
```

\* Create the test database once: `docker exec cardiosens-local-pg createdb -U cardiosens cardiosens_test`. Tests wipe it.

The 55 tests cover email sign-up, confirmation, lockout, password reset and the web session cookie; 16-digit IDs; patient/doctor/clinic linking (approval, verification and two-step gates, clinic roster, requests on a doctor's behalf, ending a link); moving app accounts to email; SMS sign-in, refresh-token theft detection, per-item ingestion outcomes, idempotency, pagination, sharing scope and history windows, revocation, grant expiry, admin isolation from health data, ECG integrity, signed report links, account deletion and the web app serving. `cd web && npm test` checks the client-side ID validation.

Before merging a change that touches models, also run `alembic check` against a migrated PostgreSQL database (no output = models and migrations agree).

### Troubleshooting

| Problem | Fix |
|---|---|
| `Docker isn't running (or your user can't reach it)` | Start Docker; add yourself to the `docker` group (`sudo usermod -aG docker $USER`, then log in again) |
| Port 8000, 5173 or 55433 already in use | `--port 8001`, stop the other Vite, or `PG_PORT=55500` |
| Database errors after switching branches with different migrations | `bash scripts/run_local.sh --reset` |
| Web app shows old screens | Reload the page; `index.html` is revalidated on every load, but an already-open tab keeps the old bundle |
| Doctor/clinic sign-in asks for a code | `bash scripts/run_local.sh totp` (codes change every 30 s) |

### Without the script

With Docker Compose (API + PostgreSQL only; build the web app first with `cd web && npm ci && npm run build`):

```bash
cp .env.example .env            # then set JWT_SECRET and SIGNING_KEY
docker compose up --build       # http://localhost:8000 (runs migrations and seeds device models)
```

By hand, against your own PostgreSQL:

```bash
uv venv -p 3.13 .venv && . .venv/bin/activate && uv pip install -r requirements.txt -r pyproject.toml --extra dev -c requirements.txt
export DATABASE_URL=postgresql+psycopg://cardiosens:cardiosens@localhost:5432/cardiosens DEV_EXPOSE_OTP=true
alembic upgrade head
python -m app.scripts.seed --demo --admin-email you@example.com
uvicorn app.main:app --reload
cd web && npm ci && npm run dev    # http://localhost:5173
```

## Architecture

### System overview

```mermaid
flowchart LR
  subgraph Devices
    BP[BP monitor<br/>BLE 0x1810]
    GL[Glucose meter<br/>BLE 0x1808]
    WR[CardiosensAI wearable]
  end
  App[Patient app<br/>Expo / React Native<br/>cardioSE-WEB repo]
  Web[Web app — React SPA<br/>patient · doctor · clinic · staff]
  subgraph Server[One Ubuntu server]
    NG[nginx<br/>TLS, gzip, 10 MB uploads]
    subgraph API[FastAPI modular monolith — uvicorn on 127.0.0.1:8000]
      R[/v1 routers/]
      S[services<br/>authz · accounts · ingestion<br/>reports · audit · email · sms]
      SITE[website/ static mount<br/>SPA fallback + CSP]
    end
    PG[(PostgreSQL)]
    FS[(Files: ECG waveforms,<br/>PDF reports)]
  end
  SMTP[[SMTP relay]]
  BP & GL & WR -- Bluetooth --> App
  App -- HTTPS /v1, bearer tokens --> NG
  Web -- HTTPS same origin --> NG
  NG --> API
  R --> S --> PG
  S --> FS
  S --> SMTP
```

- **One deployable.** The API, the web app and the background report jobs run in one uvicorn process behind nginx. The web app is a static build (`website/`) mounted by the API itself, so the browser talks to one origin and needs no CORS.
- **Route order matters.** `app/main.py` registers `/v1`, `/healthz`, `/readyz` and `/docs` first, and mounts the web app last (`install_website`), so the catch-all static mount can never shadow an API route. Unknown paths without a file extension get `index.html` (client-side routes); missing assets get a real 404.
- **Storage.** PostgreSQL holds everything except binary blobs. ECG waveforms and PDFs go through `core/storage.py` (local directory now, S3 ready via `STORAGE_BACKEND=s3`).
- **Split later, not now.** Modules are separated by layer so a service could be extracted when load or team size requires it.

### Backend layers

| Layer | Folder | Rule |
|---|---|---|
| HTTP | `app/api/v1/*.py` | Thin: parse, call services or queries, write the audit event, commit. One router per area |
| Dependencies | `app/api/deps.py` | `get_current_user` (bearer JWT → active user), `require_patient`, `require_admin` (also requires TOTP), `request_meta` (IP, request id, user agent) |
| Business rules | `app/services/` | `authz` (who may see health data), `accounts` (IDs, eligibility, emailed tokens), `ingestion`, `report_pdf`, `audit`, `notifications`, `email`, `sms`, `serializers` (ORM → response) |
| Persistence | `app/models/` | SQLAlchemy 2 models; timestamps are always UTC (`UTCDateTime`), JSON is JSONB on PostgreSQL |
| Contracts | `app/schemas/` | Pydantic models; camelCase on the wire (`CamelModel`) |
| Platform | `app/core/` | settings (with production guards), db, security (JWT, OTP hashing, signed links), `ids`, `passwords`, errors, storage, rate limiter, JSON logging |

Errors always have the shape `{"error": {"code", "message"}}`. "Not found" is also returned when the caller has no permission, so the existence of a patient is never revealed. Validation errors name the failing fields but never echo submitted values.

### Identity and sign-in

| Path | Who | Flow |
|---|---|---|
| Email + password | Everyone (web, later the app) | `POST /v1/auth/register` (always answers 202, so it can't reveal who has an account) → emailed link → `POST /v1/auth/email/verify` → `POST /v1/auth/login` |
| Two-step (TOTP) | Required for doctor, clinic, admin; optional for patients | Login returns `{mfaRequired, mfaToken}` (5 min) → `POST /v1/auth/mfa/verify` with the 6-digit code |
| Phone + SMS code | The patient app (until it moves to email) | `POST /v1/auth/otp/request` → `POST /v1/auth/otp/verify`; creates a patient account on first sign-in |
| Password reset | Everyone | `POST /v1/auth/password/forgot` (always 204) → emailed link → `POST /v1/auth/password/reset` (ends every session) |

| Credential | Lifetime | Storage |
|---|---|---|
| Access token (JWT, HS256) | 15 min | Web: memory only. App: secure store |
| Refresh token (opaque) | 30 days, rotated on every use | SHA-256 in `refresh_tokens`. Web: httpOnly, Secure, SameSite=Strict cookie on `/v1/auth`, accepted only with the `X-CS-Web: 1` header. App: response body. Reusing a rotated token revokes the whole session family |
| Emailed tokens | Verify 24 h, reset 1 h, admin set-password 72 h | SHA-256 in `action_tokens`, single use, bound to the address they were sent to |
| Passwords | — | argon2id; ≥10 characters, not in `core/common_passwords.txt`, not the email's local part. 5 wrong passwords lock the account for 15 min |

### 16-digit IDs

`app/core/ids.py` (mirrored for input checking in `web/src/format.ts`): **role digit + 14 random digits + Luhn check digit**. `1` patient, `2` doctor, `3` clinic, `9` staff. Random, so IDs can't be guessed or counted; the check digit rejects single-digit typos before any lookup. Users carry `users.unique_id`; a clinic's ID lives on `organizations.unique_id` and is copied onto its login. IDs are shown grouped (`1482 9301 7756 2048`) and accepted with spaces or dashes.

### Roles and what they can do

| | Patient | Doctor | Clinic | Admin (staff) |
|---|---|---|---|---|
| Sign up | Self | Self + registration number | Self + clinic details | `seed --admin-email` / `deploy.sh --admin-email` |
| Own health data | Read/write | — | — | — |
| Others' health data | — | Patients who approved a link | Patients who approved a link | **Never** |
| Prerequisites to link or view | Confirmed email | Confirmed email + staff verification + TOTP | Confirmed email + staff verification + TOTP | TOTP for the admin console |
| Dashboard (`/`) | Patient home | Patient list | Patient list + doctor roster | Verification queue, users |

### Authorization: one gate for health data

Every health-data read goes through `services/authz.get_access(db, actor, patient_id, need=...)`:

1. Admins → "not found". Unknown, deleted or non-patient targets → "not found".
2. The patient themself → full access.
3. Anyone else needs an **active, unexpired `access_grant`** from that patient that lists the requested vital (or `ecg`), and only sees data inside the grant's history window.
4. If the grant is for a doctor or clinic, or the viewer is one, `accounts.can_view_patients` must also hold (verified + TOTP + active). This is re-checked on every request, so revoking verification or MFA cuts access immediately.

Report downloads re-run the same check when the signed link is used, so revoking access also kills links already issued. Every viewer read is written to `audit_events`, which the patient sees as their access log.

### Links (care relationships)

`care_links` joins two IDs: `patient_doctor`, `patient_clinic` or `doctor_clinic`. API: `app/api/v1/links.py`.

```mermaid
stateDiagram-v2
  [*] --> pending: POST /v1/links (requester's side approves)
  pending --> active: other side approves
  pending --> declined: either side declines / cancels
  active --> ended: either side ends (POST /links/{id}/end)
  declined --> [*]
  ended --> [*]
```

- **Both sides approve**; the requester's approval is implied. So the patient approves every patient link, and doctor↔clinic links need the doctor and the clinic.
- **Activation of a patient link creates an `access_grant`** (all vitals + ECG, full history, downloads on) for the doctor or the clinic's login, and stores its id on the link. **Ending the link revokes that grant.** This keeps `authz.py` the only gate; links never grant access by themselves.
- **No inheritance.** A patient↔clinic link gives the clinic account access, not the clinic's doctors. A clinic can request a patient↔doctor link on behalf of one of its own doctors (doctor side pre-approved; patient must approve).
- **Lookup before linking.** `GET /v1/directory/{id}` returns name and role only, rate-limited; patient IDs can only be looked up by verified doctors and clinics.
- One open (pending or active) link per pair; declined and ended links stay as history. Deleting a patient or doctor account ends their links.

The older invite-based sharing (`/v1/grants`, `/v1/invites/accept`) remains for family members chosen from the app, with per-vital scopes and history windows.

### Readings and ECG

1. The device sends a reading to the phone over BLE; the app parses the standard profile and creates a `Measurement` with a stable `id`, queued offline.
2. `POST /v1/measurements:batch` (≤100 items) validates each item (unit, plausibility, timestamp skew, device capability, registration date) and stores it once: unique `(patient_id, client_id)` plus a content hash makes retries idempotent. Each item gets its own outcome; rejections keep only a reason code (`ingestion_rejections`).
3. Simulated readings and glucose control-solution tests are refused by design.
4. ECG metadata goes to `ecg_recordings`; raw int16 samples to storage with a SHA-256 that is verified on every read (`/samples` streams them after the permission check).
5. History is keyset-paginated and keeps each reading's original UTC offset.

### Reports

`POST /v1/reports` returns 202 and a FastAPI background task renders the PDF (reportlab) into storage. `POST /v1/reports/{id}/download-link` returns a 10-minute HMAC-signed URL bound to one user and report; the download re-checks permission.

### Web app (`web/` → `website/`)

| Piece | File | Notes |
|---|---|---|
| Entry, routes | `src/main.tsx`, `src/App.tsx` | React Router. `/` = sign-in or the role's home. `/links`, `/doctors`, `/patients/:id`, `/settings`, `/admin/users`, `/register`, `/verify-email`, `/forgot-password`, `/reset-password` |
| Session | `src/auth.tsx`, `src/api.ts` | On load calls `/v1/auth/refresh` (cookie) to restore the session; a 401 triggers one silent refresh. Access token only in memory |
| Screens | `src/pages/*` | `PatientHome`, `ProDashboard` (doctor/clinic list, patient detail, clinic doctors), `LinksPage`, `Settings`, `Admin`, `AuthPages` |
| Components | `src/components/*` | ID card with QR, vitals + SVG trend chart (gaps never interpolated), ECG strip on 25 mm/s × 10 mm/mV paper, reports, link tables, TOTP setup, setup checklist |
| Theme | `src/theme.css` | Light palette from the patient app, dark from the V1.0 site; follows the OS, toggle stored in `localStorage` |

The build output is committed in `website/` (hashed assets cached for a year, `index.html` revalidated on every load), so servers need no Node.js. `app/website.py` adds security headers: a Content-Security-Policy that allows only same-origin scripts (plus the hash of the inline theme script) and same-origin API calls, `frame-ancestors 'none'`, `nosniff`, `Referrer-Policy: same-origin`, and a Permissions-Policy.

After changing `web/`, rebuild and commit both folders: `cd web && npm ci && npm test && npm run typecheck && npm run build` (or let `run_local.sh` rebuild it).

### Security summary

- Secrets refuse development values in staging/production (`core/config.py`), as do `DEV_EXPOSE_OTP`, the console SMS sender and the console email sender.
- Rate limits (in-process): sign-in by IP, OTP by phone and IP, registration, resends, lookups, link requests, report generation.
- Audit events never contain health values. Request logs record the route template (`/v1/reports/download/{token}`), not the URL, so ids and signed tokens stay out of logs; uvicorn's own access log is off. Phone numbers and emails are masked in logs and the admin console.
- Admins can manage accounts but get no health data from any endpoint.
- Deletion removes health data and personal details, ends grants and links, and keeps consent and audit records.

## Data model

```mermaid
erDiagram
  organizations ||--o{ users : "clinic login"
  users ||--o| professional_profiles : "doctor verification"
  users ||--o{ care_links : "patient / doctor side"
  organizations ||--o{ care_links : "clinic side"
  care_links |o--o| access_grants : "creates on activation"
  users ||--o{ access_grants : "patient / grantee"
  access_grants ||--o{ invites : "single-use token"
  users ||--o{ devices : owns
  device_models ||--o{ devices : "model of"
  users ||--o{ measurements : "patient of"
  devices ||--o{ measurements : "measured by"
  users ||--o{ ecg_recordings : "patient of"
  users ||--o{ reports : "about / requested by"
  users ||--o{ refresh_tokens : sessions
  users ||--o{ action_tokens : "emailed links"
  users ||--o{ consents : gives
  users ||--o{ notifications : receives
  users {
    uuid id PK
    string unique_id UK "16-digit ID"
    string email UK "lower-case"
    timestamptz email_verified_at
    string password_hash "argon2id"
    string phone UK "optional (app sign-in)"
    string role "patient|doctor|clinic|admin"
    uuid organization_id FK "clinic login"
    string status "active|suspended|deleted"
    bool mfa_enabled
    int failed_logins
    timestamptz locked_until
  }
  organizations {
    uuid id PK
    string unique_id UK "3xxx…"
    string name
    string registration_number
    uuid owner_user_id FK
    string verification_status "pending|verified|rejected"
  }
  care_links {
    uuid id PK
    string kind "patient_doctor|patient_clinic|doctor_clinic"
    uuid patient_id FK
    uuid doctor_id FK
    uuid organization_id FK
    string status "pending|active|declined|ended"
    timestamptz patient_ok_at
    timestamptz doctor_ok_at
    timestamptz clinic_ok_at
    uuid grant_id FK
  }
  access_grants {
    uuid id PK
    uuid patient_id FK
    uuid grantee_user_id FK
    string role "family|caregiver|doctor|clinic"
    jsonb vitals
    string history "from_now|last_30_days|all"
    bool downloads_allowed
    string status "pending|active|revoked"
  }
  measurements {
    uuid id PK
    uuid patient_id FK
    string client_id "idempotency key"
    string vital
    float value
    timestamptz measured_at
    smallint utc_offset_min
    string acquisition "live|delayed|manual|simulated"
  }
```

Also present: `device_models`, `ecg_recordings` (storage key + SHA-256), `otp_challenges`, `refresh_tokens`, `action_tokens`, `invites`, `reports`, `consents`, `notifications`, `ingestion_rejections` (reason codes only) and `audit_events` (never health values).

**Indexes:** `measurements(patient_id, vital, measured_at)` for history, unique `(patient_id, client_id)` for idempotency, partial unique `(model_id, serial) WHERE status='active'` for device ownership, `audit_events(patient_id, at)`, and `care_links` by patient, doctor and organisation.

**Migrations** (`alembic/versions/`):

| Revision | Change |
|---|---|
| `0001` | Initial schema (phone sign-in, grants, readings, ECG, reports, audit) |
| `0002` | Email + password, `organizations`, `care_links`, `action_tokens`, 16-digit IDs back-filled for every user, caregivers → patients, phone optional, unique lower-case email. Downgrade refuses if email-only accounts exist |

`alembic upgrade head` applies them (`run_local.sh` and `deploy.sh` do it for you). After changing models: `alembic revision --autogenerate -m "..."`, review the file, then check with `alembic check` against PostgreSQL. The two foreign keys that close the users↔organizations cycle are named (`fk_organizations_owner`, `fk_organizations_verified_by`) so `drop_all` works.

**Retention (to be decided with legal review):** readings until the account is deleted; ingestion rejections can be pruned after 90 days; OTP challenges, used action tokens and expired refresh tokens daily; audit events per counsel's advice.

## API overview

Interactive docs at `/docs` (disabled when `ENV=production`); the full spec is `docs/openapi.json` (regenerate with `python -c "import json; from app.main import app; json.dump(app.openapi(), open('docs/openapi.json','w'), indent=2)"`).

| Area | Endpoints |
|---|---|
| Sign-in | `POST /v1/auth/register`, `/login`, `/mfa/verify`, `/refresh`, `/logout`, `/logout-all`, `/email/verify`, `/email/resend`, `/password/forgot`, `/password/reset`, `/password/change`, `GET /v1/auth/status`; app: `/otp/request`, `/otp/verify`; TOTP: `/mfa/setup`, `/mfa/enable`, `/mfa/disable` |
| Me | `GET/PATCH/DELETE /v1/me`, `/me/consents`, `/me/professional-profile` (doctor), `/me/organization` (clinic), `/me/export`, `/me/access-log` |
| Links | `GET /v1/directory/{id}`, `GET/POST /v1/links`, `POST /v1/links/{id}/approve|decline|end`, `GET /v1/linked-patients`, `GET /v1/clinic/doctors` |
| Health data | `POST /v1/measurements:batch`, `GET /v1/patients/{id}/measurements[/latest]`, `POST /v1/ecg-recordings`, `GET /v1/patients/{id}/ecg-recordings`, `GET /v1/ecg-recordings/{id}[/samples]` (`{id}` may be `me`) |
| Devices | `GET /v1/device-models`, `GET/POST /v1/devices`, `PATCH /v1/devices/{id}/status`, `DELETE /v1/devices/{id}` |
| Family sharing | `GET/POST /v1/grants`, `POST /v1/grants/{id}/revoke`, `POST /v1/invites/accept`, `GET /v1/shared-with-me` |
| Reports | `POST/GET /v1/reports`, `GET /v1/reports/{id}`, `POST /v1/reports/{id}/download-link`, `GET /v1/reports/download/{token}` |
| Notifications | `GET /v1/notifications`, `POST /v1/notifications/{id}/read`, `/read-all` |
| Admin | `/v1/admin/users` (+ `/status`, `/email`), `/professionals` (+ `/verify`), `/organizations` (+ `/verify`), `/device-models`, `/ops`, `/audit-events` |
| Ops | `GET /healthz` (process up), `GET /readyz` (database reachable) |

## Configuration

All settings are environment variables (`app/core/config.py`; locally `.env`, see `.env.example`; on the server `/etc/cardiosensai/backend.env`). In `staging` and `production` the app **refuses to start** with development secrets, `DEV_EXPOSE_OTP=true`, `SMS_PROVIDER=console`, `EMAIL_PROVIDER=console` (it would write password-reset links to the log), or `EMAIL_PROVIDER=smtp` without `SMTP_HOST`.

| Variable | Default | Meaning |
|---|---|---|
| `ENV` | `development` | `development`, `test`, `staging`, `production` (production also hides `/docs` and makes cookies `Secure`) |
| `DATABASE_URL` | local PostgreSQL | SQLAlchemy URL, `postgresql+psycopg://...` |
| `JWT_SECRET`, `SIGNING_KEY` | dev values | ≥32 random characters each outside development (`deploy.sh` generates them) |
| `API_BASE_URL` | `http://localhost:8000` | Base of report download links and invite URLs |
| `WEB_BASE_URL` | `http://localhost:5173` | Base of links in emails (verify, reset). Same host as the API in production |
| `EMAIL_PROVIDER` | `console` | `console` (log only), `smtp`, `none` |
| `EMAIL_FROM`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_TLS`, `SMTP_USERNAME`, `SMTP_PASSWORD` | — / `587` / `starttls` | SMTP relay (Zoho, Google Workspace, Amazon SES SMTP, Brevo...). `SMTP_TLS`: `starttls`, `ssl`, `none` |
| `SMS_PROVIDER` | `console` | App sign-in codes: `console` (log), `none` (503 `sms_unavailable`). No real provider yet: plug a DLT-registered one into `app/services/sms.py` |
| `DEV_EXPOSE_OTP` | `false` | Development only: return SMS codes in the API response |
| `STORAGE_BACKEND`, `STORAGE_LOCAL_PATH`, `S3_*` | `local`, `./var/storage` | Where ECG waveforms and PDFs go |
| `CORS_ORIGINS` | `[]` | Only needed for a web client on another origin (the built-in web app is same-origin) |
| Lifetimes and limits | see `config.py` | `ACCESS_TOKEN_TTL_S` 900, `REFRESH_TOKEN_TTL_S` 30 d, `EMAIL_VERIFY_TTL_S` 24 h, `PASSWORD_RESET_TTL_S` 1 h, `LOGIN_MAX_FAILURES` 5, `LOGIN_LOCKOUT_S` 900, `MAX_BATCH_SIZE` 100 |

## Connecting the mobile app

The patient app ([cardioSE-WEB](https://github.com/mazumdersayon-del/cardioSE-WEB)) talks to this API when it's built with `EXPO_PUBLIC_API_BASE_URL` set and simulation off (see its README). Its `src/sync/backend.ts`:

1. Signs in with `/v1/auth/otp/request` and `/v1/auth/otp/verify` (plus `/v1/auth/mfa/verify` when two-step sign-in is on). Tokens live in `expo-secure-store` and are refreshed with `/v1/auth/refresh`.
2. Records onboarding consent with `POST /v1/me/consents` (`data_processing`).
3. Registers each real device with `POST /v1/devices` on its first sync, with `clientRef` set to the app's local device id, so readings keep using the app's ids. Readings from a device that isn't registered yet stay queued on the phone.
4. Uploads through `POST /v1/measurements:batch` and reports sync outcomes with `PATCH /v1/devices/{id}/status`.
5. After sign-in, pulls the profile, devices (`bleIdentifier` lets a reinstalled app reconnect) and the last 30 days of readings.

The app's **simulated** readings are refused by design (`reason: Simulated readings are never stored as health data`).

To check the app against a running server (development mode, so codes come back in the response):

```bash
CARDIOSENS_E2E_API=http://127.0.0.1:8000 npx jest backend.e2e   # in the app repository
```

## Deploying to a server (`deploy.sh`)

`deploy.sh` installs and updates everything on **one Ubuntu 26.04 server** (the Vultr box). It is safe to re-run: every run copies the current code, installs dependencies, applies migrations and restarts the service, keeping settings, secrets and data.

### What it sets up

| Part | Where / how |
|---|---|
| PostgreSQL | Local server, database and role `cardiosens`, password generated once |
| Python 3.13 | Installed by uv into `/opt/cardiosensai/python`; virtualenv `/opt/cardiosensai/venv` |
| Code | Copied (rsync) to `/opt/cardiosensai/app`, root-owned. The checkout you run from is only the source |
| Settings and secrets | `/etc/cardiosensai/backend.env` (`root:cardiosens`, 0640). `JWT_SECRET`, `SIGNING_KEY` and the DB password are generated on the first run and never changed |
| Service | systemd `cardiosensai-api`: uvicorn on `127.0.0.1:8000`, runs as user `cardiosens`, sandboxed (read-only system, writes only `/var/lib/cardiosensai`) |
| Files | ECG waveforms and PDF reports in `/var/lib/cardiosensai/storage` |
| nginx + HTTPS | Via `nginx.sh` (below): web app at `/`, API at `/v1`, Let's Encrypt certificate |
| Backups | Daily at 02:30 (systemd timer): database dump + storage archive in `/var/backups/cardiosensai`, kept 14 days. Plus `pre-migrate-*.dump` before any run that applies a migration |
| Firewall | ufw: SSH (your configured port), 80 and 443 only |

At the end it checks that the API answers `/readyz`, that nginx passes requests through, and that the web app loads, and prints the URLs.

### Options

Each option can also be set as the environment variable in brackets. Settings from earlier runs (`--domain`, `--email`, `--mode`, `--workers`) are remembered in `/etc/cardiosensai/deploy.env`, so later runs need no options.

| Option | Meaning |
|---|---|
| `--domain NAME` [`DOMAIN`] | Public host name, e.g. `app.cardiosens.ai`. Empty: plain HTTP on the server's IP |
| `--email ADDRESS` [`LETSENCRYPT_EMAIL`] | Let's Encrypt account email (expiry notices). Using it accepts the Let's Encrypt terms |
| `--mode production\|demo` [`MODE`] | `production` (default): real settings; requires SMTP. `demo`: SMS codes returned by the API, emails written to the service log. **Anyone can sign in as any phone number**: test data only |
| `--admin-email ADDRESS` [`ADMIN_EMAIL`] | Create or promote a staff account; a set-password link is emailed and printed |
| `--admin PHONE` [`ADMIN_PHONE`] | Create or promote a staff account by phone (app sign-in) |
| `--demo-data` | Add the demo patient, doctor, clinic and family member (`--mode demo` only) |
| `--workers N` [`WORKERS`] | uvicorn worker processes (default 1). Request rate limits are kept per process |
| `--no-tls` | Don't request a certificate even with a domain |
| `--no-firewall` | Leave ufw alone |
| `-h`, `--help` | Show the built-in help |

### First production deployment

1. **DNS:** create an `A` record for `app.cardiosens.ai` pointing at the server's IP. Let's Encrypt needs it (and port 80 reachable) to issue the certificate.
2. **Code:** on the server,
   ```bash
   git clone https://github.com/mazumdersayon-del/cardiosensAI_backend.git
   cd cardiosensAI_backend
   ```
3. **Email:** production refuses to run without an SMTP relay (sign-up confirmation and password reset depend on it). Create the settings file with your provider's details:
   ```bash
   sudo install -d -m 0750 /etc/cardiosensai
   sudo tee -a /etc/cardiosensai/backend.env >/dev/null <<'CONF'
   EMAIL_PROVIDER=smtp
   EMAIL_FROM=CardiosensAI <no-reply@cardiosens.ai>
   SMTP_HOST=smtppro.zoho.in
   SMTP_PORT=587
   SMTP_TLS=starttls
   SMTP_USERNAME=no-reply@cardiosens.ai
   SMTP_PASSWORD=your-smtp-password
   CONF
   sudo chmod 0600 /etc/cardiosensai/backend.env   # deploy.sh sets root:cardiosens 0640 on its first run
   ```
   Use port `465` with `SMTP_TLS=ssl` if your provider wants implicit TLS. Set up SPF/DKIM for the sending domain, or the mails land in spam.
4. **Deploy:**
   ```bash
   sudo bash scripts/deploy.sh --domain app.cardiosens.ai --email ops@cardiosens.ai --admin-email ops@cardiosens.ai
   ```
5. **Staff account:** open the set-password link (emailed, and printed in the output), choose a password, sign in at https://app.cardiosens.ai, and turn on two-step sign-in under **Account** (required before the admin console works).
6. **Check:** sign up a test patient, confirm the email arrives, and look at the service log (`journalctl -u cardiosensai-api -f`).

### Updating

```bash
cd cardiosensAI_backend
git pull
sudo bash scripts/deploy.sh
```

Settings, secrets, the database and stored files are kept. If the new code has a database migration, the database is dumped to `/var/backups/cardiosensai/pre-migrate-<time>.dump` first, then `alembic upgrade head` runs, then the service restarts (a few seconds of downtime). The web app is the committed build in `website/`; the server needs no Node.js.

### Upgrading the existing production server to email sign-in (migration 0002)

This release changes how people sign in, so do it deliberately:

1. Copy the latest backup off the server first (`scp root@server:/var/backups/cardiosensai/db-*.dump .`).
2. Add the SMTP settings to `/etc/cardiosensai/backend.env` (step 3 above; the file already exists).
3. Point `app.cardiosens.ai` at the server. Then `git pull && sudo bash scripts/deploy.sh --domain app.cardiosens.ai --email ops@cardiosens.ai --admin-email ops@cardiosens.ai`. The domain changes from the remembered one, so pass it explicitly; `nginx.sh` gets a certificate for the new name.
4. What the migration does: every account keeps its data and gets a 16-digit ID; caregiver accounts become patient accounts (their sharing grants keep working); phone becomes optional; emails are lower-cased (a duplicate address on a newer account is cleared and printed in the output).
5. Existing users have no password. In the web app, staff open **Users**, search by name, 16-digit ID or last phone digits, and choose **Set email…**: the user gets a link (valid 72 h) to choose a password, which also confirms the address. App users can also add an email under Profile in the app and use **Forgot password** on the web.
6. Doctors who already see patients through app invites must now be **verified** and turn on **two-step sign-in** before they see data again. Verify them under **Verification** and tell them.
7. Phone sign-in in the app keeps working; the app moves to email later.

### Day-to-day operations

```bash
systemctl status cardiosensai-api                 # service state
journalctl -u cardiosensai-api -f                 # live logs (JSON); add --since "1 hour ago" to look back
sudo systemctl restart cardiosensai-api           # after editing /etc/cardiosensai/backend.env
sudo systemctl start cardiosensai-backup          # take a backup now
ls -lh /var/backups/cardiosensai                  # backups (same disk: copy them elsewhere regularly)
sudo -u postgres psql cardiosens                  # database shell
```

### Restoring and rolling back

To undo a bad release, go back to the previous code **and** the database as it was before the migration:

```bash
sudo systemctl stop cardiosensai-api
git checkout <previous-commit>                                       # code that matches the dump
sudo -u postgres pg_restore --clean --if-exists -d cardiosens /var/backups/cardiosensai/pre-migrate-<time>.dump
sudo bash scripts/deploy.sh                                                     # reinstalls that code and starts the service
```

Restore ECG and report files, if needed, from `storage-<time>.tar.gz` into `/var/lib/cardiosensai/` (`sudo tar -C /var/lib/cardiosensai -xzf ...`). Data created after the dump is lost, so note the time and tell affected users.

### Demo or test server

```bash
sudo bash scripts/deploy.sh --mode demo --demo-data                    # plain HTTP on the server IP
sudo bash scripts/deploy.sh --mode demo --demo-data --domain test.cardiosens.ai --email ops@cardiosens.ai
```

Emails go to `journalctl -u cardiosensai-api` instead of being sent, and SMS codes come back in the API response. Never put real patients on a demo server.

## nginx and HTTPS (`nginx.sh`)

`nginx.sh` installs nginx and certbot, writes the site config and gets the certificate. `deploy.sh` runs it on every deploy; run it on its own to add HTTPS once DNS is ready, to change the domain, or to rewrite the config.

```bash
sudo bash scripts/nginx.sh                                                        # re-apply the remembered domain and email
sudo bash scripts/nginx.sh --domain app.cardiosens.ai --email ops@cardiosens.ai   # HTTPS for this name
sudo bash scripts/nginx.sh --no-tls                                               # plain HTTP (testing on the IP)
```

| Option | Meaning |
|---|---|
| `--domain NAME` [`DOMAIN`] | Host name to serve. Empty: plain HTTP for any name or the IP |
| `--email ADDRESS` [`LETSENCRYPT_EMAIL`] | Let's Encrypt account email; needed for the first certificate |
| `--port N` [`API_PORT`] | Port the API listens on at 127.0.0.1 (default 8000) |
| `--no-tls` | Don't request a certificate |
| `-h`, `--help` | Show the built-in help |

What it does:

- Writes `/etc/nginx/sites-available/cardiosensai` (enabled; the default site is removed). Everything is proxied to the API, which serves the web app at `/` and the API at `/v1`. Uploads up to 10 MB, gzip for the web app's files, `X-Forwarded-*` and `X-Request-ID` headers, 60 s proxy timeout. The file is overwritten on every run: don't edit it by hand.
- With a domain and no certificate yet: serves the ACME challenge over HTTP, runs `certbot certonly --webroot`, then switches to HTTPS (TLS 1.2/1.3, HTTP/2) with HTTP redirecting to HTTPS. If the request fails (DNS not pointing here, port 80 closed), it stays on HTTP, warns, and you re-run it later.
- Renewals: certbot's own timer renews the certificate; a deploy hook reloads nginx afterwards. Check with `sudo certbot renew --dry-run`.
- Updates `API_BASE_URL` and `WEB_BASE_URL` in `backend.env` to `https://DOMAIN` (they build report download links and the links in emails) and restarts the API if they changed.
- Opens ports 80 and 443 if ufw is active, and remembers the domain and email in `/etc/cardiosensai/deploy.env`.

| File | What |
|---|---|
| `/etc/cardiosensai/backend.env` | API settings and secrets (`root:cardiosens`, 0640) |
| `/etc/cardiosensai/deploy.env` | Remembered deploy options (domain, email, mode, workers) |
| `/etc/nginx/sites-available/cardiosensai` | Generated nginx site |
| `/etc/letsencrypt/live/DOMAIN/` | Certificate and key |
| `/opt/cardiosensai/app`, `/opt/cardiosensai/venv` | Installed code and virtualenv |
| `/var/lib/cardiosensai/storage` | ECG waveforms and PDF reports |
| `/var/backups/cardiosensai` | Daily and pre-migration backups, kept 14 days |

## Dependencies

| What | Where | How to change |
|---|---|---|
| Python, direct | `pyproject.toml` → `[project].dependencies` (ranges) | Edit, then regenerate the lock |
| Python, lock | `requirements.txt` (every package pinned, generated) | `uv pip compile pyproject.toml --universal --python-version 3.13 --no-header --annotation-style line -o requirements.txt` (keeps existing pins; add `--upgrade-package NAME` to bump one). Keep the 3-line header |
| Python, dev tools | `pyproject.toml` → `[project.optional-dependencies].dev` (exact pins) | Edit the pin |
| Web | `web/package.json` (exact versions) + `web/package-lock.json` | `cd web && npm install --save-exact PKG@VERSION` |

Production (`deploy.sh`, `Dockerfile`) installs only `requirements.txt`. Development installs both: `uv pip install -r requirements.txt -r pyproject.toml --extra dev -c requirements.txt` (what `run_local.sh` does).

## Before production

- **SMS provider:** plug a DLT-registered Indian SMS provider into `app/services/sms.py` (only the app's phone sign-in needs it).
- **Verification evidence:** doctors and clinics give registration numbers only; staff check them against the council or state register by hand. Add document upload and a record of what was checked.
- **CI:** this repository has no CI pipeline yet. Add one that runs ruff, pytest on PostgreSQL, `alembic upgrade head` + `alembic check` + downgrade, and `cd web && npm ci && npm test && npm run build` with a check that `website/` is up to date.
- **Clinic staff:** a clinic has one login in v1. `organizations` is separate from `users` so staff logins can be added without a schema change.
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

## Repository layout

```
app/
  main.py                FastAPI app: middleware, error handlers, /v1 routers, then the web app mount (last)
  website.py             serves website/ at / with SPA fallback, cache headers and CSP
  api/deps.py            current user, role guards (admin needs TOTP), request metadata
  api/v1/                auth, me, links, devices, measurements, ecg, sharing, notifications, reports, audit, admin
  core/                  config (+ production guards), db, security, ids, passwords (+ common_passwords.txt), errors, storage, ratelimit, observability
  models/                user (users, organizations, professional_profiles, consents), link, sharing, auth, device, health, report, notification, audit
  schemas/               request/response models (camelCase)
  services/              authz, accounts, ingestion, report_pdf, audit, notifications, email, sms, serializers
  scripts/seed.py        device models, admin by phone or email, demo data
alembic/                 migrations (0001 initial, 0002 email/roles/links)
tests/                   pytest suite (conftest helpers: login, enable_mfa, make_admin, reading)
web/                     web app source (React 19 + Vite + TypeScript)
website/                 web app build, committed, served at /
docs/openapi.json        generated API spec
scripts/
  run_local.sh           local stack (Docker PostgreSQL + API + web app + demo data)
  deploy.sh              single-server install/update for Ubuntu 26.04 (runs nginx.sh)
  nginx.sh               nginx + Let's Encrypt (run by deploy.sh, or on its own)
Dockerfile, docker-compose.yml   container image and a local compose stack
pyproject.toml           project metadata, direct dependencies, ruff and pytest settings
requirements.txt         generated runtime lock
CLAUDE.md                condensed context for AI coding assistants
```

## Licence

Proprietary — © CardiosensAI Private Limited. All rights reserved.
