# Security & Authentication (Operator / JWT)

This stack uses NATS **decentralized JWT authentication** (operator mode). It is
the most flexible and granular auth model NATS offers — multi-tenant accounts,
per-user permissions, and signed, verifiable credentials.

## The trust chain

```
operator  ──signs──▶  SYS account ($SYS, system account)  ──signs──▶  sys-user
                      APP account  (JetStream enabled)     ──signs──▶  admin, app
```

- **Operator** — the root of trust (an ed25519 nkey). Signs account JWTs.
- **Accounts** — isolation boundaries. Each is a signed JWT.
  - `SYS` — the **system account**. Powers internal cluster messaging, the
    `nats server …` admin commands, and monitoring. Required.
  - `APP` — the application account, with **JetStream enabled** (account-level
    limits set via nsc). Streams/KV/object stores live here.
- **Users** — clients. Each is a JWT signed by its account, packaged with its
  private nkey seed into a `.creds` file.
  - `sys-user` (SYS) — for `nats server` / surveyor-style operations.
  - `admin` (APP) — used by `nats-init` and `nats-box` to manage JetStream.
  - `app` (APP) — a least-privilege user for your applications.

## Public vs. secret — what goes where

| Artifact | Secret? | Location | Purpose |
| --- | --- | --- | --- |
| Operator JWT | **public** | `.env` (`NATS_OPERATOR_JWT`) | server trust anchor |
| Account JWTs (SYS, APP) | **public** | `.env` (`NATS_*_ACCOUNT_JWT`) | `resolver_preload` |
| Account IDs (public keys) | **public** | `.env` (`NATS_*_ACCOUNT_ID`) | `system_account`, preload keys |
| `*.creds` (JWT + nkey seed) | **SECRET** | `./creds/` (gitignored) | client login |
| nkey seeds (`.nsc/keys`) | **SECRET** | `./.nsc/` (gitignored) | the root keystore |

> **Why JWTs are safe to commit/inject as env:** an account/operator JWT is a
> signed assertion containing only **public keys** and permission grants — no
> private key material. The private seeds live only in `.creds` and the nsc
> keystore, both gitignored. Guard `./.nsc/` and `./creds/`.

## The resolver

The server resolves account JWTs via the **MEMORY resolver** — a fixed set
preloaded in `conf.d/auth.conf`:

```hocon
operator: "<operator JWT>"
system_account: <SYS account ID>
resolver: MEMORY
resolver_preload: {
  <SYS account ID>: "<SYS JWT>"
  <APP account ID>: "<APP JWT>"
}
```

`resolver_preload` **must** include the SYS account, and `system_account` must
point at it — otherwise internal `$SYS` messaging and the `nats-box` SYS
connection break.

The MEMORY resolver is ideal for a fixed account set (lean, no extra moving
parts). To add accounts dynamically without a config change, switch to the
**NATS full resolver** and push with `nsc push` — see [clustering.md](clustering.md).

## The bootstrap script

`scripts/generate-credentials.py` runs `nsc` inside `natsio/nats-box`:

```text
nsc add operator --name BAUERGROUP --sys
nsc add account APP
nsc edit account APP --js-mem-storage 1G --js-disk-storage 10G --js-streams 100 --js-consumer 1000
nsc add user --account SYS sys-user
nsc add user --account APP admin
nsc add user --account APP app
```

It then extracts the public JWTs (and decodes each account's public key from the
JWT `sub` claim) into `.env`, and exports the `.creds` files. Re-run with
`--force` to regenerate (a full cluster reset — see installation.md).

## Cluster route authentication

Operator/JWT auth applies to **client** connections (`:4222`). The cluster
routes (`:6222`) use simple username/password authorization
(`NATS_ROUTE_USER` / `NATS_ROUTE_PASSWORD`) on the isolated Docker network — a
`cluster.authorization` block cannot use JWTs. Keep the routes off any public
interface; the bridge network does this by default.

## Connecting an application

Mount the least-privilege `app.creds` and the CA, then point your client at the
cluster:

```bash
nats --creds ./creds/app.creds --tlsca ./creds-or-certs/ca.pem \
     --server nats://host-1:4222,nats://host-2:4222,nats://host-3:4222 \
     pub app.events.test "hello"
```

In code, NATS client libraries accept the `.creds` file path and a TLS CA. To
scope `app` more tightly, edit its permissions with
`nsc edit user --account APP app --allow-pub 'app.>' --allow-sub 'app.>'` and
re-export its creds.

## Hardening checklist

- [ ] `./.nsc/` and `./creds/` are gitignored (default) and access-controlled
- [ ] `NATS_ROUTE_PASSWORD` is a strong random value (`generate-env.py`)
- [ ] Monitoring `:8222` and the exporter `:7777` are not exposed publicly
      without a proxy + auth (the bridge network keeps them internal)
- [ ] The `app` user is scoped to its subjects (not the broad admin user)
- [ ] `NATS_TLS_VERIFY=true` (mTLS) for zero-trust client networks — see
      [tls-and-certificates.md](tls-and-certificates.md)
