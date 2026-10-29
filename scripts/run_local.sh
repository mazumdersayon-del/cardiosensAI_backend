#!/usr/bin/env bash
# Run CardiosensAI locally: PostgreSQL (Docker) + API + web app on http://localhost:8000
#
#   bash scripts/run_local.sh                  API + built web app on http://localhost:8000 (rebuilds web/ if it changed)
#   bash scripts/run_local.sh --dev            also start Vite on http://localhost:5173 with hot reload (proxies /v1 to :8000)
#   bash scripts/run_local.sh --reset          wipe the local database first (fresh migrations + demo data)
#   bash scripts/run_local.sh --admin-email A  also create a staff account; its set-password link is printed in the log
#   bash scripts/run_local.sh --port 8001      API port (default 8000)
#   bash scripts/run_local.sh --no-demo        don't add the demo patient / doctor / clinic
#   bash scripts/run_local.sh totp             print the current authenticator code for the demo doctor and clinic
#   bash scripts/run_local.sh ecg-live [S]     stream a synthetic 12-lead live ECG for the demo patient for S seconds
#                                       (default 120; 0 = until Ctrl+C) to a running API (PORT=8000 by default)
#   bash scripts/run_local.sh device [OPTIONS] run the 12-lead device simulator (../Test_Scripts/stream_device.py)
#                                       against the local broker; demo test units CS12-TEST-0001 / -0002, secrets
#                                       in app/scripts/seed.py (DEMO_TEST_UNITS). Options: --help
#   bash scripts/run_local.sh stop             stop the local database and device broker containers (data is kept)
#   bash scripts/run_local.sh --lan            listen on this PC's network address too, so phones on the same Wi-Fi
#                                       (and other PCs) can use it: http://<this PC's IP>:8000
#   bash scripts/run_local.sh --show-codes     keep one-time codes on screen even when real email/SMS is set up
#
# Real email and SMS on this PC (like production; settings in var/local.env, git-ignored, mode 600):
#   bash scripts/run_local.sh mail --user no-reply@cardiosens.ai [--test you@example.com]
#                                       SMTP (Google Workspace app password, asked hidden); --host/--port/--tls/--from
#                                       for other providers. Sends a test email
#   bash scripts/run_local.sh sms --template ID [--test +91...]
#                                       MSG91 (auth key asked hidden; needs DLT approval). Sends a test code
#   bash scripts/run_local.sh mail-test ADDRESS | sms-test NUMBER | local-show | local-off
# With real email set up, codes are no longer shown on the page or in the log: they arrive by email (and SMS with
# MSG91). Without MSG91, SMS counts as down, exactly like production today: sign-up goes on with the email code.
#
# Devices: a Mosquitto container (cardiosens-local-mqtt, localhost:1883, logins in var/mosquitto) and the device
# bridge (app/scripts/mqtt_bridge.py) start with the API. Protocol: docs/device-protocol.md.
#
# Needs: Docker, Node 20+ (for the web app). Python 3.13 comes from uv; if uv isn't installed, the
# standalone uv binary is downloaded once into ./var/tools (git-ignored).
#
# Demo sign-in (password cardiosens-demo-2026):
#   patient@demo.cardiosens.ai   doctor@demo.cardiosens.ai   clinic@demo.cardiosens.ai
# Doctor and clinic use two-step sign-in: run `bash scripts/run_local.sh totp` for the current code.
# Emails (confirmation, password reset) are not sent; their links appear in this terminal.
# Local development only: development secrets, SMS codes returned by the API, emails in the log.

set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"  # repository root (this file is in scripts/)
cd "$ROOT"

PG_CONTAINER=cardiosens-local-pg
PG_VOLUME=cardiosens-local-pgdata
PG_PORT=${PG_PORT:-55433}
MQTT_CONTAINER=cardiosens-local-mqtt
MQTT_LOCAL_PORT=${MQTT_LOCAL_PORT:-1883}
PORT=8000
DEV=0
RESET=0
DEMO=1
ADMIN_EMAIL=""
DEMO_TOTP_SECRET=CARDIOSENSDEMOTOTPSECRETCODE2345
TOOLS="$ROOT/var/tools"
VENV="$ROOT/.venv"
LOCAL_ENV="$ROOT/var/local.env"
LAN=0
SHOW_CODES=0

