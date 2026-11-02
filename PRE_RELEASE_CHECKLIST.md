# HACS pre-release checklist

## Repository setup

- [ ] Confirm this is a **public, dedicated integration repository** named `OverKill-Smart-Hub-Integration`; keep the firmware/hardware repository separate.
- [ ] Confirm the repository root contains this package directly (no extra wrapper folder).
- [ ] Set the GitHub repository description to: `Home Assistant integration for OverKill ESP8266 Smart IR Hubs via MQTT, with optional AWS IoT/Alexa bridging.`
- [ ] Add topics: `home-assistant`, `home-assistant-custom-component`, `hacs`, `mqtt`, `esp8266`, `infrared`, `alexa`.
- [ ] Enable GitHub Issues.
- [x] Add the MIT License matching the Net Metering integration; verify GitHub recognizes it as MIT.
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

## Branding

- [x] Confirm the integration ships `custom_components/overkill_smart_hub/brand/icon.png`. No separate `home-assistant/brands` repository PR is needed for this integration.

## Release and HACS listing

- [ ] After CI and the functional matrix pass, publish a **full GitHub Release** tagged `v1.3.1` (a tag alone is not the full release step).
- [x] Keep the existing submission to `hacs/default` updated with the new release and re-request review after requested changes are complete.
- [ ] Remember: users can install through HACS Custom repositories while default-store inclusion is pending.
