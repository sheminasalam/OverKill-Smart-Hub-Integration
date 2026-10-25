"""Onboarding: choose local MQTT or optional AWS bridge, then configure transport."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import shutil
from pathlib import Path

import voluptuous as vol
from homeassistant.components.file_upload import process_uploaded_file
from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.selector import (
    FileSelector, FileSelectorConfig, SelectSelector, SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    CONF_AWS_TAG,
    CONF_BRIDGE_ENTRY_ID,
    CONF_AWS_CA_PEM,
    CONF_AWS_CATALOG_TOPIC,
    CONF_AWS_CERT_PEM,
    CONF_AWS_CLIENT_ID,
    CONF_AWS_CMD_TOPIC,
    CONF_AWS_ENDPOINT,
    CONF_AWS_KEY_PEM,
    CONF_AWS_PORT,
    CONF_BROKER_CLIENT_ID,
    CONF_BROKER_ENTRY_ID,
    CONF_BROKER_HOST,
    CONF_BROKER_PASSWORD,
    CONF_BROKER_PORT,
    CONF_BROKER_USERNAME,
    CONF_ENTRY_TYPE,
    CONF_HUB_NAME,
    CONF_MQTT_MODE,
    CONF_TOPIC_PREFIX,
    DEFAULT_AWS_CATALOG_TOPIC,
    DEFAULT_AWS_CMD_TOPIC,
    DEFAULT_AWS_PORT,
    DEFAULT_BROKER_PORT,
    DATA_COORDINATOR,
    DOMAIN,
    ENTRY_TYPE_AWS_BRIDGE,
    ENTRY_TYPE_HUB,
    ENTRY_TYPE_MQTT_TRANSPORT,
    MQTT_MODE_BOTH,
    MQTT_MODE_CLOUD,
    MQTT_MODE_LOCAL,
    hub_config_unique_id,
)

_LOGGER = logging.getLogger(__name__)


def _file_picker() -> FileSelector:
    try:
        return FileSelector(FileSelectorConfig(accept=".pem,.crt,.key,.txt"))
    except TypeError:
        return FileSelector()


def _bridge_cert_filename(endpoint: str, base_name: str, scope: str) -> str:
    """Give each bridge setup/entry its own certificate files on disk."""
    key = f"{_normalize_endpoint(endpoint)}::{scope}"
    suffix = hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]
    return f"{suffix}_{base_name}"


def _bridge_entries(hass) -> list[ConfigEntry]:
    return [
        entry for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_AWS_BRIDGE
    ]


def _transport_entries(hass) -> list[ConfigEntry]:
    """Return configured MQTT transport entries."""
    return [
        entry for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_MQTT_TRANSPORT
    ]


def _normalize_endpoint(value) -> str:
    """Normalize the AWS IoT DNS endpoint for comparisons and unique IDs."""
    return str(value or "").strip().lower().rstrip(".")


def _bridge_endpoint(entry: ConfigEntry) -> str:
    cfg = {**entry.data, **entry.options}
    return _normalize_endpoint(cfg.get(CONF_AWS_ENDPOINT, ""))


def _bridge_endpoint_in_use(
    hass, endpoint: str, exclude_entry_id: str | None = None
) -> bool:
    """Avoid duplicate endpoints and stale config-entry unique-ID collisions."""
    endpoint = _normalize_endpoint(endpoint)
    for entry in _bridge_entries(hass):
        if entry.entry_id == exclude_entry_id:
            continue
        if _bridge_endpoint(entry) == endpoint or entry.unique_id == f"aws:{endpoint}":
            return True
    return False


def _primary_bridge_id(hass) -> str:
    entries = _bridge_entries(hass)
    return entries[0].entry_id if entries else ""


def _default_bridge_selection(hass) -> str:
    """Auto-select only when one account exists; otherwise require a choice."""
    entries = _bridge_entries(hass)
    return entries[0].entry_id if len(entries) == 1 else ""


def _bridge_selector_options(hass, current_id: str | None = None) -> list[dict[str, str]]:
    entries = _bridge_entries(hass)
    options = [{"label": "None (no Alexa bridge)", "value": ""}]
    ids = set()
    for entry in entries:
        endpoint = _bridge_endpoint(entry)
        label = entry.title or (f"Alexa bridge ({endpoint})" if endpoint else "Alexa bridge")
        options.append({"label": label, "value": entry.entry_id})
        ids.add(entry.entry_id)
    # Keep a removed bridge visible until the user deliberately reassigns the
    # hub; never silently move it to another household's Alexa account.
    if current_id and current_id not in ids:
        options.append({"label": f"Unavailable bridge ({current_id})", "value": current_id})
    return options


def _bridge_selector(hass, current_id: str | None = None) -> SelectSelector:
    return SelectSelector(SelectSelectorConfig(
        options=_bridge_selector_options(hass, current_id),
        mode=SelectSelectorMode.DROPDOWN,
    ))


def _cert_dir(hass) -> Path:
    d = Path(hass.config.path("overkill_smart_hub"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _resolve_and_validate(hass, uploaded_value, marker, label, dest_name):
    if not uploaded_value:
        raise ValueError(f"{label}: no file selected")
    dest = _cert_dir(hass) / dest_name
    with process_uploaded_file(hass, uploaded_value) as src:
        if not src.is_file():
            raise ValueError(f"{label}: file not found")
        size = src.stat().st_size
        if size < 50:
            raise ValueError(f"{label}: file looks empty ({size} bytes)")
        text = src.read_text(errors="replace")
        if marker not in text:
            raise ValueError(f"{label}: not a valid PEM file ({marker} missing)")
        shutil.copy2(src, dest)
        return str(dest)


class OverkillSmartHubConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    # --------------------------------------------------- entry point

    async def async_step_user(self, user_input=None) -> FlowResult:
        if not _bridge_entries(self.hass) and not _transport_entries(self.hass):
            return await self.async_step_initial_setup()
        return await self.async_step_existing_setup()

    async def async_step_initial_setup(self, user_input=None) -> FlowResult:
        """Let local-only users start without AWS certificates or an Alexa skill."""
        if user_input is not None:
            if user_input.get("setup_path") == "aws_bridge":
                return await self.async_step_aws_bridge()
            # A local/cloud MQTT-only install is valid without an Alexa bridge.
            # Later, users can add a bridge through the existing setup menu.
            return await self.async_step_mqtt_transport()

        return self.async_show_form(
            step_id="initial_setup",
            data_schema=vol.Schema({
                vol.Required("setup_path", default="mqtt_only"): SelectSelector(
                    SelectSelectorConfig(
                        options=["mqtt_only", "aws_bridge"],
                        translation_key="setup_path",
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
            }),
        )

    async def async_step_existing_setup(self, user_input=None) -> FlowResult:
        """Offer transport settings or adding another independent AWS bridge."""
        if user_input is not None:
            action = user_input.get("setup_action", "transport")
            if action == "add_bridge":
                self._adding_extra_bridge = True
                return await self.async_step_aws_bridge()
            return await self.async_step_mqtt_transport()
        return self.async_show_form(
            step_id="existing_setup",
            data_schema=vol.Schema({
                vol.Required("setup_action", default="transport"): SelectSelector(
                    SelectSelectorConfig(
                        options=["transport", "add_bridge"],
                        translation_key="setup_action",
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
            }),
        )

    # --------------------------------------------------- AWS bridge

    async def async_step_aws_bridge(self, user_input=None) -> FlowResult:
        """Configure the Alexa/AWS bridge and copy its certificates safely."""
        errors: dict[str, str] = {}

        if user_input is not None:
            endpoint = _normalize_endpoint(user_input.get(CONF_AWS_ENDPOINT, ""))
            if not endpoint:
                errors[CONF_AWS_ENDPOINT] = "required"
            elif _bridge_endpoint_in_use(self.hass, endpoint):
                # Check before copying any uploaded files: certificate filenames
                # are endpoint-scoped, so copying first could overwrite the files
                # used by an already-configured bridge before aborting.
                _LOGGER.warning(
                    "OverKill setup: AWS endpoint %s is already assigned to a bridge. "
                    "Use a separate AWS IoT account/endpoint for an isolated Alexa account.",
                    endpoint,
                )
                return self.async_abort(reason="already_configured")

            uploaded_files = (
                (
                    CONF_AWS_CA_PEM, "BEGIN CERTIFICATE",
                    "Amazon Root CA certificate", "AmazonRootCA1.pem", "invalid_ca_file",
                ),
                (
                    CONF_AWS_CERT_PEM, "BEGIN CERTIFICATE",
                    "AWS IoT device certificate", "device.pem.crt", "invalid_device_cert",
                ),
                (
                    CONF_AWS_KEY_PEM, "PRIVATE KEY",
                    "AWS IoT device private key", "device.pem.key", "invalid_private_key",
                ),
            )
            resolved: dict[str, str] = {}
            if endpoint:
                for field, marker, label, base_name, error_key in uploaded_files:
                    try:
                        dest_name = _bridge_cert_filename(endpoint, base_name, self.flow_id)
                        resolved[field] = await self.hass.async_add_executor_job(
                            _resolve_and_validate,
                            self.hass,
                            user_input.get(field),
                            marker,
                            label,
                            dest_name,
                        )
                    except Exception as err:  # noqa: BLE001
                        _LOGGER.warning("AWS file validation failed for %s: %s", field, err)
                        errors[field] = error_key

            if not errors:
                # The bridge entry is created by an internal child flow at
                # the end of the onboarding wizard. Do not reserve its unique
                # ID on this parent flow: this flow must remain alive while
                # the user selects a transport and confirms a discovered hub.
                self._pending_bridge_data = {
                    CONF_ENTRY_TYPE: ENTRY_TYPE_AWS_BRIDGE,
                    CONF_AWS_ENDPOINT: endpoint,
                    CONF_AWS_PORT: DEFAULT_AWS_PORT,
                    CONF_AWS_CLIENT_ID: str(user_input.get(CONF_AWS_CLIENT_ID, "")).strip(),
                    CONF_AWS_CA_PEM: resolved[CONF_AWS_CA_PEM],
                    CONF_AWS_CERT_PEM: resolved[CONF_AWS_CERT_PEM],
                    CONF_AWS_KEY_PEM: resolved[CONF_AWS_KEY_PEM],
                    CONF_AWS_CMD_TOPIC: DEFAULT_AWS_CMD_TOPIC,
                    CONF_AWS_CATALOG_TOPIC: DEFAULT_AWS_CATALOG_TOPIC,
                }
                # The first bridge configures transport in the same wizard.
                # Additional bridge entries reuse the existing MQTT transport
                # and must not create or modify that transport.
                if getattr(self, "_adding_extra_bridge", False):
                    return await self._async_finish_additional_bridge_setup(
                        self._pending_bridge_data
                    )
                return await self.async_step_mqtt_transport()

        schema = vol.Schema({
            vol.Required(CONF_AWS_ENDPOINT): str,
            vol.Optional(CONF_AWS_CLIENT_ID, default=""): str,
            vol.Required(CONF_AWS_CA_PEM): _file_picker(),
            vol.Required(CONF_AWS_CERT_PEM): _file_picker(),
            vol.Required(CONF_AWS_KEY_PEM): _file_picker(),
        })
        return self.async_show_form(
            step_id="aws_bridge", data_schema=schema, errors=errors,
        )

    async def _async_finish_additional_bridge_setup(
        self, bridge_data: dict
    ) -> FlowResult:
        """Create an extra AWS bridge without changing the shared MQTT transport."""
        result = await self.hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "bridge_apply"},
            data=bridge_data,
        )
        if result.get("type") != "create_entry":
            _LOGGER.error(
                "OverKill setup: additional bridge creation failed: %s",
                result.get("reason", result.get("type")),
            )
            return self.async_abort(reason="setup_failed")

        endpoint = _normalize_endpoint(bridge_data.get(CONF_AWS_ENDPOINT, ""))
        bridge_entry = None
        for _ in range(100):
            bridge_entry = next(
                (entry for entry in _bridge_entries(self.hass)
                 if _bridge_endpoint(entry) == endpoint),
                None,
            )
            if bridge_entry is not None:
                break
            await asyncio.sleep(0.05)
        if bridge_entry is None:
            _LOGGER.error("OverKill setup: additional AWS bridge was not committed")
            return self.async_abort(reason="setup_failed")

        self._new_bridge_entry_id = bridge_entry.entry_id
        if any(
            entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_HUB
            for entry in self.hass.config_entries.async_entries(DOMAIN)
        ):
            return await self.async_step_assign_existing_hub()
        return self.async_abort(reason="bridge_added")

    async def async_step_assign_existing_hub(self, user_input=None) -> FlowResult:
        """Optionally move an already configured hub to the newly added bridge."""
        bridge_id = getattr(self, "_new_bridge_entry_id", "")
        hub_entries = [
            entry for entry in self.hass.config_entries.async_entries(DOMAIN)
            if entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_HUB
        ]
        if user_input is not None:
            hub_entry_id = str(user_input.get("hub_entry_id", "") or "")
            if not hub_entry_id:
                return self.async_abort(reason="bridge_added")
            hub_entry = next(
                (entry for entry in hub_entries if entry.entry_id == hub_entry_id),
                None,
            )
            if hub_entry is None:
                return self.async_abort(reason="no_data")

            new_data = dict(hub_entry.data)
            new_data[CONF_BRIDGE_ENTRY_ID] = bridge_id
            new_options = dict(hub_entry.options)
            new_options.pop(CONF_BRIDGE_ENTRY_ID, None)

            # Update the active runtime immediately as well as persistent entry
            # data. Home Assistant will reload this hub using the new assignment.
            coordinator = self.hass.data.get(DOMAIN, {}).get(DATA_COORDINATOR)
            runtime = getattr(coordinator, "hubs", {}).get(hub_entry.entry_id)
            if runtime is not None:
                runtime.bridge_entry_id = bridge_id
                runtime.bridge_assignment_explicit = True
                runtime.cfg[CONF_BRIDGE_ENTRY_ID] = bridge_id
            self.hass.config_entries.async_update_entry(
                hub_entry, data=new_data, options=new_options
            )
            if coordinator is not None:
                self.hass.async_create_task(coordinator.async_hub_catalog_changed())
            return self.async_abort(reason="bridge_hub_assigned")

        options = [{"label": "Finish without assigning an existing hub", "value": ""}]
        options.extend(
            {
                "label": f"{entry.title} — {entry.data.get(CONF_TOPIC_PREFIX, 'unknown prefix')}",
                "value": entry.entry_id,
            }
            for entry in hub_entries
        )
        return self.async_show_form(
            step_id="assign_existing_hub",
            data_schema=vol.Schema({
                vol.Required("hub_entry_id", default=""): SelectSelector(
                    SelectSelectorConfig(
                        options=options,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
            }),
            description_placeholders={
                "bridge_name": next(
                    (entry.title for entry in _bridge_entries(self.hass)
                     if entry.entry_id == bridge_id),
                    bridge_id,
                ),
            },
        )

    async def async_step_bridge_apply(self, bridge_data=None) -> FlowResult:
        """Internal step used to create the bridge without ending onboarding."""
        if not isinstance(bridge_data, dict):
            return self.async_abort(reason="no_data")
        endpoint = _normalize_endpoint(bridge_data.get(CONF_AWS_ENDPOINT, ""))
        if not endpoint:
            return self.async_abort(reason="required")
        if _bridge_endpoint_in_use(self.hass, endpoint):
            return self.async_abort(reason="already_configured")
        await self.async_set_unique_id(f"aws:{endpoint}")
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=f"OverKill AWS Bridge ({endpoint})",
            data=bridge_data,
        )

    async def async_step_chain(self, user_input=None) -> FlowResult:
        """Compatibility path for an already-created bridge entry."""
        return await self.async_step_mqtt_transport()

    # ------------------------------------------- MQTT transport

    async def async_step_mqtt_transport(self, user_input=None) -> FlowResult:
        """Choose the transport mode in one clear form."""
        if user_input is not None:
            mode = user_input[CONF_MQTT_MODE]
            if mode == MQTT_MODE_LOCAL:
                # Local MQTT uses Home Assistant's configured MQTT integration;
                # there are no extra broker fields to enter.
                if getattr(self, "_pending_bridge_data", None):
                    return await self._async_finish_initial_setup(mode, None)
                return await self._async_save_transport(mode, None)
            if mode == MQTT_MODE_CLOUD:
                return await self.async_step_mqtt_cloud()
            if mode == MQTT_MODE_BOTH:
                return await self.async_step_mqtt_both()

        schema = vol.Schema({
            vol.Required(CONF_MQTT_MODE, default=MQTT_MODE_LOCAL): SelectSelector(
                SelectSelectorConfig(
                    options=[
                        MQTT_MODE_LOCAL,
                        MQTT_MODE_CLOUD,
                        MQTT_MODE_BOTH,
                    ],
                    translation_key="mqtt_mode",
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
        })
        return self.async_show_form(
            step_id="mqtt_transport", data_schema=schema,
        )

    async def async_step_mqtt_local(self, user_input=None) -> FlowResult:
        return await self._async_save_transport(MQTT_MODE_LOCAL, None)

    async def async_step_mqtt_cloud(self, user_input=None) -> FlowResult:
        return await self._broker_form(user_input, "mqtt_cloud", MQTT_MODE_CLOUD)

    async def async_step_mqtt_both(self, user_input=None) -> FlowResult:
        return await self._broker_form(user_input, "mqtt_both", MQTT_MODE_BOTH)

    async def _async_finish_initial_setup(self, mode, broker_data) -> FlowResult:
        """Create bridge/transport entries, then discover and confirm a hub.

        HA creates one config entry per flow. Internal child flows commit the
        bridge and transport while this user-facing flow remains open for the
        final hub-discovery/confirmation page.
        """
        bridge_data = getattr(self, "_pending_bridge_data", None)
        if not bridge_data:
            return await self._async_save_transport(mode, broker_data)

        transport_data = {
            CONF_ENTRY_TYPE: ENTRY_TYPE_MQTT_TRANSPORT,
            CONF_MQTT_MODE: mode,
        }
        if broker_data:
            transport_data.update(broker_data)

        domain_data = self.hass.data.setdefault(DOMAIN, {})
        wizard = {
            "flow_id": self.flow_id,
            "event": asyncio.Event(),
            "discovery": None,
        }
        # Register before starting MQTT transport: retained sync messages can
        # arrive immediately when its discovery subscriptions are created.
        domain_data["_initial_setup_wizard"] = wizard

        try:
            bridge_result = await self.hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": "bridge_apply"},
                data=bridge_data,
            )
            if bridge_result.get("type") != "create_entry":
                _LOGGER.error(
                    "OverKill setup: bridge creation did not complete: %s",
                    bridge_result.get("reason", bridge_result.get("type")),
                )
                domain_data.pop("_initial_setup_wizard", None)
                return self.async_abort(reason="setup_failed")

            endpoint = bridge_data[CONF_AWS_ENDPOINT]
            bridge_exists = False
            for _ in range(100):
                bridge_exists = any(
                    _bridge_endpoint(entry) == endpoint
                    and entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_AWS_BRIDGE
                    for entry in self.hass.config_entries.async_entries(DOMAIN)
                )
                if bridge_exists:
                    break
                await asyncio.sleep(0.05)
            if not bridge_exists:
                raise RuntimeError("AWS bridge entry was not committed")

            transport_result = await self.hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": "transport_apply"},
                data=transport_data,
            )
            # A pre-existing transport is updated through an abort result with
            # reason transport_updated; that is an expected successful path.
            if transport_result.get("type") != "create_entry" and not (
                transport_result.get("type") == "abort"
                and transport_result.get("reason") == "transport_updated"
            ):
                raise RuntimeError(
                    "MQTT transport creation did not complete: "
                    f"{transport_result.get('reason', transport_result.get('type'))}"
                )

            transport_ready = False
            for _ in range(100):
                transport_ready = any(
                    entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_MQTT_TRANSPORT
                    for entry in self.hass.config_entries.async_entries(DOMAIN)
                )
                if transport_ready:
                    break
                await asyncio.sleep(0.05)
            if not transport_ready:
                raise RuntimeError("MQTT transport entry was not committed")

        except Exception:  # noqa: BLE001
            _LOGGER.exception("OverKill setup: failed to apply bridge/transport settings")
            domain_data.pop("_initial_setup_wizard", None)
            return self.async_abort(reason="setup_failed")

        return await self.async_step_discover_hub()

    async def async_step_discover_hub(self, user_input=None) -> FlowResult:
        """Search MQTT after transport setup and show the final hub confirmation."""
        domain_data = self.hass.data.setdefault(DOMAIN, {})
        wizard = domain_data.get("_initial_setup_wizard")
        if wizard is None or wizard.get("flow_id") != self.flow_id:
            # Recovery path for an entry created outside this wizard.
            return self.async_abort(reason="no_data")

        # If the hub is already configured, do not manufacture a duplicate.
        configured_hub = next(
            (
                entry for entry in self.hass.config_entries.async_entries(DOMAIN)
                if entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_HUB
            ),
            None,
        )
        if configured_hub is not None and wizard.get("discovery") is None:
            wizard["discovery"] = {
                **configured_hub.data,
                "already_configured": True,
            }

        if user_input is not None and wizard.get("discovery") is None:
            # The first search can time out while a hub is booting. Submitting
            # this page performs another bounded search rather than failing.
            wizard["event"].clear()

        if wizard.get("discovery") is None:
            try:
                await asyncio.wait_for(wizard["event"].wait(), timeout=12.0)
            except asyncio.TimeoutError:
                pass

        discovery = wizard.get("discovery")
        if discovery is not None:
            if discovery.get("already_configured"):
                domain_data.pop("_initial_setup_wizard", None)
                return self.async_abort(reason="setup_complete_hub_exists")
            self._discovery_info = discovery
            return await self.async_step_confirm_hub()

        return self.async_show_form(
            step_id="discover_hub",
            data_schema=vol.Schema({}),
            errors={"base": "hub_not_found"} if user_input is not None else {},
        )

    async def async_step_transport_apply(
        self, transport_data: dict | None = None
    ) -> FlowResult:
        """Internal non-interactive step used by the first-time setup wizard."""
        if not isinstance(transport_data, dict):
            return self.async_abort(reason="no_data")
        mode = transport_data.get(CONF_MQTT_MODE)
        if mode not in (MQTT_MODE_LOCAL, MQTT_MODE_CLOUD, MQTT_MODE_BOTH):
            return self.async_abort(reason="invalid_transport_mode")
        broker_fields = (
            CONF_BROKER_HOST,
            CONF_BROKER_PORT,
            CONF_BROKER_CLIENT_ID,
            CONF_BROKER_USERNAME,
            CONF_BROKER_PASSWORD,
        )
        broker_data = {
            key: transport_data[key]
            for key in broker_fields
            if key in transport_data
        }
        if mode in (MQTT_MODE_CLOUD, MQTT_MODE_BOTH) and not str(
            broker_data.get(CONF_BROKER_HOST, "")
        ).strip():
            return self.async_abort(reason="invalid_broker")
        return await self._async_save_transport(mode, broker_data or None)

    async def _async_save_transport(self, mode, broker_data) -> FlowResult:
        data = {
            CONF_ENTRY_TYPE: ENTRY_TYPE_MQTT_TRANSPORT,
            CONF_MQTT_MODE: mode,
        }
        if broker_data:
            data.update(broker_data)

        broker_fields = (
            CONF_BROKER_HOST,
            CONF_BROKER_PORT,
            CONF_BROKER_CLIENT_ID,
            CONF_BROKER_USERNAME,
            CONF_BROKER_PASSWORD,
        )
        if mode == MQTT_MODE_LOCAL:
            # Local mode deliberately stores no separate broker configuration.
            for field in broker_fields:
                data.pop(field, None)

        # Reuse an existing transport entry instead of silently aborting on
        # its unique ID. Merge any options into data so stale options cannot
        # override the newly selected mode or broker settings.
        existing = next(
            (
                e for e in self.hass.config_entries.async_entries(DOMAIN)
                if e.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_MQTT_TRANSPORT
            ),
            None,
        )
        if existing is not None:
            updated_data = {**existing.data, **existing.options, **data}
            if mode == MQTT_MODE_LOCAL:
                for field in broker_fields:
                    updated_data.pop(field, None)
            self.hass.config_entries.async_update_entry(
                existing,
                data=updated_data,
                options={},
                title=f"OverKill MQTT Transport ({mode.title()})",
            )
            # The entry update listener performs the reload; avoid issuing a
            # second concurrent reload here.
            return self.async_abort(reason="transport_updated")

        return self.async_create_entry(
            title=f"OverKill MQTT Transport ({mode.title()})",
            data=data,
        )

    async def _broker_form(self, user_input, step_id, mode) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                host = str(user_input[CONF_BROKER_HOST]).strip()
                if not host:
                    raise ValueError("Broker host is required")
            except Exception as err:
                _LOGGER.warning("Cloud broker validation failed: %s", err)
                errors["base"] = "invalid_broker"
            else:
                broker_data = {
                    CONF_BROKER_HOST: host,
                    CONF_BROKER_PORT: int(
                        user_input.get(CONF_BROKER_PORT, DEFAULT_BROKER_PORT)
                    ),
                    CONF_BROKER_CLIENT_ID: user_input.get(
                        CONF_BROKER_CLIENT_ID, ""
                    ),
                    CONF_BROKER_USERNAME: user_input.get(
                        CONF_BROKER_USERNAME, ""
                    ),
                    CONF_BROKER_PASSWORD: user_input.get(
                        CONF_BROKER_PASSWORD, ""
                    ),
                }
                if getattr(self, "_pending_bridge_data", None):
                    return await self._async_finish_initial_setup(mode, broker_data)
                return await self._async_save_transport(mode, broker_data)

        schema = vol.Schema({
            vol.Required(CONF_BROKER_HOST): str,
            vol.Optional(CONF_BROKER_PORT,
                         default=DEFAULT_BROKER_PORT): vol.All(
                int, vol.Range(min=1, max=65535)
            ),
            vol.Optional(CONF_BROKER_CLIENT_ID, default=""): str,
            vol.Optional(CONF_BROKER_USERNAME, default=""): str,
            vol.Optional(CONF_BROKER_PASSWORD, default=""): str,
        })
        return self.async_show_form(
            step_id=step_id, data_schema=schema, errors=errors,
        )

    # ------------------------------------------------- hub discovery

    async def async_step_mqtt(self, discovery_info: dict | None = None) -> FlowResult:
        """Receive a discovered hub, then ask the user before adding it."""
        if discovery_info is None:
            return self.async_abort(reason="no_data")
        tag = str(discovery_info.get("aws_tag", "")).upper()
        topic_prefix = str(discovery_info.get("topic_prefix", "")).strip()
        if len(tag) != 4 or not topic_prefix:
            return self.async_abort(reason="invalid_tag")
        self._discovery_info = {**discovery_info, "aws_tag": tag}
        broker_entry_id = str(discovery_info.get(CONF_BROKER_ENTRY_ID, "") or "")
        await self.async_set_unique_id(
            hub_config_unique_id(topic_prefix, tag, broker_entry_id)
        )
        self._abort_if_unique_id_configured()
        return await self.async_step_confirm_hub()

    async def async_step_confirm_hub(self, user_input=None) -> FlowResult:
        """Confirmation form displayed after local/cloud MQTT discovery."""
        info = getattr(self, "_discovery_info", None)
        if not info:
            return self.async_abort(reason="no_data")

        if user_input is not None:
            title = str(info.get("hub_name") or f"OverKill Hub {info['aws_tag']}")
            # The same confirmation step is used by both automatic discovery
            # and initial setup. Keep the bridge assignment explicit, including
            # an empty string for a deliberately local-only hub.
            prefix = str(info.get(CONF_TOPIC_PREFIX, "")).strip()
            tag = str(info.get(CONF_AWS_TAG, "")).upper()
            broker_entry_id = str(info.get(CONF_BROKER_ENTRY_ID, "") or "")
            await self.async_set_unique_id(
                hub_config_unique_id(prefix, tag, broker_entry_id)
            )
            self._abort_if_unique_id_configured()

            bridge_id = str(user_input.get(CONF_BRIDGE_ENTRY_ID, "") or "")
            valid_ids = {entry.entry_id for entry in _bridge_entries(self.hass)}
            if bridge_id and bridge_id not in valid_ids:
                _LOGGER.warning(
                    "Hub confirmation selected an unavailable bridge %s; "
                    "saving hub as local-only", bridge_id,
                )
                bridge_id = ""

            wizard = self.hass.data.get(DOMAIN, {}).get("_initial_setup_wizard")
            if wizard and wizard.get("flow_id") == self.flow_id:
                self.hass.data[DOMAIN].pop("_initial_setup_wizard", None)
            return self.async_create_entry(
                title=title,
                data={
                    **info,
                    CONF_HUB_NAME: title,
                    CONF_ENTRY_TYPE: ENTRY_TYPE_HUB,
                    CONF_BRIDGE_ENTRY_ID: bridge_id,
                },
            )

        broker_entry_id = str(info.get("broker_entry_id", "") or "")
        transport_label = "Cloud MQTT" if broker_entry_id else "Local MQTT"
        if CONF_BRIDGE_ENTRY_ID in info:
            default_bridge_id = str(info.get(CONF_BRIDGE_ENTRY_ID) or "")
        elif broker_entry_id:
            # Remote/cloud hubs must opt into an Alexa account explicitly.
            # Auto-selecting the only configured bridge could expose a remote
            # household's catalogue to the wrong Alexa account before its own
            # bridge has been added.
            default_bridge_id = ""
        else:
            default_bridge_id = _default_bridge_selection(self.hass)
        return self.async_show_form(
            step_id="confirm_hub",
            data_schema=vol.Schema({
                vol.Required(
                    CONF_BRIDGE_ENTRY_ID, default=default_bridge_id
                ): _bridge_selector(self.hass, default_bridge_id),
            }),
            description_placeholders={
                "hub_name": str(info.get("hub_name", "OverKill Smart Hub")),
                "topic_prefix": str(info.get("topic_prefix", "")),
                "transport": transport_label,
            },
        )

    # -------------------------------------------------------- options

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        return OverkillSmartHubOptionsFlow(entry)


# ---------------------------------------------------- options flow

class OverkillSmartHubOptionsFlow(OptionsFlow):
    def __init__(self, entry: ConfigEntry) -> None:
        self.entry = entry
        self._pending_mode: str = ""

    async def async_step_init(self, user_input=None) -> FlowResult:
        etype = self.entry.data.get(CONF_ENTRY_TYPE)
        data = {**self.entry.data, **self.entry.options}

        if etype == "hub":
            return await self._hub_options(user_input, data)
        if etype == ENTRY_TYPE_AWS_BRIDGE:
            return await self._aws_options(user_input, data)
        if etype == ENTRY_TYPE_MQTT_TRANSPORT:
            return await self._transport_options(user_input, data)
        return self.async_create_entry(title="", data={})

    async def _hub_options(self, user_input, data) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current_bridge_id = (
            str(data.get(CONF_BRIDGE_ENTRY_ID) or "")
            if CONF_BRIDGE_ENTRY_ID in data
            else _default_bridge_selection(self.hass)
        )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Optional("hub_name",
                             default=data.get("hub_name", "")): str,
                vol.Required(
                    CONF_BRIDGE_ENTRY_ID, default=current_bridge_id
                ): _bridge_selector(self.hass, current_bridge_id),
            }),
        )

    async def _transport_options(self, user_input, data) -> FlowResult:
        current_mode = data.get(CONF_MQTT_MODE, MQTT_MODE_LOCAL)
        current_host = (data.get(CONF_BROKER_HOST) or "").strip()

        # Submit of the first form: capture the mode choice.
        if user_input is not None:
            new_mode = user_input.get(CONF_MQTT_MODE, current_mode)

            # Switching to cloud/both without saved host requires a second form.
            if new_mode in (MQTT_MODE_CLOUD, MQTT_MODE_BOTH) and not current_host:
                self._pending_mode = new_mode
                return await self.async_step_broker()

            # Existing cloud details can be retained, or local mode saved now.
            merged = {**data, **user_input}
            if new_mode == MQTT_MODE_LOCAL:
                # Clear stale broker fields when switching to local.
                merged.pop(CONF_BROKER_HOST, None)
                merged.pop(CONF_BROKER_PORT, None)
                merged.pop(CONF_BROKER_CLIENT_ID, None)
                merged.pop(CONF_BROKER_USERNAME, None)
                merged.pop(CONF_BROKER_PASSWORD, None)
            return self.async_create_entry(title="", data=merged)

        # Initial display of the mode form.
        fields: dict = {
            vol.Required(CONF_MQTT_MODE, default=current_mode): vol.In([
                MQTT_MODE_LOCAL, MQTT_MODE_CLOUD, MQTT_MODE_BOTH
            ]),
        }
        # Show broker fields inline only if the entry is already cloud/both.
        if current_mode in (MQTT_MODE_CLOUD, MQTT_MODE_BOTH):
            fields.update(self._broker_field_schema(data))

        return self.async_show_form(
            step_id="init", data_schema=vol.Schema(fields),
        )

    async def async_step_broker(self, user_input=None) -> FlowResult:
        """Second form for cloud/both when switching modes without a host."""
        data = {**self.entry.data, **self.entry.options}
        if user_input is not None:
            host = str(user_input.get(CONF_BROKER_HOST, "")).strip()
            if not host:
                return self.async_show_form(
                    step_id="broker",
                    data_schema=vol.Schema(self._broker_field_schema(data)),
                    errors={"base": "invalid_broker"},
                )
            merged = {
                **data,
                CONF_MQTT_MODE: self._pending_mode,
                CONF_BROKER_HOST: host,
                CONF_BROKER_PORT: int(
                    user_input.get(CONF_BROKER_PORT, DEFAULT_BROKER_PORT)
                ),
                CONF_BROKER_CLIENT_ID: user_input.get(CONF_BROKER_CLIENT_ID, ""),
                CONF_BROKER_USERNAME: user_input.get(CONF_BROKER_USERNAME, ""),
                CONF_BROKER_PASSWORD: user_input.get(CONF_BROKER_PASSWORD, ""),
            }
            return self.async_create_entry(title="", data=merged)

        return self.async_show_form(
            step_id="broker",
            data_schema=vol.Schema(self._broker_field_schema(data)),
        )

    @staticmethod
    def _broker_field_schema(data: dict) -> dict:
        return {
            vol.Required(CONF_BROKER_HOST,
                         default=data.get(CONF_BROKER_HOST, "")): str,
            vol.Optional(CONF_BROKER_PORT,
                         default=data.get(CONF_BROKER_PORT,
                                          DEFAULT_BROKER_PORT)): vol.All(
                int, vol.Range(min=1, max=65535)
            ),
            vol.Optional(CONF_BROKER_CLIENT_ID,
                         default=data.get(CONF_BROKER_CLIENT_ID, "")): str,
            vol.Optional(CONF_BROKER_USERNAME,
                         default=data.get(CONF_BROKER_USERNAME, "")): str,
            vol.Optional(CONF_BROKER_PASSWORD,
                         default=data.get(CONF_BROKER_PASSWORD, "")): str,
        }

    async def _aws_options(self, user_input, data) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            merged = {
                CONF_AWS_ENDPOINT: user_input.get(
                    CONF_AWS_ENDPOINT, data.get(CONF_AWS_ENDPOINT, "")
                ),
                CONF_AWS_PORT: user_input.get(
                    CONF_AWS_PORT, data.get(CONF_AWS_PORT, DEFAULT_AWS_PORT)
                ),
                CONF_AWS_CLIENT_ID: user_input.get(
                    CONF_AWS_CLIENT_ID, data.get(CONF_AWS_CLIENT_ID, "")
                ),
                CONF_AWS_CMD_TOPIC: user_input.get(
                    CONF_AWS_CMD_TOPIC,
                    data.get(CONF_AWS_CMD_TOPIC, DEFAULT_AWS_CMD_TOPIC),
                ),
                CONF_AWS_CATALOG_TOPIC: user_input.get(
                    CONF_AWS_CATALOG_TOPIC,
                    data.get(CONF_AWS_CATALOG_TOPIC, DEFAULT_AWS_CATALOG_TOPIC),
                ),
            }

            try:
                cert_endpoint = _normalize_endpoint(
                    merged.get(CONF_AWS_ENDPOINT, data.get(CONF_AWS_ENDPOINT, ""))
                )
                if not cert_endpoint:
                    errors["base"] = "invalid_endpoint"
                    raise ValueError("AWS IoT endpoint hostname is required")
                if _bridge_endpoint_in_use(
                    self.hass, cert_endpoint, exclude_entry_id=self.entry.entry_id
                ):
                    errors["base"] = "already_configured"
                    raise ValueError(
                        "That AWS IoT endpoint is already used by another bridge"
                    )
                merged[CONF_AWS_ENDPOINT] = cert_endpoint
                for field, marker, label, base_name in (
                    (CONF_AWS_CA_PEM, "BEGIN CERTIFICATE",
                     "AmazonRootCA1.pem", "AmazonRootCA1.pem"),
                    (CONF_AWS_CERT_PEM, "BEGIN CERTIFICATE",
                     "Device certificate", "device.pem.crt"),
                    (CONF_AWS_KEY_PEM, "PRIVATE KEY",
                     "Device private key", "device.pem.key"),
                ):
                    uploaded = user_input.get(field)
                    if uploaded:
                        merged[field] = await self.hass.async_add_executor_job(
                            _resolve_and_validate, self.hass,
                            uploaded, marker, label,
                            _bridge_cert_filename(cert_endpoint, base_name, self.entry.entry_id),
                        )
                    else:
                        merged[field] = data.get(field, "")
            except Exception as err:
                _LOGGER.warning("AWS bridge options validation failed: %s", err)
                if not errors.get("base"):
                    errors["base"] = "invalid_cert"
            else:
                return self.async_create_entry(title="", data=merged)

        schema = vol.Schema({
            vol.Optional(CONF_AWS_ENDPOINT,
                         default=data.get(CONF_AWS_ENDPOINT, "")): str,
            vol.Optional(CONF_AWS_PORT,
                         default=data.get(CONF_AWS_PORT,
                                          DEFAULT_AWS_PORT)): vol.All(
                int, vol.Range(min=1, max=65535)
            ),
            vol.Optional(CONF_AWS_CLIENT_ID,
                         default=data.get(CONF_AWS_CLIENT_ID, "")): str,
            vol.Optional(CONF_AWS_CMD_TOPIC,
                         default=data.get(CONF_AWS_CMD_TOPIC,
                                          DEFAULT_AWS_CMD_TOPIC)): str,
            vol.Optional(CONF_AWS_CATALOG_TOPIC,
                         default=data.get(CONF_AWS_CATALOG_TOPIC,
                                          DEFAULT_AWS_CATALOG_TOPIC)): str,
            vol.Optional(CONF_AWS_CA_PEM): _file_picker(),
            vol.Optional(CONF_AWS_CERT_PEM): _file_picker(),
            vol.Optional(CONF_AWS_KEY_PEM): _file_picker(),
        })
        return self.async_show_form(
            step_id="init", data_schema=schema, errors=errors,
        )
