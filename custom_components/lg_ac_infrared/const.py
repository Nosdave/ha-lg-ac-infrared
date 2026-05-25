"""Constants for the LG AC Infrared integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "lg_ac_infrared"

CONF_NAME: Final = "name"
CONF_TRANSMITTER_ENTITY_ID: Final = "transmitter_entity_id"
CONF_RECEIVER_ENTITY_ID: Final = "receiver_entity_id"
CONF_ESPHOME_ENTRY_ID: Final = "esphome_entry_id"
CONF_TEMPERATURE_SENSOR: Final = "temperature_sensor"

DEFAULT_NAME: Final = "LG AC"
DEFAULT_TARGET_TEMP_C: Final = 22
MIN_TEMP_C: Final = 16
MAX_TEMP_C: Final = 30

MANUFACTURER: Final = "LG"
MODEL: Final = "Classic 28-bit IR (A12AHD-class)"
