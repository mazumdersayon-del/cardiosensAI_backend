#!/usr/bin/env bash
# Set up MSG91 for mobile-number one-time codes (sign-up and sign-in) on the server, then send a test code.
# The auth key is asked for with hidden input, so it never appears in the shell history or on screen.
#
#   sudo bash scripts/setup_msg91.sh --template 6650a1b2c3d4e5f6a7b8c9d0 --test +919812345678   # first setup
#   sudo bash scripts/setup_msg91.sh --notice-template 6650a1b2c3d4e5f6a7b8c9d1                 # add/change a template, keep the rest
#   sudo bash scripts/setup_msg91.sh --test +919812345678                                       # only send a test code
#   sudo bash scripts/setup_msg91.sh --show                                                     # print the current settings (key masked)
#   sudo bash scripts/setup_msg91.sh --off                                                      # switch SMS off
#
# Before running it, in the MSG91 panel (India: SMS needs DLT registration, which takes a few days):
#   1. Register the DLT entity and the sender ID (header), e.g. CRDSNS, on your DLT portal; link them in MSG91.
#   2. Create an OTP template with ##OTP## and get it DLT-approved, e.g.
#        "##OTP## is your CardiosensAI code. It expires in 10 minutes. Don't share it with anyone."
#      MSG91 → OTP → Templates → the template id (24 hex characters) is what --template takes.
#   3. Optional: a second (Flow) template without variables for "this number already has an account".
#   4. MSG91 → API → Auth Key: create one (whitelist the server's IP if you turn on IP security).
# The server makes the 6-digit code and checks it (only an HMAC is stored); MSG91 only delivers it.
#
# Writes SMS_PROVIDER=msg91 and MSG91_* in /etc/cardiosensai/backend.env (other settings are kept, old TWILIO_*
# lines are removed), restarts cardiosensai-api if it is running, and sends a test code to --test.
#
# Options:
#   --template ID         MSG91 OTP template id (DLT-approved, with ##OTP##). Required the first time
#   --notice-template ID  Optional template for the "already registered" notice ("" removes it)
#   --reminder-template ID  Optional appointment reminder template (Flow, variables ##doctor## and ##time##; "" removes it)
#   --new-key             Ask for a new auth key even if one is saved (otherwise the saved key is kept)
#   --test NUMBER         Send a test code to this number (E.164, e.g. +919812345678)
#   --show                Print the current SMS settings (auth key masked) and exit
#   --off                 SMS_PROVIDER=none: sign-up needs an email (number confirmed later), SMS sign-in answers 503
#   -h, --help            Show this help
# The auth key can also come from MSG91_AUTH_KEY in the environment (e.g. MSG91_AUTH_KEY=... sudo -E bash ...).

set -Eeuo pipefail
trap 'echo "setup_msg91.sh: failed at line $LINENO: $BASH_COMMAND" >&2' ERR

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
kv_get() { [[ -f $2 ]] && grep -m1 "^$1=" "$2" | cut -d= -f2- || true; }
kv_set() {
  local key=$1 value=$2 file=$3 tmp
  tmp=$(mktemp)
  [[ -f $file ]] && grep -v "^$key=" "$file" >"$tmp" || true
  printf '%s=%s\n' "$key" "$value" >>"$tmp"
  cat "$tmp" >"$file" # keep the file's owner and mode
  rm -f "$tmp"
}
kv_del() { # $1 = key prefix
  local tmp
  [[ -f $2 ]] || return 0
  tmp=$(mktemp)
  grep -v "^$1" "$2" >"$tmp" || true
  cat "$tmp" >"$2"
  rm -f "$tmp"
}
is_e164() { [[ $1 =~ ^\+[1-9][0-9]{7,14}$ ]]; }
is_template() { [[ $1 =~ ^[0-9a-fA-F]{24}$ ]]; }
mask() { local v=$1; [[ -z $v ]] && echo "(not set)" || echo "${v:0:4}…${v: -4} (${#v} characters)"; }

TEMPLATE=""
NOTICE=""
NOTICE_SET=0
REMINDER=""
REMINDER_SET=0
NEW_KEY=0
TEST_TO=""
SHOW=0
OFF=0
while [[ $# -gt 0 ]]; do
  case $1 in
    --template) TEMPLATE=${2?}; shift 2 ;;
    --notice-template) NOTICE=${2?}; NOTICE_SET=1; shift 2 ;;
    --reminder-template) REMINDER=${2?}; REMINDER_SET=1; shift 2 ;;
    --new-key) NEW_KEY=1; shift ;;
    --test) TEST_TO=${2?}; shift 2 ;;
    --show) SHOW=1; shift ;;
    --off) OFF=1; shift ;;
    -h | --help) usage ;;
    *) die "Unknown option: $1 (see --help)" ;;
  esac
done

[[ $EUID -eq 0 ]] || die "Run as root: sudo bash $0 ..."
[[ -z $TEST_TO ]] || is_e164 "$TEST_TO" || die "--test needs a number in E.164, e.g. +919812345678"
[[ -z $TEMPLATE ]] || is_template "$TEMPLATE" || warn "--template '$TEMPLATE' doesn't look like an MSG91 template id (24 hex characters); using it anyway"
[[ -z $NOTICE ]] || is_template "$NOTICE" || warn "--notice-template '$NOTICE' doesn't look like an MSG91 template id; using it anyway"
[[ -z $REMINDER ]] || is_template "$REMINDER" || warn "--reminder-template '$REMINDER' doesn't look like an MSG91 template id; using it anyway"

