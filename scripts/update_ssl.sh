#!/usr/bin/env bash
# Create the HTTPS certificate, or renew it when it is close to expiring (Let's Encrypt).
#
#   sudo bash scripts/update_ssl.sh                         # remembered domain: create if missing, renew if <= 30 days left
#   sudo bash scripts/update_ssl.sh --domain app.cardiosens.ai --email ops@cardiosens.ai   # first certificate / new name
#   sudo bash scripts/update_ssl.sh --check                 # only show the certificate and days left (no changes)
#   sudo bash scripts/update_ssl.sh --force                 # renew now, whatever the expiry date
#   sudo bash scripts/update_ssl.sh --dry-run               # test renewal against Let's Encrypt staging (no changes)
#
# What it does:
#   no certificate yet    runs scripts/nginx.sh, which requests one (webroot challenge on port 80),
#                         switches nginx to HTTPS and sets API_BASE_URL / WEB_BASE_URL to https://DOMAIN
#   certificate present   prints issuer, expiry and days left; renews with certbot when days left <= --days
#                         (or with --force), reloads nginx and checks the certificate nginx actually serves
#   always                makes sure certbot's automatic renewal timer is on, so this is rarely needed
#
# Needs: DNS for the domain pointing at this server, ports 80 and 443 open.
# The domain and e-mail are remembered in /etc/cardiosensai/deploy.env (shared with deploy.sh and nginx.sh).
#
# Options (each can also be given as the environment variable in brackets):
#   --domain NAME      Host name, e.g. app.cardiosens.ai [DOMAIN]
#   --email ADDRESS    Let's Encrypt account e-mail (needed for the first certificate) [LETSENCRYPT_EMAIL]
#   --days N           Renew when N or fewer days are left (default 30) [RENEW_DAYS]
#   --force            Renew now even if the certificate is still valid
#   --check            Report only; exit code 0 = valid for more than --days, 1 = missing or expiring soon
#   --dry-run          Test the renewal with Let's Encrypt's staging server; changes nothing
#   --port N           Port the API listens on at 127.0.0.1, passed to nginx.sh (default 8000) [API_PORT]
#   -h, --help         Show this help
#
# Exit codes: 0 ok · 1 certificate missing/expiring (--check) or renewal failed · 2 usage or setup error

set -Eeuo pipefail
trap 'echo "update_ssl.sh: failed at line $LINENO: $BASH_COMMAND" >&2' ERR

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONF_DIR=/etc/cardiosensai
STATE_FILE=$CONF_DIR/deploy.env
SITE_CONF=/etc/nginx/sites-available/cardiosensai
LE_LIVE=${LE_LIVE:-/etc/letsencrypt/live}

log() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
ok() { printf '\033[1;32m%s\033[0m\n' "$*"; }
warn() { printf '\033[1;33mWARNING: %s\033[0m\n' "$*" >&2; }
die() { printf '\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit "${2:-2}"; }
usage() { sed -n '2,/^$/p' "$0" | sed 's/^# \{0,1\}//'; exit 0; }
kv_get() { [[ -f $2 ]] && sed -n "s/^$1=//p" "$2" | tail -n1 || true; }

# ---------------------------------------------------------------- options
DOMAIN=${DOMAIN-}
LETSENCRYPT_EMAIL=${LETSENCRYPT_EMAIL-}
RENEW_DAYS=${RENEW_DAYS:-30}
API_PORT=${API_PORT:-8000}
FORCE=0
CHECK=0
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case $1 in
    --domain) DOMAIN=${2?}; shift 2 ;;
    --email) LETSENCRYPT_EMAIL=${2?}; shift 2 ;;
    --days) RENEW_DAYS=${2?}; shift 2 ;;
    --port) API_PORT=${2?}; shift 2 ;;
    --force) FORCE=1; shift ;;
    --check) CHECK=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h | --help) usage ;;
    *) die "Unknown option: $1 (see --help)" ;;
  esac
done

[[ $EUID -eq 0 ]] || die "Run as root: sudo bash $0 ..."
STORED_DOMAIN=$(kv_get DOMAIN "$STATE_FILE")
DOMAIN=${DOMAIN:-$STORED_DOMAIN}
LETSENCRYPT_EMAIL=${LETSENCRYPT_EMAIL:-$(kv_get LETSENCRYPT_EMAIL "$STATE_FILE")}
[[ -n $DOMAIN ]] || die "No domain given and none remembered. Use --domain app.cardiosens.ai"
[[ $DOMAIN =~ ^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)+$ ]] || die "Invalid domain: $DOMAIN"
[[ $RENEW_DAYS =~ ^[0-9]+$ ]] || die "--days must be a number"
[[ $API_PORT =~ ^[0-9]+$ ]] || die "--port must be a number"
CERT=$LE_LIVE/$DOMAIN/fullchain.pem

