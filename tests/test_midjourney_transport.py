from __future__ import annotations

import base64
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import midjourney_transport as mj

FAST = "Midjourney v8.2 高速"
STABLE = "mj-v8.2"
SERVER_ALIAS = "channel-specific-v8.2-id"
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9WlD7xkAAAAASUVORK5CYII=")
PROMPT = "  使用16:9，海报画幅 --ar 1:1 --raw --stylize 100 --q .5 --unknown future\n"


def complete(count=4, grid=False, task_id="task-test"):
    data = {"image_urls": [f"https://images.invalid/{i}.png" for i in range(count)]}
    if grid:
        data["grid_image_url"] = "https://images.invalid/grid.png"
    return {"data": {"task_id": task_id, "status": "completed", "result": {"data": data}}}


class MidjourneyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        env = mock.patch.dict(os.environ, {"FMAGE_ACTIVE_API_KEY": "test-key", "FMAGE_DIRECT_OUTPUT_DIR": ""})
        env.start()
        self.addCleanup(env.stop)

    def args(self, model=FAST, command="generate", *extra):
        return mj.build_parser().parse_args([command, "--base-url", "https://relay.invalid",
            "--model", model, "--output-dir", str(self.root), "--prompt", PROMPT, *extra])

    def test_model_id_only_changes_wire_model_for_shared_v82_contract(self):
        for model in [FAST, STABLE, SERVER_ALIAS]:
            with self.subTest(model=model):
                result = mj.run(self.args(model, "generate", "--dry-run"))
                self.assertEqual(result["request"], {"model": model, "prompt": PROMPT})
                self.assertEqual(result["endpoint"], "https://relay.invalid/v1/midjourney/generations")
                self.assertTrue(result["async_mode"])
                self.assertEqual(result["expected_image_count"], 4)
                self.assertIsNone(result["requested_size"])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_base_path_and_encoded_task_id(self):
        self.assertEqual(mj.endpoint("https://relay.invalid/prefix/v1/", "/tasks/a"), "https://relay.invalid/prefix/v1/tasks/a")
        result = mj.run(self.args(FAST, "recover", "--remote-task-id", "a/b?x", "--dry-run"))
        self.assertTrue(result["endpoint"].endswith("/tasks/a%2Fb%3Fx"))
        self.assertEqual(result["method"], "GET")

    def test_server_model_id_is_forwarded_without_local_allowlist(self):
        result = mj.run(self.args(SERVER_ALIAS, "generate", "--dry-run"))
        self.assertEqual(result["request"]["model"], SERVER_ALIAS)
        with self.assertRaisesRegex(ValueError, "non-empty"):
            mj.run(self.args("", "generate", "--dry-run"))

    def test_mixed_url_local_data_references_keep_order_and_generation_endpoint(self):
        local = self.root / "参考.png"
        local.write_bytes(PNG)
        data = "data:image/png;base64," + base64.b64encode(PNG).decode()
        result = mj.run(self.args(FAST, "edit", "--image", "https://images.invalid/a.png",
            "--image", str(local), "--image", data, "--primary-image-index", "1", "--dry-run"))
        self.assertEqual(result["request"]["images"], ["https://images.invalid/a.png", data, data])
        self.assertTrue(result["endpoint"].endswith("/midjourney/generations"))
        self.assertEqual(result["request"]["prompt"], PROMPT)

    def test_reference_limit_is_shared_and_checked_before_network(self):
        for model in [FAST, STABLE, SERVER_ALIAS]:
            with self.subTest(model=model):
                result = mj.run(self.args(model, "edit", "--image", "https://images.invalid/a.png", "--dry-run"))
                self.assertIn("image", result["request"])
                five = sum((["--image", f"https://images.invalid/{i}.png"] for i in range(5)), [])
                result = mj.run(self.args(model, "edit", *five, "--dry-run"))
                self.assertEqual(len(result["request"]["images"]), 5)
                with mock.patch.object(mj, "http_json") as network:
                    with self.assertRaisesRegex(ValueError, "at most 5"):
                        mj.run(self.args(model, "edit", *five, "--image", "extra.png"))
                    network.assert_not_called()

    def test_async_nested_submission_four_identical_images_and_separate_grid(self):
        submit = ({"data": {"task_id": "task-test", "status": "submitted"}}, "")
        with mock.patch.object(mj, "http_json", side_effect=[submit, (complete(grid=True), "")]) as network, \
             mock.patch.object(mj.time, "sleep"), mock.patch.object(mj, "download_image", return_value=PNG):
            result = mj.run(self.args())
        self.assertEqual(len(result["images"]), 4)  # Identical bytes still represent four slots.
        self.assertNotIn(result["grid_image_path"], result["images"])
        self.assertTrue(Path(result["grid_image_path"]).is_file())
        self.assertEqual(result["image_metadata"][0]["width"], 1)
        self.assertEqual(result["warnings"], [])
        self.assertEqual(network.call_count, 2)
        self.assertEqual(network.call_args_list[1].args, ("https://relay.invalid/v1/tasks/task-test", "test-key"))

    def test_header_only_task_submission_and_missing_image_warning(self):
        with mock.patch.object(mj, "http_json", side_effect=[({}, "task-test"), (complete(3, True), "")]), \
             mock.patch.object(mj.time, "sleep"), mock.patch.object(mj, "download_image", return_value=PNG):
            result = mj.run(self.args(STABLE))
        self.assertEqual(len(result["images"]), 3)
        self.assertTrue(result["warnings"])
        self.assertFalse(result["generation_resubmitted"])

    def test_unrelated_images_api_result_is_not_a_midjourney_task_result(self):
        response = {"data": [{"url": "https://images.invalid/a.png"}]}
        with mock.patch.object(mj, "http_json", return_value=(response, "")) as network, \
             mock.patch.object(mj, "download_image") as download:
            with self.assertRaisesRegex(mj.TransportFailure, "neither image URLs nor a task ID"):
                mj.run(self.args())
        network.assert_called_once()
        download.assert_not_called()

    def test_failure_status_and_unknown_status_stop_without_download_or_resubmit(self):
        for status in ["failed", "undocumented"]:
            with self.subTest(status=status), mock.patch.object(mj, "http_json", return_value=(
                    {"data": {"task_id": "task-test", "status": status, "error_message": "bad input"}}, "")) as network, \
                    mock.patch.object(mj, "download_image") as download:
                with self.assertRaises(mj.TransportFailure) as failure:
                    mj.run(self.args())
                self.assertEqual(failure.exception.context["remote_task_id"], "task-test")
                self.assertFalse(failure.exception.context["generation_resubmitted"])
                network.assert_called_once()
                download.assert_not_called()

    def test_uncertain_submission_keeps_header_task_id_and_never_retries(self):
        error = TimeoutError("read timed out")
        error.remote_task_id = "task-known"
        with mock.patch.object(mj, "http_json", side_effect=error) as network:
            with self.assertRaises(mj.TransportFailure) as failure:
                mj.run(self.args())
        self.assertEqual(failure.exception.context["stage"], "submission_uncertain")
        self.assertEqual(failure.exception.context["remote_task_id"], "task-known")
        network.assert_called_once()

    def test_task_id_mismatch_is_rejected(self):
        with mock.patch.object(mj, "http_json", return_value=(complete(), "different")):
            with self.assertRaisesRegex(mj.TransportFailure, "does not match"):
                mj.run(self.args())

    def test_download_failure_then_recover_after_model_switch_queries_original_task_only(self):
        with mock.patch.object(mj, "http_json", return_value=(complete(), "")), \
             mock.patch.object(mj, "download_image", side_effect=TimeoutError("CDN timed out")):
            with self.assertRaises(mj.TransportFailure) as failure:
                mj.run(self.args())
        self.assertEqual(failure.exception.context["stage"], "download_failed")
        self.assertEqual(list(self.root.rglob("*.png")), [])
        original_checkpoint = Path(failure.exception.context["checkpoint"])
        original_state = json.loads(original_checkpoint.read_text(encoding="utf-8"))
        original_state["download_error"] = {"http_status": 403}
        original_checkpoint.write_text(json.dumps(original_state), encoding="utf-8")
        with mock.patch.object(mj, "http_json", return_value=(complete(), "")) as network, \
             mock.patch.object(mj, "download_image", return_value=PNG):
            result = mj.run(self.args(STABLE, "recover", "--remote-task-id", "task-test"))
        self.assertEqual(result["model"], FAST)
        self.assertEqual(len(result["images"]), 4)
        recovered_state = json.loads(Path(result["checkpoint"]).read_text(encoding="utf-8"))
        self.assertNotIn("error", recovered_state)
        self.assertNotIn("download_error", recovered_state)
        self.assertEqual(json.loads(original_checkpoint.read_text(encoding="utf-8")), original_state)
        network.assert_called_once_with("https://relay.invalid/v1/tasks/task-test", "test-key", None)

    def test_recovery_rejects_changed_credential_or_service_before_network(self):
        with mock.patch.object(mj, "http_json", return_value=(complete(), "")), \
             mock.patch.object(mj, "download_image", return_value=PNG):
            result = mj.run(self.args())
        self.assertNotIn("test-key", Path(result["checkpoint"]).read_text(encoding="utf-8"))
        for new_key, base in [("changed-key", "https://relay.invalid"), ("test-key", "https://other.invalid")]:
            args = self.args(FAST, "recover", "--remote-task-id", "task-test")
            args.base_url = base
            with mock.patch.dict(os.environ, {"FMAGE_ACTIVE_API_KEY": new_key}), mock.patch.object(mj, "http_json") as network:
                with self.assertRaisesRegex(ValueError, "original service URL"):
                    mj.run(args)
                network.assert_not_called()

    def test_timeout_keeps_task_id_for_explicit_recovery(self):
        submit = ({"data": {"task_id": "task-test", "status": "submitted"}}, "")
        with mock.patch.object(mj, "http_json", side_effect=[submit, TimeoutError("deadline")]) as network, mock.patch.object(mj.time, "sleep"):
            with self.assertRaises(mj.TransportFailure) as failure:
                mj.run(self.args())
        self.assertEqual(failure.exception.context["remote_task_id"], "task-test")
        self.assertEqual(network.call_count, 2)


