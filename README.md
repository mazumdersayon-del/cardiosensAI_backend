# CardiosensAI — Backend API and Web App

FastAPI + PostgreSQL backend and React web app for the CardiosensAI connected health monitoring platform. One deployable serves:

- the **API** (`/v1`) used by the mobile app for patients, doctors and clinics ([cardioSE-WEB](https://github.com/mazumdersayon-del/cardioSE-WEB)) and the web app;
- the **web app** (`/`): one sign-in page for patients, doctors and clinics, then a separate dashboard per role, plus a staff console.

Patients, doctors and clinics are identified and linked by a **16-digit CardiosensAI ID**.

> **Status: 0.2, pre-production. Not for clinical use.** No clinical validation, legal compliance or medical-device certification is claimed. See [Before production](#before-production).

**Contents:** [What it does](#what-it-does) · [Scripts](#scripts) · [Running locally](#running-locally) · [Architecture](#architecture) · [Data model](#data-model) · [API overview](#api-overview) · [Configuration](#configuration) · [Connecting the mobile app](#connecting-the-mobile-app) · [Deploying](#deploying-to-a-server) · [Dependencies](#dependencies) · [Before production](#before-production) · [Repository layout](#repository-layout)

## What it does

| Area | Summary |
|---|---|
| **Sign-up and sign-in** | Sign-up needs a **mobile number** (email optional): a 6-digit code goes to the phone, and to the email if given; entering them creates the account, activated and signed in (no confirmation links). **If text messages are down** (MSG91 failing or not set up), sign-up goes on with the email code only and the mobile number is confirmed later in Settings; without an email it asks for one. Sign-in: type an email or mobile number → a code by email or SMS, plus one to the account's other confirmed email or number (either works), on a separate page (a number while SMS is down → "use your email or your password" with a **Sign in with password** button), with **"use password instead"** (argon2id, 10+ characters, common-password check, 5 failures lock for 15 min). **Continue with Google** on both pages (Google Identity Services; sign-up with Google still confirms the mobile number). TOTP second factor, **required for doctors, clinics and staff**. Password reset by emailed link. 15-minute access tokens, rotating refresh tokens with reuse detection; the web app keeps its refresh token in an httpOnly SameSite=Strict cookie |
| **Accounts** | Patient, doctor, clinic (self sign-up, role chosen on the form) and admin (CardiosensAI staff). Every account has a **16-digit ID**: role digit (1 patient, 2 doctor, 3 clinic, 9 staff) + 14 random digits + Luhn check digit. A clinic is an organisation with its own ID and, in v1, one login |
| **Links** | Patient↔doctor, patient↔clinic and doctor↔clinic, made by typing the other's 16-digit ID. Both sides approve (the requester's side on request), so the patient approves every patient link. An active patient link gives full, revocable access through an access grant; ending it stops access at once. A clinic's link doesn't extend to its doctors. A clinic can ask a patient to link with one of its doctors. Doctors and clinics must be verified by staff **and** use two-step sign-in before they can link or see anything |
| **Web app** | React SPA at `/`: the sign-in page, or the signed-in role's dashboard. Doctor/clinic home leads with **latest recordings** (newest readings, last ECG, live badge) across linked patients; a patient's home has **latest readings** (+ care team) on top, then **Live ECG**, **Reports and documents** (upload button, PDF report request) and a vertical **History** of every uploaded document and PDF report (newest first, a document opens to its files and comments), then ECG recordings and readings history. The patient's ID card (QR, copy) is on the Account page. Profile pictures (header + Account). Light and dark themes |
| **Devices** | Device models declare exactly which vitals they may provide. Bluetooth devices are registered by the mobile app. **CardiosensAI 12-lead devices** (ESP32, Wi-Fi) talk to the server over **MQTT with TLS** ([docs/device-protocol.md](docs/device-protocol.md)): staff import them from the factory CSV (Admin → Devices); a patient, doctor or clinic **pairs** one under Account → Devices (device ID + serial, then a button press on the device); it then saves a **20-second 12-lead snapshot** on the interval chosen in **Device configuration** (presets, quiet hours) into the patient's ECG list (the **ECG recordings** card, by date and time, beside **Live ECG**), and on **Live** records 20 s at once, streamed into the Live ECG card while it records and then stored like the others. Every message is signed with a per-device key, checked for freshness, replay, pairing and exact signal format. Doctors label snapshots with PTB-XL SCP codes; patients choose whether snapshots may be used for research |
| **Readings** | `POST /v1/measurements:batch` is idempotent and returns an outcome per item. Plausibility, unit, timestamp and device-capability checks run on every item. Simulated readings and glucose control-solution tests are refused. Readings dated before the device was registered are refused |
| **History** | Keyset-paginated queries that keep each reading's original UTC offset, plus latest-per-vital |
| **ECG** | Single-lead and **12-lead** recordings (interleaved int16 frames) shown in the standard 3×4 layout with a lead II rhythm strip. **Live ECG**: the device streams ~1 s chunks; doctors see a live status and live 12-lead trace, and the session becomes a normal recording when it stops. Samples are in object storage with a SHA-256 check, served only after a permission check |
| **Documents** | Patients and linked doctors/clinics upload lab reports, prescriptions and scans: several **PDF or JPEG** files per document (checked by content). Linked **doctors comment**; everyone with access sees the thread. Files open through 10-minute signed links |
| **Sharing** | Patient-created grants: chosen vitals, history window (`from_now`, `last_30_days`, `all`), downloads on/off, notices, expiry. Single-use hashed invite tokens. Revocation is immediate, including report links already issued |
| **Reports** | PDF built in the background (reportlab) and downloadable only through 10-minute signed links bound to one user. Permission is re-checked on every download |
| **Audit** | Append-only trail of logins, sharing changes, viewer reads, downloads and admin actions. Values are never logged. Patients see an access log |
| **Admin portal** (`/admin`) | Statistics (accounts, sign-ups, activity, volume; counts only, CSV), doctor and clinic verification against uploaded licence documents (verify, reject with reason, revoke; emailed), account search with filters and CSV export, deletion with a 30-day grace period (restore or erase now), moving app accounts to email sign-in. API also: device models, ops, audit search. Requires two-step sign-in. Admins get **no** health data |
| **Privacy** | Data export (JSON) and account deletion (health data removed; audit and consent records kept) |

## Scripts

Every operational task has a script. This section is the reference for all of them; the walkthroughs in [Running locally](#running-locally) and [Deploying to a server](#deploying-to-a-server) show them in context.

| Script | Runs on | Needs | Purpose |
|---|---|---|---|
| [`scripts/run_local.sh`](#scriptsrun_localsh) | Your machine | Docker, Node 20+ | Whole stack locally: PostgreSQL + API + web app + demo data; demo TOTP codes; live ECG simulator |
| [`scripts/deploy.sh`](#scriptsdeploysh) | Ubuntu 26.04 server | root | Install or update everything on the server: PostgreSQL, Python, service, nginx, HTTPS, backups, firewall, migrations |
| [`scripts/nginx.sh`](#scriptsnginxsh) | Server | root | nginx site + Let's Encrypt certificate (run by `deploy.sh`, or alone) |
| [`scripts/update_ssl.sh`](#scriptsupdate_sslsh) | Server | root | Create, check or renew the HTTPS certificate |
| [`scripts/set_smtp.sh`](#scriptsset_smtpsh) | Server | root | Set up outgoing email (SMTP) with a hidden password prompt, restart, send a test |
| [`scripts/setup_msg91.sh`](#scriptssetup_msg91sh) | Server | root | Set up MSG91 for mobile-number one-time codes (hidden auth-key prompt), restart, send a test code. **Needed for sign-up** |
| [`app/scripts/seed.py`](#appscriptsseedpy) | Either | database settings | Device models, staff accounts, demo data |
| [`app/scripts/simulate_ecg.py`](#appscriptssimulate_ecgpy) | Either | a running API | Stream a synthetic 12-lead live ECG through the public API |
| [`app/scripts/test_email.py`](#appscriptstest_emailpy) | Either | mail settings | Send a test email to check SMTP settings |
| [Web app (`npm run …`)](#web-app-npm-scripts) | Your machine | Node 20+ | Dev server, tests, typecheck, production build |
| [Database and checks](#database-and-checks) | Either | `.venv` | Migrations, tests, lint, API spec |

**Conventions for the shell scripts:**

- Run them with `bash scripts/<name>.sh` from the repository root. They find the repository themselves, so `bash /path/to/cardiosensAI_backend/scripts/<name>.sh` works from any directory.
- `--help` prints the built-in help (the same text as the top of the file).
- Server scripts must run as root (`sudo bash …`); they refuse otherwise.
- Most options can also be given as environment variables (shown in brackets below), e.g. `DOMAIN=app.cardiosens.ai sudo -E bash scripts/nginx.sh`.
- The server scripts share remembered settings in `/etc/cardiosensai/deploy.env` (domain, Let's Encrypt email, mode, workers), so after the first run they need no options.
- They stop at the first error and print the failing line (`deploy.sh: failed at line N: …`).

### `scripts/run_local.sh`

Starts the whole stack on your machine for development: PostgreSQL 17 in Docker, migrations, demo data, the API with auto-reload and the web app on **http://localhost:8000**, plus the device broker (Mosquitto in Docker, `cardiosens-local-mqtt` on localhost:1883, logins in `var/mosquitto`) and the device bridge. Development settings only (dev secrets, SMS codes in API responses, emails printed to the terminal).

```bash
bash scripts/run_local.sh [options]        # start (Ctrl+C stops the API; the database keeps running)
bash scripts/run_local.sh totp             # current 6-digit code for the demo doctor and clinic
bash scripts/run_local.sh ecg-live [S]     # stream a synthetic 12-lead live ECG for S seconds
bash scripts/run_local.sh device [OPTIONS] # 12-lead device simulator against the local broker (see below)
bash scripts/run_local.sh stop             # stop the database and broker containers (data kept)
bash scripts/run_local.sh mail --user ADDR # real email from this PC (SMTP password asked hidden, test email sent)
bash scripts/run_local.sh sms --template ID [--test +91...]   # real SMS through MSG91 (auth key asked hidden)
```

| Option / command | Meaning |
|---|---|
| *(none)* | Start everything on http://localhost:8000 |
| `--dev` | Also run Vite on http://localhost:5173 with hot reload (forwards `/v1` to the API). Open that address while editing `web/`; emailed links then point to :5173 |
| `--reset` | Delete the local database volume and stored files first, then migrate and seed from scratch. Your own local accounts are deleted too, and the demo IDs change |
| `--admin-email ADDRESS` | Also create a staff (admin) account; its set-password link is printed in the terminal |
| `--no-demo` | Don't create the demo patient, doctor and clinic |
| `--port N` | API port (default 8000) |
| `--lan` | Also listen on this PC's network address (e.g. http://192.168.0.15:8000), so phones and other PCs on the same Wi-Fi can use it; emailed links use that address. The Android app: `CS_API_BASE_URL=http://<IP>:8000 npx expo start` |
| `--show-codes` | Keep one-time codes on the pages even when real email is set up |
| `mail --user ADDR [--test TO] [--host H --port N --tls starttls\|ssl\|none --from ADDR]` | Save SMTP settings in `var/local.env` (git-ignored, mode 600; default host `smtp.gmail.com`, Google needs an app password) and send a test email. The next start sends real emails, hides codes from the pages and, without MSG91, treats SMS as down (like production today: sign-up goes on with the email code, SMS sign-in points to email or password) |
| `sms --template ID [--test NUMBER]` | Save MSG91 settings in `var/local.env` and optionally send a test code (needs DLT approval) |
| `mail-test ADDR`, `sms-test NUMBER` | Send a test with the saved settings |
| `local-show`, `local-off` | Show the saved settings (secrets masked) / delete `var/local.env` (back to codes on screen) |
| `totp` | Print the demo doctor/clinic authenticator code (key `CARDIOSENSDEMOTOTPSECRETCODE2345`) |
| `ecg-live [S]` | Run [`simulate_ecg.py`](#appscriptssimulate_ecgpy) for the demo patient for S seconds (default 120, `0` = until Ctrl+C) against `http://localhost:$PORT` (`PORT` defaults to 8000) |
| `stop` | Stop the `cardiosens-local-pg` container; data stays in the `cardiosens-local-pgdata` volume |
| `-h`, `--help` | Show the built-in help |
| env `PG_PORT` | Host port for PostgreSQL (default 55433) |

Examples:

```bash
bash scripts/run_local.sh --dev                                  # work on the web app with hot reload
bash scripts/run_local.sh --reset --admin-email you@example.com  # clean start with a staff account
bash scripts/run_local.sh --port 8010                            # second instance next to one on :8000
PORT=8010 bash scripts/run_local.sh ecg-live 60                  # live ECG to the :8010 instance
PG_PORT=55500 bash scripts/run_local.sh                          # if 55433 is taken
bash scripts/run_local.sh mail --user no-reply@cardiosens.ai --test you@gmail.com   # once: real email
bash scripts/run_local.sh --lan                                  # test every feature from this PC and phones on the Wi-Fi
```

Demo accounts, what each run does, local files and troubleshooting: [Running locally](#running-locally).

### `scripts/deploy.sh`

Installs or updates everything on **one Ubuntu 26.04 server**. Safe to re-run: every run copies the current code, installs dependencies, applies migrations and restarts the service, keeping settings, secrets and data.

```bash
sudo bash scripts/deploy.sh --domain app.cardiosens.ai --email ops@cardiosens.ai --admin-email ops@cardiosens.ai   # first run
git pull && sudo bash scripts/deploy.sh                                                                         # update
```

| Option | Meaning |
|---|---|
| `--domain NAME` [`DOMAIN`] | Public host name, e.g. `app.cardiosens.ai`. Empty: plain HTTP on the server's IP |
| `--email ADDRESS` [`LETSENCRYPT_EMAIL`] | Let's Encrypt account email (expiry notices). Using it accepts the Let's Encrypt terms |
| `--mode production\|demo` [`MODE`] | `production` (default): real settings; **refuses to run without SMTP settings** in `backend.env`. `demo`: SMS codes returned by the API, emails written to the service log. **Anyone can sign in as any phone number**: test data only |
| `--admin-email ADDRESS` [`ADMIN_EMAIL`] | Create or promote a staff account; a set-password link is emailed and printed |
| `--admin PHONE` [`ADMIN_PHONE`] | Create or promote a staff account by phone (app sign-in) |
| `--demo-data` | Add the demo patient, doctor, clinic and family member (`--mode demo` only) |
| `--no-email` [`NO_EMAIL=1`] | Production **without** an SMTP relay yet (`EMAIL_PROVIDER=none`). The site runs, but email codes, password-reset and set-password emails are not sent, so only staff created with `--admin-email` (link printed in the output) can sign in. Remembered; the first run after `SMTP_HOST` appears in `backend.env` switches to SMTP by itself |
| `--test-email ADDRESS` [`TEST_EMAIL`] | After deploying, send a test email through the configured relay and report success or the SMTP error |
| `--workers N` [`WORKERS`] | uvicorn worker processes (default 1). Request rate limits are kept per process |
| `--no-tls` | Don't request a certificate even with a domain |
| `--no-firewall` | Leave ufw alone |
| `-h`, `--help` | Show the built-in help |

What it does, in order:

1. Checks options. In production it needs `SMTP_HOST` in `/etc/cardiosensai/backend.env` (or `--no-email`); otherwise it stops **before changing anything** and prints the lines to add.
2. Installs system packages (PostgreSQL, rsync, curl…) and creates the `cardiosens` service account and folders.
3. Copies the code to `/opt/cardiosensai/app` (rsync, root-owned; `.git`, `.venv`, `var`, `tests`, `web/node_modules` excluded).
4. Installs Python 3.13 with uv and the locked `requirements.txt` into `/opt/cardiosensai/venv`.
5. Creates the database and role (password generated once), writes `backend.env` (secrets generated once, `API_BASE_URL`/`WEB_BASE_URL`, mode-specific SMS/email settings; MSG91 set up with [`setup_msg91.sh`](#scriptssetup_msg91sh) is kept).
6. **If a migration is pending:** saves `pg_dump` to `/var/backups/cardiosensai/pre-migrate-<time>.dump`, then runs `alembic upgrade head`; then the seed (device models, `--admin*`, `--demo-data`).
7. Installs and restarts the sandboxed systemd service `cardiosensai-api` (uvicorn on 127.0.0.1:8000, access log off).
8. Runs [`nginx.sh`](#scriptsnginxsh) (site + certificate).
9. Schedules the daily backup (02:30, 14 days kept) and configures ufw (SSH, 80, 443).
10. Checks `/readyz`, that nginx passes requests and the web app loads, and prints the URLs.

| Setting it writes | Where |
|---|---|
| Settings and secrets | `/etc/cardiosensai/backend.env` (`root:cardiosens`, 0640) |
| Remembered options | `/etc/cardiosensai/deploy.env` |
| Code, virtualenv, Python | `/opt/cardiosensai/app`, `/opt/cardiosensai/venv`, `/opt/cardiosensai/python` |
| ECG waveforms, PDFs, documents | `/var/lib/cardiosensai/storage` |
| Backups | `/var/backups/cardiosensai` (`db-*.dump`, `storage-*.tar.gz`, `pre-migrate-*.dump`) |
| Service, backup timer | `cardiosensai-api.service`, `cardiosensai-backup.timer` |

The full first-deployment checklist, upgrade notes and rollback are in [Deploying to a server](#deploying-to-a-server).

### `scripts/nginx.sh`

Installs nginx and certbot, writes the site config and obtains the certificate. `deploy.sh` runs it on every deploy; run it alone to add HTTPS once DNS is ready, to change the domain, or to rewrite the config.

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

- Writes `/etc/nginx/sites-available/cardiosensai` (enabled; the default site removed). Everything is proxied to the API, which serves the web app at `/` and the API at `/v1`: request bodies up to **55 MB** (document uploads), gzip for the web app's files, `X-Forwarded-*` and `X-Request-ID` headers, 60 s proxy timeout. The file is overwritten on every run: don't edit it by hand.
- With a domain and no certificate yet: serves the ACME challenge over HTTP, runs `certbot certonly --webroot`, then switches to HTTPS (TLS 1.2/1.3, HTTP/2) with HTTP redirecting to HTTPS. If the request fails (DNS not pointing here, port 80 closed), it stays on HTTP and warns; fix and re-run (or use `update_ssl.sh`).
- Installs a certbot deploy hook that reloads nginx after every renewal.
- Sets `API_BASE_URL` and `WEB_BASE_URL` in `backend.env` to `https://DOMAIN` (report links and links in emails) and restarts the API if they changed.
- Opens ports 80 and 443 if ufw is active, and remembers the domain and email in `/etc/cardiosensai/deploy.env`.

### `scripts/update_ssl.sh`

Let's Encrypt certificates last 90 days. certbot's timer renews them automatically (it checks twice a day and renews within 30 days of expiry) and the hook reloads nginx, so normally nothing needs doing. This script is the one command to create the certificate, check it, or renew it by hand.

```bash
sudo bash scripts/update_ssl.sh --domain app.cardiosens.ai --email ops@cardiosens.ai   # first certificate (or a new host name)
sudo bash scripts/update_ssl.sh            # remembered domain: create if missing, renew if 30 days or fewer left
sudo bash scripts/update_ssl.sh --check    # report only: issuer, expiry, days left, DNS, what nginx serves, auto-renew state
sudo bash scripts/update_ssl.sh --force    # renew now, whatever the expiry date
sudo bash scripts/update_ssl.sh --dry-run  # test renewal against Let's Encrypt staging; changes nothing
```

| Option | Meaning |
|---|---|
| `--domain NAME` [`DOMAIN`] | Host name. Defaults to the one remembered in `/etc/cardiosensai/deploy.env` |
| `--email ADDRESS` [`LETSENCRYPT_EMAIL`] | Let's Encrypt account email; required for the first certificate |
| `--days N` [`RENEW_DAYS`] | Renew when N or fewer days are left (default 30) |
| `--force` | Renew even if still valid (e.g. after a key compromise) |
| `--check` | Report only. Exit code `0` = valid for more than `--days`, `1` = missing or expiring: usable from cron or monitoring |
| `--dry-run` | Test the renewal path without touching the live certificate |
| `--port N` [`API_PORT`] | API port passed on to `nginx.sh` (default 8000) |
| `-h`, `--help` | Show the built-in help |

What it does:

1. Prints the certificate (names, issuer, expiry, days left) and checks that the domain's DNS points at this server (a warning if not; Let's Encrypt would fail).
2. **No certificate, or a new domain:** runs `nginx.sh`, which gets the certificate over HTTP (webroot challenge on port 80), switches nginx to HTTPS with HTTP→HTTPS redirect, and sets `API_BASE_URL` / `WEB_BASE_URL` to `https://DOMAIN`.
3. **Certificate present:** if nginx isn't using it yet (an earlier request failed), rewrites the site with `nginx.sh`; then renews with `certbot renew --cert-name DOMAIN` when it is due (or `--force`).
4. Tests and reloads nginx, switches certbot's renewal timer on, and confirms that nginx on `127.0.0.1:443` presents the current certificate and that `https://DOMAIN/healthz` answers.

Exit codes: `0` ok, `1` certificate missing/expiring (`--check`) or renewal failed, `2` usage or setup error. Typical failures: DNS not pointing here, port 80 closed by a cloud firewall (Vultr firewall group), or Let's Encrypt rate limits after many attempts (use `--dry-run` while debugging).

A weekly check from cron (mails root if the certificate is missing or expiring):

```bash
echo '0 9 * * 1 root bash /opt/cardiosensai/app/scripts/update_ssl.sh --check >/dev/null || echo "CardiosensAI certificate needs attention"' | sudo tee /etc/cron.d/cardiosensai-ssl
```

### `scripts/set_smtp.sh`

Sets up outgoing email on the server: writes `EMAIL_PROVIDER`, `EMAIL_FROM` and `SMTP_*` into `/etc/cardiosensai/backend.env` (all other settings are kept), restarts `cardiosensai-api` if it is running, and sends a test email. The password is asked for with **hidden input**, so it never lands in shell history, on screen or in chat. `cardiosens.ai` mail is on **Google Workspace**, so the defaults are Google's.

```bash
sudo bash scripts/set_smtp.sh --user no-reply@cardiosens.ai                   # Google: paste the 16-letter app password when asked
sudo bash scripts/set_smtp.sh --relay --from no-reply@cardiosens.ai           # Workspace SMTP relay: no password, server IP allow-listed
sudo bash scripts/set_smtp.sh --user me@example.com --host smtppro.zoho.in    # another provider
sudo bash scripts/set_smtp.sh --test you@example.com                          # only send a test with the current settings
```

| Option | Meaning |
|---|---|
| `--user ADDRESS` | SMTP login, usually the sending mailbox |
| `--from ADDRESS` | Sender (default: `--user`); `Name <address>` or an address |
| `--name NAME` | Sender display name (default `CardiosensAI`) |
| `--host NAME` | SMTP server (default `smtp.gmail.com`; `smtp-relay.gmail.com` with `--relay`) |
| `--port N`, `--tls starttls\|ssl\|none` | Default 587 + `starttls` (465 with `ssl`) |
| `--relay` | Google Workspace SMTP relay: no login; the server's public IP must be allowed in admin.google.com → Apps → Gmail → Routing → SMTP relay service |
| `--test ADDRESS` | Test recipient (default: the sender). Alone, it only sends a test with the current settings |
| `--no-test` | Don't send a test email |
| env `SMTP_PASSWORD` | Give the password non-interactively (automation) |

**Google needs an app password**, not the account password: turn on 2-Step Verification for the sending account, then create one at https://myaccount.google.com/apppasswords (16 letters; the spaces Google shows are removed). The script warns if the password doesn't look like one. Google's limits: about 2,000 messages a day per Workspace user, about 10,000 through the relay. For good delivery, set up DKIM in admin.google.com → Apps → Gmail → Authenticate email (SPF for `cardiosens.ai` is already published).

If the test fails, the reason from Google is printed: `535 … Username and Password not accepted` = not an app password / 2-Step Verification off; a timeout = port 587 blocked (try `--tls ssl`, port 465); `550 … Invalid credentials for relay` = server IP not allowed for the relay yet. After setting up email, run `sudo bash scripts/deploy.sh` again if a deploy stopped at the SMTP check or ran with `--no-email`.

### `scripts/setup_msg91.sh`

Sets up **MSG91** for mobile-number one-time codes on the server, then sends a test code. **Production needs it: sign-up confirms the mobile number by SMS.** Until MSG91 is set up (or while it is down) sign-up only works with an email address and the number stays unconfirmed until the person confirms it later; sign-in by email code, password or Google still works. The auth key is asked for with hidden input (or taken from `MSG91_AUTH_KEY` in the environment) and is never printed.

```bash
sudo bash scripts/setup_msg91.sh --template <OTP_TEMPLATE_ID> --test +919812345678     # first setup (asks for the auth key)
sudo bash scripts/setup_msg91.sh --notice-template <TEMPLATE_ID>                       # add or change a template, keep the saved key
sudo bash scripts/setup_msg91.sh --new-key                                             # rotate the auth key
sudo bash scripts/setup_msg91.sh --test +919812345678                                  # test the current settings
sudo bash scripts/setup_msg91.sh --show                                                # current settings, key masked
sudo bash scripts/setup_msg91.sh --off                                                 # SMS_PROVIDER=none
```

| Option | What it does |
|---|---|
| `--template ID` | The MSG91 OTP template id (24 hex characters), DLT-approved; its text must contain `##OTP##`, e.g. "##OTP## is your CardiosensAI code. It expires in 10 minutes. Don't share it with anyone." Required the first time |
| `--notice-template ID` | Optional template without variables for "this number already has an account" (sent instead of a code when someone signs up with a registered number; skipped without it). `""` removes it |
| `--reminder-template ID` | Optional appointment reminder (Flow template with `##doctor##` and `##time##`, sent 1 h before accepted appointments; skipped without it). `""` removes it |
| `--new-key` | Ask for a new auth key even if one is saved (otherwise the saved key is kept) |
| `--test NUMBER` | Send a test code (`app/scripts/test_sms.py`, as the service account) |
| `--show` | Print `SMS_PROVIDER` and the `MSG91_*` settings with the key masked |
| `--off` | Switch SMS off (`SMS_PROVIDER=none`): sign-up continues with the email code only (no email → 503), SMS sign-in answers 503 pointing to email or password |

**Before running it (India):** SMS needs a registered DLT entity, a sender ID (header) and a DLT-approved template; approval takes a few days. In MSG91: link the DLT entity and sender ID, create the OTP template (MSG91 → OTP → Templates) and copy its id, then create an auth key (MSG91 → API → Auth Key; whitelist the server's IP if you turn on IP security). A failed test prints MSG91's own reason (wrong key, unknown template, DLT mismatch, IP not whitelisted, no balance).

**How codes flow:** sign-up sends a code to the mobile number and the email (both needed); sign-in sends one to what was typed and one to the account's other confirmed contact (either works). For every SMS code the server makes the 6-digit code, stores only its HMAC (`otp_challenges`) and sends it through MSG91's OTP API (`POST /api/v5/otp?template_id=…&mobile=…&otp=…&otp_length=6&otp_expiry=10`); MSG91 only delivers it. Checking the code (expiry, 5 tries, single use) stays on the server, the same as for email codes, so SMS and email codes behave identically. MSG91 reports most failures as HTTP 200 with `{"type": "error"}`, which the server treats as not sent (503 for codes, logged and skipped for notices).

Writes `SMS_PROVIDER=msg91` and `MSG91_AUTH_KEY`, `MSG91_OTP_TEMPLATE_ID`, `MSG91_NOTICE_TEMPLATE_ID`, `MSG91_REMINDER_TEMPLATE_ID` (`--reminder-template ID`: appointment reminder, Flow template with `##doctor##` and `##time##`) in `backend.env` (other settings kept; leftover `TWILIO_*` lines from older versions are removed), and restarts the API. MSG91 is the only SMS provider; Twilio support was removed (a server still on `SMS_PROVIDER=twilio` is switched to `none` by the next `deploy.sh` until this script is run).

### Google sign-in

"Continue with Google" appears on the sign-in and sign-up pages once the server has a client id:

1. Google Cloud Console → APIs & Services → Credentials → **Create OAuth client ID** → *Web application*. Authorised JavaScript origins: `https://app.cardiosens.ai` (and `http://localhost:8000` for local testing). No redirect URI is needed (popup mode). Set up the OAuth consent screen (app name, support email, `cardiosens.ai` domain); only the basic `openid email profile` scopes are used.
2. On the server: add `GOOGLE_CLIENT_ID=<id>.apps.googleusercontent.com` to `/etc/cardiosensai/backend.env` and `sudo systemctl restart cardiosensai-api`.
3. Mobile app: create *Android* (package `com.cardiosensai.patient` + the signing key's SHA-1) and *iOS* client ids, build the app with `CS_GOOGLE_ANDROID_CLIENT_ID` / `CS_GOOGLE_IOS_CLIENT_ID` (and `CS_GOOGLE_WEB_CLIENT_ID`), and list them on the server: `GOOGLE_EXTRA_CLIENT_IDS=["<android id>","<ios id>"]`.

The server checks every Google ID token's signature (Google's published keys, cached), audience, issuer, expiry and that the email is verified. A Google sign-in finds the account by its Google id, or by the same verified email (then links it). Sign-up with Google takes the email from Google (no email code, no password needed) but still confirms the mobile number by SMS.

### `app/scripts/seed.py`

Adds the device models (always), staff accounts and demo data. Idempotent: running it again changes nothing that already exists (an existing demo database only gets the demo 12-lead ECG added if it is missing).

```bash
python -m app.scripts.seed [--demo] [--admin-email ADDRESS] [--admin PHONE]
```

| Option | Meaning |
|---|---|
| *(none)* | Create or update the device models only |
| `--demo` | Demo patient, doctor, clinic and family member, readings, two ECGs (single-lead and 12-lead) and their links, plus the appointments test doctor **Dr. Arjun Test** (`testdoctor@demo.cardiosens.ai`, same password and authenticator secret: verified, listed, weekly hours Mon–Sat 10–13 and 17–20 IST, a scheduled offline afternoon, linked with the demo patient, sample appointments in every state). **Refused when `ENV=production`** |
| `--admin-email ADDRESS` | Create or promote a staff account by email; prints the set-password link (valid 72 h) and emails it |
| `--admin PHONE` | Create or promote a staff account by phone number (app sign-in) |

It uses the same settings as the API (`DATABASE_URL`, `STORAGE_LOCAL_PATH`, `EMAIL_PROVIDER`…). `run_local.sh` and `deploy.sh` run it for you. To run it by hand:

```bash
# locally, against the run_local.sh database
ENV=development DATABASE_URL=postgresql+psycopg://cardiosens:cardiosens@127.0.0.1:55433/cardiosens \
  STORAGE_LOCAL_PATH=$PWD/var/storage .venv/bin/python -m app.scripts.seed --admin-email you@example.com

# on the server: easiest is to let deploy.sh do it (it re-runs the whole, idempotent deploy)
sudo bash scripts/deploy.sh --admin-email ops@cardiosens.ai

# or run only the seed, as the service account with exactly the service's settings (how deploy.sh runs it)
sudo systemd-run --quiet --wait --pipe --collect --service-type=exec -p User=cardiosens -p Group=cardiosens \
  -p WorkingDirectory=/opt/cardiosensai/app -p EnvironmentFile=/etc/cardiosensai/backend.env \
  /opt/cardiosensai/venv/bin/python -m app.scripts.seed --admin-email ops@cardiosens.ai
```

### `app/scripts/simulate_ecg.py`

Streams a **synthetic** 12-lead ECG as a live recording, exactly as a device would: it signs in as a patient through the public API (email + password), picks their ECG-capable device, starts a live session, sends one chunk per second and stops, after which the session is saved as a recording. Standard library only. For demos and for testing the live view; never on production patients.

```bash
python -m app.scripts.simulate_ecg [--base-url URL] [--email E] [--password P] [--seconds S] [--hr BPM] [--fs HZ]
```

| Option | Meaning |
|---|---|
| `--base-url URL` | API to stream to (default `http://localhost:8000`) |
| `--email`, `--password` | Patient to sign in as (default the demo patient and demo password). The account must not use two-step sign-in and needs an ECG-capable device |
| `--seconds S` | Length (default 60; `0` = until Ctrl+C or `kill`, both stop cleanly and save the recording) |
| `--hr BPM` | Heart rate of the synthetic signal (default 72) |
| `--fs HZ` | Sample rate (default 500) |

`bash scripts/run_local.sh ecg-live [S]` runs it with the local defaults. Sign in as the demo doctor and open the demo patient to watch.

### `../Test_Scripts/stream_device.py` (12-lead device simulator; outside this repo)

Behaves like the device firmware should ([docs/device-protocol.md](docs/device-protocol.md)): logs in to the MQTT broker with the password derived from its factory secret, says hello, waits to be paired (press Enter = the device's button, or `--auto-accept`), then takes 20-second 12-lead snapshots on the schedule from Device configuration (quiet hours respected, up to 48 kept while offline and uploaded oldest first, dropped only after the server's ack) and streams a live ECG while someone asks for it. It lives in `Repositories/Test_Scripts/`, next to this repo and not in git. Needs `paho-mqtt` and this repo's `app/services/ecg_synth.py` (found in `../cardiosensAI_backend`, or set `CARDIOSENS_BACKEND`).

```bash
bash scripts/run_local.sh device --device CS12-TEST-0001 --secret <64 hex> [--every 30s] [--duration 10m]
python3 ../Test_Scripts/stream_device.py --broker platform.cardiosens.ai --port 8883 --tls --device ... --secret ...
python3 ../Test_Scripts/stream_device.py --device CS12-TEST-0001 --secret ... --reset   # long-press reset (unpair)
```

Then pair it in the web app: Account → Devices → Add a CardiosensAI device (device ID + serial). Options: `--broker`, `--port`, `--tls`, `--device`, `--secret`, `--duration`, `--every` (test only: ignore the interval), `--hr`, `--auto-accept`, `--reset`, `--prod` (`platform.cardiosens.ai:8883`, TLS), `--new-unit ID` (prints a test unit's factory CSV row with a new secret, for Admin → Devices). The data is synthetic, so only **test units** (imported with `test=1`) may send it; it is stored labelled **simulated**, and refused unless test streams are on (always in development/test, in staging/production only with `DEVICE_TEST_STREAMS=true`). The demo data imports two test units: `CS12-TEST-0001` / `SN12L-T0001` and `CS12-TEST-0002` / `SN12L-T0002` (secrets in `app/scripts/seed.py`, `DEMO_TEST_UNITS`). Never against production patients.

### `app/scripts/mqtt_bridge.py` (device bridge)

Carries messages between the broker and the database: subscribes to every device's uplink, hands each message to `services/device_hub.handle_uplink`, publishes what the API queued in `device_outbox` (retained state, acks), expires pairings and live requests every 5 s and rewrites the broker's password file on start and every 5 minutes. One instance: systemd `cardiosensai-mqtt` on the server, started by `run_local.sh` locally.

### `app/scripts/appointment_reminders.py`

Reminds both sides of accepted appointments that start within the hour (once each: in the app, by email, and by SMS when `MSG91_REMINDER_TEMPLATE_ID` is set) and expires requests nobody answered. `deploy.sh` runs it every 5 minutes (`cardiosensai-reminders.timer`); safe to run by hand: `.venv/bin/python -m app.scripts.appointment_reminders`.

### `app/scripts/purge_deleted.py`

Erases accounts whose 30-day deletion grace period is over (accounts an admin deleted in the portal). `deploy.sh` installs a systemd timer that runs it daily at 03:15 (`cardiosensai-purge.timer`); running it by hand is safe.

```bash
.venv/bin/python -m app.scripts.purge_deleted --dry-run      # locally: list what is due
sudo systemctl start cardiosensai-purge.service              # on the server: run it now (as the service account)
journalctl -u cardiosensai-purge -n 20                       # what the last runs erased
```

### `app/scripts/test_sms.py`

Sends one test sign-in code by SMS through MSG91 (exit code 0 = MSG91 accepted it). `setup_msg91.sh --test` runs it on the server; locally: `.venv/bin/python -m app.scripts.test_sms +919812345678` (console provider: logged).

### `app/scripts/test_email.py`

Sends one test email with the configured provider (`EMAIL_PROVIDER`, `SMTP_*`, `EMAIL_FROM`) to check the mail settings. With SMTP it first logs in to the server directly, so a failure shows the real reason (wrong password, blocked port, TLS mode). Exit code `0` = sent.

```bash
sudo bash scripts/deploy.sh --test-email you@example.com           # on the server (runs it as the service account after deploying)
.venv/bin/python -m app.scripts.test_email you@example.com          # locally (console provider: the message is printed)
```

### Web app (`npm` scripts)

Run inside `web/` after `npm ci`:

| Command | What it does |
|---|---|
| `npm run dev` | Vite dev server on http://localhost:5173 with hot reload; proxies `/v1` to `http://localhost:8000` (start the API first, e.g. with `run_local.sh`) |
| `npm test` | Unit tests (Vitest): 16-digit ID check, phone numbers, ECG drawing |
| `npm run typecheck` | TypeScript check (`tsc -b`) |
| `npm run build` | Typecheck, then production build into `../website/` (commit it: the server serves the committed build) |

### Database and checks

From the repository root, with the `.venv` that `run_local.sh` creates:

| Command | What it does |
|---|---|
| `.venv/bin/alembic upgrade head` | Apply migrations (needs `DATABASE_URL`) |
| `.venv/bin/alembic revision --autogenerate -m "…"` | New migration from model changes (review it before committing) |
| `.venv/bin/alembic check` | Models and migrations agree (no output = OK) |
| `.venv/bin/alembic downgrade -1` | Undo the last migration (0002's downgrade refuses if email-only accounts exist) |
| `.venv/bin/pytest -q` | Backend tests on a temporary SQLite; set `TEST_DATABASE_URL=postgresql+psycopg://…` for PostgreSQL |
| `.venv/bin/ruff check . && .venv/bin/ruff format --check .` | Lint and formatting |
| `.venv/bin/python -c "import json; from app.main import app; json.dump(app.openapi(), open('docs/openapi.json','w'), indent=2)"` | Regenerate `docs/openapi.json` |
| `uv pip compile pyproject.toml --universal --python-version 3.13 --no-header --annotation-style line -o requirements.txt` | Regenerate the dependency lock (keep its 3-line header; see [Dependencies](#dependencies)) |

## Running locally

`scripts/run_local.sh` starts the whole stack on your machine with one command: PostgreSQL in Docker, the API and the web app, with demo accounts and data. It is for development only (development secrets, SMS codes returned by the API, emails printed instead of sent).

**Needs:** Docker (running, usable without sudo) and Node.js 20+. Python 3.13 is provided by [uv](https://docs.astral.sh/uv/); if `uv` isn't installed, the script downloads the standalone binary once into `var/tools/`.

```bash
bash scripts/run_local.sh            # http://localhost:8000  (API + built web app)
```

Then open http://localhost:8000 and sign in with a demo account (a step-by-step testing guide with every account, how to get codes and how to test appointments is in [`run_local.txt`](run_local.txt)):

| Account | Email | Password | Two-step code |
|---|---|---|---|
| Patient | `patient@demo.cardiosens.ai` | `cardiosens-demo-2026` | not needed |
| Doctor | `doctor@demo.cardiosens.ai` | `cardiosens-demo-2026` | `bash scripts/run_local.sh totp` |
| Clinic | `clinic@demo.cardiosens.ai` | `cardiosens-demo-2026` | `bash scripts/run_local.sh totp` |
| Doctor for appointments (Dr. Arjun Test) | `testdoctor@demo.cardiosens.ai` | `cardiosens-demo-2026` | `bash scripts/run_local.sh totp` |

The demo doctor and clinic are verified and already linked to the demo patient. Their authenticator key is `CARDIOSENSDEMOTOTPSECRETCODE2345`, so you can also add it to a real authenticator app. App (phone) sign-in demo numbers: patient `+919800000001`, doctor `+919800000002`, family member `+919800000003`.

All options (`--dev`, `--reset`, `--admin-email`, `--port`, `totp`, `ecg-live`, `stop`…) are in [Scripts → `run_local.sh`](#scriptsrun_localsh).

### What happens on each run

1. Starts (or creates) the `cardiosens-local-pg` container, PostgreSQL 17 on `127.0.0.1:55433`, data in the Docker volume `cardiosens-local-pgdata`.
2. Creates `.venv` with Python 3.13 and installs `requirements.txt` plus the dev tools from `pyproject.toml`, only when either file changed.
3. Runs `alembic upgrade head` and `app.scripts.seed --demo` (the seed is idempotent: a second run says "Demo data already present").
4. Installs `web/` dependencies when `package-lock.json` changed, and rebuilds `website/` when anything in `web/` is newer than the last build.
5. Starts uvicorn with `--reload` on `app/`: backend edits restart the API automatically.

The first run takes a few minutes (Docker image, Python, npm packages); later runs start in seconds.

### Emails and sign-up locally

By default nothing is emailed or texted (to test with real email and SMS, see `mail` / `sms` / `--lan` under [`scripts/run_local.sh`](#scriptsrun_localsh)). With `DEV_EXPOSE_OTP=true` (set by `run_local.sh`) the sign-up and sign-in pages show the one-time codes under the code fields ("Development: …"); emails (codes, password-reset and set-password links) are also printed in the terminal as `email_sent` log lines. To try sign-up: register at http://localhost:8000/register with any mobile number, enter the codes shown, and you are signed in. A new doctor or clinic stays unverified until a staff account verifies it: start with `--admin-email`, open the printed set-password link, sign in, turn on two-step sign-in under Account, then use **Verification** at `/admin/verification` (the doctor or clinic must first upload a licence document under Account).

### Local files

| Where | What |
|---|---|
| `.venv/` | Python virtualenv (git-ignored) |
| `var/tools/` | uv binary and Python 3.13 if the script downloaded them (git-ignored) |
| `var/storage/` | ECG waveforms and PDF reports (git-ignored, removed by `--reset`) |
| `var/local.env` | Real email / SMS settings saved by `run_local.sh mail` / `sms` (git-ignored, mode 600; `local-off` deletes it) |
| `var/run_local.log` | Output of a `run_local.sh` started in the background (e.g. `nohup setsid bash scripts/run_local.sh --lan > var/run_local.log 2>&1 &`) |
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

The 120 tests cover sign-up with SMS + email codes (no account until both are entered, no leaks, resend, phone-only sign-up, adding an email later), sign-in by email or SMS code and by password with email or mobile number, Google sign-up and sign-in (linking by verified email), SMS through MSG91 (request format, MSG91 error answers → 503, notice template, sign-up sending codes to both phone and email, sign-in codes to both the typed contact and the other confirmed one with either code working and both used up, no second code to an unconfirmed contact, MSG91 down on the second code), SMS outages (sign-up with the email code only, no email → 503, MSG91 failing mid-sign-up and on resend, the 5-minute down window, confirming the number later, an unconfirmed number not blocking its real owner, a registered number during an outage answering "already registered" after the email code instead of a false "wrong code", resend re-sending the email, codes never written to the log, sign-in by number pointing to email or password and both working) and with SMS off, the admin portal (licence uploads and who can open them, verify / reject with reason / resubmit / revoke, delete with grace period, restore, erase, the purge job, clinic erasure, user search and CSV export, statistics without health values), 12-lead uploads, live ECG streaming/viewing/saving and stall handling, document uploads (types, limits, permissions, comments, signed links), the dashboard summary, lockout, password reset and the web session cookie; 16-digit IDs; patient/doctor/clinic linking (approval, verification and two-step gates, clinic roster, requests on a doctor's behalf, ending a link); moving app accounts to email; SMS sign-in, refresh-token theft detection, per-item ingestion outcomes, idempotency, pagination, sharing scope and history windows, revocation, grant expiry, admin isolation from health data, ECG integrity, signed report links, profile pictures (type by content, size, replace, remove, erased with the account), the patients' Doctors list (only listed + verified doctors, search, picture, link state, patients only, listing edits keep verification), CardiosensAI devices over MQTT (`tests/test_device_hub.py`: factory CSV import all-or-nothing and the broker password file, pairing by device ID + serial + button with expiry and wrong-serial / owned / blocked answers, forged signatures, wrong keys, replays, stale clocks and another device's ID refused and counted, snapshot format, lead consistency, overlap, age, rate and duplicate rules, quality grading and research consent, configuration rules and who may change them, doctor devices recording only for an assigned linked patient and stopping when the link ends, live ECG on request with late chunks dropped quietly, unpair / device reset / staff block, test units only where test streams are on, account deletion removing snapshots, labels and pairings), patient↔doctor messages (linked only, notices without text, read receipts, access log, read-only after the link ends, erased with the account), the patient list's contact details and next appointment, patient details (validation, the Home reminder and its snooze, linked doctors and clinics only, street lines only when shared, access log, erased with the account) and Near me (PIN distances, radius filter, nearest first, no / unknown PIN answers), account deletion and the web app serving. `cd web && npm test` checks the client-side ID validation.

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
  App[Mobile app<br/>patients · doctors · clinics<br/>Expo / React Native<br/>cardioSE-WEB repo]
  Web[Web app — React SPA<br/>patient · doctor · clinic · staff]
  subgraph Server[One Ubuntu server]
    NG[nginx<br/>TLS, gzip, 55 MB uploads]
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
| Sign-up | Everyone | `POST /v1/auth/register` (mobile number required, email optional, password unless Google) → 6-digit code by SMS, and by email if given → `POST /v1/auth/register/verify {signupId, phoneCode, emailCode}` creates the account (phone/email verified) and signs in. Nothing is stored as an account before that: the pending sign-up (password already hashed) waits on the code challenge. Same answer when the number or email is taken (its owner gets a notice instead of a code). While SMS is down a taken number can't be noticed by SMS, so the email code still goes out; once it is entered correctly `/register/verify` answers 409 `already_registered` ("Sign in instead"), never a misleading "wrong code", and `/register/resend` sends the same email again. `/register/resend` sends new codes |
| SMS down (sign-up) | Everyone | When MSG91 fails (or `SMS_PROVIDER=none`) SMS counts as down for 5 minutes (`sms.mark_down`; `/v1/auth/options` → `smsAvailable: false`), so requests don't wait on MSG91 meanwhile. `/register` **with an email**: no SMS is sent, the answer has `phoneCodeSent: false`, and `/register/verify` needs only `emailCode` (a Google sign-up then also gets an email code). The account keeps the number **unconfirmed** (`phone_verified_at` NULL, `phoneVerified: false` on `/me` and `/auth/status`, audit detail "SMS down: number not confirmed yet"); the person confirms it later with `POST /v1/me/contact/code {kind: phone}` → `/me/contact/verify` (the web shows a reminder on the home page and a **Confirm mobile number** button in Settings; the app a reminder on Home/Patients and a card on Profile/Account). If the SMS fails mid-sign-up or on `/register/resend`, the same switch happens. **Without an email**: 503 `sms_unavailable` asking for one. An unconfirmed number never blocks its real owner: a later sign-up or contact confirmation that proves the number by SMS takes it over (`accounts.release_unconfirmed_phone`, audited `account.phone_released`), and while SMS is down an already-saved number can't be saved again. The unconfirmed number doesn't limit the account (the confirmed email activates it) |
| SMS down (sign-in) | Everyone | `/login/start` with a mobile number answers 503 `sms_unavailable`: "We can't send text messages right now. Sign in with your email address instead (we'll email you a code), or use your password." The web and app sign-in pages then offer **Sign in with password** (the code page in password-only mode) and **Use my email instead**. Email-code sign-in (no SMS second code while it's down) and password sign-in keep working |
| Code sign-in | Everyone | `POST /v1/auth/login/start {identifier}`: email → code by email, mobile number → code by SMS (MSG91), **and a second code to the account's other contact if it is confirmed** (`phone_verified_at` / `email_verified_at`; an unconfirmed, possibly mistyped one never gets sign-in codes). Same answer for unknown addresses (no code sent), and the answer never shows the other contact. → `POST /v1/auth/login/verify {challengeId, code}` accepts **either code**: both challenges share one `group_id` and the primary's 5-try limit (`otp.check_any`), and a successful sign-in uses up both. If MSG91 fails on the second code the first still works; an SMS failure on the typed number answers 503. A code also confirms that email or number on older accounts |
| Password | Accounts with a password | `POST /v1/auth/login {identifier, password}` (`email` still accepted from older app builds). Needs a confirmed number or email |
| Google | Everyone | `POST /v1/auth/google {credential}` (Google ID token) → signed in, or 404 `no_account` → sign-up with the token (`googleCredential`) |
| Two-step (TOTP) | Required for doctor, clinic, admin; optional for patients | Login returns `{mfaRequired, mfaToken}` (5 min) → `POST /v1/auth/mfa/verify` with the 6-digit code |
| Codes in detail | — | 6 digits, 10 min, 5 tries each, single use; only an HMAC bound to the challenge and address is stored (`otp_challenges`). Rate limits per address and per IP. SMS through MSG91 (`SMS_PROVIDER=msg91`, the server makes the code and MSG91 delivers it), email through SMTP. Older app builds' `/otp/request` + `/otp/verify` (phone sign-in that creates a patient) still work |
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
| Prerequisites to link or view | Confirmed mobile/email (sign-up does it) | Same + licence document + staff verification + TOTP | Same + licence document + staff verification + TOTP | TOTP for the admin portal |
| Dashboard (`/`) | Patient home | Patient list | Patient list + doctor roster | `/admin` portal: overview, verification, users |

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
4. ECG metadata goes to `ecg_recordings`; raw int16 samples to storage with a SHA-256 that is verified on every read (`/samples` streams them after the permission check). Multi-lead recordings list their channels in `leads` (standard names I, II, III, aVR, aVL, aVF, V1–V6) and store **interleaved frames** (`frame k = [lead0, lead1, …]`); `sample_count` counts frames. The `X-Leads` header tells clients the order.
5. History is keyset-paginated and keeps each reading's original UTC offset.

### Live ECG

```mermaid
sequenceDiagram
  participant D as Device / app (patient)
  participant API
  participant V as Doctor's browser
  D->>API: POST /v1/ecg-live {leads, sampleRateHz, deviceId}
  loop every ~1 s
    D->>API: POST /v1/ecg-live/{id}/chunks {seq, samplesBase64}
  end
  loop every 2 s
    V->>API: GET /v1/patients/{pid}/ecg-live (live | stalled | none + last recording)
  end
  loop every 1 s while live
    V->>API: GET /v1/ecg-live/{id}/samples?fromFrame=N
  end
  D->>API: POST /v1/ecg-live/{id}/stop
  API-->>V: status shows "not recording"; the new recording tops the list
```

- **Storage while live:** chunks are rows in `ecg_live_chunks` (12 leads × 500 Hz ≈ 12 KB/s), unique on `(session, seq)` so a retried chunk is ignored. Stopping joins them in `seq` order into one stored file, creates an `ecg_recordings` row (`acquisition = live`) and deletes the chunks (`services/ecg.finalize`).
- **States:** `live` (data within 5 s), `stalled` (shown as "Signal lost"), and after 2 minutes without data the session is saved automatically on the next status check. Starting a new session saves any previous one.
- **Why polling:** no WebSocket means no nginx or systemd changes and it works through any proxy; viewers never get more than the last 10 s, so a slow viewer jumps forward instead of lagging.
- **Access log:** viewers poll every second, so each viewer gets one `ecg.live_view` entry per session, not one per poll.
- **Devices:** **Live** (owner or linked doctor) makes a paired CardiosensAI device record 20 s now: its seconds stream over MQTT into the same live-session tables as a preview (`services/device_hub._live_chunk`, lead-consistency checked), then the whole 20 s arrives as a snapshot (`trigger: live`) and the preview is discarded (`services/ecg.finalize` keeps no recording for device sessions). **Also:** `app/scripts/simulate_ecg.py` (`run_local.sh ecg-live`) signs in as a patient through the public API and streams a synthetic 12-lead tracing from `services/ecg_synth.py` (sums of Gaussian waves, limb leads derived by Einthoven/Goldberger, labelled synthetic everywhere).

### Documents

`app/api/v1/documents.py`. One document = title, type (`lab`, `prescription`, `imaging`, `discharge`, `ecg`, `other`), optional date and note, **1–10 files** (each ≤ 15 MB, ≤ 50 MB together; nginx allows 55 MB bodies), and a comment thread.

| | Patient | Linked doctor | Linked clinic | Family share / others |
|---|---|---|---|---|
| See, download | ✓ | ✓ | ✓ | ✗ (404) |
| Upload | ✓ | ✓ | ✓ | ✗ |
| Comment | — | ✓ | — | ✗ |
| Delete document | Any of theirs | Own uploads | Own uploads | ✗ |
| Delete comment | — | Own | — | — |

- File type is decided by the **content signature** (`%PDF-`, `FF D8 FF`), never by name or the browser's type; names are reduced to a safe base name with the matching extension.
- Stored at `documents/{patient}/{document}/{file}.{pdf|jpg}` with SHA-256, checked on every read.
- Viewing: `POST …/files/{id}/link` returns a 10-minute signed URL bound to the user; opening it re-checks permission and writes `document.view` to the patient's access log. JPEGs get a sandboxing CSP; PDFs open in the browser's viewer.
- Doctor or clinic uploads and doctor comments notify the patient. Deleting a document removes its files; account deletion removes all of the patient's documents.

### Doctor dashboard

`GET /v1/linked-patients` returns, per approved patient, the newest `hr`, `spo2` and `bp`, the last ECG (leads, length, HR) and the live state. The web app sorts by newest activity (live first) and refreshes every 15 s. Reading these values is health-data access, so it is logged as `dashboard.summary`, at most once an hour per viewer and patient.

### Patient list and messages

- **Patients tab** (doctor; the clinic home is the same list): name and ID, **contact** (confirmed mobile number and email only, tap to call / mail; a patient shares them by approving the link), the doctor's **next appointment** with the patient, and the last ECG. Heart rate / SpO₂ / blood pressure are no longer in the list (they stay on the patient page). Patients with unread messages and live ECGs come first.
- **Patient details and Near me** (`patient_profiles` table, `services/patient_profile.py`, `api/v1/patient_profile.py`): patients add height, weight (BMI shown), sex, blood group, address with **PIN code** (district and state filled in from the PIN) and health background (common conditions, other, allergies, medicines, smoking). All optional. Web: Account → **About you** (`/settings#about`), and once right after sign-up (`/about-you`, Skip allowed); app: Profile → About you, and the same step after sign-up. Until a PIN code is saved, Home shows "Add your address and PIN code" (Later hides it for a day, stored on the server). Linked doctors and clinics see the details on the patient page (`profile.view` in the access log); the street lines only if the patient ticks *Share my street address*. Doctors add a **Practice PIN code** to their Doctors-list card; patients turn on **Near me** on the Doctors tab (10 / 25 / 50 / 100 km) for listed doctors within that distance, nearest first ("Same area", "about 45 km"). Distances are between PIN code areas, so approximate. PIN locations: `app/data/in_pincodes.csv.gz`, built from [GeoNames](https://www.geonames.org/) postal codes (CC BY 4.0; credited in the UI) by `python -m app.scripts.build_pincodes --geonames IN.txt` (`--india-post FILE.csv` takes the India Post directory instead, if its licence suits).
- **Messages** between a patient and a doctor they're linked with (`messages` table, `services/messages.py`): either side writes while the link is active; after it ends the history stays readable, read-only. Web: patients have a **Messages** tab (`/messages`, unread badge; "Message" next to an appointment), doctors write from the Patients list ("Message" with unread count; badge on the Patients tab) or the patient page (Messages + Appointments cards). App: Home → Messages (patients), "Message" on each patient card (doctors). The other side gets one in-app notice per unread streak, **never the text**; a doctor opening a conversation is logged in the patient's access log (`message.view`, at most hourly), sends as `message.send`. Erased with either account. Not for emergencies (the UI says so).

### Appointments

Doctors (web: **Home** tab) set weekly hours, slot length (10–60 min), booking window (7/14/30 days), online and/or in-person visits and a default video link (`doctor_schedules`). Slots are made from the weekly hours in the doctor's time zone. Patients book a slot as **online** (video link from the doctor, shown once accepted) or **in person**: linked patients, and anyone when the doctor is in the Doctors list; at least 1 h ahead, at most 2 open appointments per doctor, one open booking per slot (partial unique index). Booking never grants access to health data; only a link does.

- **Requests:** `requested` → the doctor accepts (optionally a different video link) or declines; unanswered requests **expire** when the slot starts. Either side cancels before the start. After the start the doctor marks it **completed** (counts as a patient served) or **no-show**. The doctor can add walk-ins (a linked patient's ID or a name; past times are saved as completed).
- **Offline mode:** "Go offline" blocks *online* bookings, by default for the next 2 hours (or until a chosen time); "Go online" ends it at once. Scheduled offline periods (holidays, conferences) do the same for a date range. In-person slots stay bookable; accepted appointments are kept.
- **Calendar** (Home): Day / Week / Month like Google Calendar, in the doctor's time zone. Working hours are white, closed hours grey; free time is simply empty (no slot tiles). Appointments are blocks coloured by status (accepted filled, requests dashed, completed light, no-show red; declined / cancelled / expired are hidden), offline periods hatched, a red line marks now. Click empty time to add a visit there; click a day header or a month day to open that day; the title opens a small month to jump to a date. The **Availability** card on top holds both "Go offline / Go online" and the scheduled offline periods (chips, ✕ to remove, "+ Schedule offline").
- **Linking when booking:** a patient who isn't linked with the doctor sees "Also link with Dr. …" (on by default). Booking then also sends a link request the patient has approved; the doctor **accepting the appointment approves it** (an active link + access grant, as in Care team). Without the tick, nothing is shared. `GET /v1/doctors/{id}/calendar` returns `linkStatus`; appointments show the doctor `linkPending`.
- **Consultation** (appointment panel, linked patients): a shortcut to the doctor's own paired CardiosensAI device: "Use for {patient}" sets the device's *Records for* (same as Account → Devices), "Record ECG now (20 s)" asks for a live recording, "Stop recording" clears it. Snapshots and live ECGs go to that patient's record and database as usual. "+ Add report or result" uploads PDF/JPEG files to the patient's documents (dated the visit day; the patient is told). Unlinked patients get "ask them to link" instead.
- **Statistics** tab: requests waiting, upcoming (online / in person), next appointment; per today / 7 / 30 days / all time: completed (online / in person), different patients, no-shows, cancelled, declined, expired, not marked yet.
- **Notifications:** every request, decision and cancellation goes to the other side in the app and by email (never the patient's note); reminders 1 h before (`appointment_reminders.py`; SMS once `MSG91_REMINDER_TEMPLATE_ID` is set, Flow variables `##doctor##` and `##time##`).
- Patients book on the website (Appointments tab, or a doctor's profile in the Doctors list) and in the app (Home → Appointments, or Doctors → profile → Book appointment). Appointments are erased with either account.

### Reports

`POST /v1/reports` returns 202 and a FastAPI background task renders the PDF (reportlab) into storage. `POST /v1/reports/{id}/download-link` returns a 10-minute HMAC-signed URL bound to one user and report; the download re-checks permission.

### Admin portal (`/admin`)

Web pages for staff (`web/src/pages/Admin.tsx`); API in `app/api/v1/admin.py` (`require_admin`: admin role + TOTP, re-checked per request). Every action is audited; nothing returns health values.

| Page | What it does | API |
|---|---|---|
| **Overview** `/admin` | Accounts by role × status, email confirmed, two-step on; verification counts (incl. "no documents"); sign-ups per day (≤ 90 days) or week; signed in last 24 h / 7 / 30 days (`users.last_active_at`, set on sign-in and refresh); patients who uploaded readings / recorded ECGs in the period / have a device; platform volume (readings, ECGs, live sessions, documents, reports, devices, care links, family shares) total and in the period. Period 7/30/90/365 days. CSV export | `GET /v1/admin/stats?days=`, `/stats.csv` |
| **Verification** `/admin/verification` | Doctors and clinics by status (pending / verified / rejected / revoked / all) with registration details and their **licence documents** (opened through 10-minute signed links; each view audited as `admin.licence_view`). **Verify** needs at least one document. **Reject** (pending) and **Revoke** (verified) need a reason, which is stored, shown to the account and emailed; revoking stops patient access at once (`can_view_patients`). Every decision is emailed | `GET /professionals`, `/organizations` `?status=`; `POST .../verify {decision: verified|rejected|revoked, note}`; `POST /verification-files/{id}/link` |
| **Users** `/admin/users` | Search (name, email, ID prefix, phone suffix), filter by role, status, verification; 50 per page; CSV export (emails masked unless "Full email addresses" is ticked; exports are audited). Per account: set/change sign-in email, reactivate a suspended account, **Delete** (with reason), and for accounts scheduled for deletion **Restore** or **Erase now** (type the 16-digit ID) | `GET /users`, `/users.csv`; `POST /users/{id}/delete`, `/restore`, `/erase`, `/email`; `PATCH /users/{id}/status` |

**Deletion** (`app/services/erasure.py`): an admin delete sets status `deleting` and `erase_after = now + 30 days`, ends every session and emails the user. Every check for `status == "active"` (sign-in, API, health-data gate, links, directory, dashboards) then treats the account as gone, but links and data are kept, so **Restore** brings everything back. After 30 days the purge job (`app/scripts/purge_deleted.py`, daily systemd timer) erases it exactly like a self-delete: health data and files, licence documents, personal details, sessions and notifications are removed; links and shares end; for a clinic, its links end and the organisation is anonymised. The anonymised row stays (the ID is never reused, audit events still resolve) and consent records are kept as evidence. Admin accounts can't be deleted from the portal (only from the server), nor can admins delete themselves. A patient's own **Delete account** is still immediate.

**Licence documents** (`app/api/v1/verification.py`): doctors and clinic logins upload their registration / licence certificate under Account (`POST /v1/me/verification/files`, PDF or JPEG by content, 15 MB each, at most 3 kept; stored under `verification/<user>/`). Only the owner and staff can open them. While an account isn't verified it can remove files; a verified account keeps them. Account status lists `upload_licence` until one exists. After a rejection or revocation the account fixes its details or files and resubmits (`POST /v1/me/verification/resubmit`, or editing the registration details), which puts it back in the pending queue.

### Web app (`web/` → `website/`)

| Piece | File | Notes |
|---|---|---|
| Entry, routes | `src/main.tsx`, `src/App.tsx` | React Router. `/` = sign-in or the role's home. `/links`, `/doctors`, `/patients/:id`, `/devices` (→ `/settings#devices`), `/settings`, `/admin`, `/admin/verification`, `/admin/users`, `/admin/devices`, `/register` → `/register/verify` (codes), `/login/code` (code or password), `/forgot-password`, `/reset-password` |
| Session | `src/auth.tsx`, `src/api.ts` | On load calls `/v1/auth/refresh` (cookie) to restore the session; a 401 triggers one silent refresh. Access token only in memory |
| Screens | `src/pages/*` | `PatientHome`, `ProDashboard` (latest-recordings dashboard, patient detail with collapsible History, clinic doctors), `LinksPage`, `Settings`, `Admin`, `AuthPages` |
| Components | `src/components/*` | Phone input (country-code dropdown with flags, India default, stores E.164; data and rules in `src/countries.ts`), ID card with QR, vitals + SVG trend chart (gaps never interpolated), Google button (`GoogleButton.tsx`, Google Identity Services), mobile/email card with add-by-code (`Contact.tsx`), ECG viewer (12-lead 3×4 + rhythm strip or per-lead strips, 25 mm/s × 10 mm/mV paper, calibration pulse, 10 s pages) and live ECG panel (`Ecg.tsx`), documents with uploads, previews and comments (`Documents.tsx`), licence documents and resubmission (`Licence.tsx`), reports, link tables, TOTP setup, setup checklist |
| Header, brand | `src/components/Layout.tsx` | Logo with the tagline **Real Time · Real Hope** underneath (`Tagline`, logo gradient, `.tagline` in `theme.css`; also on the sign-in/sign-up card). Nav holds the role's pages only; a **☰ menu button** (three bars, label "Account") opens `/settings`; it sits next to the name, ID and picture, beside the light/dark toggle and Sign out, for every role. No disclaimer lines on pages |
| Footer | `src/components/SiteFooter.tsx` | On every page (dashboards via `Layout`, sign-in / sign-up / reset via `AuthShell`): the cardiosens.ai footer links (How it works, Our AI, Team, Blog, Contact, Newsletter; absolute `https://www.cardiosens.ai/…`, new tab, `noopener`) and © year. The site's "not approved for clinical use" line is left out (no disclaimers on pages) |
| Doctors (patients) | `src/pages/Doctors.tsx` | `/doctors` for patients (clinics keep their roster there): search, cards (picture, name, specialty, degrees, experience, hospital/city) that load more as the page scrolls, a card opens the profile in a `<dialog>` (about, languages, registration council, ID, **Send link request**; click outside closes). Doctors fill their card under Account → **Doctors list** and choose whether to appear |
| Devices | `src/components/Devices.tsx` | **Devices** section of the Account page (patients, doctors, clinics; not in the header nav): **Add a CardiosensAI device** (device ID, serial, optional name → "press the button on the device" with a countdown, polling the pairing), the devices with Online/Offline, last seen, firmware and schedule; **Device configuration** (interval presets, quiet hours, time zone; doctors/clinics choose the linked patient it records for), **Live (20 s now)**, **Unpair…**; for patients the **research consent** switch (`/v1/me/consents`, kind `research`) |
| ECG cards | `src/components/EcgCards.tsx` | Patient home and the doctor's patient page: **Live ECG** card (a **● Live** button per device recording for the patient; the live stream while one records, otherwise the ECG selected in the list, newest by default; for snapshots the quality, doctors' labels and, for doctors, the **SCP-ECG label form**) beside the **ECG recordings** card (stored recordings and device snapshots by date and time; Live ones tagged) |
| Admin devices | `src/pages/Admin.tsx` (`AdminDevices`) | `/admin/devices`: factory CSV import (errors per line, nothing imported on any error), every unit with status, owner's ID, last seen, rejected-message count and last code, **Block…** (reason) / **Unblock** |
| Patient home | `src/pages/PatientHome.tsx` | Slim **latest readings** strip on top (`LatestVitals compact`), then **Live ECG** (big, with the last recording when idle) beside **ECG recordings** (`EcgRecordingsCard`: short list; a click opens the full viewer in a `<dialog>`, a click outside closes it), then one **Reports and documents** card: upload + PDF report on the left, **History** (`FileHistory.tsx`, documents and reports newest first) on the right. No care-team card (Care team page in the nav) |
| Account page | `src/pages/Settings.tsx` | `/settings`, for patient, doctor, clinic (and admin): one continuous page, no cards. Sections start with **Your ID** (ID card with QR and Copy ID) and Profile (picture via `components/Avatar.tsx`, which also shows it in the header); Password asks for the new one twice. A sticky menu on the left (name, ID, one link per section) follows the scroll (IntersectionObserver) and jumps to a section (`/settings#password` links work). Sections: Profile, Mobile and email, Registration / Clinic details and Verification documents (doctor / clinic), Two-step sign-in, Password. Under 760 px the menu becomes a sticky sideways strip and the header scrolls away |
| Theme | `src/theme.css` | Light palette from the patient app, dark from the V1.0 site; follows the OS, toggle stored in `localStorage` |

The build output is committed in `website/` (hashed assets cached for a year, `index.html` revalidated on every load), so servers need no Node.js. `app/website.py` adds security headers: a Content-Security-Policy that allows only same-origin scripts (plus the hash of the inline theme script) and same-origin API calls, `frame-ancestors 'none'`, `nosniff`, `Referrer-Policy: same-origin`, and a Permissions-Policy.

After changing `web/`, rebuild and commit both folders: `cd web && npm ci && npm test && npm run typecheck && npm run build` (or let `run_local.sh` rebuild it).

### Security summary

- Secrets refuse development values in staging/production (`core/config.py`), as do `DEV_EXPOSE_OTP`, the console SMS sender and the console email sender.
- Rate limits (in-process): sign-in by IP, codes by address and IP, registration (IP and number), resends, Google sign-in, lookups, link requests, report generation.
- The web pages' CSP allows exactly one outside origin, Google Identity Services (`accounts.google.com/gsi/`: script, style, iframe, endpoint), and they send `Referrer-Policy: strict-origin-when-cross-origin` (Google checks the origin); API responses keep `no-referrer`.
- Audit events never contain health values. Request logs record the route template (`/v1/reports/download/{token}`), not the URL, so ids and signed tokens stay out of logs; uvicorn's own access log is off. Phone numbers and emails are masked in logs and the admin portal (full emails only in an explicitly requested, audited CSV export).
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
  users ||--o{ verification_files : "licence documents"
  users {
    uuid id PK
    string unique_id UK "16-digit ID"
    string email UK "lower-case"
    timestamptz email_verified_at
    string password_hash "argon2id"
    string phone UK "mobile, required at sign-up"
    timestamptz phone_verified_at "SMS code"
    string google_sub UK "Google account id"
    string role "patient|doctor|clinic|admin"
    uuid organization_id FK "clinic login"
    string status "active|suspended|deleting|deleted"
    timestamptz erase_after "deleting: purge after"
    timestamptz last_active_at "last sign-in / refresh"
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
    string verification_status "pending|verified|rejected|revoked"
    string review_note "reason, shown to the clinic"
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

Appointments: `doctor_schedules` (one row per doctor), `doctor_offline_periods`, `appointments` (unique open booking per doctor + start: `uq_appointments_doctor_open_slot`).

Also present: `device_models`, `ecg_recordings` (storage key + SHA-256, `leads`), `ecg_live_sessions` + `ecg_live_chunks`, `documents` + `document_files` + `document_comments`, `professional_profiles` (registration details, `verification_status` incl. `revoked`, `review_note`), `verification_files` (licence documents: storage key + SHA-256), `otp_challenges` (one-time codes: purpose signup / login / add_email / add_phone / legacy, channel sms / email, the pending sign-up in `payload`), `refresh_tokens`, `action_tokens`, `invites`, `reports`, `consents`, `notifications`, `ingestion_rejections` (reason codes only) and `audit_events` (never health values).

**Indexes:** `measurements(patient_id, vital, measured_at)` for history, unique `(patient_id, client_id)` for idempotency, partial unique `(model_id, serial) WHERE status='active'` for device ownership, `audit_events(patient_id, at)`, and `care_links` by patient, doctor and organisation.

**Migrations** (`alembic/versions/`):

| Revision | Change |
|---|---|
| `0001` | Initial schema (phone sign-in, grants, readings, ECG, reports, audit) |
| `0002` | Email + password, `organizations`, `care_links`, `action_tokens`, 16-digit IDs back-filled for every user, caregivers → patients, phone optional, unique lower-case email. Downgrade refuses if email-only accounts exist |
| `0003` | `ecg_recordings.leads` (back-filled with each recording's lead), live ECG tables, document tables |
| `0005` | Code sign-up/sign-in and Google: `otp_challenges` gets purpose, channel, email, user, group and payload (phone optional); `users.phone_verified_at` (back-filled for accounts with a phone: it could only get there by SMS code), `users.google_sub`; unused email-confirmation tokens are retired |
| `0006` | Profile pictures: `users.avatar_key` (storage key) and `users.avatar_updated_at` |
| `0007` | Patients' Doctors list: `professional_profiles.listed` (default false) plus `degrees`, `experience_years`, `languages`, `practice`, `city`, `about` |
| `0008` | Device tab: `devices.device_uid` (unique among active devices: `uq_devices_active_uid`), `ecg_live_sessions.acquisition` (`live` / `simulated`, default `live`) |
| `0009` | CardiosensAI devices over MQTT: `factory_devices` (provisioned units: MQTT login hash, encrypted signing key, block state, rejection counters), `device_pairings`, `device_configs` (interval, quiet hours, time zone, assigned patient, live request), `device_outbox`, `device_nonces` (replay guard), `ecg_snapshots` (unique on device + seq) and `ecg_snapshot_labels` (doctor's SCP codes); `devices.factory_device_id` (`fk_devices_factory_device`) |
| `0010` | Appointments: `doctor_schedules`, `doctor_offline_periods`, `appointments`. New tables only |
| `0011` | Messages: `messages` (patient ↔ doctor). New table only |
| `0012` | Patient details: `patient_profiles`; `professional_profiles.pin_code` (practice PIN). New table + nullable column |
| `0004` | Admin portal: `users.erase_after`, `users.last_active_at` (back-filled from each user's newest session), `review_note` on `professional_profiles` and `organizations`, `verification_files` |

`alembic upgrade head` applies them (`run_local.sh` and `deploy.sh` do it for you). After changing models: `alembic revision --autogenerate -m "..."`, review the file, then check with `alembic check` against PostgreSQL. The two foreign keys that close the users↔organizations cycle are named (`fk_organizations_owner`, `fk_organizations_verified_by`) so `drop_all` works.

**Retention (to be decided with legal review):** readings until the account is deleted (admin deletions: 30 days later, by the purge job); ingestion rejections can be pruned after 90 days; OTP challenges, used action tokens and expired refresh tokens daily; audit events per counsel's advice.

## API overview

Interactive docs at `/docs` (disabled when `ENV=production`); the full spec is `docs/openapi.json` (regenerate with `python -c "import json; from app.main import app; json.dump(app.openapi(), open('docs/openapi.json','w'), indent=2)"`).

| Area | Endpoints |
|---|---|
| Sign-up / sign-in | `GET /v1/auth/options`; `POST /v1/auth/register`, `/register/verify`, `/register/resend`, `/login/start`, `/login/verify`, `/login` (password), `/google`, `/mfa/verify`, `/refresh`, `/logout`, `/logout-all`, `/password/forgot`, `/password/reset`, `/password/change`, `GET /v1/auth/status`; add a number/email: `POST /v1/me/contact/code`, `/me/contact/verify`; older app builds: `/otp/request`, `/otp/verify`; TOTP: `/mfa/setup`, `/mfa/enable`, `/mfa/disable` |
| Doctors | `GET /v1/doctors?q=&limit=&offset=` (patients only: doctors who opted in, staff-verified, active, on TOTP; search over name, specialty, degrees, hospital, city), `GET /v1/doctors/{id}` (profile + this patient's `linkStatus`), `GET /v1/doctors/{id}/avatar`; doctors edit theirs with `GET/PUT /v1/me/doctor-listing` (`visible` + `hiddenReason`; doesn't reset verification). Never shows phone or email |
| Me | `GET/PATCH/DELETE /v1/me`, `/me/consents`, `/me/professional-profile` (doctor), `/me/organization` (clinic), `/me/export`, `/me/access-log`; profile picture `PUT/GET/DELETE /v1/me/avatar` (JPEG/PNG by content, ≤ 512 KB; the web app crops it to a 320 px square JPEG; `avatarVersion` on the user changes with it; erased with the account) |
| Links | `GET /v1/directory/{id}`, `GET/POST /v1/links`, `POST /v1/links/{id}/approve|decline|end`, `GET /v1/linked-patients`, `GET /v1/clinic/doctors` |
| Health data | `POST /v1/measurements:batch`, `GET /v1/patients/{id}/measurements[/latest]`, `POST /v1/ecg-recordings`, `GET /v1/patients/{id}/ecg-recordings`, `GET /v1/ecg-recordings/{id}[/samples]` (`{id}` may be `me`) |
| Live ECG | `POST /v1/ecg-live`, `POST /v1/ecg-live/{id}/chunks`, `POST /v1/ecg-live/{id}/stop` (patient's device); `GET /v1/patients/{id}/ecg-live`, `GET /v1/ecg-live/{id}/samples?fromFrame=N` (viewers) |
| Documents | `POST/GET /v1/patients/{id}/documents` (multipart upload: `files`, `title`, `category`, `documentDate`, `note`), `DELETE /v1/documents/{id}`, `POST /v1/documents/{id}/comments`, `DELETE /v1/documents/{id}/comments/{cid}`, `POST /v1/documents/{id}/files/{fid}/link`, `GET /v1/documents/files/{token}` |
| Devices | `GET /v1/device-models`, `GET /v1/devices` (with `connected`, `online`, `config`), `POST /v1/devices` (patients' Bluetooth devices from the app; CardiosensAI devices answer 400 `pair_instead`), `PATCH /v1/devices/{id}/status` (patients), `DELETE /v1/devices/{id}` (remove; a CardiosensAI device is unpaired; past data kept) |
| Device pairing and config | `POST /v1/devices/pair` `{deviceUid, serial, displayName?}` → pairing (2 minutes for the button press; 404 `no_matching_device`, 409 `device_blocked` / `already_paired` / `device_owned`), `GET`/`DELETE /v1/devices/pairings/{id}`, `PUT /v1/devices/{id}/config` `{intervalMin, quietStart, quietEnd, timezone, assignedPatientId}` (owner or a linked doctor/clinic; assigning only by the doctor/clinic owner, to a linked patient), `POST /v1/devices/{id}/live` `{on}` (record 20 s now; `on: false` cancels), `GET /v1/patients/{id}/devices`. Audited `device.paired`, `device.config`, `device.live_request`, `device.unpaired` |
| ECG snapshots | `GET /v1/patients/{id}/ecg-snapshots?before=&limit=`, `GET /v1/ecg-snapshots/{id}`, `GET /v1/ecg-snapshots/{id}/samples` (int16 µV, 12 leads interleaved, SHA-256 checked), `GET /v1/ecg-snapshots/scp-codes`, `PUT`/`DELETE /v1/ecg-snapshots/{id}/label` (doctors with access only) |
| Admin devices | `GET /v1/admin/factory-devices`, `POST /v1/admin/factory-devices/import` (CSV `device_uid,serial,model_code,secret[,batch,test]`, all rows or none), `POST /v1/admin/factory-devices/{id}/block` `{reason}` / `unblock`. Audited |
| Family sharing | `GET/POST /v1/grants`, `POST /v1/grants/{id}/revoke`, `POST /v1/invites/accept`, `GET /v1/shared-with-me` |
| Reports | `POST/GET /v1/reports`, `GET /v1/reports/{id}`, `POST /v1/reports/{id}/download-link`, `GET /v1/reports/download/{token}` |
| Appointments (doctors) | `GET/PUT /v1/me/calendar` (settings + offline status), `POST/DELETE /v1/me/calendar/offline` (go offline `{until?}` default 2 h / go online), `POST /v1/me/calendar/offline-periods`, `DELETE /v1/me/calendar/offline-periods/{id}`, `GET /v1/me/calendar/week?start=&days=&slots=` (up to 42 days; `slots=false` for appointments and offline periods only), `POST /v1/me/appointments` (walk-in), `GET /v1/me/appointment-stats` |
| Appointments (both) | `GET /v1/appointments?scope=upcoming|past|all&status=`, `POST /v1/appointments/{id}/decision` `{action: accept|decline|cancel|complete|no_show, note?, meetingLink?}` (patients: cancel only). Audited `appointment.*`, `calendar.*` |
| Messages | `GET /v1/conversations` (partners, unread, preview), `GET /v1/conversations/unread`, `GET /v1/conversations/{userId or 16-digit ID}?before=` (newest 100, marks theirs read), `POST /v1/conversations/{party}/messages` `{body}` (≤ 2000 chars, linked only, 120/h) |
| Patient details | `GET`/`PUT /v1/me/patient-profile` (patients; fields sent are changed, `""` clears), `POST /v1/me/patient-profile/remind-later` (hide the Home reminder for a day), `GET /v1/me/patient-profile/conditions`, `GET /v1/patients/{id}/profile` (linked doctors and clinics), `GET /v1/pincodes/{pin}` (place, district, state) |
| Near me | `GET /v1/doctors?nearKm=1..500`: listed doctors with a practice PIN within that distance of the patient's PIN, nearest first, with `distanceKm`; `near` / `nearProblem` on the page. `PUT /v1/me/doctor-listing` takes `pinCode` |
| Linked patients | `GET /v1/linked-patients` adds `phone`, `email` (confirmed only), `nextAppointment` and `unreadMessages` (doctor view); `GET /v1/appointments?with={patientId}` filters a doctor's appointments to one patient |
| Appointments (patients) | `GET /v1/appointments/doctors` (linked doctors taking bookings), `GET /v1/doctors/{id}/calendar` (free slots, offline until, `limitReached`, `linkStatus`), `POST /v1/appointments` `{doctorId, startsAt, mode, reason?, allowLink?}` (`allowLink`: also ask to link; accepting approves it) |
| Notifications | `GET /v1/notifications`, `POST /v1/notifications/{id}/read`, `/read-all` |
| Admin | `/v1/admin/stats` (+ `.csv`), `/users` (+ `.csv`, `/{id}/status`, `/email`, `/delete`, `/restore`, `/erase`), `/professionals` (+ `/verify`), `/organizations` (+ `/verify`), `/verification-files/{id}/link`, `/device-models`, `/ops`, `/audit-events` |
| Verification (doctors, clinics) | `/v1/me/verification`, `/me/verification/files` (+ `/{id}`, `/{id}/link`), `/me/verification/resubmit`, `/v1/verification-files/{token}` |
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
| `SMS_PROVIDER` | `console` | One-time codes by SMS: `msg91` (`MSG91_AUTH_KEY`, `MSG91_OTP_TEMPLATE_ID`, optional `MSG91_NOTICE_TEMPLATE_ID`, optional `MSG91_REMINDER_TEMPLATE_ID` for appointment reminders), `console` (log), `none` (sign-up needs an email and leaves the number unconfirmed; SMS sign-in answers 503 `sms_unavailable`). Set with [`setup_msg91.sh`](#scriptssetup_msg91sh) |
| `GOOGLE_CLIENT_ID`, `GOOGLE_EXTRA_CLIENT_IDS` | empty | [Google sign-in](#google-sign-in): the web OAuth client id; app client ids (JSON list). Empty: no Google button |
| `DEVICE_TEST_STREAMS` | on in development/test, off in staging/production | Accept test units (imported with `test=1`, e.g. `../Test_Scripts/stream_device.py`); their data is stored labelled simulated. Leave unset on production |
| `DEVICE_KEYS_SECRET` | dev value | Encrypts every device's signing key (AES-256-GCM). ≥ 32 characters outside development; generated by `deploy.sh`; never change it |
| `MQTT_HOST`, `MQTT_PORT`, `MQTT_TLS`, `MQTT_USERNAME`, `MQTT_PASSWORD` | `localhost`, 1883, off, `cs-server`, dev value | The bridge's broker connection and login (`MQTT_PASSWORD` ≥ 24 characters outside development) |
| `MQTT_PASSWD_FILE`, `MQTT_RELOAD_COMMAND` | `./var/mosquitto/passwd`, empty | Where the app writes the broker's logins; a command to make the broker re-read them (locally `docker kill -s HUP cardiosens-local-mqtt`; on the server a systemd path unit does it) |
| `DEV_EXPOSE_OTP` | `false` | Development only: return one-time codes in API responses (the pages show them) |
| `STORAGE_BACKEND`, `STORAGE_LOCAL_PATH`, `S3_*` | `local`, `./var/storage` | Where ECG waveforms and PDFs go |
| `CORS_ORIGINS` | `[]` | Only needed for a web client on another origin (the built-in web app is same-origin) |
| Documents | `MAX_DOCUMENT_FILES` 10, `MAX_DOCUMENT_FILE_BYTES` 15 MB, `MAX_DOCUMENT_UPLOAD_BYTES` 50 MB, `DOCUMENT_LINK_TTL_S` 600 | Upload limits (nginx `client_max_body_size 55m` must stay above the total) |
| Lifetimes and limits | see `config.py` | `ACCESS_TOKEN_TTL_S` 900, `REFRESH_TOKEN_TTL_S` 30 d, `OTP_TTL_S` 10 min, `OTP_MAX_ATTEMPTS` 5, `PASSWORD_RESET_TTL_S` 1 h, `LOGIN_MAX_FAILURES` 5, `LOGIN_LOCKOUT_S` 900, `MAX_BATCH_SIZE` 100 |

## Connecting the mobile app

The mobile app ([cardioSE-WEB](https://github.com/mazumdersayon-del/cardioSE-WEB)) is one Expo app for **patients, doctors and clinics**, using the same accounts and API as the web app (see its README). Default server `https://platform.cardiosens.ai`; override at build time with `CS_API_BASE_URL`, or in the app's sign-in screen (Server field). Its `src/api/client.ts`:

1. Same sign-up and sign-in as the web: `/register` → `/register/verify` (codes), `/login/start` → `/login/verify` or `/login` (password), `/google` (ID token from `expo-auth-session`), then `/mfa/verify` for TOTP. Mobile clients get the refresh token in the JSON body (no `X-CS-Web` header); it's kept in `expo-secure-store` and rotated with `/v1/auth/refresh`.
2. Patients record consent with `POST /v1/me/consents` (`data_processing`) before the dashboard.
3. Patients: pulls measurements, ECG recordings (+ binary samples), devices, grants, notifications and the access log; registers real devices with `POST /v1/devices` (`clientRef` = the app's local id) and uploads through `POST /v1/measurements:batch`. Server PDF reports (`/v1/reports`), documents, links and invites use the same endpoints as the web.
4. Doctors/clinics: `/v1/linked-patients` (latest values, last ECG, live status), per-patient measurements/ECG/live ECG/documents, `/v1/links`, `/v1/directory/{id}`, `/v1/clinic/doctors`.

The app's **simulated** (demo) readings are never uploaded, and the server refuses them anyway (`reason: Simulated readings are never stored as health data`).

For the web preview of the app (`npx expo start --web`, port 8081) against a local API, allow its origin: `CORS_ORIGINS='["http://localhost:8081"]'`. The Android emulator reaches a local API at `http://10.0.2.2:8000` (development build with `CS_ALLOW_HTTP=1`).

## Deploying to a server

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
| nginx + HTTPS | Via [`nginx.sh`](#scriptsnginxsh): web app at `/`, API at `/v1`, Let's Encrypt certificate; check or renew with [`update_ssl.sh`](#scriptsupdate_sslsh) |
| Backups | Daily at 02:30 (systemd timer): database dump + storage archive in `/var/backups/cardiosensai`, kept 14 days. Plus `pre-migrate-*.dump` before any run that applies a migration |
| Appointment reminders | Every 5 minutes (systemd timer `cardiosensai-reminders`): reminders 1 h before accepted appointments, expires unanswered requests ([`appointment_reminders.py`](#appscriptsappointment_reminderspy)) |
| Account purge | Daily at 03:15 (systemd timer `cardiosensai-purge`): erases accounts deleted in the admin portal more than 30 days ago ([`purge_deleted.py`](#appscriptspurge_deletedpy)) |
| Device broker | Mosquitto: `127.0.0.1:1883` for the bridge, **8883 with TLS** (the Let's Encrypt certificate, copied for Mosquitto by a certbot deploy hook) for devices. No anonymous access; device logins written by the app to `/var/lib/cardiosensai-mqtt/passwd`; the path unit `cardiosensai-mqtt-logins` copies them to `/etc/mosquitto/cardiosens.passwd` (owner mosquitto, 0600, as Mosquitto 2.1 requires) and reloads the broker; ACL `scripts/mosquitto/cardiosens.acl` (each device only its own topics) |
| Device bridge | systemd `cardiosensai-mqtt` ([`mqtt_bridge.py`](#appscriptsmqtt_bridgepy)), same user and sandbox as the API. `DEVICE_KEYS_SECRET` and `MQTT_PASSWORD` are generated once in `backend.env`: **back up `DEVICE_KEYS_SECRET`**, without it every device must be re-imported |
| Firewall | ufw: SSH (your configured port), 80, 443 and 8883 only |

At the end it checks that the API answers `/readyz`, that nginx passes requests through, and that the web app loads, and prints the URLs.

### Options

All `deploy.sh` options, what it does step by step, and the companion scripts `nginx.sh` and `update_ssl.sh` are in [Scripts](#scriptsdeploysh). Settings from earlier runs are remembered in `/etc/cardiosensai/deploy.env`, so later runs need no options.

### First production deployment

1. **DNS:** create an `A` record for `app.cardiosens.ai` pointing at the server's IP. Let's Encrypt needs it (and port 80 reachable) to issue the certificate.
2. **Code:** on the server,
   ```bash
   git clone https://github.com/mazumdersayon-del/cardiosensAI_backend.git
   cd cardiosensAI_backend
   ```
3. **SMS and email:** sign-up sends a code by SMS, so set up MSG91 once with [`setup_msg91.sh`](#scriptssetup_msg91sh) (DLT template in India). Production also refuses to run without an SMTP relay (email codes and password reset depend on it). Optionally [Google sign-in](#google-sign-in). `cardiosens.ai` mail is on Google Workspace: create an [app password](#scriptsset_smtpsh) for the sending mailbox, then
   ```bash
   sudo bash scripts/set_smtp.sh --user no-reply@cardiosens.ai --no-test   # asks for the app password (hidden)
   ```
   (`--no-test` because the app isn't installed yet; step 4 sends the test.) Other providers: `--host smtppro.zoho.in`, `--host email-smtp.<region>.amazonaws.com`, `--host smtp-relay.brevo.com`… No SMTP account yet? Deploy with `--no-email` and add it later.

4. **Deploy:**
   ```bash
   sudo bash scripts/deploy.sh --domain app.cardiosens.ai --email ops@cardiosens.ai --admin-email ops@cardiosens.ai --test-email ops@cardiosens.ai
   ```
5. **Staff account:** open the set-password link (emailed, and printed in the output), choose a password, sign in at https://app.cardiosens.ai, and turn on two-step sign-in under **Account** (required before the admin console works).
6. **Check:** sign up a test patient with your mobile number and email, check both codes arrive, and look at the service log (`journalctl -u cardiosensai-api -f`).

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
2. Set up email: `sudo bash scripts/set_smtp.sh --user no-reply@cardiosens.ai` (asks for the Google app password, sends a test). Without them `deploy.sh` stops before changing anything ("Production needs an SMTP relay"); to go ahead without email for now, add `--no-email` to the command below.
3. Point `app.cardiosens.ai` at the server. Then `git pull && sudo bash scripts/deploy.sh --domain app.cardiosens.ai --email ops@cardiosens.ai --admin-email ops@cardiosens.ai --test-email ops@cardiosens.ai`. The domain changes from the remembered one, so pass it explicitly; `nginx.sh` gets a certificate for the new name.
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
systemctl list-timers 'cardiosensai-*'            # next backup and account purge
journalctl -u cardiosensai-purge -n 20            # what the purge erased
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

## Dependencies

| What | Where | How to change |
|---|---|---|
| Python, direct | `pyproject.toml` → `[project].dependencies` (ranges) | Edit, then regenerate the lock |
| Python, lock | `requirements.txt` (every package pinned, generated) | `uv pip compile pyproject.toml --universal --python-version 3.13 --no-header --annotation-style line -o requirements.txt` (keeps existing pins; add `--upgrade-package NAME` to bump one). Keep the 3-line header |
| Python, dev tools | `pyproject.toml` → `[project.optional-dependencies].dev` (exact pins) | Edit the pin |
| Web | `web/package.json` (exact versions) + `web/package-lock.json` | `cd web && npm install --save-exact PKG@VERSION` |

Production (`deploy.sh`, `Dockerfile`) installs only `requirements.txt`. Development installs both: `uv pip install -r requirements.txt -r pyproject.toml --extra dev -c requirements.txt` (what `run_local.sh` does).

## Before production

- **SMS (MSG91):** register the DLT entity, sender ID and OTP template, then run [`setup_msg91.sh`](#scriptssetup_msg91sh). Without it, sign-up needs an email and mobile numbers stay unconfirmed.
- **Google sign-in:** create the OAuth client ids and set `GOOGLE_CLIENT_ID` (+ app ids), see [Google sign-in](#google-sign-in).
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
  api/v1/                auth, me, verification (licence documents), links, devices, measurements, ecg (+ live), documents, sharing, notifications, reports, audit, admin (portal API)
  core/                  config (+ production guards), db, security, ids, passwords (+ common_passwords.txt), errors, storage, ratelimit, observability
  models/                user (users, organizations, professional_profiles, consents, verification_files), link, sharing, auth, device, health, report, notification, audit
  schemas/               request/response models (camelCase)
  services/              authz, accounts, erasure (scheduled deletion, erase, purge), stats (admin statistics), otp (one-time codes), google (ID-token check), ecg (leads, live state, finalize), ecg_synth, ingestion, report_pdf, audit, notifications, email, sms (MSG91), serializers
  scripts/seed.py        device models, admin by phone or email, demo data (incl. a 12-lead ECG)
  scripts/simulate_ecg.py  streams a synthetic 12-lead live ECG through the public API
  scripts/purge_deleted.py erases accounts whose 30-day deletion grace period is over (daily timer)
  scripts/test_email.py  sends one test email with the configured provider
  scripts/test_sms.py    sends one test SMS code with the configured provider
alembic/                 migrations (0001 initial, 0002 email/roles/links, 0003 12-lead/live ECG/documents, 0004 admin portal, 0005 code sign-up/sign-in + Google, 0006 profile pictures, 0007 Doctors list)
tests/                   pytest suite (conftest helpers: login, enable_mfa, make_admin, reading, upload_licence)
web/                     web app source (React 19 + Vite + TypeScript)
website/                 web app build, committed, served at /
docs/openapi.json        generated API spec
docs/device-protocol.md  CardiosensAI device protocol (MQTT): keys, messages, snapshots, firmware checklist
scripts/mosquitto/       broker ACL (production) and the local broker config
scripts/
  run_local.sh           local stack (Docker PostgreSQL + API + web app + demo data)
  deploy.sh              single-server install/update for Ubuntu 26.04 (runs nginx.sh)
  nginx.sh               nginx + Let's Encrypt (run by deploy.sh, or on its own)
  update_ssl.sh          create / check / renew the HTTPS certificate
  set_smtp.sh            outgoing email (SMTP) settings + test email
  setup_msg91.sh         MSG91 settings for SMS codes + test code
Dockerfile, docker-compose.yml   container image and a local compose stack
pyproject.toml           project metadata, direct dependencies, ruff and pytest settings
requirements.txt         generated runtime lock
CLAUDE.md                condensed context for AI coding assistants
```

## Licence

Proprietary — © CardiosensAI Private Limited. All rights reserved.