# ---------------------------------------------------------------- helpers
cert_end_epoch() { # $1 = PEM file
  local end
  end=$(openssl x509 -noout -enddate -in "$1" 2>/dev/null | cut -d= -f2) || return 1
  [[ -n $end ]] && date -d "$end" +%s
}

days_left() { echo $((($1 - $(date +%s)) / 86400)); }

show_cert() { # $1 = PEM file
  local end subject issuer names
  end=$(openssl x509 -noout -enddate -in "$1" | cut -d= -f2)
  subject=$(openssl x509 -noout -subject -in "$1" | sed 's/^subject= *//')
  issuer=$(openssl x509 -noout -issuer -in "$1" | sed 's/^issuer= *//')
  names=$(openssl x509 -noout -ext subjectAltName -in "$1" 2>/dev/null | tail -n +2 | sed 's/DNS://g; s/^ *//' || true)
  printf '  File:       %s\n  Subject:    %s\n  Names:      %s\n  Issuer:     %s\n  Expires:    %s (%s days left)\n' \
    "$1" "$subject" "${names:-?}" "$issuer" "$end" "$(days_left "$(cert_end_epoch "$1")")"
}

served_cert_end() { # expiry of the certificate nginx serves for DOMAIN on this machine
  echo | timeout 10 openssl s_client -connect 127.0.0.1:443 -servername "$DOMAIN" 2>/dev/null | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2
}

