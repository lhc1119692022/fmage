from __future__ import annotations

import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import urllib.request


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))
import ezai_banana_support as banana_support
import ezai_banana_transport as banana
import gemini_generate_content_transport as gemini
import openai_images_808_transport as image808
import openai_images_transport as openai
import transport_common as common


class TimeoutPolicyTests(unittest.TestCase):
    def test_direct_transport_arguments_raise_short_timeouts_and_preserve_longer_ones(self):
        for transport, model in (
            (openai, "gpt-image-2"), (image808, "gpt-image-2.5-sunburst"),
            (gemini, "gemini-3-pro-image"), (banana, "nano-banana-pro"),
        ):
            for supplied, expected in ((None, 600), (60, 600), (900, 900)):
                with self.subTest(transport=transport.__name__, supplied=supplied):
                    argv = ["generate", "--prompt", "test", "--base-url", "https://example.invalid", "--model", model]
                    if supplied is not None:
                        argv.extend(["--timeout", str(supplied)])
                    args = transport.build_parser().parse_args(argv)
                    self.assertEqual(args.timeout, expected)
                    if transport is image808:
                        args.pending_total_timeout = 60
                        transport.validate_808_arguments(args)
                        self.assertEqual(args.pending_total_timeout, expected)

    def test_http_and_image_download_boundaries_enforce_timeout_floor(self):
        request = urllib.request.Request("https://example.invalid")
        for supplied, expected in ((60, 600), (900, 900)):
            for call in (
                lambda: openai.perform_request(request, supplied),
                lambda: banana_support.perform_request(request, supplied),
                lambda: gemini.json_request("https://example.invalid", {}, "test-only-key", supplied),
                lambda: common.download_image("https://example.invalid", supplied),
            ):
                response = io.BytesIO(b"{}")
                response.headers = {"Content-Type": "application/octet-stream"}
                with mock.patch("urllib.request.urlopen", return_value=response) as urlopen:
                    call()
                self.assertEqual(urlopen.call_args.kwargs["timeout"], expected)

    def test_mcp_routes_short_and_long_timeouts_for_all_transports_and_batch_jobs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            providers = {}
            requests = []
            expected = {}
            for transport, model, profile in (
                ("openai-images", "gpt-image-2", None),
                ("openai-images", "gpt-image-2.5-sunburst", "808"),
                ("gemini-generate-content", "gemini-3-pro-image", None),
                ("ezai-banana-images", "nano-banana-pro", None),
            ):
                for configured, effective in ((None, 600), (60, 600), (900, 900)):
                    name = f"provider-{len(providers)}"
                    provider = {"transport": transport, "model": model, "base_url": "https://example.invalid/v1", "api_key": ""}
                    if profile:
                        provider["transport_profile"] = profile
                    if configured is not None:
                        provider["timeout"] = configured
                    providers[name] = provider
                    for tool in ("generate_image", "get_provider_status"):
                        request_id = len(requests) + 1
                        arguments = {"provider": name}
                        if tool == "generate_image":
                            arguments.update(prompt="test timeout routing", dry_run=True, timeout=60)
                        requests.append({"jsonrpc": "2.0", "id": request_id, "method": "tools/call", "params": {"name": tool, "arguments": arguments}})
                        expected[request_id] = effective
            batch_id = len(requests) + 1
            requests.append({"jsonrpc": "2.0", "id": batch_id, "method": "tools/call", "params": {
                "name": "generate_image_batch", "arguments": {"provider": "provider-1", "dry_run": True,
                    "jobs": [{"prompt": "first", "timeout": 60}, {"prompt": "second", "timeout": 900}]},
            }})
            config = root / "providers.json"
            config.write_text(json.dumps({"providers": providers, "active_providers": list(providers), "output_dir": str(root / "output"), "cache_dir": str(root / "cache")}), encoding="utf-8")
            result = subprocess.run(
                ["node", str(PLUGIN_ROOT / "mcp" / "server.mjs")],
                input="".join(json.dumps(request) + "\n" for request in requests), capture_output=True,
                text=True, encoding="utf-8", timeout=30,
                env={**os.environ, "FMAGE_CONFIG": str(config), "FMAGE_PYTHON": sys.executable, "PYTHONDONTWRITEBYTECODE": "1"},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            responses = {response["id"]: response for response in map(json.loads, result.stdout.splitlines())}
            self.assertEqual(len(responses), len(requests))
            for request_id, effective in expected.items():
                with self.subTest(request_id=request_id):
                    response = responses[request_id]
                    self.assertNotIn("error", response)
                    self.assertFalse(response["result"].get("isError"), response["result"])
                    data = response["result"]["structuredContent"]
                    self.assertEqual(data["timeout_seconds"], effective)
                    if "remote_async" in data and "total_timeout_seconds" in data["remote_async"]:
                        self.assertEqual(data["remote_async"]["total_timeout_seconds"], effective)
            batch = responses[batch_id]["result"]["structuredContent"]
            self.assertEqual([job["timeout_seconds"] for job in batch["job_statuses"]], [600, 900])


if __name__ == "__main__":
    unittest.main()
