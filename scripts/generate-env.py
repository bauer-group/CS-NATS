#!/usr/bin/env python3
"""
generate-env.py — Create a `.env` file from `.env.example` with secure secrets.

Cross-platform (Windows / Linux / macOS), pure stdlib, no dependencies.

Replaces every `CHANGE_ME_*` placeholder for known secret keys with a fresh
random hex string. Output values use only `[0-9a-f]` and are therefore
shell-safe, URL-safe, and quote-free — no `#`, `$`, backtick, or whitespace
that could trip up shell parsers, .env loaders, or copy/paste flows.

This handles the random hex secrets only (e.g. the cluster route password). The
operator/JWT credentials (NATS_OPERATOR_JWT, NATS_*_ACCOUNT_*) are produced by
`generate-credentials.py`, which runs nsc and fills those values in afterwards.

Usage
-----
From the repo root:

    python scripts/generate-env.py                # writes .env (refuses to overwrite)
    python scripts/generate-env.py --force        # overwrite an existing .env (creates .env.bak first)
    python scripts/generate-env.py --dry-run      # show what would change, write nothing
    python scripts/generate-env.py --print        # write nothing, dump rendered file to stdout
    python scripts/generate-env.py --output .env.local --example custom.env.example

Exit codes
----------
    0  success
    1  precondition failed (.env.example missing, .env exists without --force, etc.)
    2  no replacements happened (the example had no CHANGE_ME_* lines for known keys)
"""

from __future__ import annotations

import argparse
import os
import re
import secrets
import shutil
import stat
import sys
from pathlib import Path


# --- Configuration -----------------------------------------------------------
# Each entry maps an env var name to (random_byte_count, human description).
# token_hex(n) returns 2n hex chars, matching `openssl rand -hex n`.

SECRETS: dict[str, tuple[int, str]] = {
    # 32 bytes (64 hex chars) — cluster route authorization password (node-to-node)
    "NATS_ROUTE_PASSWORD": (32, "Cluster route authorization password"),
}

PLACEHOLDER_RE = re.compile(r"CHANGE_ME_[A-Z0-9_]*")


# --- Core --------------------------------------------------------------------

def render(example_text: str) -> tuple[str, dict[str, str], list[str]]:
    """
    Replace each known secret's CHANGE_ME line with a fresh hex value.

    Returns:
        rendered text, mapping of replaced keys → preview ("xxxx…xxxx"),
        list of warnings about lines that didn't get replaced.
    """
    rendered = example_text
    replaced: dict[str, str] = {}
    warnings: list[str] = []

    for name, (byte_count, _desc) in SECRETS.items():
        # Match the line exactly: NAME=CHANGE_ME_anything (until end of line)
        pattern = re.compile(
            rf"^({re.escape(name)}=)CHANGE_ME_[^\r\n]*$",
            re.MULTILINE,
        )

        new_value = secrets.token_hex(byte_count)
        rendered, count = pattern.subn(
            lambda m, v=new_value: m.group(1) + v,
            rendered,
            count=1,
        )

        if count == 0:
            if re.search(rf"^{re.escape(name)}=", rendered, re.MULTILINE):
                warnings.append(
                    f"{name}: present but not a CHANGE_ME placeholder — left untouched"
                )
            else:
                warnings.append(f"{name}: not found in example file")
        else:
            replaced[name] = preview(new_value)

    # Any leftover CHANGE_ME placeholders on non-comment lines.
    leftover_lines = [
        ln for ln in rendered.splitlines()
        if PLACEHOLDER_RE.search(ln) and not ln.lstrip().startswith("#")
    ]
    leftovers = sorted({m for ln in leftover_lines for m in PLACEHOLDER_RE.findall(ln)})
    if leftovers:
        warnings.append(
            "remaining CHANGE_ME_* placeholders (review manually): "
            + ", ".join(leftovers)
        )

    return rendered, replaced, warnings


def preview(secret: str) -> str:
    """Mask the middle of a secret for display: 'abcd...wxyz'."""
    if len(secret) <= 12:
        return "*" * len(secret)
    return f"{secret[:4]}...{secret[-4:]} ({len(secret)} chars)"


# --- File handling -----------------------------------------------------------

