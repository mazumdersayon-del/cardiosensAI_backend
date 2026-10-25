#!/usr/bin/env bash
# Deploy the CardiosensAI backend on a single Ubuntu 26.04 server.
#
#   git clone https://github.com/mazumdersayon-del/cardiosensAI_backend.git
#   cd cardiosensAI_backend
#   sudo ./deploy.sh --mode demo                                            # test server on the IP
#   sudo ./deploy.sh --domain api.cardiosens.ai --email ops@cardiosens.ai   # with HTTPS
#
# To update later:  git pull && sudo ./deploy.sh
# (settings from the first run are remembered in /etc/cardiosensai/deploy.env).
# To add HTTPS later, once DNS points here:  sudo ./nginx.sh --domain api.cardiosens.ai --email ops@cardiosens.ai
#
# What it sets up:
#   PostgreSQL (local, password auth)    database "cardiosens", role "cardiosens"
#   Python 3.13 virtualenv (uv)          /opt/cardiosensai/venv
#   API code                             /opt/cardiosensai/app
#   Secrets and settings                 /etc/cardiosensai/backend.env (root:cardiosens 0640)
#   ECG waveforms and PDF reports        /var/lib/cardiosensai/storage
#   systemd service                      cardiosensai-api (uvicorn on 127.0.0.1:8000)
#   nginx + Let's Encrypt (nginx.sh)     https://DOMAIN: cardiosens.ai home page at /, API at /v1
#   Daily backups (systemd timer)        /var/backups/cardiosensai (kept 14 days, same disk!)
#   Firewall (ufw)                       SSH, HTTP, HTTPS only
#
# Options (each can also be given as the environment variable in brackets):
#   --domain NAME             Public host name, e.g. api.cardiosens.ai [DOMAIN]. Empty: plain HTTP on the server IP.
#   --email ADDRESS           Let's Encrypt account e-mail; running with it accepts the Let's Encrypt terms [LETSENCRYPT_EMAIL]
#   --mode production|demo    production (default): production settings. No SMS provider is built in, so
#                             sign-in codes can't be sent until one is added in app/services/sms.py.
#                             demo: sign-in codes are returned in the API response, so anyone can sign in
#                             as any number. Test data only, never real patients. [MODE]
#   --admin PHONE            Create or promote this E.164 number to an admin account [ADMIN_PHONE]
#   --demo-data               Add the synthetic demo patient, doctor and family member (demo mode only)
#   --workers N               uvicorn worker processes (default 1; sign-in rate limits are per process) [WORKERS]
#   --no-tls                  Skip Let's Encrypt even when a domain is set
#   --no-firewall             Leave ufw alone
#   -h, --help                Show this help

set -Eeuo pipefail
trap 'echo "deploy.sh: failed at line $LINENO: $BASH_COMMAND" >&2' ERR

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
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
WORKERS=${WORKERS:-}
DEMO_DATA=0
TLS=1
FIREWALL=1

while [[ $# -gt 0 ]]; do
  case $1 in
    --domain) DOMAIN=${2?}; shift 2 ;;
    --email) LETSENCRYPT_EMAIL=${2?}; shift 2 ;;
    --mode) MODE=${2?}; shift 2 ;;
    --admin) ADMIN_PHONE=${2?}; shift 2 ;;
    --demo-data) DEMO_DATA=1; shift ;;
    --workers) WORKERS=${2?}; shift 2 ;;
    --no-tls) TLS=0; shift ;;
    --no-firewall) FIREWALL=0; shift ;;
    -h | --help) usage ;;
    *) die "Unknown option: $1 (see --help)" ;;
  esac
done

[[ $EUID -eq 0 ]] || die "Run as root: sudo $0 ..."
[[ -f $SRC_DIR/app/main.py && -f $SRC_DIR/requirements.txt ]] || die "Run deploy.sh from the backend repository checkout"

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
[[ $MODE == demo || $DEMO_DATA -eq 0 ]] || die "--demo-data is only allowed with --mode demo"
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
else
  warn "No SMS provider is built in: this server can't send sign-in codes, so nobody can sign in"
  warn "until a provider is added in app/services/sms.py. Use --mode demo for a test server."
