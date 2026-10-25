# OverKill Smart Hub — Home Assistant integration

The OverKill Smart Hub integration connects OverKill ESP8266 IR hubs to Home Assistant through MQTT. It discovers hubs and their entities, supports a local MQTT transport and an optional separate cloud MQTT transport, and can bridge selected hubs to one or more Alexa/AWS IoT accounts.

The companion firmware and hardware live in the [OverKill Smart IR Hub project](https://github.com/sheminasalam/OverKill-Smart-IR-Hub). This repository contains **only the Home Assistant custom integration**, as required for HACS distribution.

> **Pre-release:** manifest version 1.3.0. Before default HACS listing, complete Home Assistant Brands registration, publish a full GitHub release after CI passes, and finish the end-to-end test matrix.

## Features

- Discovers OverKill hubs from retained MQTT discovery/configuration messages.
- Uses Home Assistant's configured MQTT integration for local hubs; the local mode needs no separate broker credentials in this integration.
- Optional Cloud MQTT transport for remote hubs, or Both for local and cloud sources.
- Supports multiple hubs and separate AWS IoT bridge entries so hubs can be assigned to the correct Alexa household.
- Connects each AWS IoT bridge over MQTT with mutual TLS using a Root CA, client certificate and private key.
- Aggregates the selected hubs' Alexa-exposed devices into the retained catalogue topic, by default `alexa/config/devices`.
- Subscribes to Alexa directive topics, by default `alexa/+/directive`, and routes them to the selected hub.
- Provides the `overkill_smart_hub.rediscover_hubs` service to request discovery again.

## Requirements

- Home Assistant with the built-in **MQTT** integration set up for local MQTT mode.
- At least one OverKill hub with firmware that publishes the integration's expected MQTT discovery/configuration topics.
- A reachable MQTT broker shared by the hub and the configured integration transport.
- AWS IoT Core and the three PEM files **only if** Alexa cloud bridging is being used. Local MQTT setup does not need AWS credentials or certificates.

**Network-security warning:** the firmware and this integration's optional Cloud MQTT transport currently use ordinary MQTT (typically port 1883) without TLS. MQTT usernames, passwords and payloads are not encrypted on that connection. Nabu Casa protects remote access to the Home Assistant UI; it does **not** protect this separate MQTT link. Do not use Cloud/Both MQTT across the public Internet in the current version. Use a VPN/private routed network and per-device broker credentials/topic ACLs until both clients support TLS; a TLS broker listener alone is not enough.

## Installation

### HACS (custom repository during beta / before default listing)

1. Create/use the public repository `sheminasalam/OverKill-Smart-Hub-Integration` with these files at its root. The repository URL is intended to be `https://github.com/sheminasalam/OverKill-Smart-Hub-Integration`.
2. In Home Assistant, open **HACS → Integrations → ⋮ → Custom repositories**.
3. Add `https://github.com/sheminasalam/OverKill-Smart-Hub-Integration` and select **Integration** as the category.
4. Find **OverKill Smart Hub**, download/install it, then restart Home Assistant.
5. Go to **Settings → Devices & services → Add integration → OverKill Smart Hub**.

The repository has not yet been added to HACS default repositories. Until that is accepted, users may need to install it as a custom repository.

### Manual installation

1. Copy `custom_components/overkill_smart_hub` into `/config/custom_components/overkill_smart_hub/`.
2. Ensure `manifest.json`, `__init__.py`, `config_flow.py`, `coordinator.py`, `hub.py`, platform files and translations are directly in that directory.
3. Restart Home Assistant and add **OverKill Smart Hub** through **Settings → Devices & services**.

## First-time setup

The first screen offers two paths:

- **MQTT only — skip AWS/Alexa for now:** choose Local, Cloud or Both on the next screen. Local MQTT uses Home Assistant's existing MQTT configuration and requires no AWS certificate uploads. Cloud/Both asks for the cloud broker fields.
- **Set up AWS IoT / Alexa bridge now:** upload the AWS Root CA, client/device certificate and matching private key, then choose Local, Cloud or Both. The bridge is optional and can be added later.

### Local MQTT only

1. Configure Home Assistant's MQTT integration and broker first.
2. Point the hub's MQTT settings at that broker and enable Home Assistant discovery.
3. Add OverKill Smart Hub, select the MQTT-only path, then **Local MQTT only**.
4. Keep the hub online and allow retained discovery messages to arrive. When a hub is discovered, confirm it and choose **None (no Alexa bridge)** if you are not using Alexa.

### Cloud MQTT or Both

Choose Cloud or Both in the MQTT transport selector and enter the broker host, port, and optional client credentials. The remote hub must use the same broker and the matching topic prefix/configuration. Review the network-security warning above before configuring any remote/public network.

### Add Alexa/AWS IoT later

From the integration's setup flow choose **Add another Alexa AWS bridge**, then provide an AWS IoT data endpoint, unique MQTT client ID, Root CA PEM, device certificate PEM, and matching private-key PEM. Assign each hub explicitly to the intended bridge. Use separate AWS IoT accounts/endpoints/credentials for separate Alexa households; never assign a remote hub to the wrong household's bridge.

Create the AWS IoT certificate and policy in AWS IoT Core first. The certificate policy should be restricted to the actual bridge client ID and the required directive/catalogue topics. Do not commit certificates, private keys, or broker passwords into this repository or public issue reports. See the firmware project's [AWS/Alexa setup guide](https://github.com/sheminasalam/OverKill-Smart-IR-Hub/blob/main/docs/ALEXA_AWS.md) for the end-to-end AWS/Lambda/skill walkthrough.

## Important limitations

- The currently supplied Lambda/integration path does not implement Alexa Event Gateway token exchange/refresh for proactive events. Do not enable proactive Alexa events unless that support is added and tested.
- The bridge catalogue builder currently forwards basic device fields (`id`, `name`, `type`); it does not automatically propagate all per-device TV channel metadata.
- A valid Home Assistant MQTT configuration is required for Local mode. Cloud mode requires a reachable external broker.
- This integration is a DIY companion to the OverKill firmware, not a retail plug-and-play appliance.

## Diagnostics and troubleshooting

- **No hub discovered:** confirm the MQTT integration is connected, the hub uses the same broker in Local mode, MQTT discovery is enabled, and the hub publishes retained configuration messages. Use the `overkill_smart_hub.rediscover_hubs` service after fixing broker/topic settings.
- **Bridge TLS fails:** verify the endpoint hostname, port 8883, active certificate, Root CA, matching certificate/private key, certificate policy, bridge client ID, and outbound network access.
- **Subscribe rejected:** inspect AWS IoT policy permissions for the actual directive topic filter and topic receive resource.
- **Alexa discovers no devices:** check the retained JSON array on `alexa/config/devices`, the hub's cloud-exposure setting, and its explicit bridge assignment.
- **Cloud hub is not found:** verify broker host/port/credentials on both sides and ensure the broker is reachable from Home Assistant and the hub.

When reporting a problem, include the Home Assistant version, integration version, transport mode, relevant sanitized logs and a description of the failing step. **Redact** private keys, certificates, passwords, account IDs if desired, and full IoT endpoints before posting publicly.

## Development and validation

This repository includes GitHub Actions for the official HACS Action, Home Assistant Hassfest and lightweight package/translation checks. All three currently pass on GitHub. A local Python syntax check alone does not prove end-to-end operation; test the integration in a clean Home Assistant instance with Local MQTT, Cloud MQTT, Both, optional AWS bridge, bridge assignment, reconnect/reload and hub rediscovery before publishing a release.

## HACS publication status

See [`PRE_RELEASE_CHECKLIST.md`](PRE_RELEASE_CHECKLIST.md). This repository now uses the same MIT License as the Net Metering integration. MIT is an OSI-approved open-source license, so the license itself does not block HACS default-list submission. Default listing still requires the official HACS Action and Hassfest checks to pass, a full GitHub release, and Home Assistant Brands registration. Until those steps are complete, install it via HACS's Custom repositories feature.

## License

This project is released under the MIT License. See [`LICENSE`](LICENSE).
