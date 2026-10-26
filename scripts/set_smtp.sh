#!/usr/bin/env bash
# Configure outgoing email (SMTP) on the server, then send a test email. The password is asked for
# with hidden input, so it never appears in the shell history or on screen.
#
#   sudo bash scripts/set_smtp.sh --user no-reply@cardiosens.ai              # Google Workspace / Gmail (app password)
#   sudo bash scripts/set_smtp.sh --relay --from no-reply@cardiosens.ai      # Workspace SMTP relay (IP allow-list, no password)
#   sudo bash scripts/set_smtp.sh --user me@example.com --host smtppro.zoho.in   # another provider
#   sudo bash scripts/set_smtp.sh --test you@example.com                      # only send a test with the current settings
#
# Google needs an APP PASSWORD (16 letters from https://myaccount.google.com/apppasswords, 2-Step
# Verification must be on), not the account password. Spaces in it are removed.
#
# Writes EMAIL_PROVIDER, EMAIL_FROM and SMTP_* in /etc/cardiosensai/backend.env (other settings are kept),
# restarts cardiosensai-api if it is running, and sends a test email to --test (default: the sender).
#
# Options:
#   --user ADDRESS     SMTP login, usually the sending mailbox
#   --from ADDRESS     Sender address (default: --user). "Name <address>" or just the address
#   --name NAME        Sender display name (default "CardiosensAI")
#   --host NAME        SMTP server (default smtp.gmail.com; smtp-relay.gmail.com with --relay)
#   --port N           Port (default 587; 465 with --tls ssl)
#   --tls MODE         starttls (default) | ssl | none
#   --relay            Google Workspace SMTP relay: no login, the server's IP must be allowed in admin.google.com
#   --test ADDRESS     Where to send the test email (default: the sender). --test only = test current settings
#   --no-test          Don't send a test email
#   -h, --help         Show this help
# The password can also come from the SMTP_PASSWORD environment variable (e.g. in automation).

set -Eeuo pipefail
trap 'echo "set_smtp.sh: failed at line $LINENO: $BASH_COMMAND" >&2' ERR

CONF_DIR=/etc/cardiosensai
ENV_FILE=$CONF_DIR/backend.env
APP_DIR=/opt/cardiosensai/app
VENV=/opt/cardiosensai/venv
APP_USER=cardiosens
SERVICE=cardiosensai-api

log() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33mWARNING: %s\033[0m\n' "$*" >&2; }
die() { printf '\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit "${2:-2}"; }
usage() { sed -n '2,/^$/p' "$0" | sed 's/^# \{0,1\}//'; exit 0; }
kv_get() { [[ -f $2 ]] && sed -n "s/^$1=//p" "$2" | tail -n1 || true; }
kv_set() {
  local key=$1 value=$2 file=$3 tmp
  tmp=$(mktemp)
  [[ -f $file ]] && grep -v "^$key=" "$file" >"$tmp" || true
  printf '%s=%s\n' "$key" "$value" >>"$tmp"
  cat "$tmp" >"$file" # keep the file's owner and mode
  rm -f "$tmp"
}
kv_del() { [[ -f $2 ]] || return 0; local tmp; tmp=$(mktemp); grep -v "^$1=" "$2" >"$tmp" || true; cat "$tmp" >"$2"; rm -f "$tmp"; }
is_email() { [[ $1 =~ ^[^@[:space:]\<\>]+@[^@[:space:]\<\>]+\.[^@[:space:]\<\>]+$ ]]; }

USER_ADDR=""
FROM=""
NAME="CardiosensAI"
HOST=""
PORT=""
TLS=starttls
RELAY=0
TEST_TO=""
TEST=1
ONLY_TEST=0
[[ $# -eq 2 && $1 == --test ]] && ONLY_TEST=1

while [[ $# -gt 0 ]]; do
  case $1 in
    --user) USER_ADDR=${2?}; shift 2 ;;
    --from) FROM=${2?}; shift 2 ;;
    --name) NAME=${2?}; shift 2 ;;
    --host) HOST=${2?}; shift 2 ;;
    --port) PORT=${2?}; shift 2 ;;
    --tls) TLS=${2?}; shift 2 ;;
    --relay) RELAY=1; shift ;;
    --test) TEST_TO=${2?}; shift 2 ;;
    --no-test) TEST=0; shift ;;
    -h | --help) usage ;;
    *) die "Unknown option: $1 (see --help)" ;;
  esac
done

[[ $EUID -eq 0 ]] || die "Run as root: sudo bash $0 ..."

send_test() { # $1 = recipient
  if [[ ! -x $VENV/bin/python || ! -d $APP_DIR ]]; then
    warn "The app isn't installed yet ($APP_DIR). Settings are saved; test after deploying: sudo bash scripts/deploy.sh --test-email $1"
    return 0
  fi
  log "Sending a test email to $1"
  if systemd-run --quiet --wait --pipe --collect --service-type=exec -p User=$APP_USER -p Group=$APP_USER \
    -p WorkingDirectory=$APP_DIR -p EnvironmentFile=$ENV_FILE -p Environment=PYTHONDONTWRITEBYTECODE=1 \
    $VENV/bin/python -m app.scripts.test_email "$1"; then
    printf '\033[1;32mSent. Check the inbox (and spam folder) of %s.\033[0m\n' "$1"
  else
    die "The test email failed (reason above). Fix the settings and run this again." 1
  fi
}

