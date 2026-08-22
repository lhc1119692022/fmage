#!/usr/bin/env python3
"""Migrate a local Fmage providers.json to the current provider schema."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = Path.home() / ".codex" / "fmage" / "providers.json"
OPENAI_IMAGES_TRANSPORT = "openai-images"
LEGACY_EZAI_NANO_TRANSPORT = "ezai-banana-images"
GEMINI_GENERATE_CONTENT_TRANSPORT = "gemini-generate-content"
EZAI_NANO_MODELS = {
    "nano-banana-2": "gemini-3.1-flash-image",
    "nano-banana-pro": "gemini-3-pro-image",
}


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

    model = provider.get("model")
    if provider.get("transport") == LEGACY_EZAI_NANO_TRANSPORT and model in EZAI_NANO_MODELS:
        provider["transport"] = GEMINI_GENERATE_CONTENT_TRANSPORT
        provider["model"] = EZAI_NANO_MODELS[model]
        provider.pop("response_format", None)
        changes.append(
            f'{provider_name}: migrated EzAI Nano to Gemini generateContent model {provider["model"]}'
        )


def migrate_payload(payload: dict[str, Any]) -> list[str]:
    providers = payload.get("providers")
    if not _is_mapping(providers):
        raise ValueError('Fmage configuration must contain an object named "providers".')

    changes: list[str] = []
    for provider_name, provider in providers.items():
        if _is_mapping(provider):
            _normalize_legacy_fields(provider, str(provider_name), changes)

    if providers.pop("808-MJ", None) is not None:
        changes.append('removed provider "808-MJ"')

    active_providers = payload.get("active_providers")
    if isinstance(active_providers, list):
        if "808-MJ" in active_providers:
            payload["active_providers"] = [name for name in active_providers if name != "808-MJ"]
            changes.append('removed "808-MJ" from active_providers')
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
