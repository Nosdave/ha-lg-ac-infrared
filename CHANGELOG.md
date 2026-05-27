# Changelog

All notable changes to this project will be documented here.

## 0.2.5 — 2026-05-27

### Fixed
- **HACS-Default-Submission**: the `v0.2.4` release tag was created
  *before* the brand icon was moved from `branding/` into
  `custom_components/lg_ac_infrared/brand/`, so the HACS validation
  action — which checks the tagged release, not `main` — couldn't
  find the icon. Re-tag on the current HEAD that has the icon in
  place. No code change in the integration itself.

## 0.2.4 — 2026-05-25

### Fixed
- **ESPHome auth race**: catch `APIConnectionError` on the first
  `list_entities_services()` call and raise `ConfigEntryNotReady` so HA
  retries instead of failing setup. Reproduced reliably after a HA
  core restart when the ESPHome `runtime_data.client` is LOADED but
  still in noise-handshake.

## 0.2.3 — 2026-05-25

### Fixed
- **Vertical swing switch stuck at off**: the Mate's own receiver picks
  up every TX self-echo within milliseconds. Since the swing protocol
  is a single toggle code (not separate on/off), the echo was flipping
  the assumed switch state right back. Added a 1.5 s suppression
  window after every send.

## 0.2.2 — 2026-05-25

### Changed
- **Vertical swing moved off `climate.swing_mode`** onto its own
  `switch.*_vertical_swing` entity. A two-state pulldown on a toggle
  backend was misleading; a Switch represents toggle semantics better.

## 0.2.1 — 2026-05-25

### Fixed
- **Clean ON/OFF were inverted**: pyhvac claims `0xC8`=on / `0xB7`=off,
  but live-testing on A12AHD plus IRremoteESP8266 Issue #1772 (real
  capture of the sibling `LG6711A20083V`) prove the opposite. Swapped
  `CLEAN_ON` and `CLEAN_OFF`.
- **Stale `SWING_MODE_TO_FRAME` reference** in `_async_restore_state`
  caused `NameError` and prevented climate entity setup.

### Changed
- **Swing simplified to on/off Toggle** — A-series remotes only send
  `0x8810001`; the `0x8813xxx` position family is DualInverter-only
  (IRremoteESP8266 Issue #1770) and ignored by A12AHD.
- **Clean & Purify moved out of `preset_mode`** onto their own switch
  platform so they can run in parallel with Jet / Sleep.

## 0.2.0 — 2026-05-25

### Added
- Full LG-AC remote command set: jet, sleep, clean, purify, eco,
  light-toggle, swing, sleep / schedule timers, clear-all.
- Climate `preset_modes`: `none / jet / sleep / clean / purify / eco`.
- Climate `swing_modes` with 6 vertical positions (later reverted in 0.2.1).
- `button.*_toggle_display_led`, `button.*_clear_all_timers`.
- Service actions `set_sleep_timer`, `set_schedule_timer`, `clear_timers`.
- DE/EN translations for all new entities and services.

### Fixed
- **Major fan-mapping correction** (live-verified on A12AHD):
  - `fan=0` was "AUTO" → now `LOWEST`
  - `fan=2` was "LOW" → now `MEDIUM`
  - `fan=4` was "HIGH" → now `MAX` (constant full)
  - `fan=5` was "POWERFUL" → now `AUTO` (variable / "schwankend")
- The real Powerful is a Function frame `0x8810089 = JET_ON`, not a
  fan-value mutation.
- Truthiness bug in `state.mode if state.mode` (Mode.COOL = 0 is falsy
  → silent failure of Cool-mode RX sync).

## 0.1.0 — 2026-05-25

Initial release.

- Climate entity with power, HVAC mode, target temperature, fan mode.
- Sends through HA `infrared.async_send_command`.
- Receives via `aioesphomeapi.subscribe_infrared_rf_receive` (workaround
  for missing HA-Core wiring of `ir_rf_proxy` receivers).
- DE/EN translations, HACS-installable.
- Tested end-to-end against LG A12AHD + Seeed XIAO IR Mate.
