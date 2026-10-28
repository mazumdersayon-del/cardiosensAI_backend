#!/usr/bin/env bash
# Deploy the CardiosensAI backend on a single Ubuntu 26.04 server.
#
#   git clone https://github.com/mazumdersayon-del/cardiosensAI_backend.git
#   cd cardiosensAI_backend
#   sudo bash scripts/deploy.sh --mode demo                                            # test server on the IP
#   sudo bash scripts/deploy.sh --domain app.cardiosens.ai --email ops@cardiosens.ai   # with HTTPS
#
# To update later:  git pull && sudo bash scripts/deploy.sh
# (settings from the first run are remembered in /etc/cardiosensai/deploy.env).
# To add HTTPS later, once DNS points here:  sudo bash scripts/nginx.sh --domain app.cardiosens.ai --email ops@cardiosens.ai
#
# Production sends email (address confirmation, password reset). Before the first production run, put the
# SMTP relay settings in /etc/cardiosensai/backend.env (deploy.sh makes it root:cardiosens 0640), e.g.:
#   EMAIL_PROVIDER=smtp
#   EMAIL_FROM=CardiosensAI <no-reply@cardiosens.ai>
#   SMTP_HOST=smtppro.zoho.in
#   SMTP_PORT=587
#   SMTP_TLS=starttls
#   SMTP_USERNAME=no-reply@cardiosens.ai
#   SMTP_PASSWORD=...
#
# What it sets up:
#   PostgreSQL (local, password auth)    database "cardiosens", role "cardiosens"
#   Python 3.13 virtualenv (uv)          /opt/cardiosensai/venv
#   API code                             /opt/cardiosensai/app
#   Secrets and settings                 /etc/cardiosensai/backend.env (root:cardiosens 0640)
#   ECG waveforms and PDF reports        /var/lib/cardiosensai/storage
#   systemd service                      cardiosensai-api (uvicorn on 127.0.0.1:8000)
#   nginx + Let's Encrypt (nginx.sh)     https://DOMAIN: web app (sign-in + dashboards) at /, API at /v1
#   Daily backups (systemd timer)        /var/backups/cardiosensai (kept 14 days, same disk!), plus a
#                                        pre-migrate-*.dump before every run that applies a migration
#   Daily account purge (systemd timer)  cardiosensai-purge: erases admin-deleted accounts after 30 days
#   Device broker (Mosquitto)            mqtts://DOMAIN:8883 for CardiosensAI devices (TLS, Let's Encrypt cert),
#                                        127.0.0.1:1883 for the bridge; logins written by the app to /var/lib/cardiosensai-mqtt/passwd,
#                                        copied for Mosquitto to /etc/mosquitto/cardiosens.passwd
#   Device bridge (systemd service)      cardiosensai-mqtt: broker <-> database (app/scripts/mqtt_bridge.py)
#   Firewall (ufw)                       SSH, HTTP, HTTPS, MQTT over TLS (8883) only
#
# Options (each can also be given as the environment variable in brackets):
#   --domain NAME             Public host name, e.g. app.cardiosens.ai [DOMAIN]. Empty: plain HTTP on the server IP.
#   --email ADDRESS           Let's Encrypt account e-mail; running with it accepts the Let's Encrypt terms [LETSENCRYPT_EMAIL]
#   --mode production|demo    production (default): production settings. Needs the SMTP settings above
#                             (refuses to run without them). Sign-up sends an SMS code, so also configure an
#                             MSG91 once: sudo bash scripts/setup_msg91.sh (until then sign-up needs an email).
#                             demo: one-time codes are returned in the API response (anyone can sign in as any
#                             number) and emails go to the service log. Test data only, never real patients. [MODE]
#   --admin-email ADDRESS     Create or promote this email to a staff (admin) account and email it a
#                             set-password link; the link is also printed [ADMIN_EMAIL]
#   --admin PHONE            Create or promote this E.164 number to an admin account (app sign-in) [ADMIN_PHONE]
#   --demo-data               Add the synthetic demo patient, doctor, clinic and family member (demo mode only)
#   --no-email                Production without an SMTP relay yet (EMAIL_PROVIDER=none): the site runs, but
#                             sign-up confirmation, password-reset and set-password emails are NOT sent, so only
#                             staff created with --admin-email (link printed here) can sign in. Remembered; the
#                             next run after SMTP_HOST is added to backend.env switches to SMTP by itself [NO_EMAIL=1]
#   --test-email ADDRESS      After deploying, send a test email through the configured relay [TEST_EMAIL]
#   --workers N               uvicorn worker processes (default 1; sign-in rate limits are per process) [WORKERS]
#   --no-tls                  Skip Let's Encrypt even when a domain is set
#   --no-firewall             Leave ufw alone
#   -h, --help                Show this help

