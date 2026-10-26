#!/usr/bin/env bash
# Set up nginx in front of the CardiosensAI API, with HTTPS from Let's Encrypt.
#
#   sudo bash scripts/nginx.sh                                                        # plain HTTP on the server IP
#   sudo bash scripts/nginx.sh --domain app.cardiosens.ai --email ops@cardiosens.ai   # HTTPS
#
# deploy.sh runs this for you. Run it on its own to add HTTPS once the domain's DNS points
# at the server, or to rewrite the nginx config. The domain and e-mail are remembered in
# /etc/cardiosensai/deploy.env, so `sudo bash scripts/nginx.sh` alone re-applies the last settings.
#
# What it sets up:
#   /etc/nginx/sites-available/cardiosensai   everything proxied to the API on 127.0.0.1:PORT, which serves the
#                                             web app at / and the API at /v1. HTTP redirects to HTTPS
#                                             once a certificate exists
#   Let's Encrypt certificate (webroot)       renewed by certbot's timer; nginx reloads after each renewal
#   API_BASE_URL, WEB_BASE_URL in backend.env  switched to https://DOMAIN once the certificate is in place
#
# Options (each can also be given as the environment variable in brackets):
#   --domain NAME      Public host name, e.g. app.cardiosens.ai [DOMAIN]. Empty: plain HTTP on any name or the IP.
#   --email ADDRESS    Let's Encrypt account e-mail; running with it accepts the Let's Encrypt terms [LETSENCRYPT_EMAIL]
#   --port N           Port the API listens on at 127.0.0.1 (default 8000) [API_PORT]
#   --no-tls           Don't request a certificate, even with a domain
#   -h, --help         Show this help

set -Eeuo pipefail
trap 'echo "nginx.sh: failed at line $LINENO: $BASH_COMMAND" >&2' ERR

CONF_DIR=/etc/cardiosensai
ENV_FILE=$CONF_DIR/backend.env
STATE_FILE=$CONF_DIR/deploy.env
SITE_CONF=/etc/nginx/sites-available/cardiosensai
ACME_ROOT=/var/www/letsencrypt
SERVICE=cardiosensai-api

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

# ---------------------------------------------------------------- options
DOMAIN=${DOMAIN-__unset__}
LETSENCRYPT_EMAIL=${LETSENCRYPT_EMAIL-__unset__}
API_PORT=${API_PORT:-8000}
TLS=1

while [[ $# -gt 0 ]]; do
  case $1 in
    --domain) DOMAIN=${2?}; shift 2 ;;
    --email) LETSENCRYPT_EMAIL=${2?}; shift 2 ;;
    --port) API_PORT=${2?}; shift 2 ;;
    --no-tls) TLS=0; shift ;;
    -h | --help) usage ;;
    *) die "Unknown option: $1 (see --help)" ;;
  esac
done

[[ $EUID -eq 0 ]] || die "Run as root: sudo bash $0 ..."
[[ $DOMAIN == __unset__ ]] && DOMAIN=$(kv_get DOMAIN "$STATE_FILE")
[[ $LETSENCRYPT_EMAIL == __unset__ ]] && LETSENCRYPT_EMAIL=$(kv_get LETSENCRYPT_EMAIL "$STATE_FILE")
[[ -z $DOMAIN || $DOMAIN =~ ^[A-Za-z0-9.-]+$ ]] || die "Invalid --domain: $DOMAIN"
[[ $API_PORT =~ ^[0-9]+$ ]] || die "--port must be a number"
CERT_DIR=/etc/letsencrypt/live/$DOMAIN
if [[ -n $DOMAIN && $TLS -eq 1 && -z $LETSENCRYPT_EMAIL && ! -f $CERT_DIR/fullchain.pem ]]; then
  die "HTTPS needs --email for Let's Encrypt (or pass --no-tls)"
fi

# ---------------------------------------------------------------- packages
if ! command -v nginx >/dev/null 2>&1 || ! command -v certbot >/dev/null 2>&1; then
  log "Installing nginx and certbot"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -q
  apt-get install -y -q --no-install-recommends nginx certbot ca-certificates curl
fi
install -d -m 0755 $ACME_ROOT