log() { printf '\033[1;36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33mWARNING: %s\033[0m\n' "$*" >&2; }
die() { printf '\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- var/local.env (real email / SMS on this PC)
kv_get() { [[ -f $LOCAL_ENV ]] && sed -n "s/^$1=//p" "$LOCAL_ENV" | tail -n1 || true; }
kv_set() {
  local tmp
  mkdir -p "$ROOT/var"
  [[ -f $LOCAL_ENV ]] || install -m 0600 /dev/null "$LOCAL_ENV"
  tmp=$(mktemp)
  grep -v "^$1=" "$LOCAL_ENV" >"$tmp" || true
  printf '%s=%s\n' "$1" "$2" >>"$tmp"
  cat "$tmp" >"$LOCAL_ENV" # keeps mode 600
  rm -f "$tmp"
}
kv_del() { [[ -f $LOCAL_ENV ]] || return 0; local tmp; tmp=$(mktemp); grep -v "^$1" "$LOCAL_ENV" >"$tmp" || true; cat "$tmp" >"$LOCAL_ENV"; rm -f "$tmp"; }
load_local_env() { # KEY=value lines, values taken literally (spaces, <, quotes allowed)
  local line
  [[ -f $LOCAL_ENV ]] || return 0
  while IFS= read -r line || [[ -n $line ]]; do
    [[ $line =~ ^[A-Z][A-Z0-9_]*= ]] && export "${line?}"
  done <"$LOCAL_ENV"
}
mask() { [[ -z $1 ]] && echo "(not set)" || echo "${1:0:4}…${1: -4} (${#1} characters)"; }
is_email() { [[ $1 =~ ^[^@[:space:]\<\>]+@[^@[:space:]\<\>]+\.[^@[:space:]\<\>]+$ ]]; }
local_run() { # run an app module with the local settings (needs the venv)
  [[ -x $VENV/bin/python ]] || die "Run bash scripts/run_local.sh once first (it creates .venv)"
  (load_local_env; ENV=development PYTHONDONTWRITEBYTECODE=1 "$VENV/bin/python" -m "$@")
}

case "${1:-}" in
  mail)
    shift
    M_USER="" M_FROM="" M_HOST=smtp.gmail.com M_PORT="" M_TLS=starttls M_TEST=""
    while [[ $# -gt 0 ]]; do
      case $1 in
        --user) M_USER=${2?}; shift 2 ;;
        --from) M_FROM=${2?}; shift 2 ;;
        --host) M_HOST=${2?}; shift 2 ;;
        --port) M_PORT=${2?}; shift 2 ;;
        --tls) M_TLS=${2?}; shift 2 ;;
        --test) M_TEST=${2?}; shift 2 ;;
        *) die "Unknown mail option: $1 (see --help)" ;;
      esac
    done
    is_email "$M_USER" || die "Give the SMTP login: bash scripts/run_local.sh mail --user no-reply@cardiosens.ai"
    [[ $M_TLS =~ ^(starttls|ssl|none)$ ]] || die "--tls must be starttls, ssl or none"
    [[ -n $M_PORT ]] || { [[ $M_TLS == ssl ]] && M_PORT=465 || M_PORT=587; }
    M_FROM=${M_FROM:-$M_USER}
    [[ $M_FROM == *"<"* ]] || M_FROM="CardiosensAI <$M_FROM>"
    PASSWORD=${SMTP_PASSWORD:-}
    if [[ -z $PASSWORD ]]; then
      [[ -t 0 ]] || die "No terminal to ask for the password; set SMTP_PASSWORD in the environment instead"
      read -rsp "SMTP password for $M_USER (hidden; Google: the 16-letter app password): " PASSWORD
      echo
    fi
    [[ $M_HOST == smtp.gmail.com ]] && PASSWORD=${PASSWORD// /}
    [[ -n $PASSWORD ]] || die "Empty password"
    kv_set EMAIL_PROVIDER smtp
    kv_set EMAIL_FROM "$M_FROM"
    kv_set SMTP_HOST "$M_HOST"
    kv_set SMTP_PORT "$M_PORT"
    kv_set SMTP_TLS "$M_TLS"
    kv_set SMTP_USERNAME "$M_USER"
    kv_set SMTP_PASSWORD "$PASSWORD"
    echo "Saved in $LOCAL_ENV (password not shown)."
    log "Sending a test email to ${M_TEST:-$M_USER}"
    local_run app.scripts.test_email "${M_TEST:-$M_USER}" || die "The test email failed (reason above)"
    echo "Sent. Restart run_local.sh to use it."
    exit 0
    ;;
  sms)
    shift
    S_TEMPLATE="" S_TEST=""
    while [[ $# -gt 0 ]]; do
      case $1 in
        --template) S_TEMPLATE=${2?}; shift 2 ;;
        --test) S_TEST=${2?}; shift 2 ;;
        *) die "Unknown sms option: $1 (see --help)" ;;
      esac
    done
    S_TEMPLATE=${S_TEMPLATE:-$(kv_get MSG91_OTP_TEMPLATE_ID)}
    [[ -n $S_TEMPLATE ]] || die "Give the MSG91 OTP template id: bash scripts/run_local.sh sms --template ID"
    KEY=${MSG91_AUTH_KEY:-}
    if [[ -z $KEY ]]; then
      [[ -t 0 ]] || die "No terminal to ask for the auth key; set MSG91_AUTH_KEY in the environment instead"
      read -rsp "MSG91 auth key (hidden): " KEY
      echo
    fi
    [[ $KEY =~ ^[A-Za-z0-9]{16,64}$ ]] || die "That doesn't look like an MSG91 auth key"
    kv_set SMS_PROVIDER msg91
    kv_set MSG91_AUTH_KEY "$KEY"
    kv_set MSG91_OTP_TEMPLATE_ID "$S_TEMPLATE"
    echo "Saved in $LOCAL_ENV (auth key not shown)."
    if [[ -n $S_TEST ]]; then
      log "Sending a test code to $S_TEST"
      local_run app.scripts.test_sms "$S_TEST" || die "The test SMS failed (MSG91's reason is above)"
    fi
    exit 0
    ;;
  mail-test) local_run app.scripts.test_email "${2?Give an email address}"; exit $? ;;
  sms-test) local_run app.scripts.test_sms "${2?Give a number, e.g. +919812345678}"; exit $? ;;
  local-show)
    echo "File: $LOCAL_ENV"
    echo "EMAIL_PROVIDER=$(kv_get EMAIL_PROVIDER)  SMTP $(kv_get SMTP_USERNAME) @ $(kv_get SMTP_HOST):$(kv_get SMTP_PORT)  password $(mask "$(kv_get SMTP_PASSWORD)")"
    echo "SMS_PROVIDER=$(kv_get SMS_PROVIDER)  MSG91 template $(kv_get MSG91_OTP_TEMPLATE_ID)  key $(mask "$(kv_get MSG91_AUTH_KEY)")"
    exit 0
    ;;
  local-off) rm -f "$LOCAL_ENV"; echo "Removed $LOCAL_ENV: back to codes on screen and emails in the log."; exit 0 ;;