set -Eeuo pipefail
trap 'echo "deploy.sh: failed at line $LINENO: $BASH_COMMAND" >&2' ERR

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"  # repository root (this file is in scripts/)
APP_USER=cardiosens
DB_NAME=cardiosens
PREFIX=/opt/cardiosensai
APP_DIR=$PREFIX/app
VENV=$PREFIX/venv
CONF_DIR=/etc/cardiosensai
ENV_FILE=$CONF_DIR/backend.env
STATE_FILE=$CONF_DIR/deploy.env
DATA_DIR=/var/lib/cardiosensai
BACKUP_DIR=/var/backups/cardiosensai
MQTT_DIR=/var/lib/cardiosensai-mqtt
MQTT_SERVICE=cardiosensai-mqtt
SERVICE=cardiosensai-api
API_PORT=8000
PYTHON_VERSION=3.13

log() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33mWARNING: %s\033[0m\n' "$*" >&2; }
die() { printf '\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }
usage() { sed -n '2,/^$/p' "$0" | sed 's/^# \{0,1\}//'; exit 0; }

# KEY=value files (no quoting; values never contain newlines).
kv_get() { [[ -f $2 ]] && sed -n "s/^$1=//p" "$2" | tail -n1 || true; }
kv_set() {
  local key=$1 value=$2 file=$3 tmp
  tmp=$(mktemp)
  [[ -f $file ]] && grep -v "^$key=" "$file" >"$tmp" || true
  printf '%s=%s\n' "$key" "$value" >>"$tmp"
  cat "$tmp" >"$file" # keep the file's owner and mode
  rm -f "$tmp"
}
random_secret() { head -c 48 /dev/urandom | base64 -w0 | tr -d '/+='; }

# ---------------------------------------------------------------- options
DOMAIN=${DOMAIN-__unset__}
LETSENCRYPT_EMAIL=${LETSENCRYPT_EMAIL-__unset__}
MODE=${MODE:-}
ADMIN_PHONE=${ADMIN_PHONE:-}
ADMIN_EMAIL=${ADMIN_EMAIL:-}
WORKERS=${WORKERS:-}
DEMO_DATA=0
NO_EMAIL=${NO_EMAIL:-}
TEST_EMAIL=${TEST_EMAIL:-}
TLS=1
FIREWALL=1

while [[ $# -gt 0 ]]; do
  case $1 in
    --domain) DOMAIN=${2?}; shift 2 ;;
    --email) LETSENCRYPT_EMAIL=${2?}; shift 2 ;;
    --mode) MODE=${2?}; shift 2 ;;
    --admin) ADMIN_PHONE=${2?}; shift 2 ;;
    --admin-email) ADMIN_EMAIL=${2?}; shift 2 ;;
    --demo-data) DEMO_DATA=1; shift ;;
    --workers) WORKERS=${2?}; shift 2 ;;
    --no-tls) TLS=0; shift ;;
    --no-firewall) FIREWALL=0; shift ;;
    --no-email) NO_EMAIL=1; shift ;;
    --test-email) TEST_EMAIL=${2?}; shift 2 ;;
    -h | --help) usage ;;
    *) die "Unknown option: $1 (see --help)" ;;
  esac
done

[[ $EUID -eq 0 ]] || die "Run as root: sudo bash $0 ..."
[[ -f $SRC_DIR/app/main.py && -f $SRC_DIR/requirements.txt ]] || die "scripts/deploy.sh must stay inside the backend repository checkout"

# Remembered settings from earlier runs fill in anything not given now.
[[ $DOMAIN == __unset__ ]] && DOMAIN=$(kv_get DOMAIN "$STATE_FILE")
[[ $LETSENCRYPT_EMAIL == __unset__ ]] && LETSENCRYPT_EMAIL=$(kv_get LETSENCRYPT_EMAIL "$STATE_FILE")
MODE=${MODE:-$(kv_get MODE "$STATE_FILE")}
MODE=${MODE:-production}
WORKERS=${WORKERS:-$(kv_get WORKERS "$STATE_FILE")}
WORKERS=${WORKERS:-1}

