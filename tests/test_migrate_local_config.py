from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

from migrate_local_config import migrate_file, migrate_payload


class MigrateLocalConfigTests(unittest.TestCase):
    def test_migrates_ezai_nano_to_native_gemini_protocol(self) -> None:
        payload = {
            "active_providers": ["EzAI-nano", "808-MJ"],
            "providers": {
                "EzAI-nano": {
                    "transport": "ezai-banana-images",
                    "base_url": "https://api-direct.ezaiclub.com",
                    "model": "nano-banana-pro",
                    "response_format": "url",
                    "api_key": "keep-this-secret",
                },
                "808-image-2": {
                    "transport": "openai-images",
                    "transport_profile": "808",
                    "base_url": "https://example.invalid/v1",
                    "model": "gpt-image-2",
                    "api_key": "other-secret",
                },
                "808-MJ": {
                    "transport": "808-midjourney",
                    "base_url": "https://example.invalid/v1",
                    "model": "midjourney-v8.2",
                    "api_key": "other-secret",
                },
            },
        }

        changes = migrate_payload(payload)

        provider = payload["providers"]["EzAI-nano"]
        self.assertEqual(provider["transport"], "gemini-generate-content")
        self.assertEqual(provider["model"], "gemini-3-pro-image")
        self.assertEqual(provider["api_key"], "keep-this-secret")
        self.assertNotIn("response_format", provider)
        self.assertTrue(any("Gemini generateContent" in change for change in changes))

    def test_removes_midjourney_provider_and_active_entry(self) -> None:
        payload = {
            "active_providers": ["808-image-2", "808-MJ"],
            "providers": {
                "808-image-2": {
                    "transport": "openai-images",
                    "transport_profile": "808",
                    "base_url": "https://example.invalid/v1",
                    "model": "gpt-image-2",
                    "response_format": "url",
                    "timeout": 600,
                    "api_key": "keep-this-secret",
                },
                "808-MJ": {
                    "transport": "808-midjourney",
                    "model": "midjourney-v8.2",
                    "api_key": "remove-this-secret",
                },
            },
        }

        changes = migrate_payload(payload)

        self.assertNotIn("808-MJ", payload["providers"])
        self.assertNotIn("808-MJ", payload["active_providers"])
        self.assertTrue(any('removed provider "808-MJ"' in change for change in changes))

    def test_migrates_legacy_transport_and_compatibility_fields(self) -> None:
        payload = {
            "active_providers": ["legacy"],
            "providers": {
                "legacy": {
                    "transport": "808-openai-images",
                    "compatibility": "dall-e3",
                    "base_url": "https://example.invalid/v1",
                    "model": "gpt-image-2",
                    "api_key": "keep-this-secret",
                }
            },
        }

        migrate_payload(payload)

        legacy = payload["providers"]["legacy"]
        self.assertEqual(legacy["transport"], "openai-images")
        self.assertEqual(legacy["transport_profile"], "808")
        self.assertEqual(legacy["prompt_profile"], "dall-e3")
        self.assertNotIn("compatibility", legacy)
        self.assertNotIn("808-MJ", payload["providers"])

    def test_check_mode_does_not_write(self) -> None:
        payload = {
            "active_providers": ["808-image-2", "808-MJ"],
            "providers": {
                "808-image-2": {
                    "transport": "openai-images",
                    "transport_profile": "808",
                    "base_url": "https://example.invalid/v1",
                    "model": "gpt-image-2",
                    "api_key": "keep-this-secret",
                },
                "808-MJ": {"transport": "808-midjourney", "model": "midjourney-v8.2"},
            },
        }
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "providers.json"
            original = copy.deepcopy(payload)
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

            changes = migrate_file(path, check_only=True)

            self.assertTrue(changes)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), original)


if __name__ == "__main__":
    unittest.main()
