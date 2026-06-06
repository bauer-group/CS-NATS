# Clustering & Scale-Out

This stack ships a **3-node JetStream cluster (RAFT)** by default — NATS is
natively clustered, and three nodes are the minimum for a fault-tolerant
meta-group and R3 streams.

## How the cluster forms

- Each node sets a **unique `server_name`** (`nats-1`/`nats-2`/`nats-3`) and the
  **same `cluster.name`** (`NATS_CLUSTER_NAME`).
- Nodes connect over the cluster port (`6222`) using the `routes` list; route
  auth is a shared user/password (`NATS_ROUTE_USER`/`NATS_ROUTE_PASSWORD`).
- JetStream auto-forms a **meta-group** (RAFT) across the nodes. Streams elect
  their own RAFT group; an R3 stream tolerates one node down.

```
nats-1 ◄────────► nats-2
   ▲   ╲        ╱   ▲
   │    ╲      ╱    │      routes :6222 (RAFT)
   │     ╲    ╱     │      meta-group leader elected automatically
   ▼      ╲  ╱      ▼
        nats-3
```

## Quorum rules

| Nodes | Tolerates | Notes |
| --- | --- | --- |
| 1 | none | not a cluster — dev only |
| 3 | 1 down | **this stack** — odd count keeps RAFT majority |
| 5 | 2 down | larger HA; enables R5 streams |

Always use an **odd** node count so RAFT can hold a majority. Adding a 4th node
does not improve fault tolerance over 3.

## Operational checks

```bash
# Cluster membership and per-node state (SYS account)
docker exec <stack>_BOX nats --creds /creds/sys-user.creds server list

# JetStream meta-group leader + per-node JS health
docker exec <stack>_BOX nats --creds /creds/sys-user.creds server report jetstream

# Per-stream RAFT placement / replicas
docker exec <stack>_BOX nats stream info <name>
```

## External access (Model B)

There is **no load balancer or reverse proxy** in front of NATS — that would be a
single point of failure, and NATS doesn't need one: clients are cluster-aware and
do their own failover. NATS also load-balances *messages* itself (queue groups +
JetStream leader routing), so an external balancer is never needed for that.

The only thing the cluster needs for external access is for each node to
advertise a **reachable** address. By default a node advertises its internal
Docker address, which an outside client can't reach — so when the client fails
over it would try unreachable peers.

**Model B — `client_advertise` per node.** Each node advertises its PUBLIC
`host:port`; a client connects once (to any node), learns all three public
addresses via gossip, and then fails over **directly** to the nodes — no
intermediary in the steady-state path:

```text
client ──connect──▶ nats.example.com:4222 (node 1)
       ◀──gossip──  [nats.example.com:4222, :4223, :4224]   # advertised, reachable
       ──failover─▶ any node directly
```

Enable it by setting one variable per node in `.env` (cluster mode publishes
`4222`/`4223`/`4224` on the host):

```bash
NATS_ADVERTISE_NODE1=nats.example.com:4222
NATS_ADVERTISE_NODE2=nats.example.com:4223
NATS_ADVERTISE_NODE3=nats.example.com:4224
```

The entrypoint then renders `client_advertise` into each node's config
(`conf.d/advertise.conf`). Leave the variables empty for internal-only or
localhost development (no advertise — clients should then connect to a single
node, or run inside the Docker network where internal gossip is correct).

Clients connect with all three public URLs as seeds:

```bash
nats --creds ./app.creds --tlsca ./ca.pem \
     --server "tls://nats.example.com:4222,tls://nats.example.com:4223,tls://nats.example.com:4224" ...
```

> Nodes on **separate hosts**? Set each node's advertise to its own
> `host:4222`, and update the `routes` in `nats-server.conf.template` to the
> real inter-node addresses (the default `nats-1/2/3` aliases assume one Docker
> network).

## Rolling restart / upgrade

Recreate one node at a time, waiting for `(healthy)` between each, so the
meta-group never loses quorum:

```bash
docker compose -f docker-compose.cluster.yml up -d --no-deps nats-1
# wait healthy, then nats-2, then nats-3
```

## Scaling the account set: the NATS full resolver

The default **MEMORY resolver** preloads a fixed account set (SYS + APP) — lean,
but adding an account means editing `conf.d/auth.conf` and restarting. For many
or frequently-changing accounts, switch to the **NATS full resolver**, which
stores account JWTs on disk and accepts `nsc push`:

```hocon
operator: "<operator JWT>"
system_account: <SYS account ID>

resolver: {
  type: full
  dir: "/data/resolver"
  allow_delete: false
  interval: "2m"
}
resolver_preload: {
  <SYS account ID>: "<SYS JWT>"   # SYS still preloaded so the cluster bootstraps
}
```

Then push accounts to the running cluster:

```bash
nsc push -A --account-jwt-server-url nats://sys-user@host:4222
```

This is the production path when account churn outgrows a static preload. The
server image, init container, sizing, and CI/CD all carry over unchanged — only
the resolver block and the push step are new.

## Beyond one cluster

For multi-region or very large deployments, NATS offers **superclusters**
(gateways) and **leaf nodes** (edge). Those are out of scope for this stack but
compose cleanly on top of it; see the references.

## References

- [JetStream Clustering](https://docs.nats.io/running-a-nats-service/configuration/clustering/jetstream_clustering)
- [Clustering Configuration](https://docs.nats.io/running-a-nats-service/configuration/clustering/cluster_config)
- [Account lookup / resolvers](https://docs.nats.io/running-a-nats-service/configuration/securing_nats/auth_intro/jwt/resolver)
- [Superclusters & Leaf Nodes](https://docs.nats.io/running-a-nats-service/configuration/leafnodes)
