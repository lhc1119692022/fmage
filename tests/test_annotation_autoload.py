from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from PIL import Image

SERVER_PATH = Path(__file__).resolve().parents[1] / "mcp" / "server.mjs"


class AnnotationSession:
    """Runs the MCP server against a data home that already holds one layer."""

    def __init__(self, model: str, transport: str = "openai-images", cache_dir: bool = True) -> None:
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.temp = root / "tmp"
        self.temp.mkdir()
        config = {
            "active_providers": ["test"],
            "output_dir": str(root / "outputs"),
            "cache_dir": str(root / "cache") if cache_dir else "",
            "providers": {
                "test": {
                    "transport": transport,
                    "base_url": "https://example.invalid/v1",
                    "model": model,
                    "api_key": "",
                },
            },
        }
        (root / "providers.json").write_text(json.dumps(config), encoding="utf-8")
        self.data_home = root
        self.environment = {
            **os.environ,
            "FMAGE_CONFIG": str(root / "providers.json"),
            "FMAGE_DATA_HOME": str(root),
            "FMAGE_PYTHON": sys.executable,
            # os.tmpdir() reads TEMP/TMP on Windows and TMPDIR elsewhere.
            "TEMP": str(self.temp),
            "TMP": str(self.temp),
            "TMPDIR": str(self.temp),
        }
        self.process = subprocess.Popen(
            ["node", str(SERVER_PATH)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            env=self.environment,
        )
        self.next_id = 1

    def add_layer(self, mode: str, size: tuple[int, int] = (64, 64), root: Path | None = None) -> Path:
        directory = (root or self.data_home / "cache" / "annotations") / "abc123abc123abc1"
        directory.mkdir(parents=True, exist_ok=True)
        layer = Image.new("RGBA", size, (0, 0, 0, 0))
        layer.putpixel((8, 8), (220, 30, 30, 255))
        path = directory / "layer.png"
        layer.save(path)
        meta = {
            "version": 1,
            "id": directory.name,
            "mode": mode,
            "width": size[0],
            "height": size[1],
            "comments": [],
            "sourceName": "cat.png",
            "createdAt": __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ).isoformat(),
            "consumedAt": None,
        }
        (directory / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        return path

    def call(self, method: str, params: dict[str, object]) -> dict[str, object]:
        if self.process.stdin is None or self.process.stdout is None:
            raise AssertionError("Fmage test server pipes are unavailable")
        request_id = self.next_id
        self.next_id += 1
        self.process.stdin.write(json.dumps(
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        ) + "\n")
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        if not line:
            stderr = self.process.stderr.read() if self.process.stderr is not None else ""
            raise AssertionError(stderr or "Fmage test server exited without a response")
        return json.loads(line)

    def close(self) -> None:
        if self.process.stdin is not None:
            self.process.stdin.close()
        self.process.wait(timeout=10)
        for stream in (self.process.stdout, self.process.stderr):
            if stream is not None:
                stream.close()
        self.directory.cleanup()


class AnnotationAutoloadTests(unittest.TestCase):
    def test_a_waiting_layer_is_attached_for_a_markup_capable_model(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            session = AnnotationSession("gpt-image-2.6")
            self.addCleanup(session.close)
            layer = session.add_layer("markup")
            reference = Path(directory) / "reference.png"
            Image.new("RGB", (64, 64), (40, 70, 100)).save(reference)
            response = session.call("tools/call", {
                "name": "edit_image",
                "arguments": {"prompt": "recolor the marked area", "images": [str(reference)], "dry_run": True},
            })
        payload = json.dumps(response)
        self.assertNotIn("error", response, payload)
        self.assertFalse(response["result"].get("isError"), payload)
        content = response["result"]["structuredContent"]
        self.assertEqual(content["annotation_applied"], "markup")
        self.assertEqual(Path(content["markup"]).resolve(), layer.resolve())
        # The agent's prompt reaches the provider unchanged.
        self.assertEqual(content["prompt_submitted"], "recolor the marked area")

    def test_without_a_cache_dir_the_layer_is_read_from_the_temp_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            session = AnnotationSession("gpt-image-2.6", cache_dir=False)
            self.addCleanup(session.close)
            layer = session.add_layer("markup", root=session.temp / "fmage" / "annotations")
            reference = Path(directory) / "reference.png"
            Image.new("RGB", (64, 64), (40, 70, 100)).save(reference)
            response = session.call("tools/call", {
                "name": "edit_image",
                "arguments": {"prompt": "recolor the marked area", "images": [str(reference)], "dry_run": True},
            })
        payload = json.dumps(response)
        self.assertNotIn("error", response, payload)
        content = response["result"]["structuredContent"]
        self.assertEqual(content["annotation_applied"], "markup")
        self.assertEqual(Path(content["markup"]).resolve(), layer.resolve())

    def test_a_layer_drawn_on_the_original_attaches_to_the_hosts_scaled_copy(self) -> None:
        # The sidebar draws on the original file; the chat hands the model a
        # normalized copy a few pixels smaller (2368x1776 -> 2364x1773).
        with tempfile.TemporaryDirectory() as directory:
            session = AnnotationSession("gpt-image-2.6")
            self.addCleanup(session.close)
            layer = session.add_layer("markup", (592, 444))
            reference = Path(directory) / "attachment.jpg"
            Image.new("RGB", (591, 443), (40, 70, 100)).save(reference, format="JPEG")
            response = session.call("tools/call", {
                "name": "edit_image",
                "arguments": {"prompt": "follow the marks", "images": [str(reference)], "dry_run": True},
            })
        payload = json.dumps(response)
        self.assertNotIn("error", response, payload)
        self.assertFalse(response["result"].get("isError"), payload)
        content = response["result"]["structuredContent"]
        self.assertEqual(content["annotation_applied"], "markup")
        self.assertEqual(Path(content["markup"]).resolve(), layer.resolve())

    def test_a_flatten_layer_targets_the_compositing_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            session = AnnotationSession("gpt-image-2")
            self.addCleanup(session.close)
            layer = session.add_layer("flatten")
            reference = Path(directory) / "reference.png"
            Image.new("RGB", (64, 64), (40, 70, 100)).save(reference)
            response = session.call("tools/call", {
                "name": "edit_image",
                "arguments": {"prompt": "recolor it", "images": [str(reference)], "dry_run": True},
            })
        payload = json.dumps(response)
        self.assertNotIn("error", response, payload)
        content = response["result"]["structuredContent"]
        self.assertEqual(content["annotation_applied"], "flatten")
        self.assertEqual(Path(content["flatten_markup"]).resolve(), layer.resolve())
        self.assertIsNone(content.get("markup"))
        self.assertEqual(content["prompt_submitted"], "recolor it")

    def test_a_markup_layer_is_refused_on_a_model_without_the_channel(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            session = AnnotationSession("gpt-image-2")
            self.addCleanup(session.close)
            session.add_layer("markup")
            reference = Path(directory) / "reference.png"
            Image.new("RGB", (64, 64), (40, 70, 100)).save(reference)
            response = session.call("tools/call", {
                "name": "edit_image",
                "arguments": {"prompt": "recolor it", "images": [str(reference)], "dry_run": True},
            })
        payload = json.dumps(response)
        self.assertIn("annotation layer is waiting", payload)
        self.assertIn("gpt-image-2.5/2.6", payload)

    def test_no_layer_leaves_the_request_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            session = AnnotationSession("gpt-image-2.6")
            self.addCleanup(session.close)
            reference = Path(directory) / "reference.png"
            Image.new("RGB", (64, 64), (40, 70, 100)).save(reference)
            response = session.call("tools/call", {
                "name": "edit_image",
                "arguments": {"prompt": "recolor it", "images": [str(reference)], "dry_run": True},
            })
        content = response["result"]["structuredContent"]
        self.assertNotIn("annotation_applied", content)
        self.assertIsNone(content.get("markup"))
        self.assertIsNone(content.get("flatten_markup"))


if __name__ == "__main__":
    unittest.main()
