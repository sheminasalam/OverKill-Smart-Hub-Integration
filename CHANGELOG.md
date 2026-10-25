# Changelog

All notable changes to the OverKill Smart Hub Home Assistant integration are documented here.

## 1.3.0 — HACS release candidate

- Prepared a dedicated, single-integration HACS repository layout.
- Added HACS validation, Hassfest and static package checks.
- Added a first-run choice so MQTT-only users can install without AWS certificates.
- Documented Local, Cloud and Both MQTT modes, optional AWS IoT/Alexa bridge setup, multi-bridge assignment and troubleshooting.
- Removed generated Python bytecode from the release package.

The official HACS Action, Home Assistant Hassfest and repository static checks pass on GitHub. End-to-end Home Assistant testing across the supported transport and AWS bridge configurations remains required before release. The repository license is MIT, matching the Net Metering integration.
