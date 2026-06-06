#!/usr/bin/env python3
"""
generate-credentials.py — Bootstrap the NATS operator/JWT trust chain with nsc.

Cross-platform (Windows / Linux / macOS), pure stdlib. Requires Docker — it runs
`nsc` INSIDE the official `natsio/nats-box` image, so you do NOT need nsc, nk, or
Go installed on the host.

What it creates (decentralized JWT auth, MEMORY resolver):

    operator  ──signs──▶  SYS account (system account)  ──▶  sys-user
                          APP account (JetStream on)     ──▶  admin, app

It then splits the artifacts into two classes:

  PUBLIC  (written into .env, safe — only public keys + permission grants):
    NATS_OPERATOR_JWT, NATS_SYS_ACCOUNT_ID/JWT, NATS_APP_ACCOUNT_ID/JWT
    -> the server preloads these (resolver_preload) via the rendered auth.conf.

  SECRET  (written to ./creds/, gitignored — contain private nkey seeds):
    sys-user.creds (SYS), admin.creds (APP), app.creds (APP)
    -> mounted read-only into nats-box and nats-init.

The nsc keystore itself (operator/account/user SEEDS) lives under ./.nsc
(gitignored). Keep it safe: it is the root of trust for the cluster.

Usage
-----
    python scripts/generate-credentials.py                 # bootstrap (refuses if .nsc exists)
    python scripts/generate-credentials.py --force         # wipe .nsc + creds and regenerate
    python scripts/generate-credentials.py --dry-run       # show what it would do, change nothing
    python scripts/generate-credentials.py --operator NAME --js-disk 20G

Exit codes
----------
    0  success
    1  precondition failed (Docker missing, .env missing, .nsc exists without --force, nsc error)
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# PUBLIC env keys filled in .env from the nsc artifacts.
ENV_KEYS = (
    "NATS_OPERATOR_JWT",
    "NATS_SYS_ACCOUNT_ID",
    "NATS_SYS_ACCOUNT_JWT",
    "NATS_APP_ACCOUNT_ID",
    "NATS_APP_ACCOUNT_JWT",
)

# (account, user, output filename) for the exported SECRET creds.
CREDS = (
    ("SYS", "sys-user", "sys-user.creds"),
    ("APP", "admin", "admin.creds"),
    ("APP", "app", "app.creds"),
)


# --- nsc-in-docker -----------------------------------------------------------

class Nsc:
    """Runs nsc inside natsio/nats-box with a host-mounted keystore (./.nsc)."""

    def __init__(self, nsc_home: Path, image: str):
        self.home = nsc_home
        self.image = image

    def _docker(self, entrypoint: str, args: list[str]) -> subprocess.CompletedProcess:
        host = self.home.as_posix()  # docker-friendly on Windows too (C:/...)
        cmd = [
            "docker", "run", "--rm",
            "-v", f"{host}:/nsc",
            "-e", "XDG_DATA_HOME=/nsc/data",
            "-e", "XDG_CONFIG_HOME=/nsc/config",
            "-e", "NKEYS_PATH=/nsc/keys",
            "-e", "HOME=/nsc",
            "--entrypoint", entrypoint,
            self.image, *args,
        ]
        return subprocess.run(cmd, capture_output=True, text=True)

    def run(self, *args: str) -> str:
        """Run an nsc subcommand; return stdout. Raises on non-zero exit."""
        proc = self._docker("nsc", list(args))
        if proc.returncode != 0:
            raise RuntimeError(
                f"nsc {' '.join(args)} failed (exit {proc.returncode}):\n"
                f"{(proc.stderr or proc.stdout).strip()}"
            )
        return proc.stdout


# --- JWT helpers (no verification — we only read public claims) ---------------

def jwt_subject(token: str) -> str:
    """Return the 'sub' claim (the operator/account public key) of a JWT."""
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError(f"not a JWT (expected 3 dot-separated parts): {token[:40]}...")
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    claims = json.loads(base64.urlsafe_b64decode(payload))
    return claims["sub"]


def clean_jwt(raw: str) -> str:
    """Extract a single JWT token from nsc --raw output (strip noise/whitespace)."""
    for line in raw.splitlines():
        line = line.strip()
        if line.count(".") == 2 and line.replace(".", "").replace("-", "").replace("_", "").isalnum():
            return line
    # Fallback: whole output stripped.
    candidate = raw.strip()
    if candidate.count(".") == 2:
        return candidate
    raise ValueError(f"could not find a JWT in nsc output:\n{raw[:200]}")


# --- bootstrap ---------------------------------------------------------------

def bootstrap(nsc: Nsc, operator: str, js_mem: str, js_disk: str,
              js_streams: int, js_consumers: int) -> None:
    """Create operator + SYS/APP accounts (APP with JetStream) + users."""
    # Operator + system account (SYS) in one shot.
    nsc.run("add", "operator", "--name", operator, "--sys")
    # Application account.
    nsc.run("add", "account", "APP")
    # Enable JetStream on the APP account (account-level limits).
    nsc.run(
        "edit", "account", "APP",
        "--js-mem-storage", js_mem,
        "--js-disk-storage", js_disk,
        "--js-streams", str(js_streams),
        "--js-consumer", str(js_consumers),
    )
    # Users.
    nsc.run("add", "user", "--account", "SYS", "sys-user")
    nsc.run("add", "user", "--account", "APP", "admin")
    nsc.run("add", "user", "--account", "APP", "app")


def extract_public(nsc: Nsc) -> dict[str, str]:
    """Collect the PUBLIC operator + account JWTs and their IDs."""
    op_jwt = clean_jwt(nsc.run("describe", "operator", "--raw"))
    sys_jwt = clean_jwt(nsc.run("describe", "account", "SYS", "--raw"))
    app_jwt = clean_jwt(nsc.run("describe", "account", "APP", "--raw"))
    return {
        "NATS_OPERATOR_JWT": op_jwt,
        "NATS_SYS_ACCOUNT_ID": jwt_subject(sys_jwt),
        "NATS_SYS_ACCOUNT_JWT": sys_jwt,
        "NATS_APP_ACCOUNT_ID": jwt_subject(app_jwt),
        "NATS_APP_ACCOUNT_JWT": app_jwt,
    }


def export_creds(nsc: Nsc, creds_dir: Path) -> list[Path]:
    """Write the SECRET .creds files for sys-user / admin / app."""
    creds_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for account, user, filename in CREDS:
        creds = nsc.run("generate", "creds", "--account", account, "--name", user)
        # nsc may emit a leading log line before the -----BEGIN block; trim to it.
        start = creds.find("-----BEGIN")
        body = creds[start:] if start != -1 else creds
        out = creds_dir / filename
        out.write_text(body, encoding="utf-8", newline="\n")
        written.append(out)
    return written


# --- .env update -------------------------------------------------------------

def update_env(env_path: Path, values: dict[str, str], dry_run: bool) -> list[str]:
    """Replace each ENV_KEYS line in .env with its value. Returns notes."""
    text = env_path.read_text(encoding="utf-8")
    notes = []
    for key in ENV_KEYS:
        value = values[key]
        pattern = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
        replacement = f"{key}={value}"
        new_text, count = pattern.subn(lambda _m, r=replacement: r, text, count=1)
        if count == 0:
            new_text = text + ("" if text.endswith("\n") else "\n") + replacement + "\n"
            notes.append(f"{key}: appended (no placeholder found)")
        else:
            notes.append(f"{key}: set")
        text = new_text
    if not dry_run:
        env_path.write_text(text, encoding="utf-8", newline="\n")
    return notes


# --- CLI ---------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="generate-credentials.py",
        description="Bootstrap the NATS operator/JWT trust chain with nsc (via Docker).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--operator", default="BAUERGROUP", help="operator name (default: BAUERGROUP)")
    p.add_argument("--image", default="natsio/nats-box:latest", help="nats-box image providing nsc")
    p.add_argument("--output", default=".env", help="env file to fill (default: .env)")
    p.add_argument("--js-mem", default="1G", help="APP account JetStream memory limit (default: 1G)")
    p.add_argument("--js-disk", default="10G", help="APP account JetStream disk limit (default: 10G)")
    p.add_argument("--js-streams", type=int, default=100, help="APP account max streams (default: 100)")
    p.add_argument("--js-consumers", type=int, default=1000, help="APP account max consumers (default: 1000)")
    p.add_argument("-f", "--force", action="store_true", help="wipe ./.nsc and ./creds and regenerate")
    p.add_argument("--dry-run", action="store_true", help="show what would happen, change nothing")
    return p.parse_args(argv)


def docker_available() -> bool:
    try:
        return subprocess.run(
            ["docker", "version"], capture_output=True, text=True
        ).returncode == 0
    except FileNotFoundError:
        return False


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    nsc_home = REPO_ROOT / ".nsc"
    creds_dir = REPO_ROOT / "creds"
    env_path = (REPO_ROOT / args.output) if not Path(args.output).is_absolute() else Path(args.output)

    # --- Preconditions ---
    if not docker_available():
        print("error: Docker is required (nsc runs inside natsio/nats-box). Start Docker and retry.",
              file=sys.stderr)
        return 1

    if not env_path.exists():
        print(f"error: {env_path} not found. Run 'python scripts/generate-env.py' first.",
              file=sys.stderr)
        return 1

    if nsc_home.exists() and any(nsc_home.iterdir()):
        if not args.force:
            print(f"error: {nsc_home} already exists. Use --force to wipe and regenerate.",
                  file=sys.stderr)
            print("       NOTE: regenerating invalidates existing .creds and requires wiping",
                  file=sys.stderr)
            print("       the cluster JetStream data volumes (docker compose down -v).",
                  file=sys.stderr)
            return 1
        if args.dry_run:
            print(f"[dry-run] would wipe {nsc_home} and {creds_dir}")
        else:
            for d in (nsc_home, creds_dir):
                try:
                    shutil.rmtree(d, ignore_errors=False)
                except OSError as e:
                    print(f"error: could not remove {d}: {e}", file=sys.stderr)
                    print("       (on Linux the keystore may be root-owned from the container; "
                          "remove it with sudo and retry).", file=sys.stderr)
                    return 1

    if args.dry_run:
        print("[dry-run] would run nsc inside", args.image, "to create:")
        print(f"          operator '{args.operator}', accounts SYS + APP (JetStream "
              f"mem={args.js_mem} disk={args.js_disk}), users sys-user/admin/app")
        print(f"[dry-run] would write creds: {', '.join(f for _, _, f in CREDS)} -> {creds_dir}")
        print(f"[dry-run] would set in {env_path}: {', '.join(ENV_KEYS)}")
        return 0

    nsc_home.mkdir(parents=True, exist_ok=True)
    nsc = Nsc(nsc_home, args.image)

    # --- Bootstrap ---
    print(f"Bootstrapping operator '{args.operator}' + SYS/APP accounts via nsc ({args.image})...")
    try:
        bootstrap(nsc, args.operator, args.js_mem, args.js_disk, args.js_streams, args.js_consumers)
        values = extract_public(nsc)
        written = export_creds(nsc, creds_dir)
    except (RuntimeError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    # --- Wire .env ---
    notes = update_env(env_path, values, dry_run=False)

    # --- Report ---
    print()
    print("operator/accounts created. Public IDs:")
    print(f"  system_account (SYS) : {values['NATS_SYS_ACCOUNT_ID']}")
    print(f"  application    (APP) : {values['NATS_APP_ACCOUNT_ID']}")
    print()
    print(f"public JWTs written to {env_path}:")
    for note in notes:
        print(f"  {note}")
    print()
    print(f"secret creds written to {creds_dir} (gitignored):")
    for path in written:
        print(f"  {path.name}")
    print()
    print("next: start the stack")
    print("      docker compose -f docker-compose.development.yml up -d --build")
    return 0


def reconfigure_stdout_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, OSError):
            pass


if __name__ == "__main__":
    reconfigure_stdout_utf8()
    sys.exit(main())
