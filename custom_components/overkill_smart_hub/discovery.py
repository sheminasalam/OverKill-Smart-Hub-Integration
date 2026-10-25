"""Parse a single HA discovery config and mint the appropriate entity."""
from __future__ import annotations

import json
import logging

from .button import OverkillButton
from .sensor import OverkillSensor

_LOGGER = logging.getLogger(__name__)


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
    parts = topic.split("/")
    if len(parts) < 3 or parts[-1] != "config":
        return

    component = parts[-3]
    uid = parts[-2]

    raw = msg.payload
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="replace")
    raw = (raw or "").strip()

    if not raw:
        await hub.hass.async_create_task(hub.coord.async_remove_entity(uid))
        return

    try:
        payload = json.loads(raw)
    except ValueError:
        _LOGGER.warning("Bad discovery JSON on %s", topic)
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
