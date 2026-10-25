# HACS pre-release checklist

## Repository setup

- [ ] Confirm this is a **public, dedicated integration repository** named `OverKill-Smart-Hub-Integration`; keep the firmware/hardware repository separate.
- [ ] Confirm the repository root contains this package directly (no extra wrapper folder).
- [ ] Set the GitHub repository description to: `Home Assistant integration for OverKill ESP8266 Smart IR Hubs via MQTT, with optional AWS IoT/Alexa bridging.`
- [ ] Add topics: `home-assistant`, `home-assistant-custom-component`, `hacs`, `mqtt`, `esp8266`, `infrared`, `alexa`.
- [ ] Enable GitHub Issues.
- [ ] Choose and add an actual open-source license. Do not submit `LICENSE_PENDING.md` as a license.
- [ ] Verify the new repository links in `manifest.json` point to the final repo URL after creation.

## Automated validation

- [ ] GitHub Actions: HACS validation passes without ignored failures.
- [ ] GitHub Actions: Home Assistant Hassfest passes.
- [ ] Static package/translation checks pass.
- [ ] Review any warnings from CI rather than treating a successful Python compile as sufficient.

## Home Assistant functional testing

- [ ] Fresh install can select MQTT-only setup without requesting AWS certificates.
- [ ] Local mode works with Home Assistant MQTT; hub discovery and entity creation work.
- [ ] Existing setup options can add an AWS bridge later.
- [ ] Cloud mode works with an appropriately protected broker.
- [ ] Both mode handles local and cloud hubs without duplicate identities.
- [ ] AWS bridge uses correct Root CA, client certificate/private key and client ID policy.
- [ ] Catalogue aggregation publishes the selected hubs only and keeps household assignments isolated.
- [ ] Alexa directive routing works for every intended hub tag.
- [ ] Reload/unload, reconnect, rediscovery, removing a bridge and assigning a hub to None are tested.
- [ ] Existing installations using version 1.3.0 data can be upgraded without losing their config entries or certificate files.

## Home Assistant Brands

- [ ] Submit `OverKill-HomeAssistant-Brands-Submission.zip` to the `home-assistant/brands` repository as a PR under `custom_integrations/overkill_smart_hub/`.
- [ ] Wait for the Brands PR to be accepted. HACS's integration publishing guidance requires brand registration.

## Release and HACS listing

- [ ] After CI and the functional matrix pass, publish a **full GitHub Release** tagged `v1.3.0` (a tag alone is not the full release step).
- [ ] Only after the public repository is valid, actions pass, license is recognized, and Brands registration is complete, submit the relevant PR to `hacs/default` if default-store listing is desired.
- [ ] Remember: HACS custom-repository installation can be tested before default-store inclusion, but default inclusion has additional requirements.
