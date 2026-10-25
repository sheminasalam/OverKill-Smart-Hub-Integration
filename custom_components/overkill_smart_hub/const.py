"""Constants for the OverKill Smart Hub integration."""
from __future__ import annotations

DOMAIN = "overkill_smart_hub"

# Entry types
CONF_ENTRY_TYPE            = "entry_type"
ENTRY_TYPE_HUB             = "hub"
ENTRY_TYPE_AWS_BRIDGE      = "aws_bridge"
ENTRY_TYPE_MQTT_TRANSPORT  = "mqtt_transport"

# Hub fields
CONF_HUB_NAME          = "hub_name"
CONF_TOPIC_PREFIX      = "topic_prefix"
CONF_AWS_TAG           = "aws_tag"
CONF_BROKER_ENTRY_ID   = "broker_entry_id"     # MQTT transport entry for this hub
CONF_BRIDGE_ENTRY_ID   = "bridge_entry_id"     # AWS/Alexa bridge assigned to this hub

# MQTT transport
CONF_MQTT_MODE         = "mqtt_mode"           # "local" | "cloud" | "both"
MQTT_MODE_LOCAL        = "local"
MQTT_MODE_CLOUD        = "cloud"
MQTT_MODE_BOTH         = "both"

CONF_BROKER_HOST       = "broker_host"
CONF_BROKER_PORT       = "broker_port"
CONF_BROKER_CLIENT_ID  = "broker_client_id"
CONF_BROKER_USERNAME   = "broker_username"
CONF_BROKER_PASSWORD   = "broker_password"

# AWS bridge fields
CONF_AWS_ENDPOINT      = "aws_endpoint"
CONF_AWS_PORT          = "aws_port"
CONF_AWS_CLIENT_ID     = "aws_client_id"
CONF_AWS_CA_PEM        = "aws_ca_pem"
CONF_AWS_CERT_PEM      = "aws_cert_pem"
CONF_AWS_KEY_PEM       = "aws_key_pem"
CONF_AWS_CMD_TOPIC     = "aws_cmd_topic"
CONF_AWS_CATALOG_TOPIC = "aws_catalog_topic"

# Defaults
DEFAULT_BROKER_PORT       = 1883
DEFAULT_AWS_PORT          = 8883
DEFAULT_AWS_CMD_TOPIC     = "alexa/+/directive"
DEFAULT_AWS_CATALOG_TOPIC = "alexa/config/devices"

# Runtime storage
DATA_COORDINATOR = "coordinator"


def mqtt_source_namespace(broker_entry_id: str | None) -> str:
    """Stable hub source namespace that survives cloud transport re-creation.

    This integration supports one shared cloud MQTT transport plus Home
    Assistant's local MQTT broker. A transport entry ID can change when users
    delete/re-add the transport, so it must not be embedded in a hub identity.
    """
    return "cloud" if str(broker_entry_id or "").strip() else "local"


def hub_prefix_key(topic_prefix: str, broker_entry_id: str | None) -> tuple[str, str]:
    """Unique runtime index key for a topic prefix on local vs cloud MQTT."""
    return mqtt_source_namespace(broker_entry_id), str(topic_prefix).rstrip("/")


def hub_config_unique_id(
    topic_prefix: str, tag: str, broker_entry_id: str | None
) -> str:
    """Config-entry unique ID that permits identical prefixes on two brokers."""
    return (
        f"hub:{mqtt_source_namespace(broker_entry_id)}:"
        f"{str(topic_prefix).rstrip('/')}:{str(tag).upper()}"
    )
