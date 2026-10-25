"""Parse a single HA discovery config and mint the appropriate entity."""
from __future__ import annotations

import json
import logging

from .button import OverkillButton
from .sensor import OverkillSensor

_LOGGER = logging.getLogger(__name__)

_MAX_DISCOVERY_CONFIG_BYTES = 64 * 1024
_MAX_MQTT_TOPIC_LENGTH = 512
_TOPIC_FIELDS = (
    "cmd_t",
    "command_topic",
    "stat_t",
    "state_topic",
    "avty_t",
    "availability_topic",
)


def _topic_belongs_to_hub(topic: object, prefix: str) -> bool:
    """Accept only literal MQTT topics scoped to this hub's configured prefix."""
    if not isinstance(topic, str):
        return False
    topic = topic.strip()
    prefix = prefix.rstrip("/")
    if (
        not topic
        or len(topic) > _MAX_MQTT_TOPIC_LENGTH
        or not topic.startswith(f"{prefix}/")
        or any(ord(char) < 0x20 for char in topic)
        or "+" in topic
        or "#" in topic
        or "\\x00" in topic
    ):
        return False
    return True


async def handle_discovery_message(hub, msg) -> None:
    """Handle a retained HA discovery config emitted by hub firmware."""
    # Ignore callbacks that were already in flight when this runtime was
    # removed. During a chip rebuild, retain messages and replay them once the
    # old device/entities have been cleaned up.
    if (getattr(hub.coord, "_stopped", False)
            or hub.coord.hubs.get(hub.entry.entry_id) is not hub):
        return
    if hub._rebuild_in_flight:
        hub._rebuild_buffer.append(msg)
        return

    topic = str(msg.topic)
    expected_root = f"{hub.topic_prefix.rstrip('/')}/_system/ha/"
    if (
        not topic.startswith(expected_root)
        or len(topic) > _MAX_MQTT_TOPIC_LENGTH
        or any(ord(char) < 0x20 for char in topic)
    ):
        _LOGGER.warning("Ignored discovery topic outside hub namespace")
        return

    parts = topic.split("/")
    if len(parts) < 3 or parts[-1] != "config":
        return

    component = parts[-3]
    uid = parts[-2]
    if component not in ("button", "sensor") or not uid or len(uid) > 128:
        _LOGGER.warning("Ignored invalid discovery component or identifier on %s", topic)
        return

    raw = msg.payload
    if isinstance(raw, (bytes, bytearray)):
        if len(raw) > _MAX_DISCOVERY_CONFIG_BYTES:
            _LOGGER.warning("Ignored oversized discovery payload on %s", topic)
            return
        raw = raw.decode("utf-8", errors="replace")
    raw = (raw or "").strip()
    if len(raw.encode("utf-8")) > _MAX_DISCOVERY_CONFIG_BYTES:
        _LOGGER.warning("Ignored oversized discovery payload on %s", topic)
        return

    if not raw:
        await hub.hass.async_create_task(hub.coord.async_remove_entity(uid))
        return

    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        _LOGGER.warning("Bad discovery JSON on %s", topic)
        return

    if not isinstance(payload, dict):
        _LOGGER.warning("Ignored non-object discovery payload on %s", topic)
        return

    prefix = hub.topic_prefix.rstrip("/")
    for field in _TOPIC_FIELDS:
        value = payload.get(field)
        if value is not None and value != "" and not _topic_belongs_to_hub(value, prefix):
            _LOGGER.warning(
                "Ignored discovery config with out-of-namespace MQTT topic field %s on %s",
                field, topic,
            )
            return

    payload["_topic"] = topic

    # Learn the hub's identifier from any discovery payload. Two cases:
    #
    # 1. Child entity (button): payload has `via_device = "irhub_XXXXXX"`.
    #    That's the hub's own identifier.
    #
    # 2. Hub-level entity (temperature sensor): payload has `ids` starting
    #    with `irhub_` but no `via_device`. The id IS the hub identifier.
    #
    # Either way, we get the chip suffix and can correct the AWS tag.
    dev = payload.get("dev") or {}
    origin = payload.get("o") or {}
    via_raw = dev.get("via_device")
    if isinstance(via_raw, (list, tuple)):
        via_raw = via_raw[0] if via_raw else None
    via = str(via_raw) if via_raw else None

    ids_raw = dev.get("ids")
    if isinstance(ids_raw, (list, tuple)):
        ids_raw = ids_raw[0] if ids_raw else None
    first_id = str(ids_raw) if ids_raw else None

    hub_identifier: str | None = None
    if via:
        hub_identifier = via
    elif first_id and first_id.startswith("irhub_") \
            and not first_id.startswith("irhub_device_"):
        # Hub-level sensor (temperature). The id itself is the hub.
        hub_identifier = first_id

    if hub_identifier:
        if not hub.ha_identifier:
            hub.ha_identifier = hub_identifier
        elif hub.ha_identifier != hub_identifier:
            # A different chip is publishing on this prefix. Rebuild the old
            # hub device/entities before attaching the new chip's discovery.
            await hub.coord.async_rebuild_hub(hub, hub_identifier)

        chip = hub_identifier.replace("irhub_", "").replace("irhub-", "")
        if len(chip) >= 4:
            await hub.coord.async_update_hub_tag(hub, chip[-4:].upper())
        # Firmware version appears in dev.sw for temperature discovery and
        # o.sw for button discovery. Accept both in either arrival order.
        sw_version = dev.get("sw") or origin.get("sw")
        await hub.coord.async_ensure_hub_device(hub, sw_version=sw_version)

    if component == "button":
        entity = OverkillButton(hub, uid, payload)
    elif component == "sensor":
        entity = OverkillSensor(hub, uid, payload)
    else:
        _LOGGER.debug("Ignoring discovery component %s", component)
        return

    await hub.coord.async_add_entity(hub, entity)
