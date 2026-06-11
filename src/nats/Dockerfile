# ===============================================================================
# BAUER GROUP NATS - JetStream Server Container Image
# ===============================================================================
# Thin, professional wrapper around the official nats:*-alpine image.
#
# Adds:
#   - Env-driven config rendering at boot (nats-server.conf via envsubst)
#   - Layered client TLS provisioning (self-signed / managed Let's Encrypt / BYO)
#   - JWT/operator auth (MEMORY resolver) wired from public account JWTs
#   - Race-safe shared self-signed cert generation across the 3-node cluster
#
# Documentation: https://github.com/bauer-group/CS-NATS
# ===============================================================================

# ---------------------------------------------------------------------------
# Build Arguments
# ---------------------------------------------------------------------------
# Floating tag: tracks the latest NATS 2.x on Alpine (major pinned to avoid a
# breaking jump). The base-image monitor rebuilds when this tag's digest moves.
ARG NATS_REPOSITORY=nats
ARG NATS_VERSION=2-alpine

# ---------------------------------------------------------------------------
# Base Image
# ---------------------------------------------------------------------------
FROM ${NATS_REPOSITORY}:${NATS_VERSION}

# ---------------------------------------------------------------------------
# Metadata Labels (Legacy)
# ---------------------------------------------------------------------------
LABEL vendor="BAUER GROUP"
LABEL maintainer="Karl Bauer <karl.bauer@bauer-group.com>"

# ---------------------------------------------------------------------------
# OCI Image Spec Labels
# https://github.com/opencontainers/image-spec/blob/main/annotations.md
# ---------------------------------------------------------------------------
LABEL org.opencontainers.image.title="NATS"
LABEL org.opencontainers.image.description="NATS JetStream Server - BAUER GROUP Edition (3-node RAFT cluster, operator/JWT auth, layered TLS, env-driven config)"
LABEL org.opencontainers.image.vendor="BAUER GROUP"
LABEL org.opencontainers.image.authors="Karl Bauer <karl.bauer@bauer-group.com>"
LABEL org.opencontainers.image.licenses="MIT"
LABEL org.opencontainers.image.source="https://github.com/bauer-group/CS-NATS"
LABEL org.opencontainers.image.url="https://github.com/bauer-group/CS-NATS"
LABEL org.opencontainers.image.documentation="https://github.com/bauer-group/CS-NATS#readme"
LABEL org.opencontainers.image.version="0.1.2"

# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------
# openssl  - self-signed certificate generation
# gettext  - provides `envsubst` for rendering the config templates
# su-exec  - drop privileges to the 'nats' user after root-only provisioning
# The 'nats' user/group is created here — the upstream nats:*-alpine image does
# not ship one, and we drop to it at runtime (tolerant if a base ever adds it).
USER root
RUN set -eux; \
    apk add --no-cache openssl gettext su-exec; \
    addgroup -S nats 2>/dev/null || true; \
    adduser -S -G nats -H -h /data -s /sbin/nologin nats 2>/dev/null || true

# ---------------------------------------------------------------------------
# Configuration Templates
# ---------------------------------------------------------------------------
COPY etc/nats/nats-server.conf.template       /etc/nats/nats-server.conf.template
COPY etc/nats/conf.d/jetstream.conf.template  /etc/nats/conf.d/jetstream.conf.template
COPY etc/nats/conf.d/auth.conf.template       /etc/nats/conf.d/auth.conf.template
RUN mkdir -p /etc/nats/certs /data/jetstream \
    && chown -R nats:nats /etc/nats /data

# ---------------------------------------------------------------------------
# Custom Entrypoint
# ---------------------------------------------------------------------------
COPY docker-entrypoint-custom.sh /usr/local/bin/docker-entrypoint-custom.sh
RUN chmod a+rx /usr/local/bin/docker-entrypoint-custom.sh

# ---------------------------------------------------------------------------
# Tuning Defaults (small instance — overridable via compose .env)
# ---------------------------------------------------------------------------
ENV NATS_CLUSTER_NAME=bauergroup \
    NATS_JS_MAX_MEM=1G \
    NATS_JS_MAX_FILE=10G \
    NATS_MAX_PAYLOAD=8MB \
    NATS_MAX_CONNECTIONS=65536 \
    NATS_TLS_MODE=selfsigned \
    NATS_TLS_VERIFY=false

# ---------------------------------------------------------------------------
# Ports
# ---------------------------------------------------------------------------
# 4222 client (TLS)   6222 cluster / RAFT routes   8222 HTTP monitoring
# 8080 websocket (optional, off by default)
EXPOSE 4222 6222 8222 8080

# ---------------------------------------------------------------------------
# Health Check
# ---------------------------------------------------------------------------
# /healthz is unauthenticated; ?js-enabled-only=true reports healthy once
# JetStream is enabled (200) and 503 while the node is still starting.
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
    CMD wget -q -O - "http://127.0.0.1:8222/healthz?js-enabled-only=true" >/dev/null 2>&1 || exit 1

# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
ENTRYPOINT ["docker-entrypoint-custom.sh"]
CMD ["nats-server", "-c", "/etc/nats/nats-server.conf"]
