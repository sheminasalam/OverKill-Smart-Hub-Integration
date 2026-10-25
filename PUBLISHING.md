# Publishing OverKill Smart Hub

This public repository contains the Home Assistant custom integration at `custom_components/overkill_smart_hub/`, separate from the companion firmware and hardware repository.

## Current validation

The repository now uses the MIT License, matching the Net Metering integration. GitHub currently reports the official HACS Action, Hassfest and static checks passing. End-to-end tests in Home Assistant are still required before treating version 1.3.0 as a production release.

Relevant links:

- Repository: https://github.com/sheminasalam/OverKill-Smart-Hub-Integration
- Actions: https://github.com/sheminasalam/OverKill-Smart-Hub-Integration/actions
- Release page: https://github.com/sheminasalam/OverKill-Smart-Hub-Integration/releases
- Pre-release checklist: [`PRE_RELEASE_CHECKLIST.md`](PRE_RELEASE_CHECKLIST.md)

## Finish before submitting to HACS default

1. Complete the Home Assistant functional test matrix in `PRE_RELEASE_CHECKLIST.md`, including Local MQTT, Cloud MQTT, Both, hub discovery, reconnection, multiple hubs, bridge assignment and AWS/Alexa routing.
2. Submit the icon assets from `OverKill-HomeAssistant-Brands-Submission.zip` to the `home-assistant/brands` repository under `custom_integrations/overkill_smart_hub/`, and wait until the brand entry is merged.
3. Publish a full GitHub Release tagged `v1.3.0`. A tag alone is not a published Release.
4. In the personal fork `sheminasalam/add-mutual-fund-tracker`, create a new branch from `master` (do not use `master` directly). Add `sheminasalam/OverKill-Smart-Hub-Integration` to the sorted `integration` file.
5. Open a pull request from that new branch to `hacs/default:master`. The default-list PR checks verify branding, repository metadata, the HACS manifest and repository release.

The existing `add-net-metering` branch and its PR should remain separate; the OverKill entry belongs in its own branch and PR.

## HACS custom repository install

Users can add `https://github.com/sheminasalam/OverKill-Smart-Hub-Integration` through **HACS → Integrations → ⋮ → Custom repositories**, choosing category **Integration**. The default-list PR is a separate publication step.
