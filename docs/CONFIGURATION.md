# Configuration details

## Entry types created by this integration

- **Hub entry:** one discovered OverKill hub, including its MQTT source and optional AWS bridge assignment.
- **MQTT transport entry:** Local, Cloud or Both transport configuration.
- **AWS bridge entry:** one AWS IoT mutual-TLS connection associated with a distinct Alexa catalogue/household.

## Local mode

Local mode listens through Home Assistant's existing MQTT integration. It intentionally does not request a second broker hostname or credentials. The ESP hub and Home Assistant must use the same MQTT broker/topic namespace.

## Cloud mode

Cloud mode creates a separate broker connection for a hub that is not on the Home Assistant LAN. Both the hub and integration must use the same broker address, port, authentication and corresponding topic configuration.

**Security limitation:** the current firmware and integration use plain MQTT without TLS. Usernames, passwords and topic payloads are therefore not protected by this MQTT connection. Nabu Casa encrypts remote access to the Home Assistant UI, but it does not encrypt this separate MQTT traffic. Do not use Cloud/Both MQTT across the public Internet in the current version. Use a VPN/private routed network and per-device broker credentials/topic ACLs until both the hub firmware and integration support TLS. A TLS listener on the broker alone is insufficient while either client still uses plain MQTT.

## Both mode

Both keeps the Home Assistant local MQTT listener and configures a separate cloud broker. Use unique client IDs and avoid reusing a client ID that is already active on the broker.

## AWS bridge certificate files

The bridge asks for:

- AWS IoT Root CA PEM.
- An active AWS IoT client/device certificate PEM.
- The private key matching that certificate.

The integration validates that files resemble PEM certificates/keys and copies them into Home Assistant's private `overkill_smart_hub` configuration directory. Treat the private key as a secret. AWS IoT policy permissions must match the bridge's client ID and the configured directive/catalogue topics.

## Hub-to-bridge assignment

Assign each cloud-exposed hub explicitly to the AWS bridge for its intended Alexa account. Select **None (no Alexa bridge)** for a hub that must remain local-only. Do not rely on automatic assignment when multiple household/bridge entries exist.

## Rediscovery service

Call `overkill_smart_hub.rediscover_hubs` to rescan retained discovery/configuration topics after a broker or hub configuration change.
