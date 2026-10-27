#!/usr/bin/env bash
# Configure SMS (one-time codes for sign-up and sign-in) on the server, then send a test code. Secrets are
# asked for with hidden input, so they never appear in the shell history or on screen.
#
#   sudo bash scripts/set_sms.sh --provider msg91 --template 6650a1b2c3d4e5f6a7b8c9d0 --test +919812345678
#   sudo bash scripts/set_sms.sh --provider twilio --sid ACxxxxxxxx --from MGxxxxxxxx --test +15551234567
#   sudo bash scripts/set_sms.sh --test +919812345678       # only send a test with the current settings
#
# India: SMS must use a DLT-registered sender ID and template. With MSG91, create an OTP template that contains
# ##OTP## (e.g. "##OTP## is your CardiosensAI code. It expires in 10 minutes. Don't share it with anyone.")
# and give its template id. Optionally a second template for "this number already has an account" notices.
#
# Writes SMS_PROVIDER and TWILIO_* / MSG91_* in /etc/cardiosensai/backend.env (other settings are kept),
# restarts cardiosensai-api if it is running, and sends a test code to --test.
#
# Options:
#   --provider NAME     msg91 | twilio | none
#   --template ID       MSG91: OTP template id (DLT, with ##OTP##)
#   --notice-template ID  MSG91: optional template for the "already registered" notice (no variables)
#   --sid SID           Twilio: account SID (AC...)
#   --from SENDER       Twilio: sender number (+1...) or Messaging Service SID (MG...)
#   --test NUMBER       Send a test code to this number (E.164). --test only = test the current settings
#   -h, --help          Show this help
# The secret (MSG91 auth key / Twilio auth token) can also come from SMS_SECRET in the environment.

set -Eeuo pipefail
trap 'echo "set_sms.sh: failed at line $LINENO: $BASH_COMMAND" >&2' ERR

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
kv_set() {
  local key=$1 value=$2 file=$3 tmp
  tmp=$(mktemp)
  [[ -f $file ]] && grep -v "^$key=" "$file" >"$tmp" || true
  printf '%s=%s\n' "$key" "$value" >>"$tmp"
  cat "$tmp" >"$file" # keep the file's owner and mode
  rm -f "$tmp"
}
is_e164() { [[ $1 =~ ^\+[1-9][0-9]{7,14}$ ]]; }

PROVIDER=""
TEMPLATE=""
NOTICE=""
SID=""
FROM=""
TEST_TO=""
while [[ $# -gt 0 ]]; do
  case $1 in
    --provider) PROVIDER=${2?}; shift 2 ;;
    --template) TEMPLATE=${2?}; shift 2 ;;
    --notice-template) NOTICE=${2?}; shift 2 ;;
    --sid) SID=${2?}; shift 2 ;;
    --from) FROM=${2?}; shift 2 ;;
    --test) TEST_TO=${2?}; shift 2 ;;
    -h | --help) usage ;;
    *) die "Unknown option: $1 (see --help)" ;;
  esac
done

[[ $EUID -eq 0 ]] || die "Run as root: sudo bash $0 ..."
[[ -z $TEST_TO ]] || is_e164 "$TEST_TO" || die "--test needs a number in E.164, e.g. +919812345678"

send_test() { # $1 = number
  if [[ ! -x $VENV/bin/python || ! -d $APP_DIR ]]; then
    warn "The app isn't installed yet ($APP_DIR). Settings are saved; test after deploying: sudo bash scripts/set_sms.sh --test $1"
    return 0
  fi
  log "Sending a test code to $1"
  if systemd-run --quiet --wait --pipe --collect --service-type=exec -p User=$APP_USER -p Group=$APP_USER \
    -p WorkingDirectory=$APP_DIR -p EnvironmentFile=$ENV_FILE -p Environment=PYTHONDONTWRITEBYTECODE=1 \
    $VENV/bin/python -m app.scripts.test_sms "$1"; then
    printf '\033[1;32mSent. Check the phone.\033[0m\n'
  else
    die "The test SMS failed (reason above). Fix the settings and run this again." 1
  fi
}

if [[ -z $PROVIDER ]]; then
  [[ -n $TEST_TO ]] || die "Give --provider msg91|twilio|none (or --test NUMBER to test the current settings)"
  send_test "$TEST_TO"
  exit 0
fi

secret_prompt() { # $1 = label
  local v=${SMS_SECRET:-}
  if [[ -z $v ]]; then
    [[ -t 0 ]] || die "No terminal to ask for the $1; set SMS_SECRET in the environment instead"
    read -rsp "$1 (hidden): " v
    echo >&2 # the value is captured from stdout; keep the newline off it
  fi
  [[ -n $v && $v != *$'\n'* && $v != *'"'* && $v != *'\'* ]] || die "Empty or invalid $1"
  printf '%s' "$v"
}

if [[ ! -f $ENV_FILE ]]; then
  install -d -m 0750 $CONF_DIR
  install -m 0600 /dev/null $ENV_FILE
fi
case $PROVIDER in
  msg91)
    [[ -n $TEMPLATE ]] || die "--template ID is required for msg91 (the DLT OTP template, with ##OTP##)"
    KEY=$(secret_prompt "MSG91 auth key")
    log "Writing MSG91 settings to $ENV_FILE"
    kv_set SMS_PROVIDER msg91 $ENV_FILE
    kv_set MSG91_AUTH_KEY "$KEY" $ENV_FILE
    kv_set MSG91_OTP_TEMPLATE_ID "$TEMPLATE" $ENV_FILE
    kv_set MSG91_NOTICE_TEMPLATE_ID "$NOTICE" $ENV_FILE
    ;;
  twilio)
    [[ $SID =~ ^AC[0-9a-fA-F]{32}$ ]] || die "--sid must be the Twilio account SID (AC + 32 hex characters)"
    [[ $FROM =~ ^(\+[1-9][0-9]{7,14}|MG[0-9a-fA-F]{32})$ ]] || die "--from must be a sender number (+...) or a Messaging Service SID (MG...)"
    TOKEN=$(secret_prompt "Twilio auth token")
    log "Writing Twilio settings to $ENV_FILE"
    kv_set SMS_PROVIDER twilio $ENV_FILE
    kv_set TWILIO_ACCOUNT_SID "$SID" $ENV_FILE
    kv_set TWILIO_AUTH_TOKEN "$TOKEN" $ENV_FILE
    kv_set TWILIO_FROM "$FROM" $ENV_FILE
    ;;
  none)
    warn "SMS off: nobody can sign up, and SMS sign-in codes can't be sent."
    kv_set SMS_PROVIDER none $ENV_FILE
    ;;
  *) die "--provider must be msg91, twilio or none" ;;
esac

if systemctl is-active -q $SERVICE 2>/dev/null; then
  log "Restarting $SERVICE"
  systemctl restart $SERVICE
fi
[[ -n $TEST_TO && $PROVIDER != none ]] && send_test "$TEST_TO"
echo "Done. SMS_PROVIDER=$PROVIDER (secrets saved, not shown)."
exit 0
