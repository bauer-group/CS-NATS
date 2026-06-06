# NATS Init Container

One-shot initialization container that declaratively provisions a NATS
**JetStream** cluster — streams, KV buckets, object stores, durable consumers —
from a JSON topology. Runs on every start and is fully idempotent.

A thin Python orchestrator (rich console, numbered tasks, `${ENV_VAR}`
resolution) drives the official `nats` CLI, which is copied from the
`natsio/nats-box` image at build time (multi-stage). The CLI speaks operator/JWT
natively via `--creds`, so the init authenticates as the **APP-account admin**.

## Why the `nats` CLI (not a client lib)

The CLI round-trips the exact stream/consumer config JSON that JetStream emits
(`--config <file>`), speaks JWT auth and TLS with two flags, and avoids pinning a
client-library version. The Python layer only orchestrates and parses exit codes.

## Configuration loading

The init applies your **user topology**, read from `/config/nats-topology.json`
(override with `NATS_INIT_CONFIG`). In development this is a repo bind-mount; in
production it lives on a named volume and is **seeded** with the baked demo
(`/app/config/seed.json`) on first boot if absent, then editable at runtime.

JSON string values support `${VAR_NAME}` placeholders, resolved from the
environment (missing var → hard error, so values are never silently blanked).
`_`-prefixed keys are treated as comments.

## Idempotency

Every resource is **probed** before any mutation (the exit code of an
`info`/`status` command decides existence — stable across CLI versions):

| Resource | Probe | Missing → | Exists → |
| --- | --- | --- | --- |
| Stream | `nats stream info` | `stream add --config` | `stream edit --config -f` (converge) |
| KV bucket | `nats kv status` | `nats kv add` | left as-is (reported) |
| Object store | `nats object info` | `nats object add` | left as-is (reported) |
| Consumer | `nats consumer info` | `nats consumer add --config` | left as-is (reported) |

`nats stream add` is only idempotent on identical config, which is exactly why
the init branches on existence rather than blind-adding. The init is **additive**
— it creates/converges but never deletes; remove resources via the `nats` CLI.

## Task reference

| Order | Task | Config key | nats CLI |
| --- | --- | --- | --- |
| 01 | Account | *(none)* | `nats account info` (JetStream reachability gate) |
| 02 | Streams | `streams` | `stream info` → `stream add`/`edit --config` |
| 03 | Key/Value | `kv` | `kv status` → `kv add` |
| 04 | Object Stores | `object_stores` | `object info` → `object add` |
| 05 | Consumers | `consumers` | `consumer info` → `consumer add --config` |

JSON topology schema and every field: **➡ [docs/messaging-topology.md](../../docs/messaging-topology.md)**.

## Environment variables

| Variable | Default | Description |
| --- | --- | --- |
| `NATS_URLS` | `nats://nats-1:4222,nats://nats-2:4222,nats://nats-3:4222` | Cluster client URLs |
| `NATS_CREDS` | `/creds/admin.creds` | APP-account admin credentials (mounted) |
| `NATS_TLS_CA` | *(unset)* | CA file for client TLS verification (`/certs/ca.pem`) |
| `NATS_INIT_CONFIG` | `/config/nats-topology.json` | Path to the user topology |
| `NATS_WAIT_TIMEOUT` | `120` | Seconds to poll for the JetStream API (meta-group leader) |

Plus any `${VAR}` referenced by your topology JSON (e.g. `APP_STREAM_NAME`).

## Adding a task

1. Drop a numbered file in `tasks/` (e.g. `06_mirrors.py`).
2. Define `TASK_NAME`, `TASK_DESCRIPTION`, `CONFIG_KEY` (or `None`), and
   `run(items, console, *, client, config, **kwargs) -> dict`.
3. Return `{"changed": bool, "skipped": bool, "message": str}`.

## Tests

```bash
pip install -r requirements-test.txt
pytest            # pure-function unit tests (env resolution, CLI args, temp json)
```

## License

MIT License - BAUER GROUP
