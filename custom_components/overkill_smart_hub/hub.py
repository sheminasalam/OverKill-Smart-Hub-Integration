"""Per-hub runtime: broker binding + Alexa catalogue mirror + HA entity discovery."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from homeassistant.components import mqtt as ha_mqtt
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback

from .const import (
    CONF_AWS_TAG,
    CONF_BROKER_ENTRY_ID,
    CONF_BRIDGE_ENTRY_ID,
    CONF_HUB_NAME,
    CONF_TOPIC_PREFIX,
)

_LOGGER = logging.getLogger(__name__)


class HubRuntime:
    """One OverKill hub on one broker (local or cloud)."""

    def __init__(self, coord, entry: ConfigEntry) -> None:
        self.coord = coord
        self.hass: HomeAssistant = coord.hass
        self.entry = entry
        self.cfg: dict[str, Any] = {**entry.data, **entry.options}

        self.name: str = self.cfg[CONF_HUB_NAME]
        self.topic_prefix: str = self.cfg[CONF_TOPIC_PREFIX].rstrip("/")
        self.aws_tag: str = str(self.cfg[CONF_AWS_TAG]).upper()
        self.broker_entry_id: str = self.cfg.get(CONF_BROKER_ENTRY_ID, "")
        # A missing key means a legacy hub that can be migrated to the
        # primary bridge. An explicitly empty value means intentionally local-only.
        self.bridge_assignment_explicit: bool = (
            CONF_BRIDGE_ENTRY_ID in entry.data
            or CONF_BRIDGE_ENTRY_ID in entry.options
        )
        self.bridge_entry_id: str = str(
            self.cfg.get(CONF_BRIDGE_ENTRY_ID, "") or ""
        )

        # Alexa catalogue mirror
        self.devices: dict[str, dict] = {}
        self.sync: dict | None = None
        self.ok: list[dict] = []
        self.complete: bool = False
        # MQTT callbacks are scheduled as independent HA tasks. Serialize
        # device-slot and sync updates so a sync-triggered retained-slot clear
        # cannot race a newer device config for the same slot.
        self._alexa_message_lock = asyncio.Lock()
        # Slot topics are retained. When a sync signature changes, defer
        # clearing stale retained slots until the new catalogue is complete;
        # a slot can be reused by the new generation during discovery.
        self._alexa_stale_slots: set[str] = set()

        self._unsubs: list = []
        self._entity_add_callbacks: dict[str, Any] = {}

        # One MQTT subscription per topic, fanned out to all entity listeners.
        self._topic_listeners: dict[str, list] = {}
        # Protect first-subscription creation: two entities can request the same
        # topic while the first network subscribe is still awaiting completion.
        self._topic_subscribe_lock = asyncio.Lock()

        # Set after _do_start successfully establishes the hub subscriptions.
        self._subs_active: bool = False
        self._start_lock = asyncio.Lock()
        self._stopping: bool = False

        # Serialize chip-identity rebuilds and buffer discovery messages that
        # arrive while the old device/entities are being removed.
        self._rebuild_in_flight: bool = False
        self._rebuild_buffer: list = []

        # Hub-level device glue.
        self.ha_identifier: str | None = None   # e.g. "irhub_FD4A03"
        self._hub_device_created: bool = False
        self._hub_status_entity = None

    # -------------------------------------------------------------- Broker

    @property
    def broker(self):
        """Return the shared cloud broker for cloud-discovered hubs; None means local MQTT."""
        if not self.broker_entry_id:
            return None
        broker = getattr(self.coord, "cloud_broker", None)
        if broker is not None and self.broker_entry_id == broker.entry_id:
            return broker
        # A stale/legacy broker binding should not crash hub startup.
        return None

    async def async_publish(self, topic: str, payload: str | bytes,
                            retain: bool = False) -> bool:
        """Publish through the hub's configured broker; report acceptance."""
        try:
            if self.broker_entry_id and self.broker is None:
                # This hub belongs to a cloud transport that is currently
                # unavailable. Never leak its traffic to the local broker.
                _LOGGER.error(
                    "OverKill hub '%s': cannot publish to %s — configured "
                    "cloud MQTT transport %s is unavailable",
                    self.name, topic, self.broker_entry_id,
                )
                return False
            if self.broker is None:
                if not ha_mqtt.is_connected(self.hass):
                    _LOGGER.error(
                        "OverKill hub '%s': Home Assistant local MQTT is not "
                        "connected; cannot publish topic=%s",
                        self.name, topic,
                    )
                    return False
                _LOGGER.info(
                    "OverKill hub '%s': publishing to Home Assistant local MQTT "
                    "topic=%s payload_bytes=%d retain=%s",
                    self.name, topic,
                    len(payload) if isinstance(payload, (bytes, bytearray, str)) else 0,
                    retain,
                )
                await ha_mqtt.async_publish(
                    self.hass, topic, payload, qos=1, retain=retain
                )
            else:
                accepted = await self.broker.async_publish(
                    topic, payload, retain=retain
                )
                if accepted is False:
                    return False
            return True
        except Exception:  # noqa: BLE001
            _LOGGER.exception(
                "OverKill hub '%s': publish to %s failed", self.name, topic
            )
            return False

    # ---------------------------------------------------------------- Life

    async def async_start(self) -> None:
        # A hub entry can load before its MQTT transport entry during an HA
        # restart. Never silently fall back to local MQTT while its cloud
        # broker has not been registered yet.
        if self.broker_entry_id and self.broker is None:
            _LOGGER.info(
                "OverKill hub '%s': waiting for cloud broker %s to load",
                self.name, self.broker_entry_id,
            )
            self.coord.register_hub_waiting_for_broker(self)
            return

        await self.async_resume()

    async def _do_start(self) -> None:
        """Create hub subscriptions once, rolling back if stop races startup."""
        async with self._start_lock:
            if self._stopping or self._subs_active:
                return
            self._subs_active = True

            def _cleanup_partial_start() -> None:
                for unsub in list(self._unsubs):
                    try:
                        unsub()
                    except Exception:  # noqa: BLE001
                        _LOGGER.debug(
                            "OverKill hub '%s': partial unsubscribe failed",
                            self.name, exc_info=True,
                        )
                self._unsubs.clear()
                self._topic_listeners.clear()

            def _abandon_if_stopped() -> bool:
                """Roll back after an await if async_stop was requested."""
                if self._subs_active and not self._stopping:
                    return False
                self._subs_active = False
                _cleanup_partial_start()
                return True

            try:
                await self._subscribe(
                    f"{self.topic_prefix}/_system/alexa/#",
                    self._on_alexa_message,
                )
                if _abandon_if_stopped():
                    return

                await self._subscribe(
                    f"{self.topic_prefix}/_system/ha/+/+/config",
                    self._on_discovery_message,
                )
                if _abandon_if_stopped():
                    return

                await self.async_publish(
                    f"{self.topic_prefix}/_system/ha/birth", "online"
                )
                if _abandon_if_stopped():
                    return
            except Exception:
                self._subs_active = False
                _cleanup_partial_start()
                raise

            _LOGGER.info(
                "OverKill hub '%s': subscribed on %s",
                self.name, self.broker_entry_id or "local",
            )

    async def async_resume(self) -> None:
        """Start once the configured broker exists and restore entity listeners."""
        if self.broker_entry_id and self.broker is None:
            self.coord.register_hub_waiting_for_broker(self)
            return

        await self._do_start()

        # During rediscovery the broker runtime is replaced. Restore callback
        # listeners owned by already-created entities without duplicating the
        # entity's own subscription bookkeeping. Stop can race these awaits
        # too, so check the runtime after every subscription.
        if self._stopping or not self._subs_active:
            return
        for entity in list(self.coord._entities.values()):
            if getattr(entity, "_hub", None) is not self:
                continue
            for topic, cb in list(getattr(entity, "_own_subscriptions", [])):
                if self._stopping or not self._subs_active:
                    await self.async_stop()
                    return
                await self._subscribe(topic, cb)
                if self._stopping or not self._subs_active:
                    # _subscribe may have completed after stop cleared its
                    # bookkeeping; stop again to remove that late subscription.
                    await self.async_stop()
                    return

    async def async_stop(self) -> None:
        # Mark the runtime stopped before waiting for an in-flight startup.
        # _do_start checks this after every await and rolls back partial work.
        self._stopping = True
        self._subs_active = False
        async with self._start_lock:
            for unsub in list(self._unsubs):
                try:
                    unsub()
                except Exception:  # noqa: BLE001
                    _LOGGER.debug(
                        "OverKill hub '%s': unsubscribe failed during stop",
                        self.name, exc_info=True,
                    )
            self._unsubs.clear()
            self._topic_listeners.clear()
            self._rebuild_buffer.clear()
            self._alexa_stale_slots.clear()
            # If a rebuild is in progress, let its finally block clear this
            # flag and discard buffered messages after confirming hub liveness.
            if not self._rebuild_in_flight:
                self._rebuild_in_flight = False

    # ------------------------------------------------------ Subscription

    def _unsubscribe(self, topic: str, cb) -> None:
        """Remove one entity callback but keep the network subscription cached."""
        listeners = self._topic_listeners.get(topic)
        if not listeners:
            return
        try:
            listeners.remove(cb)
        except ValueError:
            return
        # Keep the (possibly empty) list in the dictionary. The MQTT
        # subscription itself remains active until hub unload; a later entity
        # can then reuse it rather than creating a duplicate subscription.

    async def _subscribe(self, topic: str, cb) -> None:
        """Subscribe once per topic, without leaving partial state on failure."""
        async with self._topic_subscribe_lock:
            if self._stopping or not self._subs_active:
                return

            # Reuse an established network subscription and fan messages out
            # to additional entity listeners. No await occurs after this check.
            listeners = self._topic_listeners.get(topic)
            if listeners is not None:
                if cb not in listeners:
                    listeners.append(cb)
                return

            async def _dispatch(msg) -> None:
                for listener in list(self._topic_listeners.get(topic, [])):
                    try:
                        await listener(msg)
                    except Exception:  # noqa: BLE001
                        _LOGGER.exception(
                            "OverKill hub '%s': listener failed on %s",
                            self.name, topic,
                        )

            # Establish the network subscription first. If this raises, no
            # callback list or unsubscribe handle is left partially registered.
            if self.broker is None:
                @callback
                def _wrap(msg):
                    self.hass.async_create_task(_dispatch(msg))

                unsub = await ha_mqtt.async_subscribe(
                    self.hass, topic, _wrap, qos=1
                )
            else:
                unsub = await self.broker.async_subscribe(
                    topic, lambda m: self.hass.async_create_task(_dispatch(m))
                )

            # async_stop can run while the network subscribe is awaiting.
            # In that case undo the just-created subscription instead of
            # attaching it to an already stopped runtime.
            if self._stopping or not self._subs_active:
                try:
                    unsub()
                except Exception:  # noqa: BLE001
                    _LOGGER.debug(
                        "OverKill hub '%s': late subscription cleanup failed for %s",
                        self.name, topic, exc_info=True,
                    )
                return

            # Only register local state after subscribe succeeds. A retained
            # message arriving in this tiny interval may be dropped, which is
            # safer than dispatching against partially initialized state.
            self._topic_listeners[topic] = [cb]
            self._unsubs.append(unsub)

    # --------------------------------------------- Alexa catalogue mirror

    async def _on_alexa_message(self, msg) -> None:
        """Serialize catalogue updates, then publish the merged AWS view."""
        async with self._alexa_message_lock:
            if self._stopping:
                return
            changed = await self._process_alexa_message_locked(msg)
        # Do not hold the per-hub lock while AWS publication waits for QoS ACK;
        # subsequent MQTT updates must still be able to update the snapshot.
        if changed:
            await self.coord.async_hub_catalog_changed()

    async def _process_alexa_message_locked(self, msg) -> bool:
        """Apply one retained Alexa slot/sync message in serialized order."""
        parts = str(msg.topic).split("/")
        try:
            at = parts.index("_system")
        except ValueError:
            return False
        if at + 1 >= len(parts) or parts[at + 1] != "alexa":
            return False
        kind = parts[at + 2] if at + 2 < len(parts) else ""
        slot = parts[at + 3] if at + 3 < len(parts) else None

        raw = msg.payload
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8", errors="replace")
        raw = (raw or "").strip()

        data: Any = None
        if raw:
            try:
                data = json.loads(raw)
            except ValueError:
                return False

        previous_complete = self.complete
        previous_ok = list(self.ok)

        if kind == "device" and slot:
            if data:
                if not isinstance(data, dict):
                    _LOGGER.warning(
                        "OverKill hub '%s': ignored non-object Alexa device "
                        "payload at slot=%s topic=%s", self.name, slot, msg.topic
                    )
                    return False
                self.devices[slot] = data
                current_sig = self.sync.get("sig") if isinstance(self.sync, dict) else None
                if current_sig is not None and data.get("sig") == current_sig:
                    # This slot has been reused by the current generation. Do
                    # not later publish a tombstone for the new config.
                    self._alexa_stale_slots.discard(slot)
                elif current_sig is not None and data.get("sig") != current_sig:
                    self._alexa_stale_slots.add(slot)
                _LOGGER.debug(
                    "OverKill hub '%s': received Alexa device config slot=%s "
                    "id=%s sig=%s", self.name, slot, data.get("id"), data.get("sig")
                )
                # Learn the real chip-derived tag from IDs such as "4A03_1".
                dev_id = str(data.get("id", "")) if isinstance(data, dict) else ""
                if "_" in dev_id:
                    real_tag = dev_id.split("_", 1)[0].upper()
                    if len(real_tag) == 4:
                        await self.coord.async_update_hub_tag(self, real_tag)
            else:
                removed = self.devices.pop(slot, None)
                if removed is not None:
                    self._alexa_stale_slots.add(slot)
                    _LOGGER.info(
                        "OverKill hub '%s': Alexa device slot=%s was cleared "
                        "(device link/config removed); waiting for the new sync "
                        "generation before publishing the updated AWS catalogue",
                        self.name, slot,
                    )
        elif kind == "sync":
            if not data:
                _LOGGER.info(
                    "OverKill hub '%s': cleared sync topic — removing "
                    "integration entry (hub may have switched to Native "
                    "HA mode, or the firmware reset)",
                    self.name,
                )
                entry_id = self.entry.entry_id
                # A fresh retained sync can arrive while HA is still finalizing
                # removal. Resubscribe after removal to replay that retained
                # message and recreate the hub entry if it is back in Custom mode.
                await self.hass.config_entries.async_remove(entry_id)
                self.hass.async_create_task(self.coord.async_rediscover())
                return False
            if not isinstance(data, dict):
                _LOGGER.warning(
                    "OverKill hub '%s': ignored invalid Alexa sync payload at %s",
                    self.name, msg.topic,
                )
                return False
            self.sync = data
            _LOGGER.info(
                "OverKill hub '%s': received Alexa sync sig=%s expected_devices=%s "
                "prefix=%s", self.name, data.get("sig"), data.get("count"),
                self.topic_prefix,
            )
            want = data.get("sig")
            for k, d in list(self.devices.items()):
                if d.get("sig") != want:
                    self._alexa_stale_slots.add(k)
                    del self.devices[k]
        else:
            return False

        if isinstance(self.sync, dict):
            want_sig = self.sync.get("sig")
            try:
                want_count = int(self.sync.get("count", -1))
            except (TypeError, ValueError):
                want_count = -1
            valid = [
                d for d in self.devices.values()
                if isinstance(d, dict)
                and d.get("sig") == want_sig
                and {"id", "name", "type"} <= d.keys()
            ]
            self.complete = want_count >= 0 and len(valid) == want_count
            if self.complete:
                self.ok = sorted(
                    (
                        {"id": str(d["id"]), "name": str(d["name"]),
                         "type": str(d["type"])}
                        for d in valid
                    ),
                    key=lambda x: x["id"],
                )

        if self.complete:
            # Only clear retained slots after the new generation is complete.
            # Slots now present in the current generation are excluded, since
            # they may have been reused for a different Alexa-linked device.
            current_sig = self.sync.get("sig") if isinstance(self.sync, dict) else None
            current_slots = {
                slot for slot, device in self.devices.items()
                if isinstance(device, dict)
                and device.get("sig") == current_sig
                and {"id", "name", "type"} <= device.keys()
            }
            self._alexa_stale_slots.difference_update(current_slots)
            stale_to_clear = sorted(self._alexa_stale_slots)
            for slot in stale_to_clear:
                self._alexa_stale_slots.discard(slot)
                topic = f"{self.topic_prefix}/_system/alexa/device/{slot}"
                _LOGGER.info(
                    "OverKill hub '%s': clearing stale retained Alexa device slot "
                    "after sync completed topic=%s",
                    self.name, topic,
                )
                await self.async_publish(topic, "", retain=True)

        if self.complete:
            if not previous_complete or self.ok != previous_ok:
                _LOGGER.info(
                    "OverKill hub '%s': Alexa catalogue complete; valid_devices=%d "
                    "expected=%s slots=%d signature=%s",
                    self.name, len(self.ok),
                    self.sync.get("count") if isinstance(self.sync, dict) else "?",
                    len(self.devices),
                    self.sync.get("sig") if isinstance(self.sync, dict) else "?",
                )
        elif previous_complete:
            _LOGGER.warning(
                "OverKill hub '%s': Alexa catalogue became incomplete; retaining "
                "the last complete AWS catalogue until sync finishes",
                self.name,
            )

        return True

    # ---------------------------------------------- HA entity discovery

    async def _on_discovery_message(self, msg) -> None:
        from .discovery import handle_discovery_message
        await handle_discovery_message(self, msg)