esac

case "${1:-}" in
  stop)
    docker stop "$PG_CONTAINER" >/dev/null 2>&1 && echo "Stopped $PG_CONTAINER (data kept in volume $PG_VOLUME)" || echo "Not running"
    docker rm -f "$MQTT_CONTAINER" >/dev/null 2>&1 && echo "Stopped $MQTT_CONTAINER (logins and retained messages kept in var/mosquitto)" || true
    exit 0
    ;;
  device)
    shift
    [[ -x $VENV/bin/python ]] || die "Run bash scripts/run_local.sh once first"
    SIM="$ROOT/../Test_Scripts/stream_device.py"
    [[ -f $SIM ]] || die "The simulator isn't at $SIM"
    CARDIOSENS_BACKEND="$ROOT" exec "$VENV/bin/python" "$SIM" --broker localhost --port "$MQTT_LOCAL_PORT" "$@"
    ;;
  ecg-live)
    [[ -x $VENV/bin/python ]] || die "Run bash scripts/run_local.sh once first"
    exec "$VENV/bin/python" -m app.scripts.simulate_ecg --base-url "http://localhost:${PORT:-8000}" --seconds "${2:-120}"
    ;;
  totp)
    [[ -x $VENV/bin/python ]] || die "Run bash scripts/run_local.sh once first"
    "$VENV/bin/python" -c "import pyotp; print(pyotp.TOTP('$DEMO_TOTP_SECRET').now())"
    exit 0
    ;;
