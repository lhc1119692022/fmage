from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
SERVER_PATH = PLUGIN_ROOT / "mcp" / "server.mjs"
sys.path.insert(0, str(SCRIPTS_DIR))

import banana_models
import chat_completions_image_transport as chat_transport
import ezai_banana_transport as transport
import zenmux_vertex_transport as zenmux_transport


PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9WlD7xkAAAAASUVORK5CYII="
)


def png_bytes_with_dimensions(width: int, height: int) -> bytes:
    return PNG_BYTES[:16] + width.to_bytes(4, "big") + height.to_bytes(4, "big") + PNG_BYTES[24:]


def banana_args(
    command: str,
    output_dir: Path,
    *extra: str,
    model: str = "nano-banana-2",
):
    argv = [
        command,
        "--prompt",
        "test image",
        "--base-url",
        "https://api-direct.ezaiclub.com",
        "--model",
        model,
        "--api-key-env",
        "FMAGE_TEST_API_KEY",
        "--output-dir",
        str(output_dir),
        "--timeout",
        "30",
        *extra,
    ]
    return transport.build_parser().parse_args(argv)


def call_server(config: dict[str, object], tool_name: str, arguments: dict[str, object]) -> dict[str, object]:
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = Path(temp_dir) / "providers.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        environment = {
            **os.environ,
            "FMAGE_CONFIG": str(config_path),
            "FMAGE_PYTHON": sys.executable,
        }
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        }
        completed = subprocess.run(
            ["node", str(SERVER_PATH)],
            input=json.dumps(request) + "\n",
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environment,
            timeout=15,
            check=False,
        )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr or completed.stdout)
    response = json.loads(completed.stdout.splitlines()[0])
    if "error" in response:
        raise AssertionError(response["error"])
    return response["result"]["structuredContent"]


