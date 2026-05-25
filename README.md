# LG AC Infrared

A Home Assistant **HACS custom integration** that exposes an LG split-AC as
a native `climate.*` entity, driven entirely through the Home Assistant
**`infrared` platform** (introduced in HA 2026.4) and an ESPHome
**`ir_rf_proxy`** device.

The IR proxy stays **generic** — no LG-specific code on the ESP. All protocol
logic lives in this integration. Adding another brand later means writing
another integration, not reflashing your proxy.

## Status

**Work in progress** — verified end-to-end against:

- LG split-AC **A12AHD (AS-H126PDL1)**
- LG remote **6711A20073Z** (classic 28-bit LG family)
- Seeed Studio **XIAO Smart IR Mate** running the upstream
  `esphome/infrared-proxies/xiao-ir-mate` firmware
- Home Assistant **2026.4.3**

Send + receive paths confirmed with bit-identical capture/diff. See
[scripts/capture_ir.py](scripts/capture_ir.py) and
[scripts/send_test.py](scripts/send_test.py).

## Features (v0.2)

- **Power** on / off
- **HVAC modes**: `off`, `cool`, `dry`, `fan_only`, `auto`, `heat`
- **Target temperature** 16–30 °C (whole-degree steps)
- **Fan modes**: `auto` (variable / "schwankend"), `low`, `medium`, `high` (constant full)
- **Swing modes**: `off`, `on` (auto-swing), plus 6 vertical positions
  (`lowest`, `low`, `middle`, `upper_middle`, `high`, `highest`)
- **Preset modes**:
  - `jet` — Powerful mode (closes side vents, full-blast downward)
  - `sleep` — 7-hour sleep timer (default; configurable via service)
  - `clean` — Auto-clean / self-clean (dries the evaporator)
  - `purify` — Plasma / Ionizer
  - `eco` — Energy-save mode (inverter models only)
- **Service actions**:
  - `lg_ac_infrared.set_sleep_timer` — 0..1439 min (0 cancels)
  - `lg_ac_infrared.set_schedule_timer` — delayed on/off
  - `lg_ac_infrared.clear_timers` — cancel all active timers
- **Button entities**: `button.*_toggle_display_led`, `button.*_clear_all_timers`
- **Bidirectional state sync** — manual remote operation updates HA entity
- **Optional room-temperature sensor** exposed as `current_temperature`
- Pure local control — no cloud, no LG account

## Architecture

```
LG Remote ──IR──▶ ┌────────────────────┐
                  │ ESPHome ir_rf_proxy│ (Xiao IR Mate, generic, no LG logic)
HA Climate ──────▶│  - TX transmitter  │ ──IR──▶ LG Split AC
                  │  - RX receiver     │ ◀──IR──
                  └─────────┬──────────┘
                            │ aioesphomeapi
              ┌─────────────┴─────────────┐
              │ Home Assistant            │
              │  ├─ esphome integration   │ (provides emitter entity)
              │  └─ lg_ac_infrared (this) │
              │      ├─ codec.py (28-bit) │
              │      └─ climate.py        │
              └───────────────────────────┘
```

**Transmit path** uses the HA-supported `InfraredEmitterConsumerEntity` →
`infrared.async_send_command()` → ESPHome `infrared_rf_transmit_raw_timings`.

**Receive path** subscribes directly to `aioesphomeapi.subscribe_infrared_rf_receive`
because HA Core's `esphome` integration does not yet wire `ir_rf_proxy`
receivers as `InfraredReceiverEntity` (verified in HA 2026.4 / `dev`).
The subscription reuses the existing `APIClient` from the ESPHome
integration — no second TCP connection, no extra config.

## Installation

### Prerequisites

