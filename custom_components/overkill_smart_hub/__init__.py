"""OverKill Smart Hub — integration entry point."""
from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall

from .const import (
    CONF_ENTRY_TYPE,
    DATA_COORDINATOR,
    DOMAIN,
    ENTRY_TYPE_AWS_BRIDGE,
    ENTRY_TYPE_HUB,
    ENTRY_TYPE_MQTT_TRANSPORT,
)
from .coordinator import OverkillCoordinator

_LOGGER = logging.getLogger(__name__)
PLATFORMS = ["button", "sensor", "binary_sensor"]

SERVICE_REDISCOVER = "rediscover_hubs"


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register the rediscover service once, at integration load."""

    async def _async_rediscover(call: ServiceCall) -> None:
        coord: OverkillCoordinator | None = (
            hass.data.get(DOMAIN, {}).get(DATA_COORDINATOR)
        )
        if coord is None:
            _LOGGER.warning("Rediscover requested but no coordinator is running")
            return
        await coord.async_rediscover()
        _LOGGER.info("OverKill Smart Hub: rediscovery triggered")

    hass.services.async_register(
        DOMAIN, SERVICE_REDISCOVER, _async_rediscover,
        schema=vol.Schema({}),
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})
    coord: OverkillCoordinator = hass.data[DOMAIN].setdefault(
        DATA_COORDINATOR, OverkillCoordinator(hass)
    )

    etype = entry.data.get(CONF_ENTRY_TYPE)
    if etype == ENTRY_TYPE_HUB:
        ok = await coord.async_add_hub(entry)
        if ok:
            await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    elif etype == ENTRY_TYPE_MQTT_TRANSPORT:
        await coord.async_add_mqtt_transport(entry)
    elif etype == ENTRY_TYPE_AWS_BRIDGE:
        await coord.async_add_bridge(entry)
    else:
        _LOGGER.error("unknown entry type %r", etype)
        return False

    await coord.async_ensure_local_listener()

    # Reconcile retained discovery after each entry starts. This also repairs
    # cases where HA entries were removed/re-added in the same HA session.
    hass.async_create_task(coord.async_rediscover())

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coord: OverkillCoordinator | None = (
        hass.data.get(DOMAIN, {}).get(DATA_COORDINATOR)
    )
    if coord is None:
        return True

    etype = entry.data.get(CONF_ENTRY_TYPE)
    if etype == ENTRY_TYPE_HUB:
        await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
        await coord.async_remove_hub(entry)
    elif etype == ENTRY_TYPE_MQTT_TRANSPORT:
        await coord.async_remove_mqtt_transport(entry)
    elif etype == ENTRY_TYPE_AWS_BRIDGE:
        await coord.async_remove_bridge(entry)

    # If this is the last configured entry, fully dispose of runtime state.
    remaining = [
        e for e in hass.config_entries.async_entries(DOMAIN)
        if e.entry_id != entry.entry_id
    ]
    if not remaining:
        _LOGGER.info(
            "OverKill Smart Hub: last entry removed — tearing down coordinator"
        )
        await coord.async_stop()
        hass.data[DOMAIN].pop(DATA_COORDINATOR, None)
        if not hass.data[DOMAIN]:
            hass.data.pop(DOMAIN, None)
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    # A bridge entry unload caused by an options edit is a reload, not removal;
    # keep its retained AWS catalogue intact during the short reconnect window.
    coord: OverkillCoordinator | None = (
        hass.data.get(DOMAIN, {}).get(DATA_COORDINATOR)
    )
    if coord is not None:
        if entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_AWS_BRIDGE:
            # Preserve its retained AWS catalogue during an options reload.
            coord.mark_bridge_reloading(entry.entry_id)
        elif entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_HUB:
            # Keep the old complete catalogue while this hub's runtime is rebuilt.
            coord.mark_hub_reloading(entry.entry_id)
    await hass.config_entries.async_reload(entry.entry_id)