[[ $MODE == production || $MODE == demo ]] || die "--mode must be production or demo"
[[ $WORKERS =~ ^[1-9][0-9]*$ ]] || die "--workers must be a positive number"
[[ -z $DOMAIN || $DOMAIN =~ ^[A-Za-z0-9.-]+$ ]] || die "Invalid --domain: $DOMAIN"
[[ -z $ADMIN_PHONE || $ADMIN_PHONE =~ ^\+[1-9][0-9]{7,14}$ ]] || die "--admin must be an E.164 number such as +919812345678"
[[ -z $ADMIN_EMAIL || $ADMIN_EMAIL =~ ^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$ ]] || die "--admin-email must be an email address"
[[ $MODE == demo || $DEMO_DATA -eq 0 ]] || die "--demo-data is only allowed with --mode demo"
[[ -z $TEST_EMAIL || $TEST_EMAIL =~ ^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$ ]] || die "--test-email must be an email address"
NO_EMAIL=${NO_EMAIL:-$(kv_get NO_EMAIL "$STATE_FILE")}
if [[ $MODE == production ]]; then
  if [[ -n $(kv_get SMTP_HOST "$ENV_FILE") ]]; then
    EMAIL_MODE=smtp
    NO_EMAIL=
  elif [[ $NO_EMAIL == 1 ]]; then
    EMAIL_MODE=none
    warn "Deploying WITHOUT email (--no-email): sign-up confirmation and password-reset emails will not be sent."
  else
    die "Production needs an SMTP relay: sign-up confirmation and password-reset links are sent by email.

Nothing has been changed; the running site keeps its current version.

Either set up email (asks for the password hidden, writes $ENV_FILE, sends a test), then run this again:

  sudo bash $(dirname "$0")/set_smtp.sh --user no-reply@cardiosens.ai      # Google Workspace: use an app password
  sudo bash $0

(other providers: add --host, e.g. --host smtppro.zoho.in; see --help)

or deploy now without email and add SMTP later (only staff created with --admin-email can sign in meanwhile):

  sudo bash $0 --no-email --admin-email you@example.com"
  fi
else
  EMAIL_MODE=console
fi
if [[ -n $DOMAIN && $TLS -eq 1 && -z $LETSENCRYPT_EMAIL && ! -f /etc/letsencrypt/live/$DOMAIN/fullchain.pem ]]; then
  die "HTTPS needs --email for Let's Encrypt (or pass --no-tls)"
fi

# shellcheck source=/dev/null
. /etc/os-release
[[ ${ID:-} == ubuntu ]] || warn "This script targets Ubuntu 26.04; found ${PRETTY_NAME:-unknown}"
[[ ${VERSION_ID:-} == 26.04 ]] || warn "Tested for Ubuntu 26.04; found ${VERSION_ID:-unknown}"

if [[ $MODE == demo ]]; then
  warn "DEMO MODE: sign-in codes are returned by the API, so anyone can sign in as any phone number."
  warn "Use this server for test data only. Never store real patient data on it."
fi

# ---------------------------------------------------------------- packages
log "Installing system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q --no-install-recommends \
  ca-certificates curl gzip openssl rsync tar \
  postgresql postgresql-client nginx certbot ufw python3 python3-venv mosquitto

# ---------------------------------------------------------------- user and folders
log "Creating service account and folders"
id -u $APP_USER >/dev/null 2>&1 || useradd --system --home-dir $PREFIX --shell /usr/sbin/nologin $APP_USER
install -d -m 0755 $PREFIX
install -d -m 0750 -o $APP_USER -g $APP_USER $DATA_DIR $DATA_DIR/storage
install -d -m 0750 -o root -g $APP_USER $CONF_DIR
install -d -m 0700 $BACKUP_DIR
# Broker logins: written here by the app (cardiosens); root copies them to $MQTT_PASSWD for Mosquitto.
install -d -m 2750 -o $APP_USER -g mosquitto $MQTT_DIR

