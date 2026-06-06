#!/usr/bin/env python3
"""
NATS Init - Declarative JetStream Provisioning

Reads a JSON topology file and applies it to a NATS JetStream cluster via the
`nats` CLI (operator/JWT auth). Designed to be idempotent - safe to run on every
container start: each resource is probed (info/status) and created or, for
streams, converged (edit) only when needed.

Config sources, processed in order:
  1. Built-in default (/app/config/default.json) - OPTIONAL; none ships.
  2. User topology - /config/nats-topology.json (override with NATS_INIT_CONFIG).
     A volume mount in production (seeded with the demo on first boot) or a repo
     bind-mount in development.

JSON values may contain ${ENV_VAR} placeholders for value injection.
"""

import json
import os
import shutil
import sys
import time
from importlib import import_module
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

from nats_cli import NatsCli, resolve_config_values

console = Console()

DEFAULT_CONFIG = "/app/config/default.json"
# User config lives on a mounted volume (prod) or a repo bind-mount (dev).
FALLBACK_USER_CONFIG = "/config/nats-topology.json"
# Baked demo template seeded into the user-config path on first boot if missing.
SEED_CONFIG = "/app/config/seed.json"


def get_client_config() -> dict:
    """Get NATS connection configuration from environment variables."""
    return {
        "urls": os.environ.get(
            "NATS_URLS", "nats://nats-1:4222,nats://nats-2:4222,nats://nats-3:4222"
        ),
        "creds": os.environ.get("NATS_CREDS", "/creds/admin.creds"),
        "tls_ca": os.environ.get("NATS_TLS_CA") or None,
    }


def wait_for_jetstream(client: NatsCli, timeout: int = 120) -> bool:
    """Wait for the JetStream API to answer on the APP account.

    The init container is gated on `service_started` (not `service_healthy`),
    so it may start before the cluster has elected a meta-group leader. Polling
    `nats account info` exercises the JS API end-to-end and only succeeds once a
    leader is up and our creds are valid.
    """
    console.print("[dim]Waiting for JetStream API (meta-group leader)...[/]")

    start_time = time.time()
    last_error = "timeout"

    while time.time() - start_time < timeout:
        res = client.account_info()
        if res.ok:
            console.print("[green]JetStream API is ready[/]")
            return True
        last_error = res.error
        time.sleep(2)

    console.print(f"[red]JetStream not ready after {timeout}s: {last_error}[/]")
    return False


def load_config(config_path: str) -> dict | None:
    """Load and resolve a JSON configuration file. Returns None if missing."""
    path = Path(config_path)
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        raw_config = json.load(f)
    return resolve_config_values(raw_config)


def seed_user_config(path: str) -> None:
    """First-boot seeding: if the user config is missing and a baked seed exists,
    copy it into place. On a writable volume this creates an editable demo on
    first start; on a read-only bind mount (dev) it's a harmless no-op.
    """
    target = Path(path)
    seed = Path(SEED_CONFIG)
    if target.exists() or not seed.exists():
        return
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(seed, target)
        console.print(f"[green]Seeded demo topology -> {path}[/]")
    except OSError as e:
        console.print(f"[yellow]Could not seed {path} (read-only mount?): {e}[/]")


def discover_configs() -> list[tuple[str, dict]]:
    """Discover and load configuration files in order (default, then user)."""
    configs: list[tuple[str, dict]] = []

    default = load_config(DEFAULT_CONFIG)
    if default:
        configs.append(("default", default))

    user_config_path = os.environ.get("NATS_INIT_CONFIG", FALLBACK_USER_CONFIG)
    seed_user_config(user_config_path)
    if user_config_path != DEFAULT_CONFIG and Path(user_config_path).exists():
        user_config = load_config(user_config_path)
        if user_config:
            configs.append(("user", user_config))

    return configs


def discover_tasks() -> list:
    """Discover initialization tasks from the tasks/ directory (numbered files)."""
    tasks_dir = Path(__file__).parent / "tasks"
    tasks = []

    for task_file in sorted(tasks_dir.glob("*.py")):
        if task_file.name.startswith("_"):
            continue

        module_name = f"tasks.{task_file.stem}"
        try:
            module = import_module(module_name)
            if hasattr(module, "run"):
                tasks.append({
                    "name": getattr(module, "TASK_NAME", task_file.stem),
                    "description": getattr(module, "TASK_DESCRIPTION", ""),
                    "config_key": getattr(module, "CONFIG_KEY", None),
                    "module": module,
                })
        except Exception as e:  # noqa: BLE001
            console.print(f"[yellow]Warning: Failed to load task {task_file.name}: {e}[/]")

    return tasks


def process_config(label: str, config: dict, tasks: list, client: NatsCli) -> tuple[int, int, int]:
    """Process a single config through all tasks. Returns (applied, skipped, failed)."""
    applied = 0
    skipped = 0
    failed = 0

    for task in tasks:
        task_name = task["name"]
        config_key = task["config_key"]

        if config_key and not config.get(config_key):
            skipped += 1
            continue

        console.print(f"[bold]> {task_name}[/]")
        if task["description"]:
            console.print(f"  [dim]{task['description']}[/]")

        try:
            items = config.get(config_key, []) if config_key else []
            result = task["module"].run(items, console, client=client, config=config)

            if result.get("skipped"):
                console.print(f"  [dim]Skipped: {result.get('message', 'Not applicable')}[/]")
                skipped += 1
            elif result.get("changed"):
                console.print(f"  [green]+ {result.get('message', 'Done')}[/]")
                applied += 1
            else:
                console.print(f"  [blue]= {result.get('message', 'Already configured')}[/]")
                applied += 1
        except Exception as e:  # noqa: BLE001
            console.print(f"  [red]x Failed: {e}[/]")
            failed += 1

        console.print()

    return applied, skipped, failed


def main() -> int:
    console.print(Panel.fit(
        "[bold blue]NATS Init[/]\n"
        "[dim]Declarative JetStream Provisioning[/]",
        border_style="blue",
    ))
    console.print()

    cfg = get_client_config()
    console.print(f"[dim]Servers: {cfg['urls']} (creds: {cfg['creds']})[/]")
    console.print()

    client = NatsCli(cfg["urls"], cfg["creds"], cfg["tls_ca"])

    timeout = int(os.environ.get("NATS_WAIT_TIMEOUT", "120"))
    if not wait_for_jetstream(client, timeout):
        return 1
    console.print()

    tasks = discover_tasks()
    if not tasks:
        console.print("[yellow]No initialization tasks found[/]")
        return 0

    try:
        configs = discover_configs()
    except (ValueError, json.JSONDecodeError) as e:
        console.print(f"[red]Error loading config: {e}[/]")
        return 1

    if not configs:
        console.print("[yellow]No configuration files found[/]")
        return 0

    total_applied = total_skipped = total_failed = 0

    for label, config in configs:
        console.print(f"[bold cyan]-- Processing {label} configuration --[/]")
        console.print()
        applied, skipped, failed = process_config(label, config, tasks, client)
        total_applied += applied
        total_skipped += skipped
        total_failed += failed

    console.print("-" * 50)
    if total_failed == 0:
        console.print(
            f"[green]Initialization complete "
            f"({total_applied} applied, {total_skipped} skipped)[/]"
        )
        return 0

    console.print(
        f"[red]Initialization had errors "
        f"({total_failed} failed, {total_applied} applied, {total_skipped} skipped)[/]"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
