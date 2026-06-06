# TLS & Certificates

Client connections (`:4222`) are TLS-terminated by NATS. Cluster routes
(`:6222`) are **plaintext** on the isolated Docker network by design (see the
implementation plan). TLS is layered in three modes via `NATS_TLS_MODE`.

## Modes (`NATS_TLS_MODE`)

| Mode | Behaviour |
| --- | --- |
| `selfsigned` (default) | One 4096-bit, 10-year self-signed cert **shared** by all three nodes is generated on first boot. SAN covers `nats-1`, `nats-2`, `nats-3`, `localhost`, `127.0.0.1`, and `NATS_CLIENT_HOSTNAME`. Zero-config; clients trust the generated `ca.pem`. |
| `managed` | Uses `cert.pem`/`key.pem` written into the shared certs volume by the `traefik-certs-dumper` sidecar (real Let's Encrypt). Falls back to self-signed if absent (waits up to `NATS_TLS_MANAGED_WAIT`s). |
| `byo` | Uses operator-provided `cert.pem`/`key.pem` placed in the shared certs volume; fails fast if missing. |

In every mode the entrypoint guarantees `ca.pem` exists and `chmod 600`s the key.

## Shared cert, race-safe

All three nodes mount the **same** `nats-certs` volume. A client connecting to
any node must validate against one CA, so the nodes share a single cert whose
SAN lists every node name. On cold boot all three start at once; the entrypoint
uses an atomic `mkdir`-lock so exactly **one** node generates the cert and the
others wait for it. JetStream *storage* is never shared — only certs.

```
nats-1 ─┐
nats-2 ─┼─▶  nats-certs volume  (cert.pem / key.pem / ca.pem)  ── mkdir-lock guards generation
nats-3 ─┘            │
                     ▼
            nats-box / nats-init mount ca.pem (read-only) to verify the server
```

## Client verification (`NATS_TLS_VERIFY`)

- `false` (default) — the server presents a cert but does **not** require client
  certificates (no mTLS). Clients still verify the server via `ca.pem`.
- `true` — **mTLS**: the server requires and verifies client certificates. Use
  for zero-trust networks. You must issue client certs from the same CA and
  distribute them; the self-signed flow does not mint per-client certs, so
  switch to `managed`/`byo` with a real CA for mTLS.

## Trusting the self-signed CA

`nats-box` and `nats-init` already mount the CA (`/certs/ca.pem`) and set
`NATS_CA`. For an external client, copy the CA out of the volume:

```bash
docker cp <stack>_NODE1:/etc/nats/certs/ca.pem ./ca.pem
nats --tlsca ./ca.pem --creds ./creds/app.creds --server nats://host:4222 ...
```

## Managed Let's Encrypt (Traefik)

```
Traefik (solves ACME, stores in acme.json)
        │
        ▼
certs-dumper  ──writes──▶  nats-certs volume  (cert.pem, key.pem)
                                   │
                                   ▼
                              nats-N  ──serves──▶  client TLS :4222
```

```bash
# .env: NATS_TLS_MODE=managed   (and set TRAEFIK_ACME_FILE)
docker compose -f docker-compose.traefik.yml --profile tls-letsencrypt up -d
```

The dumper writes the default certificate; ensure Traefik serves the client
hostname's certificate as default (or extend the dumper command with
`--domain-subdir` + a post-hook).

## Bring your own (`byo`)

Place your PEM files into the shared certs volume before starting:

```bash
docker volume create <stack>-certs
docker run --rm -v <stack>-certs:/certs -v "$PWD":/in alpine \
  sh -c "cp /in/cert.pem /in/key.pem /in/ca.pem /certs/"
# .env: NATS_TLS_MODE=byo
```

## NATS WebSocket (optional, `wss://`)

NATS can serve browser clients over WebSocket. It is **off by default**. To
enable, add a `websocket {}` block to the server config and expose it via
Traefik (`WS_HOSTNAME`). Example block:

```hocon
websocket {
  port: 8080
  tls {
    cert_file: "/etc/nats/certs/cert.pem"
    key_file:  "/etc/nats/certs/key.pem"
  }
  # no_tls: true        # only behind a TLS-terminating proxy
  compression: true
}
```
