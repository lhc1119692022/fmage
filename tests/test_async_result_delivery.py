from __future__ import annotations

import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest import mock
import urllib.error
import urllib.parse


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import openai_images_async_transport as transport


PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9WlD7xkAAAAASUVORK5CYII="
)


def args_for(command: str, root: Path, base_url: str, *extra: str):
    return transport.build_parser().parse_args([
        command, "--prompt", "test result delivery", "--base-url", base_url,
        "--model", "gpt-image-2.5-sunburst", "--api-key-env", "FMAGE_TEST_API_KEY",
        "--output-dir", str(root), "--resolution", "4k", "--aspect", "16:9",
        "--quality", "max", "--timeout", "10", *extra,
    ])


class ResultDeliveryTests(unittest.TestCase):
    def test_completed_edit_and_generation_skip_broken_image_urls_without_resubmitting(self):
        for command in ("edit", "generate"):
            with self.subTest(command=command), tempfile.TemporaryDirectory() as temp:
                calls = []
                root = Path(temp)
                reference = root / "reference.png"
                reference.write_bytes(PNG_BYTES)

                class Handler(BaseHTTPRequestHandler):
                    def send_json(self, response):
                        body = json.dumps(response).encode()
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)

                    def do_POST(self):
                        calls.append(("POST", self.path))
                        self.rfile.read(int(self.headers["Content-Length"]))
                        self.send_json({"task_id": "task-existing", "status": "queued"})

                    def do_GET(self):
                        calls.append(("GET", self.path))
                        if self.path == "/image.png?token=private-image-token":
                            self.send_error(502)
                            return
                        checkpoints = list((root / "output").glob("*/remote-task.json"))
                        self.server.pending_checkpoint = json.loads(checkpoints[0].read_text())
                        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                        if query.get("response_format") == ["b64_json"]:
                            self.send_json({
                                "task_id": "task-existing", "status": "completed",
                                "data": [{"b64_json": base64.b64encode(PNG_BYTES).decode()}],
                            })
                            return
                        self.send_json({
                            "task_id": "task-existing", "status": "completed",
                            "data": [{"url": f"http://127.0.0.1:{self.server.server_port}/image.png?token=private-image-token"}],
                        })

                    def log_message(self, *args):
                        pass

                server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    args = args_for(
                        command, root / "output", f"http://127.0.0.1:{server.server_port}/v1",
                        *(["--image", str(reference)] if command == "edit" else []),
                    )
                    with (
                        mock.patch.dict(os.environ, {"FMAGE_TEST_API_KEY": "test-api-key"}),
                        mock.patch.object(transport.time, "sleep"),
                    ):
                        result = getattr(transport, f"run_{command}")(args)
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join()

                posts = [path for method, path in calls if method == "POST"]
                self.assertEqual(len(posts), 1)
                self.assertIn("async=true", posts[0])
                self.assertEqual([path for method, path in calls if method == "GET"], [
                    "/v1/images/tasks/task-existing?response_format=b64_json",
                ])
                self.assertEqual(server.pending_checkpoint["stage"], "remote_pending")
                self.assertEqual(server.pending_checkpoint["remote_task_id"], "task-existing")
                checkpoint_text = json.dumps(server.pending_checkpoint)
                self.assertNotIn("test-api-key", checkpoint_text)
                self.assertNotIn("private-image-token", checkpoint_text)
                self.assertEqual(result["remote_task_id"], "task-existing")
                self.assertEqual(Path(result["images"][0]).read_bytes(), PNG_BYTES)
                self.assertNotIn("remote_result_recovered_as_b64_json", result["notes"])
                self.assertNotIn("remote_result_url_download_http_502", result["notes"])
                manifest = json.loads(Path(result["manifest"]).read_text())
                self.assertEqual(manifest["request"]["quality"], "max")
                self.assertEqual(manifest["requested_size"], "3840x2160")
                self.assertNotIn("result_recovery_completed_at", manifest["timing"])
                self.assertFalse((Path(result["manifest"]).parent / "remote-task.json").exists())

    def test_failed_base64_query_keeps_task_id_and_stops_after_one_read(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            args = args_for("generate", root, "https://provider.example/v1")
            response = {"data": [{"url": "https://image.example/result?token=secret-url"}]}
            with (
                mock.patch.object(transport, "download_image", side_effect=urllib.error.URLError("private-url")),
                mock.patch.object(transport.openai, "json_get", side_effect=transport.openai.ApiError(502, "private-provider-body")) as get,
            ):
                with self.assertRaises(transport.RemoteTaskError) as caught:
                    transport.save_completed_images(args, response, "secret-key", root, {
                        "remote_task_id": "task-recover", "remote_status": "completed",
                    }, {})
            self.assertEqual(get.call_count, 1)
            self.assertEqual(caught.exception.task_id, "task-recover")
            self.assertIn("image URL download failed", str(caught.exception))
            self.assertIn("base64 result query failed with HTTP 502", str(caught.exception))
            checkpoint_text = (root / "remote-task.json").read_text()
            self.assertEqual(json.loads(checkpoint_text)["stage"], "download_failed")
            for secret in ("secret-key", "secret-url", "private-url", "private-provider-body"):
                self.assertNotIn(secret, checkpoint_text)
                self.assertNotIn(secret, str(caught.exception))

    def test_download_failure_without_task_id_does_not_query_or_resubmit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            args = args_for("generate", root, "https://provider.example/v1")
            with (
                mock.patch.object(transport, "download_image", side_effect=TimeoutError()),
                mock.patch.object(transport.openai, "json_get") as get,
            ):
                with self.assertRaisesRegex(RuntimeError, "no remote task ID was returned"):
                    transport.save_completed_images(args, {"data": [{"url": "https://image.example/image"}]}, "key", root, None, {})
            get.assert_not_called()
            checkpoint = json.loads((root / "remote-task.json").read_text())
            self.assertEqual(checkpoint["stage"], "download_failed")
            self.assertIsNone(checkpoint["remote_task_id"])

    def test_url_only_partial_or_wrong_task_recovery_is_terminal(self):
        cases = [
            [],
            {"task_id": "task-original", "data": [{"url": "https://image.example/again"}]},
            {"task_id": "task-other", "data": [{"b64_json": base64.b64encode(PNG_BYTES).decode()}]},
            {"task_id": "task-original", "status": "running", "data": [{"b64_json": base64.b64encode(PNG_BYTES).decode()}]},
            {"task_id": "task-original", "data": []},
        ]
        for recovered in cases:
            with self.subTest(recovered=recovered), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                args = args_for("generate", root, "https://provider.example/v1")
                with (
                    mock.patch.object(transport, "download_image", side_effect=TimeoutError()) as download,
                    mock.patch.object(transport.openai, "json_get", return_value=recovered) as get,
                ):
                    with self.assertRaisesRegex(transport.RemoteTaskError, "matching complete base64"):
                        transport.save_completed_images(args, {
                            "task_id": "task-original", "data": [{"url": "https://image.example/original"}],
                        }, "key", root, None, {})
                self.assertEqual(get.call_count, 1)
                self.assertEqual(download.call_count, 1)
                self.assertFalse(list(root.glob("*.png")))

    def test_later_url_failure_recovers_multiple_images_without_duplicate_partial_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            args = args_for("generate", root, "https://provider.example/v1")
            second_image = PNG_BYTES + b"different-result"
            recovered = {"data": [
                {"b64_json": base64.b64encode(PNG_BYTES).decode()},
                {"b64_json": base64.b64encode(second_image).decode()},
            ]}
            with (
                mock.patch.object(transport, "download_image", side_effect=[PNG_BYTES, TimeoutError()]),
                mock.patch.object(transport.openai, "json_get", return_value=recovered),
            ):
                images, _, _ = transport.save_completed_images(args, {
                    "task_id": "task-multi", "data": [{"url": "https://image.example/one"}, {"url": "https://image.example/two"}],
                }, "key", root, None, {})
            self.assertEqual(len(images), 2)
            self.assertEqual(len(list(root.glob("*.png"))), 2)
            self.assertEqual([path.read_bytes() for path in images], [PNG_BYTES, second_image])


if __name__ == "__main__":
    unittest.main()
