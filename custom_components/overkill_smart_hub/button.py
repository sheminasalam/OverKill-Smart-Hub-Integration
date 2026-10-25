"""Button entities — one per learned IR command."""
from __future__ import annotations

from homeassistant.components import mqtt as ha_mqtt
from homeassistant.components.button import ButtonEntity
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
    coord.register_platform(entry.entry_id, "button", async_add_entities)
    entry.async_on_unload(
        lambda: coord.unregister_platform(entry.entry_id, async_add_entities)
    )
    # Entities arrive asynchronously via discovery messages.
    async_add_entities([])


class OverkillButton(OverkillEntity, ButtonEntity):
    """A learned IR command, published as a HA button."""

    def __init__(self, hub, unique_id: str, payload: dict) -> None:
        super().__init__(hub, unique_id, payload)
        self._command_topic = payload.get("cmd_t") or payload.get("command_topic")
        self._payload_press = payload.get("pl_prs", "1")
        self._availability_topic = payload.get("avty_t")

    async def async_added_to_hass(self) -> None:
        if self._availability_topic:
            await self._sub(self._availability_topic, self._on_availability)

    async def _on_availability(self, msg) -> None:
        payload = msg.payload
        if isinstance(payload, (bytes, bytearray)):
            payload = payload.decode("utf-8", errors="replace")
        online = (payload or "").strip().lower() != "offline"
        self.set_availability(online)

    async def async_press(self) -> None:
        if not self._command_topic:
            return
        await self._hub.async_publish(self._command_topic, self._payload_press)
