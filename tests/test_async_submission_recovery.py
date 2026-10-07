from __future__ import annotations

import base64
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock
import urllib.parse


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import openai_images_async_transport as transport

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9WlD7xkAAAAASUVORK5CYII="
)


@contextmanager
def fake_provider(*, disconnect_submit=False):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append(("POST", self.path))
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            if disconnect_submit:
                # The task was accepted, but the response never reached the client.
                self.close_connection = True
                return
            self.send_json({"task_id": "task-existing", "status": "queued"})

        def do_GET(self):
            calls.append(("GET", self.path))
            self.send_json({
                "task_id": "task-existing", "status": "completed",
                "data": [{"b64_json": base64.b64encode(PNG).decode()}],
            })

        def send_json(self, payload):
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", calls
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def argv(command, root, base_url, *extra):
    return [command, "--base-url", base_url, "--model", "gpt-image-2.5-sunburst",
            "--api-key-env", "FMAGE_TEST_API_KEY", "--output-dir", str(root), *extra]


def mcp_call(root, base_url, tool, arguments, *, key="test-key"):
    config = root / "providers.json"
    config.write_text(json.dumps({
        "active_providers": ["test"], "output_dir": str(root / "images"),
        "cache_dir": str(root / "cache"), "task_dir": str(root / "tasks"),
        "providers": {"test": {"transport": "openai-images", "async_mode": True,
                                 "base_url": base_url, "model": "gpt-image-2.5-sunburst", "api_key": key}},
    }), encoding="utf-8")
    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": tool, "arguments": arguments}}
    result = subprocess.run(
        ["node", str(ROOT / "mcp" / "server.mjs")], input=json.dumps(request) + "\n",
        capture_output=True, text=True, encoding="utf-8", timeout=25,
        env={**os.environ, "FMAGE_CONFIG": str(config), "FMAGE_PYTHON": sys.executable},
    )
    if result.returncode:
        raise AssertionError(result.stderr)
    response = json.loads(result.stdout.splitlines()[0])
    if "error" in response:
        raise AssertionError(response["error"])
    return response["result"]["structuredContent"]


