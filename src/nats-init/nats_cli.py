"""
Shared helpers for the NATS init container.

Contains:
  - Environment-variable resolution for ${VAR} placeholders in JSON config
  - A thin wrapper around the `nats` CLI (idempotent existence probes + apply)

Kept import-light (stdlib only) so the pure helpers can be unit-tested without
a running cluster or the `nats` binary present.
"""

from __future__ import annotations

import json
import os
import re
import subprocess

DEFAULT_TIMEOUT = 30


# --- Environment variable resolution -----------------------------------------

_ENV_RE = re.compile(r"\$\{([^}]+)}")


def resolve_env_vars(value: str) -> str:
    """Replace ${VAR_NAME} patterns with environment variable values.

    Raises ValueError if a referenced variable is not set, so misconfiguration
    fails loudly at startup rather than silently provisioning blank values.
    """
    def replacer(match: "re.Match") -> str:
        var_name = match.group(1)
        env_value = os.environ.get(var_name)
        if env_value is None:
            raise ValueError(f"Environment variable '{var_name}' is not set")
        return env_value

    return _ENV_RE.sub(replacer, value)


def resolve_config_values(obj):
    """Recursively resolve environment variables in config values.

    Keys starting with '_' are treated as comments/metadata and passed through
    untouched (their values may legitimately contain literal ${...} examples).
    """
    if isinstance(obj, str):
        return resolve_env_vars(obj)
    if isinstance(obj, dict):
        return {
            k: (v if k.startswith("_") else resolve_config_values(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [resolve_config_values(item) for item in obj]
    return obj


# --- Result type -------------------------------------------------------------

class Result:
    """Outcome of a `nats` CLI invocation."""

    def __init__(self, returncode: int, stdout: str, stderr: str):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def error(self) -> str:
        return (self.stderr or self.stdout or f"exit {self.returncode}").strip()


# --- nats CLI wrapper --------------------------------------------------------

class NatsCli:
    """Thin wrapper around the `nats` CLI.

    Builds the common connection prefix once (server URLs + creds + TLS CA) and
    exposes idempotent existence probes and apply helpers. Existence is decided
    by the EXIT CODE of an `info`/`status` command (0 = exists), which is stable
    across CLI versions and avoids brittle output parsing.
    """

    def __init__(
        self,
        urls: str,
        creds: str | None = None,
        tls_ca: str | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        binary: str = "nats",
    ):
        self.timeout = timeout
        self.base = [binary, "--server", urls]
        if creds:
            self.base += ["--creds", creds]
        if tls_ca:
            self.base += ["--tlsca", tls_ca]

    # -- low level --
    def run(self, args: list[str]) -> Result:
        try:
            proc = subprocess.run(
                self.base + args,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
            return Result(proc.returncode, proc.stdout, proc.stderr)
        except subprocess.TimeoutExpired:
            return Result(124, "", f"timed out after {self.timeout}s: nats {' '.join(args)}")
        except FileNotFoundError as e:  # `nats` binary missing
            return Result(127, "", str(e))

    # -- readiness --
    def account_info(self) -> Result:
        """Return JetStream account info — succeeds only once the JS API answers."""
        return self.run(["account", "info"])

    # -- streams --
    def stream_exists(self, name: str) -> bool:
        return self.run(["stream", "info", name]).ok

    def stream_add(self, name: str, config_file: str) -> Result:
        return self.run(["stream", "add", name, "--config", config_file])

    def stream_edit(self, name: str, config_file: str) -> Result:
        return self.run(["stream", "edit", name, "--config", config_file, "-f"])

    # -- key/value --
    def kv_exists(self, bucket: str) -> bool:
        return self.run(["kv", "status", bucket]).ok

    def kv_add(self, bucket: str, args: list[str]) -> Result:
        return self.run(["kv", "add", bucket, *args])

    # -- object store --
    def obj_exists(self, bucket: str) -> bool:
        return self.run(["object", "info", bucket]).ok

    def obj_add(self, bucket: str, args: list[str]) -> Result:
        return self.run(["object", "add", bucket, *args])

    # -- consumers --
    def consumer_exists(self, stream: str, name: str) -> bool:
        return self.run(["consumer", "info", stream, name]).ok

    def consumer_add(self, stream: str, name: str, config_file: str) -> Result:
        return self.run(["consumer", "add", stream, name, "--config", config_file])


# --- helpers -----------------------------------------------------------------

def write_temp_json(payload: dict) -> str:
    """Write a config dict to a temp JSON file and return its path."""
    import tempfile

    fd, path = tempfile.mkstemp(suffix=".json", prefix="nats-cfg-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    return path
