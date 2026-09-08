from __future__ import annotations

import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

from PIL import Image


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = PLUGIN_ROOT / "mcp" / "server.mjs"


class ImageApiHandler(BaseHTTPRequestHandler):
    image_bytes = b""
    revised_prompt = "Create a tiny local transport test image."

    def do_POST(self) -> None:  # noqa: N802
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length:
            self.rfile.read(content_length)
        payload = json.dumps(
            {
                "id": "provider-request-123",
                "model": "gpt-image-1",
                "usage": {"input_tokens": 12, "output_tokens": 34},
                "authorization": "must-not-be-recorded",
                "data": [
                    {
                        "b64_json": base64.b64encode(self.image_bytes).decode("ascii"),
                        "revised_prompt": self.revised_prompt,
                        "url": "https://signed.example.invalid/private-image",
                    }
                ],
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        return


class DirectOutputTests(unittest.TestCase):
    def test_mcp_writes_image_to_explicit_output_and_removes_empty_cache_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            png_path = root / "fixture.png"
            Image.new("RGB", (8, 8), (10, 20, 30)).save(png_path)
            ImageApiHandler.image_bytes = png_path.read_bytes()

            http_server = ThreadingHTTPServer(("127.0.0.1", 0), ImageApiHandler)
            http_thread = threading.Thread(target=http_server.serve_forever, daemon=True)
            http_thread.start()

            config_path = root / "providers.json"
            config_path.write_text(
                json.dumps(
                    {
                        "active_providers": ["local-test"],
                        "output_dir": "final",
                        "cache_dir": "cache",
                        "providers": {
                            "local-test": {
                                "transport": "openai-images",
                                "base_url": f"http://127.0.0.1:{http_server.server_port}/v1",
                                "model": "gpt-image-1",
                                "api_key": "test-only-key",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            environment = {
                **os.environ,
                "FMAGE_CONFIG": str(config_path),
                "FMAGE_PYTHON": sys.executable,
            }
            process = subprocess.Popen(
                ["node", str(SERVER_PATH)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                env=environment,
            )
            try:
                if process.stdin is None or process.stdout is None:
                    self.fail("Fmage test server pipes are unavailable")
                request = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "generate_image",
                        "arguments": {
                            "prompt": ImageApiHandler.revised_prompt,
                            "output_dir": str(root / "requested-output"),
                            "output_format": "png",
                            "size": "1024x1024",
                            "resolution_user_requested": True,
                            "include_provider_metadata": True,
                        },
                    },
                }
                process.stdin.write(json.dumps(request) + "\n")
                process.stdin.flush()
                line = process.stdout.readline()
                if not line:
                    stderr = process.stderr.read() if process.stderr is not None else ""
                    self.fail(stderr or "Fmage test server exited without a response")
                response = json.loads(line)
                self.assertNotIn("error", response)
                result = response["result"]["structuredContent"]

                output_path = Path(result["images"][0])
                final_dir = root / "requested-output"
                cache_dir = root / "cache"
                self.assertEqual(output_path.parent, final_dir)
                self.assertTrue(output_path.is_file())
                self.assertRegex(output_path.name, r"^\d{8}-\d{6}-001(?:-\d+)?\.png$")
                self.assertFalse(any(path.is_dir() for path in final_dir.iterdir()))
                self.assertTrue(any(path.suffix == ".json" for path in cache_dir.iterdir()))
                self.assertEqual(
                    [path for path in cache_dir.rglob("*") if path.is_dir()],
                    [],
                    [str(path) for path in cache_dir.rglob("*")],
                )
                self.assertNotIn("request", result)
                self.assertEqual(
                    result["prompt_provenance"],
                    {
                        "submitted": ImageApiHandler.revised_prompt,
                        "changed": False,
                        "provider_prompt_status": "echoed",
                    },
                )
                self.assertEqual(
                    result["provider_response_metadata"],
                    {
                        "id": "provider-request-123",
                        "model": "gpt-image-1",
                        "usage": {"input_tokens": 12, "output_tokens": 34},
                    },
                )
                response_text = response["result"]["content"][0]["text"]
                self.assertIn("Provider prompt status: echoed", response_text)
                self.assertIn("provider-request-123", response_text)
                self.assertTrue(result["warnings"])
                self.assertTrue(
                    any("smaller than requested 1024x1024" in warning for warning in result["warnings"])
                )
                self.assertIn("Warnings:", response_text)

                manifest_path = Path(result["manifest"])
                manifest_text = manifest_path.read_text(encoding="utf-8")
                manifest = json.loads(manifest_text)
                self.assertNotIn("prompt", manifest["request"])
                self.assertNotIn("response_metadata", manifest)
                self.assertEqual(manifest["prompt_provenance"], result["prompt_provenance"])
                self.assertEqual(
                    manifest["provider_response_metadata"],
                    result["provider_response_metadata"],
                )
                self.assertEqual(manifest_text.count(ImageApiHandler.revised_prompt), 1)
                self.assertNotIn("must-not-be-recorded", manifest_text)
                self.assertNotIn("signed.example.invalid", manifest_text)
            finally:
                if process.stdin is not None:
                    process.stdin.close()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                if process.stdout is not None:
                    process.stdout.close()
                if process.stderr is not None:
                    process.stderr.close()
                http_server.shutdown()
                http_server.server_close()
                http_thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
