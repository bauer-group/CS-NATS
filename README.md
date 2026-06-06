# NATS JetStream Cluster

Production-ready 3-node [NATS](https://nats.io/) **JetStream** cluster (RAFT)
with operator/JWT authentication, declarative provisioning (Infrastructure-as-
Code), layered client TLS, Prometheus metrics, and full CI/CD automation.

Tracks the floating `2-alpine` image tag — always the latest NATS 2.x, with the
major pinned to avoid a breaking jump.

A thin, professional wrapper around the official `nats:*-alpine` image plus a
Python init sidecar that provisions your JetStream topology — streams, KV
buckets, object stores, durable consumers — from a single JSON file. `nats-box`
ships as the operational CLI and a `prometheus-nats-exporter` scrapes all nodes.

## Features

- **3-node JetStream cluster (RAFT)** — native HA. R3 streams tolerate one node
  down; the meta-group elects a leader automatically. Unique `server_name` per
  node, shared `cluster.name`.
- **Operator / JWT auth** — decentralized, multi-tenant: operator → `SYS` +
  `APP` accounts → user `.creds`. MEMORY resolver for a lean fixed account set
  (NATS full resolver documented as the scale-out). See
  [docs/security-and-auth.md](docs/security-and-auth.md).
- **Declarative provisioning (IaC)** — an idempotent init container applies your
  topology from JSON on every start, via the `nats` CLI:
  - **Streams** (R3), **KV buckets**, **object stores**, **durable consumers**
  - `${ENV_VAR}` resolution keeps values out of config files; additive & idempotent
- **nats-box CLI** — `nats`, `nsc`, `nk` pre-wired (`NATS_URL`/`NATS_CREDS`/`NATS_CA`).
- **Prometheus exporter** — `prometheus-nats-exporter` scrapes all three nodes'
  monitoring endpoints; `/metrics` on `:7777`.
- **Sizing presets** — small / medium / large tuning, documented as a table in
  `.env.example`, keyed by streams + retained data. **Default: small.**
- **Layered client TLS** — self-signed (zero-config, race-safe shared cert) →
  bring-your-own (real CA cert) → managed (external issuer writes into the certs
  volume). Routes plaintext on the isolated network.
- **Three deployment modes** — development (local build), cluster (direct ports),
  Coolify (dashboard domains). No reverse proxy / LB: NATS is raw TCP and does
  its own client-side failover (see `client_advertise`, below).
- **CI/CD automation** — semantic releases, GHCR image builds, base-image
  monitoring, Dependabot auto-merge, SBOMs, Teams + AI issue triage.

## Quick Start

1. **Clone & enter**
   ```bash
   git clone https://github.com/bauer-group/CS-NATS.git
   cd CS-NATS
   ```

2. **Generate `.env`** (fills the `CHANGE_ME_*` hex secrets)
   ```bash
   python scripts/generate-env.py
   ```

3. **Bootstrap operator/JWT credentials** (runs `nsc` via Docker)
   ```bash
   python scripts/generate-credentials.py
   ```
   Writes the **public** JWTs into `.env` and the **secret** `.creds` into
   `./creds/` (gitignored). The server refuses to start until this is done.

4. **(Optional) Define your topology** — edit `config/nats-topology.json`, or
   start from `config/nats-topology.example.json` (development mounts the demo
   automatically).

5. **Start**
   ```bash
   # Development (local builds, mounts the demo topology)
   docker compose -f docker-compose.development.yml up -d --build

   # Cluster (direct ports, pre-built GHCR images)
   docker compose -f docker-compose.cluster.yml up -d
   ```

6. **Access**

   | Mode | Client | Monitoring | Metrics |
   | --- | --- | --- | --- |
   | Development | `nats://localhost:4222` (TLS, nats-1 only) | `http://localhost:8222/healthz` | `http://localhost:7777/metrics` |
   | Cluster | `nats://localhost:4222` / `:4223` / `:4224` (TLS, all 3) | `http://localhost:8222/healthz` | `http://localhost:7777/metrics` |

   For access from **outside** the host, set `NATS_ADVERTISE_NODE1/2/3` so each
   node advertises its public address and clients failover directly across all
   three (no load balancer) — see [docs/clustering.md](docs/clustering.md).

   Clients authenticate with a `.creds` file and the CA. Inside `nats-box` the
   `nats` CLI is pre-wired:
   ```bash
   docker exec <stack>_BOX nats stream ls
   docker exec <stack>_BOX nats --creds /creds/sys-user.creds server report jetstream
   ```

## Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                        Docker Compose Stack                            │
│   ┌─────────┐   ┌─────────┐   ┌─────────┐                              │
│   │ nats-1  │◄─►│ nats-2  │◄─►│ nats-3  │   RAFT meta-group + R3 streams│
│   │ js+tls  │   │ js+tls  │   │ js+tls  │   routes :6222 (plaintext)    │
│   │ :4222   │   │ :4222   │   │ :4222   │   client :4222 (TLS)          │
│   │ :8222   │   │ :8222   │   │ :8222   │   monitor :8222 (HTTP, no auth)│
│   └────┬────┘   └────┬────┘   └────┬────┘                              │
│        │             │             │                                   │
│   ┌────▼─────────────▼─────────────▼────┐  ┌──────────┐  ┌──────────┐ │
│   │     nats-exporter  :7777/metrics     │  │ nats-box │  │nats-init │ │
│   │  scrapes all 3 monitoring endpoints  │  │  (CLI)   │  │(one-shot)│ │
│   └──────────────────────────────────────┘  └──────────┘  └──────────┘ │
│              Auth: operator → SYS / APP accounts → .creds              │
└──────────────────────────────────────────────────────────────────────┘
```

## Deployment Modes

| Mode | Compose file | Exposure | Use for |
| --- | --- | --- | --- |
| **Development** | `docker-compose.development.yml` | host ports | local builds & testing (mounts demo topology) |
| **Cluster** | `docker-compose.cluster.yml` | host ports | single-host 3-node cluster, GHCR images |
| **Coolify** | `docker-compose.coolify.yml` | Coolify dashboard | PaaS-managed domains & TLS |

## Configuration

Everything is driven from `.env`:

- **Sizing** — `NATS_JS_MAX_MEM`, `NATS_JS_MAX_FILE`, `NATS_MAX_PAYLOAD`,
  `NATS_MAX_CONNECTIONS`. See the preset table in `.env.example` and
  [docs/sizing-and-tuning.md](docs/sizing-and-tuning.md).
- **Auth** — operator/JWT via `scripts/generate-credentials.py`; the public
  JWTs land in `.env`, secrets in `./creds/`.
  See [docs/security-and-auth.md](docs/security-and-auth.md).
- **TLS** — `NATS_TLS_MODE` (`selfsigned` | `managed` | `byo`),
  `NATS_TLS_VERIFY`. See [docs/tls-and-certificates.md](docs/tls-and-certificates.md).
- **Topology** — `config/nats-topology.json`.
  See [docs/messaging-topology.md](docs/messaging-topology.md).

The server image renders its config from these env vars at boot
(`src/nats/etc/nats/*.template`) — no committed-file mutation. See
[src/nats/README.md](src/nats/README.md).

## Ports

| Port | Purpose |
| --- | --- |
| 4222 | NATS client (TLS) |
| 6222 | Cluster / RAFT routes (plaintext, internal) |
| 8222 | HTTP monitoring (`/varz` `/jsz` `/healthz` — unauthenticated, internal) |
| 7777 | Prometheus exporter `/metrics` |
| 8080 | NATS WebSocket (optional, off by default) |

## Documentation

- [Installation](docs/installation.md)
- [JetStream topology (IaC)](docs/messaging-topology.md)
- [Security & authentication (operator/JWT)](docs/security-and-auth.md)
- [TLS & certificates](docs/tls-and-certificates.md)
- [Sizing & tuning](docs/sizing-and-tuning.md)
- [Clustering & scale-out](docs/clustering.md)
- [Init container reference](src/nats-init/README.md)
- [Server image reference](src/nats/README.md)

## License

MIT License — BAUER GROUP. See [LICENSE](LICENSE).