@contextmanager
def local_relay(count=4, grid=False, download_status=200):
    calls = []
    class Handler(BaseHTTPRequestHandler):
        server_version = "cloudflare"
        sys_version = ""
        def send_json(self, payload):
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append(("POST", self.path, payload, self.headers.get("Authorization")))
            self.send_json({"data": {"task_id": "task-http", "status": "submitted"}})
        def do_GET(self):
            calls.append(("GET", self.path, None, self.headers.get("Authorization"), self.headers.get("User-Agent")))
            if self.path.startswith("/image.png"):
                status = download_status if self.headers.get("User-Agent") == "fmage-midjourney/1.0" else 403
                if status != 200:
                    body = b"error code: 1010\n signed-secret test-key"
                    self.send_response(status)
                    self.send_header("Content-Type", "text/plain")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(PNG)))
                self.end_headers()
                self.wfile.write(PNG)
            else:
                response = complete(task_id="task-http")
                response["data"]["result"]["data"]["image_urls"] = [f"http://127.0.0.1:{self.server.server_port}/image.png?token=signed-secret"] * count
                if grid:
                    response["data"]["result"]["data"]["grid_image_url"] = f"http://127.0.0.1:{self.server.server_port}/image.png"
                self.send_json(response)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", calls
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


class MidjourneyMcpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / "providers.json"
        self.configure()

    def configure(self, model=FAST, url="https://relay.invalid"):
        self.config.write_text(json.dumps({"active_providers": ["KC-MJ"],
            "output_dir": str(self.root / "outputs"), "cache_dir": str(self.root / "cache"),
            "providers": {"KC-MJ": {"transport": "midjourney", "model": model, "base_url": url, "api_key": "test-key"}}}), encoding="utf-8")

    def call(self, tool, arguments, error=False):
        message = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": arguments}}
        result = subprocess.run(["node", str(ROOT / "mcp/server.mjs")], input=json.dumps(message) + "\n",
            capture_output=True, text=True, encoding="utf-8", timeout=20, check=True,
            env={**os.environ, "FMAGE_CONFIG": str(self.config), "FMAGE_PYTHON": sys.executable, "PYTHONDONTWRITEBYTECODE": "1"})
        response = json.loads(result.stdout.splitlines()[0])
        if error:
            self.assertTrue("error" in response or response.get("result", {}).get("isError"), response)
            return response
        self.assertNotIn("error", response)
        self.assertFalse(response["result"].get("isError"), response)
        return response["result"]["structuredContent"]

    def test_dry_runs_switch_models_keep_flags_and_native_resolution(self):
        for model in [FAST, STABLE, SERVER_ALIAS]:
            self.configure(model)
            result = self.call("generate_image", {"provider": "KC-MJ", "prompt": PROMPT, "prompt_mode": "direct", "aspect": "16:9", "dry_run": True})
            self.assertEqual(result["request"], {"model": model, "prompt": PROMPT})
            self.assertIsNone(result["requested_size"])
            self.assertEqual(result["provider_model"], model)

    def test_structured_controls_rejected_but_visual_8k_stays_in_prompt(self):
        self.call("generate_image", {"prompt": "apple", "resolution": "4k", "resolution_user_requested": True, "dry_run": True}, error=True)
        self.call("generate_image", {"prompt": "apple", "quality": "high", "dry_run": True}, error=True)
        self.call("generate_image", {"prompt": "apple", "aspect": "16:9", "dry_run": True}, error=True)
        prompt = "8K超高分辨率画质 --ar 1:1 --q 2"
        result = self.call("generate_image", {"prompt": prompt, "dry_run": True})
        self.assertEqual(result["request"]["prompt"], prompt)
        self.assertEqual(set(result["request"]), {"model", "prompt"})

    def test_batch_has_no_quality_or_resolution_injection(self):
        result = self.call("generate_image_batch", {"jobs": [{"prompt": PROMPT}, {"prompt": "二 --ar 9:16"}], "dry_run": True})
        self.assertEqual(result["requested_count"], 2)
        self.assertEqual(result["prompts_submitted"][0], PROMPT)
        self.assertNotIn("resolution_inferred", json.dumps(result))

    def test_status_and_recovery_use_shared_contract(self):
        status = self.call("get_provider_status", {})
        self.assertEqual(status["remote_async"]["status_path"], "/v1/tasks/{task_id}")
        self.assertTrue(status["midjourney"]["prompt_only_controls"])
        self.assertNotIn("supported_models", status["midjourney"])
        self.assertEqual(status["midjourney"]["series"], "Midjourney v8.2")
        result = self.call("get_image_task_status", {"provider": "KC-MJ", "remote_task_id": "task-test", "dry_run": True})
        self.assertTrue(result["endpoint"].endswith("/v1/tasks/task-test"))
        self.assertEqual(result["method"], "GET")

    def test_full_http_submission_poll_download_and_same_task_recovery(self):
        with local_relay() as (url, calls):
            self.configure(url=url)
            result = self.call("generate_image", {"prompt": PROMPT, "prompt_mode": "direct"})
            self.assertEqual(len(result["images"]), 4)
            self.assertTrue(all(Path(path).is_file() for path in result["images"]))
            self.assertEqual(result["remote_task_id"], "task-http")
            self.assertEqual([call[1] for call in calls[:2]], ["/v1/midjourney/generations", "/v1/tasks/task-http"])
            self.assertEqual(calls[0][2], {"model": FAST, "prompt": PROMPT})
            self.assertEqual(calls[1][3], "Bearer test-key")
            self.assertIsNone(calls[2][3])  # CDN gets no API credential.
            self.assertEqual(calls[2][4], "fmage-midjourney/1.0")
            self.configure(STABLE, url)
            recovered = self.call("get_image_task_status", {"provider": "KC-MJ", "remote_task_id": "task-http"})
            self.assertEqual(recovered["provider_model"], FAST)
            self.assertEqual(len([c for c in calls if c[0] == "POST"]), 1)

    def test_download_failure_reaches_mcp_with_task_context_and_sanitized_diagnostics(self):
        with local_relay(download_status=403) as (url, calls):
            self.configure(url=url)
            response = self.call("generate_image", {"prompt": PROMPT}, error=True)
            self.assertNotIn("error", response)
            result = response["result"]
            self.assertTrue(result["isError"])
            failure = result["structuredContent"]
            self.assertEqual(json.loads(result["content"][0]["text"]), failure)
            self.assertEqual(failure["status"], "failed")
            self.assertEqual(failure["stage"], "download_failed")
            self.assertEqual(failure["remote_status"], "completed")
            self.assertEqual(failure["remote_task_id"], "task-http")
            self.assertFalse(failure["generation_resubmitted"])
            self.assertEqual(failure["download_error"], {
                "http_status": 403, "host": "127.0.0.1", "asset_type": "image",
                "image_index": 1, "cloudflare_error_code": "1010",
            })
            checkpoint = json.loads(Path(failure["checkpoint"]).read_text(encoding="utf-8"))
            self.assertEqual(checkpoint["download_error"], failure["download_error"])
            self.assertNotIn("signed-secret", json.dumps(result))
            self.assertNotIn("test-key", json.dumps(result))
            self.assertEqual(len([c for c in calls if c[0] == "POST"]), 1)
            self.assertEqual(len([c for c in calls if c[1].startswith("/image.png")]), 1)
            self.assertEqual(list(self.root.rglob("*.png")), [])

    def test_shared_http_edit_and_reference_limit(self):
        with local_relay() as (url, calls):
            self.configure(SERVER_ALIAS, url)
            result = self.call("edit_image", {"prompt": PROMPT, "images": [url + "/reference.png"]})
            self.assertEqual(len(result["images"]), 4)
            self.assertEqual(calls[0][1], "/v1/midjourney/generations")
            self.assertEqual(calls[0][2]["model"], SERVER_ALIAS)
            self.assertEqual(set(calls[0][2]), {"model", "prompt", "image"})
            self.call("edit_image", {"prompt": PROMPT, "images": [url + f"/{i}" for i in range(6)]}, error=True)
            self.assertEqual(len([c for c in calls if c[0] == "POST"]), 1)

    def test_partial_result_warning_and_grid_survive_compact_delivery(self):
        with local_relay(count=3, grid=True) as (url, calls):
            self.configure(STABLE, url)
            result = self.call("generate_image", {"prompt": "apple --ar 1:1"})
            self.assertEqual(result["status"], "partial")
            self.assertEqual(len(result["images"]), 3)
            self.assertTrue(result["warnings"])
            self.assertTrue(Path(result["grid_image_path"]).is_file())
            self.assertEqual(len([c for c in calls if c[0] == "POST"]), 1)

    def test_invalid_edit_batch_stops_before_any_submission(self):
        with local_relay() as (url, calls):
            self.configure(STABLE, url)
            self.call("edit_image_batch", {"jobs": [
                {"prompt": "valid", "images": [url + "/a"]},
                {"prompt": "invalid", "images": [url + f"/{i}" for i in range(6)]},
            ]}, error=True)
            self.assertEqual(calls, [])

    def test_migration_preserves_midjourney_configuration(self):
        from migrate_local_config import migrate_payload
        payload = json.loads(self.config.read_text())
        before = json.loads(json.dumps(payload))
        migrate_payload(payload)
        self.assertEqual(payload, before)

    def test_partial_batch_exposes_count_mismatch_and_separate_grids(self):
        with local_relay(count=3, grid=True) as (url, calls):
            self.configure(STABLE, url)
            result = self.call("generate_image_batch", {"jobs": [{"prompt": "one"}, {"prompt": "two"}], "return_when": "completed"})
            self.assertEqual(result["status"], "partial")
            self.assertEqual(result["partial_count"], 2)
            self.assertEqual(len(result["images"]), 6)
            self.assertEqual(len(result["grid_images"]), 2)
            self.assertTrue(result["warnings"])
            self.assertEqual(len([c for c in calls if c[0] == "POST"]), 2)


if __name__ == "__main__":
    unittest.main()
