# CLAUDE.md — CardiosensAI backend + web app

Condensed context. README.md is the full reference (architecture, data model, API, deploy); read the relevant section there instead of re-deriving it from code.

## What this is
FastAPI + PostgreSQL API (`/v1`) and a React SPA (`/`) in one deployable. Roles: **patient, doctor, clinic** (self sign-up) + **admin** (staff). Everyone has a **16-digit ID** (role digit 1/2/3/9 + 14 random + Luhn). Health data. Not for clinical use. Production runs on one Ubuntu 26.04 server with **real users**.

## Commands
```bash
bash scripts/run_local.sh [--dev] [--reset]        # Docker PG :55433 + migrations + demo data + API :8000 (+ Vite :5173)
bash scripts/run_local.sh totp                     # demo doctor/clinic TOTP code; demo password cardiosens-demo-2026
bash scripts/run_local.sh ecg-live 120             # stream a synthetic 12-lead live ECG for the demo patient
.venv/bin/pytest -q                     # 64 tests, SQLite; TEST_DATABASE_URL=postgresql+psycopg://... for PG
.venv/bin/ruff check . && .venv/bin/ruff format --check .   # line length 140
cd web && npm test && npm run typecheck && npm run build    # build writes ../website (committed)
.venv/bin/alembic revision --autogenerate -m "..."  && alembic check   # after model changes, against PG
```
Done = ruff clean + pytest green (ideally also on PG) + web typecheck/test/build if `web/` changed + README updated.

## Layout (see README "Repository layout")
`app/api/v1/*` thin routers → `app/services/*` rules → `app/models/*` (SQLAlchemy 2) · `app/schemas/*` (Pydantic, camelCase via `CamelModel`) · `app/core/*` (config, ids, passwords, security, storage, ratelimit) · `alembic/versions/` · `tests/` · `web/src/` → `website/`.

## Invariants (do not break)
- **`services/authz.get_access` is the only gate for health data.** Access = patient self, or an active unexpired `access_grant`. Doctor/clinic viewers also need `accounts.can_view_patients` (staff-verified + TOTP + active), re-checked on every request. Admins never get health data. No-permission answers are 404, not 403, for patient lookups.
- **Links never grant access directly.** Activating a patient `care_link` creates an `AccessGrant`; ending it revokes that grant. Both sides approve; requester's side is implied. Clinic links do not extend to its doctors.
- `can_link` requires confirmed email; doctors/clinics also verification + TOTP. TOTP can't be disabled for doctor/clinic/admin; `require_admin` requires TOTP.
- Register / forgot-password / resend never reveal whether an account exists. Emailed tokens and refresh tokens are stored as SHA-256 only, single use.
- Web session: refresh token only in httpOnly SameSite=Strict cookie (`/v1/auth`) + `X-CS-Web: 1` header; access token in memory only.
- Audit events and logs never contain health values; logs use route templates, not URLs.
- Documents: visible to the patient and doctor/clinic grants only (not family shares); type by content signature (PDF/JPEG only); only doctors comment; files via signed links. Live ECG: polling (no WebSocket), chunks idempotent on seq, `services/ecg.finalize` turns a session into a recording; high-frequency views log once per viewer (`ecg.live_view`, `dashboard.summary` hourly).
- ECG samples are interleaved int16 frames in `leads` order; `sample_count` = frames.
- Timestamps are UTC (`UTCDateTime` rejects naive datetimes). Measurement ingestion is idempotent on `(patient_id, client_id)`.
- `install_website(app)` stays **last** in `create_app` so the static mount can't shadow API routes.
- Production config guards in `core/config.py` (no dev secrets, no console SMS/email) must stay.
- Migrations must preserve production data; `0002` back-fills IDs and folds caregiver → patient. Named FKs `fk_organizations_owner/verified_by` close the users↔organizations cycle.

## Conventions
- Errors: raise `ApiError` helpers (`not_found`, `forbidden`, `bad_request`, `conflict`) → `{"error": {"code","message"}}`. Messages are user-facing plain English.
- New endpoint: router function + `audit.record(...)` for anything security-relevant + `db.commit()` in the router + test in `tests/`.
- Tests: helpers in `tests/conftest.py` (`login` phone OTP, `auth`, `enable_mfa`, `make_admin`, `set_role`, `reading`); email outbox via `app.services.email.outbox`; `tests/test_accounts_links.py` has `signup` / `ready_professional`.
- Dependencies: direct deps in `pyproject.toml`; `requirements.txt` is a **generated lock** (`uv pip compile ...`, command in its header) — never hand-edit; dev tools are exact pins in the `dev` extra. Web deps are exact versions.
- Web: React 19 + react-router 7, no UI library; styles in `web/src/theme.css` tokens (light + dark). Mirror server rules for input checks (`web/src/format.ts` Luhn = `app/core/ids.py`).
- Phone numbers: always `components/PhoneInput` (country dropdown → E.164); server validates `^\+[1-9]\d{7,14}$`. Country data/length rules in `web/src/countries.ts`.
- After changing `web/`, commit the rebuilt `website/` too (servers have no Node).

## Deploy (README "Deploying")
`git pull && sudo bash scripts/deploy.sh` on the server. Production needs SMTP vars in `/etc/cardiosensai/backend.env`; deploy dumps the DB before pending migrations. `nginx.sh` handles nginx + Let's Encrypt for `app.cardiosens.ai`. Never run demo mode or demo data against production. Don't deploy, push or run migrations on the server without the user's go-ahead.

## Related
- Patient app: `../cardioSE-WEB` (Expo; still uses phone OTP — `/v1/auth/otp/*` must keep working).
- Old marketing page archive: `../../Assets/V1.0/`.
- `credential.txt` (git-ignored) lists local demo accounts and IDs.
