"""LG AC over HA native infrared platform.

Bridges a Home Assistant climate entity to an ESPHome `ir_rf_proxy` device,
speaking the classic 28-bit LG-AC IR protocol.

The integration uses the HA `infrared` platform for the transmit path (clean,
upstream-supported) and reaches into `aioesphomeapi` for the receive path
because HA-Core's `esphome` integration does not yet wire ir_rf_proxy
receivers as `InfraredReceiverEntity` (verified on HA 2026.4 / dev).
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import entity_registry as er

from .const import CONF_ESPHOME_ENTRY_ID, CONF_RECEIVER_ENTITY_ID

if TYPE_CHECKING:
    from aioesphomeapi import APIClient

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.CLIMATE]


@dataclass(slots=True)
class LgAcRuntimeData:
    """Runtime data attached to the ConfigEntry."""

    client: APIClient
    rx_key: int | None


type LgAcConfigEntry = ConfigEntry[LgAcRuntimeData]


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

    # When the underlying ESPHome entry reloads (proxy reboot, OTA, etc.) the
    # APIClient we just cached becomes stale and the RX subscription dies on
    # the disconnected connection. Reload ourselves so a fresh client is
    # picked up. The outer async_on_unload ensures we deregister our listener
    # if we are unloaded first (avoiding a dangling reference on the
    # ESPHome entry).
    entry.async_on_unload(
        esphome_entry.async_on_unload(
            lambda: hass.config_entries.async_schedule_reload(entry.entry_id)
        )
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: LgAcConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


def _get_esphome_client(esphome_entry: ConfigEntry) -> APIClient | None:
    """Extract the aioesphomeapi APIClient from an esphome config entry.

    HA 2026.4+ stores it on entry.runtime_data.client. This indirection
    isolates us from minor attribute shuffles in future HA releases.
    """
    runtime = getattr(esphome_entry, "runtime_data", None)
    if runtime is None:
        return None
    return getattr(runtime, "client", None)


async def _resolve_receiver_key(
    hass: HomeAssistant, client: APIClient, receiver_entity_id: str
) -> int | None:
    """Map an HA infrared receiver entity_id to the ESPHome protobuf key.

    Strategy:
    1. If the user picked a specific entity, match its unique_id suffix
       against the ESPHome object_id of each receiver.
    2. If exactly one receiver exists on the device, fall back to that.
    """
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
            # ESPHome unique_ids follow "<mac>-infrared-<object_id>"
            for info in receivers:
                if ha_entry.unique_id.endswith(f"-{info.object_id}"):
                    return info.key

    # Single-receiver fallback covers the typical IR-proxy setup.
    if len(receivers) == 1:
        return receivers[0].key
    return None
