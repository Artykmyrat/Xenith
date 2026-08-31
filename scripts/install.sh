#!/usr/bin/env bash
#
# Xenith installer for a fresh Debian/Ubuntu server.
#
# Installs Docker, builds (or pulls) the panel image, writes /opt/xenith/.env
# with a generated sudo password, optionally obtains a Let's Encrypt certificate
# so the panel is served over HTTPS, arranges for that certificate to be renewed,
# and starts everything under Docker.
#
#   curl -fsSL https://raw.githubusercontent.com/bugbusta/Xenith/main/scripts/install.sh -o install.sh
#   sudo bash install.sh --domain panel.example.com --email ops@example.com
#
# Run bare, with no configuration flags, it asks for those settings instead and
# prints the equivalent command at the end.
#
# Piping the script straight into bash works too, but only with --yes: stdin is
# then the script itself, and there is nothing left for a prompt to read.
#
# Run it again to upgrade. It keeps the existing .env and data, and takes the
# settings it is not given on the command line back out of that .env.

set -euo pipefail

INSTALL_DIR=/opt/xenith
DATA_DIR=/var/lib/marzban
LETSENCRYPT_DIR=/etc/letsencrypt
REPO_URL=https://github.com/bugbusta/Xenith.git
REPO_REF=main
IMAGE=xenith:local
IMAGE_SOURCE=build          # build | pull
PULL_IMAGE=ghcr.io/bugbusta/xenith:latest
SERVICE=xenith
DOMAIN=""
EMAIL=""
PANEL_PORT=8000
WITH_TLS=1
WITH_NGINX=0
RENEW_TIMER=1
ASSUME_YES=0

# Which settings the command line actually carried. Anything not set here is
# adopted from an existing .env instead of silently reverting to the default.
PORT_SET=0
NGINX_SET=0

# Whether any of it was configuration rather than just --yes or --ref. A run
# that carries none is the one worth asking questions about: the alternative
# is a panel quietly installed on localhost because nobody mentioned a domain.
CONFIGURED=0

# Shared by the questions and by the checks, so a value typed at a prompt is
# held to exactly what a value passed as a flag is.
DOMAIN_RE='^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)+$'
EMAIL_RE='^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$'

log()   { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn()  { printf '\033[1;33m warn\033[0m %s\n' "$*" >&2; }
die()   { printf '\033[1;31m error\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<USAGE
Xenith installer

Usage: install.sh [options]

Run without any of the settings below and it asks for them instead.

  --domain <host>     Domain that points at this server. Enables HTTPS and
                      subscription links. Without it the panel binds to
                      localhost only and you reach it through an SSH tunnel.
  --email <address>   Contact address for Let's Encrypt expiry notices.
  --port <port>       Port the panel listens on (default: ${PANEL_PORT}, or
                      whatever an existing .env already says).
  --pull              Use the published image (${PULL_IMAGE}) instead of
                      building from this repository.
  --ref <git-ref>     Branch or tag to build from (default: ${REPO_REF}).
  --no-tls            Skip certificate issuance even when --domain is given.
  --with-nginx        Let the panel manage the host's nginx. Mounts /etc/nginx
                      and shares the host's PID namespace, which the container
                      needs to signal nginx but which also removes process
                      isolation between the two.
  --no-renew-timer    Do not install the timer that renews the certificate.
                      You then have to renew it yourself before it expires.
  --yes               Do not ask for confirmation.
  --help              Show this message.
USAGE
}

# `shift 2` past a flag whose value is missing fails, and under `set -e` that
# exits with no message at all -- the installer just stops. Checking first is
# what turns it into a sentence the operator can act on.
need_value() {
  [[ $# -ge 2 && -n "${2:-}" && "${2:0:2}" != "--" ]] \
    || die "$1 needs a value (try --help)."
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --domain) need_value "$@"; DOMAIN="$2"; CONFIGURED=1; shift 2 ;;
    --email)  need_value "$@"; EMAIL="$2"; CONFIGURED=1; shift 2 ;;
    --port)   need_value "$@"; PANEL_PORT="$2"; PORT_SET=1; CONFIGURED=1; shift 2 ;;
    --pull)   IMAGE_SOURCE=pull; IMAGE="$PULL_IMAGE"; CONFIGURED=1; shift ;;
    --ref)    need_value "$@"; REPO_REF="$2"; CONFIGURED=1; shift 2 ;;
    --no-tls) WITH_TLS=0; CONFIGURED=1; shift ;;
    --with-nginx) WITH_NGINX=1; NGINX_SET=1; CONFIGURED=1; shift ;;
    --no-renew-timer) RENEW_TIMER=0; shift ;;
    --yes|-y) ASSUME_YES=1; shift ;;
    --help|-h) usage; exit 0 ;;
    *) die "Unknown option: $1 (try --help)" ;;
  esac
