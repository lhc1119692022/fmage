from __future__ import annotations

import argparse
import base64
import http.client
import io
from pathlib import Path
import socket
import sys
import tempfile
import time
import unittest
from unittest import mock
import urllib.parse
import urllib.request
from PIL import Image


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import transport_common as common
import openai_images_transport as openai
import openai_images_808_transport as image808
import ezai_banana_support as banana
import gemini_generate_content_transport as gemini

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6Z8sAAAAASUVORK5CYII=")


class RequestBudgetTests(unittest.TestCase):
    def test_nested_budgets_never_extend_the_parent_and_restore_on_error(self):
        clock = [0.0]
        with mock.patch.object(common.time, "monotonic", side_effect=lambda: clock[0]):
            with common.request_budget(900):
                clock[0] = 100
                self.assertEqual(common.request_timeout(900), 800)
                with common.request_budget(2000):
                    self.assertEqual(common.request_timeout(900), 800)
                with self.assertRaises(TimeoutError), common.request_budget(5):
                    clock[0] = 106
                    common.request_timeout()
                self.assertEqual(common.request_timeout(900), 794)
            self.assertEqual(common.request_timeout(900), 900)

    def test_all_http_boundaries_use_only_the_remaining_budget(self):
        request = urllib.request.Request("https://example.invalid")
        calls = (
            lambda: openai.perform_request(request, 600),
            lambda: banana.perform_request(request, 600),
            lambda: gemini.json_request("https://example.invalid", {}, "test-key", 600),
            lambda: common.download_image("https://example.invalid", 600),
        )
        clock = [0.0]
        with mock.patch.object(common.time, "monotonic", side_effect=lambda: clock[0]):
            for call in calls:
                clock[0] = 0
                response = io.BytesIO(b"{}")
                response.headers = {"Content-Type": "application/octet-stream"}
                with common.request_budget(600):
                    clock[0] = 595
                    with mock.patch("urllib.request.urlopen", return_value=response) as opening:
                        call()
                    self.assertEqual(opening.call_args.kwargs["timeout"], 5)

    def test_slow_stream_cannot_reset_the_deadline_with_each_chunk(self):
        clock = [0.0]

        class SlowBody(io.BytesIO):
            def read1(self, size):
                clock[0] += 0.4
                return super().read1(1)

        with mock.patch.object(common.time, "monotonic", side_effect=lambda: clock[0]):
            with common.request_budget(1), self.assertRaises(TimeoutError):
                common.read_response_bytes(SlowBody(b"12345"))
        self.assertAlmostEqual(clock[0], 1.2)

    def test_real_http_body_socket_uses_the_remaining_time(self):
        client, server = socket.socketpair()
        self.addCleanup(client.close)
        self.addCleanup(server.close)
        server.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 10\r\n\r\n{")
        response = http.client.HTTPResponse(client)
        response.begin()
        self.addCleanup(response.close)
        started = time.monotonic()
        with common.request_budget(0.15), self.assertRaises(TimeoutError):
            common.read_response_bytes(response)
        self.assertLess(time.monotonic() - started, 1)


class PollingPolicyTests(unittest.TestCase):
    def args(self):
        return argparse.Namespace(timeout=600, pending_total_timeout=600,
                                  pending_fast_window=120, pending_fast_interval=5,
                                  pending_slow_interval=10, poll_interval=5,
                                  response_format="url", base_url="https://example.invalid/v1")

    def test_generic_first_query_is_immediate_and_fast_window_starts_after_submission(self):
        clock = [130.0]
        queries = []

        def query(url, key, timeout):
            queries.append((clock[0], timeout))
            return {"id": "task", "status": "queued"} if len(queries) == 1 else {"data": [{"b64_json": "bytes"}]}

        with (mock.patch.object(openai.time, "monotonic", side_effect=lambda: clock[0]),
              mock.patch.object(openai.time, "sleep", side_effect=lambda delay: clock.__setitem__(0, clock[0] + delay)),
              mock.patch.object(openai, "json_get", side_effect=query)):
            result, _, _, expired = openai.poll_pending_response(self.args(), "task", "queued", "key", 0, "generate")
        self.assertIsNotNone(result)
        self.assertFalse(expired)
        self.assertEqual([q[0] for q in queries], [130, 135])
        self.assertTrue(all(0 < q[1] <= 30 for q in queries))

    def test_generic_does_not_accept_an_over_budget_result(self):
        clock = [590.0]

        def query(url, key, timeout):
            self.assertEqual(timeout, 10)
            clock[0] = 601
            return {"data": [{"b64_json": "bytes"}]}

        with (mock.patch.object(openai.time, "monotonic", side_effect=lambda: clock[0]),
              mock.patch.object(openai, "json_get", side_effect=query)):
            result, notes, _, expired = openai.poll_pending_response(self.args(), "task", "queued", "key", 0, "generate")
        self.assertIsNone(result)
        self.assertTrue(expired)
        self.assertIn("remote_task_pending_timeout", notes)

    def test_generic_service_error_does_not_probe_every_endpoint(self):
        clock = [0.0]
        urls = []

        def query(url, *args):
            urls.append(url)
            if len(urls) == 1:
                raise openai.ApiError(503, "service unavailable")
            return {"data": [{"b64_json": "bytes"}]}

        with (mock.patch.object(openai.time, "monotonic", side_effect=lambda: clock[0]),
              mock.patch.object(openai.time, "sleep", side_effect=lambda delay: clock.__setitem__(0, clock[0] + delay)),
              mock.patch.object(openai, "json_get", side_effect=query)):
            openai.poll_pending_response(self.args(), "task", "queued", "key", 0, "generate")
        self.assertEqual(len(urls), 2)
        self.assertEqual(urls[0], urls[1])
        self.assertEqual(clock[0], 5)

    def test_preparation_time_is_not_added_back_to_the_polling_budget(self):
        clock = [0.0]
        with mock.patch.object(common.time, "monotonic", side_effect=lambda: clock[0]):
            with common.request_budget(600):
                clock[0] = 590
                self.assertEqual(common.bounded_deadline(900), 600)

                def query(*args):
                    self.assertEqual(args[-1], 10)
                    clock[0] = 601
                    return {"data": [{"b64_json": "bytes"}]}

                with mock.patch.object(openai, "json_get", side_effect=query):
                    result, _, _, expired = openai.poll_pending_response(self.args(), "task", "queued", "key", 300, "generate")
                self.assertIsNone(result)
                self.assertTrue(expired)

    def test_808_queries_immediately_with_bounded_timeout_and_preserves_task_id(self):
        clock = [590.0]
        timing = {}
        queries = []

        def query(url, key, timeout):
            queries.append(url)
            self.assertTrue(0 < timeout <= 10)
            clock[0] = 601
            return {"status": "completed", "data": [{"b64_json": "bytes"}]}

        with self.assertRaises(image808.RemoteTaskError) as caught:
            image808.poll_remote_task(self.args(), "existing-task", "queued", "key", 0, timing,
                                      get_fn=query, sleep_fn=lambda _: self.fail("slept before first query"),
                                      monotonic_fn=lambda: clock[0], now_fn=lambda: "test-time")
        self.assertEqual(caught.exception.task_id, "existing-task")
        self.assertEqual(timing["remote_poll_count"], 1)
        self.assertEqual(urllib.parse.parse_qs(urllib.parse.urlsplit(queries[0]).query)["response_format"], ["b64_json"])


class SubmissionPolicyTests(unittest.TestCase):
    def test_errors_never_strip_parameters_or_send_another_post(self):
        payload = {"model": "gpt-image-2", "prompt": "test", "quality": "high",
                   "moderation": "low", "background": "transparent", "output_format": "png", "size": "auto"}
        for status in (400, 404, 415, 422, 401, 500):
            with self.subTest(status=status):
                submit = mock.Mock(side_effect=openai.ApiError(status, "unsupported field"))
                with self.assertRaises(openai.ApiError):
                    openai.request_with_compat_retry(submit, payload, "generate")
                submit.assert_called_once_with(payload)

    def test_edit_image_field_error_does_not_resubmit(self):
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "input.png"
            Image.new("RGBA", (16, 16), (100, 150, 200, 255)).save(image)
            args = openai.build_parser().parse_args([
                "edit", "--prompt", "test", "--base-url", "https://example.invalid/v1",
                "--model", "gpt-image-2", "--image", str(image), "--output-dir", directory,
            ])
            with (mock.patch.object(openai, "api_key", return_value="test-key"),
                  mock.patch.object(openai, "multipart_request", side_effect=openai.ApiError(400, "missing image[] field")) as submit):
                with self.assertRaises(openai.ApiError):
                    openai.run_edit(args)
            self.assertEqual(submit.call_count, 1)
            self.assertEqual(submit.call_args.args[2][0][0], "image")

    def test_808_async_result_avoids_url_download_and_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            args = image808.build_parser().parse_args([
                "generate", "--prompt", "test", "--base-url", "https://example.invalid/v1",
                "--model", "gpt-image-2", "--output-dir", directory,
            ])
            with (mock.patch.object(openai, "api_key", return_value="test-key"),
                  mock.patch.object(openai, "json_request", return_value={"task_id": "existing-task", "status": "queued"}) as submit,
                  mock.patch.object(openai, "json_get", return_value={"task_id": "existing-task", "status": "completed", "data": [{"b64_json": base64.b64encode(PNG).decode()}]}) as query,
                  mock.patch.object(image808, "download_image") as download,
                  mock.patch.object(image808.time, "sleep") as sleep):
                result = image808.run_generate(args)
            submit.assert_called_once()
            query.assert_called_once()
            download.assert_not_called()
            sleep.assert_not_called()
            self.assertEqual(submit.call_args.args[1]["response_format"], "url")
            self.assertEqual(Path(result["images"][0]).read_bytes(), PNG)
            self.assertNotIn("remote_result_recovered_as_b64_json", result["notes"])


if __name__ == "__main__":
    unittest.main()