check_dns() {
  local resolved public
  resolved=$(getent ahostsv4 "$DOMAIN" 2>/dev/null | awk '{print $1}' | sort -u | tr '\n' ' ' || true)
  public=$(curl -fsS -4 --max-time 5 https://api.ipify.org 2>/dev/null || true)
  if [[ -z $resolved ]]; then
    warn "$DOMAIN does not resolve. Create an A record pointing at this server${public:+ ($public)} first"
  elif [[ -n $public && " $resolved" != *" $public "* ]]; then
    warn "$DOMAIN resolves to ${resolved% }, but this server's public IP is $public. Let's Encrypt will fail until DNS points here"
  else
    echo "  DNS:        $DOMAIN -> ${resolved% } (this server)"
  fi
}

auto_renewal() { # $1 = 1 to switch the timer on, 0 to only report
  if systemctl list-unit-files certbot.timer 2>/dev/null | grep -q certbot.timer; then
    [[ $1 -eq 1 ]] && { systemctl enable --now certbot.timer >/dev/null 2>&1 || true; }
    if ! systemctl is-active -q certbot.timer; then
      echo "  Auto-renew: certbot.timer is OFF (run this script without --check to switch it on)"
      return
    fi
    local next
    next=$(systemctl show certbot.timer -p NextElapseUSecRealtime --value 2>/dev/null || true)
    echo "  Auto-renew: certbot.timer on (twice a day; renews within 30 days of expiry)${next:+, next run $next}"
  else
    warn "certbot.timer not found: automatic renewal is off. Re-run this script from a cron job, e.g. weekly"
  fi
}

reload_nginx() {
  nginx -t -q || die "nginx configuration test failed; nothing reloaded" 1
  systemctl reload nginx
}

# ---------------------------------------------------------------- report
log "Certificate for $DOMAIN"
command -v openssl >/dev/null || die "openssl is not installed (apt-get install openssl)"
HAVE_CERT=0
if [[ -f $CERT ]]; then
  HAVE_CERT=1
  show_cert "$CERT"
  LEFT=$(days_left "$(cert_end_epoch "$CERT")")
else
  echo "  No certificate yet ($CERT)."
  LEFT=-1
fi
check_dns

if [[ $CHECK -eq 1 ]]; then
  if command -v systemctl >/dev/null; then auto_renewal 0; fi
  SERVED=$(served_cert_end || true)
  [[ -n $SERVED ]] && echo "  Served:     nginx on :443 presents a certificate valid until $SERVED"
  if [[ $HAVE_CERT -eq 1 && $LEFT -gt $RENEW_DAYS ]]; then
    ok "Valid for $LEFT more days (renewal threshold: $RENEW_DAYS)."
    exit 0
  fi
  warn "Certificate is $([[ $HAVE_CERT -eq 1 ]] && echo "expiring in $LEFT days" || echo missing). Run: sudo bash $0"
  exit 1
fi

command -v certbot >/dev/null && command -v nginx >/dev/null || NEED_SETUP=1

# ---------------------------------------------------------------- create (or new domain)
if [[ $HAVE_CERT -eq 0 || ${NEED_SETUP:-0} -eq 1 || $DOMAIN != "$STORED_DOMAIN" ]]; then
  [[ $DRY_RUN -eq 0 ]] || die "--dry-run needs an existing certificate to test renewal with"
  [[ -n $LETSENCRYPT_EMAIL || $HAVE_CERT -eq 1 ]] || die "The first certificate needs --email (Let's Encrypt account e-mail)"
  log "Requesting a certificate and switching nginx to HTTPS (nginx.sh)"
  ARGS=(--domain "$DOMAIN" --port "$API_PORT")
  [[ -n $LETSENCRYPT_EMAIL ]] && ARGS+=(--email "$LETSENCRYPT_EMAIL")
  bash "$SCRIPT_DIR/nginx.sh" "${ARGS[@]}"
  [[ -f $CERT ]] || die "No certificate was issued. Check DNS and that port 80 is reachable, then run this again" 1
  show_cert "$CERT"

# ---------------------------------------------------------------- renew
else
  # A certificate exists but nginx may still be on plain HTTP (e.g. an earlier request failed): rewrite the site.
  if [[ ! -f $SITE_CONF ]] || ! grep -q "ssl_certificate $LE_LIVE/$DOMAIN/fullchain.pem" "$SITE_CONF"; then
    log "nginx is not using this certificate yet; rewriting the site (nginx.sh)"
    bash "$SCRIPT_DIR/nginx.sh" --domain "$DOMAIN" --port "$API_PORT" ${LETSENCRYPT_EMAIL:+--email "$LETSENCRYPT_EMAIL"}
  fi
  if [[ $DRY_RUN -eq 1 ]]; then
    log "Testing renewal against Let's Encrypt staging (no changes)"
    certbot renew --cert-name "$DOMAIN" --dry-run || die "Renewal test failed. Check that http://$DOMAIN/.well-known/acme-challenge/ reaches this server" 1
    ok "Renewal test passed."
  elif [[ $FORCE -eq 1 || $LEFT -le $RENEW_DAYS ]]; then
    log "Renewing ($([[ $FORCE -eq 1 ]] && echo forced || echo "$LEFT days left"))"
    RENEW_ARGS=(renew --cert-name "$DOMAIN" --non-interactive)
    [[ $FORCE -eq 1 ]] && RENEW_ARGS+=(--force-renewal)
    certbot "${RENEW_ARGS[@]}" || die "Renewal failed. Try: sudo certbot renew --cert-name $DOMAIN --dry-run, and check DNS / port 80" 1
    show_cert "$CERT"
  else
    ok "Still valid for $LEFT days; nothing to renew (threshold $RENEW_DAYS days, --force to renew anyway)."
  fi
fi

# ---------------------------------------------------------------- apply and verify
if [[ $DRY_RUN -eq 0 ]]; then
  log "Reloading nginx"
  reload_nginx
fi
log "Checking"
auto_renewal $((1 - DRY_RUN))
WANT=$(openssl x509 -noout -enddate -in "$CERT" | cut -d= -f2)
SERVED=$(served_cert_end || true)
if [[ -z $SERVED ]]; then
  warn "Could not read the certificate from nginx on 127.0.0.1:443 (is nginx running with the HTTPS site?)"
elif [[ $SERVED != "$WANT" ]]; then
  warn "nginx serves a certificate valid until $SERVED, but the file says $WANT. Try: sudo systemctl restart nginx"
else
  echo "  Served:     nginx presents the current certificate (valid until $SERVED)"
fi
if curl -fsS --max-time 10 --resolve "$DOMAIN:443:127.0.0.1" "https://$DOMAIN/healthz" >/dev/null 2>&1; then
  ok "https://$DOMAIN is up with a valid certificate."
else
  warn "https://$DOMAIN/healthz did not answer through nginx (is the API service running? journalctl -u cardiosensai-api)"
fi