if [[ $SHOW -eq 1 ]]; then
  echo "SMS_PROVIDER=$(kv_get SMS_PROVIDER $ENV_FILE)"
  echo "MSG91_AUTH_KEY=$(mask "$(kv_get MSG91_AUTH_KEY $ENV_FILE)")"
  echo "MSG91_OTP_TEMPLATE_ID=$(kv_get MSG91_OTP_TEMPLATE_ID $ENV_FILE)"
  echo "MSG91_NOTICE_TEMPLATE_ID=$(kv_get MSG91_NOTICE_TEMPLATE_ID $ENV_FILE)"
  echo "MSG91_REMINDER_TEMPLATE_ID=$(kv_get MSG91_REMINDER_TEMPLATE_ID $ENV_FILE)"
  exit 0
fi

send_test() { # $1 = number
  if [[ ! -x $VENV/bin/python || ! -d $APP_DIR ]]; then
    warn "The app isn't installed yet ($APP_DIR). Settings are saved; test after deploying: sudo bash scripts/setup_msg91.sh --test $1"
    return 0
  fi
  log "Sending a test code to $1 through MSG91"
  if systemd-run --quiet --wait --pipe --collect --service-type=exec -p User=$APP_USER -p Group=$APP_USER \
    -p WorkingDirectory=$APP_DIR -p EnvironmentFile=$ENV_FILE -p Environment=PYTHONDONTWRITEBYTECODE=1 \
    $VENV/bin/python -m app.scripts.test_sms "$1"; then
    printf '\033[1;32mMSG91 accepted it. Check the phone (DLT delivery can take a few seconds).\033[0m\n'
  else
    die "The test SMS failed (MSG91's reason is in the log line above: auth key, template id, DLT approval, IP whitelist or balance). Fix it and run this again." 1
  fi
}

restart() {
  if systemctl is-active -q $SERVICE 2>/dev/null; then
    log "Restarting $SERVICE"
    systemctl restart $SERVICE
  fi
}

if [[ ! -f $ENV_FILE ]]; then
  install -d -m 0750 $CONF_DIR
  install -m 0600 /dev/null $ENV_FILE
fi

if [[ $OFF -eq 1 ]]; then
  warn "SMS off: sign-up needs an email address (numbers stay unconfirmed until SMS is back); SMS sign-in codes can't be sent (email codes, passwords and Google still work)."
  kv_set SMS_PROVIDER none $ENV_FILE
  restart
  exit 0
fi

# Only a test: keep the settings as they are.
if [[ -z $TEMPLATE && $NOTICE_SET -eq 0 && $REMINDER_SET -eq 0 && $NEW_KEY -eq 0 ]]; then
  [[ -n $TEST_TO ]] || die "Nothing to do. First setup: --template ID [--test NUMBER]; see --help"
  [[ $(kv_get SMS_PROVIDER $ENV_FILE) == msg91 ]] || die "MSG91 isn't set up yet: sudo bash $0 --template ID --test $TEST_TO"
  send_test "$TEST_TO"
  exit 0
fi

TEMPLATE=${TEMPLATE:-$(kv_get MSG91_OTP_TEMPLATE_ID $ENV_FILE)}
[[ -n $TEMPLATE ]] || die "--template ID is required (the MSG91 OTP template id, DLT-approved, with ##OTP##)"

KEY=${MSG91_AUTH_KEY:-}
if [[ -z $KEY && $NEW_KEY -eq 0 ]]; then
  KEY=$(kv_get MSG91_AUTH_KEY $ENV_FILE)
  [[ -z $KEY ]] || echo "Keeping the saved MSG91 auth key $(mask "$KEY") (use --new-key to replace it)."
fi
if [[ -z $KEY ]]; then
  [[ -t 0 ]] || die "No terminal to ask for the MSG91 auth key; set MSG91_AUTH_KEY in the environment instead"
  read -rsp "MSG91 auth key (hidden): " KEY
  echo
fi
[[ $KEY =~ ^[A-Za-z0-9]{16,64}$ ]] || die "That doesn't look like an MSG91 auth key (letters and digits, from MSG91 → API → Auth Key)"

log "Writing MSG91 settings to $ENV_FILE"
kv_set SMS_PROVIDER msg91 $ENV_FILE
kv_set MSG91_AUTH_KEY "$KEY" $ENV_FILE
kv_set MSG91_OTP_TEMPLATE_ID "$TEMPLATE" $ENV_FILE
if [[ $NOTICE_SET -eq 1 ]]; then
  kv_set MSG91_NOTICE_TEMPLATE_ID "$NOTICE" $ENV_FILE
fi
if [[ $REMINDER_SET -eq 1 ]]; then
  kv_set MSG91_REMINDER_TEMPLATE_ID "$REMINDER" $ENV_FILE
fi
kv_del TWILIO_ $ENV_FILE # Twilio is no longer supported

restart
[[ -z $TEST_TO ]] || send_test "$TEST_TO"
echo "Done. SMS_PROVIDER=msg91, OTP template $TEMPLATE (auth key saved, not shown)."
[[ -n $TEST_TO ]] || echo "Send a test code: sudo bash $0 --test +91XXXXXXXXXX"
exit 0
