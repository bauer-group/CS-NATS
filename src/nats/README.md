# NATS Server Image

A thin, professional wrapper around the official `nats:*-alpine` image
([Docker Hub](https://hub.docker.com/_/nats)) — tracking the floating
**`2-alpine`** tag (latest NATS 2.x; major pinned to avoid a breaking jump).

It keeps the upstream binary intact and layers on the concerns a clustered,
secured deployment needs:

| Concern | Upstream image | This image |
| --- | --- | --- |
| Config | static `nats-server.conf` you mount | `envsubst`-rendered from env at boot |
| TLS certificate | none / bring your own | Layered: **self-signed** / **managed LE** / **BYO** |
| Cluster cert race | n/a (single node) | atomic `mkdir`-lock — one node generates, others wait |
| Auth | manual config | operator/JWT (MEMORY resolver) wired from public account JWTs |
| Privileges | runs as `nats` | provisions as root, drops to `nats` via `su-exec` |

## Rendered configuration

The entrypoint renders three templates into their `.conf` outputs, then starts
`nats-server -c /etc/nats/nats-server.conf`:

1. `nats-server.conf.template` — listeners (client `4222`, monitoring `8222`),
   global limits, `cluster {}` (RAFT routes `6222` + route authorization), and
   the client `tls {}` block. `include`s the two below.
2. `conf.d/jetstream.conf.template` — JetStream store dir + server-level limits.
3. `conf.d/auth.conf.template` — `operator`, `system_account`, `resolver: MEMORY`
   and the `resolver_preload` (public SYS + APP account JWTs).

`envsubst` is given an explicit variable allowlist so any stray `$` in a value
survives untouched.

## TLS modes (`NATS_TLS_MODE`)

| Mode | Behaviour |
| --- | --- |
| `selfsigned` (default) | Generates one 4096-bit, 10-year self-signed cert **shared** by all three nodes (SAN covers `nats-1/2/3`, `localhost`). Race-safe via an atomic `mkdir`-lock on the shared certs volume. `NATS_TLS_VERIFY=false`. |
| `managed` | Uses `cert.pem`/`key.pem` written into the certs volume by a `traefik-certs-dumper` sidecar (real Let's Encrypt). Falls back to self-signed if absent (optionally waits `NATS_TLS_MANAGED_WAIT` seconds). |
| `byo` | Uses operator-provided `cert.pem`/`key.pem` in the certs volume; fails fast if missing. |

In every mode the entrypoint guarantees `ca.pem` exists and `chmod 600`s the key.

## Key environment variables

| Variable | Default | Description |
| --- | --- | --- |
| `NATS_SERVER_NAME` | `$(hostname)` | **Unique per node** — required for JetStream RAFT |
| `NATS_CLUSTER_NAME` | `bauergroup` | **Identical on all nodes** |
| `NATS_ROUTE_USER` / `NATS_ROUTE_PASSWORD` | `route` / *(generated)* | Cluster route authorization (plaintext) |
| `NATS_JS_MAX_MEM` / `NATS_JS_MAX_FILE` | `1G` / `10G` | Server-level JetStream limits |
| `NATS_MAX_PAYLOAD` / `NATS_MAX_CONNECTIONS` | `8MB` / `65536` | Global limits |
| `NATS_TLS_MODE` / `NATS_TLS_VERIFY` | `selfsigned` / `false` | Client TLS |
| `NATS_OPERATOR_JWT` | *(required)* | Public operator JWT (from `generate-credentials.py`) |
| `NATS_SYS_ACCOUNT_ID` / `..._JWT` | *(required)* | System account preload |
| `NATS_APP_ACCOUNT_ID` / `..._JWT` | *(required)* | Application account preload |

The entrypoint fails fast with a helpful message if the operator/JWT material is
missing — run `python scripts/generate-credentials.py` once before starting.

## Build

```bash
docker build \
  --build-arg NATS_VERSION=2-alpine \
  -t ghcr.io/bauer-group/cs-nats/nats:local .
```

See the repository root `README.md` and `docs/` for full deployment guidance.
