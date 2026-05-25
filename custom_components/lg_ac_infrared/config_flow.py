"""Config flow for LG AC Infrared."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.infrared import (
    DOMAIN as INFRARED_DOMAIN,
    async_get_emitters,
    async_get_receivers,
)
from homeassistant.config_entries import (
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
)
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    TextSelector,
)

from .const import (
    CONF_ESPHOME_ENTRY_ID,
    CONF_NAME,
    CONF_RECEIVER_ENTITY_ID,
    CONF_TEMPERATURE_SENSOR,
    CONF_TRANSMITTER_ENTITY_ID,
    DEFAULT_NAME,
    DOMAIN,
)


class LgAcInfraredConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial setup flow."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Single-step config: pick a transmitter + receiver + name."""
        errors: dict[str, str] = {}

        emitter_ids = list(async_get_emitters(self.hass))
        receiver_ids = list(async_get_receivers(self.hass))
        if not emitter_ids:
            return self.async_abort(reason="no_infrared_emitter")

        if user_input is not None:
            transmitter_id: str = user_input[CONF_TRANSMITTER_ENTITY_ID]
            esphome_entry_id = self._find_esphome_entry_id(transmitter_id)
            if esphome_entry_id is None:
                errors["base"] = "transmitter_not_on_esphome"
            else:
                receiver_id: str = user_input.get(CONF_RECEIVER_ENTITY_ID, "")
                await self.async_set_unique_id(
                    f"{esphome_entry_id}-{transmitter_id}"
                )
                self._abort_if_unique_id_configured()

                data: dict[str, Any] = {
                    CONF_NAME: user_input[CONF_NAME].strip() or DEFAULT_NAME,
                    CONF_TRANSMITTER_ENTITY_ID: transmitter_id,
                    CONF_RECEIVER_ENTITY_ID: receiver_id,
                    CONF_ESPHOME_ENTRY_ID: esphome_entry_id,
                }
                if user_input.get(CONF_TEMPERATURE_SENSOR):
                    data[CONF_TEMPERATURE_SENSOR] = user_input[
                        CONF_TEMPERATURE_SENSOR
                    ]
                return self.async_create_entry(title=data[CONF_NAME], data=data)

        defaults = user_input or {}
        schema_dict: dict[Any, Any] = {
            vol.Required(
                CONF_NAME, default=defaults.get(CONF_NAME, DEFAULT_NAME)
            ): TextSelector(),
            vol.Required(
                CONF_TRANSMITTER_ENTITY_ID,
                default=defaults.get(CONF_TRANSMITTER_ENTITY_ID),
            ): EntitySelector(
                EntitySelectorConfig(
                    domain=INFRARED_DOMAIN, include_entities=emitter_ids
                )
            ),
        }
        if receiver_ids:
            schema_dict[
                vol.Optional(
                    CONF_RECEIVER_ENTITY_ID,
                    default=defaults.get(CONF_RECEIVER_ENTITY_ID, ""),
                )
            ] = EntitySelector(
                EntitySelectorConfig(
                    domain=INFRARED_DOMAIN, include_entities=receiver_ids
                )
            )
        schema_dict[
            vol.Optional(
                CONF_TEMPERATURE_SENSOR,
                default=defaults.get(CONF_TEMPERATURE_SENSOR, ""),
            )
        ] = EntitySelector(
            EntitySelectorConfig(domain="sensor", device_class="temperature")
        )

        return self.async_show_form(
            step_id="user", data_schema=vol.Schema(schema_dict), errors=errors
        )

    def _find_esphome_entry_id(self, transmitter_entity_id: str) -> str | None:
        """Resolve which ESPHome config entry owns this transmitter entity.

        If a device has multiple ESPHome entries (rare leftover from a
        re-adoption), prefer the one that is currently LOADED.
        """
        entity_registry = er.async_get(self.hass)
        device_registry = dr.async_get(self.hass)

        ent = entity_registry.async_get(transmitter_entity_id)
        if ent is None or ent.device_id is None:
            return None
        device = device_registry.async_get(ent.device_id)
        if device is None:
            return None

        esphome_entries = [
            entry_id
            for entry_id in device.config_entries
            if (entry := self.hass.config_entries.async_get_entry(entry_id))
            and entry.domain == "esphome"
        ]
        if not esphome_entries:
            return None
        if len(esphome_entries) == 1:
            return esphome_entries[0]
        loaded = [
            entry_id
            for entry_id in esphome_entries
            if (entry := self.hass.config_entries.async_get_entry(entry_id))
            and entry.state is ConfigEntryState.LOADED
        ]
        if len(loaded) == 1:
            return loaded[0]
        return None  # ambiguous → caller surfaces error
