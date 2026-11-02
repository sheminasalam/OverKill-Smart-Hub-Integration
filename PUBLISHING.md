# Publishing OverKill Smart Hub

This public repository contains the Home Assistant custom integration at `custom_components/overkill_smart_hub/`, separate from the companion firmware and hardware repository.

## Current validation

The repository uses the MIT License. The `custom_components/overkill_smart_hub/brand/` directory already contains the integration icon; no separate Home Assistant Brands PR is required. Version 1.3.1 contains non-blocking Paho client shutdown and updated HACS installation guidance. End-to-end tests in Home Assistant should still be completed across the supported transport and AWS bridge configurations before treating it as production-ready.

Relevant links:

- Repository: https://github.com/sheminasalam/OverKill-Smart-Hub-Integration
- Actions: https://github.com/sheminasalam/OverKill-Smart-Hub-Integration/actions
- Release page: https://github.com/sheminasalam/OverKill-Smart-Hub-Integration/releases
- Pre-release checklist: [`PRE_RELEASE_CHECKLIST.md`](PRE_RELEASE_CHECKLIST.md)

## HACS default listing and review

1. Run the official HACS Action, Home Assistant Hassfest and repository static checks on the release commit.
2. Complete or explicitly track the Home Assistant functional test matrix in `PRE_RELEASE_CHECKLIST.md`, including Local MQTT, Cloud MQTT, Both, hub discovery, reconnection, multiple hubs, bridge assignment and AWS/Alexa routing.
3. Publish a full GitHub Release tagged `v1.3.1`; a tag alone is not a published GitHub Release.
4. Update the existing HACS default-list submission with the release link and re-request review after the maintainer's requested changes are complete.

The integration icon is already included at `custom_components/overkill_smart_hub/brand/icon.png`. A separate PR to `home-assistant/brands` is not required. Do not create a second HACS default-list PR; update the existing submission.

## HACS installation

Once the integration is included in HACS default, users can find **OverKill Smart Hub** in **HACS → Integrations** and install it directly. While the default-list review is pending, users can add the repository through **HACS → Integrations → ⋮ → Custom repositories**, category **Integration**.

## HACS custom repository install

Users can add `https://github.com/sheminasalam/OverKill-Smart-Hub-Integration` through **HACS → Integrations → ⋮ → Custom repositories**, choosing category **Integration**. The default-list PR is a separate publication step.
