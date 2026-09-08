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

import gemini_generate_content_transport as transport


PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9WlD7xkAAAAASUVORK5CYII="
)


def arguments(command: str, output_dir: Path, *extra: str, model: str = "gemini-3.1-flash-image"):
    argv = [
        command,
        "--prompt",
        "test image",
        "--base-url",
        "https://api-direct.ezaiclub.com",
        "--model",
        model,
        "--output-dir",
        str(output_dir),
        *extra,
    ]
    return transport.build_parser().parse_args(argv)


def call_server(config: dict[str, object], tool_name: str, tool_arguments: dict[str, object]) -> dict[str, object]:
    with tempfile.TemporaryDirectory() as temporary_directory:
        config_path = Path(temporary_directory) / "providers.json"
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
            "params": {"name": tool_name, "arguments": tool_arguments},
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
    def test_preview_aliases_preserve_wire_ids_and_share_capabilities(self) -> None:
        for original, canonical in (
            ("gemini-3.1-flash-image", "nano-banana-2"),
            ("gemini-3-pro-image", "nano-banana-pro"),
        ):
            with self.subTest(model=original):
                preview = original + "-preview"
                expected = transport.banana_models.resolve_model(transport.TRANSPORT_NAME, original)
                actual = transport.banana_models.resolve_model(transport.TRANSPORT_NAME, preview)
                self.assertEqual(actual["wire_model"], preview)
                self.assertEqual(actual["canonical_model"], canonical)
                self.assertEqual(
                    {key: value for key, value in actual.items() if key != "wire_model"},
                    {key: value for key, value in expected.items() if key != "wire_model"},
                )
                with tempfile.TemporaryDirectory() as directory:
                    args = arguments("generate", Path(directory), "--dry-run", model=preview)
                    result = transport.run_request(args, [])
                    self.assertTrue(result["endpoint"].endswith(f"/{preview}:generateContent"))
                    args.resolution = "512px"
                    if canonical == "nano-banana-pro":
                        with self.assertRaisesRegex(ValueError, "does not support resolution"):
                            transport.resolve_shape(args, [])
                    else:
                        self.assertEqual(transport.resolve_shape(args, [])[1], "512px")

    def test_unknown_model_is_still_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            args = arguments("generate", Path(directory), "--dry-run", model="unknown-image")
            with self.assertRaisesRegex(ValueError, "accepts only"):
                transport.run_request(args, [])

    def test_http_authentication_headers(self) -> None:
        for scheme in ("x-goog-api-key", "bearer"):
            with self.subTest(scheme=scheme), mock.patch.object(transport.urllib.request, "urlopen") as opening:
                opening.return_value.__enter__.return_value.read.return_value = b'{}'
                transport.json_request("https://api.808relay.com/test", {}, "dummy-test-key", 30, scheme)
                request = opening.call_args.args[0]
                headers = {key.lower(): value for key, value in request.header_items()}
                if scheme == "bearer":
                    self.assertEqual(headers["authorization"], "Bearer dummy-test-key")
                    self.assertNotIn("x-goog-api-key", headers)
                else:
                    self.assertEqual(headers["x-goog-api-key"], "dummy-test-key")
                    self.assertNotIn("authorization", headers)

    def test_run_request_forwards_authentication_scheme(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            args = arguments("generate", Path(directory), "--auth-scheme", "bearer")
            with mock.patch.object(transport, "api_key", return_value="dummy-test-key"), mock.patch.object(
                transport, "json_request", side_effect=RuntimeError("stop before saving")
            ) as request:
                with self.assertRaisesRegex(RuntimeError, "stop before saving"):
                    transport.run_request(args, [])
            self.assertEqual(request.call_args.args[-1], "bearer")

    def test_endpoint_uses_native_v1beta_generate_content(self) -> None:
        self.assertEqual(
            transport.generate_content_endpoint(
                "https://api-direct.ezaiclub.com",
                "gemini-3.1-flash-image",
            ),
            "https://api-direct.ezaiclub.com/v1beta/models/gemini-3.1-flash-image:generateContent",
        )
        self.assertEqual(
            transport.generate_content_endpoint(
                "https://api-direct.ezaiclub.com/v1beta",
                "models/gemini-3-pro-image",
            ),
            "https://api-direct.ezaiclub.com/v1beta/models/gemini-3-pro-image:generateContent",
        )
        self.assertEqual(
            transport.generate_content_endpoint(
                "https://api-direct.ezaiclub.com/v1",
                "gemini-3-pro-image",
            ),
            "https://api-direct.ezaiclub.com/v1beta/models/gemini-3-pro-image:generateContent",
        )

    def test_native_aspect_tokens_are_not_fraction_reduced(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            args = arguments(
                "generate",
                Path(temporary_directory),
                "--aspect",
                "21:9",
            )
            aspect, _, _, _ = transport.resolve_shape(args, [])

        self.assertEqual(aspect, "21:9")

    def test_generate_payload_uses_contents_and_image_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            args = arguments(
                "generate",
                Path(temporary_directory),
                "--resolution",
                "1k",
                "--aspect",
                "16:9",
                "--thinking-level",
                "high",
            )
            aspect, resolution, _, _ = transport.resolve_shape(args, [])
            payload = transport.build_payload(args, "test image", [], aspect, resolution)

        self.assertEqual(payload["contents"], [{"role": "user", "parts": [{"text": "test image"}]}])
        self.assertEqual(
            payload["generationConfig"],
            {
                "responseModalities": ["TEXT", "IMAGE"],
                "imageConfig": {"aspectRatio": "16:9", "imageSize": "1K"},
                "thinkingConfig": {"thinkingLevel": "HIGH"},
            },
        )

    def test_edit_payload_embeds_local_reference_as_inline_data(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            image = Path(temporary_directory) / "reference.png"
            image.write_bytes(PNG_BYTES)
            args = arguments(
                "edit",
                Path(temporary_directory) / "outputs",
                "--image",
                str(image),
                "--aspect",
                "1:1",
            )
            aspect, resolution, _, _ = transport.resolve_shape(args, [str(image)])
            payload = transport.build_payload(args, "edit image", [str(image)], aspect, resolution)

        parts = payload["contents"][0]["parts"]
        self.assertEqual(parts[0], {"text": "edit image"})
        self.assertEqual(parts[1]["inlineData"]["mimeType"], "image/png")
        self.assertEqual(base64.b64decode(parts[1]["inlineData"]["data"]), PNG_BYTES)
        self.assertNotIn("instances", payload)
        self.assertNotIn("parameters", payload)

    def test_candidate_inline_data_is_normalized_for_common_saver(self) -> None:
        response = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "done"},
                            {"inlineData": {"mimeType": "image/png", "data": base64.b64encode(PNG_BYTES).decode("ascii")}},
                        ]
                    },
                    "finishReason": "STOP",
                }
            ]
        }

        normalized = transport.data_response_from_candidates(response)

        self.assertEqual(len(normalized["data"]), 1)
        self.assertEqual(normalized["data"][0]["mimeType"], "image/png")
        self.assertEqual(base64.b64decode(normalized["data"][0]["b64_json"]), PNG_BYTES)

    def test_common_prompt_provenance_reads_native_contents(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            args = arguments("generate", Path(temporary_directory))
            aspect, resolution, _, _ = transport.resolve_shape(args, [])
            payload = transport.build_payload(args, "native prompt", [], aspect, resolution)

        from transport_common import request_prompt

        self.assertEqual(request_prompt(payload), "native prompt")


class ServerRoutingTests(unittest.TestCase):
    def config(self, model: str = "gemini-3.1-flash-image") -> dict[str, object]:
        return {
            "active_providers": ["ezai-nano"],
            "output_dir": "outputs",
            "cache_dir": "cache",
            "providers": {
                "ezai-nano": {
                    "transport": "gemini-generate-content",
                    "base_url": "https://api-direct.ezaiclub.com",
                    "model": model,
                    "api_key": "",
                }
            },
        }

    def test_generate_routes_to_native_gemini_transport(self) -> None:
        result = call_server(
            self.config(),
            "generate_image",
            {
                "provider": "ezai-nano",
                "prompt": "test image",
                "resolution": "1k",
                "resolution_user_requested": True,
                "aspect": "16:9",
                "dry_run": True,
                "verbose": True,
            },
        )

        self.assertEqual(result["provider_transport"], "gemini-generate-content")
        self.assertEqual(result["provider_model"], "gemini-3.1-flash-image")
        self.assertEqual(
            result["endpoint"],
            "https://api-direct.ezaiclub.com/v1beta/models/gemini-3.1-flash-image:generateContent",
        )
        self.assertEqual(result["request"]["prompt"], "test image")
        self.assertEqual(result["request"]["generation_config"]["imageConfig"]["aspectRatio"], "16:9")

    def test_edit_routes_reference_into_native_parts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            image = Path(temporary_directory) / "reference.png"
            image.write_bytes(PNG_BYTES)
            result = call_server(
                self.config("gemini-3-pro-image"),
                "edit_image",
                {
                    "provider": "ezai-nano",
                    "prompt": "edit image",
                    "images": [str(image)],
                    "dry_run": True,
                    "verbose": True,
                },
            )

        self.assertEqual(result["provider_transport"], "gemini-generate-content")
        self.assertEqual(result["provider_model"], "gemini-3-pro-image")
        self.assertEqual(result["request"]["prompt"], "edit image")

    def test_808_preview_model_uses_nano_banana_2_capabilities(self) -> None:
        result = call_server(
            {
                "active_providers": ["808-nano"],
                "providers": {
                    "808-nano": {
                        "transport": "gemini-generate-content",
                        "base_url": "https://api.808relay.com",
                        "model": "gemini-3.1-flash-image-preview",
                        "transport_profile": "808",
                        "timeout": 600,
                        "api_key": "",
                    }
                },
            },
            "generate_image",
            {
                "provider": "808-nano",
                "prompt": "test image",
                "resolution": "2k",
                "resolution_user_requested": True,
                "aspect": "16:9",
                "dry_run": True,
                "verbose": True,
            },
        )

        self.assertEqual(result["provider_model"], "gemini-3.1-flash-image-preview")
        self.assertEqual(
            result["endpoint"],
            "https://api.808relay.com/v1beta/models/gemini-3.1-flash-image-preview:generateContent",
        )
        self.assertEqual(result["request"]["generation_config"]["imageConfig"]["imageSize"], "2K")


if __name__ == "__main__":
    unittest.main()
