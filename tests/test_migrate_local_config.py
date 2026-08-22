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
    def test_adds_midjourney_provider_and_preserves_api_key(self) -> None:
        payload = {
            "active_providers": ["808-image-2"],
            "providers": {
                "808-image-2": {
                    "transport": "openai-images",
                    "transport_profile": "808",
                    "base_url": "https://example.invalid/v1",
                    "model": "gpt-image-2",
                    "response_format": "url",
                    "timeout": 600,
                    "api_key": "keep-this-secret",
                }
            },
        }

        changes = migrate_payload(payload)

        midjourney = payload["providers"]["808-MJ"]
        self.assertEqual(midjourney["transport"], "808-midjourney")
        self.assertEqual(midjourney["model"], "midjourney-v8.2")
        self.assertEqual(midjourney["base_url"], "https://example.invalid/v1")
        self.assertEqual(midjourney["api_key"], "keep-this-secret")
        self.assertNotIn("transport_profile", midjourney)
        self.assertIn("808-MJ", payload["active_providers"])
        self.assertTrue(any('added provider "808-MJ"' in change for change in changes))

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
        self.assertIn("808-MJ", payload["providers"])

    def test_existing_midjourney_provider_keeps_custom_connection_fields(self) -> None:
        payload = {
            "active_providers": ["808-MJ"],
            "providers": {
                "808-image-2": {
                    "transport": "openai-images",
                    "transport_profile": "808",
                    "base_url": "https://source.invalid/v1",
                    "model": "gpt-image-2",
                    "api_key": "source-secret",
                },
                "808-MJ": {
                    "transport": "openai-images",
                    "transport_profile": "808",
                    "base_url": "https://custom.invalid/v1",
                    "model": "old-model",
                    "api_key": "custom-secret",
                },
            },
        }

        migrate_payload(payload)

        midjourney = payload["providers"]["808-MJ"]
        self.assertEqual(midjourney["transport"], "808-midjourney")
        self.assertEqual(midjourney["base_url"], "https://custom.invalid/v1")
        self.assertEqual(midjourney["api_key"], "custom-secret")
        self.assertEqual(midjourney["model"], "midjourney-v8.2")

    def test_check_mode_does_not_write(self) -> None:
        payload = {
            "active_providers": ["808-image-2"],
            "providers": {
                "808-image-2": {
                    "transport": "openai-images",
                    "transport_profile": "808",
                    "base_url": "https://example.invalid/v1",
                    "model": "gpt-image-2",
                    "api_key": "keep-this-secret",
                }
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
