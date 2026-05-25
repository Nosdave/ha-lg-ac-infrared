"""Send-Test: encode an LG-AC frame, transmit via Mate, verify self-echo
on the local receiver. Goal: prove the encoder + transmit path before we
build the HACS integration.

DEFAULT FRAME: 0x880070F = Power ON, Cool, 22 C, Fan Auto.
The AC will turn on / re-issue this state. Use --dry-run to inspect
timings without transmitting. Use --frame 0x88C0051 to send OFF instead.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

# Allow `from lg_ac_infrared import codec` without installing the package.
_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "custom_components"))
from lg_ac_infrared import codec  # noqa: E402

from aioesphomeapi import APIClient  # noqa: E402
from aioesphomeapi.model import InfraredInfo  # noqa: E402

DEFAULT_PSK_FILE = Path(
    r"c:/Users/david/dev/.secrets/ha-green/xiao-ir-mate-7cb510.psk"
)


def resolve_psk(cli_value: str | None) -> str | None:
    if cli_value:
        return cli_value.strip()
    env = os.environ.get("ESPHOME_NOISE_PSK")
    if env:
        return env.strip()
    if DEFAULT_PSK_FILE.is_file():
        return DEFAULT_PSK_FILE.read_text(encoding="utf-8").strip()
    return None


async def run(host: str, port: int, psk: str, frame: int) -> int:
    timings = codec.frame_to_raw_timings(frame)
    total_us = sum(abs(t) for t in timings)
    print(f"[+] Frame to send: 0x{frame:07X}")
    state = codec.decode_state(frame)
    print(f"    interpreted as: {state}")
    print(f"    {len(timings)} raw entries, "
          f"total {total_us / 1000:.1f} ms on-air")

    print(f"\n[+] Connecting to {host}:{port} ...")
    client = APIClient(address=host, port=port, password=None, noise_psk=psk)
    try:
        await client.connect(login=True)
    except Exception as e:
        print(f"[!] Connect failed: {e}", file=sys.stderr)
        return 2
    print("[+] Connected.")

    entities, _ = await client.list_entities_services()
    ir = [e for e in entities if isinstance(e, InfraredInfo)]
    tx_key = next(
        (e.key for e in ir if getattr(e, "capabilities", 0) & 0x01), None
    )
    if tx_key is None:
        print("[!] No transmitter entity found.")
        await client.disconnect()
        return 3
    print(f"[+] TX key=0x{tx_key:08x}")

    received: list[dict] = []

    def on_event(ev) -> None:
        tlist = list(ev.timings)
        decoded = codec.decode_frame(tlist)
        ts = time.strftime("%H:%M:%S")
        received.append({"frame": decoded, "len": len(tlist)})
        if decoded is not None:
            print(f"[{ts}] RX 0x{decoded:07X}  (len={len(tlist)})")
        else:
            print(f"[{ts}] RX undecoded  (len={len(tlist)})  head={tlist[:4]}")

    unsub = client.subscribe_infrared_rf_receive(on_event)
    await asyncio.sleep(0.4)  # let subscription settle

    print(f"\n[+] TRANSMITTING 0x{frame:07X} ...")
    client.infrared_rf_transmit_raw_timings(
        tx_key,
        carrier_frequency=codec.CARRIER_HZ,
        timings=timings,
        repeat_count=1,
        device_id=0,
    )
    print("[+] Sent. Waiting 2 s for self-echo and AC reaction ...")
    await asyncio.sleep(2.0)

    try:
        unsub()
    except Exception:
        pass
    await client.disconnect()

    print()
    matches = [r for r in received if r["frame"] == frame]
    if matches:
        print(f"[+] SELF-ECHO MATCH (n={len(matches)}). Encoder verified "
              f"bit-identical via on-air capture.")
        return 0
    decoded_seen = [f"0x{r['frame']:07X}" for r in received
                    if r["frame"] is not None]
    if decoded_seen:
        print(f"[!] Received frames, but none matched 0x{frame:07X}.")
        print(f"    Got: {decoded_seen}")
        return 4
    if received:
        print(f"[!] {len(received)} events received but none decoded as LG. "
              f"Header timing may be off, or another remote interfered.")
        return 5
    print("[!] No events received within 2 s of transmit.")
    print("    Possible causes:")
    print("    - IR LED not actually transmitting "
          "(check carrier_frequency and timings)")
    print("    - Receiver not pointed at LED (the Mate's own LED + receiver "
          "are on the same PCB, so this should always echo unless RX is "
          "saturated). Try moving the Mate away from strong sunlight.")
    return 6


def main() -> int:
    ap = argparse.ArgumentParser(description="Send-Test for the LG-AC encoder")
    ap.add_argument("--host", default="xiao-ir-mate-7cb510.local")
    ap.add_argument("--port", type=int, default=6053)
    ap.add_argument("--noise-psk", default=None,
                    help=f"Defaults to {DEFAULT_PSK_FILE} or $ESPHOME_NOISE_PSK")
    ap.add_argument("--frame", type=lambda s: int(s, 0), default=0x8800707,
                    help="28-bit frame in hex (default: 0x8800707 = ON/Cool/22/Auto, bit15=0)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Just print timings, don't transmit")
    args = ap.parse_args()

    if args.dry_run:
        timings = codec.frame_to_raw_timings(args.frame)
        state = codec.decode_state(args.frame)
        print(f"Frame 0x{args.frame:07X} -> {state}")
        print(f"{len(timings)} raw entries:")
        print(timings)
        return 0

    psk = resolve_psk(args.noise_psk)
    if not psk:
        print(f"[!] No noise PSK found.", file=sys.stderr)
        return 1

    try:
        return asyncio.run(run(args.host, args.port, psk, args.frame))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
