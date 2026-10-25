"""Sensor entities (temperature, and future extras)."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DATA_COORDINATOR, DOMAIN
from .entity import OverkillEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coord = hass.data[DOMAIN][DATA_COORDINATOR]
    coord.register_platform(entry.entry_id, "sensor", async_add_entities)
    entry.async_on_unload(
        lambda: coord.unregister_platform(entry.entry_id, async_add_entities)
    )
    async_add_entities([])


class OverkillSensor(OverkillEntity, SensorEntity):
    """A sensor published by the hub (temperature today)."""

    def __init__(self, hub, unique_id: str, payload: dict) -> None:
        super().__init__(hub, unique_id, payload)
        self._state_topic = payload.get("stat_t") or payload.get("state_topic")
        self._availability_topic = payload.get("avty_t")
        self._attr_device_class = payload.get("dev_cla")
        self._attr_native_unit_of_measurement = payload.get("unit_of_meas")
        self._attr_state_class = payload.get("stat_cla")
        self._native_value = None

    async def async_added_to_hass(self) -> None:
        if self._state_topic:
            await self._sub(self._state_topic, self._on_state)
        if self._availability_topic:
            await self._sub(self._availability_topic, self._on_availability)

    async def _on_state(self, msg) -> None:
        payload = msg.payload
        if isinstance(payload, (bytes, bytearray)):
            payload = payload.decode("utf-8", errors="replace")
        payload = (payload or "").strip()
        if not payload:
            self._native_value = None
        else:
            try:
                self._native_value = float(payload)
            except ValueError:
                self._native_value = payload
        self.async_write_ha_state()

    async def _on_availability(self, msg) -> None:
        payload = msg.payload
        if isinstance(payload, (bytes, bytearray)):
            payload = payload.decode("utf-8", errors="replace")
        online = (payload or "").strip().lower() != "offline"
        self.set_availability(online)

    @property
    def native_value(self):
        return self._native_value
