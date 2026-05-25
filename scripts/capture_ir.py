"""
IR Capture Test — verifies that aioesphomeapi.subscribe_infrared_rf_receive
delivers events from the Xiao IR Mate, and captures raw LG-AC timings
for offline decoder development.

Run:
    pip install -r scripts/requirements.txt
    python scripts/capture_ir.py
    # Press buttons on LG remote, watch output.
    # Ctrl+C to stop. Captures saved to captures-YYYYMMDD-HHMMSS.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

DEFAULT_PSK_FILE = Path(
    os.environ.get(
        "LG_AC_PSK_FILE",
        str(Path.home() / ".secrets" / "ha-green" / "xiao-ir-mate.psk"),
    )
)

try:
    from aioesphomeapi import APIClient
    from aioesphomeapi.model import InfraredInfo
except ImportError:
    sys.exit("pip install -r scripts/requirements.txt  -- then retry")


# ---------- LG classic 28-bit decoder (heuristic, for live inspection) ----------

def looks_like_lg_classic(timings: list[int]) -> bool:
    if len(timings) < 5:
        return False
    return 7500 <= timings[0] <= 9500 and -5000 <= timings[1] <= -3500


def decode_lg_classic(timings: list[int]) -> int | None:
    """Decode classic LG 28-bit AC frame from raw mark/space timings.

    Positive = mark, negative = space (µs). Returns 28-bit int or None.
    """
    if not looks_like_lg_classic(timings):
        return None
    # 2 header entries + 28 bits * 2 entries + optional trailing mark
    if len(timings) < 2 + 28 * 2:
        return None
    bits = 0
    for i in range(28):
        space_us = -timings[2 + 2 * i + 1]
        bit = 1 if space_us > 1000 else 0
        bits = (bits << 1) | bit
    return bits


def lg_ac_checksum(code28: int) -> int:
    body = (code28 >> 4) & 0xFFFF
    s = 0
    for _ in range(4):
        s += body & 0xF
        body >>= 4
    return s & 0xF


def explain(code: int) -> str:
    sign = (code >> 20) & 0xFF
    power = (code >> 18) & 0b11
    mode = (code >> 12) & 0b111
    temp = (code >> 8) & 0xF
    fan = (code >> 4) & 0xF
    csum = code & 0xF
    exp_csum = lg_ac_checksum(code)
    modes = {0: "cool", 1: "dry", 2: "fan", 3: "auto", 4: "heat"}
    power_str = {0: "ON", 3: "OFF"}.get(power, f"P{power}")
    return (
        f"sign=0x{sign:02X}{'' if sign == 0x88 else '!!'} "
        f"power={power_str} mode={modes.get(mode, f'm{mode}')} "
        f"temp={temp + 15}C fan=0x{fan:X} "
        f"csum={'OK' if csum == exp_csum else f'BAD(got=0x{csum:X} exp=0x{exp_csum:X})'}"
    )


# ---------------------- main ----------------------

async def run(host: str, port: int, password: str | None,
              noise_psk: str | None, out_path: Path) -> int:
    captures: list[dict] = []

    print(f"[+] Connecting to {host}:{port} ...")
    client = APIClient(
        address=host, port=port,
        password=password, noise_psk=noise_psk,
    )
    try:
        await client.connect(login=True)
    except Exception as e:
        print(f"[!] Connect failed: {e}")
        return 2
    print("[+] Connected.")

    entities, _services = await client.list_entities_services()
    ir_entities = [e for e in entities if isinstance(e, InfraredInfo)]
    print(f"[+] Found {len(ir_entities)} infrared entit"
          f"{'y' if len(ir_entities) == 1 else 'ies'}:")
    rx_keys: set[int] = set()
    for e in ir_entities:
        caps_bits = getattr(e, "capabilities", 0)
        caps = []
        if caps_bits & 0x01:
            caps.append("TRANSMITTER")
        if caps_bits & 0x02:
            caps.append("RECEIVER")
            rx_keys.add(e.key)
        cap_str = ",".join(caps) if caps else f"raw=0x{caps_bits:x}"
        print(f"    key=0x{e.key:08x}  name={e.name!r}  caps={cap_str}")

    if not rx_keys:
        print("[!] No entity advertises RECEIVER capability. Listening anyway "
              "(all keys), but the proxy may not deliver events.")

    print("\n[+] Subscribing to infrared_rf_receive ...")
    print("[+] Press buttons on the LG remote. Ctrl+C to stop.")
    print(f"[+] Output file: {out_path}\n")

    counter = {"n": 0}

    def on_event(event) -> None:
        counter["n"] += 1
        n = counter["n"]
        ts = time.strftime("%H:%M:%S")
        timings = list(event.timings)
        head = f"[{ts}] #{n:03d} key=0x{event.key:08x} len={len(timings):3d}"
        decoded = decode_lg_classic(timings)
        if decoded is not None:
            print(f"{head}  LG28=0x{decoded:07X}  {explain(decoded)}")
        elif looks_like_lg_classic(timings):
            print(f"{head}  LG header but unexpected length")
        else:
            print(f"{head}  non-LG  head={timings[:6]}")
        captures.append({
            "n": n, "ts": time.time(), "key": event.key,
            "timings": timings,
            "lg28": f"0x{decoded:07X}" if decoded is not None else None,
        })

    try:
        unsub = client.subscribe_infrared_rf_receive(on_event)
    except AttributeError:
        await client.disconnect()
        print("[!] aioesphomeapi has no subscribe_infrared_rf_receive. "
              "Upgrade: pip install --upgrade aioesphomeapi")
        return 3

    try:
        while True:
            await asyncio.sleep(1)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        try:
            unsub()
        except Exception:
            pass
        await client.disconnect()

    if captures:
        out_path.write_text(json.dumps(captures, indent=2))
        print(f"\n[+] Saved {len(captures)} captures to {out_path}")
        return 0
    print("\n[!] No events received. Check that the receiver was pointed at "
          "the Mate and that the proxy delivers receive events.")
    return 1


def resolve_psk(cli_value: str | None) -> str | None:
    """Resolve noise PSK from (1) CLI flag, (2) env var, (3) default file."""
    if cli_value:
        return cli_value.strip()
    env = os.environ.get("ESPHOME_NOISE_PSK")
    if env:
        return env.strip()
    if DEFAULT_PSK_FILE.is_file():
        return DEFAULT_PSK_FILE.read_text(encoding="utf-8").strip()
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Capture IR events from a Xiao IR Mate")
    ap.add_argument(
        "--host",
        default=os.environ.get("LG_AC_HOST", "xiao-ir-mate.local"),
    )
    ap.add_argument("--port", type=int, default=6053)
    ap.add_argument("--password", default=None,
                    help="ESPHome API password (legacy auth, usually empty)")
    ap.add_argument("--noise-psk", default=None,
                    help=(f"ESPHome API encryption key. If omitted, reads "
                          f"ESPHOME_NOISE_PSK env or {DEFAULT_PSK_FILE}"))
    ap.add_argument("--out", default=None,
                    help="Output JSON file (default: captures-YYYYMMDD-HHMMSS.json)")
    args = ap.parse_args()
    out_path = Path(args.out) if args.out else Path(
        f"captures-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    )
    psk = resolve_psk(args.noise_psk)
    if not psk:
        print(f"[!] No noise PSK found. Provide --noise-psk, set "
              f"ESPHOME_NOISE_PSK, or write the key to {DEFAULT_PSK_FILE}",
              file=sys.stderr)
        return 4
    try:
        return asyncio.run(run(args.host, args.port, args.password,
                               psk, out_path))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