1. Home Assistant **2026.4** or later.
2. An ESPHome device flashed with `ir_rf_proxy` firmware (e.g., the
   upstream [Xiao IR Mate YAML](https://github.com/esphome/infrared-proxies/blob/main/xiao-ir-mate/xiao-ir-mate.yaml))
   and adopted into Home Assistant.
3. The device must expose at least one entity in the `infrared.*` domain.

### Install via HACS (manual repo, while not yet in the default list)

1. In HACS → **Integrations** → ⋮ menu → **Custom repositories**.
2. Add `https://github.com/Nosdave/ha-lg-ac-infrared` as category
   **Integration**.
3. Install **LG AC Infrared**.
4. Restart Home Assistant.

### Manual install

Copy `custom_components/lg_ac_infrared/` to `<HA config>/custom_components/`
and restart HA.

### Configure

Settings → **Devices & services** → **+ Add integration** → search for
**LG AC Infrared**. Pick your IR proxy transmitter, optionally the receiver
and a room-temperature sensor.

## Supported devices

This integration speaks the **classic 28-bit LG AC IR protocol**. The
following remote families are known to use it:

- `6711A20***`
- `6711AR2***`
- `AKB73757604`, `AKB73315611` (LG2-adjacent, may need tuning)

It does **not** support:

- LG2 protocol (newer 32-bit header timing, `AKB749***` multi-splits)
- Tower-mount fan layouts (`AC_FAN_TOWER` = `{0, 4, 6, 6}`)
- Wired LG controllers (use [JanM321/esphome-lg-controller](https://github.com/JanM321/esphome-lg-controller)
  for those)

If your remote model isn't on the verified list, run
`scripts/capture_ir.py` against your remote — if the decoder prints
`sign=0x88` and `csum=OK` for state-changing frames, you're in.

## Known limitations

> **Two ACs sharing one IR proxy + receiver:** the classic 28-bit LG
> protocol carries no per-remote address, so the proxy cannot tell two
> simultaneous LG remotes apart at the IR layer. If you have two LG
> splits in the same room, use **two separate IR proxies** (one per
> AC) — each gets its own config entry and clean state sync.

- **No swing / vane control yet** — the protocol's swing frames are
  understood (`0x8810001`, `0x8813xxx` family) but not exposed; planned for
  v0.2.
- **No timer support** — LG-AC `P2` timer frames are detected but ignored
  (decoder returns `None`). Planned for v0.2.
- **No display-light / sleep / clean-mode toggles** — the `0x88C00xx`
  sub-code family isn't fully reverse-engineered upstream. A capture-and-
  learn wizard is planned for v0.3.
- **Optimistic state** — IR is one-way at the radio level. The integration
  marks itself as `assumed_state=True`. Use the receiver entity (and the
  optional room-temperature sensor) to keep things honest.
- **HA Core esphome integration does not wire ir_rf_proxy receivers**
  (as of 2026.4). This integration works around it by subscribing
  directly to `aioesphomeapi`. If a future HA release adds the wiring
  natively, we'll switch over transparently.

## Development

```bash
git clone https://github.com/Nosdave/ha-lg-ac-infrared
cd ha-lg-ac-infrared
python -m venv .venv
.venv/Scripts/activate   # on Windows; use bin/activate elsewhere
pip install -r scripts/requirements.txt

# Verify the codec offline:
python custom_components/lg_ac_infrared/codec.py

# Capture frames from your remote:
python scripts/capture_ir.py --host xiao-ir-mate-XXXX.local

# Send a test frame (will piep your AC):
python scripts/send_test.py --frame 0x8800707
```

## Credits

- Protocol reference: [crankyoldgit/IRremoteESP8266](https://github.com/crankyoldgit/IRremoteESP8266)
  (`ir_LG.h`, `ir_LG.cpp`)
- Fan-mapping cross-check: [Arduino-IRremote](https://github.com/Arduino-IRremote/Arduino-IRremote)
  (`ac_LG.h`)
- Capture verification: [nokru/lg-ac-lirc](https://github.com/nokru/lg-ac-lirc)
- HA `infrared` platform: [home-assistant/core PR + dev blog](https://developers.home-assistant.io/blog/2026/03/30/infrared-entity-platform/)

## License

MIT.
