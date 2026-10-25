"""Central coordinator: hubs, MQTT transport, AWS bridge, entity plumbing."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components import mqtt as ha_mqtt
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store

from .aws_bridge import AwsBridgeRuntime
from .cloud_broker import CloudBrokerRuntime
from .const import (
    CONF_AWS_TAG,
    CONF_BRIDGE_ENTRY_ID,
    CONF_BROKER_ENTRY_ID,
    CONF_ENTRY_TYPE,
    CONF_HUB_NAME,
    CONF_MQTT_MODE,
    CONF_TOPIC_PREFIX,
    DATA_COORDINATOR,
    DOMAIN,
    ENTRY_TYPE_AWS_BRIDGE,
    ENTRY_TYPE_HUB,
    ENTRY_TYPE_MQTT_TRANSPORT,
    MQTT_MODE_BOTH,
    MQTT_MODE_CLOUD,
    MQTT_MODE_LOCAL,
    hub_config_unique_id,
    hub_prefix_key,
    mqtt_source_namespace,
)
from .hub import HubRuntime

_LOGGER = logging.getLogger(__name__)

_LOCAL_BROKER_ID = ""


class OverkillCoordinator:
    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.hubs: dict[str, HubRuntime] = {}
        self.by_prefix: dict[tuple[str, str], HubRuntime] = {}
        self.by_tag: dict[str, HubRuntime] = {}
        self.bridges: dict[str, AwsBridgeRuntime] = {}
        self._bridge_reloading: set[str] = set()
        self._hub_reloading: set[str] = set()

        self.cloud_broker: CloudBrokerRuntime | None = None
        self._transport_entry_id: str | None = None

        # Hubs whose configured cloud broker has not loaded yet.
        self._hubs_waiting_for_broker: dict[str, list[HubRuntime]] = {}

        self._local_listener_started = False
        self._local_unsubs: list = []

        self._entity_add_callbacks: dict[str, dict[str, list]] = {}
        self._entities: dict[str, Any] = {}
        self._pending_entities: dict[str, dict[str, list]] = {}

        self._hub_creation_in_flight: set[tuple[str, str]] = set()
        self._hubs_removing: set[str] = set()
        # Config entries that exist but whose hub runtime failed to start.
        # They must not block catalogue publication for healthy hubs assigned
        # to the same Alexa bridge. Cleared when retrying or removing the hub.
        self._hubs_failed_load: set[str] = set()
        # Serialize hardware-identifier rebuilds during discovery bursts.
        self._hub_rebuild_locks: dict[str, asyncio.Lock] = {}

        # Corrected chip tags are persisted separately from config entries.
        # Updating entry.data/options invokes HA's update listener and reloads
        # the hub, which is unnecessary churn for a tag learned from discovery.
        self._tag_store = Store(hass, 1, "overkill_smart_hub.tags")
        self._tag_cache: dict[str, str] = {}
        self._tag_loaded = False
        self._tag_store_lock = asyncio.Lock()
        self._stopped = False

    # --------------------------------------------- Local MQTT listener

    async def async_ensure_local_listener(self) -> None:
        """Listen for local hubs only after a local-capable transport is chosen.

        This deliberately prevents hub prompts from appearing while the user
        is still configuring the AWS bridge and before the transport page.
        """
        if self._stopped:
            return
        transport_entry = next(
            (
                entry
                for entry in self.hass.config_entries.async_entries(DOMAIN)
                if entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_MQTT_TRANSPORT
            ),
            None,
        )
        if transport_entry is None:
            await self.async_stop_local_listener()
            return
        transport_data = {**transport_entry.data, **transport_entry.options}
        mode = transport_data.get(CONF_MQTT_MODE, MQTT_MODE_LOCAL)
        if mode not in (MQTT_MODE_LOCAL, MQTT_MODE_BOTH):
            await self.async_stop_local_listener()
            return
        if self._local_listener_started:
            return
        self._local_listener_started = True

        @callback
        def _on_local_sync(msg) -> None:
            # Filter here so only real sync topics reach discovery handling.
            if not msg.topic.endswith("/_system/alexa/sync"):
                return
            self.hass.async_create_task(
                self._async_hub_seen(msg.topic, _LOCAL_BROKER_ID)
            )

        @callback
        def _on_local_directive(msg) -> None:
            # Diagnostic echo: proves the message was seen on the MQTT broker
            # used by Home Assistant. This listener never republishes it.
            payload = msg.payload
            size = len(payload) if isinstance(payload, (bytes, bytearray, str)) else 0
            _LOGGER.info(
                "OverKill local MQTT diagnostic: observed topic=%s "
                "payload_bytes=%d retain=%s",
                msg.topic, size, getattr(msg, "retain", False),
            )

        # MQTT '+' matches a single level only. Subscribe for topic prefixes
        # with one, two, or three segments so deeper valid firmware prefixes
        # are discovered as well.
        self._local_unsubs = []
        try:
            for pattern in (
                "+/_system/alexa/sync",
                "+/+/_system/alexa/sync",
                "+/+/+/_system/alexa/sync",
            ):
                self._local_unsubs.append(
                    await ha_mqtt.async_subscribe(
                        self.hass, pattern, _on_local_sync, qos=1
                    )
                )
            # Observe the local command topic too. If AWS reception and route
            # logs appear but this echo does not, the issue is at the local
            # MQTT publish/broker boundary rather than AWS subscription.
            self._local_unsubs.append(
                await ha_mqtt.async_subscribe(
                    self.hass, "alexa/+/directive", _on_local_directive, qos=1
                )
            )
        except Exception:
            for unsub in self._local_unsubs:
                try:
                    unsub()
                except Exception:  # noqa: BLE001
                    _LOGGER.debug("Local MQTT unsubscribe failed during setup", exc_info=True)
            self._local_unsubs = []
            self._local_listener_started = False
            raise
        _LOGGER.info("OverKill Smart Hub: local discovery listener active")

    async def async_stop_local_listener(self) -> None:
        """Unsubscribe all local discovery patterns and reset listener state."""
        for unsub in getattr(self, "_local_unsubs", []) or []:
            try:
                unsub()
            except Exception:  # noqa: BLE001
                _LOGGER.debug(
                    "Local MQTT unsubscribe failed", exc_info=True
                )
        self._local_unsubs = []
        self._local_listener_started = False

    # --------------------------------------------------------- AWS bridges

    def configured_bridge_entries(self) -> list[ConfigEntry]:
        """Return configured bridge entries in stable Home Assistant order."""
        return [
            entry
            for entry in self.hass.config_entries.async_entries(DOMAIN)
            if entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_AWS_BRIDGE
        ]

    def primary_bridge_entry_id(self) -> str | None:
        """Stable default bridge for legacy hubs that predate assignments."""
        entries = self.configured_bridge_entries()
        return entries[0].entry_id if entries else None

    def bridge_entry_id_for_hub(self, hub: HubRuntime) -> str | None:
        """Return the explicit bridge assignment for a hub.

        Old entries without the assignment are treated as belonging to the one
        available bridge only. Once multiple bridges exist, missing assignments
        are not guessed here; migration persists the primary assignment first.
        """
        assigned = str(getattr(hub, "bridge_entry_id", "") or "")
        if assigned:
            return assigned
        if getattr(hub, "bridge_assignment_explicit", False):
            return None
        entries = self.configured_bridge_entries()
        if len(entries) == 1:
            return entries[0].entry_id
        return None

    def bridge_entry_id_for_config_entry(self, entry: ConfigEntry) -> str | None:
        """Resolve bridge assignment before a hub runtime has loaded."""
        cfg = {**entry.data, **entry.options}
        if CONF_BRIDGE_ENTRY_ID in cfg:
            return str(cfg.get(CONF_BRIDGE_ENTRY_ID) or "") or None
        entries = self.configured_bridge_entries()
        return entries[0].entry_id if len(entries) == 1 else None

    def configured_hub_entry_ids_for_bridge(self, bridge_entry_id: str) -> set[str]:
        """Return configured hub entries assigned to this bridge, excluding unloads."""
        return {
            entry.entry_id
            for entry in self.hass.config_entries.async_entries(DOMAIN)
            if entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_HUB
            and entry.entry_id not in self._hubs_failed_load
            and (
                entry.entry_id not in self._hubs_removing
                or entry.entry_id in self._hub_reloading
            )
            and self.bridge_entry_id_for_config_entry(entry) == bridge_entry_id
        }

    async def _async_migrate_legacy_bridge_assignments(
        self, primary_bridge_id: str | None
    ) -> None:
        """Persist the primary bridge on legacy hubs with no assignment field."""
        if not primary_bridge_id:
            return
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            if entry.data.get(CONF_ENTRY_TYPE) != ENTRY_TYPE_HUB:
                continue
            if (CONF_BRIDGE_ENTRY_ID in entry.data
                    or CONF_BRIDGE_ENTRY_ID in entry.options):
                continue
            new_data = dict(entry.data)
            new_data[CONF_BRIDGE_ENTRY_ID] = primary_bridge_id
            new_options = dict(entry.options)
            new_options.pop(CONF_BRIDGE_ENTRY_ID, None)
            # If a runtime is already live, update it before scheduling the
            # config-entry reload so routing/catalogue isolation is immediate.
            runtime = self.hubs.get(entry.entry_id)
            if runtime is not None:
                runtime.bridge_entry_id = primary_bridge_id
                runtime.bridge_assignment_explicit = True
                runtime.cfg[CONF_BRIDGE_ENTRY_ID] = primary_bridge_id
            self.hass.config_entries.async_update_entry(
                entry, data=new_data, options=new_options
            )
            _LOGGER.info(
                "OverKill: migrated legacy hub '%s' to Alexa bridge %s",
                entry.title, primary_bridge_id,
            )

    # ------------------------------------------------------------- Hubs

    async def async_add_hub(self, entry: ConfigEntry) -> bool:
        # A fresh setup/reload attempt means the previous failed-load marker
        # is no longer authoritative. If this attempt fails again, it is added
        # back in the exception path below.
        self._hubs_failed_load.discard(entry.entry_id)
        if entry.entry_id in self.hubs:
            return True

        # Migrate pre-multi-bridge entries before constructing the runtime.
        # The first configured bridge remains the backward-compatible default.
        if (CONF_BRIDGE_ENTRY_ID not in entry.data
                and CONF_BRIDGE_ENTRY_ID not in entry.options):
            primary_bridge_id = self.primary_bridge_entry_id()
            if primary_bridge_id:
                new_data = dict(entry.data)
                new_data[CONF_BRIDGE_ENTRY_ID] = primary_bridge_id
                self.hass.config_entries.async_update_entry(entry, data=new_data)

        await self._async_load_tag_store()
        hub = HubRuntime(self, entry)
        stored_tag = self.get_corrected_tag(hub.topic_prefix, hub.broker_entry_id)
        if stored_tag:
            hub.aws_tag = stored_tag
            hub.name = f"OverKill Hub {stored_tag}"

        # Publish the runtime mappings before yielding to MQTT startup. Retained
        # discovery callbacks can run during async_start and must see a
        # registered hub when adding entities or correcting its tag.
        self.hubs[entry.entry_id] = hub
        self.by_prefix[hub_prefix_key(hub.topic_prefix, hub.broker_entry_id)] = hub
        self.by_tag[hub.aws_tag] = hub
        try:
            await hub.async_start()
        except Exception:
            _LOGGER.exception("hub start failed")
            if self.hubs.get(entry.entry_id) is hub:
                self.hubs.pop(entry.entry_id, None)
            prefix_key = hub_prefix_key(hub.topic_prefix, hub.broker_entry_id)
            if self.by_prefix.get(prefix_key) is hub:
                self.by_prefix.pop(prefix_key, None)
            if self.by_tag.get(hub.aws_tag) is hub:
                self.by_tag.pop(hub.aws_tag, None)
            self._unregister_waiting_hub(hub)
            try:
                await hub.async_stop()
            except Exception:  # noqa: BLE001
                _LOGGER.debug("Failed to stop hub after startup error", exc_info=True)
            self._hub_reloading.discard(entry.entry_id)
            self._hubs_removing.discard(entry.entry_id)
            # The config entry remains registered after runtime startup fails.
            # Exclude it from the readiness barrier so healthy hubs on the
            # same bridge can continue publishing their complete catalogue.
            self._hubs_failed_load.add(entry.entry_id)
            assigned_bridge = self.bridge_entry_id_for_config_entry(entry)
            _LOGGER.warning(
                "OverKill: hub entry %s failed to start; excluding it from "
                "catalogue readiness for bridge %s until retry or removal",
                entry.entry_id, assigned_bridge or "none",
            )
            # A catalogue update may have been withheld while this hub was
            # starting. Re-evaluate all bridges now that the failed entry no
            # longer blocks their readiness barrier.
            await self.async_publish_catalogs(force=True)
            return False

        self._hub_reloading.discard(entry.entry_id)
        self._hubs_failed_load.discard(entry.entry_id)
        _LOGGER.info(
            "OverKill Smart Hub: hub '%s' (tag=%s, prefix=%s, broker=%s) online",
            hub.name, hub.aws_tag, hub.topic_prefix,
            hub.broker_entry_id or "local",
        )
        await self.async_publish_catalogs(force=True)
        return True

    async def async_remove_hub(self, entry: ConfigEntry) -> None:
        self._hubs_removing.add(entry.entry_id)
        hub = self.hubs.pop(entry.entry_id, None)
        if hub is None:
            self._hubs_removing.discard(entry.entry_id)
            self._hub_reloading.discard(entry.entry_id)
            self._hubs_failed_load.discard(entry.entry_id)
            return
        self._unregister_waiting_hub(hub)
        # Only clear index entries if they still point to this runtime. A newer
        # hub may already have claimed the same tag or prefix.
        prefix_key = hub_prefix_key(hub.topic_prefix, hub.broker_entry_id)
        if self.by_prefix.get(prefix_key) is hub:
            self.by_prefix.pop(prefix_key, None)
        if self.by_tag.get(hub.aws_tag) is hub:
            self.by_tag.pop(hub.aws_tag, None)
        for uid in [k for k, v in self._entities.items()
                    if getattr(v, "_hub", None) is hub]:
            self._entities.pop(uid, None)
        self._entity_add_callbacks.pop(entry.entry_id, None)
        self._pending_entities.pop(entry.entry_id, None)
        await hub.async_stop()
        try:
            await self.async_publish_catalogs(force=True)
        finally:
            self._hubs_removing.discard(entry.entry_id)
            self._hubs_failed_load.discard(entry.entry_id)

    def hub_for_prefix(
        self, prefix: str, broker_entry_id: str | None = None
    ) -> HubRuntime | None:
        """Resolve a prefix by source; without source, refuse ambiguous matches."""
        if broker_entry_id is not None:
            return self.by_prefix.get(hub_prefix_key(prefix, broker_entry_id))
        matches = [
            hub for (namespace, stored_prefix), hub in self.by_prefix.items()
            if stored_prefix == str(prefix).rstrip("/")
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            _LOGGER.warning(
                "OverKill: topic prefix %s exists on both local and cloud MQTT; "
                "use a source-qualified lookup",
                prefix,
            )
        return None

    def hub_for_tag(
        self, tag: str, bridge_entry_id: str | None = None
    ) -> HubRuntime | None:
        """Resolve a command tag, optionally confined to one AWS bridge."""
        tag = str(tag).upper()

        if bridge_entry_id is not None:
            # In multi-account mode the tag index is intentionally not used:
            # two separate bridges may legitimately have the same 4-hex tag.
            matches = [
                candidate for candidate in self.hubs.values()
                if not candidate._stopping
                and str(candidate.aws_tag).upper() == tag
                and self.bridge_entry_id_for_hub(candidate) == bridge_entry_id
            ]
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                _LOGGER.error(
                    "OverKill: refusing ambiguous route for tag %s on bridge %s; "
                    "matching hubs=%s",
                    tag, bridge_entry_id,
                    ", ".join(f"{hub.name}@{hub.topic_prefix}" for hub in matches),
                )
                return None

            # Recovery fallback remains scoped to this bridge, never global.
            recovered = []
            for candidate in self.hubs.values():
                if candidate._stopping or self.bridge_entry_id_for_hub(candidate) != bridge_entry_id:
                    continue
                for dev in candidate.devices.values():
                    if not isinstance(dev, dict):
                        continue
                    dev_id = str(dev.get("id", ""))
                    if "_" in dev_id and dev_id.split("_", 1)[0].upper() == tag:
                        recovered.append(candidate)
                        break
            if len(recovered) == 1:
                return recovered[0]
            if len(recovered) > 1:
                _LOGGER.error(
                    "OverKill: refusing ambiguous catalogue fallback for tag %s "
                    "on bridge %s", tag, bridge_entry_id,
                )
            return None

        # Without an originating bridge, never pick an arbitrary runtime when
        # two Alexa accounts legitimately use the same four-character tag.
        matches = [
            candidate for candidate in self.hubs.values()
            if not candidate._stopping
            and str(candidate.aws_tag).upper() == tag
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            _LOGGER.error(
                "OverKill: refusing unscoped route for tag %s; it matches hubs %s "
                "assigned to different or ambiguous bridges",
                tag,
                ", ".join(
                    f"{hub.name}@{hub.topic_prefix}"
                    for hub in matches
                ),
            )
            return None

        recovered = []
        for candidate in self.hubs.values():
            if candidate._stopping:
                continue
            if any(
                isinstance(dev, dict)
                and "_" in str(dev.get("id", ""))
                and str(dev.get("id", "")).split("_", 1)[0].upper() == tag
                for dev in candidate.devices.values()
            ):
                recovered.append(candidate)
        if len(recovered) == 1:
            return recovered[0]
        if len(recovered) > 1:
            _LOGGER.error(
                "OverKill: refusing unscoped catalogue fallback for tag %s; "
                "multiple hubs match", tag,
            )
        return None

    def register_hub_waiting_for_broker(self, hub: HubRuntime) -> None:
        """Queue a hub for later subscription once its broker exists.

        Idempotent: re-queuing the same runtime for the same broker is a
        no-op. Reloads and rediscovery can reach this path more than once.
        """
        if not hub.broker_entry_id:
            return
        bucket = self._hubs_waiting_for_broker.setdefault(
            hub.broker_entry_id, []
        )
        if hub not in bucket:
            bucket.append(hub)

    async def _async_load_tag_store(self) -> None:
        """Load the persistent prefix-to-corrected-tag map exactly once."""
        async with self._tag_store_lock:
            if self._tag_loaded:
                return
            try:
                data = await self._tag_store.async_load()
                if isinstance(data, dict):
                    self._tag_cache = {
                        str(key): str(value).upper()
                        for key, value in data.items()
                        if key and value
                    }
                else:
                    self._tag_cache = {}
                self._tag_loaded = True
            except Exception:  # noqa: BLE001
                # Leave _tag_loaded false so a later call can retry the read.
                _LOGGER.exception("OverKill: failed to load tag store")

    def _tag_store_key(self, prefix: str, broker_entry_id: str | None = None) -> str:
        """Keep corrected tags distinct for local and cloud MQTT namespaces."""
        return f"{mqtt_source_namespace(broker_entry_id)}::{str(prefix).rstrip('/')}"

    async def _async_save_tag(
        self, prefix: str, tag: str, broker_entry_id: str | None = None
    ) -> None:
        """Persist one source-qualified corrected tag without clobbering peers."""
        await self._async_load_tag_store()
        key = self._tag_store_key(prefix, broker_entry_id)
        tag = str(tag).upper()
        async with self._tag_store_lock:
            if self._tag_cache.get(key) == tag:
                return
            updated = dict(self._tag_cache)
            updated[key] = tag
            try:
                await self._tag_store.async_save(updated)
            except Exception:  # noqa: BLE001
                # Do not update the in-memory cache on failure; the next
                # discovery message can retry the persistent write.
                _LOGGER.exception("OverKill: failed to save tag store")
                return
            self._tag_cache = updated

    def get_corrected_tag(
        self, prefix: str, broker_entry_id: str | None = None
    ) -> str | None:
        """Return a source-qualified tag, reading legacy keys for local hubs."""
        key = self._tag_store_key(prefix, broker_entry_id)
        value = self._tag_cache.get(key)
        if value:
            return value
        # Compatibility with pre-multi-source tag stores, whose keys were raw
        # topic prefixes. Treat old unqualified values as local-only so they
        # cannot seed a remote household with another hub's chip tag.
        if mqtt_source_namespace(broker_entry_id) == "local":
            return self._tag_cache.get(str(prefix).rstrip("/"))
        return None

    async def async_update_hub_tag(self, hub: HubRuntime, real_tag: str) -> None:
        """Correct a hub tag and persist it without triggering entry reload."""
        real_tag = real_tag.upper()
        old_tag = hub.aws_tag
        old_name = hub.name
        new_name = f"OverKill Hub {real_tag}"

        # Detect a second runtime claiming the same chip-derived tag. This
        # commonly follows a firmware MQTT base-topic change: the old retained
        # sync remains on the broker and its integration entry is still alive.
        # Its entities own the same firmware unique IDs, so remove that stale
        # entry before allowing this runtime to finish discovery.
        target_bridge_id = self.bridge_entry_id_for_hub(hub)
        existing = next(
            (
                candidate for candidate in self.hubs.values()
                if candidate is not hub
                and str(candidate.aws_tag).upper() == real_tag
                and self.bridge_entry_id_for_hub(candidate) == target_bridge_id
                and (
                    candidate.topic_prefix != hub.topic_prefix
                    or candidate.broker_entry_id != hub.broker_entry_id
                )
            ),
            None,
        )
        if existing is not None:
            _LOGGER.warning(
                "OverKill: hub '%s' (%s) collides on tag %s with existing "
                "hub '%s' (%s, broker=%s). Removing the older entry because "
                "the same physical chip tag is already registered for this "
                "Alexa bridge.",
                hub.name,
                hub.topic_prefix,
                real_tag,
                existing.name,
                existing.topic_prefix,
                existing.broker_entry_id or "local",
            )
            # Clear this mapping first: async_remove_hub must not remove a
            # newer runtime's mapping while the older config entry unloads.
            if self.by_tag.get(real_tag) is existing:
                self.by_tag.pop(real_tag, None)
            try:
                await self.hass.config_entries.async_remove(
                    existing.entry.entry_id
                )
            except Exception:  # noqa: BLE001
                _LOGGER.exception(
                    "OverKill: failed to remove stale hub entry %s",
                    existing.entry.entry_id,
                )

        # Correct runtime routing, removing only mappings that still point to
        # this hub so a tag collision cannot erase another hub's mapping.
        if old_tag != real_tag:
            if self.by_tag.get(old_tag) is hub:
                self.by_tag.pop(old_tag, None)
            if self.by_tag.get(old_tag.upper()) is hub:
                self.by_tag.pop(old_tag.upper(), None)
            hub.aws_tag = real_tag
            self.by_tag[real_tag] = hub
            _LOGGER.info(
                "OverKill hub '%s': tag corrected %s → %s",
                old_name, old_tag, real_tag,
            )

        if hub.name != new_name:
            hub.name = new_name

        # Dedicated Store persistence is independent of config-entry data and
        # therefore does not call HA's update listener or reload the hub.
        await self._async_save_tag(hub.topic_prefix, real_tag, hub.broker_entry_id)

        # The config-entry title is a title-only update, avoiding a data/options
        # change that would trigger a reload. Keep the device registry in sync.
        try:
            from homeassistant.helpers import device_registry as dr
            reg = dr.async_get(self.hass)
            if hub.ha_identifier:
                device = reg.async_get_device(
                    identifiers={(DOMAIN, hub.ha_identifier)}
                )
                if device is not None and device.name != new_name:
                    reg.async_update_device(device.id, name=new_name)
        except Exception:  # noqa: BLE001
            _LOGGER.debug("device registry update skipped", exc_info=True)

        if hub.entry.title != new_name:
            try:
                self.hass.config_entries.async_update_entry(
                    hub.entry, title=new_name
                )
            except Exception:  # noqa: BLE001
                _LOGGER.exception("failed to update hub entry title")

    def _unregister_waiting_hub(self, hub: HubRuntime) -> None:
        """Remove a hub from any pending broker-start queue."""
        for broker_id, waiting in list(self._hubs_waiting_for_broker.items()):
            try:
                waiting.remove(hub)
            except ValueError:
                continue
            if not waiting:
                self._hubs_waiting_for_broker.pop(broker_id, None)

    # ------------------------------------------- Entity plumbing

    def register_platform(self, entry_id: str, platform: str, add_entities) -> None:
        per = self._entity_add_callbacks.setdefault(entry_id, {})
        per.setdefault(platform, []).append(add_entities)
        pending = self._pending_entities.get(entry_id, {}).pop(platform, [])
        if pending:
            add_entities(pending)

    def unregister_platform(self, entry_id: str, add_entities) -> None:
        per = self._entity_add_callbacks.get(entry_id, {})
        for lst in per.values():
            if add_entities in lst:
                lst.remove(add_entities)

    async def async_add_entity(self, hub: HubRuntime, entity) -> None:
        # Ignore discovery messages that were already in flight during hub teardown.
        if hub.entry.entry_id not in self.hubs:
            return

        existing = self._entities.get(entity.unique_id)
        if existing is not None:
            try:
                existing.update_payload(entity._payload)
            except Exception:
                pass
            return

        self._entities[entity.unique_id] = entity

        platform = {
            "OverkillButton": "button",
            "OverkillSensor": "sensor",
            "OverkillHubStatus": "binary_sensor",
        }.get(type(entity).__name__)
        if platform is None:
            return

        callbacks = (
            self._entity_add_callbacks.get(hub.entry.entry_id, {}).get(platform, [])
        )
        if callbacks:
            for add_fn in callbacks:
                add_fn([entity])
            _LOGGER.debug("Added %s entity %s", platform, entity.unique_id)
        else:
            self._pending_entities.setdefault(
                hub.entry.entry_id, {}
            ).setdefault(platform, []).append(entity)

    async def async_rebuild_hub(self, hub: HubRuntime, new_identifier: str) -> None:
        """Rebuild a hub's device/entities after its physical chip changes.

        Discovery messages arrive in bursts. Serialize identity changes and
        replay messages buffered while the old chip's entities are removed.
        """
        lock = self._hub_rebuild_locks.setdefault(
            hub.entry.entry_id, asyncio.Lock()
        )
        buffered = []
        try:
            async with lock:
                if hub.ha_identifier == new_identifier:
                    return
                if (self._stopped
                        or self.hubs.get(hub.entry.entry_id) is not hub
                        or hub._stopping):
                    return

                hub._rebuild_in_flight = True
                old_identifier = hub.ha_identifier
                _LOGGER.warning(
                    "OverKill hub '%s': chip identifier changed %s → %s; "
                    "rebuilding hub device and entities",
                    hub.name, old_identifier, new_identifier,
                )
                try:
                    # Remove entity instances and their registry rows.
                    from homeassistant.helpers import entity_registry as er

                    entity_registry = er.async_get(self.hass)
                    platform_by_class = {
                        "OverkillButton": "button",
                        "OverkillSensor": "sensor",
                        "OverkillHubStatus": "binary_sensor",
                    }
                    old_entities = [
                        (uid, entity)
                        for uid, entity in list(self._entities.items())
                        if getattr(entity, "_hub", None) is hub
                    ]
                    for uid, entity in old_entities:
                        try:
                            await self.async_remove_entity(uid)
                        except Exception:  # noqa: BLE001
                            _LOGGER.exception(
                                "OverKill: entity removal failed during rebuild for %s",
                                uid,
                            )
                        platform = platform_by_class.get(type(entity).__name__)
                        if platform:
                            entity_id = entity_registry.async_get_entity_id(
                                platform, DOMAIN, uid
                            )
                            if entity_id:
                                try:
                                    entity_registry.async_remove(entity_id)
                                except Exception:  # noqa: BLE001
                                    _LOGGER.debug(
                                        "OverKill: stale entity registry cleanup skipped for %s",
                                        entity_id, exc_info=True,
                                    )

                    # Remove references queued before platform setup completed.
                    pending = self._pending_entities.get(hub.entry.entry_id, {})
                    for platform, entities in list(pending.items()):
                        remaining = [
                            entity for entity in entities
                            if getattr(entity, "_hub", None) is not hub
                        ]
                        if remaining:
                            pending[platform] = remaining
                        else:
                            pending.pop(platform, None)
                    if not pending:
                        self._pending_entities.pop(hub.entry.entry_id, None)

                    # Remove the old parent hub device from the registry.
                    try:
                        from homeassistant.helpers import device_registry as dr

                        registry = dr.async_get(self.hass)
                        if old_identifier:
                            device = registry.async_get_device(
                                identifiers={(DOMAIN, old_identifier)}
                            )
                            if device is not None:
                                registry.async_remove_device(device.id)
                    except Exception:  # noqa: BLE001
                        _LOGGER.debug(
                            "device registry cleanup skipped", exc_info=True
                        )

                    # Don't publish a new identity for a runtime already removed.
                    if (not self._stopped
                            and self.hubs.get(hub.entry.entry_id) is hub
                            and not hub._stopping):
                        hub.ha_identifier = new_identifier
                        hub._hub_device_created = False
                        hub._hub_status_entity = None
                finally:
                    hub._rebuild_in_flight = False
                    buffered = hub._rebuild_buffer
                    hub._rebuild_buffer = []
        finally:
            # Replay only after releasing the rebuild lock; replay can itself
            # discover another identifier and legitimately request a rebuild.
            if (buffered and not self._stopped
                    and self.hubs.get(hub.entry.entry_id) is hub
                    and not hub._stopping):
                from .discovery import handle_discovery_message

                for msg in buffered:
                    try:
                        await handle_discovery_message(hub, msg)
                    except Exception:  # noqa: BLE001
                        _LOGGER.exception(
                            "OverKill: buffered discovery replay failed"
                        )

    async def async_ensure_hub_device(self, hub, sw_version: str | None = None) -> None:
        """Create the hub device, and update its firmware version when learned later."""
        if not hub.ha_identifier:
            return

        if not hub._hub_device_created:
            from .binary_sensor import OverkillHubStatus

            entity = OverkillHubStatus(hub, hub.ha_identifier, sw_version=sw_version)
            try:
                await self.async_add_entity(hub, entity)
            except Exception:  # noqa: BLE001
                _LOGGER.exception(
                    "OverKill: hub device creation failed for '%s'", hub.name
                )
                return
            hub._hub_device_created = True
            _LOGGER.info(
                "OverKill Smart Hub: hub device '%s' (%s) registered",
                hub.name, hub.ha_identifier,
            )
            return

        # Another discovery payload may provide the version after the hub
        # status entity was created (buttons use o.sw, temperature uses dev.sw).
        if not sw_version:
            return
        # Update the cached entity's DeviceInfo too. This matters when the
        # next discovery message arrives before HA has registered the
        # pending entity and therefore before a DeviceEntry exists to patch.
        status_entity = self._entities.get(f"{hub.ha_identifier}_status")
        if status_entity is not None:
            info = dict(getattr(status_entity, "_attr_device_info", {}) or {})
            if info.get("sw_version") != sw_version:
                info["sw_version"] = sw_version
                status_entity._attr_device_info = info

        try:
            from homeassistant.helpers import device_registry as dr

            registry = dr.async_get(self.hass)
            device = registry.async_get_device(
                identifiers={(DOMAIN, hub.ha_identifier)}
            )
            if device is not None and device.sw_version != sw_version:
                registry.async_update_device(device.id, sw_version=sw_version)
                _LOGGER.debug(
                    "OverKill hub '%s': sw_version set to %s",
                    hub.name, sw_version,
                )
        except Exception:  # noqa: BLE001
            _LOGGER.debug("sw_version update skipped", exc_info=True)

    async def async_remove_entity(self, unique_id: str) -> None:
        entity = self._entities.pop(unique_id, None)
        if entity is None:
            return
        if getattr(entity, "hass", None) is not None:
            try:
                await entity.async_remove()
            except Exception:
                pass

    # ------------------------------------------- Hub auto-discovery

    async def _async_hub_seen(self, topic: str, broker_entry_id: str) -> None:
        if not topic.endswith("/_system/alexa/sync"):
            return
        prefix = topic[: -len("/_system/alexa/sync")]
        if not prefix:
            return

        prefix_key = hub_prefix_key(prefix, broker_entry_id)
        existing = self.by_prefix.get(prefix_key)
        if existing is not None:
            return

        if prefix_key in self._hub_creation_in_flight:
            return

        chip = prefix.rsplit("/", 1)[-1]
        # The MQTT base topic is user-defined, so its final segment is not
        # guaranteed to contain four characters (for example, home/ir/hub).
        # Use a temporary deterministic tag in that case; discovery payloads
        # later provide the real chip ID and async_update_hub_tag corrects it.
        tag = chip[-4:].upper() if len(chip) >= 4 else "AUTO"
        hub_name = f"OverKill Hub {tag}"

        # Avoid starting a duplicate flow during reload, when the runtime
        # mapping may be temporarily empty but the config entry still exists.
        namespace = mqtt_source_namespace(broker_entry_id)
        unique_id = hub_config_unique_id(prefix, tag, broker_entry_id)
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            if entry.data.get(CONF_ENTRY_TYPE) != ENTRY_TYPE_HUB:
                continue
            # Match existing entries by source + configured prefix as well as
            # unique ID. This catches legacy entries whose unique ID predates
            # source namespacing and avoids duplicate flows during reload.
            if (
                str(entry.data.get(CONF_TOPIC_PREFIX, "")).rstrip("/") == prefix
                and mqtt_source_namespace(entry.data.get(CONF_BROKER_ENTRY_ID, "")) == namespace
            ) or entry.unique_id == unique_id:
                return

        # During first-time onboarding, the same discovery callback should
        # feed the wizard's final confirmation page instead of opening a
        # second, detached config-flow dialog. Only use the hand-off while
        # that exact flow is still active; stale state is discarded.
        domain_data = self.hass.data.get(DOMAIN, {})
        wizard = domain_data.get("_initial_setup_wizard")
        if wizard is not None:
            flow_id = wizard.get("flow_id")
            try:
                active_flow_ids = {
                    item.get("flow_id")
                    for item in self.hass.config_entries.flow.async_progress()
                }
                wizard_active = flow_id in active_flow_ids
            except Exception:  # noqa: BLE001
                # Keep the hand-off if Home Assistant's flow manager cannot
                # report progress; the flow itself will clear it on success.
                wizard_active = True

            if not wizard_active:
                domain_data.pop("_initial_setup_wizard", None)
                wizard = None

        if wizard is not None:
            if wizard.get("discovery") is None:
                wizard["discovery"] = {
                    "entry_type": ENTRY_TYPE_HUB,
                    CONF_HUB_NAME: hub_name,
                    CONF_TOPIC_PREFIX: prefix,
                    CONF_AWS_TAG: tag,
                    CONF_BROKER_ENTRY_ID: broker_entry_id,
                }
                event = wizard.get("event")
                if event is not None:
                    event.set()
                _LOGGER.info(
                    "OverKill Smart Hub: discovered hub %s during initial setup",
                    prefix,
                )
                return
            if (
                wizard["discovery"].get(CONF_TOPIC_PREFIX) == prefix
                and mqtt_source_namespace(
                    wizard["discovery"].get(CONF_BROKER_ENTRY_ID, "")
                ) == namespace
            ):
                return
            # If several new hubs are retained on the broker, reserve only
            # the first for the onboarding confirmation. Let other prefixes
            # continue through the normal independent discovery flow.

        self._hub_creation_in_flight.add(prefix_key)
        try:
            _LOGGER.info(
                "OverKill Smart Hub: new hub detected (prefix=%s, tag=%s, broker=%s)",
                prefix, tag, broker_entry_id or "local",
            )
            await self.hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": "mqtt"},
                data={
                    "entry_type": ENTRY_TYPE_HUB,
                    CONF_HUB_NAME: hub_name,
                    CONF_TOPIC_PREFIX: prefix,
                    CONF_AWS_TAG: tag,
                    CONF_BROKER_ENTRY_ID: broker_entry_id,
                },
            )
        finally:
            self._hub_creation_in_flight.discard(prefix_key)

    # ------------------------------------------------ MQTT transport

    async def async_add_mqtt_transport(self, entry: ConfigEntry) -> None:
        if self._transport_entry_id is not None:
            _LOGGER.warning("MQTT transport already configured; ignoring extra")
            return
        self._transport_entry_id = entry.entry_id
        cfg = {**entry.data, **entry.options}
        mode = cfg.get(CONF_MQTT_MODE, "local")
        if mode in (MQTT_MODE_CLOUD, MQTT_MODE_BOTH):
            broker = CloudBrokerRuntime(self, entry)
            try:
                await broker.async_start()
            except Exception:  # noqa: BLE001
                _LOGGER.exception("OverKill: cloud broker failed to start")
                return
            self.cloud_broker = broker

            # Wake every waiting hub, not just those whose stored
            # broker_entry_id matches. Home Assistant can assign a new entry_id
            # when a transport is deleted and re-added, leaving hubs bound to
            # the old id waiting forever.
            to_resume: list[HubRuntime] = []
            for stored_id, waiting in list(self._hubs_waiting_for_broker.items()):
                for hub in waiting:
                    if stored_id != entry.entry_id:
                        _LOGGER.warning(
                            "OverKill hub '%s' was bound to broker %s which no "
                            "longer exists; rebinding to %s",
                            hub.name, stored_id, entry.entry_id,
                        )
                        hub.broker_entry_id = entry.entry_id
                        # Persist the new binding so a future restart is clean.
                        try:
                            new_data = dict(hub.entry.data)
                            new_data["broker_entry_id"] = entry.entry_id
                            self.hass.config_entries.async_update_entry(
                                hub.entry, data=new_data
                            )
                        except Exception:  # noqa: BLE001
                            _LOGGER.exception("failed to rebind hub entry")
                    to_resume.append(hub)
                self._hubs_waiting_for_broker.pop(stored_id, None)

            for hub in to_resume:
                try:
                    await hub.async_resume()
                except Exception:  # noqa: BLE001
                    _LOGGER.exception(
                        "OverKill: failed to resume hub '%s'", hub.name
                    )

    async def async_remove_mqtt_transport(self, entry: ConfigEntry) -> None:
        if self._transport_entry_id != entry.entry_id:
            return
        self._transport_entry_id = None
        await self.async_stop_local_listener()
        if self.cloud_broker is not None:
            await self.cloud_broker.async_stop()
            self.cloud_broker = None

        # Hubs bound to this transport lose their broker runtime. Tear down
        # their old callback registrations and queue them so a replacement
        # transport (which may receive a fresh entry_id) can resume them.
        for hub in list(self.hubs.values()):
            if hub.broker_entry_id != entry.entry_id:
                continue
            for unsub in hub._unsubs:
                try:
                    unsub()
                except Exception:  # noqa: BLE001
                    pass
            hub._unsubs.clear()
            hub._topic_listeners.clear()
            hub._subs_active = False
            self.register_hub_waiting_for_broker(hub)

    # ---------------------------------------------------- AWS bridges

    def mark_bridge_reloading(self, entry_id: str) -> None:
        """Remember that bridge unload is a reload, not permanent removal."""
        self._bridge_reloading.add(entry_id)

    def mark_hub_reloading(self, entry_id: str) -> None:
        """Keep this hub in its bridge's readiness barrier while it reloads."""
        self._hub_reloading.add(entry_id)

    async def async_add_bridge(self, entry: ConfigEntry) -> None:
        """Start an independent AWS IoT runtime for this bridge entry."""
        if entry.entry_id in self.bridges:
            return

        # Migrate hubs created by earlier single-bridge releases to the oldest
        # configured bridge. Explicitly local-only or already assigned hubs are
        # never changed.
        entries = self.configured_bridge_entries()
        primary_id = entries[0].entry_id if entries else entry.entry_id
        await self._async_migrate_legacy_bridge_assignments(primary_id)

        runtime = AwsBridgeRuntime(self, entry)
        self.bridges[entry.entry_id] = runtime
        try:
            await runtime.async_start()
        except Exception:
            self.bridges.pop(entry.entry_id, None)
            try:
                await runtime.async_stop()
            except Exception:  # noqa: BLE001
                _LOGGER.debug("Failed to stop AWS bridge after startup error", exc_info=True)
            raise
        _LOGGER.info(
            "OverKill: AWS bridge '%s' registered (entry_id=%s endpoint=%s)",
            entry.title, entry.entry_id,
            {**entry.data, **entry.options}.get("aws_endpoint", "unknown"),
        )

    async def async_remove_bridge(self, entry: ConfigEntry) -> None:
        runtime = self.bridges.get(entry.entry_id)
        if runtime is None:
            self._bridge_reloading.discard(entry.entry_id)
            return
        # On reload, async_start republishes the current catalogue. On removal,
        # retain the hub assignment as an orphaned ID rather than exposing those
        # devices through another bridge; the log directs the user to reassign.
        assigned = [
            hub for hub in self.hubs.values()
            if self.bridge_entry_id_for_hub(hub) == entry.entry_id
        ]
        if assigned:
            _LOGGER.warning(
                "OverKill: removing AWS bridge %s leaves %d hub(s) assigned to it. "
                "They will not be exposed through another bridge; reassign them "
                "in each hub's options if this removal is permanent.",
                entry.entry_id, len(assigned),
            )
        # Clear the retained list only for permanent removal. Config-entry
        # option edits mark the following unload as a reload so their current
        # catalogue does not momentarily disappear from Alexa.
        is_reload = entry.entry_id in self._bridge_reloading
        self._bridge_reloading.discard(entry.entry_id)
        if not is_reload:
            # Block queued callback tasks from republishing after we clear the
            # retained catalogue but before the network client is stopped.
            runtime._stopping = True
            await runtime.async_clear_catalog()
        await runtime.async_stop()
        if self.bridges.get(entry.entry_id) is runtime:
            self.bridges.pop(entry.entry_id, None)

    async def async_publish_catalogs(self, force: bool = False) -> None:
        """Refresh all independently scoped AWS catalogues."""
        for bridge in list(self.bridges.values()):
            try:
                await bridge.async_publish_catalog(force=force)
            except Exception:  # noqa: BLE001
                _LOGGER.exception(
                    "OverKill: failed refreshing catalogue for bridge %s",
                    bridge.entry_id,
                )

    # --------------------------------------------- Coordinator recovery

    async def async_stop(self) -> None:
        """Tear down coordinator runtimes and forget all in-memory state."""
        self._stopped = True
        await self.async_stop_local_listener()

        if self.cloud_broker is not None:
            try:
                await self.cloud_broker.async_stop()
            except Exception:  # noqa: BLE001
                _LOGGER.debug("Cloud broker stop failed during coordinator stop", exc_info=True)
            self.cloud_broker = None

        for bridge in list(self.bridges.values()):
            try:
                await bridge.async_stop()
            except Exception:  # noqa: BLE001
                _LOGGER.debug("AWS bridge stop failed during coordinator stop", exc_info=True)
        self.bridges.clear()

        for hub in list(self.hubs.values()):
            try:
                await hub.async_stop()
            except Exception:  # noqa: BLE001
                _LOGGER.debug("Hub stop failed during coordinator stop", exc_info=True)

        self.hubs.clear()
        self.by_prefix.clear()
        self.by_tag.clear()
        self._entities.clear()
        self._entity_add_callbacks.clear()
        self._pending_entities.clear()
        self._hub_creation_in_flight.clear()
        self._hubs_removing.clear()
        self._hubs_failed_load.clear()
        self._hub_reloading.clear()
        self._bridge_reloading.clear()
        self._hubs_waiting_for_broker.clear()
        self._transport_entry_id = None

    async def async_rediscover(self) -> None:
        """Re-subscribe to retained MQTT discovery topics and recover missed hubs."""
        current = self.hass.data.get(DOMAIN, {}).get(DATA_COORDINATOR)
        if self._stopped or current is not self:
            _LOGGER.debug("OverKill: ignoring rediscovery from an inactive coordinator")
            return
        await self.async_stop_local_listener()

        # Reconnect the cloud client so retained sync messages replay. Reset
        # the entry guard before rebuilding the configured MQTT transport.
        if self.cloud_broker is not None:
            try:
                await self.cloud_broker.async_stop()
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Cloud broker stop failed during rediscovery")
            self.cloud_broker = None
        self._transport_entry_id = None

        for entry in self.hass.config_entries.async_entries(DOMAIN):
            if entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_MQTT_TRANSPORT:
                try:
                    await self.async_add_mqtt_transport(entry)
                except Exception:  # noqa: BLE001
                    _LOGGER.exception("Failed to restore MQTT transport during rediscovery")
                break

        await self.async_ensure_local_listener()

        # Existing hubs also need their subscriptions rebound to the new
        # broker runtime. async_resume restores entity-owned listeners too.
        for hub in list(self.hubs.values()):
            try:
                for unsub in hub._unsubs:
                    try:
                        unsub()
                    except Exception:  # noqa: BLE001
                        _LOGGER.debug(
                            "Hub unsubscribe failed during rediscovery", exc_info=True
                        )
                hub._unsubs.clear()
                hub._topic_listeners.clear()
                hub._subs_active = False
                await hub.async_start()
            except Exception:  # noqa: BLE001
                _LOGGER.exception(
                    "OverKill: failed to re-subscribe hub '%s' after rediscover",
                    hub.name,
                )

        _LOGGER.info("OverKill Smart Hub: discovery resubscribed")

    async def async_hub_catalog_changed(self) -> None:
        await self.async_publish_catalogs()
