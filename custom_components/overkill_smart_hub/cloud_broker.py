"""Cloud MQTT broker runtime (plain MQTT — ESP has no TLS)."""
from __future__ import annotations

import logging
from typing import Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_BROKER_CLIENT_ID,
    CONF_BROKER_HOST,
    CONF_BROKER_PASSWORD,
    CONF_BROKER_PORT,
    CONF_BROKER_USERNAME,
    DEFAULT_BROKER_PORT,
)

_LOGGER = logging.getLogger(__name__)

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


class CloudBrokerRuntime:
    """One paho client, shared by every hub that lives on this broker."""

    def __init__(self, coord, entry: ConfigEntry) -> None:
        self.coord = coord
        self.hass: HomeAssistant = coord.hass
        self.entry = entry
        self.entry_id = entry.entry_id
        self.cfg = {**entry.data, **entry.options}

        self._client = None
        self._subs: dict[str, list[Callable]] = {}
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    async def async_start(self) -> None:
        host = (self.cfg.get(CONF_BROKER_HOST) or "").strip()
        if not host:
            _LOGGER.error(
                "OverKill cloud MQTT: no broker host configured. "
                "Open the MQTT Transport entry and add the broker details."
            )
            return
        port = int(self.cfg.get(CONF_BROKER_PORT, DEFAULT_BROKER_PORT))
        client_id = (
            self.cfg.get(CONF_BROKER_CLIENT_ID)
            or f"ha-overkill-{self.entry_id[:8]}"
        )
        user = self.cfg.get(CONF_BROKER_USERNAME) or None
        pw = self.cfg.get(CONF_BROKER_PASSWORD) or None

        def _on_connect(client, userdata, flags, rc):
            if rc == 0:
                _LOGGER.info("OverKill cloud MQTT: connected to %s:%s", host, port)
                self._connected = True
                for topic in list(self._subs.keys()):
                    try:
                        client.subscribe(topic, qos=1)
                    except Exception:
                        _LOGGER.exception("subscribe %s failed", topic)
            else:
                _LOGGER.error("OverKill cloud MQTT: connect failed rc=%s", rc)
                self._connected = False

        def _on_disconnect(client, userdata, rc):
            if rc != 0:
                _LOGGER.warning("OverKill cloud MQTT: disconnected rc=%s", rc)
            self._connected = False

        def _on_message(client, userdata, msg):
            for topic, callbacks in list(self._subs.items()):
                if mqtt.topic_matches_sub(topic, msg.topic):
                    for cb in callbacks:
                        try:
                            self.hass.loop.call_soon_threadsafe(cb, msg)
                        except Exception:
                            _LOGGER.exception("dispatch failed")

        def _build():
            client = _make_client(client_id)
            if user is not None:
                client.username_pw_set(user, pw)
            client.reconnect_delay_set(min_delay=1, max_delay=60)
            client.on_connect = _on_connect
            client.on_disconnect = _on_disconnect
            client.on_message = _on_message
            return client

        client = await self.hass.async_add_executor_job(_build)
        self._client = client
        await self.hass.async_add_executor_job(
            lambda: (client.connect_async(host, port, keepalive=60),
                     client.loop_start())
        )
        # MQTT '+' is a single-level wildcard; cover common prefix depths.
        for pattern in (
            "+/_system/alexa/sync",
            "+/+/_system/alexa/sync",
            "+/+/+/_system/alexa/sync",
        ):
            await self.async_subscribe(pattern, self._on_sync)

    async def async_stop(self) -> None:
        client = self._client
        self._client = None
        self._connected = False
        if client is not None:
            try:
                client.loop_stop()
            except Exception:
                pass
            try:
                client.disconnect()
            except Exception:
                pass
        # No subscribers survive this runtime. The coordinator will rebuild
        # subscriptions against a newly-created runtime when needed.
        self._subs.clear()

    async def async_subscribe(self, topic: str, cb: Callable) -> Callable[[], None]:
        """Subscribe and return a function that removes this callback.

        The network subscription remains cached/reusable; this removes only
        the caller's callback and prevents old hub runtimes from being held.
        """
        self._subs.setdefault(topic, []).append(cb)
        if self._client is not None and self._connected:
            await self.hass.async_add_executor_job(
                lambda: self._client.subscribe(topic, qos=1)
            )

        def _unsub() -> None:
            listeners = self._subs.get(topic)
            if not listeners:
                return
            try:
                listeners.remove(cb)
            except ValueError:
                return
            if not listeners:
                self._subs.pop(topic, None)

        return _unsub

    async def async_publish(self, topic: str, payload, retain: bool = False) -> bool:
        """Publish on the cloud broker and report whether Paho accepted it."""
        client = self._client
        if client is None or not self._connected:
            _LOGGER.error(
                "OverKill cloud MQTT: cannot publish to %s — broker is not connected",
                topic,
            )
            return False

        def _publish():
            info = client.publish(topic, payload, qos=1, retain=retain)
            return info.rc

        rc = await self.hass.async_add_executor_job(_publish)
        if rc != mqtt.MQTT_ERR_SUCCESS:
            _LOGGER.error(
                "OverKill cloud MQTT: publish to %s rejected with rc=%s",
                topic, rc,
            )
            return False
        return True

    def _on_sync(self, msg) -> None:
        self.hass.async_create_task(
            self.coord._async_hub_seen(msg.topic, self.entry_id)
        )
