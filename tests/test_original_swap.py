"""edit_image sends the composer's original in place of the host's normalized copy."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
import threading
import time
import unittest

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_annotation_autoload import AnnotationSession, binding_of  # noqa: E402
from test_original_restore import candidate, host_copy, photo  # noqa: E402


def iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


class OriginalSwapTests(unittest.TestCase):
    def session(self, model: str = "gpt-image-2.6", transport: str = "openai-images") -> AnnotationSession:
        session = AnnotationSession(model, transport=transport)
        self.addCleanup(session.close)
        return session

    def store_original(self, session: AnnotationSession, image: Image.Image, status: str = "ready") -> Path:
        staging = session.data_home / f"staging-{time.time_ns()}.png"
        image.save(staging)
        record = candidate(staging, image)
        directory = session.data_home / "cache" / "originals" / str(record["sha256"])
        directory.mkdir(parents=True)
        now = iso(datetime.now(timezone.utc))
        meta = {
            "version": 1, "sha256": record["sha256"], "status": status,
            "width": image.size[0], "height": image.size[1], "fingerprint": record["fingerprint"],
            "startedAt": now,
        }
        if status == "ready":
            (directory / "original.png").write_bytes(staging.read_bytes())
            meta.update({"bytes": staging.stat().st_size, "format": "png", "file": "original.png",
                         "createdAt": now, "seenAt": now, "usedAt": None})
        else:
            (directory / "original.part").write_bytes(staging.read_bytes()[:100])
        (directory / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        return directory

    def attachment(self, session: AnnotationSession, image: Image.Image) -> Path:
        """The host's copy, at the path shape of its attachment store."""
        copy = host_copy(image, session.data_home / f"copy-{time.time_ns()}")
        digest = hashlib.sha256(copy.read_bytes()).hexdigest()
        path = session.data_home / "attachments" / "v1" / "objects" / digest[:2] / digest
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(copy.read_bytes())
        return path

    def edit(self, session: AnnotationSession, *images: Path) -> dict:
        response = session.call("tools/call", {
            "name": "edit_image",
            "arguments": {"prompt": "make it dusk", "images": [str(image) for image in images], "dry_run": True},
        })
        payload = json.dumps(response)
        self.assertNotIn("error", response, payload)
        self.assertFalse(response["result"].get("isError"), payload)
        return response["result"]

    def test_the_original_goes_instead_of_the_hosts_copy(self) -> None:
        session = self.session()
        source = photo((3000, 2000))
        stored = self.store_original(session, source)
        copy = self.attachment(session, source)
        result = self.edit(session, copy)
        content = result["structuredContent"]
        self.assertEqual(len(content["original_restored"]), 1)
        restored = content["original_restored"][0]
        self.assertEqual((restored["image"], restored["width"], restored["height"]), (1, 3000, 2000))
        self.assertLess(restored["copy_width"], 3000)
        self.assertNotEqual(Path(content["images"][0]), copy)
        self.assertEqual(content["request"]["size"], "3008x2000")
        text = "\n".join(item.get("text", "") for item in result["content"])
        self.assertIn("Original: Image 1 uses the attached file at 3000x2000", text)
        self.assertNotIn("Original", content["prompt_submitted"])
        meta = json.loads((stored / "meta.json").read_text(encoding="utf-8"))
        self.assertIsNotNone(meta["usedAt"])
        self.assertTrue((stored / "original.png").is_file(), "the stored original is copied, not moved")

    def test_only_recognized_copies_in_the_attachment_store_are_replaced(self) -> None:
        session = self.session()
        first, second = photo((2600, 1800), seed=3), photo((2600, 1800), seed=4)
        self.store_original(session, first)
        elsewhere = session.data_home / "downloads" / "copy.jpg"
        elsewhere.parent.mkdir()
        host_copy(first, elsewhere)
        unknown = self.attachment(session, second)
        known = self.attachment(session, first)
        content = self.edit(session, elsewhere, unknown, known)["structuredContent"]
        self.assertEqual([item["image"] for item in content["original_restored"]], [3])
        self.assertEqual(Path(content["images"][0]), elsewhere)
        self.assertEqual(Path(content["images"][1]), unknown)
        self.assertNotEqual(Path(content["images"][2]), known)

    def test_an_upload_still_arriving_is_waited_for(self) -> None:
        session = self.session()
        source = photo((2800, 2000), seed=5)
        directory = self.store_original(session, source, status="uploading")
        copy = self.attachment(session, source)
        staging = session.data_home / "late.png"
        source.save(staging)

        def finish() -> None:
            time.sleep(1.5)
            (directory / "original.png").write_bytes(staging.read_bytes())
            meta = json.loads((directory / "meta.json").read_text(encoding="utf-8"))
            now = iso(datetime.now(timezone.utc))
            meta.update({"status": "ready", "file": "original.png", "format": "png",
                         "bytes": staging.stat().st_size, "createdAt": now, "seenAt": now, "usedAt": None})
            (directory / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

        worker = threading.Thread(target=finish)
        worker.start()
        self.addCleanup(worker.join)
        content = self.edit(session, copy)["structuredContent"]
        self.assertEqual(content["original_restored"][0]["width"], 2800)

    def test_an_abandoned_upload_is_not_waited_for(self) -> None:
        session = self.session()
        source = photo((2800, 2000), seed=6)
        directory = self.store_original(session, source, status="uploading")
        meta = json.loads((directory / "meta.json").read_text(encoding="utf-8"))
        meta["startedAt"] = iso(datetime.now(timezone.utc) - timedelta(minutes=30))
        (directory / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        copy = self.attachment(session, source)
        started = time.monotonic()
        content = self.edit(session, copy)["structuredContent"]
        self.assertLess(time.monotonic() - started, 4.5)
        self.assertNotIn("original_restored", content)
        self.assertEqual(Path(content["images"][0]), copy)

    def test_other_transports_keep_the_copy(self) -> None:
        session = self.session(model="gemini-3-pro-image-preview", transport="gemini-generate-content")
        source = photo((2600, 1800), seed=7)
        self.store_original(session, source)
        copy = self.attachment(session, source)
        content = self.edit(session, copy)["structuredContent"]
        self.assertNotIn("original_restored", content)

    def test_a_layer_bound_to_the_original_follows_it(self) -> None:
        session = self.session()
        source = photo((2400, 1600), seed=8)
        drawn_on = session.data_home / "drawn.png"
        source.save(drawn_on)
        self.store_original(session, source)
        layer = session.add_layer("markup", (2400, 1600), binding=binding_of(drawn_on))
        copy = self.attachment(session, source)
        content = self.edit(session, copy)["structuredContent"]
        self.assertEqual(content["annotation_applied"], "markup")
        self.assertEqual(Path(content["markup"]).resolve(), layer.resolve())
        self.assertEqual(content["original_restored"][0]["width"], 2400)
        self.assertNotIn("annotation_waiting_not_matched", content)

    def test_a_waiting_layer_for_another_picture_is_reported(self) -> None:
        session = self.session()
        drawn_on = session.data_home / "drawn.png"
        photo((640, 480), seed=9).save(drawn_on)
        session.add_layer("markup", (640, 480), binding=binding_of(drawn_on))
        other = session.data_home / "other.png"
        photo((640, 480), seed=10).transpose(Image.Transpose.FLIP_LEFT_RIGHT).save(other)
        content = self.edit(session, other)["structuredContent"]
        self.assertNotIn("annotation_applied", content)
        self.assertTrue(content["annotation_waiting_not_matched"])


if __name__ == "__main__":
    unittest.main()
