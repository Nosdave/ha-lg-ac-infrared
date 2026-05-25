"""
Fetch the ESPHome noise PSK for the XIAO IR Mate from Home Assistant via SSH
and write it to the local secrets file. The PSK value is never printed.

Requires:
- ssh CLI on PATH (Windows OpenSSH ships with this)
- `ssh ha-green` already configured (e.g., in %USERPROFILE%\\.ssh\\config)

Run:
    python scripts\\fetch_psk.py
    # then run capture_ir.py — it auto-picks up the file
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

OUT = Path(r"c:/Users/david/dev/.secrets/ha-green/xiao-ir-mate-7cb510.psk")
ENTRY_ID = "01KSFFNATP4DXX489WNF24TXE1"

REMOTE_PY = f'''
import json, sys
d = json.load(open("/config/.storage/core.config_entries"))
for e in d["data"]["entries"]:
    if e.get("entry_id") == "{ENTRY_ID}":
        sys.stdout.write(e["data"]["noise_psk"])
        sys.exit(0)
sys.exit("entry not found")
'''


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)

    proc = subprocess.run(
        ["ssh", "ha-green", "python3", "-"],
        input=REMOTE_PY,
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        print(f"FAIL: ssh/python3 returned {proc.returncode}", file=sys.stderr)
        if proc.stderr.strip():
            print(f"stderr: {proc.stderr.strip()[:200]}", file=sys.stderr)
        return 1

    psk = proc.stdout.strip()
    if not psk:
        print("FAIL: empty PSK in HA storage", file=sys.stderr)
        return 2

    # Base64-encoded 32-byte noise key is exactly 44 chars ending in '='
    # We check the shape but do NOT log the value.
    if len(psk) < 40 or len(psk) > 48:
        print("FAIL: PSK shape unexpected (refusing to write)", file=sys.stderr)
        return 3

    OUT.write_text(psk, encoding="utf-8")
    print(f"OK: wrote PSK to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
