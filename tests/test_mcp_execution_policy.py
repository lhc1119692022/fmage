from __future__ import annotations

import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import queue
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
            self.assertEqual(result["running_count"], 2)
            self.assertFalse(result["terminal"])
            self.assertEqual(result["next_action"], {
                "tool": "get_image_task_status",
                "arguments": {"task_id": result["task_id"], "wait_seconds": 40},
            })
            self.assertIn("Do not resubmit", result["message"])
            state = json.loads((root / "cache/tasks" / result["task_id"] / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "completed")
            self.assertEqual(state["completed_count"], 2)
            self.assertEqual(len(posts), 2)
            self.assertTrue(all(Path(image).is_file() for image in state["images"]))

    def test_waiting_for_generate_and_edit_batches_never_submits_more_images(self):
        for tool, fail_second in (
            ("generate_image_batch", False),
            ("edit_image_batch", False),
            ("generate_image_batch", True),
        ):
            with self.subTest(tool=tool, fail_second=fail_second), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                input_image = root / "input.png"
                Image.new("RGB", (8, 8)).save(input_image)
                encoded = base64.b64encode(input_image.read_bytes()).decode()
                release = threading.Event()
                posts = []

                class Handler(BaseHTTPRequestHandler):
                    def do_POST(self):
                        posts.append(self.path)
                        number = len(posts)
                        self.rfile.read(int(self.headers.get("Content-Length", "0")))
                        if number == 2:
                            release.wait(10)
                        failed = fail_second and number == 2
                        payload = json.dumps(
                            {"error": {"message": "test failure"}} if failed
                            else {"data": [{"b64_json": encoded}]}
                        ).encode()
                        self.send_response(500 if failed else 200)
                        self.send_header("Content-Length", str(len(payload)))
                        self.end_headers()
                        self.wfile.write(payload)

                    def log_message(self, *args):
                        pass

                # Fire the foreground deadline exactly after one job has settled.
                # This reproduces the old false-partial response without a 40s test.
                preload = root / "foreground-after-first.mjs"
                preload.write_text(
                    "import {readdirSync,readFileSync} from 'node:fs';\n"
                    "const original=globalThis.setTimeout;\n"
                    "globalThis.setTimeout=(callback,ms,...args)=>{\n"
                    "if(ms!==40000)return original(callback,ms,...args);\n"
                    "const check=()=>{const root=process.env.FMAGE_TEST_TASKS;\n"
                    "const ready=readdirSync(root).some(id=>{try{return "
                    "JSON.parse(readFileSync(root+'/'+id+'/state.json','utf8')).completed_count===1;"
                    "}catch{return false;}});\n"
                    "if(ready)callback(...args);else original(check,10);};\n"
                    "return original(check,10);};\n", encoding="utf-8",
                )
                server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                config = self.config(root, f"http://127.0.0.1:{server.server_port}/v1")
                process = subprocess.Popen(
                    ["node", str(ROOT / "mcp/server.mjs")],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, encoding="utf-8",
                    env={**os.environ, "FMAGE_CONFIG": str(config), "FMAGE_PYTHON": sys.executable,
                         "PYTHONDONTWRITEBYTECODE": "1", "NODE_OPTIONS": f"--import={preload.as_uri()}",
                         "FMAGE_TEST_TASKS": str(root / "cache/tasks")},
                )
                responses = queue.Queue()
                reader = threading.Thread(
                    target=lambda: [responses.put(line) for line in process.stdout], daemon=True,
                )
                reader.start()
                next_id = 0

                def call(name, arguments):
                    nonlocal next_id
                    next_id += 1
                    process.stdin.write(json.dumps({
                        "jsonrpc": "2.0", "id": next_id, "method": "tools/call",
                        "params": {"name": name, "arguments": arguments},
                    }) + "\n")
                    process.stdin.flush()
                    response = json.loads(responses.get(timeout=10))
                    self.assertEqual(response["id"], next_id)
                    self.assertNotIn("error", response)
                    self.assertFalse(response["result"].get("isError"), response)
                    return response["result"]

                try:
                    arguments = {"jobs": [{"prompt": "first"}, {"prompt": "second"}]}
                    if tool == "edit_image_batch":
                        arguments["images"] = [str(input_image)]
                    response = call(tool, arguments)
                    running = response["structuredContent"]
                    self.assertEqual(running["status"], "running")
                    self.assertFalse(running["terminal"])
                    self.assertEqual(running["completed_count"], 1)
                    self.assertEqual(running["running_count"], 1)
                    self.assertEqual(running["pending_count"], 0)
                    text = "\n".join(item.get("text", "") for item in response["content"])
                    self.assertIn("get_image_task_status", text)
                    self.assertIn("Do not resubmit", text)
                    self.assertIn("1 running", text)
                    for _ in range(2):
                        snapshot = call("get_image_task_status", {
                            "task_id": running["task_id"], "wait_seconds": 0,
                        })["structuredContent"]
                        self.assertFalse(snapshot["terminal"])
                        self.assertEqual(snapshot["task_id"], running["task_id"])
                    timer = threading.Timer(0.15, release.set)
                    timer.start()
                    action = running["next_action"]
                    finished = call(action["tool"], action["arguments"])["structuredContent"]
                    timer.join()
                    self.assertTrue(finished["terminal"])
                    self.assertEqual(finished["status"], "partial" if fail_second else "completed")
                    self.assertEqual(finished["completed_count"], 1 if fail_second else 2)
                    self.assertEqual(finished["failed_count"], 1 if fail_second else 0)
                    self.assertEqual(finished["running_count"], 0)
                    self.assertNotIn("next_action", finished)
                    self.assertEqual(finished["task_id"], running["task_id"])
                    self.assertTrue(all(Path(path).is_file() for path in finished["images"]))
                    self.assertEqual(len(posts), 2)
                    # Terminal reads return immediately with the same final result.
                    again = call("get_image_task_status", {
                        "task_id": running["task_id"],
                    })["structuredContent"]
                    self.assertEqual(again, finished)
                    self.assertEqual(len(posts), 2)
                finally:
                    release.set()
                    process.stdin.close()
                    process.wait(timeout=10)
                    reader.join(timeout=2)
                    process.stdout.close()
                    process.stderr.close()
                    server.shutdown()
                    server.server_close()
                    thread.join()

    def test_local_wait_expiry_and_terminal_pending_are_distinct(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.config(root, "https://example.invalid/v1")
            state_path = root / "cache/tasks/task-existing/state.json"
            state_path.parent.mkdir(parents=True)
            state = {
                "task_id": "task-existing", "status": "running", "requested_count": 2,
                "completed_count": 0, "pending_count": 0, "failed_count": 0, "images": [],
            }
            state_path.write_text(json.dumps(state), encoding="utf-8")
            running = self.call(config, "get_image_task_status", {
                "task_id": state["task_id"], "wait_seconds": 1,
            })
            self.assertFalse(running["terminal"])
            self.assertEqual(running["running_count"], 2)
            self.assertEqual(running["next_action"]["arguments"]["task_id"], state["task_id"])
            for status in ("failed", "partial", "pending"):
                state.update(status=status, completed_at="2026-10-12T00:00:00Z")
                state_path.write_text(json.dumps(state), encoding="utf-8")
                terminal = self.call(config, "get_image_task_status", {"task_id": state["task_id"]})
                self.assertTrue(terminal["terminal"])
                self.assertNotIn("next_action", terminal)


if __name__ == "__main__":
    unittest.main()
