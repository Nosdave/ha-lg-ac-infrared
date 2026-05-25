"""Climate entity for an LG split-AC controlled via an ESPHome IR proxy."""

from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

from homeassistant.components.climate import (
    ATTR_FAN_MODE,
    ATTR_PRESET_MODE,
    ATTR_SWING_MODE,
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
from . import codec
from .codec import CARRIER_HZ, Fan, LgAcState, Mode
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
    PRESET_CLEAN,
    PRESET_ECO,
    PRESET_JET,
    PRESET_MODES,
    PRESET_NONE,
    PRESET_PURIFY,
    PRESET_SLEEP,
    SWING_HIGH,
    SWING_HIGHEST,
    SWING_LOW,
    SWING_LOWEST,
    SWING_MIDDLE,
    SWING_MODES,
    SWING_OFF,
    SWING_ON,
    SWING_UPPER_MIDDLE,
)

if TYPE_CHECKING:
    from aioesphomeapi import InfraredRFReceiveEvent

_LOGGER = logging.getLogger(__name__)

# Default minutes when the user picks the Sleep preset (mirrors remote's 7h max).
DEFAULT_SLEEP_MINUTES = 420

HA_HVAC_TO_LG: dict[HVACMode, Mode] = {
    HVACMode.COOL: Mode.COOL,
    HVACMode.DRY: Mode.DRY,
    HVACMode.FAN_ONLY: Mode.FAN_ONLY,
    HVACMode.AUTO: Mode.AUTO,
    HVACMode.HEAT: Mode.HEAT,
}
LG_TO_HA_HVAC: dict[Mode, HVACMode] = {v: k for k, v in HA_HVAC_TO_LG.items()}

# HA fan_mode → LG fan value (live-verified mapping for A12AHD)
HA_FAN_TO_LG: dict[str, Fan] = {
    FAN_AUTO: Fan.AUTO,        # 5, variable
    FAN_LOW: Fan.LOWEST,       # 0, slowest
    FAN_MEDIUM: Fan.MEDIUM,    # 2
    FAN_HIGH: Fan.MAX,         # 4, constant full
}
LG_TO_HA_FAN: dict[Fan, str] = {v: k for k, v in HA_FAN_TO_LG.items()}
# Tolerate `Fan.LOW` and `Fan.HIGH` (alt encodings) on receive only.
LG_TO_HA_FAN[Fan.LOW] = FAN_LOW
LG_TO_HA_FAN[Fan.HIGH] = FAN_HIGH

SWING_MODE_TO_FRAME: dict[str, int] = {
    SWING_OFF: codec.SWING_V_OFF,
    SWING_ON: codec.SWING_V_SWING,
    SWING_LOWEST: codec.SWING_V_LOWEST,
    SWING_LOW: codec.SWING_V_LOW,
    SWING_MIDDLE: codec.SWING_V_MIDDLE,
    SWING_UPPER_MIDDLE: codec.SWING_V_UPPER_MIDDLE,
    SWING_HIGH: codec.SWING_V_HIGH,
    SWING_HIGHEST: codec.SWING_V_HIGHEST,
}
FRAME_TO_SWING_MODE: dict[int, str] = {v: k for k, v in SWING_MODE_TO_FRAME.items()}


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
    entity = LgAcClimate(entry)
    # Expose the frame-send helper so switch / button entities can share it.
    entry.runtime_data.send_frame = entity.async_send_frame
    async_add_entities([entity])