class SubmissionRecoveryTests(unittest.TestCase):
    def test_disconnect_before_acknowledgment_is_unknown_and_never_resubmitted(self):
        for command in ("generate", "edit"):
            with self.subTest(command=command), tempfile.TemporaryDirectory() as temp, fake_provider(disconnect_submit=True) as (url, calls):
                root = Path(temp)
                reference = root / "reference.png"
                reference.write_bytes(PNG)
                args = transport.build_parser().parse_args(argv(
                    command, root / "cache", url, "--prompt", "private-prompt",
                    *(["--image", str(reference)] if command == "edit" else []),
                ))
                with mock.patch.dict(os.environ, {"FMAGE_TEST_API_KEY": "private-key"}):
                    with self.assertRaises(transport.SubmissionUncertain) as caught:
                        getattr(transport, f"run_{command}")(args)
                self.assertEqual(len(calls), 1)
                self.assertEqual(calls[0][0], "POST")
                checkpoint = Path(caught.exception.context["checkpoint"]).read_text()
                state = json.loads(checkpoint)
                self.assertEqual(state["stage"], "submission_uncertain")
                self.assertEqual(state["remote_status"], "unknown")
                self.assertIsNone(state["remote_task_id"])
                self.assertIn("provider_submission_failed_at", state["timing"])
                self.assertFalse(list((root / "cache").glob("**/manifest.json")))
                for secret in ("private-key", "private-prompt"):
                    self.assertNotIn(secret, checkpoint)
                    self.assertNotIn(secret, str(caught.exception))

    def test_cli_failure_keeps_machine_readable_stage_without_sensitive_exception_details(self):
        with tempfile.TemporaryDirectory() as temp:
            out, err = StringIO(), StringIO()
            with (mock.patch.dict(os.environ, {"FMAGE_TEST_API_KEY": "private-key"}),
                  mock.patch.object(transport.openai, "json_request", side_effect=http.client.RemoteDisconnected("private-url-and-key")),
                  redirect_stdout(out), redirect_stderr(err)):
                code = transport.main(argv("generate", Path(temp), "https://provider.example/v1", "--prompt", "private-prompt"))
            failure = json.loads(out.getvalue())
            self.assertEqual(code, 1)
            self.assertEqual(failure["stage"], "submission_uncertain")
            self.assertFalse(failure["generation_resubmitted"])
            self.assertNotIn("private-url-and-key", out.getvalue() + err.getvalue())

    def test_known_task_can_be_retrieved_after_submission_disconnect_without_another_post(self):
        with tempfile.TemporaryDirectory() as temp, fake_provider(disconnect_submit=True) as (url, calls):
            root = Path(temp)
            generate = transport.build_parser().parse_args(argv("generate", root, url, "--prompt", "test"))
            recover = transport.build_parser().parse_args(argv("recover", root, url, "--remote-task-id", "task-existing"))
            with mock.patch.dict(os.environ, {"FMAGE_TEST_API_KEY": "test-key"}), mock.patch.object(transport.time, "sleep"):
                with self.assertRaises(transport.SubmissionUncertain):
                    transport.run_generate(generate)
                result = transport.run_recover(recover)
            self.assertEqual([method for method, _ in calls], ["POST", "GET"])
            self.assertEqual(Path(result["images"][0]).read_bytes(), PNG)
            self.assertEqual(result["remote_task_id"], "task-existing")
            self.assertFalse(result["generation_resubmitted"])
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(calls[1][1]).query)
            self.assertEqual(query["response_format"], ["b64_json"])
            manifest = json.loads(Path(result["manifest"]).read_text())
            self.assertEqual(manifest["command"], "recover")
            self.assertEqual(manifest["request"], {})

    def test_status_disconnect_preserves_the_known_id_for_explicit_retrieval(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            args = transport.build_parser().parse_args(argv("recover", root, "https://provider.example/v1", "--remote-task-id", "task-existing"))
            with (mock.patch.dict(os.environ, {"FMAGE_TEST_API_KEY": "private-key"}),
                  mock.patch.object(transport.time, "sleep"),
                  mock.patch.object(transport.openai, "json_get", side_effect=http.client.RemoteDisconnected("private-url")) as get,
                  mock.patch.object(transport.openai, "json_request") as submit):
                with self.assertRaises(transport.RemoteTaskError) as caught:
                    transport.run_recover(args)
            self.assertEqual(get.call_count, 1)
            submit.assert_not_called()
            state = json.loads(Path(caught.exception.context["checkpoint"]).read_text())
            self.assertEqual(state["remote_task_id"], "task-existing")
            self.assertEqual(state["stage"], "status_query_failed")
            self.assertNotIn("private-url", str(caught.exception))

    def test_wrong_task_empty_result_and_failed_status_cannot_be_published_as_success(self):
        responses = [
            {"task_id": "other-task", "status": "completed", "data": [{"b64_json": base64.b64encode(PNG).decode()}]},
            {"task_id": "task-existing", "status": "completed", "data": []},
            {"task_id": "task-existing", "status": "failed", "data": [{"b64_json": base64.b64encode(PNG).decode()}]},
        ]
        for response in responses:
            with self.subTest(response=response), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                args = transport.build_parser().parse_args(argv("recover", root, "https://provider.example/v1", "--remote-task-id", "task-existing"))
                with (mock.patch.dict(os.environ, {"FMAGE_TEST_API_KEY": "test-key"}),
                      mock.patch.object(transport.time, "sleep"),
                      mock.patch.object(transport.openai, "json_get", return_value=response)):
                    with self.assertRaises(transport.RemoteTaskError):
                        transport.run_recover(args)
                self.assertFalse(list(root.glob("**/*.png")))
                self.assertFalse(list(root.glob("**/manifest.json")))

    def test_mcp_preserves_submission_uncertainty_and_transport_timestamps(self):
        with tempfile.TemporaryDirectory() as temp, fake_provider(disconnect_submit=True) as (url, calls):
            result = mcp_call(Path(temp), url, "generate_image_batch", {
                "provider": "test", "jobs": [{"prompt": "test"}], "verbose": True,
                "return_when": "completed",
            })
            self.assertEqual(len(calls), 1)
            failure = result["job_statuses"][0]
            self.assertEqual(failure["stage"], "submission_uncertain")
            self.assertEqual(failure["remote_status"], "unknown")
            self.assertIn("provider_submission_failed_at", failure["timing"])
            self.assertTrue(Path(failure["checkpoint"]).is_file())

    def test_mcp_retrieves_the_existing_result_with_get_only(self):
        with tempfile.TemporaryDirectory() as temp, fake_provider() as (url, calls):
            root = Path(temp)
            result = mcp_call(root, url, "get_image_task_status", {
                "provider": "test", "remote_task_id": "task-existing", "verbose": True,
            })
            self.assertEqual([method for method, _ in calls], ["GET"])
            self.assertEqual(result["remote_task_id"], "task-existing")
            image = Path(result["images"][0])
            self.assertEqual(image.parent, root / "images")
            self.assertEqual(image.read_bytes(), PNG)

    def test_mcp_remote_dry_run_is_keyless_and_does_not_contact_the_provider(self):
        with tempfile.TemporaryDirectory() as temp, fake_provider() as (url, calls):
            result = mcp_call(Path(temp), url, "get_image_task_status", {
                "provider": "test", "remote_task_id": "task-existing", "dry_run": True,
            }, key="")
            self.assertTrue(result["dry_run"])
            self.assertEqual(result["method"], "GET")
            self.assertFalse(calls)


if __name__ == "__main__":
    unittest.main()
