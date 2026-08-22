from __future__ import annotations

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

import midjourney_808_transport as transport


def transport_args(output_dir: Path, *extra: str):
    argv = [
        "generate",
        "--prompt",
        "https://images.example/reference.png vibrant California poppies --raw --ar 3:2 --q 250",
        "--base-url",
        "https://images808.example/v1",
        "--model",
        "midjourney-v8.2",
        "--api-key-env",
        "FMAGE_TEST_API_KEY",
        "--output-dir",
        str(output_dir),
        "--timeout",
        "30",
        "--pending-total-timeout",
        "30",
        "--poll-interval",
        "1",
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


class PromptAssemblyTests(unittest.TestCase):
    def test_transport_does_not_depend_on_openai_808_transport(self) -> None:
        source = (SCRIPTS_DIR / "midjourney_808_transport.py").read_text(encoding="utf-8")
        self.assertNotIn("openai_images_808_transport", source)

    def test_preserves_reference_url_and_inline_directives(self) -> None:
        prompt = "https://images.example/reference.png vibrant California poppies --raw --ar 3:2 --q 250"
        self.assertEqual(
            transport.build_prompt(prompt, "16:9", "--seed 123"),
            "https://images.example/reference.png vibrant California poppies "
            "--ar 16:9 --raw --ar 3:2 --q 250 --seed 123",
        )

    def test_newlines_become_spaces_without_rewriting_quoted_values(self) -> None:
        prompt = 'vibrant poppies --style "high contrast\nfilm" --raw'
        self.assertEqual(
            transport.build_prompt(prompt),
            'vibrant poppies --style "high contrast film" --raw',
        )

    def test_generic_image_fields_are_not_sent(self) -> None:
        args = transport_args(
            Path("outputs"),
            "--aspect",
            "3:2",
            "--midjourney-parameters",
            "--q 250 --seed 17",
            "--size",
            "4096x4096",
            "--resolution",
            "4k",
            "--quality",
            "high",
        )
        payload = transport.common_payload(args, args.prompt)
        self.assertEqual(
            payload,
            {
                "model": "midjourney-v8.2",
                "prompt": "https://images.example/reference.png vibrant California poppies "
                "--ar 3:2 --raw --ar 3:2 --q 250 --q 250 --seed 17",
                "response_format": "url",
            },
        )


class GenerationTests(unittest.TestCase):
    def test_one_async_submission_returns_fixed_four_result_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            args = transport_args(output_dir)

            def save_images(_response, run_dir, _output_format, _timeout):
                paths = []
                for index in range(1, 5):
                    path = run_dir / f"image_{index}.png"
                    path.write_bytes(b"image")
                    paths.append(path)
                return paths

            with (
                mock.patch.dict(os.environ, {"FMAGE_TEST_API_KEY": "secret"}),
                mock.patch.object(
                    transport.openai,
                    "json_request",
                    return_value={"data": [{"url": f"https://images.example/{index}"} for index in range(4)]},
                ) as request,
                mock.patch.object(transport.openai, "save_response_images", side_effect=save_images),
                mock.patch.object(
                    transport.openai,
                    "collect_image_metadata",
                    return_value=[{"width": 1024, "height": 1024} for _ in range(4)],
                ),
            ):
                result = transport.run_generate(args)

            request.assert_called_once_with(
                "https://images808.example/v1/images/generations?async=true",
                {
                    "model": "midjourney-v8.2",
                    "prompt": "https://images.example/reference.png vibrant California poppies "
                    "--raw --ar 3:2 --q 250",
                    "response_format": "url",
                },
                "secret",
                30,
            )
            self.assertEqual(len(result["images"]), 4)
            self.assertEqual(result["fixed_result_count"], 4)
            self.assertIsNone(result["requested_size"])
            manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
            self.assertEqual(manifest["transport"], "808-midjourney")
            self.assertEqual(manifest["fixed_result_count"], 4)


class ServerRoutingTests(unittest.TestCase):
    def config(self) -> dict[str, object]:
        return {
            "active_providers": ["808-MJ"],
            "output_dir": "outputs",
            "cache_dir": "cache",
            "providers": {
                "808-MJ": {
                    "transport": "808-midjourney",
                    "base_url": "https://images808.example/v1",
                    "model": "midjourney-v8.2",
                    "response_format": "url",
                    "timeout": 321,
                    "api_key": "",
                }
            },
        }

    def test_generate_dry_run_omits_generic_image_parameters(self) -> None:
        result = call_server(
            self.config(),
            "generate_image",
            {
                "provider": "808-MJ",
                "prompt": "vibrant California poppies --raw --ar 3:2",
                "aspect": "16:9",
                "size": "4096x4096",
                "resolution": "4k",
                "quality": "high",
                "midjourney_parameters": "--q 250",
                "dry_run": True,
            },
        )
        self.assertEqual(result["provider_transport"], "808-midjourney")
        self.assertEqual(result["fixed_result_count"], 4)
        self.assertEqual(result["request"]["model"], "midjourney-v8.2")
        self.assertNotIn("size", result["request"])
        self.assertNotIn("resolution", result["request"])
        self.assertNotIn("quality", result["request"])
        self.assertEqual(
            result["request"]["prompt"],
            "vibrant California poppies --ar 16:9 --raw --ar 3:2 --q 250",
        )

    def test_edit_and_batch_entries_redirect_to_one_generate_call(self) -> None:
        edit = call_server(
            self.config(),
            "edit_image",
            {
                "provider": "808-MJ",
                "prompt": "vibrant California poppies",
                "images": ["C:/not-used/reference.png"],
                "dry_run": True,
            },
        )
        batch = call_server(
            self.config(),
            "generate_image_batch",
            {
                "provider": "808-MJ",
                "jobs": [{"prompt": "vibrant California poppies --raw"}],
                "dry_run": True,
            },
        )
        self.assertEqual(edit["request"]["model"], "midjourney-v8.2")
        self.assertEqual(edit["request"]["prompt"], "vibrant California poppies")
        self.assertEqual(edit["notes"][-1], "midjourney_edit_redirected_to_generate")
        self.assertEqual(batch["request"]["prompt"], "vibrant California poppies --raw")
        self.assertEqual(batch["notes"][-1], "midjourney_batch_redirected_to_generate")

    def test_provider_status_reports_fixed_async_contract(self) -> None:
        status = call_server(
            self.config(),
            "get_provider_status",
            {"provider": "808-MJ"},
        )
        self.assertEqual(status["transport"], "808-midjourney")
        self.assertEqual(status["model"], "midjourney-v8.2")
        self.assertEqual(status["fixed_result_count"], 4)
        self.assertEqual(status["supported_operation"], "generate_image")
        self.assertEqual(status["remote_async"]["submission_query"], {"async": "true"})

if __name__ == "__main__":
    unittest.main()
