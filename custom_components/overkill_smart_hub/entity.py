"""Shared entity scaffolding."""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN


def _as_id_list(value) -> list[str]:
    """Normalize a device identifier or list of identifiers to a list."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(x) for x in value if x]
    return [str(value)]


def _device_info_from_payload(payload: dict, hub) -> DeviceInfo:
    """Build DeviceInfo for a child entity (button/sensor).

    Sub-device identifiers from the firmware are only unique within one
    hub (for example, ``irhub_device_3`` can exist on multiple hubs).
    Namespace them with the parent identifier so multi-hub installs do not
    merge unrelated remotes.
    """
    dev = payload.get("dev") or {}
    ids = _as_id_list(dev.get("ids"))
    via_ids = _as_id_list(dev.get("via_device"))
    parent = via_ids[0] if via_ids else None

    if parent and ids:
        # A child remote: make its identifier unique across all hubs.
        ident = f"{parent}__{ids[0]}"
    elif ids:
        # Hub-level entities (such as temperature) retain the hub ID.
        ident = ids[0]
    else:
        ident = str(payload.get("uniq_id", "unknown"))

    di = DeviceInfo(
        identifiers={(DOMAIN, ident)},
        name=dev.get("name") or hub.name,
        manufacturer=dev.get("mf", "OverKill"),
        model=dev.get("mdl", "IR Remote"),
        sw_version=dev.get("sw"),
    )

    # Keep the parent link only when this is genuinely a child device.
    if parent and parent != ident:
        di["via_device"] = (DOMAIN, parent)

    return di


class OverkillEntity(Entity):
    """Base class: entity is a thin wrapper around one discovery payload."""

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, hub, unique_id: str, payload: dict) -> None:
        self._hub = hub
        self._payload = payload
        self._attr_unique_id = unique_id
        self._attr_name = payload.get("name")
        self._attr_device_info = _device_info_from_payload(payload, hub)
        self._available = True
        # Track entity-owned subscriptions so callbacks are detached when HA
        # removes this entity after a retained discovery payload is cleared.
        self._own_subscriptions: list[tuple[str, object]] = []
        self._entity_removed = False

    async def _sub(self, topic: str, cb) -> None:
        """Subscribe and remember the callback for removal cleanup.

        Record the intent before awaiting the hub's network subscribe. If HA
        removes this entity during that await, the post-await check undoes the
        subscription after it becomes visible in the hub's listener registry.
        """
        item = (topic, cb)
        self._own_subscriptions.append(item)
        try:
            await self._hub._subscribe(topic, cb)
        except Exception:
            try:
                self._own_subscriptions.remove(item)
            except ValueError:
                pass
            raise

        if self._entity_removed:
            self._hub._unsubscribe(topic, cb)
            try:
                self._own_subscriptions.remove(item)
            except ValueError:
                pass

    async def async_will_remove_from_hass(self) -> None:
        """Release references held by the hub's topic fan-out registry."""
        self._entity_removed = True
        for topic, cb in list(self._own_subscriptions):
            self._hub._unsubscribe(topic, cb)
        self._own_subscriptions.clear()

    @property
    def available(self) -> bool:
        return self._available

    def update_payload(self, payload: dict) -> None:
        self._payload = payload
        self._attr_name = payload.get("name")
        self._attr_device_info = _device_info_from_payload(payload, self._hub)
        self.async_write_ha_state()

    def set_availability(self, online: bool) -> None:
        self._available = online
        self.async_write_ha_state()