class LgAcClimate(ClimateEntity, RestoreEntity):
    """A Home Assistant climate entity for an LG split-AC via IR proxy."""

    _attr_has_entity_name = True
    _attr_name = None
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
    _attr_fan_modes = [FAN_AUTO, FAN_LOW, FAN_MEDIUM, FAN_HIGH]
    _attr_swing_modes = SWING_MODES
    _attr_preset_modes = PRESET_MODES
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.FAN_MODE
        | ClimateEntityFeature.SWING_MODE
        | ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )

    def __init__(self, entry: LgAcConfigEntry) -> None:
        self._entry = entry
        self._tx_entity_id: str = entry.data[CONF_TRANSMITTER_ENTITY_ID]
        self._temperature_sensor_entity_id: str | None = entry.data.get(
            CONF_TEMPERATURE_SENSOR
        )
        self._unsub_rx: Any | None = None
        self._unsub_temp: Any | None = None

        self._attr_assumed_state = not entry.data.get(CONF_RECEIVER_ENTITY_ID)

        self._attr_unique_id = entry.entry_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.data[CONF_NAME],
            manufacturer=MANUFACTURER,
            model=MODEL,
        )

        # Optimistic defaults
        self._attr_hvac_mode = HVACMode.OFF
        self._attr_target_temperature = DEFAULT_TARGET_TEMP_C
        self._attr_fan_mode = FAN_AUTO
        self._attr_swing_mode = SWING_OFF
        self._attr_preset_mode = PRESET_NONE
        self._attr_current_temperature: float | None = None
        self._last_known_hvac: HVACMode = HVACMode.COOL
        # Track active sleep-timer minutes for state attributes.
        self._sleep_minutes: int = 0

    # ----------------- lifecycle -----------------

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        await self._async_restore_state()
        self._subscribe_temperature_sensor()
        self._subscribe_ir_receiver()

    async def async_will_remove_from_hass(self) -> None:
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
        if (swing := last.attributes.get(ATTR_SWING_MODE)) in SWING_MODE_TO_FRAME:
            self._attr_swing_mode = swing
        if (preset := last.attributes.get(ATTR_PRESET_MODE)) in PRESET_MODES:
            self._attr_preset_mode = preset

    def _subscribe_ir_receiver(self) -> None:
        runtime = self._entry.runtime_data
        if runtime.rx_key is None:
            return
        self._unsub_rx = runtime.client.subscribe_infrared_rf_receive(
            self._on_ir_event
        )

    def _subscribe_temperature_sensor(self) -> None:
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
    def _on_temperature_change(self, event: Event[EventStateChangedData]) -> None:
        new_state = event.data.get("new_state")
        if new_state is None:
            return
        if self._update_current_temperature_from_state(new_state.state):
            self.async_write_ha_state()

    def _update_current_temperature_from_state(self, raw: str | None) -> bool:
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

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {}
        if self._sleep_minutes:
            attrs["sleep_timer_minutes"] = self._sleep_minutes
        return attrs

    # ----------------- frame send helper (shared with switch/button) ---------

    async def async_send_frame(self, frame: int) -> None:
        """Send any pre-built 28-bit frame via the IR proxy."""
        timings = codec.frame_to_raw_timings(frame)
        _LOGGER.debug("TX frame 0x%07X (%d entries)", frame, len(timings))
        await async_send_command(
            self.hass, self._tx_entity_id, _RawInfraredCommand(timings)
        )

    # ----------------- HA-driven commands -----------------

    async def async_turn_on(self) -> None:
        await self.async_set_hvac_mode(self._last_known_hvac)

    async def async_turn_off(self) -> None:
        await self.async_set_hvac_mode(HVACMode.OFF)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
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

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        if swing_mode not in SWING_MODE_TO_FRAME:
            raise ValueError(f"Unsupported swing_mode: {swing_mode}")
        previous = self._attr_swing_mode
        self._attr_swing_mode = swing_mode
        try:
            await self.async_send_frame(SWING_MODE_TO_FRAME[swing_mode])
        except Exception:
            self._attr_swing_mode = previous
            raise
        finally:
            self.async_write_ha_state()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        if preset_mode not in PRESET_MODES:
            raise ValueError(f"Unsupported preset_mode: {preset_mode}")
        previous_preset = self._attr_preset_mode
        previous_sleep = self._sleep_minutes
        try:
            await self._apply_preset(previous_preset, preset_mode)
            self._attr_preset_mode = preset_mode
        except Exception:
            self._attr_preset_mode = previous_preset
            self._sleep_minutes = previous_sleep
            raise
        finally:
            self.async_write_ha_state()

    async def _apply_preset(self, previous: str, new: str) -> None:
        """Translate a preset transition into one or two IR frames."""
        if new == PRESET_NONE:
            # Clearing: send the appropriate off-code per previous preset.
            if previous == PRESET_JET:
                # Exit Jet by re-asserting the normal state frame.
                await self._transmit_state(
                    power_on=self._attr_hvac_mode != HVACMode.OFF
                )
            elif previous == PRESET_SLEEP:
                await self.async_send_frame(codec.encode_sleep_timer(0))
                self._sleep_minutes = 0
            elif previous == PRESET_CLEAN:
                await self.async_send_frame(codec.CLEAN_OFF)
            elif previous == PRESET_PURIFY:
                await self.async_send_frame(codec.PURIFY_OFF)
            elif previous == PRESET_ECO:
                await self.async_send_frame(codec.ENERGY_SAVE_OFF)
            return

        if new == PRESET_JET:
            await self.async_send_frame(codec.JET_ON)
        elif new == PRESET_SLEEP:
            await self.async_send_frame(
                codec.encode_sleep_timer(DEFAULT_SLEEP_MINUTES)
            )
            self._sleep_minutes = DEFAULT_SLEEP_MINUTES
        elif new == PRESET_CLEAN:
            await self.async_send_frame(codec.CLEAN_ON)
        elif new == PRESET_PURIFY:
            await self.async_send_frame(codec.PURIFY_ON)
        elif new == PRESET_ECO:
            await self.async_send_frame(codec.ENERGY_SAVE_60)

    # ----------------- emit state frame -----------------

    async def _transmit_state(self, *, power_on: bool) -> None:
        if power_on:
            mode = HA_HVAC_TO_LG.get(self._attr_hvac_mode, Mode.COOL)
            temp = int(self._attr_target_temperature or DEFAULT_TARGET_TEMP_C)
            fan = HA_FAN_TO_LG.get(self._attr_fan_mode or FAN_AUTO, Fan.AUTO)
            state = LgAcState(power_on=True, mode=mode, temp=temp, fan=fan)
        else:
            state = LgAcState(power_on=False)
        await self.async_send_frame(codec.encode(state))

    # ----------------- receive (state sync from original remote) -------------

    @callback
    def _on_ir_event(self, event: InfraredRFReceiveEvent) -> None:
        runtime = self._entry.runtime_data
        if event.key != runtime.rx_key:
            return

        frame = codec.decode_frame(list(event.timings))
        if frame is None:
            return

        if self._absorb_named_frame(frame):
            return

        if (minutes := codec.decode_sleep_timer(frame)) is not None:
            if minutes == 0:
                if self._attr_preset_mode == PRESET_SLEEP:
                    self._attr_preset_mode = PRESET_NONE
                    self._sleep_minutes = 0
                    self.async_write_ha_state()
            else:
                self._attr_preset_mode = PRESET_SLEEP
                self._sleep_minutes = minutes
                self.async_write_ha_state()
            return

        if frame in FRAME_TO_SWING_MODE:
            new_swing = FRAME_TO_SWING_MODE[frame]
            if self._attr_swing_mode != new_swing:
                self._attr_swing_mode = new_swing
                self.async_write_ha_state()
            return

        state = codec.decode_state(frame)
        if state is None:
            _LOGGER.debug("RX 0x%07X — unrecognised frame, ignored", frame)
            return

        _LOGGER.debug("RX 0x%07X decoded as %s", frame, state)
        self._apply_received_state(state)

    def _absorb_named_frame(self, frame: int) -> bool:
        """Handle Function-frames that map to presets. Return True if handled."""
        if frame == codec.JET_ON:
            if self._attr_preset_mode != PRESET_JET:
                self._attr_preset_mode = PRESET_JET
                self.async_write_ha_state()
            return True
        if frame == codec.CLEAN_ON:
            if self._attr_preset_mode != PRESET_CLEAN:
                self._attr_preset_mode = PRESET_CLEAN
                self.async_write_ha_state()
            return True
        if frame == codec.CLEAN_OFF:
            if self._attr_preset_mode == PRESET_CLEAN:
                self._attr_preset_mode = PRESET_NONE
                self.async_write_ha_state()
            return True
        if frame == codec.PURIFY_ON:
            if self._attr_preset_mode != PRESET_PURIFY:
                self._attr_preset_mode = PRESET_PURIFY
                self.async_write_ha_state()
            return True
        if frame == codec.PURIFY_OFF:
            if self._attr_preset_mode == PRESET_PURIFY:
                self._attr_preset_mode = PRESET_NONE
                self.async_write_ha_state()
            return True
        if frame == codec.TIMER_CLEAR_ALL:
            if self._sleep_minutes or self._attr_preset_mode == PRESET_SLEEP:
                self._sleep_minutes = 0
                if self._attr_preset_mode == PRESET_SLEEP:
                    self._attr_preset_mode = PRESET_NONE
                self.async_write_ha_state()
            return True
        return False

    def _apply_received_state(self, state: LgAcState) -> None:
        """Mirror a received state frame into the entity attributes."""
        changed = False
        if not state.power_on:
            if self._attr_hvac_mode != HVACMode.OFF:
                self._attr_hvac_mode = HVACMode.OFF
                changed = True
        else:
            # A normal state frame implicitly clears Jet (LG behaviour).
            if self._attr_preset_mode == PRESET_JET:
                self._attr_preset_mode = PRESET_NONE
                changed = True

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
