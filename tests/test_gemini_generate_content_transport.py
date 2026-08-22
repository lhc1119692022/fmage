from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


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


if __name__ == "__main__":
    unittest.main()