done

# ── checks the questions and the install both use ────────────────────────────

port_in_use() {
  ss -lnt 2>/dev/null | awk 'NR>1 {print $4}' | sed 's/.*[:.]//' | grep -qx "$1"
}

# Let's Encrypt validates over the address the domain resolves to, so a domain
# pointing somewhere else fails issuance with an error about the challenge
# rather than about DNS. Saying it here costs one lookup. Non-zero means the
# operator was told something, which is what lets a prompt offer to try again.
domain_points_here() {
  local domain="$1" resolved public_ip address
  resolved="$(getent ahostsv4 "$domain" 2>/dev/null | awk '{print $1; exit}')"

  if [[ -z "$resolved" ]]; then
    warn "$domain does not resolve yet. Issuance will fail until it does."
    return 1
  fi

  while read -r address; do
    [[ "$address" == "$resolved" ]] && return 0
  done < <(ip -4 -o addr show scope global 2>/dev/null | awk '{split($4,a,"/"); print a[1]}')

  public_ip="$(curl -fsS --max-time 5 http://api4.ipify.org 2>/dev/null \
    || curl -fsS --max-time 5 http://ipv4.icanhazip.com 2>/dev/null || true)"
  public_ip="${public_ip//[[:space:]]/}"
  [[ -n "$public_ip" && "$public_ip" == "$resolved" ]] && return 0

  warn "$domain resolves to $resolved, which is not an address of this server${public_ip:+ (${public_ip})}."
  warn "Issuance will fail unless that address forwards port 80 here."
  return 1
}

# ── reading the existing installation ────────────────────────────────────────

ENV_FILE="$INSTALL_DIR/.env"
COMPOSE_FILE="$INSTALL_DIR/docker-compose.yml"

# Values the way the panel's own reader takes them: the last assignment wins,
# an inline comment is not part of the value, and neither are the quotes. This
# matches env_enabled() in scripts/xenith deliberately -- the two files have to
# agree on what a setting says.
env_get() {
  local value
  [[ -f "$ENV_FILE" ]] || return 1
  value="$(sed -n -E "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*(.*)$/\1/p" "$ENV_FILE" | tail -1)"
  [[ -n "$value" ]] || return 1
  value="${value%%#*}"
  value="${value//[[:space:]\"\']/}"
  printf '%s' "$value"
}

env_has() {
  [[ -f "$ENV_FILE" ]] && grep -qE "^[[:space:]]*$1[[:space:]]*=" "$ENV_FILE"
}

# Replaces the last assignment of a key, or appends one when there is none.
env_set() {
  local key="$1" value="$2"
  if env_has "$key"; then
    sed -i -E "s|^([[:space:]]*$key[[:space:]]*=[[:space:]]*).*$|$key = $value|" "$ENV_FILE"
  else
    printf '%s = %s\n' "$key" "$value" >> "$ENV_FILE"
  fi
}

env_truthy() {
  case "$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')" in
    true|yes|y|on|t|1) return 0 ;;
    *) return 1 ;;
  esac
}

UPGRADE=0
if [[ -f "$ENV_FILE" ]]; then
  UPGRADE=1

  # Re-running is the documented upgrade path, and it keeps the existing .env.
  # The compose file, though, is rewritten from the command line every time, so
  # whatever lives in both places has to come back out of .env when the command
  # line does not carry it. Without this, an upgrade run quietly moves the panel
  # back to port 8000 and drops the nginx mounts it was installed with, while
  # .env still claims both.
  if (( ! PORT_SET )); then
    existing_port="$(env_get UVICORN_PORT || true)"
    [[ "$existing_port" =~ ^[0-9]+$ ]] && PANEL_PORT="$existing_port"
  fi
  if (( ! NGINX_SET )) && env_truthy "$(env_get NGINX_ENABLED || true)"; then
    WITH_NGINX=1
  fi
