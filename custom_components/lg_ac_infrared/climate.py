"""Climate entity for an LG split-AC controlled via an ESPHome IR proxy.

Compatible with the **stable** HA `infrared` platform (HA 2026.4–2026.5):
uses `async_send_command(hass, entity_id, command)` for TX, and reaches
into `aioesphomeapi.subscribe_infrared_rf_receive` for RX because the
HA-Core infrared platform does not yet abstract receivers.

When HA upstreams the receiver classes (`InfraredEmitterConsumerEntity`,
`async_get_receivers`, `InfraredReceiverConsumerEntity`) in a future
release, this module can switch over without affecting downstream
configuration.
"""

from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

from homeassistant.components.climate import (
    ATTR_FAN_MODE,
    ATTR_TEMPERATURE,
    FAN_AUTO,
    FAN_HIGH,
    FAN_LOW,
    FAN_MEDIUM,
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.components.infrared import (
    InfraredCommand,
    async_send_command,
)
from homeassistant.const import (
    PRECISION_WHOLE,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfTemperature,
)
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.restore_state import RestoreEntity

from . import LgAcConfigEntry
from .codec import (
    CARRIER_HZ,
    Fan,
    LgAcState,
    Mode,
    decode_frame,
    decode_state,
    encode,
    frame_to_raw_timings,
)
from .const import (
    CONF_NAME,
    CONF_RECEIVER_ENTITY_ID,
    CONF_TEMPERATURE_SENSOR,
    CONF_TRANSMITTER_ENTITY_ID,
    DEFAULT_TARGET_TEMP_C,
    DOMAIN,
    MANUFACTURER,
    MAX_TEMP_C,
    MIN_TEMP_C,
    MODEL,
)

if TYPE_CHECKING:
    from aioesphomeapi import InfraredRFReceiveEvent

_LOGGER = logging.getLogger(__name__)

FAN_POWERFUL = "powerful"

HA_HVAC_TO_LG: dict[HVACMode, Mode] = {
    HVACMode.COOL: Mode.COOL,
    HVACMode.DRY: Mode.DRY,
    HVACMode.FAN_ONLY: Mode.FAN_ONLY,
    HVACMode.AUTO: Mode.AUTO,
    HVACMode.HEAT: Mode.HEAT,
}
LG_TO_HA_HVAC: dict[Mode, HVACMode] = {v: k for k, v in HA_HVAC_TO_LG.items()}

HA_FAN_TO_LG: dict[str, Fan] = {
    FAN_AUTO: Fan.AUTO,
    FAN_LOW: Fan.LOW,
    FAN_MEDIUM: Fan.MEDIUM,
    FAN_HIGH: Fan.HIGH,
    FAN_POWERFUL: Fan.POWERFUL,
}
LG_TO_HA_FAN: dict[Fan, str] = {v: k for k, v in HA_FAN_TO_LG.items()}


class _RawInfraredCommand(InfraredCommand):
    """Adapter that hands pre-computed raw timings to the infrared platform."""

    __slots__ = ("_timings",)

    def __init__(self, timings: list[int]) -> None:
        super().__init__(modulation=CARRIER_HZ, repeat_count=0)
        self._timings = timings

    def get_raw_timings(self) -> list[int]:
        return self._timings


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LgAcConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the LG AC climate entity."""
    async_add_entities([LgAcClimate(entry)])


class LgAcClimate(ClimateEntity, RestoreEntity):
    """A Home Assistant climate entity for an LG split-AC via IR proxy."""

    _attr_has_entity_name = True
    _attr_name = None  # use device name
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_precision = PRECISION_WHOLE
    _attr_target_temperature_step = 1.0
    _attr_min_temp = MIN_TEMP_C
    _attr_max_temp = MAX_TEMP_C
    _attr_hvac_modes = [
        HVACMode.OFF,
        HVACMode.COOL,
        HVACMode.DRY,
        HVACMode.FAN_ONLY,
        HVACMode.AUTO,
        HVACMode.HEAT,
    ]
    _attr_fan_modes = [FAN_AUTO, FAN_LOW, FAN_MEDIUM, FAN_HIGH, FAN_POWERFUL]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.FAN_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )

    def __init__(self, entry: LgAcConfigEntry) -> None:
        """Initialise the climate entity."""
        self._entry = entry
        self._tx_entity_id: str = entry.data[CONF_TRANSMITTER_ENTITY_ID]
        self._temperature_sensor_entity_id: str | None = entry.data.get(
            CONF_TEMPERATURE_SENSOR
        )
        self._unsub_rx: Any | None = None
        self._unsub_temp: Any | None = None

        # IR is one-way: state is only confirmed when a receiver is wired up.
        self._attr_assumed_state = not entry.data.get(CONF_RECEIVER_ENTITY_ID)

        self._attr_unique_id = entry.entry_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.data[CONF_NAME],
            manufacturer=MANUFACTURER,
            model=MODEL,
        )

        # Optimistic defaults — overwritten by RestoreEntity, then by the
        # first received frame from the original remote, or by HA actions.
        self._attr_hvac_mode = HVACMode.OFF
        self._attr_target_temperature = DEFAULT_TARGET_TEMP_C
        self._attr_fan_mode = FAN_AUTO
        self._attr_current_temperature: float | None = None
        self._last_known_hvac: HVACMode = HVACMode.COOL

    # ----------------- lifecycle -----------------

    async def async_added_to_hass(self) -> None:
        """Restore state and subscribe to IR receive events."""
        await super().async_added_to_hass()
        await self._async_restore_state()
        self._subscribe_temperature_sensor()
        self._subscribe_ir_receiver()

    async def async_will_remove_from_hass(self) -> None:
        """Tear down all subscriptions."""
        if self._unsub_rx is not None:
            try:
                self._unsub_rx()
            except (KeyError, RuntimeError):
                _LOGGER.debug("RX unsubscribe failed", exc_info=True)
            self._unsub_rx = None
        if self._unsub_temp is not None:
            self._unsub_temp()
            self._unsub_temp = None
        await super().async_will_remove_from_hass()

    async def _async_restore_state(self) -> None:
        """Restore previous state across HA restarts."""
        last = await self.async_get_last_state()
        if last is None or last.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            return
        try:
            restored_hvac = HVACMode(last.state)
        except ValueError:
            return
        self._attr_hvac_mode = restored_hvac
        if restored_hvac != HVACMode.OFF:
            self._last_known_hvac = restored_hvac
        if (temp := last.attributes.get(ATTR_TEMPERATURE)) is not None:
            try:
                self._attr_target_temperature = int(round(float(temp)))
            except (TypeError, ValueError):
                pass
        if (fan := last.attributes.get(ATTR_FAN_MODE)) in HA_FAN_TO_LG:
            self._attr_fan_mode = fan

    def _subscribe_ir_receiver(self) -> None:
        """Subscribe to IR receive events via aioesphomeapi."""
        runtime = self._entry.runtime_data
        if runtime.rx_key is None:
            return
        self._unsub_rx = runtime.client.subscribe_infrared_rf_receive(
            self._on_ir_event
        )

    def _subscribe_temperature_sensor(self) -> None:
        """Mirror the optional room-temperature sensor."""
        if not self._temperature_sensor_entity_id:
            return
        if (
            state := self.hass.states.get(self._temperature_sensor_entity_id)
        ) is not None:
            self._update_current_temperature_from_state(state.state)
        self._unsub_temp = async_track_state_change_event(
            self.hass,
            [self._temperature_sensor_entity_id],
            self._on_temperature_change,
        )

    @callback
    def _on_temperature_change(
        self, event: Event[EventStateChangedData]
    ) -> None:
        new_state = event.data.get("new_state")
        if new_state is None:
            return
        if self._update_current_temperature_from_state(new_state.state):
            self.async_write_ha_state()

    def _update_current_temperature_from_state(self, raw: str | None) -> bool:
        """Parse a sensor state string into _attr_current_temperature."""
        if raw is None or raw in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            if self._attr_current_temperature is not None:
                self._attr_current_temperature = None
                return True
            return False
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return False
        if value != self._attr_current_temperature:
            self._attr_current_temperature = value
            return True
        return False

    # ----------------- HA-driven commands -----------------

    async def async_turn_on(self) -> None:
        """Turn the AC on, restoring the last known mode (default Cool)."""
        await self.async_set_hvac_mode(self._last_known_hvac)

    async def async_turn_off(self) -> None:
        """Turn the AC off (sends the canonical OFF frame)."""
        await self.async_set_hvac_mode(HVACMode.OFF)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Switch HVAC mode (also handles power on/off)."""
        if hvac_mode == HVACMode.OFF:
            previous = self._attr_hvac_mode
            self._attr_hvac_mode = HVACMode.OFF
            try:
                await self._transmit_state(power_on=False)
            except Exception:
                self._attr_hvac_mode = previous
                raise
            finally:
                self.async_write_ha_state()
            return

        if hvac_mode not in HA_HVAC_TO_LG:
            raise ValueError(f"Unsupported hvac_mode: {hvac_mode}")
        previous = self._attr_hvac_mode
        previous_last = self._last_known_hvac
        self._attr_hvac_mode = hvac_mode
        self._last_known_hvac = hvac_mode
        try:
            await self._transmit_state(power_on=True)
        except Exception:
            self._attr_hvac_mode = previous
            self._last_known_hvac = previous_last
            raise
        finally:
            self.async_write_ha_state()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set a new target temperature."""
        if (temp := kwargs.get(ATTR_TEMPERATURE)) is None:
            return
        new_temp = int(round(float(temp)))
        new_temp = max(self._attr_min_temp, min(self._attr_max_temp, new_temp))
        previous = self._attr_target_temperature
        self._attr_target_temperature = new_temp
        try:
            if self._attr_hvac_mode != HVACMode.OFF:
                await self._transmit_state(power_on=True)
        except Exception:
            self._attr_target_temperature = previous
            raise
        finally:
            self.async_write_ha_state()

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set a new fan mode."""
        if fan_mode not in HA_FAN_TO_LG:
            raise ValueError(f"Unsupported fan_mode: {fan_mode}")
        previous = self._attr_fan_mode
        self._attr_fan_mode = fan_mode
        try:
            if self._attr_hvac_mode != HVACMode.OFF:
                await self._transmit_state(power_on=True)
        except Exception:
            self._attr_fan_mode = previous
            raise
        finally:
            self.async_write_ha_state()

    # ----------------- emit -----------------

    async def _transmit_state(self, *, power_on: bool) -> None:
        """Encode current target state and send it via the IR proxy."""
        if power_on:
            mode = HA_HVAC_TO_LG.get(self._attr_hvac_mode, Mode.COOL)
            temp = int(self._attr_target_temperature or DEFAULT_TARGET_TEMP_C)
            fan = HA_FAN_TO_LG.get(self._attr_fan_mode or FAN_AUTO, Fan.AUTO)
            state = LgAcState(power_on=True, mode=mode, temp=temp, fan=fan)
        else:
            state = LgAcState(power_on=False)

        frame = encode(state)
        timings = frame_to_raw_timings(frame)
        _LOGGER.debug(
            "TX frame 0x%07X (%d entries) for %s", frame, len(timings), state
        )
        await async_send_command(
            self.hass, self._tx_entity_id, _RawInfraredCommand(timings)
        )

    # ----------------- receive (state sync from original remote) -----------------

    @callback
    def _on_ir_event(self, event: InfraredRFReceiveEvent) -> None:
        """Handle a raw IR receive event from the ESPHome proxy.

        Filters on the receiver key and decodes only valid LG-AC state
        frames; ignores swing/timer/function frames.
        """
        runtime = self._entry.runtime_data
        if event.key != runtime.rx_key:
            return

        frame = decode_frame(list(event.timings))
        if frame is None:
            return
        state = decode_state(frame)
        if state is None:
            _LOGGER.debug("RX 0x%07X — not a state-changing frame, ignored", frame)
            return

        _LOGGER.debug("RX 0x%07X decoded as %s", frame, state)

        changed = False
        if not state.power_on:
            if self._attr_hvac_mode != HVACMode.OFF:
                self._attr_hvac_mode = HVACMode.OFF
                changed = True
        else:
            # state.mode is Mode.COOL = 0 → use `is not None`, not truthiness.
            new_hvac = (
                LG_TO_HA_HVAC.get(state.mode) if state.mode is not None else None
            )
            if new_hvac is not None and self._attr_hvac_mode != new_hvac:
                self._attr_hvac_mode = new_hvac
                self._last_known_hvac = new_hvac
                changed = True
            if state.temp is not None and self._attr_target_temperature != state.temp:
                self._attr_target_temperature = state.temp
                changed = True
            if state.fan is not None:
                new_fan = LG_TO_HA_FAN.get(state.fan)
                if new_fan is not None and self._attr_fan_mode != new_fan:
                    self._attr_fan_mode = new_fan
                    changed = True

        if changed:
            self.async_write_ha_state()
