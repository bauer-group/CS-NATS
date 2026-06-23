# ===============================================================================
# BAUER GROUP NATS Init - Declarative JetStream Provisioning
# ===============================================================================
# One-shot container that provisions JetStream streams / KV / object stores /
# consumers from a JSON topology, idempotently, on every start.
#
# It drives the official `nats` CLI (copied from natsio/nats-box) from a thin
# Python orchestrator (rich console, numbered tasks, ${ENV_VAR} resolution).
#
# Documentation: https://github.com/bauer-group/CS-NATS
# ===============================================================================

# ---------------------------------------------------------------------------
# Build Arguments
# ---------------------------------------------------------------------------
ARG NATS_BOX_REPOSITORY=natsio/nats-box
ARG NATS_BOX_VERSION=latest
ARG PYTHON_VERSION=3.14-alpine

# ---------------------------------------------------------------------------
# Stage 1 — grab the static `nats` CLI from the official nats-box image
# ---------------------------------------------------------------------------
FROM ${NATS_BOX_REPOSITORY}:${NATS_BOX_VERSION} AS natsbox

# ---------------------------------------------------------------------------
# Stage 2 — Python runtime
# ---------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}

# ---------------------------------------------------------------------------
# Metadata Labels (Legacy)
# ---------------------------------------------------------------------------
LABEL vendor="BAUER GROUP"
LABEL maintainer="Karl Bauer <karl.bauer@bauer-group.com>"

# ---------------------------------------------------------------------------
# OCI Image Spec Labels
# ---------------------------------------------------------------------------
LABEL org.opencontainers.image.title="NATS Init"
LABEL org.opencontainers.image.description="Declarative JetStream provisioning (streams/KV/object/consumers) from JSON via the nats CLI"
LABEL org.opencontainers.image.vendor="BAUER GROUP"
LABEL org.opencontainers.image.authors="Karl Bauer <karl.bauer@bauer-group.com>"
LABEL org.opencontainers.image.licenses="MIT"
LABEL org.opencontainers.image.source="https://github.com/bauer-group/CS-NATS"
LABEL org.opencontainers.image.url="https://github.com/bauer-group/CS-NATS"
LABEL org.opencontainers.image.documentation="https://github.com/bauer-group/CS-NATS#readme"
LABEL org.opencontainers.image.version="0.1.4"

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
ENV PYTHONUNBUFFERED=1

# ---------------------------------------------------------------------------
# nats CLI (static Go binary, portable across Alpine)
# ---------------------------------------------------------------------------
COPY --from=natsbox /usr/local/bin/nats /usr/local/bin/nats

# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------
COPY requirements.txt .
RUN apk add --no-cache tini \
    && pip install --no-cache-dir -r requirements.txt \
    && rm -rf /root/.cache

# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------
WORKDIR /app

COPY main.py nats_cli.py ./
COPY tasks/ ./tasks/
COPY config/ ./config/

# ---------------------------------------------------------------------------
# Non-root User
# ---------------------------------------------------------------------------
# /config holds the user topology (a mounted named volume in production, a repo
# bind-mount in development). /creds holds the read-only admin .creds mount.
RUN addgroup -g 1000 init \
    && adduser -u 1000 -G init -h /app -D init \
    && mkdir -p /config /creds \
    && chown -R init:init /app /config

USER init

# ---------------------------------------------------------------------------
# Entrypoint (tini for proper signal handling)
# ---------------------------------------------------------------------------
ENTRYPOINT ["/sbin/tini", "--", "python", "main.py"]