fi

# ── preflight ────────────────────────────────────────────────────────────────

[[ $EUID -eq 0 ]] || die "Run this as root (sudo bash install.sh ...)."
[[ -f /etc/debian_version ]] || warn "This installer targets Debian/Ubuntu. Continuing anyway."

if [[ -n "$DOMAIN" && ! "$DOMAIN" =~ $DOMAIN_RE ]]; then
  die "--domain must be a plain hostname, e.g. panel.example.com"
fi
if [[ -n "$EMAIL" && ! "$EMAIL" =~ $EMAIL_RE ]]; then
  die "--email must be an address Let's Encrypt will accept, e.g. ops@example.com"
fi
if [[ ! "$PANEL_PORT" =~ ^[0-9]+$ ]] || (( 10#$PANEL_PORT < 1 || 10#$PANEL_PORT > 65535 )); then
  die "--port must be a number between 1 and 65535"
fi
# Decimal from here on: a leading zero is octal to the shell's arithmetic, and
# it would otherwise reach .env and the printed URL as typed.
PANEL_PORT=$((10#$PANEL_PORT))
if [[ ! "$REPO_REF" =~ ^[A-Za-z0-9._/-]+$ ]]; then
  die "--ref must be a branch or tag name, e.g. main or v0.9.0"
fi
if [[ "$IMAGE_SOURCE" == pull && "$REPO_REF" != main ]]; then
  warn "--ref is ignored with --pull; the published image decides what is in it."
fi

# ── asking ───────────────────────────────────────────────────────────────────

# Every prompt reads from the terminal rather than stdin. When the script
# arrives through a pipe stdin is the script itself, and a read would consume
# the next line of it instead of an answer. Opened once, here, and used by the
# questions and by the final confirmation alike.
HAVE_TTY=0
if { exec 3</dev/tty; } 2>/dev/null; then
  HAVE_TTY=1
fi

# The prompt goes to stderr so that `$(ask ...)` captures the answer alone.
ask() {
  local prompt="$1" default="${2:-}" reply
  if [[ -n "$default" ]]; then
    printf '  %s [%s]: ' "$prompt" "$default" >&2
  else
    printf '  %s: ' "$prompt" >&2
  fi
  read -r reply <&3 || reply=""
  printf '%s' "${reply:-$default}"
}

ask_yes_no() {
  local prompt="$1" default="$2" reply hint
  [[ "$default" == y ]] && hint="Y/n" || hint="y/N"
  while true; do
    printf '  %s [%s]: ' "$prompt" "$hint" >&2
    read -r reply <&3 || reply=""
    case "$(printf '%s' "${reply:-$default}" | tr '[:upper:]' '[:lower:]')" in
      y|yes) return 0 ;;
      n|no)  return 1 ;;
      *) printf '  Please answer y or n.\n' >&2 ;;
    esac
  done
}

# What the answers would have been as a command line. Printed afterwards so the
# second server, or the same one rebuilt, does not have to be a conversation.
equivalent_command() {
  local cmd="sudo bash install.sh"
  [[ -n "$DOMAIN" ]] && cmd+=" --domain $DOMAIN"
  [[ -n "$EMAIL" ]] && cmd+=" --email $EMAIL"
  cmd+=" --port $PANEL_PORT"
  [[ "$IMAGE_SOURCE" == pull ]] && cmd+=" --pull"
  (( WITH_NGINX )) && cmd+=" --with-nginx"
  (( WITH_TLS )) || cmd+=" --no-tls"
  (( RENEW_TIMER )) || cmd+=" --no-renew-timer"
  printf '%s --yes' "$cmd"
}

ask_for_settings() {
  local answer

  cat >&2 <<'INTRO'

Nothing was given on the command line, so the questions come here. Enter takes
the value in brackets, and the equivalent flags are printed at the end so the
next server can skip all of this.

INTRO

  # Domain. Everything else about how reachable the panel is follows from it.
  printf 'A domain pointing at this server is what gets you HTTPS and working\nsubscription links. Left empty the panel listens on localhost only, and\nyou reach it through an SSH tunnel.\n\n' >&2
  while true; do
    DOMAIN="$(ask 'Domain, empty for localhost only')"
    [[ -z "$DOMAIN" ]] && break
    if [[ ! "$DOMAIN" =~ $DOMAIN_RE ]]; then
      warn "That is not a plain hostname. Example: panel.example.com"
      continue
    fi
    # Checked now rather than four minutes from now, when certbot would have
    # been the one to find out.
    domain_points_here "$DOMAIN" && break
    ask_yes_no "Use $DOMAIN anyway?" y && break
  done

  if [[ -n "$DOMAIN" ]]; then
    printf "\nLet's Encrypt sends expiry notices to this address. Empty registers\nwithout one, which works, but then nothing warns you if renewal stops.\n\n" >&2
    while true; do
      EMAIL="$(ask 'Email for expiry notices, empty to skip')"
      [[ -z "$EMAIL" || "$EMAIL" =~ $EMAIL_RE ]] && break
      warn "That does not look like an address. Example: ops@example.com"
    done
  fi

  printf '\nThe port the panel listens on. 443 keeps it out of the dashboard URL,\nbut only works if nothing else on this server wants 443.\n\n' >&2
  while true; do
    # Held in answer rather than PANEL_PORT: a rejected value must not come
    # back as the default that the next question offers.
    answer="$(ask 'Panel port' "$PANEL_PORT")"
    if [[ ! "$answer" =~ ^[0-9]+$ ]] || (( 10#$answer < 1 || 10#$answer > 65535 )); then
      warn "A number between 1 and 65535."
      continue
    fi
    if port_in_use "$answer"; then
      warn "Something is already listening on port $answer."
      ask_yes_no "Choose a different port?" y && continue
    fi
    PANEL_PORT=$((10#$answer))
    break
  done
  PORT_SET=1

  printf '\nThe image can be built from source here, which takes a few minutes and\nabout 6GB, or pulled ready-made. Building is the default because it is\nthe one guaranteed to match this repository.\n\n' >&2
  if ask_yes_no 'Build the image from source?' y; then
    IMAGE_SOURCE=build
    IMAGE=xenith:local
  else
    IMAGE_SOURCE=pull
    IMAGE="$PULL_IMAGE"
  fi

  printf "\nThe panel can edit and reload this host's nginx. That mounts /etc/nginx\nand shares the host's PID namespace, which removes the process isolation\nbetween the panel and the rest of the machine. Say no unless you want it.\n\n" >&2
  if ask_yes_no "Let the panel manage this host's nginx?" n; then
    WITH_NGINX=1
    NGINX_SET=1
  fi
}

# Asked only of a fresh install driven by nobody: an upgrade takes its settings
# from the .env it is keeping, and a run carrying flags is already being told
# what to do.
if (( ! ASSUME_YES )) && (( ! CONFIGURED )) && (( ! UPGRADE )) && (( HAVE_TTY )); then
  ask_for_settings
  ASKED=1
else
  ASKED=0
fi

if (( ! ASKED )) && (( ! UPGRADE )) && [[ -z "$DOMAIN" ]]; then
  warn "No --domain, so this installs a panel that only listens on localhost."
fi

# ── preflight, part two ──────────────────────────────────────────────────────

# A build unpacks the node and python layers before it produces anything, and
# running out of room halfway leaves a half-written image and an error that
# does not mention disk at all.
if [[ "$IMAGE_SOURCE" == build ]]; then required_mb=6144; else required_mb=3072; fi
available_mb="$(df -Pm /var 2>/dev/null | awk 'NR==2 {print $4}')"
if [[ "$available_mb" =~ ^[0-9]+$ ]] && (( available_mb < required_mb )); then
  die "Only ${available_mb}MB free on /var; this needs about ${required_mb}MB. Free some space and re-run."
fi

log "Installing Xenith"
echo "    directory : $INSTALL_DIR"
echo "    data      : $DATA_DIR"
echo "    image     : $IMAGE ($IMAGE_SOURCE)"
echo "    domain    : ${DOMAIN:-<none, localhost only>}"
echo "    port      : $PANEL_PORT"
(( UPGRADE )) && echo "    mode      : upgrade, keeping $ENV_FILE"

if (( ! ASSUME_YES )); then
  (( HAVE_TTY )) || die "No terminal to ask on. Re-run with --yes, or save the script and run it directly."
  # Enter means yes only when the questions were just answered, since then it
  # is the last of a series. A run driven by flags keeps the old default of no.
  (( ASKED )) && confirm_default=y || confirm_default=n
  ask_yes_no "Continue?" "$confirm_default" || die "Aborted."
fi

if (( ASKED )); then
  log "Next time, without the questions:"
  echo "    $(equivalent_command)"
fi

# Nothing else reads from the terminal.
(( HAVE_TTY )) && exec 3<&- || true

# ── dependencies ─────────────────────────────────────────────────────────────

log "Installing packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
# iproute2 carries ss(8). Without it the port checks below find nothing and
# quietly pass, which is worse than not having them.
apt-get install -y -qq ca-certificates curl git openssl iproute2 >/dev/null

if ! command -v docker >/dev/null 2>&1; then
  log "Installing Docker"
  curl -fsSL https://get.docker.com | sh
else
  log "Docker is already installed"
fi

docker compose version >/dev/null 2>&1 || die "The Docker Compose plugin is missing. Install docker-compose-plugin and re-run."

systemctl enable --now docker >/dev/null 2>&1 || true

mkdir -p "$INSTALL_DIR" "$DATA_DIR" "$LETSENCRYPT_DIR"

# ── what is already holding the ports ────────────────────────────────────────

# The container runs with host networking, so a port conflict is not a compose
# error -- the panel starts, fails to bind, and the only sign is a restart loop
# a minute later. Checked only on a fresh install: on an upgrade the port is
# held by the panel itself.
if (( ! UPGRADE )) && [[ ! -f "$COMPOSE_FILE" ]] && port_in_use "$PANEL_PORT"; then
  die "Port $PANEL_PORT is already in use. Stop what is holding it, or pass --port."
fi

# ── image ────────────────────────────────────────────────────────────────────

if [[ "$IMAGE_SOURCE" == "build" ]]; then
  if [[ -d "$INSTALL_DIR/src/.git" ]]; then
    log "Updating sources ($REPO_REF)"
    git -C "$INSTALL_DIR/src" fetch --depth 1 origin "$REPO_REF"
    git -C "$INSTALL_DIR/src" checkout -q FETCH_HEAD
  else
    log "Cloning sources ($REPO_REF)"
    rm -rf "$INSTALL_DIR/src"
    git clone --depth 1 --branch "$REPO_REF" "$REPO_URL" "$INSTALL_DIR/src"
  fi

  log "Building the image (this takes a few minutes)"
  docker build -t "$IMAGE" "$INSTALL_DIR/src"
else
  log "Pulling $IMAGE"
  docker pull "$IMAGE"
fi

# ── certificate ──────────────────────────────────────────────────────────────

CERT_DIR="$LETSENCRYPT_DIR/live/$DOMAIN"
HAVE_CERT=0

if [[ -n "$DOMAIN" && $WITH_TLS -eq 1 ]]; then
  if [[ -f "$CERT_DIR/fullchain.pem" ]]; then
    log "Reusing the existing certificate for $DOMAIN"
    HAVE_CERT=1
  else
    log "Obtaining a certificate for $DOMAIN"
    domain_points_here "$DOMAIN" || true

    if port_in_use 80; then
      warn "Something is already listening on port 80; certbot needs it free."
      warn "Stop it and re-run, or issue the certificate yourself and set UVICORN_SSL_* in $ENV_FILE."
    fi

    certbot_args=(certonly --standalone --non-interactive --agree-tos --keep-until-expiring -d "$DOMAIN")
    if [[ -n "$EMAIL" ]]; then
      certbot_args+=(--email "$EMAIL")
    else
      certbot_args+=(--register-unsafely-without-email)
    fi

    # certbot ships inside the panel image, so the host stays clean.
    if docker run --rm -p 80:80 -v "$LETSENCRYPT_DIR:/etc/letsencrypt" --entrypoint certbot "$IMAGE" "${certbot_args[@]}"; then
      HAVE_CERT=1
    else
      warn "Certificate issuance failed. Installing without HTTPS; fix DNS and re-run, or use the Certificates screen later."
    fi
  fi
fi

# ── configuration ────────────────────────────────────────────────────────────

subscription_prefix() {
  if (( PANEL_PORT == 443 )); then
    printf 'https://%s' "$DOMAIN"
  else
    printf 'https://%s:%s' "$DOMAIN" "$PANEL_PORT"
  fi
}

SUDO_PASSWORD=""

if (( UPGRADE )); then
  log "Keeping the existing $ENV_FILE"

  if (( PORT_SET )) && [[ "$(env_get UVICORN_PORT || true)" != "$PANEL_PORT" ]]; then
    log "Setting UVICORN_PORT to $PANEL_PORT"
    env_set UVICORN_PORT "$PANEL_PORT"
  fi

  # A first run whose certificate failed leaves an .env with no SSL lines, and
  # the panel then binds to localhost however good the domain is. The advice for
  # that case is to fix DNS and run the installer again -- which only helps if
  # the second run is allowed to add the lines the first one could not.
  if (( HAVE_CERT )) && ! env_has UVICORN_SSL_CERTFILE; then
    log "Adding the certificate to $ENV_FILE"
    env_set UVICORN_SSL_CERTFILE "\"$CERT_DIR/fullchain.pem\""
    env_set UVICORN_SSL_KEYFILE "\"$CERT_DIR/privkey.pem\""
    env_has XRAY_SUBSCRIPTION_URL_PREFIX || env_set XRAY_SUBSCRIPTION_URL_PREFIX "\"$(subscription_prefix)\""
  fi
else
  log "Writing $ENV_FILE"
  SUDO_PASSWORD="$(openssl rand -base64 24 | tr -d '/+=' | cut -c1-24)"
  SUBSCRIPTION_PATH="$(openssl rand -hex 6)"

  {
    echo "# Generated by scripts/install.sh on $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "SUDO_USERNAME = \"admin\""
    echo "SUDO_PASSWORD = \"$SUDO_PASSWORD\""
    echo
    echo "UVICORN_HOST = \"0.0.0.0\""
    echo "UVICORN_PORT = $PANEL_PORT"
    if (( HAVE_CERT )); then
      echo "UVICORN_SSL_CERTFILE = \"$CERT_DIR/fullchain.pem\""
      echo "UVICORN_SSL_KEYFILE = \"$CERT_DIR/privkey.pem\""
    fi
    echo
    echo "# Subscription links live under this path; the random value keeps them"
    echo "# from being guessed."
    echo "XRAY_SUBSCRIPTION_PATH = \"$SUBSCRIPTION_PATH\""
    if [[ -n "$DOMAIN" ]]; then
      echo "XRAY_SUBSCRIPTION_URL_PREFIX = \"$(subscription_prefix)\""
    fi
    echo
    echo "# TLS certificates from the Certificates screen."
    echo "CERTBOT_ENABLED = True"
    [[ -n "$EMAIL" ]] && echo "CERTBOT_EMAIL = \"$EMAIL\""
    echo
    echo "# Set this to your reverse proxy's address if you put one in front of"
    echo "# the panel; otherwise forwarding headers are ignored, which is what"
    echo "# you want when clients reach the panel directly."
    echo "# TRUSTED_PROXIES = '127.0.0.1,::1'"
    echo
    echo "# Login brute-force protection."
    echo "LOGIN_RATE_LIMIT_ATTEMPTS = 5"
    echo "LOGIN_RATE_LIMIT_WINDOW = 300"
    echo
    echo "# Kernel tuning from the System settings screen. Uncommenting this is"
    echo "# the whole procedure: xenith restart then brings the container up"
    echo "# with the privileges that writing /proc/sys needs."
    echo "# SYSCTL_ENABLED = True"
  } > "$ENV_FILE"

  chmod 600 "$ENV_FILE"
fi

log "Writing $COMPOSE_FILE"
if (( WITH_NGINX )); then
  # Sharing the host's PID namespace is what lets the panel signal the host's
  # nginx master; without it a reload from the panel reaches nothing.
  NGINX_COMPOSE="    pid: host"
  NGINX_VOLUMES="      - /etc/nginx:/etc/nginx
      - /var/www:/var/www
      - /var/log/nginx:/var/log/nginx
      - /run:/run"
else
  NGINX_COMPOSE=""
  NGINX_VOLUMES=""
fi

cat > "$COMPOSE_FILE" <<COMPOSE
services:
  $SERVICE:
    image: $IMAGE
    restart: always
    env_file: .env
    network_mode: host
$NGINX_COMPOSE
    # A proxy holds two descriptors per connection; the Docker default of 1024
    # runs out long before anything else does.
    ulimits:
      nofile:
        soft: 1048576
        hard: 1048576
    volumes:
      - $DATA_DIR:/var/lib/marzban
      - $LETSENCRYPT_DIR:/etc/letsencrypt
$NGINX_VOLUMES
COMPOSE

# Strip the blank lines the empty substitutions leave behind.
sed -i '/^[[:space:]]*$/d' "$COMPOSE_FILE"

# Kernel tuning needs privileges the main compose file deliberately withholds.
# Keeping them in an override file is what makes turning the feature on one
# line in .env: the xenith wrapper adds this file whenever SYSCTL_ENABLED is
# set, so nobody has to edit compose by hand.
cat > "$INSTALL_DIR/docker-compose.sysctl.yml" <<'COMPOSE'
# Written by Xenith. Used automatically while SYSCTL_ENABLED is on in .env,
# ignored otherwise. The installer and `xenith` both replace this file.
services:
  xenith:
    # Docker mounts /proc/sys read-only, and the System settings screen cannot
    # change a kernel parameter without writing it.
    privileged: true
    volumes:
      # So a change survives a reboot: the host reads this directory at boot.
      - /etc/sysctl.d:/etc/sysctl.d
COMPOSE

# env_set replaces rather than appends, so re-running cannot leave the file with
# a growing stack of the same setting.
if (( WITH_NGINX )); then
  env_set NGINX_ENABLED True
fi

# ── host command ─────────────────────────────────────────────────────────────

log "Installing the xenith command"
if [[ -f "$INSTALL_DIR/src/scripts/xenith" ]]; then
  install -m 755 "$INSTALL_DIR/src/scripts/xenith" /usr/local/bin/xenith
else
  wrapper_tmp="$(mktemp)"
  curl -fsSL "https://raw.githubusercontent.com/bugbusta/Xenith/$REPO_REF/scripts/xenith" -o "$wrapper_tmp"
  # Only replace the command once the download looks like the real thing; a
  # captive portal or a 404 page would otherwise land in /usr/local/bin.
  grep -q "^INSTALL_DIR=" "$wrapper_tmp" || die "Downloaded xenith script does not look right; leaving the existing one alone."
  install -m 755 "$wrapper_tmp" /usr/local/bin/xenith
  rm -f "$wrapper_tmp"
fi

# ── certificate renewal ──────────────────────────────────────────────────────

# A Let's Encrypt certificate lasts 90 days and nothing inside the container
# renews it: certbot's systemd timer belongs to the host package, which this
# install deliberately does not use. Without something here every panel
# installed with TLS stops answering a quarter of a year later.
install_renewal() {
  cat > /usr/local/bin/xenith-renew-cert <<RENEW
#!/usr/bin/env bash
#
# Written by the Xenith installer. Renews the panel's certificates and restarts
# it only when one was actually replaced: uvicorn reads the certificate once, at
# startup, so a renewal it does not see changes nothing -- and a daily restart
# for a renewal that did not happen drops every connection for no reason.

set -euo pipefail

# The deploy hook runs inside the container, where the data directory is always
# /var/lib/marzban. The host reads the same file back through the bind mount.
CONTAINER_MARKER=/var/lib/marzban/.certificate-renewed
HOST_MARKER=$DATA_DIR/.certificate-renewed

rm -f "\$HOST_MARKER"

# A failure here is worth a log line, not a non-zero exit that the timer would
# report as a broken unit: the next run in twelve hours will try again, and
# there are thirty days of room before the certificate actually expires.
if ! xenith certbot renew --quiet --deploy-hook "touch \$CONTAINER_MARKER"; then
  echo "xenith-renew-cert: renewal failed; will retry on the next run" >&2
  exit 0
fi

if [[ -f "\$HOST_MARKER" ]]; then
  rm -f "\$HOST_MARKER"
  xenith restart
fi
RENEW
  chmod 755 /usr/local/bin/xenith-renew-cert

  if command -v systemctl >/dev/null 2>&1 && [[ -d /etc/systemd/system ]]; then
    cat > /etc/systemd/system/xenith-cert-renew.service <<'UNIT'
[Unit]
Description=Renew Xenith's TLS certificates
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
ExecStart=/usr/local/bin/xenith-renew-cert
UNIT

    cat > /etc/systemd/system/xenith-cert-renew.timer <<'UNIT'
[Unit]
Description=Renew Xenith's TLS certificates twice a day

[Timer]
# Twice a day is what certbot's own packaging uses. A certificate is only
# replaced inside its last thirty days, so the extra runs cost one exec each
# and leave a month of room for a failing one to be noticed.
OnCalendar=*-*-* 00,12:00:00
RandomizedDelaySec=1h
Persistent=true

[Install]
WantedBy=timers.target
UNIT

    systemctl daemon-reload
    if systemctl enable --now xenith-cert-renew.timer >/dev/null 2>&1; then
      log "Certificate renewal runs twice a day (systemctl list-timers xenith-cert-renew.timer)"
    else
      warn "Could not enable xenith-cert-renew.timer. Renew from the Certificates screen instead."
    fi
  else
    # No systemd: cron is the other thing a Debian server always has.
    cat > /etc/cron.d/xenith-cert-renew <<'CRON'
# Written by the Xenith installer. Renews the panel's certificates.
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
17 0,12 * * * root /usr/local/bin/xenith-renew-cert
CRON
    chmod 644 /etc/cron.d/xenith-cert-renew
    log "Certificate renewal runs twice a day (/etc/cron.d/xenith-cert-renew)"
  fi
}

if (( HAVE_CERT )) && (( RENEW_TIMER )); then
  install_renewal
elif (( HAVE_CERT )); then
  warn "Renewal timer skipped. The certificate expires in 90 days; renew it from the Certificates screen."
fi

# ── start ────────────────────────────────────────────────────────────────────

log "Starting the panel"
cd "$INSTALL_DIR"
docker compose up -d

# What the container is doing, across compose versions that disagree about the
# flags for asking.
container_state() {
  local cid
  cid="$(docker compose ps -q "$SERVICE" 2>/dev/null | head -1)"
  [[ -n "$cid" ]] || { printf 'missing'; return; }
  docker inspect -f '{{.State.Status}}' "$cid" 2>/dev/null || printf 'unknown'
}

scheme="http"
(( HAVE_CERT )) && scheme="https"
host="${DOMAIN:-localhost}"
(( HAVE_CERT )) || host="localhost"

log "Waiting for the panel to answer"
ready=0
crashed=0
for _ in $(seq 1 30); do
  if curl -fsk --max-time 2 "$scheme://127.0.0.1:$PANEL_PORT/dashboard/" >/dev/null 2>&1; then
    ready=1
    break
  fi
  # A container that has already exited or is looping is not going to start
  # answering, and waiting the full minute for it only delays the logs that
  # say why.
  case "$(container_state)" in
    exited|restarting|dead) crashed=1; break ;;
  esac
  sleep 2
done

echo
if (( ready )); then
  log "Xenith is running"
else
  if (( crashed )); then
    warn "The panel is not staying up. Its last lines:"
  else
    warn "The panel did not answer in time. Its last lines:"
  fi
  docker compose logs --tail=20 "$SERVICE" 2>&1 | sed 's/^/    /' >&2 || true
  warn "Full logs: xenith logs -f"
fi

if { (( PANEL_PORT == 443 )) && [[ "$scheme" == "https" ]]; } || { (( PANEL_PORT == 80 )) && [[ "$scheme" == "http" ]]; }; then
  echo "    dashboard : $scheme://$host/dashboard/"
else
  echo "    dashboard : $scheme://$host:$PANEL_PORT/dashboard/"
fi
if [[ -n "$SUDO_PASSWORD" ]]; then
  echo "    username  : admin"
  echo "    password  : $SUDO_PASSWORD"
  echo
  echo "    Credentials are stored in $ENV_FILE — save them now."
else
  echo "    username  : see SUDO_USERNAME in $ENV_FILE"
fi

if ! (( HAVE_CERT )); then
  echo
  warn "No certificate configured, so the panel only listens on localhost."
  warn "Reach it with:  ssh -L $PANEL_PORT:localhost:$PANEL_PORT root@<this-server>"
  warn "Then re-run with --domain, or issue a certificate from the Certificates screen."
fi

echo
echo "    logs      : xenith logs -f"
echo "    restart   : xenith restart"
echo "    cli       : xenith cli admin create --sudo"
echo "    update    : xenith update"
