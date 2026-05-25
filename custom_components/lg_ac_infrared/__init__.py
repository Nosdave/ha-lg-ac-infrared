"""LG AC over HA native infrared platform.

Bridges a Home Assistant climate entity (plus switches and buttons) to
an ESPHome `ir_rf_proxy` device, speaking the classic 28-bit LG-AC IR
protocol.

TX uses the HA `infrared.async_send_command` helper. RX uses
`aioesphomeapi.subscribe_infrared_rf_receive` directly because HA-Core's
`esphome` integration does not yet wire ir_rf_proxy receivers as
`InfraredReceiverEntity` (verified on HA 2026.5.4 stable).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import TYPE_CHECKING, Any

import voluptuous as vol

from homeassistant.components.infrared import async_send_command
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er

from . import codec
from .const import (
    CONF_ESPHOME_ENTRY_ID,
    CONF_RECEIVER_ENTITY_ID,
    CONF_TRANSMITTER_ENTITY_ID,
    DOMAIN,
    SERVICE_CLEAR_TIMERS,
    SERVICE_SET_SCHEDULE_TIMER,
    SERVICE_SET_SLEEP_TIMER,
)

if TYPE_CHECKING:
    from aioesphomeapi import APIClient

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.CLIMATE,
    Platform.BUTTON,
]


@dataclass(slots=True)
class LgAcRuntimeData:
    """Runtime data attached to the ConfigEntry."""

    client: APIClient
    rx_key: int | None
    # Hook for switch/button entities to send a frame without going
    # through the climate entity. Set by climate.py during setup.
    send_frame: Any = field(default=None)


type LgAcConfigEntry = ConfigEntry[LgAcRuntimeData]


_SCHEDULE_TIMER_SCHEMA = vol.Schema(
    {
        vol.Required("action"): vol.In(["on", "off"]),
        vol.Required("minutes"): vol.All(int, vol.Range(min=0, max=1439)),
    },
    extra=vol.ALLOW_EXTRA,
)
_SLEEP_TIMER_SCHEMA = vol.Schema(
    {vol.Required("minutes"): vol.All(int, vol.Range(min=0, max=1439))},
    extra=vol.ALLOW_EXTRA,
)


async def async_setup_entry(hass: HomeAssistant, entry: LgAcConfigEntry) -> bool:
    """Set up LG AC Infrared from a config entry."""
    esphome_entry_id: str = entry.data[CONF_ESPHOME_ENTRY_ID]
    esphome_entry = hass.config_entries.async_get_entry(esphome_entry_id)
    if esphome_entry is None:
        _LOGGER.error("Referenced ESPHome config entry %s is gone", esphome_entry_id)
        return False
    if esphome_entry.state is not ConfigEntryState.LOADED:
        raise ConfigEntryNotReady(
            f"ESPHome entry {esphome_entry.title!r} not loaded yet"
        )

    client = _get_esphome_client(esphome_entry)
    if client is None:
        raise ConfigEntryNotReady(
            "Cannot reach aioesphomeapi.APIClient via ESPHome runtime_data"
        )

    rx_key = await _resolve_receiver_key(
        hass, client, entry.data.get(CONF_RECEIVER_ENTITY_ID, "")
    )
    if rx_key is None and entry.data.get(CONF_RECEIVER_ENTITY_ID):
        _LOGGER.warning(
            "Receiver entity %s not found on ESPHome device — state sync "
            "from the original remote will be disabled",
            entry.data[CONF_RECEIVER_ENTITY_ID],
        )

    entry.runtime_data = LgAcRuntimeData(client=client, rx_key=rx_key)

    entry.async_on_unload(
        esphome_entry.async_on_unload(
            lambda: hass.config_entries.async_schedule_reload(entry.entry_id)
        )
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _async_register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: LgAcConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


# ---------------------------------------------------------------------------
# Service actions
# ---------------------------------------------------------------------------


def _async_register_services(hass: HomeAssistant) -> None:
    """Register integration services (once)."""
    if hass.services.has_service(DOMAIN, SERVICE_SET_SLEEP_TIMER):
        return

    async def _send_via_climate(call: ServiceCall, frame: int) -> None:
        """Send a raw frame via the target climate entity's IR proxy."""
        entity_ids = await async_extract_entity_ids(hass, call)
        if not entity_ids:
            raise HomeAssistantError("No target entity specified")
        for entity_id in entity_ids:
            tx_id = _resolve_tx_for_climate(hass, entity_id)
            if tx_id is None:
                _LOGGER.warning("Could not resolve transmitter for %s", entity_id)
                continue
            timings = codec.frame_to_raw_timings(frame)
            from .climate import _RawInfraredCommand  # local import to avoid cycle
            await async_send_command(hass, tx_id, _RawInfraredCommand(timings))

    async def handle_sleep_timer(call: ServiceCall) -> None:
        data = _SLEEP_TIMER_SCHEMA(dict(call.data))
        frame = codec.encode_sleep_timer(data["minutes"])
        await _send_via_climate(call, frame)

    async def handle_schedule_timer(call: ServiceCall) -> None:
        data = _SCHEDULE_TIMER_SCHEMA(dict(call.data))
        frame = codec.encode_schedule_timer(
            turn_on=(data["action"] == "on"), minutes=data["minutes"]
        )
        await _send_via_climate(call, frame)

    async def handle_clear_timers(call: ServiceCall) -> None:
        await _send_via_climate(call, codec.TIMER_CLEAR_ALL)

    hass.services.async_register(
        DOMAIN, SERVICE_SET_SLEEP_TIMER, handle_sleep_timer
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SET_SCHEDULE_TIMER, handle_schedule_timer
    )
    hass.services.async_register(
        DOMAIN, SERVICE_CLEAR_TIMERS, handle_clear_timers
    )