if [[ $ONLY_TEST -eq 1 ]]; then
  is_email "$TEST_TO" || die "--test needs an email address"
  send_test "$TEST_TO"
  exit 0
fi

# ---------------------------------------------------------------- validate
if [[ $RELAY -eq 1 ]]; then
  HOST=${HOST:-smtp-relay.gmail.com}
  FROM=${FROM:-$USER_ADDR}
  [[ -n $FROM ]] || die "--relay needs --from ADDRESS (a mailbox or alias in your Workspace domain)"
else
  HOST=${HOST:-smtp.gmail.com}
  [[ -n $USER_ADDR ]] || die "Give the SMTP login: --user no-reply@cardiosens.ai (or --relay for the Workspace SMTP relay)"
  is_email "$USER_ADDR" || die "--user must be an email address"
  FROM=${FROM:-$USER_ADDR}
fi
[[ $TLS == starttls || $TLS == ssl || $TLS == none ]] || die "--tls must be starttls, ssl or none"
if [[ -z $PORT ]]; then [[ $TLS == ssl ]] && PORT=465 || PORT=587; fi
[[ $PORT =~ ^[0-9]+$ ]] || die "--port must be a number"
[[ $HOST =~ ^[A-Za-z0-9.-]+$ ]] || die "Invalid --host: $HOST"
FROM_ADDR=$(sed -n 's/.*<\(.*\)>.*/\1/p' <<<"$FROM"); FROM_ADDR=${FROM_ADDR:-$FROM}
is_email "$FROM_ADDR" || die "--from must be an email address or \"Name <address>\""
[[ $FROM == *"<"* ]] || FROM="$NAME <$FROM>"
TEST_TO=${TEST_TO:-$FROM_ADDR}
is_email "$TEST_TO" || die "--test needs an email address"

# ---------------------------------------------------------------- password
PASSWORD=""
if [[ $RELAY -eq 0 ]]; then
  PASSWORD=${SMTP_PASSWORD:-}
  if [[ -z $PASSWORD ]]; then
    [[ -t 0 ]] || die "No terminal to ask for the password; set SMTP_PASSWORD in the environment instead"
    read -rsp "SMTP password for $USER_ADDR (hidden; for Google use the 16-letter app password): " PASSWORD
    echo
  fi
  if [[ $HOST == smtp.gmail.com ]]; then
    PASSWORD=${PASSWORD// /} # Google shows app passwords as "abcd efgh ijkl mnop"
    [[ $PASSWORD =~ ^[a-z]{16}$ ]] || warn "That doesn't look like a Google app password (16 lowercase letters). Google refuses normal account passwords for SMTP; create one at https://myaccount.google.com/apppasswords"
  fi
  [[ -n $PASSWORD ]] || die "Empty password"
  [[ $PASSWORD != *$'\n'* && $PASSWORD != *'\'* && $PASSWORD != *'"'* ]] || die "The password contains a backslash, quote or newline, which the settings file can't hold safely. Use an app password"
fi

# ---------------------------------------------------------------- write
log "Writing email settings to $ENV_FILE"
if [[ ! -f $ENV_FILE ]]; then
  install -d -m 0750 $CONF_DIR
  install -m 0600 /dev/null $ENV_FILE
fi
kv_set EMAIL_PROVIDER smtp $ENV_FILE
kv_set EMAIL_FROM "$FROM" $ENV_FILE
kv_set SMTP_HOST "$HOST" $ENV_FILE
kv_set SMTP_PORT "$PORT" $ENV_FILE
kv_set SMTP_TLS "$TLS" $ENV_FILE
if [[ $RELAY -eq 1 ]]; then
  kv_del SMTP_USERNAME $ENV_FILE
  kv_del SMTP_PASSWORD $ENV_FILE
else
  kv_set SMTP_USERNAME "$USER_ADDR" $ENV_FILE
  kv_set SMTP_PASSWORD "$PASSWORD" $ENV_FILE
fi
printf '  EMAIL_FROM=%s\n  SMTP_HOST=%s  SMTP_PORT=%s  SMTP_TLS=%s\n  SMTP_USERNAME=%s\n  SMTP_PASSWORD=%s\n' \
  "$FROM" "$HOST" "$PORT" "$TLS" "${USER_ADDR:-(none, relay)}" "$([[ -n $PASSWORD ]] && echo '******** (saved)' || echo '(none)')"

if systemctl is-active -q $SERVICE 2>/dev/null; then
  log "Restarting $SERVICE"
  systemctl restart $SERVICE
fi
[[ $TEST -eq 1 ]] && send_test "$TEST_TO"
if [[ $(kv_get ENV "$ENV_FILE") == production && ! -d $APP_DIR ]] || [[ $(kv_get NO_EMAIL "$CONF_DIR/deploy.env") == 1 ]]; then
  echo "Next: sudo bash scripts/deploy.sh   (it now finds SMTP_HOST and switches email on)"
fi
exit 0
