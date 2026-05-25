"""Constants for the LG AC Infrared integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "lg_ac_infrared"

# --- Config entry keys -------------------------------------------------------

CONF_NAME: Final = "name"
CONF_TRANSMITTER_ENTITY_ID: Final = "transmitter_entity_id"
CONF_RECEIVER_ENTITY_ID: Final = "receiver_entity_id"
CONF_ESPHOME_ENTRY_ID: Final = "esphome_entry_id"
CONF_TEMPERATURE_SENSOR: Final = "temperature_sensor"

# --- Defaults ----------------------------------------------------------------

DEFAULT_NAME: Final = "LG AC"
DEFAULT_TARGET_TEMP_C: Final = 22
MIN_TEMP_C: Final = 16
MAX_TEMP_C: Final = 30

# --- Device info -------------------------------------------------------------

MANUFACTURER: Final = "LG"
MODEL: Final = "Classic 28-bit IR (A12AHD-class)"

# --- Preset modes (HA climate.preset_mode) -----------------------------------

PRESET_NONE: Final = "none"
PRESET_JET: Final = "jet"        # Powerful — closes side vents, blasts downward
PRESET_SLEEP: Final = "sleep"    # 7h sleep timer (default; configurable via service)
PRESET_CLEAN: Final = "clean"    # Auto-clean / self-cleaning (dry evaporator)
PRESET_PURIFY: Final = "purify"  # Plasma / Ionizer
PRESET_ECO: Final = "eco"        # Energy-save 60% (only on inverter models)

PRESET_MODES: Final[list[str]] = [
    PRESET_NONE,
    PRESET_JET,
    PRESET_SLEEP,
    PRESET_CLEAN,
    PRESET_PURIFY,
    PRESET_ECO,
]

# --- Swing modes (HA climate.swing_mode) -------------------------------------

SWING_OFF: Final = "off"
SWING_ON: Final = "on"               # auto-swing across all positions
SWING_LOWEST: Final = "lowest"
SWING_LOW: Final = "low"
SWING_MIDDLE: Final = "middle"
SWING_UPPER_MIDDLE: Final = "upper_middle"
SWING_HIGH: Final = "high"
SWING_HIGHEST: Final = "highest"

SWING_MODES: Final[list[str]] = [
    SWING_OFF,
    SWING_ON,
    SWING_LOWEST,
    SWING_LOW,
    SWING_MIDDLE,
    SWING_UPPER_MIDDLE,
    SWING_HIGH,
    SWING_HIGHEST,
]

# --- Service names -----------------------------------------------------------

SERVICE_SET_SLEEP_TIMER: Final = "set_sleep_timer"
SERVICE_SET_SCHEDULE_TIMER: Final = "set_schedule_timer"
SERVICE_CLEAR_TIMERS: Final = "clear_timers"
