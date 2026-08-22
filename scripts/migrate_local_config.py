#!/usr/bin/env python3
"""Migrate a local Fmage providers.json to the current provider schema."""

from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
from typing import Any


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = Path.home() / ".codex" / "fmage" / "providers.json"
MIDJOURNEY_PROVIDER = "808-MJ"
MIDJOURNEY_TRANSPORT = "808-midjourney"
MIDJOURNEY_MODEL = "midjourney-v8.2"
OPENAI_IMAGES_TRANSPORT = "openai-images"


def resolve_config_path(explicit: str | None = None) -> Path:
    if explicit and explicit.strip():
        return Path(explicit).expanduser().resolve()
    configured = os.environ.get("FMAGE_CONFIG", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    codex_home = os.environ.get("CODEX_HOME", "").strip()
    if codex_home:
        return (Path(codex_home).expanduser() / "fmage" / "providers.json").resolve()
    return DEFAULT_CONFIG_PATH.resolve()


def _is_mapping(value: Any) -> bool:
    return isinstance(value, dict)


def _find_midjourney_source(providers: dict[str, Any]) -> dict[str, Any]:
    direct = providers.get("808-image-2")
    if _is_mapping(direct):
        return direct
    for provider in providers.values():
        if not _is_mapping(provider):
            continue
        if (
            provider.get("transport") == OPENAI_IMAGES_TRANSPORT
            and provider.get("transport_profile") == "808"
        ):
            return provider
    raise ValueError(
        'Cannot create "808-MJ": no existing 808 OpenAI Images provider was found.'
    )


def _normalize_legacy_fields(provider: dict[str, Any], provider_name: str, changes: list[str]) -> None:
    if provider.get("transport") == "808-openai-images":
        provider["transport"] = OPENAI_IMAGES_TRANSPORT
        provider["transport_profile"] = "808"
        changes.append(f'{provider_name}: migrated transport to openai-images + profile 808')

    legacy_fields = [
        field
        for field in ("compatibility", "compatibility_profile")
        if field in provider
    ]
    if legacy_fields:
        provider["prompt_profile"] = "dall-e3"
        for field in legacy_fields:
            provider.pop(field, None)
        changes.append(
            f'{provider_name}: migrated {", ".join(legacy_fields)} to prompt_profile dall-e3'
        )


def migrate_payload(payload: dict[str, Any]) -> list[str]:
    providers = payload.get("providers")
    if not _is_mapping(providers):
        raise ValueError('Fmage configuration must contain an object named "providers".')

    changes: list[str] = []
    for provider_name, provider in providers.items():
        if _is_mapping(provider):
            _normalize_legacy_fields(provider, str(provider_name), changes)

    source = _find_midjourney_source(providers)
    existing = providers.get(MIDJOURNEY_PROVIDER)
    if not _is_mapping(existing):
        migrated = copy.deepcopy(source)
        migrated.pop("transport_profile", None)
        migrated["transport"] = MIDJOURNEY_TRANSPORT
        migrated["model"] = MIDJOURNEY_MODEL
        migrated.setdefault("response_format", "url")
        migrated.setdefault("timeout", 600)
        providers[MIDJOURNEY_PROVIDER] = migrated
        changes.append('added provider "808-MJ" from the configured 808 provider')
    else:
        if existing.get("transport") != MIDJOURNEY_TRANSPORT:
            existing["transport"] = MIDJOURNEY_TRANSPORT
            changes.append('808-MJ: set transport to 808-midjourney')
        if "transport_profile" in existing:
            existing.pop("transport_profile", None)
            changes.append('808-MJ: removed incompatible transport_profile')
        if existing.get("model") != MIDJOURNEY_MODEL:
            existing["model"] = MIDJOURNEY_MODEL
            changes.append('808-MJ: set model to midjourney-v8.2')
        if "response_format" not in existing:
            existing["response_format"] = "url"
            changes.append('808-MJ: defaulted response_format to url')
        if "timeout" not in existing:
            existing["timeout"] = 600
            changes.append('808-MJ: defaulted timeout to 600 seconds')

    active_providers = payload.get("active_providers")
    if isinstance(active_providers, list):
        if MIDJOURNEY_PROVIDER not in active_providers:
            active_providers.append(MIDJOURNEY_PROVIDER)
            changes.append('added "808-MJ" to active_providers')
    elif "active_providers" in payload:
        raise ValueError('Fmage configuration field "active_providers" must be an array.')

    return changes


def migrate_file(path: Path, *, check_only: bool = False) -> list[str]:
    if not path.is_file():
        raise FileNotFoundError(f"Fmage configuration does not exist: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not _is_mapping(payload):
        raise ValueError("Fmage configuration must contain a JSON object.")
    changes = migrate_payload(payload)
    if changes and not check_only:
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return changes


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", help="Path to the local providers.json")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report required migrations without writing the file",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    path = resolve_config_path(args.config)
    changes = migrate_file(path, check_only=args.check)
    if not changes:
        print(f"Fmage local config is compatible: {path}")
        return 0
    action = "would migrate" if args.check else "migrated"
    print(f"Fmage local config {action}: {path}")
    for change in changes:
        print(f"- {change}")
    return 1 if args.check else 0


if __name__ == "__main__":
    raise SystemExit(main())