def resolve_path(arg: str, base: Path) -> Path:
    """Resolve a path argument relative to `base` if not absolute."""
    p = Path(arg)
    return p if p.is_absolute() else (base / p)


def harden_permissions(path: Path) -> bool:
    """Set 0o600 on POSIX. Returns True if applied, False on Windows/no-op."""
    if os.name == "nt":
        return False
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        return True
    except OSError:
        return False


# --- CLI ---------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="generate-env.py",
        description=(
            "Generate a .env file from .env.example with secure random secrets. "
            "Hex-only values: shell-safe, URL-safe, no special characters."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--example", default=".env.example",
        help="path to the example file (default: .env.example, relative to repo root)",
    )
    p.add_argument(
        "--output", default=".env",
        help="path to write the generated file (default: .env, relative to repo root)",
    )
    p.add_argument(
        "-f", "--force", action="store_true",
        help="overwrite an existing output file (a .bak is created first)",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="show what would change, write nothing",
    )
    p.add_argument(
        "--print", action="store_true", dest="print_only",
        help="write nothing, print the rendered file to stdout (use with redirection)",
    )
    p.add_argument(
        "--no-harden", action="store_true",
        help="skip chmod 600 on the generated file (POSIX only; default: harden)",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    repo_root = Path(__file__).resolve().parent.parent
    example_path = resolve_path(args.example, repo_root)
    output_path = resolve_path(args.output, repo_root)

    # --- Preconditions ---
    if not example_path.exists():
        print(f"error: example file not found: {example_path}", file=sys.stderr)
        return 1

    if output_path.exists() and not (args.force or args.dry_run or args.print_only):
        print(
            f"error: {output_path} already exists. "
            f"Use --force to overwrite (a .bak will be created), "
            f"--dry-run to preview, or --print to dump to stdout.",
            file=sys.stderr,
        )
        return 1

    # --- Render ---
    example_text = example_path.read_text(encoding="utf-8")
    rendered, replaced, warnings = render(example_text)

    if not replaced:
        print(
            "error: no known CHANGE_ME_* placeholders were replaced. "
            "Check that the example file matches the expected format.",
            file=sys.stderr,
        )
        for w in warnings:
            print(f"  - {w}", file=sys.stderr)
        return 2

    # --- Output mode dispatch ---
    if args.print_only:
        sys.stdout.write(rendered)
        return 0

    if args.dry_run:
        print(f"would write: {output_path}")
        print(f"replacements ({len(replaced)}):")
        for name, prev in replaced.items():
            print(f"  {name:24s} {prev}")
        if warnings:
            print("warnings:")
            for w in warnings:
                print(f"  ! {w}")
        return 0

    # --- Write ---
    if output_path.exists():
        backup = output_path.with_suffix(output_path.suffix + ".bak")
        shutil.copy2(output_path, backup)
        print(f"backup: {backup}")

    # Write with explicit UTF-8 + LF newlines for cross-platform consistency.
    output_path.write_text(rendered, encoding="utf-8", newline="\n")

    hardened = False
    if not args.no_harden:
        hardened = harden_permissions(output_path)

    # --- Report ---
    print(f"wrote:  {output_path}")
    print(f"secrets generated ({len(replaced)}):")
    for name, prev in replaced.items():
        desc = SECRETS[name][1]
        print(f"  {name:24s} {prev}  # {desc}")
    if hardened:
        print("permissions: 0600 (owner read/write only)")
    elif os.name != "nt" and not args.no_harden:
        print("permissions: chmod failed — set them manually with `chmod 600 .env`")
    if warnings:
        print("warnings:")
        for w in warnings:
            print(f"  ! {w}")

    print()
    print("next: bootstrap the operator/JWT credentials (requires Docker):")
    print("      python scripts/generate-credentials.py")
    print("      then start the stack:")
    print("      docker compose -f docker-compose.development.yml up -d --build")
    return 0


def reconfigure_stdout_utf8() -> None:
    """
    Force stdout/stderr to UTF-8 on platforms where the default is cp1252
    (Windows). Needed for --print which dumps a UTF-8 file containing
    arrows/box-drawing characters.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, OSError):
            pass


if __name__ == "__main__":
    reconfigure_stdout_utf8()
    sys.exit(main())
