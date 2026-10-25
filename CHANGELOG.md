# Changelog

All notable changes to the OverKill Smart Hub Home Assistant integration are documented here.

## 1.3.0 — HACS release candidate

- Prepared a dedicated, single-integration HACS repository layout.
- Added HACS validation, Hassfest and static package checks.
- Added a first-run choice so MQTT-only users can install without AWS certificates.
- Documented Local, Cloud and Both MQTT modes, optional AWS IoT/Alexa bridge setup, multi-bridge assignment and troubleshooting.
- Removed generated Python bytecode from the release package.

This candidate has passed local Python syntax and package consistency checks only. GitHub HACS/Hassfest workflows and end-to-end Home Assistant tests remain required before release.
