"""
Consumer Task

Creates durable JetStream consumers on existing streams. Idempotent: probes
`nats consumer info <stream> <name>`, then `add`s a missing consumer from a
ConsumerConfig file. Existing consumers are left as-is (reported).

JSON config example:
{
  "consumers": [
    {
      "stream": "app-events",
      "name": "workers",
      "ack_policy": "explicit",
      "deliver_policy": "all",
      "max_deliver": 5,
      "ack_wait": 30000000000,
      "filter_subject": "app.events.>"
    }
  ]
}

`ack_wait` is nanoseconds (ConsumerConfig wire format). The consumer is created
durable (durable_name = name).
"""

import os

from nats_cli import write_temp_json

TASK_NAME = "Consumers"
TASK_DESCRIPTION = "Create durable JetStream consumers"
CONFIG_KEY = "consumers"

# Fields copied verbatim into the ConsumerConfig file (when present in the item).
_PASSTHROUGH = (
    "ack_policy", "deliver_policy", "replay_policy", "max_deliver",
    "ack_wait", "filter_subject", "filter_subjects", "max_ack_pending",
    "deliver_subject", "deliver_group", "description", "sample_freq",
)


def run(items, console, *, client, **kwargs) -> dict:
    if not items:
        return {"skipped": True, "message": "No consumers configured"}

    created = 0
    existing = 0
    failed = 0

    for c in items:
        stream = c["stream"]
        name = c["name"]

        if client.consumer_exists(stream, name):
            existing += 1
            console.print(f"    [dim]Consumer exists: {name} @ {stream}[/]")
            continue

        cfg = {"durable_name": name}
        for key in _PASSTHROUGH:
            if key in c:
                cfg[key] = c[key]

        cfg_file = write_temp_json(cfg)
        try:
            res = client.consumer_add(stream, name, cfg_file)
        finally:
            os.unlink(cfg_file)

        if res.ok:
            created += 1
            console.print(f"    [green]Created consumer: {name} @ {stream}[/]")
        else:
            failed += 1
            console.print(f"    [red]Failed consumer {name}@{stream}: {res.error}[/]")

    if failed:
        raise RuntimeError(f"{failed} consumer(s) failed to create")

    return {
        "changed": created > 0,
        "message": f"{len(items)} consumer(s) processed ({created} created, {existing} existing)",
    }
