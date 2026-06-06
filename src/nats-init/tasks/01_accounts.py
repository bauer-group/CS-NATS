"""
Account Task

Sanity gate: confirms the APP account's JetStream API is reachable with the
provided credentials before any provisioning runs. Accounts themselves are
provisioned out-of-band by nsc (see docs/security-and-auth.md) — this task does
NOT create accounts; it surfaces the account's JetStream limits for visibility.

CONFIG_KEY is None: the task always runs (no config block needed).
"""

TASK_NAME = "Account"
TASK_DESCRIPTION = "Verify APP account JetStream is reachable (nsc-provisioned)"
CONFIG_KEY = None


def run(items, console, *, client, **kwargs) -> dict:
    res = client.account_info()
    if not res.ok:
        # wait_for_jetstream already gates on this, so a failure here is unusual.
        return {"changed": False, "message": f"account info unavailable: {res.error}"}

    # Print a couple of informative lines from `nats account info` output.
    for line in res.stdout.splitlines():
        stripped = line.strip()
        if any(k in stripped for k in ("Tier", "Storage", "Streams", "Memory", "Disk")):
            console.print(f"    [dim]{stripped}[/]")

    return {"changed": False, "message": "APP account JetStream reachable"}
