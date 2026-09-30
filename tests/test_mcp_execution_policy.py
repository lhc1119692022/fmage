from __future__ import annotations

import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]


class McpExecutionPolicyTests(unittest.TestCase):
    def config(self, root, url, **settings):
        path = root / "providers.json"
        path.write_text(json.dumps({
            "active_providers": ["test"], "cache_dir": str(root / "cache"),
            "output_dir": str(root / "output"),
            "providers": {"test": {"transport": "openai-images", "model": "gpt-image-2",
                                   "base_url": url, "api_key": "test-only-key", **settings}},
        }), encoding="utf-8")
        return path

    def call(self, config, tool, arguments, extra_env=None):
        request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                   "params": {"name": tool, "arguments": arguments}}
        process = subprocess.run(
            ["node", str(ROOT / "mcp/server.mjs")], input=json.dumps(request) + "\n",
            capture_output=True, text=True, encoding="utf-8", timeout=10, check=True,
            env={**os.environ, "FMAGE_CONFIG": str(config), "FMAGE_PYTHON": sys.executable,
                 "PYTHONDONTWRITEBYTECODE": "1", **(extra_env or {})},
        )
        response = json.loads(process.stdout.splitlines()[0])
        self.assertNotIn("error", response)
        self.assertFalse(response["result"].get("isError"), response)
        return response["result"]["structuredContent"]

    def test_explicit_provider_image_field_is_used_before_the_first_edit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "input.png"
            Image.new("RGB", (16, 16)).save(image)
            config = self.config(root, "https://example.invalid/v1", image_field="image[]")
            result = self.call(config, "edit_image", {"prompt": "test", "images": [str(image)], "dry_run": True})
            self.assertEqual(result["image_field"], "image[]")

    def test_batch_foreground_deadline_returns_while_the_same_task_finishes_in_background(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / "foreground-timer.txt"
            preload = root / "virtual-foreground.mjs"
            # Advance only the 40s foreground timer; child/network timeouts stay real.
            preload.write_text(
                "import {writeFileSync} from 'node:fs';\n"
                "const original=globalThis.setTimeout;\n"
                "globalThis.setTimeout=(callback,ms,...args)=>{\n"
                "if(ms===40000){writeFileSync(process.env.FMAGE_TEST_TIMER_MARKER,'40s');"
                "return original(callback,10,...args);}\n"
                "return original(callback,ms,...args);};\n", encoding="utf-8",
            )
            image = io.BytesIO()
            Image.new("RGB", (8, 8), (100, 150, 200)).save(image, format="PNG")
            encoded = base64.b64encode(image.getvalue()).decode()
            posts = []

            class Handler(BaseHTTPRequestHandler):
                def do_POST(self):
                    posts.append(self.path)
                    self.rfile.read(int(self.headers.get("Content-Length", "0")))
                    threading.Event().wait(0.2)
                    payload = json.dumps({"data": [{"b64_json": encoded}]}).encode()
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)

                def log_message(self, *args):
                    pass

            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                config = self.config(root, f"http://127.0.0.1:{server.server_port}/v1")
                result = self.call(config, "generate_image_batch", {
                    "jobs": [{"prompt": "first"}, {"prompt": "second"}], "return_when": "completed",
                }, {"NODE_OPTIONS": f"--import={preload.as_uri()}", "FMAGE_TEST_TIMER_MARKER": str(marker)})
            finally:
                server.shutdown()
                server.server_close()
                thread.join()
            self.assertEqual(marker.read_text(), "40s")
            self.assertEqual(result["status"], "running")
            self.assertEqual(result["completed_count"], 0)
            state = json.loads((root / "cache/tasks" / result["task_id"] / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "completed")
            self.assertEqual(state["completed_count"], 2)
            self.assertEqual(len(posts), 2)
            self.assertTrue(all(Path(image).is_file() for image in state["images"]))


if __name__ == "__main__":
    unittest.main()