# ---------------------------------------------------------------- code
log "Copying application code to $APP_DIR"
rsync -a --delete --chown=root:root --chmod=D755,F644 \
  --exclude '.git/' --exclude '.venv/' --exclude 'venv/' --exclude '.env' --exclude 'var/' --exclude 'web/node_modules/' \
  --exclude 'tests/' --exclude '__pycache__/' --exclude '.pytest_cache/' --exclude '.ruff_cache/' --exclude '*.db' \
  "$SRC_DIR"/ $APP_DIR/
chmod 755 $APP_DIR/scripts/*.sh

# ---------------------------------------------------------------- python
log "Preparing Python $PYTHON_VERSION virtualenv"
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh ||
    warn "Could not install uv; falling back to the system Python"
fi
export UV_PYTHON_INSTALL_DIR=$PREFIX/python UV_CACHE_DIR=/var/cache/cardiosensai-uv
if command -v uv >/dev/null 2>&1 && uv python install $PYTHON_VERSION; then
  if [[ ! -x $VENV/bin/python ]] || ! $VENV/bin/python -c "import sys; assert sys.version_info[:2] == (${PYTHON_VERSION/./, })" 2>/dev/null; then
    rm -rf $VENV
    uv venv --python $PYTHON_VERSION $VENV
  fi
  uv pip install --python $VENV/bin/python --quiet -r $APP_DIR/requirements.txt
else
  warn "Using system $(python3 --version); the project is tested on Python $PYTHON_VERSION"
  [[ -x $VENV/bin/python ]] || python3 -m venv $VENV
  $VENV/bin/pip install --quiet --upgrade pip
  $VENV/bin/pip install --quiet -r $APP_DIR/requirements.txt
fi

# ---------------------------------------------------------------- postgres
log "Setting up PostgreSQL"
systemctl enable --now postgresql
psql_admin() { runuser -u postgres -- psql -v ON_ERROR_STOP=1 -qtAX "$@"; }
for _ in $(seq 30); do runuser -u postgres -- pg_isready -q && break; sleep 1; done

touch $ENV_FILE
chown root:$APP_USER $ENV_FILE
chmod 0640 $ENV_FILE

DATABASE_URL=$(kv_get DATABASE_URL $ENV_FILE)
if [[ -z $(psql_admin -c "SELECT 1 FROM pg_roles WHERE rolname = '$APP_USER'") ]]; then
  DB_PASSWORD=$(openssl rand -hex 24)
  psql_admin -c "CREATE ROLE $APP_USER LOGIN PASSWORD '$DB_PASSWORD'"
  DATABASE_URL="postgresql+psycopg://$APP_USER:$DB_PASSWORD@127.0.0.1:5432/$DB_NAME"
elif [[ -z $DATABASE_URL ]]; then
  # Role exists but its password was lost with the settings file: issue a new one.
  DB_PASSWORD=$(openssl rand -hex 24)
  psql_admin -c "ALTER ROLE $APP_USER LOGIN PASSWORD '$DB_PASSWORD'"
  DATABASE_URL="postgresql+psycopg://$APP_USER:$DB_PASSWORD@127.0.0.1:5432/$DB_NAME"
fi
if [[ -z $(psql_admin -c "SELECT 1 FROM pg_database WHERE datname = '$DB_NAME'") ]]; then
  runuser -u postgres -- createdb --owner=$APP_USER --encoding=UTF8 --template=template0 $DB_NAME
fi

# ---------------------------------------------------------------- settings
log "Writing $ENV_FILE"
CERT_DIR=/etc/letsencrypt/live/$DOMAIN
if [[ -z $DOMAIN ]]; then
  PUBLIC_IP=$(curl -fsS -4 --max-time 5 https://api.ipify.org 2>/dev/null || hostname -I | awk '{print $1}')
  BASE_URL="http://$PUBLIC_IP"
elif [[ $TLS -eq 1 || -f $CERT_DIR/fullchain.pem ]]; then
  BASE_URL="https://$DOMAIN"
else
  BASE_URL="http://$DOMAIN"
fi

[[ -n $(kv_get JWT_SECRET $ENV_FILE) ]] || kv_set JWT_SECRET "$(random_secret)" $ENV_FILE
[[ -n $(kv_get SIGNING_KEY $ENV_FILE) ]] || kv_set SIGNING_KEY "$(random_secret)" $ENV_FILE
# Devices: DEVICE_KEYS_SECRET encrypts every device's signing key (lose it = re-import every device; it is only in
# this file and its backups); MQTT_PASSWORD is the bridge's own broker login.
[[ -n $(kv_get DEVICE_KEYS_SECRET $ENV_FILE) ]] || kv_set DEVICE_KEYS_SECRET "$(random_secret)" $ENV_FILE
[[ -n $(kv_get MQTT_PASSWORD $ENV_FILE) ]] || kv_set MQTT_PASSWORD "$(random_secret)" $ENV_FILE
kv_set MQTT_HOST 127.0.0.1 $ENV_FILE
kv_set MQTT_PORT 1883 $ENV_FILE
kv_set MQTT_PASSWD_FILE $MQTT_DIR/passwd $ENV_FILE
kv_set DATABASE_URL "$DATABASE_URL" $ENV_FILE
kv_set API_BASE_URL "$BASE_URL" $ENV_FILE
kv_set WEB_BASE_URL "$BASE_URL" $ENV_FILE  # links in emails (the web app is served from the same host)
kv_set STORAGE_BACKEND local $ENV_FILE
kv_set STORAGE_LOCAL_PATH $DATA_DIR/storage $ENV_FILE
[[ -n $(kv_get CORS_ORIGINS $ENV_FILE) ]] || kv_set CORS_ORIGINS '[]' $ENV_FILE
if [[ $MODE == production ]]; then
  kv_set ENV production $ENV_FILE
  kv_set DEV_EXPOSE_OTP false $ENV_FILE
  # Keep MSG91 set up with setup_msg91.sh; otherwise none (sign-up and SMS sign-in then answer 503).
  [[ $(kv_get SMS_PROVIDER $ENV_FILE) == msg91 ]] || kv_set SMS_PROVIDER none $ENV_FILE
  kv_set EMAIL_PROVIDER "$EMAIL_MODE" $ENV_FILE
else
  kv_set ENV development $ENV_FILE
  kv_set DEV_EXPOSE_OTP true $ENV_FILE
  kv_set SMS_PROVIDER console $ENV_FILE
  kv_set EMAIL_PROVIDER console $ENV_FILE  # emails (and their links) go to the service log
fi

install -m 0600 /dev/null "$STATE_FILE.new"
kv_set DOMAIN "$DOMAIN" "$STATE_FILE.new"
kv_set LETSENCRYPT_EMAIL "$LETSENCRYPT_EMAIL" "$STATE_FILE.new"
kv_set MODE "$MODE" "$STATE_FILE.new"
kv_set WORKERS "$WORKERS" "$STATE_FILE.new"
kv_set NO_EMAIL "$NO_EMAIL" "$STATE_FILE.new"
mv "$STATE_FILE.new" "$STATE_FILE"

# Run a one-off command as the service account with exactly the service's environment.
run_app() {
  systemd-run --quiet --wait --pipe --collect --service-type=exec \
    -p User=$APP_USER -p Group=$APP_USER -p WorkingDirectory=$APP_DIR \
    -p EnvironmentFile=$ENV_FILE -p Environment=PYTHONDONTWRITEBYTECODE=1 \
    "$@"
}

# ---------------------------------------------------------------- database schema
log "Applying database migrations"
if [[ -n $(psql_admin -d $DB_NAME -c "SELECT 1 FROM information_schema.tables WHERE table_name = 'alembic_version'") ]] &&
  [[ $(run_app $VENV/bin/alembic current 2>/dev/null | awk '{print $1}') != $(run_app $VENV/bin/alembic heads 2>/dev/null | awk '{print $1}') ]]; then
  PRE=$BACKUP_DIR/pre-migrate-$(date +%Y%m%d-%H%M%S).dump
  runuser -u postgres -- pg_dump --format=custom $DB_NAME >"$PRE"
  chmod 0640 "$PRE"
  log "Database saved to $PRE before migrating (restore: pg_restore --clean -d $DB_NAME $PRE)"
fi
run_app $VENV/bin/alembic upgrade head
SEED_ARGS=()
[[ -n $ADMIN_PHONE ]] && SEED_ARGS+=(--admin "$ADMIN_PHONE")
[[ -n $ADMIN_EMAIL ]] && SEED_ARGS+=(--admin-email "$ADMIN_EMAIL")
[[ $DEMO_DATA -eq 1 ]] && SEED_ARGS+=(--demo)
run_app $VENV/bin/python -m app.scripts.seed "${SEED_ARGS[@]}"

# ---------------------------------------------------------------- service
log "Installing systemd service $SERVICE"
cat >/etc/systemd/system/$SERVICE.service <<EOF
[Unit]
Description=CardiosensAI API
After=network-online.target postgresql.service
Wants=network-online.target postgresql.service

[Service]
Type=exec
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$ENV_FILE
Environment=PYTHONDONTWRITEBYTECODE=1
# Uvicorn's access log prints full URLs (which can contain signed download tokens), so it's off;
# the app writes its own structured request log with ids and tokens masked.
ExecStart=$VENV/bin/uvicorn app.main:app --host 127.0.0.1 --port $API_PORT --workers $WORKERS --proxy-headers --forwarded-allow-ips 127.0.0.1 --no-access-log
Restart=on-failure
RestartSec=3
TimeoutStopSec=20
NoNewPrivileges=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectSystem=strict
ProtectHome=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
LockPersonality=yes
ReadWritePaths=$DATA_DIR $MQTT_DIR
UMask=0027

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable $SERVICE >/dev/null
systemctl restart $SERVICE

# ---------------------------------------------------------------- nginx
log "Configuring nginx (nginx.sh)"
NGINX_ARGS=(--domain "$DOMAIN" --port "$API_PORT")
[[ -n $LETSENCRYPT_EMAIL ]] && NGINX_ARGS+=(--email "$LETSENCRYPT_EMAIL")
[[ $TLS -eq 0 ]] && NGINX_ARGS+=(--no-tls)
bash "$SRC_DIR/scripts/nginx.sh" "${NGINX_ARGS[@]}"

# ---------------------------------------------------------------- backups
log "Scheduling daily backups to $BACKUP_DIR"
cat >/usr/local/sbin/cardiosensai-backup <<EOF
#!/usr/bin/env bash
# Database dump + ECG/report files. Copy these off the server too: a backup on the same disk
# doesn't survive losing the server.
set -euo pipefail
umask 077
stamp=\$(date -u +%Y%m%dT%H%M%SZ)
runuser -u postgres -- pg_dump --format=custom $DB_NAME >"$BACKUP_DIR/db-\$stamp.dump"
tar -C $DATA_DIR -czf "$BACKUP_DIR/storage-\$stamp.tar.gz" storage
find $BACKUP_DIR -type f -mtime +14 -delete
EOF
chmod 750 /usr/local/sbin/cardiosensai-backup
cat >/etc/systemd/system/cardiosensai-backup.service <<'EOF'
[Unit]
Description=CardiosensAI database and file backup
After=postgresql.service

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/cardiosensai-backup
EOF
cat >/etc/systemd/system/cardiosensai-backup.timer <<'EOF'
[Unit]
Description=Daily CardiosensAI backup

[Timer]
OnCalendar=*-*-* 02:30:00
RandomizedDelaySec=20m
Persistent=true

[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now cardiosensai-backup.timer >/dev/null

# ---------------------------------------------------------------- account purge
log "Scheduling the daily purge of deleted accounts"
cat >/etc/systemd/system/cardiosensai-purge.service <<EOF
[Unit]
Description=CardiosensAI: erase accounts whose 30-day deletion grace period is over
After=postgresql.service

[Service]
Type=oneshot
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$ENV_FILE
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=$VENV/bin/python -m app.scripts.purge_deleted
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=$DATA_DIR
UMask=0027
EOF
cat >/etc/systemd/system/cardiosensai-purge.timer <<'EOF'
[Unit]
Description=Daily CardiosensAI deleted-account purge

[Timer]
OnCalendar=*-*-* 03:15:00
RandomizedDelaySec=10m
Persistent=true

[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now cardiosensai-purge.timer >/dev/null

# ---------------------------------------------------------------- device broker + bridge
log "Configuring the device broker (Mosquitto) and bridge"
# Mosquitto 2.1 wants its password and ACL files owned by mosquitto and not world-readable.
install -m 0600 -o mosquitto -g mosquitto "$SRC_DIR/scripts/mosquitto/cardiosens.acl" /etc/mosquitto/cardiosens.acl
MQTT_PASSWD=/etc/mosquitto/cardiosens.passwd
MQTT_TLS_CONF=""
if [[ -n $DOMAIN && $TLS -eq 1 && -f /etc/letsencrypt/live/$DOMAIN/fullchain.pem ]]; then
  # Mosquitto can't read /etc/letsencrypt: keep a copy it owns, refreshed by certbot after every renewal.
  install -d -m 0755 /etc/letsencrypt/renewal-hooks/deploy
  cat >/etc/letsencrypt/renewal-hooks/deploy/cardiosensai-mosquitto.sh <<HOOK
#!/bin/sh
install -d -m 0750 -o mosquitto -g mosquitto /etc/mosquitto/certs/cardiosens
install -m 0640 -o mosquitto -g mosquitto /etc/letsencrypt/live/$DOMAIN/fullchain.pem /etc/mosquitto/certs/cardiosens/fullchain.pem
install -m 0640 -o mosquitto -g mosquitto /etc/letsencrypt/live/$DOMAIN/privkey.pem /etc/mosquitto/certs/cardiosens/privkey.pem
systemctl reload mosquitto 2>/dev/null || true
HOOK
  chmod 0755 /etc/letsencrypt/renewal-hooks/deploy/cardiosensai-mosquitto.sh
  /etc/letsencrypt/renewal-hooks/deploy/cardiosensai-mosquitto.sh
  MQTT_TLS_CONF="
# Devices: MQTT over TLS only (health data never crosses the internet unencrypted)
listener 8883
certfile /etc/mosquitto/certs/cardiosens/fullchain.pem
keyfile /etc/mosquitto/certs/cardiosens/privkey.pem
tls_version tlsv1.2"
else
  warn "No HTTPS certificate: the device broker listens on 127.0.0.1 only, so devices can't connect until --domain is set up"
fi
cat >/etc/mosquitto/conf.d/cardiosensai.conf <<CONF
# Written by deploy.sh. Device logins are managed by the app (Admin -> Devices); see docs/device-protocol.md.
# The bridge (cardiosensai-mqtt), on the loopback interface only
listener 1883 127.0.0.1
$MQTT_TLS_CONF

allow_anonymous false
password_file $MQTT_PASSWD
acl_file /etc/mosquitto/cardiosens.acl
use_username_as_clientid true
max_packet_size 600000
max_inflight_messages 20
CONF
# The broker re-reads its logins whenever the app rewrites them (device imported, blocked, unblocked).
cat >/etc/systemd/system/cardiosensai-mqtt-logins.path <<UNIT
[Unit]
Description=Reload Mosquitto when CardiosensAI device logins change

[Path]
PathChanged=$MQTT_DIR/passwd

[Install]
WantedBy=multi-user.target
UNIT
cat >/etc/systemd/system/cardiosensai-mqtt-logins.service <<UNIT
[Unit]
Description=Copy CardiosensAI device logins to Mosquitto and reload it

[Service]
Type=oneshot
ExecStart=/usr/bin/install -m 0600 -o mosquitto -g mosquitto $MQTT_DIR/passwd $MQTT_PASSWD
ExecStart=/bin/systemctl try-reload-or-restart mosquitto
UNIT
# The first password file, before Mosquitto starts (the bridge rewrites it on every start)
run_app $VENV/bin/python -c "from app.core.db import SessionLocal; from app.services.device_hub import sync_broker_logins; db = SessionLocal(); sync_broker_logins(db); db.close()" \
  || warn "Couldn't write the broker logins yet; the bridge writes them when it starts"
[[ -f $MQTT_DIR/passwd ]] || install -m 0640 -o $APP_USER -g $APP_USER /dev/null $MQTT_DIR/passwd
install -m 0600 -o mosquitto -g mosquitto $MQTT_DIR/passwd $MQTT_PASSWD
cat >/etc/systemd/system/$MQTT_SERVICE.service <<UNIT
[Unit]
Description=CardiosensAI device bridge (MQTT <-> database)
After=network-online.target postgresql.service mosquitto.service
Wants=network-online.target postgresql.service mosquitto.service

[Service]
Type=exec
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$ENV_FILE
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=$VENV/bin/python -m app.scripts.mqtt_bridge
Restart=always
RestartSec=3
TimeoutStopSec=20
NoNewPrivileges=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectSystem=strict
ProtectHome=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
LockPersonality=yes
ReadWritePaths=$DATA_DIR $MQTT_DIR
UMask=0027

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now cardiosensai-mqtt-logins.path >/dev/null
systemctl enable mosquitto >/dev/null
systemctl reset-failed mosquitto 2>/dev/null || true
if ! systemctl restart mosquitto; then
  # systemd's journal only says "failed": Mosquitto writes the reason to its own log
  tail -n 15 /var/log/mosquitto/mosquitto.log >&2 || true
  die "Mosquitto didn't start (its log is above; config: /etc/mosquitto/conf.d/cardiosensai.conf)"
fi
systemctl enable $MQTT_SERVICE >/dev/null
systemctl restart $MQTT_SERVICE

# ---------------------------------------------------------------- firewall
if [[ $FIREWALL -eq 1 ]]; then
  log "Configuring firewall"
  SSH_PORTS=$(sshd -T 2>/dev/null | awk '$1 == "port" {print $2}' || true)
  for port in ${SSH_PORTS:-22}; do ufw allow "$port/tcp" comment ssh >/dev/null; done
  ufw allow 80/tcp comment http >/dev/null
  ufw allow 443/tcp comment https >/dev/null
  ufw allow 8883/tcp comment mqtts-devices >/dev/null
  ufw --force enable >/dev/null
fi

# ---------------------------------------------------------------- checks
log "Checking the deployment"
ok=0
for _ in $(seq 30); do
  if curl -fsS --max-time 3 http://127.0.0.1:$API_PORT/readyz >/dev/null 2>&1; then ok=1; break; fi
  sleep 1
done
if [[ $ok -ne 1 ]]; then
  journalctl -u $SERVICE -n 40 --no-pager >&2 || true
  die "The API did not become ready. Logs above; see also: journalctl -u $SERVICE"
fi
FINAL_URL=$(kv_get API_BASE_URL $ENV_FILE)
if [[ $FINAL_URL == https://* ]]; then
  VIA_NGINX=(--resolve "$DOMAIN:443:127.0.0.1")
  NGINX_URL="https://$DOMAIN"
else
  VIA_NGINX=(-H "Host: ${DOMAIN:-localhost}")
  NGINX_URL="http://127.0.0.1"
fi
curl -fsS --max-time 5 "${VIA_NGINX[@]}" "$NGINX_URL/healthz" >/dev/null || die "nginx is not passing requests to the API"
HOME_PAGE=$(curl -fsS --max-time 5 "${VIA_NGINX[@]}" "$NGINX_URL/" || true)
[[ $HOME_PAGE == *CardiosensAI* ]] || warn "The home page did not load through nginx"

cat <<EOF

CardiosensAI backend is running ($MODE mode).
  Home page:   $FINAL_URL/
  API:         $FINAL_URL/v1   (health: $FINAL_URL/healthz)
  App setting: EXPO_PUBLIC_API_BASE_URL=$FINAL_URL
  Settings:    $ENV_FILE
  Logs:        journalctl -u $SERVICE -f
  Backups:     $BACKUP_DIR (daily; copy them off this server)
EOF
if [[ -n $TEST_EMAIL ]]; then
  log "Sending a test email to $TEST_EMAIL"
  if run_app $VENV/bin/python -m app.scripts.test_email "$TEST_EMAIL"; then
    echo "Sent. Check the inbox (and the spam folder) of $TEST_EMAIL."
  else
    warn "The test email failed (details above). Check SMTP_* in $ENV_FILE, then: sudo systemctl restart $SERVICE"
  fi
fi
if [[ $MODE == demo ]]; then
  warn "Demo mode: sign-in codes are visible to anyone, emails go to the log (journalctl -u $SERVICE). Test data only."
else
  [[ $EMAIL_MODE == none ]] && warn "No email relay: email codes and password-reset links can't be sent. Add SMTP_* to $ENV_FILE and run this script again."
  [[ $(kv_get SMS_PROVIDER $ENV_FILE) == msg91 ]] ||
    warn "No SMS provider yet: sign-up only works with an email address (mobile numbers stay unconfirmed) and SMS sign-in is off. Run: sudo bash scripts/setup_msg91.sh --template ID --test +91XXXXXXXXXX"
  [[ -n $(kv_get GOOGLE_CLIENT_ID $ENV_FILE) ]] ||
    echo "Optional: \"Continue with Google\" appears once GOOGLE_CLIENT_ID is set in $ENV_FILE (see README → Google sign-in)."
fi
exit 0
