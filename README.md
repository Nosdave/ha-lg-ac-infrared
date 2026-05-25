# LG AC Infrared

A **HACS custom integration for Home Assistant** that exposes an LG split-AC
as a native `climate.*` entity (plus switches, buttons and service actions),
driven through the Home Assistant **`infrared` platform** (HA 2026.4+) and
any ESPHome **`ir_rf_proxy`** device.

In plain terms:

- It controls your LG split-air-conditioner over IR — power, mode,
  temperature, fan, swing, jet, sleep, clean, plasma, timers — through
  any generic IR proxy that ESPHome speaks. No LG-specific firmware on
  the proxy, no LG account, no cloud.
- It **listens** as well: when somebody grabs the original remote and
  changes anything, Home Assistant mirrors it.
- Example proxy hardware used during development: a **Seeed Studio XIAO
  Smart IR Mate** flashed with the upstream
  [`esphome/infrared-proxies/xiao-ir-mate`](https://github.com/esphome/infrared-proxies/blob/main/xiao-ir-mate/xiao-ir-mate.yaml)
  firmware — a ~€10 USB-C-powered ESP32-C3 board with three IR LEDs
  (360° transmit) and a TSOP receiver.
- Example AC verified end-to-end: **LG A12AHD (AS-H126PDL1)** with
  remote **6711A20073Z** (classic 28-bit LG IR family).
- The IR proxy stays **generic**: all LG protocol logic lives in this
  integration. Add another brand later by writing another integration,
  not reflashing your proxy.

## Verified

- Home Assistant **2026.4 / 2026.5**
- ESPHome **2026.2.x** with `ir_rf_proxy` component
- LG A12AHD (AS-H126PDL1) split, remote 6711A20073Z
- Seeed XIAO Smart IR Mate (Seeed-Studio P/N 102991829)

Send + receive paths verified with on-air bit-identical capture/diff.

## Features

- **Power** on / off
- **HVAC modes**: `off`, `cool`, `dry`, `fan_only`, `auto`, `heat`
- **Target temperature** 16–30 °C (whole-degree steps)
- **Fan modes**: `auto` (variable), `low` (lowest), `medium`, `high` (constant full)
- **Preset modes** (mutually exclusive):
  - `jet` — Powerful (closes side vents, full-blast downward)
  - `sleep` — 7-hour sleep timer (default; configurable via service)
  - `eco` — Energy-save (inverter models only — exposed on every model,
    may be a no-op on non-inverter units like the A-series)
- **Switch entities** (independent of the climate preset — can run in parallel):
  - `switch.*_auto_clean` — self-cleaning / dry-evaporator
  - `switch.*_plasma_purify` — Plasma / Ionizer
  - `switch.*_vertical_swing` — vane oscillation (toggle protocol with
    self-echo suppression)
- **Button entities**:
  - `button.*_toggle_display_led`
  - `button.*_clear_all_timers`
- **Service actions**:
  - `lg_ac_infrared.set_sleep_timer` (0..1439 min, 0 cancels)
  - `lg_ac_infrared.set_schedule_timer` (action, minutes)
  - `lg_ac_infrared.clear_timers`
- **Bidirectional state sync** — manual remote operation updates the HA
  entity within milliseconds
- **Optional room-temperature sensor** exposed as `current_temperature`
- **Restores state across HA restarts** (RestoreEntity)
- **Reloads transparently** when ESPHome reconnects (proxy reboot, OTA)
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
              │      └─ climate / switch  │
              │         + button + svc    │
              └───────────────────────────┘
```

**Transmit path** uses HA's `infrared.async_send_command()` helper. **Receive
path** subscribes directly to `aioesphomeapi.subscribe_infrared_rf_receive`
because HA Core's `esphome` integration does not yet wire `ir_rf_proxy`
receivers as `InfraredReceiverEntity` (verified in HA 2026.5). The
subscription reuses the existing `APIClient` from the ESPHome integration —
no second TCP connection.

When HA upstreams the receiver wiring in a later release, this integration
will transparently switch over.

## Installation

### Prerequisites

1. Home Assistant **2026.4** or later.
2. An ESPHome device flashed with `ir_rf_proxy` firmware (e.g. the upstream
   [Xiao IR Mate YAML](https://github.com/esphome/infrared-proxies/blob/main/xiao-ir-mate/xiao-ir-mate.yaml))
   and adopted into Home Assistant.
3. The device must expose at least one entity in the `infrared.*` domain.

### Install via HACS

1. HACS → **Integrations** → ⋮ menu → **Custom repositories**.
2. Add `https://github.com/Nosdave/ha-lg-ac-infrared` as category **Integration**.
3. Install **LG AC Infrared**.
4. Restart Home Assistant.

### Manual install

Copy `custom_components/lg_ac_infrared/` to `<HA config>/custom_components/`
and restart HA.

### Configure

Settings → **Devices & services** → **+ Add integration** → search for
**LG AC Infrared**. Pick your IR proxy transmitter, optionally a
room-temperature sensor.

## Supported devices

This integration speaks the **classic 28-bit LG AC IR protocol**. The
following remote families are known to use it:

- `6711A20***` (verified live: `6711A20073Z` on A12AHD)
- `6711AR2***`
- Some `AKB***` variants (e.g. `AKB73315611`, `AKB73456113`)

It does **not** currently support:

- LG2 protocol (newer 32-bit header timing — some newer multi-splits)
- Tower-mount fan layouts (`AC_FAN_TOWER = {0, 4, 6, 6}`)
- Wired LG controllers — for those, see
  [JanM321/esphome-lg-controller](https://github.com/JanM321/esphome-lg-controller)

> **Two ACs sharing one IR proxy receiver:** the classic 28-bit LG
> protocol carries no per-remote address, so the proxy cannot tell two
> simultaneous LG remotes apart at the IR layer. If you have two LG
> splits in the same room, use **two separate IR proxies** (one per AC).

If your remote model isn't on the verified list, run
`scripts/capture_ir.py` against your remote — if the decoder prints
`sign=0x88` and `csum=OK` for state-changing frames, you're in.

## Known limitations

- **No swing positions** (only on/off toggle) — the `0x8813xxx` positions
  family is DualInverter-only and ignored by A-series hardware (see
  IRremoteESP8266 Issue #1770).
- **No timer-readback** — the `0x88Axxxx` sleep frames are decoded
  correctly, but only when transmitted; the AC doesn't periodically
  broadcast its remaining time.
- **Optimistic state for some controls** — IR is one-way at the radio
  level. Where a receiver is present, the integration syncs real frames;
  for toggle-only controls (swing), state is tracked optimistically with
  self-echo suppression.

## Development

```bash
git clone https://github.com/Nosdave/ha-lg-ac-infrared
cd ha-lg-ac-infrared
python -m venv .venv
.venv/Scripts/activate   # on Windows; use bin/activate elsewhere
pip install -r scripts/requirements.txt

# Verify the codec offline (no hardware needed):
python custom_components/lg_ac_infrared/codec.py

# Capture frames from your remote (point Mate at remote, press buttons):
python scripts/capture_ir.py --host xiao-ir-mate-XXXX.local

# Send a test frame (will piep your AC):
python scripts/send_test.py --frame 0x8800707
```

`scripts/fetch_psk.py` is a personal-use helper that copies the ESPHome
noise PSK from a Home Assistant instance via SSH into a local secrets
file; it expects an `ssh ha-green` alias and writes to
`c:/Users/david/dev/.secrets/ha-green/xiao-ir-mate-7cb510.psk`. Adapt
the constants for your environment.

## Credits

- Protocol reference: [crankyoldgit/IRremoteESP8266](https://github.com/crankyoldgit/IRremoteESP8266)
  (`ir_LG.h`, `ir_LG.cpp`)
- Sub-OFF / function frames: IRremoteESP8266 [Issue #1772](https://github.com/crankyoldgit/IRremoteESP8266/issues/1772)
- Fan / mode cross-check: [Arduino-IRremote](https://github.com/Arduino-IRremote/Arduino-IRremote)
  (`ac_LG.h`)
- Sleep / schedule / clear frame structure: [frawau/pyhvac](https://github.com/frawau/pyhvac)
- Capture verification: [nokru/lg-ac-lirc](https://github.com/nokru/lg-ac-lirc)
- HA `infrared` platform: [HA dev blog 2026-03-30](https://developers.home-assistant.io/blog/2026/03/30/infrared-entity-platform/)

## License

MIT.
