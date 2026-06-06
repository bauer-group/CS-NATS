"""
Object Store Task

Creates JetStream Object Store buckets. Idempotent: probes `nats object info`,
then `add`s a missing bucket. Object bucket config is largely immutable after
creation, so an existing bucket is left as-is (reported, not edited).

JSON config example:
{
  "object_stores": [
    { "bucket": "app-blobs", "replicas": 3, "storage": "file", "ttl": "0s" }
  ]
}

`ttl` is a Go duration string (e.g. "24h", "0s"); "0s"/0/absent = no expiry.
"""

TASK_NAME = "Object Stores"
TASK_DESCRIPTION = "Create JetStream Object Store buckets (default replicas: 3)"
CONFIG_KEY = "object_stores"

DEFAULT_REPLICAS = 3


def _ttl_flag(value) -> list[str]:
    if not value or str(value) in ("0", "0s"):
        return []
    return [f"--ttl={value}"]


def run(items, console, *, client, **kwargs) -> dict:
    if not items:
        return {"skipped": True, "message": "No object stores configured"}

    created = 0
    existing = 0
    failed = 0

    for obj in items:
        bucket = obj["bucket"]
        if client.obj_exists(bucket):
            existing += 1
            console.print(f"    [dim]Object store exists: {bucket}[/]")
            continue

        args = [
            f"--replicas={obj.get('replicas', DEFAULT_REPLICAS)}",
            f"--storage={obj.get('storage', 'file')}",
            *_ttl_flag(obj.get("ttl")),
        ]
        if obj.get("max_bucket_size"):
            args.append(f"--max-bucket-size={obj['max_bucket_size']}")
        if obj.get("description"):
            args.append(f"--description={obj['description']}")

        res = client.obj_add(bucket, args)
        if res.ok:
            created += 1
            console.print(
                f"    [green]Created object store: {bucket} "
                f"(R{obj.get('replicas', DEFAULT_REPLICAS)})[/]"
            )
        else:
            failed += 1
            console.print(f"    [red]Failed object store {bucket}: {res.error}[/]")

    if failed:
        raise RuntimeError(f"{failed} object store(s) failed to create")

    return {
        "changed": created > 0,
        "message": f"{len(items)} object store(s) processed ({created} created, {existing} existing)",
    }
