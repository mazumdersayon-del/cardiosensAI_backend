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
#   bash scripts/run_local.sh stop             stop the local database container (data is kept)
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
PORT=8000
DEV=0
RESET=0
DEMO=1
ADMIN_EMAIL=""
DEMO_TOTP_SECRET=CARDIOSENSDEMOTOTPSECRETCODE2345
TOOLS="$ROOT/var/tools"
VENV="$ROOT/.venv"

log() { printf '\033[1;36m==> %s\033[0m\n' "$*"; }
die() { printf '\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

case "${1:-}" in
  stop)
    docker stop "$PG_CONTAINER" >/dev/null 2>&1 && echo "Stopped $PG_CONTAINER (data kept in volume $PG_VOLUME)" || echo "Not running"
    exit 0
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
  rm -rf "$ROOT/var/storage"
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
export ENV=development
export DATABASE_URL="postgresql+psycopg://cardiosens:cardiosens@127.0.0.1:$PG_PORT/cardiosens"
export API_BASE_URL="http://localhost:$PORT"
export WEB_BASE_URL="http://localhost:$([[ $DEV -eq 1 ]] && echo 5173 || echo "$PORT")"
export DEV_EXPOSE_OTP=true
export SMS_PROVIDER=console
export EMAIL_PROVIDER=console
export STORAGE_BACKEND=local
export STORAGE_LOCAL_PATH="$ROOT/var/storage"
export CORS_ORIGINS='[]'
export PYTHONDONTWRITEBYTECODE=1

log "Applying database migrations"
"$VENV/bin/alembic" upgrade head
SEED_ARGS=()
[[ $DEMO -eq 1 ]] && SEED_ARGS+=(--demo)
[[ -n $ADMIN_EMAIL ]] && SEED_ARGS+=(--admin-email "$ADMIN_EMAIL")
"$VENV/bin/python" -m app.scripts.seed "${SEED_ARGS[@]}" 2>&1 | grep -v '^{' || true

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
  echo "Stopped. The database keeps running in Docker: bash scripts/run_local.sh stop"
}
trap cleanup EXIT INT TERM

if [[ $DEV -eq 1 ]]; then
  (cd web && exec ./node_modules/.bin/vite --port 5173 --strictPort --clearScreen false) &
  PIDS+=($!)
fi

cat <<EOF

  Web app:  $WEB_BASE_URL
  API docs: http://localhost:$PORT/docs
  Sign in:  patient@demo.cardiosens.ai / doctor@demo.cardiosens.ai / clinic@demo.cardiosens.ai
            password cardiosens-demo-2026; doctor & clinic code: bash scripts/run_local.sh totp
  Emails (confirmation / reset links) are printed below. Ctrl+C to stop.

EOF
"$VENV/bin/uvicorn" app.main:app --host 127.0.0.1 --port "$PORT" --reload --reload-dir app &
PIDS+=($!)
wait "${PIDS[@]}"
