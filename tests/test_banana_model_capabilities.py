from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import banana_models
import gemini_generate_content_transport as gemini
import json_images_transport as images
from test_gemini_generate_content_transport import call_server, PNG_BYTES
from migrate_local_config import migrate_payload


class BananaCapabilitiesTests(unittest.TestCase):
    def test_only_three_families_and_transport_independent_aliases(self):
        self.assertEqual(set(banana_models.MODEL_CAPABILITIES), {"nano-banana-pro", "nano-banana-2", "nano-banana-2.1"})
        for protocol in ("json-images", "gemini-generate-content", "openai-images"):
            for wire_id, canonical in banana_models.MODEL_ALIASES.items():
                model = banana_models.resolve_model(protocol, wire_id)
                self.assertEqual(model["canonical_model"], canonical)
                self.assertEqual(model["wire_model"], wire_id)
            for retired in ("gemini-2.5-flash-image", "gemini-3.1-flash-lite-image", "nano-banana"):
                with self.assertRaises(ValueError):
                    banana_models.resolve_model(protocol, retired)

    def test_21_capabilities_do_not_inherit_512px_from_2(self):
        for model in ("gemini-nano-banana-2.1", "nano-banana-2.1"):
            for protocol in ("json-images", "gemini-generate-content"):
                self.assertEqual(banana_models.resolve_thinking_level(protocol, model, None), "medium")
                for level in ("minimal", "medium", "high"):
                    self.assertEqual(banana_models.resolve_thinking_level(protocol, model, level), level)
                for resolution in ("1K", "2K", "4K"):
                    self.assertEqual(banana_models.validate_resolution(protocol, model, resolution), resolution)
                with self.assertRaisesRegex(ValueError, "does not support resolution"):
                    banana_models.validate_resolution(protocol, model, "512px")
                with self.assertRaises(ValueError):
                    banana_models.resolve_thinking_level(protocol, model, "low")
                self.assertEqual(banana_models.validate_aspect(protocol, model, "8:1"), "8:1")

    def test_21_generate_and_edit_wire_ids_and_thinking(self):
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / "input.png"
            image.write_bytes(PNG_BYTES)
            for model in ("gemini-nano-banana-2.1", "nano-banana-2.1"):
                for module in (gemini, images):
                    for command in ("generate", "edit"):
                        args = module.build_parser().parse_args([
                            command, "--base-url", "https://relay.invalid/v1", "--model", model,
                            "--prompt", "preserve prompt", "--resolution", "4K", "--aspect", "8:1",
                            "--thinking-level", "medium", "--output-dir", folder, "--dry-run",
                            *(["--image", str(image)] if command == "edit" else []),
                        ])
                        if module is gemini:
                            result = module.run_request(args, [str(image)] if command == "edit" else [])
                            self.assertIn("/v1/models/" + model + ":generateContent", result["endpoint"])
                            self.assertEqual(result["request"]["generationConfig"]["thinkingConfig"]["thinkingLevel"], "MEDIUM")
                        else:
                            result = module.run_edit(args) if command == "edit" else module.run_generate(args)
                            self.assertEqual(result["request"]["model"], model)
                            self.assertEqual(result["request"]["thinking_level"], "medium")
                        self.assertIsNone(result["timeout_seconds"])

    def test_mcp_v1_response_format_and_aliases(self):
        for model in ("gemini-nano-banana-2.1", "nano-banana-2.1"):
            config = {"active_providers": ["neutral"], "providers": {"neutral": {
                "transport": "gemini-generate-content", "base_url": "https://relay.invalid/v1",
                "model": model, "auth_scheme": "bearer", "generation_config_format": "response-format",
            }}}
            result = call_server(config, "generate_image", {"prompt": "sample", "dry_run": True, "verbose": True,
                "thinking_level": "medium", "thinking_level_user_requested": True})
            generation = result["request"]["generation_config"]
            self.assertIn("responseFormat", generation)
            self.assertNotIn("imageConfig", generation)
            self.assertEqual(generation["thinkingConfig"]["thinkingLevel"], "MEDIUM")
            self.assertTrue(result["endpoint"].endswith(model + ":generateContent"))
            self.assertIsNone(result["timeout_seconds"])

    def test_legacy_gemini_auth_migration_preserves_alias_and_credentials(self):
        payload = {"providers": {"arbitrary": {"transport": "gemini-generate-content",
            "transport_profile": "808", "model": "nano-banana-2.1", "timeout": 600,
            "api_key_env": "TEST_ONLY", "base_url": "https://relay.invalid"}}}
        migrate_payload(payload)
        provider = payload["providers"]["arbitrary"]
        self.assertEqual(provider["auth_scheme"], "bearer")
        self.assertEqual(provider["model"], "nano-banana-2.1")
        self.assertEqual(provider["api_key_env"], "TEST_ONLY")
        self.assertNotIn("timeout", provider)
        self.assertNotIn("transport_profile", provider)
        self.assertEqual(migrate_payload(payload), [])


if __name__ == "__main__":
    unittest.main()
