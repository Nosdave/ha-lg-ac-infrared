"""Button entities for LG AC discrete commands.

These are single-shot actions that don't fit the climate preset model:
- light_toggle: toggle the AC's display LED
- clear_timers: cancel any active sleep/on/off schedule timer
"""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import LgAcConfigEntry
from . import codec
from .const import CONF_NAME, DOMAIN, MANUFACTURER, MODEL

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LgAcConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the button entities."""
    async_add_entities(
        [
            LgAcLightToggleButton(entry),
            LgAcClearTimersButton(entry),
        ]
    )


class _LgAcButtonBase(ButtonEntity):
    """Shared infrastructure for all LG AC buttons."""

    _attr_has_entity_name = True

    _frame: int
    _suffix: str
    _attr_translation_key: str

    def __init__(self, entry: LgAcConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{self._suffix}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.data[CONF_NAME],
            manufacturer=MANUFACTURER,
            model=MODEL,
        )

    async def async_press(self) -> None:
        send = self._entry.runtime_data.send_frame
        if send is None:
            _LOGGER.warning(
                "Climate entity not yet ready — button press ignored"
            )
            return
        await send(self._frame)


class LgAcLightToggleButton(_LgAcButtonBase):
    """Toggle the AC's display LED on/off."""

    _frame = codec.LIGHT_TOGGLE
    _suffix = "light_toggle"
    _attr_translation_key = "light_toggle"
    _attr_icon = "mdi:lightbulb"


class LgAcClearTimersButton(_LgAcButtonBase):
    """Cancel any active sleep / on-schedule / off-schedule timer."""

    _frame = codec.TIMER_CLEAR_ALL
    _suffix = "clear_timers"
    _attr_translation_key = "clear_timers"
    _attr_icon = "mdi:timer-off"
