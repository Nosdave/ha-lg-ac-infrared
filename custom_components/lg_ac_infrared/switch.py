"""Switch entities for LG AC features that run in parallel to the main state.

Clean (self-cleaning / dry-evaporator) and Purify (Plasma / Ionizer) can be
active independently from Jet, Sleep, etc. — exposing them as climate
preset_mode (which is mutually exclusive) doesn't fit. Switches let HA
mirror the AC's true ability to have multiple features on at once.
"""

from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import (
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    STATE_ON,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import LgAcConfigEntry
from . import codec
from .const import (
    CONF_NAME,
    CONF_RECEIVER_ENTITY_ID,
    DOMAIN,
    MANUFACTURER,
    MODEL,
)

if TYPE_CHECKING:
    from aioesphomeapi import InfraredRFReceiveEvent

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LgAcConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the switch entities."""
    async_add_entities(
        [
            LgAcCleanSwitch(entry),
            LgAcPurifySwitch(entry),
            LgAcSwingVSwitch(entry),
        ]
    )


class _LgAcToggleSwitch(SwitchEntity, RestoreEntity):
    """Shared infrastructure for LG AC on/off toggle switches.

    Each subclass declares two raw frames (on / off) and a unique suffix.
    State sync from the original remote is wired via the receive callback
    on the shared aioesphomeapi subscription.
    """

    _attr_has_entity_name = True

    _on_frame: int
    _off_frame: int
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
        self._attr_assumed_state = not entry.data.get(CONF_RECEIVER_ENTITY_ID)
        self._attr_is_on = False
        self._unsub_rx: Any | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            if last.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE):
                self._attr_is_on = last.state == STATE_ON
        self._subscribe_rx()

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub_rx is not None:
            try:
                self._unsub_rx()
            except (KeyError, RuntimeError):
                _LOGGER.debug("RX unsubscribe failed", exc_info=True)
            self._unsub_rx = None
        await super().async_will_remove_from_hass()

    def _subscribe_rx(self) -> None:
        runtime = self._entry.runtime_data
        if runtime.rx_key is None:
            return
        self._unsub_rx = runtime.client.subscribe_infrared_rf_receive(
            self._on_ir_event
        )

    async def _send(self, frame: int) -> None:
        send = self._entry.runtime_data.send_frame
        if send is None:
            _LOGGER.warning("Climate entity not yet ready — switch ignored")
            return
        await send(frame)

    async def async_turn_on(self, **kwargs: Any) -> None:
        previous = self._attr_is_on
        self._attr_is_on = True
        try:
            await self._send(self._on_frame)
        except Exception:
            self._attr_is_on = previous
            raise
        finally:
            self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        previous = self._attr_is_on
        self._attr_is_on = False
        try:
            await self._send(self._off_frame)
        except Exception:
            self._attr_is_on = previous
            raise
        finally:
            self.async_write_ha_state()

    @callback
    def _on_ir_event(self, event: InfraredRFReceiveEvent) -> None:
        runtime = self._entry.runtime_data
        if event.key != runtime.rx_key:
            return
        frame = codec.decode_frame(list(event.timings))
        if frame is None:
            return
        if frame == self._on_frame and not self._attr_is_on:
            self._attr_is_on = True
            self.async_write_ha_state()
        elif frame == self._off_frame and self._attr_is_on:
            self._attr_is_on = False
            self.async_write_ha_state()


class LgAcCleanSwitch(_LgAcToggleSwitch):
    """Auto-clean / self-cleaning (dries the evaporator after cool mode)."""

    _on_frame = codec.CLEAN_ON
    _off_frame = codec.CLEAN_OFF
    _suffix = "clean"
    _attr_translation_key = "clean"
    _attr_icon = "mdi:emoticon-cool-outline"


class LgAcPurifySwitch(_LgAcToggleSwitch):
    """Air purify / Plasma / Ionizer."""

    _on_frame = codec.PURIFY_ON
    _off_frame = codec.PURIFY_OFF
    _suffix = "purify"
    _attr_translation_key = "purify"
    _attr_icon = "mdi:air-purifier"


class LgAcSwingVSwitch(SwitchEntity, RestoreEntity):
    """Vertical swing — toggle protocol with optimistic on/off semantics.

    A12AHD-class remotes only send the single 0x8810001 toggle code; the
    AC flips its own swing state on every press. We track an assumed
    boolean state and emit the toggle whenever the user requests a flip.
    RX of the toggle frame (e.g. user pressed the original remote) flips
    the assumed state back into sync.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "swing_vertical"
    _attr_icon = "mdi:air-conditioner"

    def __init__(self, entry: LgAcConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_swing_vertical"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.data[CONF_NAME],
            manufacturer=MANUFACTURER,
            model=MODEL,
        )
        # Always assumed — we can never *confirm* what the AC's actual
        # vane state is; we only see toggle events.
        self._attr_assumed_state = True
        self._attr_is_on = False
        self._unsub_rx: Any | None = None
        # Suppress the self-echo: when we send a toggle, the Mate's own
        # receiver picks it up within milliseconds and would otherwise
        # flip the assumed state right back. Block RX of the toggle
        # frame for a short window after every TX.
        self._suppress_rx_until: float = 0.0

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            if last.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE):
                self._attr_is_on = last.state == STATE_ON
        runtime = self._entry.runtime_data
        if runtime.rx_key is not None:
            self._unsub_rx = runtime.client.subscribe_infrared_rf_receive(
                self._on_ir_event
            )

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub_rx is not None:
            try:
                self._unsub_rx()
            except (KeyError, RuntimeError):
                _LOGGER.debug("RX unsubscribe failed", exc_info=True)
            self._unsub_rx = None
        await super().async_will_remove_from_hass()

    async def _toggle(self) -> None:
        send = self._entry.runtime_data.send_frame
        if send is None:
            _LOGGER.warning("Climate entity not yet ready — swing ignored")
            return
        # Open the suppression window BEFORE awaiting the send so the
        # self-echo (which may arrive before send() returns) is caught.
        self._suppress_rx_until = self.hass.loop.time() + 1.5
        await send(codec.SWING_V_TOGGLE)

    async def async_turn_on(self, **kwargs: Any) -> None:
        if self._attr_is_on:
            return
        previous = self._attr_is_on
        self._attr_is_on = True
        try:
            await self._toggle()
        except Exception:
            self._attr_is_on = previous
            raise
        finally:
            self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        if not self._attr_is_on:
            return
        previous = self._attr_is_on
        self._attr_is_on = False
        try:
            await self._toggle()
        except Exception:
            self._attr_is_on = previous
            raise
        finally:
            self.async_write_ha_state()

    @callback
    def _on_ir_event(self, event: InfraredRFReceiveEvent) -> None:
        runtime = self._entry.runtime_data
        if event.key != runtime.rx_key:
            return
        frame = codec.decode_frame(list(event.timings))
        if frame != codec.SWING_V_TOGGLE:
            return
        # Ignore the self-echo of our own TX. Genuine remote presses
        # arrive outside this window and still flip the state.
        if self.hass.loop.time() < self._suppress_rx_until:
            return
        self._attr_is_on = not self._attr_is_on
        self.async_write_ha_state()