esac

while [[ $# -gt 0 ]]; do
  case $1 in
    --dev) DEV=1; shift ;;
    --reset) RESET=1; shift ;;
    --no-demo) DEMO=0; shift ;;
    --admin-email) ADMIN_EMAIL=${2?}; shift 2 ;;
    --port) PORT=${2?}; shift 2 ;;
    --lan) LAN=1; shift ;;
    --show-codes) SHOW_CODES=1; shift ;;
    -h | --help) sed -n '2,/^$/p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "Unknown option: $1 (see --help)" ;;
  esac
done

command -v docker >/dev/null || die "Docker is required for the local PostgreSQL"
docker info >/dev/null 2>&1 || die "Docker isn't running (or your user can't reach it)"

# ---------------------------------------------------------------- PostgreSQL
if [[ $RESET -eq 1 ]]; then
  log "Removing local database"
  docker rm -f "$PG_CONTAINER" >/dev/null 2>&1 || true
  docker volume rm "$PG_VOLUME" >/dev/null 2>&1 || true
  docker rm -f "$MQTT_CONTAINER" >/dev/null 2>&1 || true
  rm -rf "$ROOT/var/storage" "$ROOT/var/mosquitto"
fi
if [[ -z $(docker ps -q -f "name=^${PG_CONTAINER}$") ]]; then
  if [[ -n $(docker ps -aq -f "name=^${PG_CONTAINER}$") ]]; then
    log "Starting PostgreSQL ($PG_CONTAINER)"
    docker start "$PG_CONTAINER" >/dev/null
  else
    log "Creating PostgreSQL 17 on localhost:$PG_PORT ($PG_CONTAINER)"
    docker run -d --name "$PG_CONTAINER" -e POSTGRES_USER=cardiosens -e POSTGRES_PASSWORD=cardiosens -e POSTGRES_DB=cardiosens \
      -p "127.0.0.1:$PG_PORT:5432" -v "$PG_VOLUME:/var/lib/postgresql/data" postgres:17 >/dev/null
  fi
fi
for _ in $(seq 60); do docker exec "$PG_CONTAINER" pg_isready -q -h 127.0.0.1 -U cardiosens 2>/dev/null && break; sleep 1; done
docker exec "$PG_CONTAINER" pg_isready -q -h 127.0.0.1 -U cardiosens || die "PostgreSQL didn't start (docker logs $PG_CONTAINER)"

# ---------------------------------------------------------------- Python
UV=$(command -v uv || true)
if [[ -z $UV ]]; then
  UV="$TOOLS/uv"
  if [[ ! -x $UV ]]; then
    log "uv not found; downloading the standalone binary to $TOOLS"
    mkdir -p "$TOOLS"
    case "$(uname -s)-$(uname -m)" in
      Linux-x86_64) TRIPLE=x86_64-unknown-linux-gnu ;;
      Linux-aarch64) TRIPLE=aarch64-unknown-linux-gnu ;;
      Darwin-arm64) TRIPLE=aarch64-apple-darwin ;;
      Darwin-x86_64) TRIPLE=x86_64-apple-darwin ;;
      *) die "Unsupported platform; install uv yourself: https://docs.astral.sh/uv/" ;;
    esac
    curl -fsSL "https://github.com/astral-sh/uv/releases/latest/download/uv-$TRIPLE.tar.gz" | tar xz -C "$TOOLS" --strip-components=1
  fi