class EndpointAndPayloadTests(unittest.TestCase):
    def test_endpoint_adds_v1_once(self) -> None:
        self.assertEqual(
            transport.endpoint("https://api-direct.ezaiclub.com", "generate"),
            "https://api-direct.ezaiclub.com/v1/images/generations",
        )
        self.assertEqual(
            transport.endpoint("https://api-direct.ezaiclub.com/v1", "edit"),
            "https://api-direct.ezaiclub.com/v1/images/edits",
        )

    def test_nano_banana_2_payload_matches_documented_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            args = banana_args(
                "generate",
                Path(temp_dir),
                "--resolution",
                "1k",
                "--aspect",
                "16:9",
                "--response-format",
                "b64_json",
            )
            resolution, aspect, requested_size, notes = transport.resolve_shape(args, [])
            payload = transport.build_payload(args, "prompt", resolution, aspect)

        self.assertEqual(resolution, "1K")
        self.assertEqual(aspect, "16:9")
        self.assertEqual(requested_size, "1K@16:9")
        self.assertIn("resolution_explicit_1k", notes)
        self.assertEqual(
            payload,
            {
                "model": "nano-banana-2",
                "prompt": "prompt",
                "resolution": "1K",
                "aspect_ratio": "16:9",
                "thinking_level": "minimal",
                "n": 1,
                "response_format": "b64_json",
            },
        )

    def test_nano_banana_2_supports_512_extreme_ratio_and_high_thinking(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            args = banana_args(
                "generate",
                Path(temp_dir),
                "--resolution",
                "512px",
                "--aspect",
                "1:8",
                "--thinking-level",
                "high",
            )
            resolution, aspect, requested_size, _ = transport.resolve_shape(args, [])
            payload = transport.build_payload(args, "prompt", resolution, aspect)

        self.assertEqual(requested_size, "512px@1:8")
        self.assertEqual(payload["resolution"], "512px")
        self.assertEqual(payload["aspect_ratio"], "1:8")
        self.assertEqual(payload["thinking_level"], "high")

    def test_nano_banana_pro_omits_thinking_level_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            args = banana_args(
                "generate",
                Path(temp_dir),
                "--resolution",
                "4k",
                "--aspect",
                "9:16",
                model="nano-banana-pro",
            )
            resolution, aspect, _, _ = transport.resolve_shape(args, [])
            payload = transport.build_payload(args, "prompt", resolution, aspect)

        self.assertNotIn("thinking_level", payload)
        self.assertEqual(payload["resolution"], "4K")
        self.assertEqual(payload["aspect_ratio"], "9:16")

    def test_aspect_ratio_tokens_are_not_fraction_reduced(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            explicit = banana_args(
                "generate",
                output_dir,
                "--aspect",
                "21:9",
                model="nano-banana-pro",
            )
            _, explicit_aspect, _, _ = transport.resolve_shape(explicit, [])
            self.assertEqual(explicit_aspect, "21:9")

            unsupported_alias = banana_args(
                "generate",
                output_dir,
                "--aspect",
                "7:3",
                model="nano-banana-pro",
            )
            with self.assertRaisesRegex(ValueError, "does not support aspect ratio"):
                transport.resolve_shape(unsupported_alias, [])

            derived = banana_args(
                "generate",
                output_dir,
                "--size",
                "2100x900",
                model="nano-banana-pro",
            )
            _, derived_aspect, _, _ = transport.resolve_shape(derived, [])
            self.assertEqual(derived_aspect, "21:9")

    def test_nano_banana_pro_rejects_512_extreme_ratio_and_thinking(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            resolution_args = banana_args(
                "generate",
                output_dir,
                "--resolution",
                "512px",
                model="nano-banana-pro",
            )
            with self.assertRaisesRegex(ValueError, "does not support resolution"):
                transport.resolve_shape(resolution_args, [])

            aspect_args = banana_args(
                "generate",
                output_dir,
                "--aspect",
                "8:1",
                model="nano-banana-pro",
            )
            with self.assertRaisesRegex(ValueError, "does not support aspect ratio"):
                transport.resolve_shape(aspect_args, [])

            thinking_args = banana_args(
                "generate",
                output_dir,
                "--thinking-level",
                "high",
                model="nano-banana-pro",
            )
            resolution, aspect, _, _ = transport.resolve_shape(thinking_args, [])
            with self.assertRaisesRegex(ValueError, "does not support thinking_level"):
                transport.build_payload(thinking_args, "prompt", resolution, aspect)

    def test_exact_size_is_mapped_to_provider_tier_and_aspect(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            args = banana_args("generate", Path(temp_dir), "--size", "3072x2048")
            resolution, aspect, requested_size, notes = transport.resolve_shape(args, [])

        self.assertEqual(resolution, "4K")
        self.assertEqual(aspect, "3:2")
        self.assertEqual(requested_size, "3072x2048")
        self.assertIn("explicit_size_mapped_to_4k_tier", notes)

    def test_nearest_supported_aspect_maps_1_08_to_square(self) -> None:
        self.assertEqual(
            banana_models.nearest_aspect_ratio(
                "ezai-banana-images",
                "nano-banana-pro",
                1296 / 1200,
            ),
            "1:1",
        )

    def test_explicit_three_k_tier_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            args = banana_args("generate", Path(temp_dir), "--resolution", "3k")
            with self.assertRaisesRegex(ValueError, "does not support resolution"):
                transport.resolve_shape(args, [])


class BananaModelRuleTests(unittest.TestCase):
    def test_provider_wire_models_share_capability_without_being_interchangeable(self) -> None:
        ezai = banana_models.resolve_model(transport.TRANSPORT_NAME, "nano-banana-2")
        chat = banana_models.resolve_model(
            chat_transport.TRANSPORT_NAME,
            "gemini-3.1-flash-image-preview",
        )
        zenmux = banana_models.resolve_model(
            zenmux_transport.TRANSPORT_NAME,
            "google/gemini-3.1-flash-image",
        )

        self.assertEqual(ezai["canonical_model"], "nano-banana-2")
        self.assertEqual(chat["canonical_model"], "nano-banana-2")
        self.assertEqual(zenmux["canonical_model"], "nano-banana-2")
        self.assertEqual(ezai["wire_model"], "nano-banana-2")
        self.assertEqual(chat["wire_model"], "gemini-3.1-flash-image-preview")
        self.assertEqual(zenmux["wire_model"], "google/gemini-3.1-flash-image")
        for key in (
            "resolutions",
            "aspect_ratios",
            "thinking_levels",
            "default_thinking_level",
        ):
            self.assertEqual(chat[key], ezai[key])
            self.assertEqual(zenmux[key], ezai[key])

    def test_provider_wire_model_ids_cannot_cross_contracts(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot be used with 'chat-completions-image'"):
            banana_models.resolve_model(chat_transport.TRANSPORT_NAME, "nano-banana-2")
        with self.assertRaisesRegex(ValueError, "cannot be used with 'zenmux-vertex'"):
            banana_models.resolve_model(
                zenmux_transport.TRANSPORT_NAME,
                "gemini-3.1-flash-image-preview",
            )

    def test_chat_transport_uses_shared_nano_banana_2_shape_rules(self) -> None:
        args = chat_transport.build_parser().parse_args(
            [
                "generate",
                "--prompt",
                "test",
                "--base-url",
                "https://example.com",
                "--model",
                "gemini-3.1-flash-image-preview",
                "--resolution",
                "512px",
                "--aspect",
                "1:8",
                "--dry-run",
            ]
        )

        result = chat_transport.run_generate(args)

        self.assertEqual(result["requested_size"], "1:8@512px")
        self.assertEqual(result["request"]["image_size"], "512")
        self.assertEqual(result["request"]["image_config"]["aspect_ratio"], "1:8")

    def test_chat_transport_rejects_ezai_wire_model_id(self) -> None:
        args = chat_transport.build_parser().parse_args(
            [
                "generate",
                "--prompt",
                "test",
                "--base-url",
                "https://example.com",
                "--model",
                "nano-banana-2",
                "--dry-run",
            ]
        )

        with self.assertRaisesRegex(ValueError, "cannot be used with 'chat-completions-image'"):
            chat_transport.run_generate(args)

    def test_zenmux_transport_preserves_its_wire_model_and_nano_tokens(self) -> None:
        args = zenmux_transport.build_parser().parse_args(
            [
                "generate",
                "--prompt",
                "test",
                "--base-url",
                "https://zenmux.ai/api/vertex-ai",
                "--model",
                "google/gemini-3.1-flash-image",
                "--resolution",
                "512px",
                "--aspect",
                "21:9",
                "--dry-run",
            ]
        )

        result = zenmux_transport.run_generate(args)
        parameters = result["request"]["parameters"]

        self.assertEqual(parameters["aspectRatio"], "21:9")
        self.assertEqual(parameters["sampleImageSize"], "512")
        self.assertIn("/publishers/google/models/gemini-3.1-flash-image:predict", result["endpoint"])

    def test_512_wire_serialization_is_scoped_to_each_nano_contract(self) -> None:
        self.assertEqual(
            chat_transport.chat_resolution_value("gemini-3.1-flash-image-preview", "512px"),
            "512",
        )
        self.assertEqual(
            chat_transport.chat_resolution_value("custom-chat-image-model", "512px"),
            "512px",
        )
        self.assertEqual(
            zenmux_transport.zenmux_resolution_value("google/gemini-3.1-flash-image", "512px"),
            "512",
        )
        self.assertEqual(
            zenmux_transport.zenmux_resolution_value("vendor/custom-image-model", "512px"),
            "512px",
        )

    def test_ezai_transport_does_not_import_image_series_transport(self) -> None:
        source = (SCRIPTS_DIR / "ezai_banana_transport.py").read_text(encoding="utf-8")
        self.assertNotIn("openai_images_transport", source)


class EditInputModeTests(unittest.TestCase):
    def test_url_edit_uses_json_image_urls(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            args = banana_args(
                "edit",
                Path(temp_dir),
                "--image",
                "https://example.com/input.png",
                "--resolution",
                "2k",
                "--aspect",
                "16:9",
                "--dry-run",
            )
            result = transport.run_edit(args)

        self.assertEqual(result["request_mode"], "json")
        self.assertEqual(result["endpoint"], "https://api-direct.ezaiclub.com/v1/images/edits")
        self.assertEqual(result["request"]["image_urls"], ["https://example.com/input.png"])
        self.assertEqual(result["request_headers"]["Idempotency-Key"], "[generated-per-request]")

    def test_local_edit_uses_multipart_image_fields_and_idempotency(self) -> None:
        captured: dict[str, object] = {}

        def multipart(url, fields, files, api_key, timeout, extra_headers=None):
            captured.update(
                {
                    "url": url,
                    "fields": fields,
                    "files": files,
                    "api_key": api_key,
                    "timeout": timeout,
                    "extra_headers": extra_headers,
                }
            )
            return {"data": [{"b64_json": base64.b64encode(PNG_BYTES).decode("ascii")}]}

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "outputs"
            first = Path(temp_dir) / "first.png"
            second = Path(temp_dir) / "second.png"
            first.write_bytes(PNG_BYTES)
            second.write_bytes(PNG_BYTES)
            args = banana_args(
                "edit",
                output_dir,
                "--image",
                str(first),
                "--image",
                str(second),
                "--idempotency-key",
                "stable-edit-key",
            )
            with (
                mock.patch.dict(os.environ, {"FMAGE_TEST_API_KEY": "secret"}),
                mock.patch.object(transport.support, "multipart_request", side_effect=multipart),
            ):
                result = transport.run_edit(args)

        self.assertEqual(captured["url"], "https://api-direct.ezaiclub.com/v1/images/edits")
        self.assertEqual([field for field, _ in captured["files"]], ["image", "image"])
        self.assertEqual(captured["extra_headers"], {"Idempotency-Key": "stable-edit-key"})
        self.assertEqual(result["request_mode"], "multipart")
        self.assertEqual(len(result["images"]), 1)

    def test_unsupported_reference_aspect_uses_nearest_ratio_and_outpainting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            reference = Path(temp_dir) / "input.png"
            reference.write_bytes(png_bytes_with_dimensions(1296, 1200))
            args = banana_args(
                "edit",
                Path(temp_dir) / "outputs",
                "--image",
                str(reference),
                "--resolution",
                "4k",
                "--dry-run",
                model="nano-banana-pro",
            )
            result = transport.run_edit(args)

        self.assertEqual(result["requested_aspect_ratio"], "1:1")
        self.assertEqual(result["requested_size"], "4K@1:1")
        self.assertIn(transport.REFERENCE_ASPECT_REMAPPED_NOTE, result["notes"])
        self.assertIn(transport.REFERENCE_OUTPAINT_NOTE, result["notes"])
        self.assertIn("reference_dimensions_1296x1200", result["notes"])
        prompt = result["request"]["prompt"]
        self.assertTrue(prompt.startswith("test image"))
        self.assertIn("by outpainting only", prompt)
        self.assertIn("Preserve all existing visible content", prompt)
        self.assertIn("Do not crop, stretch, squeeze", prompt)

    def test_mixed_url_and_local_edit_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            local = Path(temp_dir) / "input.png"
            local.write_bytes(PNG_BYTES)
            args = banana_args(
                "edit",
                Path(temp_dir) / "outputs",
                "--image",
                str(local),
                "--image",
                "https://example.com/input.png",
                "--dry-run",
            )
            with self.assertRaisesRegex(ValueError, "cannot mix URL and local-file"):
                transport.run_edit(args)


class ServerRoutingTests(unittest.TestCase):
    def config(self, model: str = "nano-banana-2") -> dict[str, object]:
        return {
            "active_providers": ["ezai-banana"],
            "output_dir": "outputs",
            "cache_dir": "cache",
            "providers": {
                "ezai-banana": {
                    "transport": "ezai-banana-images",
                    "base_url": "https://api-direct.ezaiclub.com",
                    "model": model,
                    "response_format": "b64_json",
                    "api_key": "",
                }
            },
        }

    def test_server_routes_generate_to_ezai_banana_transport(self) -> None:
        result = call_server(
            self.config(),
            "generate_image",
            {
                "provider": "ezai-banana",
                "prompt": "test",
                "resolution": "1k",
                "aspect": "16:9",
                "dry_run": True,
                "verbose": True,
            },
        )

        self.assertEqual(result["provider_transport"], "ezai-banana-images")
        self.assertEqual(result["endpoint"], "https://api-direct.ezaiclub.com/v1/images/generations")
        self.assertEqual(result["request"]["response_format"], "b64_json")
        self.assertEqual(result["request"]["thinking_level"], "minimal")
        self.assertEqual(result["request"]["aspect_ratio"], "16:9")

    def test_server_passes_high_thinking_for_nano_banana_2(self) -> None:
        result = call_server(
            self.config(),
            "generate_image",
            {
                "provider": "ezai-banana",
                "prompt": "test",
                "resolution": "2k",
                "aspect": "4:1",
                "thinking_level": "high",
                "dry_run": True,
                "verbose": True,
            },
        )

        self.assertEqual(result["request"]["thinking_level"], "high")
        self.assertEqual(result["request"]["aspect_ratio"], "4:1")

    def test_same_provider_name_switches_to_nano_banana_pro_by_model(self) -> None:
        result = call_server(
            self.config("nano-banana-pro"),
            "generate_image",
            {
                "provider": "ezai-banana",
                "prompt": "test",
                "resolution": "4k",
                "aspect": "21:9",
                "dry_run": True,
                "verbose": True,
            },
        )

        self.assertEqual(result["request"]["model"], "nano-banana-pro")
        self.assertEqual(result["request"]["aspect_ratio"], "21:9")
        self.assertNotIn("thinking_level", result["request"])

    def test_custom_provider_name_does_not_define_the_model(self) -> None:
        config = self.config("nano-banana-pro")
        provider = config["providers"].pop("ezai-banana")
        config["providers"]["ezai-studio"] = provider
        config["active_providers"] = ["ezai-studio"]

        result = call_server(
            config,
            "generate_image",
            {
                "provider": "ezai-studio",
                "prompt": "test",
                "resolution": "2k",
                "aspect": "1:1",
                "dry_run": True,
                "verbose": True,
            },
        )

        self.assertEqual(result["provider"], "ezai-studio")
        self.assertEqual(result["request"]["model"], "nano-banana-pro")

    def test_server_routes_url_edit_to_image_urls(self) -> None:
        result = call_server(
            self.config(),
            "edit_image",
            {
                "provider": "ezai-banana",
                "prompt": "edit",
                "images": ["https://example.com/input.png"],
                "dry_run": True,
                "verbose": True,
            },
        )

        self.assertEqual(result["request_mode"], "json")
        self.assertEqual(result["request"]["image_urls"], ["https://example.com/input.png"])

    def test_provider_status_reports_transport_configuration_only(self) -> None:
        status = call_server(
            self.config(),
            "get_provider_status",
            {"provider": "ezai-banana"},
        )

        self.assertEqual(status["response_format"], "b64_json")
        self.assertEqual(status["edit_input_modes"], ["json_image_urls", "multipart_local_files"])
        self.assertNotIn("banana_model_spec", status)
        self.assertNotIn("banana_model_capabilities", status)

    def test_server_status_does_not_interpret_chat_transport_model_capabilities(self) -> None:
        config = {
            "active_providers": ["right-banana"],
            "providers": {
                "right-banana": {
                    "transport": "chat-completions-image",
                    "base_url": "https://www.rightapi.ai/draw",
                    "model": "gemini-3.1-flash-image-preview",
                    "api_key": "",
                }
            },
        }

        status = call_server(config, "get_provider_status", {"provider": "right-banana"})

        self.assertEqual(status["model"], "gemini-3.1-flash-image-preview")
        self.assertNotIn("banana_model_capabilities", status)


if __name__ == "__main__":
    unittest.main()
