#!/usr/bin/env sh
# =============================================================================
# BAUER GROUP NATS - Custom Entrypoint
# =============================================================================
# Wraps the official nats-server with four concerns:
#   1. Fail-fast validation that operator/JWT material is present
#   2. Client TLS certificate provisioning (layered: byo / managed / self-signed)
#   3. Env-driven config rendering (nats-server.conf + conf.d via envsubst)
#   4. Privilege drop to the 'nats' user (provisioning needs root to chown)
#
# POSIX sh (Alpine busybox ash). Runs as root so it can write certs/config and
# chown them, then drops to 'nats' via su-exec for the server process itself.
# =============================================================================
set -eu

ETC="/etc/nats"
CONF_DIR="${ETC}/conf.d"
CERT_DIR="${ETC}/certs"
DATA_DIR="/data"

# --- Defaults (also declared in the Dockerfile ENV block) --------------------
: "${NATS_SERVER_NAME:=$(hostname)}"
: "${NATS_CLUSTER_NAME:=bauergroup}"
: "${NATS_ROUTE_USER:=route}"
: "${NATS_ROUTE_PASSWORD:=route}"
: "${NATS_JS_MAX_MEM:=1G}"
: "${NATS_JS_MAX_FILE:=10G}"
: "${NATS_MAX_PAYLOAD:=8MB}"
: "${NATS_MAX_CONNECTIONS:=65536}"
: "${NATS_TLS_MODE:=selfsigned}"
: "${NATS_TLS_VERIFY:=false}"
: "${NATS_TLS_CN:=${NATS_SERVER_NAME}}"
: "${NATS_TLS_MANAGED_WAIT:=0}"
: "${NATS_RUN_AS:=nats}"
export NATS_SERVER_NAME NATS_CLUSTER_NAME NATS_ROUTE_USER NATS_ROUTE_PASSWORD \
       NATS_JS_MAX_MEM NATS_JS_MAX_FILE NATS_MAX_PAYLOAD NATS_MAX_CONNECTIONS \
       NATS_TLS_VERIFY

log() { printf '%s\n' "$*"; }

banner() {
  log "============================================="
  log " BAUER GROUP NATS"
  log "============================================="
  log "Server name         : ${NATS_SERVER_NAME}"
  log "Cluster name        : ${NATS_CLUSTER_NAME}"
  log "Timezone            : ${TZ:-Etc/UTC}"
  log "JetStream store      : ${DATA_DIR}/jetstream (mem=${NATS_JS_MAX_MEM} file=${NATS_JS_MAX_FILE})"
  log "Max payload          : ${NATS_MAX_PAYLOAD}"
  log "TLS mode (client)    : ${NATS_TLS_MODE} (verify=${NATS_TLS_VERIFY})"
  log "Auth                 : operator/JWT, MEMORY resolver"
  log "============================================="
}

# --- Fail fast on missing operator/JWT material ------------------------------
require_operator() {
  if [ -z "${NATS_OPERATOR_JWT:-}" ] || [ -z "${NATS_SYS_ACCOUNT_ID:-}" ]; then
    log "ERROR: operator/JWT material is missing (NATS_OPERATOR_JWT / NATS_SYS_ACCOUNT_ID empty)."
    log "       Run 'python scripts/generate-credentials.py' once before starting the stack."
    log "       It bootstraps the operator + SYS/APP accounts and fills the public JWTs in .env."
    exit 1
  fi
}

# --- TLS ---------------------------------------------------------------------
# A single self-signed cert is SHARED by all three nodes (same nats-certs
# volume). The SAN covers every node hostname so a client connecting to any
# node validates against the same CA. Generation is guarded by an atomic
# mkdir-lock so exactly one node generates on cold boot; the others wait.
generate_shared_selfsigned() {
  lock="${CERT_DIR}/.genlock"
  if mkdir "${lock}" 2>/dev/null; then
    log "- Generating shared self-signed TLS certificate (CN=${NATS_TLS_CN}) -"
    openssl req -nodes -x509 -newkey rsa:4096 \
      -keyout "${CERT_DIR}/key.pem.tmp" \
      -out "${CERT_DIR}/cert.pem.tmp" \
      -sha256 -days 3650 \
      -subj "/C=DE/ST=BY/L=Cham/O=BAUER GROUP/OU=IT/CN=${NATS_TLS_CN}/emailAddress=info@bauer-group.com" \
      -addext "subjectAltName=DNS:nats-1,DNS:nats-2,DNS:nats-3,DNS:${NATS_TLS_CN},DNS:localhost,IP:127.0.0.1"
    cp "${CERT_DIR}/cert.pem.tmp" "${CERT_DIR}/ca.pem.tmp"
    # Atomic publish: waiters only ever see complete files.
    mv "${CERT_DIR}/key.pem.tmp"  "${CERT_DIR}/key.pem"
    mv "${CERT_DIR}/cert.pem.tmp" "${CERT_DIR}/cert.pem"
    mv "${CERT_DIR}/ca.pem.tmp"   "${CERT_DIR}/ca.pem"
  else
    log "- Another node is generating the shared certificate; waiting... -"
    waited=0
    while [ ! -f "${CERT_DIR}/cert.pem" ]; do
      if [ "${waited}" -ge 60 ]; then
        log "ERROR: timed out waiting for the shared certificate."
        exit 1
      fi
      sleep 1
      waited=$((waited + 1))
    done
  fi
}