fi

# ---------------------------------------------------------------- packages
log "Installing system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q --no-install-recommends \
  ca-certificates curl gzip openssl rsync tar \
  postgresql postgresql-client nginx certbot ufw python3 python3-venv

# ---------------------------------------------------------------- user and folders
log "Creating service account and folders"
id -u $APP_USER >/dev/null 2>&1 || useradd --system --home-dir $PREFIX --shell /usr/sbin/nologin $APP_USER
install -d -m 0755 $PREFIX
install -d -m 0750 -o $APP_USER -g $APP_USER $DATA_DIR $DATA_DIR/storage
install -d -m 0750 -o root -g $APP_USER $CONF_DIR
install -d -m 0700 $BACKUP_DIR

# ---------------------------------------------------------------- code
log "Copying application code to $APP_DIR"
rsync -a --delete --chown=root:root --chmod=D755,F644 \
  --exclude '.git/' --exclude '.venv/' --exclude 'venv/' --exclude '.env' --exclude 'var/' \
  --exclude 'tests/' --exclude '__pycache__/' --exclude '.pytest_cache/' --exclude '.ruff_cache/' --exclude '*.db' \
  "$SRC_DIR"/ $APP_DIR/
chmod 755 $APP_DIR/deploy.sh $APP_DIR/nginx.sh

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
kv_set DATABASE_URL "$DATABASE_URL" $ENV_FILE
kv_set API_BASE_URL "$BASE_URL" $ENV_FILE
kv_set STORAGE_BACKEND local $ENV_FILE
kv_set STORAGE_LOCAL_PATH $DATA_DIR/storage $ENV_FILE
[[ -n $(kv_get CORS_ORIGINS $ENV_FILE) ]] || kv_set CORS_ORIGINS '[]' $ENV_FILE
if [[ $MODE == production ]]; then
  kv_set ENV production $ENV_FILE
  kv_set DEV_EXPOSE_OTP false $ENV_FILE
  kv_set SMS_PROVIDER none $ENV_FILE
else
  kv_set ENV development $ENV_FILE
  kv_set DEV_EXPOSE_OTP true $ENV_FILE
  kv_set SMS_PROVIDER console $ENV_FILE
fi

install -m 0600 /dev/null "$STATE_FILE.new"
kv_set DOMAIN "$DOMAIN" "$STATE_FILE.new"
kv_set LETSENCRYPT_EMAIL "$LETSENCRYPT_EMAIL" "$STATE_FILE.new"
kv_set MODE "$MODE" "$STATE_FILE.new"
kv_set WORKERS "$WORKERS" "$STATE_FILE.new"
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
run_app $VENV/bin/alembic upgrade head
SEED_ARGS=()
[[ -n $ADMIN_PHONE ]] && SEED_ARGS+=(--admin "$ADMIN_PHONE")
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
ReadWritePaths=$DATA_DIR
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
bash "$SRC_DIR/nginx.sh" "${NGINX_ARGS[@]}"

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

# ---------------------------------------------------------------- firewall
if [[ $FIREWALL -eq 1 ]]; then
  log "Configuring firewall"
  SSH_PORTS=$(sshd -T 2>/dev/null | awk '$1 == "port" {print $2}' || true)
  for port in ${SSH_PORTS:-22}; do ufw allow "$port/tcp" comment ssh >/dev/null; done
  ufw allow 80/tcp comment http >/dev/null
  ufw allow 443/tcp comment https >/dev/null
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
if [[ $MODE == demo ]]; then
  warn "Demo mode: sign-in codes are visible to anyone. Test data only."
else
  warn "Sign-in is unavailable until an SMS provider is added (app/services/sms.py)."
fi
exit 0
