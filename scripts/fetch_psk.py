"""Fetch an ESPHome noise PSK for the XIAO IR Mate from a Home Assistant
instance via SSH and write it to a local secrets file. The PSK value is
never printed.

Requires:
- ssh CLI on PATH (Windows OpenSSH ships with this)
- An ssh alias / config entry that lets `ssh <host>` reach a HA shell
  with passwordless access to /config/.storage/core.config_entries
  (e.g. via the Advanced SSH & Web Terminal add-on)

Run:
    python scripts/fetch_psk.py
    # Auto-discovers any ESPHome entry whose host or title matches
    # "xiao-ir-mate" and writes its PSK.

    python scripts/fetch_psk.py --ssh-host my-ha --entry-id 01ABC...
    # Or be explicit about the SSH alias + which config entry to read.

    python scripts/fetch_psk.py --list
    # Just list ESPHome entries on the HA side, no write.

Override the output path with --out, or via $LG_AC_PSK_FILE.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

DEFAULT_OUT = Path(
    os.environ.get(
        "LG_AC_PSK_FILE",
        str(Path.home() / ".secrets" / "ha-green" / "xiao-ir-mate.psk"),
    )
)
DEFAULT_SSH_HOST = "ha-green"


REMOTE_LIST_SCRIPT = """
import json, sys
d = json.load(open("/config/.storage/core.config_entries"))
for e in d["data"]["entries"]:
    if e.get("domain") != "esphome":
        continue
    data = e.get("data", {}) or {}
    print(f"{e['entry_id']}\\t{e.get('title','')}\\t{data.get('host','')}")
"""

REMOTE_FETCH_SCRIPT = """
import json, sys
target_entry = sys.argv[1] if len(sys.argv) > 1 else ""
target_match = sys.argv[2].lower() if len(sys.argv) > 2 else "xiao-ir-mate"
d = json.load(open("/config/.storage/core.config_entries"))
hits = []
for e in d["data"]["entries"]:
    if e.get("domain") != "esphome":
        continue
    if target_entry and e.get("entry_id") != target_entry:
        continue
    data = e.get("data", {}) or {}
    hay = " ".join([
        e.get("title", "") or "",
        data.get("host", "") or "",
        data.get("device_name", "") or "",
    ]).lower()
    if not target_entry and target_match not in hay:
        continue
    psk = data.get("noise_psk")
    if not psk:
        continue
    hits.append((e["entry_id"], psk))
if not hits:
    sys.exit("no matching ESPHome entry with a noise_psk")
if len(hits) > 1:
    sys.exit(
        "multiple matching ESPHome entries — pass --entry-id, candidates: "
        + ", ".join(h[0] for h in hits)
    )
sys.stdout.write(hits[0][1])
"""


def _run_remote(ssh_host: str, script: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["ssh", ssh_host, "python3", "-", *args],
        input=script,
        text=True,
        capture_output=True,
    )


def cmd_list(ssh_host: str) -> int:
    proc = _run_remote(ssh_host, REMOTE_LIST_SCRIPT)
    if proc.returncode != 0:
        print(
            f"FAIL: ssh/python3 returned {proc.returncode}",
            file=sys.stderr,
        )
        if proc.stderr.strip():
            print(f"stderr: {proc.stderr.strip()[:300]}", file=sys.stderr)
        return 1
    print("entry_id\ttitle\thost")
    print(proc.stdout, end="")
    return 0


def cmd_fetch(
    ssh_host: str, out: Path, entry_id: str, match: str
) -> int:
    out.parent.mkdir(parents=True, exist_ok=True)
    proc = _run_remote(ssh_host, REMOTE_FETCH_SCRIPT, entry_id, match)
    if proc.returncode != 0:
        print(
            f"FAIL: ssh/python3 returned {proc.returncode}",
            file=sys.stderr,
        )
        if proc.stderr.strip():
            print(f"stderr: {proc.stderr.strip()[:300]}", file=sys.stderr)
        return 2
    psk = proc.stdout.strip()
    if not psk:
        print("FAIL: empty PSK returned", file=sys.stderr)
        return 3
    # Base64-encoded 32-byte noise key is exactly 44 chars ending in '='.
    # Shape-check only; never log the value.
    if len(psk) < 40 or len(psk) > 48:
        print("FAIL: PSK shape unexpected — refusing to write", file=sys.stderr)
        return 4
    out.write_text(psk, encoding="utf-8")
    print(f"OK: wrote PSK to {out}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--ssh-host",
        default=DEFAULT_SSH_HOST,
        help=f"SSH alias of the HA host (default: {DEFAULT_SSH_HOST})",
    )
    ap.add_argument(
        "--entry-id",
        default="",
        help="ESPHome config-entry ULID (skip auto-discovery)",
    )
    ap.add_argument(
        "--match",
        default="xiao-ir-mate",
        help=(
            "Auto-discovery substring matched against entry title/host/"
            "device_name (default: xiao-ir-mate)"
        ),
    )
    ap.add_argument(
        "--out",
        default=str(DEFAULT_OUT),
        help=f"Output PSK file (default: {DEFAULT_OUT})",
    )
    ap.add_argument(
        "--list",
        action="store_true",
        help="List ESPHome entries on the HA side, don't fetch",
    )
    args = ap.parse_args()

    if args.list:
        return cmd_list(args.ssh_host)
    return cmd_fetch(args.ssh_host, Path(args.out), args.entry_id, args.match)


if __name__ == "__main__":
    sys.exit(main())
