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

# Preset modes are mutually exclusive (HA standard). Features that the
# AC can run *in parallel* with these (Clean, Purify) live on the switch
# platform instead.
PRESET_NONE: Final = "none"
PRESET_JET: Final = "jet"     # Powerful — closes side vents, blasts downward
PRESET_SLEEP: Final = "sleep" # 7h sleep timer (default; configurable via service)
PRESET_ECO: Final = "eco"     # Energy-save 60% (only on inverter models)

PRESET_MODES: Final[list[str]] = [
    PRESET_NONE,
    PRESET_JET,
    PRESET_SLEEP,
    PRESET_ECO,
]

# --- Swing modes (HA climate.swing_mode) -------------------------------------

# A12AHD-class remotes (6711A20***) only send the SWING_V_TOGGLE code
# (0x8810001). Position frames (0x8813***) belong to newer DualInverter
# models and are not supported by the A-series. We expose a simple
# on/off swing and let the AC's own state toggle.
SWING_OFF: Final = "off"
SWING_ON: Final = "on"

SWING_MODES: Final[list[str]] = [SWING_OFF, SWING_ON]

# --- Service names -----------------------------------------------------------

SERVICE_SET_SLEEP_TIMER: Final = "set_sleep_timer"
SERVICE_SET_SCHEDULE_TIMER: Final = "set_schedule_timer"
SERVICE_CLEAR_TIMERS: Final = "clear_timers"
