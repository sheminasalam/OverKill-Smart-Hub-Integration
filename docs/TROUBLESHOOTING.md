# Troubleshooting

## The integration does not appear in the Add integration dialog

Confirm the folder is named `custom_components/overkill_smart_hub`, contains `manifest.json` and `config_flow.py`, and that Home Assistant was restarted after installation. Check logs for manifest/import errors.

## The hub is not discovered

1. Check that Home Assistant's MQTT integration is connected.
2. For Local mode, confirm the hub and Home Assistant use the same MQTT broker.
3. Confirm the firmware MQTT client is connected, Home Assistant auto-discovery is enabled, and the base topic is unique.
4. Check the hub's serial/Web UI status for MQTT connection errors.
5. Call `overkill_smart_hub.rediscover_hubs` after fixing the issue.

## An AWS bridge cannot connect

Check the IoT data endpoint, port 8883, certificate activation, Root CA chain, matching client certificate/private key, client ID policy and outbound connectivity. Never publish the private key in logs or issues.

## AWS connection succeeds but subscriptions are rejected

Review the attached AWS IoT policy. `iot:Connect` needs the allowed client ID; `iot:Subscribe` applies to the topic-filter ARN; `iot:Receive` applies to the actual topic ARN. Keep permissions restricted to the directive topic pattern and catalogue topic used by your setup.

## Alexa catalogue remains empty

Check whether the hub is marked for cloud exposure in its Web UI, whether the hub was assigned to the intended AWS bridge, whether the bridge is connected, and whether a retained JSON array is present on `alexa/config/devices`.

## Multiple Alexa households

Use distinct AWS IoT endpoints/accounts and certificates where isolation is required. Assign each hub to its household's bridge explicitly. Do not share a single catalogue across households unless that cross-account exposure is intentional.
