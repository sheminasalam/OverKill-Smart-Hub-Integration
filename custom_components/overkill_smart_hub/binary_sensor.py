"""Hub-level binary sensor — creates the parent hub device in HA."""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DATA_COORDINATOR, DOMAIN
from .entity import OverkillEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coord = hass.data[DOMAIN][DATA_COORDINATOR]
    coord.register_platform(entry.entry_id, "binary_sensor", async_add_entities)
    entry.async_on_unload(
        lambda: coord.unregister_platform(entry.entry_id, async_add_entities)
    )
    async_add_entities([])


class OverkillHubStatus(OverkillEntity, BinarySensorEntity):
    """Hub status entity that also materializes the parent hub device."""

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, hub, ha_identifier: str, sw_version: str | None = None):
        # Do not call OverkillEntity.__init__: this entity's DeviceInfo is
        # explicitly the hub itself, rather than a child discovery payload.
        self._hub = hub
        self._payload = {}
        self._attr_unique_id = f"{ha_identifier}_status"
        self._attr_name = "Connected"
        self._attr_should_poll = False
        self._attr_has_entity_name = True
        self._attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, ha_identifier)},
            name=hub.name,
            manufacturer="OverKill",
            model="Smart IR Hub",
            sw_version=sw_version,
        )
        self._attr_is_on = True
        self._available = True
        self._own_subscriptions: list[tuple[str, object]] = []
        # OverkillEntity.__init__ is intentionally skipped for the hub-level
        # entity, so initialize the lifecycle flag used by its shared _sub().
        self._entity_removed = False

    async def async_added_to_hass(self) -> None:
        # Cache the entity on the hub for future reference.
        self._hub._hub_status_entity = self

        # Mirror the hub's own availability topic.
        topic = f"{self._hub.topic_prefix}/_system/availability"
        await self._sub(topic, self._on_availability)

    async def _on_availability(self, msg) -> None:
        payload = msg.payload
        if isinstance(payload, (bytes, bytearray)):
            payload = payload.decode("utf-8", errors="replace")
        online = (payload or "").strip().lower() == "online"
        if online != self._attr_is_on:
            self._attr_is_on = online
            self.async_write_ha_state()