fi
export UV_PYTHON_INSTALL_DIR="$TOOLS/python"
if [[ ! -x $VENV/bin/python ]]; then
  log "Creating Python 3.13 virtualenv in .venv"
  "$UV" venv -q -p 3.13 "$VENV"
fi
STAMP="$VENV/.requirements.sha"
REQ_SHA=$(cat requirements.txt pyproject.toml | sha256sum | cut -d' ' -f1)
if [[ ! -f $STAMP || $(cat "$STAMP") != "$REQ_SHA" ]]; then
  log "Installing Python dependencies"
  # Runtime pins from the lock file, dev tools (pytest, httpx, ruff) from pyproject.toml's "dev" extra.
  VIRTUAL_ENV="$VENV" "$UV" pip install -q -r requirements.txt -r pyproject.toml --extra dev -c requirements.txt
  echo "$REQ_SHA" >"$STAMP"
fi

# ---------------------------------------------------------------- settings (development only)
HOST_IP=127.0.0.1
PUBLIC_HOST=localhost
if [[ $LAN -eq 1 ]]; then
  HOST_IP=0.0.0.0
  PUBLIC_HOST=$(ip -4 route get 1.1.1.1 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p')
  [[ -n $PUBLIC_HOST ]] || PUBLIC_HOST=$(hostname -I | awk '{print $1}')
  [[ -n $PUBLIC_HOST ]] || die "Couldn't find this PC's network address"
fi
export ENV=development
export DATABASE_URL="postgresql+psycopg://cardiosens:cardiosens@127.0.0.1:$PG_PORT/cardiosens"
export API_BASE_URL="http://$PUBLIC_HOST:$PORT"
export WEB_BASE_URL="http://$PUBLIC_HOST:$([[ $DEV -eq 1 ]] && echo 5173 || echo "$PORT")"
export DEV_EXPOSE_OTP=true
export SMS_PROVIDER=console
export EMAIL_PROVIDER=console
REAL_MAIL=0
if [[ -f $LOCAL_ENV ]]; then
  load_local_env
  if [[ $EMAIL_PROVIDER == smtp ]]; then
    REAL_MAIL=1
    if [[ $SHOW_CODES -eq 0 ]]; then
      export DEV_EXPOSE_OTP=false
      [[ $SMS_PROVIDER == msg91 ]] || export SMS_PROVIDER=none # like production without MSG91: sign-up uses the email code
    fi
  fi
fi
export STORAGE_BACKEND=local
export STORAGE_LOCAL_PATH="$ROOT/var/storage"
export CORS_ORIGINS='[]'
# Device broker: the app writes the logins file, the container reads it; a HUP makes Mosquitto re-read it.
export MQTT_HOST=127.0.0.1
export MQTT_PORT=$MQTT_LOCAL_PORT
export MQTT_PASSWD_FILE="$ROOT/var/mosquitto/passwd"
export MQTT_RELOAD_COMMAND="docker kill -s HUP $MQTT_CONTAINER"
export PYTHONDONTWRITEBYTECODE=1

log "Applying database migrations"
"$VENV/bin/alembic" upgrade head
SEED_ARGS=()
[[ $DEMO -eq 1 ]] && SEED_ARGS+=(--demo)
[[ -n $ADMIN_EMAIL ]] && SEED_ARGS+=(--admin-email "$ADMIN_EMAIL")
"$VENV/bin/python" -m app.scripts.seed "${SEED_ARGS[@]}" 2>&1 | grep -v '^{' || true

# ---------------------------------------------------------------- device broker (Mosquitto)
# Recreated on every start (it holds nothing but var/mosquitto), so --lan / the port always match.
log "Starting the device broker ($MQTT_CONTAINER on $HOST_IP:$MQTT_LOCAL_PORT)"
mkdir -p "$ROOT/var/mosquitto"
"$VENV/bin/python" -c "from app.core.db import SessionLocal; from app.services.device_hub import sync_broker_logins; db = SessionLocal(); sync_broker_logins(db); db.close()"
docker rm -f "$MQTT_CONTAINER" >/dev/null 2>&1 || true
docker run -d --name "$MQTT_CONTAINER" --user "$(id -u):$(id -g)" -p "$HOST_IP:$MQTT_LOCAL_PORT:1883" \
  -v "$ROOT/scripts/mosquitto/cardiosens-local.conf:/mosquitto/config/mosquitto.conf:ro" \
  -v "$ROOT/scripts/mosquitto/cardiosens.acl:/mosquitto/config/cardiosens.acl:ro" \
  -v "$ROOT/var/mosquitto:/mosquitto/data" eclipse-mosquitto:2 >/dev/null || die "The device broker didn't start"

# ---------------------------------------------------------------- web app
command -v npm >/dev/null || die "Node.js 20+ (npm) is required for the web app"
if [[ ! -d web/node_modules || web/package-lock.json -nt web/node_modules ]]; then
  log "Installing web dependencies"
  (cd web && npm ci --no-audit --no-fund --loglevel=error)
fi
if [[ $DEV -eq 0 ]]; then
  NEWEST_SRC=$(find web/src web/index.html web/public -type f -newer website/index.html 2>/dev/null | head -1 || true)
  if [[ ! -f website/index.html || -n $NEWEST_SRC ]]; then
    log "Building the web app into website/"
    (cd web && npm run build --silent)
  fi
fi

# ---------------------------------------------------------------- run
PIDS=()
cleanup() {
  for p in "${PIDS[@]}"; do kill "$p" 2>/dev/null || true; done
  wait 2>/dev/null || true
  echo
  echo "Stopped. The database and device broker keep running in Docker: bash scripts/run_local.sh stop"
}
trap cleanup EXIT INT TERM

if [[ $DEV -eq 1 ]]; then
  (cd web && exec ./node_modules/.bin/vite --port 5173 --strictPort --clearScreen false $([[ $LAN -eq 1 ]] && echo --host)) &
  PIDS+=($!)
fi

if [[ $REAL_MAIL -eq 1 ]]; then
  MODE="Real email via $SMTP_HOST ($SMTP_USERNAME); SMS: $SMS_PROVIDER; codes on screen: $DEV_EXPOSE_OTP"
else
  MODE="Emails and codes are printed below and shown on the pages (no real email; set up: bash scripts/run_local.sh mail --user ...)"
fi
cat <<EOF

  Web app:  $WEB_BASE_URL
  API docs: http://localhost:$PORT/docs
  Sign in:  patient@demo.cardiosens.ai / doctor@demo.cardiosens.ai / clinic@demo.cardiosens.ai
            testdoctor@demo.cardiosens.ai (appointments: Home calendar, offline mode, Statistics)
            password cardiosens-demo-2026; doctor & clinic code: bash scripts/run_local.sh totp
  Mode:     $MODE
  Devices:  broker localhost:$MQTT_LOCAL_PORT; simulator: bash scripts/run_local.sh device --device CS12-TEST-0001 --secret ...
$([[ $LAN -eq 1 ]] && printf '  Phone:    same Wi-Fi, open %s ; Android app: CS_API_BASE_URL=%s npx expo start\n' "$WEB_BASE_URL" "$API_BASE_URL")
  Ctrl+C to stop.

EOF
"$VENV/bin/uvicorn" app.main:app --host "$HOST_IP" --port "$PORT" --reload --reload-dir app &
PIDS+=($!)
"$VENV/bin/python" -m app.scripts.mqtt_bridge &
PIDS+=($!)
wait "${PIDS[@]}"
