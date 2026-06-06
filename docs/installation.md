# Installation

## Prerequisites

- Docker Engine 24+ with the Compose plugin (`docker compose`)
- Python 3.9+ (for the `scripts/` helpers — pure stdlib, no pip installs)
- Docker is also used by `generate-credentials.py` to run `nsc` (no host install)

## 1. Configure environment

```bash
python scripts/generate-env.py
```

Creates `.env` from `.env.example` and fills the `CHANGE_ME_*` hex secrets (the
cluster route password). Review `.env` afterwards — pick a sizing preset and set
hostnames for Traefik/Coolify.

## 2. Bootstrap operator / JWT credentials

```bash
python scripts/generate-credentials.py
```

Runs `nsc` inside `natsio/nats-box` to create the operator, the `SYS` system
account, the `APP` application account (JetStream enabled), and their users. It
writes:

- the **public** operator + account JWTs into `.env`
  (`NATS_OPERATOR_JWT`, `NATS_*_ACCOUNT_*`), and
- the **secret** `.creds` files into `./creds/` (gitignored).

See [security-and-auth.md](security-and-auth.md) for the model. The server
refuses to start while `NATS_OPERATOR_JWT` is empty.

## 3. (Optional) Define your topology

The demo provisions one stream + one KV bucket. To customize, edit
`config/nats-topology.json` (mounted in development), or start from the full
example:

```bash
cp config/nats-topology.example.json config/nats-topology.json
```

See [messaging-topology.md](messaging-topology.md) for the schema.

## 4. Start

```bash
# Development (local builds, mounts the demo topology)
docker compose -f docker-compose.development.yml up -d --build

# Cluster (direct ports, pre-built GHCR images)
docker compose -f docker-compose.cluster.yml up -d

# Traefik (HTTPS metrics via Let's Encrypt)
docker compose -f docker-compose.traefik.yml up -d
```

## 5. Verify

```bash
# Cluster formed? (uses the SYS account)
docker exec <stack>_BOX nats --creds /creds/sys-user.creds server list
docker exec <stack>_BOX nats --creds /creds/sys-user.creds server report jetstream

# Provisioning applied? (APP account)
docker logs <stack>_INIT
docker exec <stack>_BOX nats stream ls

# Metrics
curl -s http://localhost:7777/metrics | head

# Health
docker ps    # all three nats-N should be (healthy)
```

`<stack>` is your `STACK_NAME` (e.g. `nats_example_domain_com`). Inside
`nats-box` the `nats` CLI auto-reads `NATS_URL` / `NATS_CREDS` / `NATS_CA`, so
most commands need no flags; `server` commands need the SYS creds shown above.

## Re-running provisioning

`nats-init` is idempotent — it runs on every `up` and converges to the declared
topology. Re-run it explicitly:

```bash
docker compose -f docker-compose.development.yml up -d --force-recreate nats-init
```

## High-availability test

```bash
docker stop <stack>_NODE2
docker exec <stack>_BOX nats stream info app-events   # still available (R3 tolerates 1 down)
docker start <stack>_NODE2                            # re-syncs automatically
```

## Upgrading NATS

Bump `NATS_VERSION` (build base) or `NATS_IMAGE_VERSION` (GHCR) in `.env`, then
recreate nodes one at a time to keep quorum:

```bash
docker compose -f docker-compose.cluster.yml up -d --no-deps nats-1
# wait for (healthy), then nats-2, then nats-3
```

## Rotating credentials

Regenerating the operator invalidates all `.creds` and the preloaded accounts —
treat it as a cluster reset:

```bash
docker compose -f docker-compose.development.yml down -v   # wipes JetStream data
python scripts/generate-credentials.py --force
docker compose -f docker-compose.development.yml up -d --build
```
