# CLAUDE.md — CardiosensAI backend + web app

Condensed context. README.md is the full reference (architecture, data model, API, deploy); read the relevant section there instead of re-deriving it from code.

## What this is
FastAPI + PostgreSQL API (`/v1`) and a React SPA (`/`, staff portal at `/admin`) in one deployable. Roles: **patient, doctor, clinic** (self sign-up) + **admin** (staff). Everyone has a **16-digit ID** (role digit 1/2/3/9 + 14 random + Luhn). Health data. Not for clinical use. Production runs on one Ubuntu 26.04 server with **real users**.

## Commands (every script and option: README → "Scripts")
```bash
bash scripts/run_local.sh [--dev] [--reset]        # Docker PG :55433 + migrations + demo data + API :8000 (+ Vite :5173)
bash scripts/run_local.sh totp                     # demo doctor/clinic TOTP code; demo password cardiosens-demo-2026
bash scripts/run_local.sh ecg-live 120             # stream a synthetic 12-lead live ECG for the demo patient
python3 ../Test_Scripts/stream_device.py             # test stream (outside this repo): 12-lead + all Home readings (demo CS12-DEMO-P0001 / SN12L-P0001)
bash scripts/run_local.sh mail --user ADDR && bash scripts/run_local.sh --lan   # real email (var/local.env, git-ignored) + reachable on the Wi-Fi
.venv/bin/pytest -q                     # 99 tests, SQLite; TEST_DATABASE_URL=postgresql+psycopg://... for PG
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
- `can_link` requires a confirmed mobile number or email (`accounts.is_activated`); doctors/clinics also a licence file + verification + TOTP. TOTP can't be disabled for doctor/clinic/admin; `require_admin` requires TOTP.
- Sign-up: mobile number required, email optional; codes by SMS (+ email) and the account is created only in `/auth/register/verify` (pending data waits in `otp_challenges.payload`). No email confirmation links. Sign-in: `/auth/login/start` (email → email code, number → SMS; plus a second code to the account's other **confirmed** contact, grouped by `group_id`, either works via `otp.check_any`, one sign-in consumes the group; the response never shows the other contact) + `/login/verify`, or `/login` (password, email or number), or `/auth/google` (ID token, `services/google.py`). Register / login-start / forgot-password never reveal whether an account exists (taken number/email → notice instead of a code; a "blocked" sign-up answers 409 `already_registered` only after every code it needs was right, and resend sends the same emails as the first send). Never log one-time codes (email subjects contain them: `email.py` masks digits). Codes: HMAC only, 10 min, 5 tries, single use (`services/otp.py`). Emailed tokens and refresh tokens are stored as SHA-256 only, single use. SMS is MSG91 only (`services/sms.py`): the server makes and checks the code, MSG91 only delivers it (`/api/v5/otp?otp=`); MSG91 reports failures as HTTP 200 `{"type":"error"}`, so check `type`. Production needs `SMS_PROVIDER=msg91` (`scripts/setup_msg91.sh`). **SMS down** (send failure → `sms.mark_down()` for 5 min, `available()` false, `/auth/options smsAvailable`): sign-up with an email goes on with the email code only (`payload.sms_skipped`, `phoneCodeSent: false`, account keeps the number with `phone_verified_at` NULL, confirmed later via `/me/contact/code`), without an email → 503 asking for one; sign-in by number → 503 `sms_unavailable` pointing to email code or password. An **unconfirmed number never blocks** the person who proves it by SMS (`accounts.phone_holder(confirmed_only=…)`, `release_unconfirmed_phone`). Unconfirmed phone is not in `missing` (that would block `can_link`); it is `phoneVerified` on `/me` and `/auth/status`.
- Web session: refresh token only in httpOnly SameSite=Strict cookie (`/v1/auth`) + `X-CS-Web: 1` header; access token in memory only.
- Audit events and logs never contain health values; logs use route templates, not URLs.
- Admin portal (`/admin`, `api/v1/admin.py`): counts and account details only, never health values; every action audited; emails masked except an explicit, audited CSV export. Verify needs ≥ 1 licence file (`verification_files`, owner + staff only, signed links); reject/revoke need a reason (stored in `review_note`, emailed). Transitions: verified ← any other; rejected ← pending; revoked ← verified; resubmit → pending.
- Deletion: admin delete = status `deleting` + `erase_after` (+30 d), sessions ended, data/links kept so Restore works; anything checking `status == "active"` already hides it. Erasure is one function, `services/erasure.erase_account` (self-delete, "erase now", daily `app/scripts/purge_deleted.py` timer). Admin accounts can't be deleted from the portal. New per-user tables must be added to `erase_account`.
- Profile pictures (`/v1/me/avatar`): own account only, JPEG/PNG by content, ≤ 512 KB; storage key on `users.avatar_key`, deleted on replace and in `erase_account`. Web shows it as a `data:` URL (CSP `img-src 'self' data:`, no `blob:`).
- Every web page ends with `components/SiteFooter.tsx` (cardiosens.ai links, absolute URLs, new tab); keep it in `Layout` and `AuthShell`. Header Account entry is the ☰ button.
- Doctors list (`api/v1/doctors.py`): patients only; a doctor appears only when `professional_profiles.listed` (their choice) AND verified, active, TOTP on; never phone/email. Listing fields don't reset verification; registration fields do.
- Documents: visible to the patient and doctor/clinic grants only (not family shares); type by content signature (PDF/JPEG only); only doctors comment; files via signed links. Live ECG: polling (no WebSocket), chunks idempotent on seq, `services/ecg.finalize` turns a session into a recording; high-frequency views log once per viewer (`ecg.live_view`, `dashboard.summary` hourly).
- Devices (`api/v1/devices.py`, `api/v1/device_stream.py`): `devices.patient_id` is the **owner** (patient, doctor or clinic login); `device_uid` unique among active devices. Device streams authenticate by device ID + serial (constant-time) + owner ID, get a short device token, and re-check device active / owner / link on every call. Doctor/clinic devices write only to patients with an active grant (`can_view_patients` + `active_grant_for`). Stream data is synthetic → always `acquisition: simulated`, accepted only where `settings.test_streams_allowed` (off in staging/production unless `DEVICE_TEST_STREAMS=true`); the normal API still rejects simulated readings.
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
`git pull && sudo bash scripts/deploy.sh` on the server. Production needs SMTP in `/etc/cardiosensai/backend.env` (`sudo bash scripts/set_smtp.sh --user no-reply@cardiosens.ai`: Google Workspace, app password, hidden prompt; never put passwords in repo files or chat) and SMS (`sudo bash scripts/setup_msg91.sh --template ID --test +91...`, auth key by hidden prompt; optional `GOOGLE_CLIENT_ID`); deploy dumps the DB before pending migrations. `nginx.sh` handles nginx + Let's Encrypt for `app.cardiosens.ai`; `sudo bash scripts/update_ssl.sh [--check|--force|--dry-run]` creates/checks/renews the certificate (certbot.timer renews automatically). Never run demo mode or demo data against production. Don't deploy, push or run migrations on the server without the user's go-ahead.

## Related
- Mobile app: `../cardioSE-WEB` (Expo; one app for patient/doctor/clinic, same code / password / Google sign-in + TOTP, refresh token in the JSON body). Older installs use phone OTP — keep `/v1/auth/otp/*` working. Web pages' CSP allows Google Identity Services only (`app/website.py`).
- Old marketing page archive: `../../Assets/V1.0/`.
- `credential.txt` (git-ignored) lists local demo accounts and IDs.
