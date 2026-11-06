from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from PIL import Image

SERVER_PATH = Path(__file__).resolve().parents[1] / "mcp" / "server.mjs"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from image_fingerprint import fingerprint  # noqa: E402


def gradient(size: tuple[int, int], flip: bool = False) -> Image.Image:
    """A picture with structure, so its fingerprint is not flat."""
    width, height = size
    image = Image.new("RGB", size)
    image.putdata([
        ((x * 255 // width), (y * 255 // height), 255 - (x * 255 // width) if flip else 90)
        for y in range(height) for x in range(width)
    ])
    return image.transpose(Image.Transpose.FLIP_LEFT_RIGHT) if flip else image


def binding_of(path: Path) -> dict[str, object]:
    return {
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "fingerprint": fingerprint(path)["grid"],
    }


class AnnotationSession:
    """Runs the MCP server against a data home that already holds one layer."""

    def __init__(
        self,
        model: str,
        transport: str = "openai-images",
        cache_dir: bool = True,
        sidebar: bool = True,
    ) -> None:
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
        self.environment.pop("FMAGE_SIDEBAR", None)
        if sidebar:
            self.environment["FMAGE_SIDEBAR"] = "1"
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

    def add_layer(
        self,
        mode: str,
        size: tuple[int, int] = (64, 64),
        root: Path | None = None,
        binding: dict[str, object] | None = None,
        entry_id: str = "abc123abc123abc1",
    ) -> Path:
        directory = (root or self.data_home / "cache" / "annotations") / entry_id
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
            **({"version": 2, **binding} if binding else {}),
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


    def edit(self, session: AnnotationSession, image: Path) -> dict[str, object]:
        response = session.call("tools/call", {
            "name": "edit_image",
            "arguments": {"prompt": "follow the marks", "images": [str(image)], "dry_run": True},
        })
        payload = json.dumps(response)
        self.assertNotIn("error", response, payload)
        self.assertFalse(response["result"].get("isError"), payload)
        return response["result"]

    def test_a_layer_bound_by_sha256_attaches_to_that_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            session = AnnotationSession("gpt-image-2.6")
            self.addCleanup(session.close)
            image = Path(directory) / "cat.png"
            gradient((96, 64)).save(image)
            layer = session.add_layer("markup", (96, 64), binding={"sha256": hashlib.sha256(image.read_bytes()).hexdigest()})
            result = self.edit(session, image)
        content = result["structuredContent"]
        self.assertEqual(content["annotation_applied"], "markup")
        self.assertEqual(Path(content["markup"]).resolve(), layer.resolve())
        text = "\n".join(item.get("text", "") for item in result["content"])
        self.assertIn("Annotation: the markup layer was sent with Image 1", text)

    def test_a_bound_layer_recognizes_a_resized_reencoded_copy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            session = AnnotationSession("gpt-image-2.6")
            self.addCleanup(session.close)
            original = Path(directory) / "original.png"
            gradient((400, 300)).save(original)
            layer = session.add_layer("flatten", (400, 300), binding=binding_of(original))
            copy = Path(directory) / "attachment.jpg"
            Image.open(original).resize((399, 299), Image.Resampling.LANCZOS).save(copy, format="JPEG", quality=85)
            result = self.edit(session, copy)
        content = result["structuredContent"]
        self.assertEqual(content["annotation_applied"], "flatten")
        self.assertEqual(Path(content["flatten_markup"]).resolve(), layer.resolve())
        text = "\n".join(item.get("text", "") for item in result["content"])
        self.assertIn("Annotation: the marks were composited into the copy of Image 1", text)

    def test_a_layer_for_another_picture_is_skipped_without_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            # The model has no markup channel: a wrongly matched markup layer
            # would fail the call, so success proves the layer was skipped.
            session = AnnotationSession("gpt-image-2")
            self.addCleanup(session.close)
            drawn_on = Path(directory) / "drawn.png"
            gradient((96, 64)).save(drawn_on)
            layer = session.add_layer("markup", (96, 64), binding=binding_of(drawn_on))
            other = Path(directory) / "other.png"
            gradient((96, 64), flip=True).save(other)
            result = self.edit(session, other)
            meta = json.loads((layer.parent / "meta.json").read_text(encoding="utf-8"))
        content = result["structuredContent"]
        self.assertNotIn("annotation_applied", content)
        self.assertIsNone(content.get("markup"))
        self.assertIsNone(content.get("flatten_markup"))
        text = "\n".join(item.get("text", "") for item in result["content"])
        self.assertNotIn("Annotation:", text)
        self.assertIsNone(meta["consumedAt"])

    def test_the_layer_for_the_edited_picture_wins_over_a_newer_one(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            session = AnnotationSession("gpt-image-2.6")
            self.addCleanup(session.close)
            mine = Path(directory) / "mine.png"
            gradient((96, 64)).save(mine)
            other = Path(directory) / "other.png"
            gradient((96, 64), flip=True).save(other)
            layer = session.add_layer("markup", (96, 64), binding=binding_of(mine), entry_id="aaaaaaaaaaaaaaaa")
            session.add_layer("flatten", (96, 64), binding=binding_of(other), entry_id="bbbbbbbbbbbbbbbb")
            result = self.edit(session, mine)
        content = result["structuredContent"]
        self.assertEqual(content["annotation_applied"], "markup")
        self.assertEqual(Path(content["markup"]).resolve(), layer.resolve())

    def assert_ran_without_a_layer(self, result: dict[str, object]) -> None:
        content = result["structuredContent"]
        self.assertNotIn("annotation_applied", content)
        self.assertNotIn("annotation_waiting_not_matched", content)
        self.assertIsNone(content.get("markup"))
        self.assertIsNone(content.get("flatten_markup"))

    def test_a_marked_copy_swapped_into_the_composer_edits_without_a_layer(self) -> None:
        # A flatten save that swaps the composer image writes no layer at all.
        with tempfile.TemporaryDirectory() as directory:
            session = AnnotationSession("gpt-image-2.6")
            self.addCleanup(session.close)
            marked = Path(directory) / "marked.png"
            gradient((96, 64)).save(marked)
            result = self.edit(session, marked)
        self.assert_ran_without_a_layer(result)

    def test_a_comments_only_save_edits_without_a_layer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            session = AnnotationSession("gpt-image-2.6")
            self.addCleanup(session.close)
            image = Path(directory) / "cat.png"
            gradient((96, 64)).save(image)
            response = session.call("tools/call", {
                "name": "edit_image",
                "arguments": {
                    "prompt": "图像 1： 1. (x: 22.1%, y: 26%) 改成红色",
                    "images": [str(image)],
                    "dry_run": True,
                },
            })
        self.assertFalse(response["result"].get("isError"), json.dumps(response))
        self.assert_ran_without_a_layer(response["result"])

    def test_an_edit_after_the_layer_was_used_still_runs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            session = AnnotationSession("gpt-image-2.6")
            self.addCleanup(session.close)
            image = Path(directory) / "cat.png"
            gradient((96, 64)).save(image)
            layer = session.add_layer("markup", (96, 64), binding=binding_of(image))
            meta_path = layer.parent / "meta.json"
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            meta["consumedAt"] = __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ).isoformat()
            meta_path.write_text(json.dumps(meta), encoding="utf-8")
            result = self.edit(session, image)
        self.assert_ran_without_a_layer(result)

    def test_a_host_without_the_sidebar_ignores_a_shared_layer(self) -> None:
        # DeepSeek and Codex may name one cache_dir: Codex must not use or
        # retire a layer the sidebar saved for the very same picture.
        with tempfile.TemporaryDirectory() as directory:
            session = AnnotationSession("gpt-image-2.6", sidebar=False)
            self.addCleanup(session.close)
            image = Path(directory) / "cat.png"
            gradient((96, 64)).save(image)
            layer = session.add_layer("markup", (96, 64), binding=binding_of(image))
            result = self.edit(session, image)
            meta = json.loads((layer.parent / "meta.json").read_text(encoding="utf-8"))
        self.assert_ran_without_a_layer(result)
        self.assertIsNone(meta["consumedAt"])


if __name__ == "__main__":
    unittest.main()