# ---------------------------------------------------------------- config
write_nginx() { # $1 = 1 to include the HTTPS server
  local https=$1 name=${DOMAIN:-_}
  local default=""
  [[ -z $DOMAIN ]] && default=" default_server"
  {
    cat <<EOF
# Managed by nginx.sh (run by deploy.sh); changes here are overwritten on the next run.
upstream cardiosensai_api {
    server 127.0.0.1:$API_PORT;
    keepalive 16;
}

EOF
    if [[ $https -eq 1 ]]; then
      cat <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name $name;
    location /.well-known/acme-challenge/ { root $ACME_ROOT; }
    location / { return 301 https://\$host\$request_uri; }
}

server {
    listen 443 ssl;
    listen [::]:443 ssl;
    http2 on;
    server_name $name;
    ssl_certificate $CERT_DIR/fullchain.pem;
    ssl_certificate_key $CERT_DIR/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_cache shared:cardiosensai_tls:10m;
    ssl_session_timeout 1d;
    ssl_session_tickets off;
EOF
    else
      cat <<EOF
server {
    listen 80$default;
    listen [::]:80$default;
    server_name $name;
    location /.well-known/acme-challenge/ { root $ACME_ROOT; }
EOF
    fi
    cat <<EOF

    server_tokens off;
    client_max_body_size 10m;   # ECG uploads (10 min at 1 kHz) are ~1.6 MB
    gzip on;
    gzip_types text/css application/javascript image/svg+xml;   # site files only; API JSON stays uncompressed


    location / {
        proxy_pass http://cardiosensai_api;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-For \$remote_addr;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header X-Request-ID \$request_id;
        proxy_read_timeout 60s;
    }
}
EOF
  } >$SITE_CONF
  ln -sf $SITE_CONF /etc/nginx/sites-enabled/cardiosensai
  rm -f /etc/nginx/sites-enabled/default
  nginx -t -q
  systemctl reload-or-restart nginx
}

log "Configuring nginx"
systemctl enable --now nginx
HTTPS=0
if [[ -n $DOMAIN && -f $CERT_DIR/fullchain.pem ]]; then
  HTTPS=1
elif [[ -n $DOMAIN && $TLS -eq 1 ]]; then
  write_nginx 0 # serves the ACME challenge
  log "Requesting a Let's Encrypt certificate for $DOMAIN"
  if certbot certonly --webroot -w $ACME_ROOT -d "$DOMAIN" --email "$LETSENCRYPT_EMAIL" \
    --agree-tos --no-eff-email --non-interactive --keep-until-expiring; then
    HTTPS=1
  else
    warn "Certificate request failed. Check that $DOMAIN points at this server and port 80 is open, then re-run: sudo bash $0"
  fi
fi
write_nginx $HTTPS

install -d /etc/letsencrypt/renewal-hooks/deploy
cat >/etc/letsencrypt/renewal-hooks/deploy/cardiosensai-reload-nginx <<'EOF'
#!/bin/sh
systemctl reload nginx
EOF
chmod 755 /etc/letsencrypt/renewal-hooks/deploy/cardiosensai-reload-nginx

if command -v ufw >/dev/null 2>&1 && ufw status | grep -q '^Status: active'; then
  ufw allow 80/tcp comment http >/dev/null
  ufw allow 443/tcp comment https >/dev/null
fi

# ---------------------------------------------------------------- backend settings
if [[ -d $CONF_DIR ]]; then
  kv_set DOMAIN "$DOMAIN" "$STATE_FILE"
  kv_set LETSENCRYPT_EMAIL "$LETSENCRYPT_EMAIL" "$STATE_FILE"
  chmod 0600 "$STATE_FILE"
fi
if [[ -n $DOMAIN && -f $ENV_FILE ]]; then
  # Signed report links and invite URLs are built from API_BASE_URL, links in emails from WEB_BASE_URL.
  BASE_URL="http://$DOMAIN"
  [[ $HTTPS -eq 1 ]] && BASE_URL="https://$DOMAIN"
  if [[ $(kv_get API_BASE_URL "$ENV_FILE") != "$BASE_URL" || $(kv_get WEB_BASE_URL "$ENV_FILE") != "$BASE_URL" ]]; then
    kv_set API_BASE_URL "$BASE_URL" "$ENV_FILE"
    kv_set WEB_BASE_URL "$BASE_URL" "$ENV_FILE"
    systemctl try-restart $SERVICE
  fi
fi

# ---------------------------------------------------------------- check
if [[ $HTTPS -eq 1 ]]; then
  VIA_NGINX=(--resolve "$DOMAIN:443:127.0.0.1")
  NGINX_URL="https://$DOMAIN"
else
  VIA_NGINX=(-H "Host: ${DOMAIN:-localhost}")
  NGINX_URL="http://127.0.0.1"
fi
ok=0
for _ in $(seq 15); do
  if curl -fsS --max-time 3 "${VIA_NGINX[@]}" "$NGINX_URL/healthz" >/dev/null 2>&1; then ok=1; break; fi
  sleep 1
done
if [[ $ok -eq 1 ]]; then
  echo "nginx is serving ${DOMAIN:-this server} over $([[ $HTTPS -eq 1 ]] && echo HTTPS || echo HTTP) and reaching the API."
else
  warn "nginx is configured, but the API on 127.0.0.1:$API_PORT isn't answering yet (systemctl status $SERVICE)"
fi