async def async_extract_entity_ids(
    hass: HomeAssistant, call: ServiceCall
) -> list[str]:
    """Resolve target entity_ids from a service call."""
    from homeassistant.helpers.service import async_extract_entity_ids as helper
    return list(await helper(hass, call))


def _resolve_tx_for_climate(
    hass: HomeAssistant, climate_entity_id: str
) -> str | None:
    """Find the transmitter entity_id behind a given climate entity."""
    entity_registry = er.async_get(hass)
    ent = entity_registry.async_get(climate_entity_id)
    if ent is None or ent.config_entry_id is None:
        return None
    entry = hass.config_entries.async_get_entry(ent.config_entry_id)
    if entry is None or entry.domain != DOMAIN:
        return None
    return entry.data.get(CONF_TRANSMITTER_ENTITY_ID)


# ---------------------------------------------------------------------------
# ESPHome client lookup helpers
# ---------------------------------------------------------------------------


def _get_esphome_client(esphome_entry: ConfigEntry) -> APIClient | None:
    runtime = getattr(esphome_entry, "runtime_data", None)
    if runtime is None:
        return None
    return getattr(runtime, "client", None)


async def _resolve_receiver_key(
    hass: HomeAssistant, client: APIClient, receiver_entity_id: str
) -> int | None:
    """Map an HA infrared receiver entity_id to the ESPHome protobuf key."""
    try:
        from aioesphomeapi.model import InfraredCapability, InfraredInfo
    except ImportError:
        _LOGGER.error("aioesphomeapi.model.InfraredInfo not available")
        return None

    entities, _ = await client.list_entities_services()
    receivers = [
        info
        for info in entities
        if isinstance(info, InfraredInfo)
        and (getattr(info, "capabilities", 0) & InfraredCapability.RECEIVER)
    ]
    if not receivers:
        return None

    if receiver_entity_id:
        entity_registry = er.async_get(hass)
        ha_entry = entity_registry.async_get(receiver_entity_id)
        if ha_entry is not None and ha_entry.unique_id:
            for info in receivers:
                if ha_entry.unique_id.endswith(f"-{info.object_id}"):
                    return info.key

    if len(receivers) == 1:
        return receivers[0].key
    return None
