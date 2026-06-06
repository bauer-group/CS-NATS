# Sizing & Tuning

JetStream resource use is driven by **streams + consumers + retained messages**
(and message size) — not by raw throughput. A NATS node sustains very high
message rates, so daily totals rarely limit you; size by the real drivers below.

## Presets

| Profile | Client conns | Streams | Retained | `JS_MAX_MEM` | `JS_MAX_FILE` | Host RAM |
| --- | --- | --- | --- | --- | --- | --- |
| **Small** (default) | < 500 | < 50 | < 10 GB/node | `1G` | `10G` | 2–4 GB |
| **Medium** | 500–5 000 | 50–500 | < 50 GB/node | `2G` | `50G` | 8–12 GB |
| **Large** | 5k–50k+ | 500–5 000 | < 200 GB/node | `4G` | `200G` | 16–32 GB |

Set these in `.env` (`NATS_JS_MAX_MEM`, `NATS_JS_MAX_FILE`). They are
**server-level** per-node JetStream caps.

## Two layers of JetStream limits

A stream needs headroom in **both**:

1. **Server level** (`jetstream { max_memory_store, max_file_store }`, from env) —
   caps everything stored on a node.
2. **Account level** (the APP account JWT, set via nsc:
   `--js-mem-storage`, `--js-disk-storage`, `--js-streams`, `--js-consumer`) —
   caps the APP account across the cluster.

If a stream creation fails with a limits/quota error, raise whichever layer is
the bottleneck. Account limits change requires re-running
`generate-credentials.py --force` (or `nsc edit account` + `nsc push` with the
full resolver).

## Replication & disk

R3 streams (`num_replicas: 3`) keep a **full copy on every node**, so per-node
disk ≈ sum of all R3 stream `max_bytes`. Size `JS_MAX_FILE` accordingly, plus
headroom for compaction and the RAFT logs.

| Replicas | Tolerates | Use for |
| --- | --- | --- |
| 1 | no node loss | dev / disposable data |
| 3 | 1 node down | **default** — HA on a 3-node cluster |

A 3-node cluster cannot host R5 streams (needs 5 nodes).

## Message size

`NATS_MAX_PAYLOAD` bounds a single message body (NATS default 1 MB, hard ceiling
64 MB). Large messages buffer in RAM on publish **and** deliver — keep them rare.
For routine large payloads use a JetStream **object store** or external object
storage (MinIO) and publish a reference.

## Connections & file descriptors

`NATS_MAX_CONNECTIONS` is a per-node leak/DoS guard. For 10k+ connections also
raise the host file-descriptor limit (`ulimit -n`) for the Docker daemon.

## Memory model

There is deliberately **no Docker `mem_limit`** on the nodes: a hard cgroup cap
would OOM-kill (SIGKILL) a node on a transient spike. JetStream is bounded at the
application level via the store limits instead. Give each node host RAM per the
preset table and monitor `jetstream` metrics from the exporter.

## What to watch (Prometheus)

- `jetstream_*` — stream/consumer counts, bytes, messages, API errors
- `gnatsd_varz_mem` / connections / slow consumers
- RAFT/meta health via `nats server report jetstream` (SYS creds)

Beyond Large on three nodes, scale vertically first (bigger nodes), then
consider more nodes or superclusters — see [clustering.md](clustering.md).