provision_tls() {
  mkdir -p "${CERT_DIR}"
  case "${NATS_TLS_MODE}" in
    byo)
      if [ ! -f "${CERT_DIR}/cert.pem" ] || [ ! -f "${CERT_DIR}/key.pem" ]; then
        log "ERROR: NATS_TLS_MODE=byo but ${CERT_DIR}/cert.pem and/or key.pem are missing."
        log "       Provide your certificate and key in the nats-certs volume."
        exit 1
      fi
      log "- Using bring-your-own TLS certificate -"
      ;;
    managed)
      # The traefik-certs-dumper sidecar writes cert.pem/key.pem into the shared
      # certs volume. It may not be present on the very first boot.
      waited=0
      while [ ! -f "${CERT_DIR}/cert.pem" ] || [ ! -f "${CERT_DIR}/key.pem" ]; do
        if [ "${waited}" -ge "${NATS_TLS_MANAGED_WAIT}" ]; then
          break
        fi
        log "  waiting for managed certificate... (${waited}s/${NATS_TLS_MANAGED_WAIT}s)"
        sleep 2
        waited=$((waited + 2))
      done
      if [ -f "${CERT_DIR}/cert.pem" ] && [ -f "${CERT_DIR}/key.pem" ]; then
        log "- Using managed (Let's Encrypt) TLS certificate -"
      else
        log "- Managed certificate not yet available; generating self-signed fallback -"
        generate_shared_selfsigned
      fi
      ;;
    selfsigned|*)
      if [ -f "${CERT_DIR}/cert.pem" ] && [ -f "${CERT_DIR}/key.pem" ]; then
        log "- Existing self-signed TLS certificate found (shared volume) -"
      else
        generate_shared_selfsigned
      fi
      ;;
  esac

  # Guarantee a CA file exists (self-signed cert is its own CA).
  if [ ! -f "${CERT_DIR}/ca.pem" ]; then
    cp "${CERT_DIR}/cert.pem" "${CERT_DIR}/ca.pem"
  fi

  chown -R "${NATS_RUN_AS}:${NATS_RUN_AS}" "${CERT_DIR}" 2>/dev/null || true
  chmod 600 "${CERT_DIR}/key.pem" 2>/dev/null || true
}

# --- Config rendering --------------------------------------------------------
# envsubst is given an explicit allowlist of ${VAR} names so any stray '$' in a
# value (JWTs are base64url + dots, no '$') survives untouched.
render_one() {
  tmpl="$1"; out="$2"; vars="$3"
  envsubst "${vars}" < "${tmpl}" > "${out}"
}

render_config() {
  log "- Rendering NATS config from templates -"
  render_one "${ETC}/nats-server.conf.template" "${ETC}/nats-server.conf" \
    '${NATS_SERVER_NAME} ${NATS_CLUSTER_NAME} ${NATS_ROUTE_USER} ${NATS_ROUTE_PASSWORD} ${NATS_TLS_VERIFY} ${NATS_MAX_PAYLOAD} ${NATS_MAX_CONNECTIONS}'
  render_one "${CONF_DIR}/jetstream.conf.template" "${CONF_DIR}/jetstream.conf" \
    '${NATS_JS_MAX_MEM} ${NATS_JS_MAX_FILE}'
  render_one "${CONF_DIR}/auth.conf.template" "${CONF_DIR}/auth.conf" \
    '${NATS_OPERATOR_JWT} ${NATS_SYS_ACCOUNT_ID} ${NATS_SYS_ACCOUNT_JWT} ${NATS_APP_ACCOUNT_ID} ${NATS_APP_ACCOUNT_JWT}'
  chown -R "${NATS_RUN_AS}:${NATS_RUN_AS}" "${ETC}" 2>/dev/null || true
}

# --- Main --------------------------------------------------------------------
banner
require_operator
provision_tls
render_config

mkdir -p "${DATA_DIR}/jetstream"
chown -R "${NATS_RUN_AS}:${NATS_RUN_AS}" "${DATA_DIR}" 2>/dev/null || true

log "Starting nats-server: $*"
if command -v su-exec >/dev/null 2>&1 && id "${NATS_RUN_AS}" >/dev/null 2>&1; then
  exec su-exec "${NATS_RUN_AS}:${NATS_RUN_AS}" "$@"
else
  exec "$@"
fi
