"""
Stream Task

Creates / converges JetStream streams. Idempotent: probes `nats stream info`,
then either `add`s a missing stream or `edit`s an existing one to the declared
config. `nats stream add` is only idempotent on identical config, so we branch
on existence rather than blind-adding.

JSON config example:
{
  "streams": [
    {
      "name": "app-events",
      "subjects": ["app.events.>"],
      "retention": "limits",
      "storage": "file",
      "num_replicas": 3,
      "max_age": 604800000000000,
      "max_bytes": 1073741824,
      "discard": "old",
      "duplicate_window": 120000000000
    }
  ]
}

Durations (max_age, duplicate_window) are nanoseconds — the JetStream wire
format the `nats stream --config` file expects.
"""

import os

from nats_cli import write_temp_json

TASK_NAME = "Streams"
TASK_DESCRIPTION = "Create/converge JetStream streams (default replicas: 3)"
CONFIG_KEY = "streams"

DEFAULT_REPLICAS = 3


def run(items, console, *, client, **kwargs) -> dict:
    if not items:
        return {"skipped": True, "message": "No streams configured"}

    created = 0
    updated = 0
    failed = 0

    for stream in items:
        name = stream["name"]
        cfg = dict(stream)
        cfg.setdefault("num_replicas", DEFAULT_REPLICAS)

        cfg_file = write_temp_json(cfg)
        try:
            if client.stream_exists(name):
                res = client.stream_edit(name, cfg_file)
                if res.ok:
                    updated += 1
                    console.print(f"    [dim]Stream converged: {name}[/]")
                else:
                    # Identical config or an immutable-field change — not fatal.
                    console.print(f"    [yellow]Stream exists, edit skipped ({name}): {res.error.splitlines()[0] if res.error else 'no changes'}[/]")
            else:
                res = client.stream_add(name, cfg_file)
                if res.ok:
                    created += 1
                    console.print(
                        f"    [green]Created stream: {name} "
                        f"(R{cfg['num_replicas']}, {cfg.get('storage', 'file')})[/]"
                    )
                else:
                    failed += 1
                    console.print(f"    [red]Failed stream {name}: {res.error}[/]")
        finally:
            os.unlink(cfg_file)

    if failed:
        raise RuntimeError(f"{failed} stream(s) failed to create")

    return {
        "changed": created > 0 or updated > 0,
        "message": f"{len(items)} stream(s) processed ({created} created, {updated} converged)",
    }
