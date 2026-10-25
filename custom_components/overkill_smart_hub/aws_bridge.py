"""AWS IoT TLS bridge: directive routing + aggregated catalogue."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import ssl
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_AWS_CA_PEM,
    CONF_AWS_CATALOG_TOPIC,
    CONF_AWS_CERT_PEM,
    CONF_AWS_CLIENT_ID,
    CONF_AWS_CMD_TOPIC,
    CONF_AWS_ENDPOINT,
    CONF_AWS_KEY_PEM,
    CONF_AWS_PORT,
    DEFAULT_AWS_CATALOG_TOPIC,
    DEFAULT_AWS_CMD_TOPIC,
    DEFAULT_AWS_PORT,
)

_LOGGER = logging.getLogger(__name__)

_DIRECTIVE_RE = re.compile(r"^alexa/([0-9A-Fa-f]{4})/directive$")

# paho API version shim
try:
    from paho.mqtt.enums import CallbackAPIVersion
    import paho.mqtt.client as mqtt

    def _make_client(client_id: str):
        return mqtt.Client(
            callback_api_version=CallbackAPIVersion.VERSION1,
            client_id=client_id,
            protocol=mqtt.MQTTv311,
        )
except Exception:
    import paho.mqtt.client as mqtt  # type: ignore

    def _make_client(client_id: str):
        return mqtt.Client(client_id=client_id, protocol=mqtt.MQTTv311)


class AwsBridgeRuntime:
    """Independent AWS IoT client for one Alexa account/catalogue."""

    def __init__(self, coord, entry: ConfigEntry) -> None:
        self.coord = coord
        self.hass: HomeAssistant = coord.hass
        self.entry = entry
        self.entry_id = entry.entry_id
        self.cfg: dict[str, Any] = {**entry.data, **entry.options}

        self._client = None
        self._connected = False
        self._stopping = False
        self._last_catalog: str | None = None
        self._last_catalog_wait: tuple[str, ...] | None = None
        # Serialize snapshot calculation and publication so a slower older
        # publish cannot overwrite a newer catalogue after rapid MQTT updates.
        self._catalog_lock = asyncio.Lock()
        self._client_id: str | None = None
        self._command_topic: str = str(
            self.cfg.get(CONF_AWS_CMD_TOPIC, DEFAULT_AWS_CMD_TOPIC)
        )

    @property
    def aws_connected(self) -> bool:
        return self._connected

    # ---------------------------------------------------------------- Life

    async def async_start(self) -> None:
        self._stopping = False
        endpoint = self.cfg[CONF_AWS_ENDPOINT]
        port = int(self.cfg.get(CONF_AWS_PORT, DEFAULT_AWS_PORT))
        client_id = (
            self.cfg.get(CONF_AWS_CLIENT_ID)
            or f"ha-overkill-bridge-{self.entry_id[:8]}"
        )
        ca = self.cfg[CONF_AWS_CA_PEM]
        cert = self.cfg[CONF_AWS_CERT_PEM]
        key = self.cfg[CONF_AWS_KEY_PEM]
        cmd_topic = self._command_topic
        self._client_id = client_id
        _LOGGER.info(
            "OverKill AWS bridge: starting AWS IoT MQTT client endpoint=%s:%s "
            "client_id=%s command_subscription=%s",
            endpoint, port, client_id, cmd_topic,
        )

        def _on_connect(client, userdata, flags, rc):
            if rc == 0:
                self._connected = True
                _LOGGER.warning(
                    "OverKill AWS bridge: CONNECTED to AWS IoT endpoint=%s:%s "
                    "client_id=%s; requesting subscription to %s",
                    endpoint, port, client_id, cmd_topic,
                )
                try:
                    result, mid = client.subscribe(cmd_topic, qos=1)
                    if result != mqtt.MQTT_ERR_SUCCESS:
                        _LOGGER.error(
                            "OverKill AWS bridge: command subscription request %s "
                            "failed locally rc=%s",
                            cmd_topic, result,
                        )
                    else:
                        _LOGGER.info(
                            "OverKill AWS bridge: subscription request queued "
                            "topic=%s mid=%s; awaiting broker SUBACK",
                            cmd_topic, mid,
                        )
                except Exception:  # noqa: BLE001
                    _LOGGER.exception("OverKill AWS bridge: directive subscription failed")
                self.hass.add_job(self.async_publish_catalog(force=True))
            else:
                _LOGGER.error(
                    "OverKill AWS bridge: AWS IoT rejected connection endpoint=%s:%s "
                    "client_id=%s rc=%s",
                    endpoint, port, client_id, rc,
                )
                self._connected = False

        def _on_connect_fail(client, userdata):
            # Paho calls this when the TCP/TLS connection attempt fails before
            # a CONNACK is received. Without it, a bad endpoint, certificate,
            # client policy, or network failure can look like a silent bridge.
            self._connected = False
            _LOGGER.error(
                "OverKill AWS bridge: TCP/TLS connection attempt failed "
                "endpoint=%s:%s client_id=%s. Check endpoint, CA/certificate/key, "
                "outbound access to port 8883, and AWS IoT iot:Connect policy.",
                endpoint, port, client_id,
            )

        def _on_subscribe(client, userdata, mid, granted_qos):
            grants = list(granted_qos or [])
            if not grants or any(int(qos) >= 128 for qos in grants):
                _LOGGER.error(
                    "OverKill AWS bridge: AWS IoT denied/failed SUBACK for "
                    "topic=%s mid=%s granted_qos=%s; check iot:Subscribe and "
                    "iot:Receive permissions for this client ID.",
                    cmd_topic, mid, grants,
                )
                return
            _LOGGER.warning(
                "OverKill AWS bridge: AWS IoT SUBACK accepted topic=%s "
                "granted_qos=%s; waiting for Alexa directives",
                cmd_topic, grants,
            )

        def _on_disconnect(client, userdata, rc):
            if rc != 0:
                _LOGGER.warning("OverKill AWS bridge: disconnected rc=%s", rc)
            self._connected = False

        def _on_message(client, userdata, msg):
            # This callback runs on Paho's network thread. Schedule routing on
            # Home Assistant's event loop and log receipt for field diagnosis.
            self.hass.add_job(
                self._async_route_directive, msg.topic, bytes(msg.payload)
            )

        def _build():
            client = _make_client(client_id)
            client.tls_set(
                ca_certs=ca,
                certfile=cert,
                keyfile=key,
                tls_version=ssl.PROTOCOL_TLS_CLIENT,
            )
            client.tls_insecure_set(False)
            client.reconnect_delay_set(min_delay=1, max_delay=60)
            client.on_connect = _on_connect
            client.on_connect_fail = _on_connect_fail
            client.on_subscribe = _on_subscribe
            client.on_disconnect = _on_disconnect
            client.on_message = _on_message
            return client

        client = await self.hass.async_add_executor_job(_build)
        self._client = client
        try:
            await self.hass.async_add_executor_job(
                lambda: (client.connect_async(endpoint, port, keepalive=60),
                         client.loop_start())
            )
        except Exception:
            self._connected = False
            _LOGGER.exception(
                "OverKill AWS bridge: could not start MQTT network loop "
                "for endpoint=%s:%s client_id=%s",
                endpoint, port, client_id,
            )
            raise

    async def async_stop(self) -> None:
        self._stopping = True
        client = self._client
        self._client = None
        self._connected = False
        if client is not None:
            try:
                client.loop_stop()
            except Exception:  # noqa: BLE001
                pass
            try:
                client.disconnect()
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------ Routing

    async def _async_route_directive(self, topic: str, payload: bytes) -> None:
        if self._stopping:
            _LOGGER.debug(
                "OverKill AWS bridge: ignoring directive during bridge shutdown topic=%s",
                topic,
            )
            return
        _LOGGER.info(
            "OverKill AWS bridge: received directive topic=%s payload_bytes=%d",
            topic, len(payload),
        )
        # Log the firmware command fields for diagnosis, but always forward the
        # exact original payload bytes so the ESP receives the same JSON as it
        # did through the Node-RED flow.
        try:
            parsed_payload = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            parsed_payload = None
        if isinstance(parsed_payload, dict):
            _LOGGER.info(
                "OverKill AWS bridge: directive fields id=%s dev=%s do=%s",
                parsed_payload.get("id"), parsed_payload.get("dev"),
                parsed_payload.get("do"),
            )

        m = _DIRECTIVE_RE.match(topic)
        if not m:
            _LOGGER.warning(
                "OverKill AWS bridge: received message on the configured command "
                "subscription, but topic %r does not match alexa/XXXX/directive",
                topic,
            )
            return
        tag = m.group(1).upper()
        hub = self.coord.hub_for_tag(tag, bridge_entry_id=self.entry_id)
        if hub is None:
            active = [
                f"{h.aws_tag}@{h.topic_prefix}[mqtt={h.broker_entry_id or 'local'},"
                f"bridge={self.coord.bridge_entry_id_for_hub(h) or 'none'}]"
                for h in self.coord.hubs.values()
                if not h._stopping
            ]
            _LOGGER.error(
                "OverKill AWS bridge: cannot route %s — no active hub matches "
                "tag %s. Active hubs: %s",
                topic, tag, ", ".join(active) if active else "none",
            )
            return

        transport = (
            f"cloud:{hub.broker_entry_id}"
            if hub.broker_entry_id else "local"
        )
        _LOGGER.info(
            "OverKill AWS bridge: route resolved tag=%s hub=%s prefix=%s "
            "broker=%s; publishing original topic=%s payload_bytes=%d retain=false",
            tag, hub.name, hub.topic_prefix,
            hub.broker_entry_id or "Home Assistant local MQTT",
            topic, len(payload),
        )
        published = await hub.async_publish(topic, payload, retain=False)
        if not published:
            _LOGGER.error(
                "OverKill AWS bridge: forwarding %s to hub '%s' failed",
                topic, hub.name,
            )
            return
        _LOGGER.info(
            "OverKill AWS bridge: publish call completed for %s via %s; "
            "check the local MQTT echo diagnostic to confirm broker receipt",
            topic, transport,
        )

    # --------------------------------------------------------- Catalogue

    async def async_clear_catalog(self) -> None:
        """Clear this bridge's retained catalogue before its entry is removed."""
        async with self._catalog_lock:
            topic = self.cfg.get(
                CONF_AWS_CATALOG_TOPIC, DEFAULT_AWS_CATALOG_TOPIC
            )
            client = self._client
            if client is None or not self._connected:
                _LOGGER.warning(
                    "OverKill AWS bridge: cannot clear retained catalogue topic=%s; "
                    "AWS IoT client is not connected",
                    topic,
                )
                return

            def _do_clear() -> tuple[int, int | None, bool]:
                info = client.publish(topic, "[]", qos=1, retain=True)
                if info.rc != mqtt.MQTT_ERR_SUCCESS:
                    return info.rc, getattr(info, "mid", None), False
                try:
                    info.wait_for_publish(timeout=5)
                    confirmed = bool(info.is_published())
                except Exception:  # noqa: BLE001
                    confirmed = False
                return info.rc, getattr(info, "mid", None), confirmed

            try:
                rc, mid, confirmed = await self.hass.async_add_executor_job(_do_clear)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("OverKill AWS bridge: failed clearing retained catalogue")
                return
            if rc != mqtt.MQTT_ERR_SUCCESS or not confirmed:
                _LOGGER.error(
                    "OverKill AWS bridge: retained catalogue clear not confirmed "
                    "topic=%s rc=%s mid=%s confirmed=%s",
                    topic, rc, mid, confirmed,
                )
                return
            self._last_catalog = "[]"
            _LOGGER.warning(
                "OverKill AWS bridge: cleared retained catalogue topic=%s mid=%s",
                topic, mid,
            )

    async def async_publish_catalog(self, force: bool = False) -> None:
        """Publish the full, complete multi-hub catalogue to AWS IoT.

        Match the Node-RED bridge semantics: do not publish a partial catalogue
        while any configured hub is still synchronizing. The retained AWS list
        is only replaced after every active hub is complete, or when there are
        no hubs (in which case publish [] to clear stale Alexa devices).
        """
        async with self._catalog_lock:
            if self._stopping:
                _LOGGER.debug(
                    "OverKill AWS bridge: skipping catalogue publish during shutdown "
                    "entry_id=%s", self.entry_id,
                )
                return
            configured_hub_ids = self.coord.configured_hub_entry_ids_for_bridge(
                self.entry_id
            )
            hubs = [
                hub for hub in self.coord.hubs.values()
                if not getattr(hub, "_stopping", False)
                and self.coord.bridge_entry_id_for_hub(hub) == self.entry_id
            ]
            loaded_hub_ids = {hub.entry.entry_id for hub in hubs}
            not_loaded_ids = configured_hub_ids - loaded_hub_ids
            if not_loaded_ids:
                _LOGGER.warning(
                    "OverKill AWS bridge: withholding catalogue publication to %s "
                    "while assigned hub runtime(s) are still loading: %s",
                    self.cfg.get(CONF_AWS_CATALOG_TOPIC, DEFAULT_AWS_CATALOG_TOPIC),
                    ", ".join(sorted(not_loaded_ids)),
                )
                return
            incomplete = [hub for hub in hubs if not hub.complete]
            if incomplete:
                details: list[str] = []
                for hub in incomplete:
                    sync = hub.sync if isinstance(hub.sync, dict) else {}
                    expected = sync.get("count", "?")
                    signature = sync.get("sig")
                    received = sum(
                        1 for device in hub.devices.values()
                        if isinstance(device, dict)
                        and signature is not None
                        and device.get("sig") == signature
                    )
                    details.append(
                        f"{hub.name}@{hub.topic_prefix} ({received}/{expected}; "
                        f"sig={signature or 'unknown'})"
                    )
                wait_signature = tuple(details)
                if wait_signature != self._last_catalog_wait:
                    _LOGGER.warning(
                        "OverKill AWS bridge: withholding catalogue publication "
                        "to %s until every hub is complete; waiting for: %s",
                        self.cfg.get(CONF_AWS_CATALOG_TOPIC, DEFAULT_AWS_CATALOG_TOPIC),
                        "; ".join(details),
                    )
                    self._last_catalog_wait = wait_signature
                return

            self._last_catalog_wait = None
            merged: list[dict[str, str]] = []
            for hub in hubs:
                merged.extend(
                    {"id": str(item["id"]), "name": str(item["name"]),
                     "type": str(item["type"])}
                    for item in hub.ok
                    if isinstance(item, dict)
                    and {"id", "name", "type"} <= item.keys()
                )

            # Node-RED uses localeCompare(..., { numeric: true }); use a
            # numeric-aware stable key so command IDs such as _2 sort before _10.
            def _natural_key(value: str) -> tuple:
                return tuple(
                    (1, int(part)) if part.isdigit() else (0, part.casefold())
                    for part in re.split(r"(\d+)", value)
                )

            merged.sort(key=lambda item: _natural_key(item["id"]))
            payload = json.dumps(merged, separators=(",", ":"))
            hub_summary = ", ".join(
                f"{hub.aws_tag}:{len(hub.ok)}" for hub in hubs
            ) or "none"
            _LOGGER.debug(
                "OverKill AWS bridge: assembled one combined Alexa catalogue "
                "from %d complete hub(s) [%s], total_devices=%d",
                len(hubs), hub_summary, len(merged),
            )

            if not force and payload == self._last_catalog:
                _LOGGER.debug(
                    "OverKill AWS bridge: complete catalogue unchanged (%d devices); "
                    "not republishing",
                    len(merged),
                )
                return

            topic = self.cfg.get(
                CONF_AWS_CATALOG_TOPIC, DEFAULT_AWS_CATALOG_TOPIC
            )
            client = self._client
            if client is None or not self._connected:
                _LOGGER.warning(
                    "OverKill AWS bridge: cannot publish complete catalogue to %s — "
                    "AWS IoT client is not connected; will retry on reconnect or "
                    "the next catalogue update",
                    topic,
                )
                return

            def _do_publish() -> tuple[int, int | None, bool]:
                info = client.publish(topic, payload, qos=1, retain=True)
                if info.rc != mqtt.MQTT_ERR_SUCCESS:
                    return info.rc, getattr(info, "mid", None), False
                try:
                    info.wait_for_publish(timeout=5)
                    confirmed = bool(info.is_published())
                except Exception:  # noqa: BLE001
                    _LOGGER.debug(
                        "OverKill AWS bridge: publish acknowledgement wait failed",
                        exc_info=True,
                    )
                    confirmed = False
                return info.rc, getattr(info, "mid", None), confirmed

            try:
                rc, mid, confirmed = await self.hass.async_add_executor_job(
                    _do_publish
                )
            except Exception:  # noqa: BLE001
                _LOGGER.exception(
                    "OverKill AWS bridge: failed to publish catalogue to %s",
                    topic,
                )
                return

            if rc != mqtt.MQTT_ERR_SUCCESS or not confirmed:
                _LOGGER.error(
                    "OverKill AWS bridge: catalogue publish not confirmed "
                    "topic=%s rc=%s mid=%s confirmed=%s devices=%d retain=true; "
                    "the old retained catalogue may still be active",
                    topic, rc, mid, confirmed, len(merged),
                )
                return

            # Record only a broker-confirmed publish, so a later event can retry
            # if this attempt failed or timed out.
            self._last_catalog = payload
            _LOGGER.warning(
                "OverKill AWS bridge: COMPLETE combined catalogue published and "
                "acknowledged by AWS IoT topic=%s hubs=%d devices=%d hub_catalogues=[%s] "
                "qos=1 retain=true mid=%s",
                topic, len(hubs), len(merged), hub_summary, mid,
            )
            _LOGGER.debug("OverKill AWS bridge: catalogue payload=%s", payload)
