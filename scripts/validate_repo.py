#!/usr/bin/env python3
"""Lightweight checks for the OverKill Smart Hub HACS repository."""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
DOMAIN = "overkill_smart_hub"
COMPONENT = ROOT / "custom_components" / DOMAIN


def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        raise SystemExit(f"Invalid or missing JSON: {path.relative_to(ROOT)}: {err}") from err


def main() -> int:
    components = ROOT / "custom_components"
    directories = sorted(p.name for p in components.iterdir() if p.is_dir())
    if directories != [DOMAIN]:
        raise SystemExit(f"Expected exactly one integration folder {DOMAIN!r}, found {directories!r}")

    manifest = load_json(COMPONENT / "manifest.json")
    for key in ("domain", "name", "codeowners", "config_flow", "dependencies", "documentation", "integration_type", "iot_class", "issue_tracker", "version"):
        if key not in manifest:
            raise SystemExit(f"manifest.json is missing required field {key!r}")
    if manifest["domain"] != DOMAIN:
        raise SystemExit(f"Expected domain {DOMAIN!r}, found {manifest['domain']!r}")
    if manifest["integration_type"] != "hub":
        raise SystemExit("OverKill Smart Hub should be declared as integration_type 'hub'")

    hacs = load_json(ROOT / "hacs.json")
    if hacs.get("name") != "OverKill Smart Hub":
        raise SystemExit("hacs.json must name the HACS repository 'OverKill Smart Hub'")
    if hacs.get("render_readme") is not True:
        raise SystemExit("hacs.json must set render_readme=true or the root README will not render in HACS")

    # This project intentionally restricts commercial use. Validate that choice
    # directly; the official HACS Action's default-store license gate will reject
    # non-commercial licenses, even though custom repository installation is supported.
    license_path = ROOT / "LICENSE"
    try:
        license_text = license_path.read_text(encoding="utf-8")
    except OSError as err:
        raise SystemExit("Missing root LICENSE file") from err
    if not license_text.startswith("# PolyForm Noncommercial License 1.0.0"):
        raise SystemExit("LICENSE must identify the selected PolyForm Noncommercial License 1.0.0")
    if "Required Notice: Copyright (c) 2026 Shemin A Salam" not in license_text:
        raise SystemExit("LICENSE is missing the author's required copyright notice")

    strings = load_json(COMPONENT / "strings.json")
    english = load_json(COMPONENT / "translations" / "en.json")
    if strings != english:
        raise SystemExit("strings.json and translations/en.json are not identical")
    if "initial_setup" not in strings.get("config", {}).get("step", {}):
        raise SystemExit("Missing first-run choice for local MQTT without AWS certificates")

    bad_files = [p.relative_to(ROOT).as_posix() for p in ROOT.rglob("*") if p.is_file() and (p.suffix == ".pyc" or "__pycache__" in p.parts)]
    if bad_files:
        raise SystemExit(f"Generated Python bytecode must not be committed: {bad_files}")

    print("OverKill Smart Hub package checks passed.")
    print(f"Integration domain: {DOMAIN}")
    print(f"Version: {manifest['version']}")
    print(f"Home Assistant integration folder: {COMPONENT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
