# JetStream Topology (Infrastructure-as-Code)

The `nats-init` container provisions your JetStream topology — streams, KV
buckets, object stores, durable consumers — declaratively from a single JSON
file, idempotently, on every start. This is the authoritative field reference.

## Contents

- [How the config is loaded](#how-the-config-is-loaded)
- [Where the config lives, per deployment](#where-the-config-lives-per-deployment)
- [Environment-variable resolution](#environment-variable-resolution-var)
- [Top-level structure](#top-level-structure)
- [`streams`](#streams)
- [`kv`](#kv)
- [`object_stores`](#object_stores)
- [`consumers`](#consumers)
- [Durations & sizes](#durations--sizes)
- [Idempotency & deletion](#idempotency--deletion)

---

## How the config is loaded

`nats-init` waits for the JetStream API (a meta-group leader), then runs numbered
tasks in order, each consuming one config block:

| Order | Task | Config key | Action |
| --- | --- | --- | --- |
| 01 | Account | *(none)* | reachability gate — `nats account info` |
| 02 | Streams | `streams` | `stream info` → `stream add`/`edit --config` |
| 03 | Key/Value | `kv` | `kv status` → `kv add` |
| 04 | Object Stores | `object_stores` | `object info` → `object add` |
| 05 | Consumers | `consumers` | `consumer info` → `consumer add --config` |

The init authenticates as the **APP-account admin** (`/creds/admin.creds`).

## Where the config lives, per deployment

| Deployment | Path | Source |
| --- | --- | --- |
| development | `config/nats-topology.json` | repo bind-mount (edit in your IDE) |
| cluster / traefik / coolify | `/config/nats-topology.json` | `nats-config` volume, seeded with the demo on first boot |

Override the in-container path with `NATS_INIT_CONFIG`.

## Environment-variable resolution (`${VAR}`)

Any string value may contain `${VAR}` placeholders, resolved from the init
container's environment. A missing variable is a **hard error** (values are
never silently blanked). Keys starting with `_` are comments and are passed
through untouched (so `_note` fields may contain literal `${...}` examples).

```json
{ "streams": [ { "name": "${APP_STREAM_NAME}", "subjects": ["app.events.>"] } ] }
```

## Top-level structure

```jsonc
{
  "streams": [...],
  "kv": [...],
  "object_stores": [...],
  "consumers": [...]
}
```

Every block is optional; an absent or empty block is skipped.

## `streams`

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `name` | string | *(required)* | Stream name |
| `subjects` | string[] | *(required)* | Subjects captured (e.g. `app.events.>`) |
| `retention` | string | `limits` | `limits` \| `interest` \| `workqueue` |
| `storage` | string | `file` | `file` \| `memory` |
| `num_replicas` | int | `3` | R3 needs the 3-node cluster |
| `max_age` | int (ns) | `0` (∞) | Max message age |
| `max_bytes` | int | `-1` (∞) | Max stream size |
| `max_msgs` | int | `-1` (∞) | Max message count |
| `discard` | string | `old` | `old` \| `new` when full |
| `duplicate_window` | int (ns) | `120000000000` | Dedup window for `Nats-Msg-Id` |

Existing streams are **converged** (`stream edit`); immutable-field changes (e.g.
`storage`, `retention`) are reported and skipped rather than failing the run.

## `kv`

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `bucket` | string | *(required)* | Bucket name |
| `history` | int | `1` | Revisions kept per key |
| `ttl` | duration string | `0s` | Per-key expiry; `0s` = none |
| `replicas` | int | `3` | |
| `storage` | string | `file` | `file` \| `memory` |
| `max_value_size` | int | — | Optional cap per value |
| `description` | string | — | Optional |

KV config is largely immutable; existing buckets are reported, not edited.

## `object_stores`

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `bucket` | string | *(required)* | Bucket name |
| `replicas` | int | `3` | |
| `storage` | string | `file` | `file` \| `memory` |
| `ttl` | duration string | `0s` | |
| `max_bucket_size` | int | — | Optional |
| `description` | string | — | Optional |

## `consumers`

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `stream` | string | *(required)* | Target stream (must exist) |
| `name` | string | *(required)* | Durable name |
| `ack_policy` | string | — | `explicit` \| `none` \| `all` |
| `deliver_policy` | string | — | `all` \| `last` \| `new` \| `by_start_sequence` \| `by_start_time` |
| `replay_policy` | string | — | `instant` \| `original` |
| `max_deliver` | int | — | Redelivery limit |
| `ack_wait` | int (ns) | — | Ack timeout |
| `max_ack_pending` | int | — | In-flight unacked cap |
| `filter_subject` | string | — | Narrow to a subset of stream subjects |

Created **durable** (`durable_name = name`). Existing consumers are reported.

## Durations & sizes

- **Stream/consumer durations** (`max_age`, `ack_wait`, `duplicate_window`) are
  **nanoseconds** (JetStream wire format): `1s=1e9`, `1m=6e10`, `1h=3.6e12`,
  `1d=8.64e13`.
- **KV/object `ttl`** is a **Go duration string** (`"24h"`, `"0s"`).
- Byte sizes are integers (bytes): `1 MiB = 1048576`, `1 GiB = 1073741824`.

## Idempotency & deletion

The init is **additive** — it creates resources and converges stream config, but
**never deletes**. Remove resources with the `nats` CLI:

```bash
docker exec <stack>_BOX nats stream rm <name>
docker exec <stack>_BOX nats kv del <bucket>
docker exec <stack>_BOX nats object del <bucket>
docker exec <stack>_BOX nats consumer rm <stream> <name>
```

Re-running the init after a manual delete recreates the declared resources.
