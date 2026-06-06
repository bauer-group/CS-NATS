"""
Key/Value Task

Creates JetStream KV buckets. Idempotent: probes `nats kv status`, then `add`s a
missing bucket. KV bucket config is largely immutable after creation, so an
existing bucket is left as-is (reported, not edited).

JSON config example:
{
  "kv": [
    { "bucket": "app-config", "history": 5, "ttl": "0s", "replicas": 3, "storage": "file" }
  ]
}

`ttl` is a Go duration string (e.g. "24h", "0s"); "0s"/0/absent = no expiry.
"""

TASK_NAME = "Key/Value"
TASK_DESCRIPTION = "Create JetStream KV buckets (default replicas: 3)"
CONFIG_KEY = "kv"

DEFAULT_REPLICAS = 3


def _ttl_flag(value) -> list[str]:
    if not value or str(value) in ("0", "0s"):
        return []
    return [f"--ttl={value}"]


def run(items, console, *, client, **kwargs) -> dict:
    if not items:
        return {"skipped": True, "message": "No KV buckets configured"}

    created = 0
    existing = 0
    failed = 0

    for kv in items:
        bucket = kv["bucket"]
        if client.kv_exists(bucket):
            existing += 1
            console.print(f"    [dim]KV bucket exists: {bucket}[/]")
            continue

        args = [
            f"--history={kv.get('history', 1)}",
            f"--replicas={kv.get('replicas', DEFAULT_REPLICAS)}",
            f"--storage={kv.get('storage', 'file')}",
            *_ttl_flag(kv.get("ttl")),
        ]
        if kv.get("max_value_size"):
            args.append(f"--max-value-size={kv['max_value_size']}")
        if kv.get("description"):
            args.append(f"--description={kv['description']}")

        res = client.kv_add(bucket, args)
        if res.ok:
            created += 1
            console.print(
                f"    [green]Created KV bucket: {bucket} "
                f"(R{kv.get('replicas', DEFAULT_REPLICAS)})[/]"
            )
        else:
            failed += 1
            console.print(f"    [red]Failed KV bucket {bucket}: {res.error}[/]")

    if failed:
        raise RuntimeError(f"{failed} KV bucket(s) failed to create")

    return {
        "changed": created > 0,
        "message": f"{len(items)} KV bucket(s) processed ({created} created, {existing} existing)",
    }
