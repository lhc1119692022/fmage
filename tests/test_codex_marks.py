from __future__ import annotations

import base64
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

from test_annotation_autoload import AnnotationSession, gradient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import harness_plugins as hp  # noqa: E402

PROMPT = "将标注的那个瓶子改为粉色透明玻璃"


def data_url(image: Image.Image) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


def marked(image: Image.Image) -> Image.Image:
    copy = image.convert("RGBA")
    ImageDraw.Draw(copy).ellipse((20, 20, 70, 50), outline=(230, 30, 30, 255), width=3)
    return copy


class CodexLog:
    """Writes the records of one Codex session log."""

    def __init__(self, sessions: Path, name: str = "rollout-2026-11-07T00-24-17-a.jsonl") -> None:
        self.path = sessions / "2026" / "11" / "07" / name
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, record: dict[str, object]) -> None:
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record) + "\n")

    def user(self, *previews: Image.Image) -> None:
        content = [{"type": "image", "image_url": data_url(image)} for image in previews]
        self.append({
            "type": "event_msg",
            "payload": {"type": "item_completed", "item": {"type": "UserMessage", "content": content}},
        })

    def call(self, images: list[Path], prompt: str = PROMPT, call_id: str = "call-1") -> None:
        self.append({
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "edit_image",
                "call_id": call_id,
                "arguments": json.dumps({"images": [str(path) for path in images], "prompt": prompt}),
            },
        })

    def batch(self, images: list[Path], prompts: list[str], call_id: str = "call-1") -> None:
        self.append({
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "edit_image_batch",
                "call_id": call_id,
                "arguments": json.dumps({
                    "images": [str(path) for path in images],
                    "jobs": [{"prompt": prompt} for prompt in prompts],
                }),
            },
        })

    def output(self, call_id: str = "call-1") -> None:
        self.append({
            "type": "response_item",
            "payload": {"type": "function_call_output", "call_id": call_id, "output": "done"},
        })


class CodexMarksTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.sessions = self.root / "sessions"
        self.original = self.save(gradient((96, 64)), "original.png")
        self.log = CodexLog(self.sessions)

    def save(self, image: Image.Image, name: str) -> Path:
        path = self.root / name
        image.save(path)
        return path

    def session(self, model: str = "gpt-image-2.6") -> AnnotationSession:
        with patch.dict(os.environ, {"FMAGE_CODEX_SESSIONS": str(self.sessions)}):
            session = AnnotationSession(model, sidebar=False)
        self.addCleanup(session.close)
        return session

    def edit(self, session: AnnotationSession, images: list[Path]) -> dict[str, object]:
        return session.call("tools/call", {
            "name": "edit_image",
            "arguments": {"prompt": PROMPT, "images": [str(path) for path in images], "dry_run": True},
        })

    def succeeded(self, response: dict[str, object]) -> dict[str, object]:
        self.assertNotIn("error", response, json.dumps(response))
        self.assertFalse(response["result"].get("isError"), json.dumps(response))
        return response["result"]["structuredContent"]

    def test_marks_go_out_as_a_markup_layer_with_the_prompt_unchanged(self) -> None:
        self.log.user(marked(gradient((96, 64))))
        self.log.call([self.original])
        content = self.succeeded(self.edit(self.session(), [self.original]))
        self.assertEqual(content["annotation_applied"], "markup")
        self.assertEqual(content["prompt_submitted"], PROMPT)

    def test_a_model_without_the_channel_gets_the_marks_composited(self) -> None:
        self.log.user(marked(gradient((96, 64))))
        self.log.call([self.original])
        content = self.succeeded(self.edit(self.session("gpt-image-2"), [self.original]))
        self.assertEqual(content["annotation_applied"], "flatten")
        self.assertIsNone(content.get("markup"))

    def edit_batch(self, session: AnnotationSession, images: list[Path], prompts: list[str]) -> dict[str, object]:
        return session.call("tools/call", {
            "name": "edit_image_batch",
            "arguments": {
                "images": [str(path) for path in images],
                "jobs": [{"prompt": prompt} for prompt in prompts],
                "dry_run": True,
            },
        })

    def test_every_job_of_a_batch_carries_the_one_layer(self) -> None:
        prompts = ["把标注的瓶子换成蓝色，方案一", "把标注的瓶子换成蓝色，方案二"]
        self.log.user(marked(gradient((96, 64))))
        self.log.batch([self.original], prompts)
        content = self.succeeded(self.edit_batch(self.session(), [self.original], prompts))
        self.assertEqual([item["annotation_applied"] for item in content["job_statuses"]], ["markup"] * 2)
        self.assertEqual(content["prompts_submitted"], prompts)

    def test_repeated_jobs_still_match_their_call(self) -> None:
        self.log.user(marked(gradient((96, 64))))
        self.log.batch([self.original], [PROMPT] * 4)
        content = self.succeeded(self.edit_batch(self.session(), [self.original], [PROMPT] * 4))
        self.assertEqual([item["annotation_applied"] for item in content["job_statuses"]], ["markup"] * 4)

    def test_a_batch_with_marks_on_a_later_image_is_refused_whole(self) -> None:
        other = self.save(gradient((96, 64), flip=True), "other.png")
        self.log.user(marked(gradient((96, 64))))
        self.log.batch([other, self.original], [PROMPT, "第二个方案"])
        response = self.edit_batch(self.session(), [other, self.original], [PROMPT, "第二个方案"])
        self.assertIn("The Codex marks are on Image 2", response["error"]["message"])

    def test_marks_on_a_later_image_are_refused_before_the_provider(self) -> None:
        other = self.save(gradient((96, 64), flip=True), "other.png")
        self.log.user(marked(gradient((96, 64))))
        self.log.call([other, self.original])
        response = self.edit(self.session(), [other, self.original])
        self.assertIn("The Codex marks are on Image 2", response["error"]["message"])

    def test_marks_from_an_earlier_turn_are_not_reused(self) -> None:
        self.log.user(marked(gradient((96, 64))))
        self.log.call([self.original], call_id="first")
        self.log.output("first")
        self.log.user()
        self.log.call([self.original], call_id="second")
        content = self.succeeded(self.edit(self.session(), [self.original]))
        self.assertNotIn("annotation_applied", content)
        self.assertFalse(content.get("warnings"))

    def test_a_finished_call_is_not_this_request(self) -> None:
        self.log.user(marked(gradient((96, 64))))
        self.log.call([self.original])
        self.log.output()
        content = self.succeeded(self.edit(self.session(), [self.original]))
        self.assertNotIn("annotation_applied", content)
        self.assertIn("Could not check the Codex session", " ".join(content["warnings"]))

    def test_another_session_with_a_different_prompt_is_ignored(self) -> None:
        other = CodexLog(self.sessions, "rollout-2026-11-07T00-29-17-b.jsonl")
        other.user(marked(gradient((96, 64))))
        other.call([self.original], prompt="别的请求")
        self.log.user()
        self.log.call([self.original])
        content = self.succeeded(self.edit(self.session(), [self.original]))
        self.assertNotIn("annotation_applied", content)

    def test_an_unrelated_picture_of_the_same_size_is_not_marks(self) -> None:
        self.log.user(gradient((96, 64), flip=True))
        self.log.call([self.original])
        content = self.succeeded(self.edit(self.session(), [self.original]))
        self.assertNotIn("annotation_applied", content)
        self.assertFalse(content.get("warnings"))

    def test_the_layer_holds_only_the_marks(self) -> None:
        import codex_marks

        output = self.root / "layer.png"
        content = [{"type": "image", "image_url": data_url(marked(gradient((96, 64))))}]
        request = {"jobs": [{"images": [str(self.original)], "prompt": PROMPT}]}
        result = codex_marks.recover(content, request, output)
        self.assertEqual(result, {"status": "applied", "image": str(self.original)})
        with Image.open(output) as layer:
            self.assertEqual(layer.mode, "RGBA")
            self.assertEqual(layer.size, (96, 64))
            alpha = layer.getchannel("A")
            self.assertEqual(alpha.getextrema(), (0, 255))
            self.assertEqual(alpha.getpixel((2, 2)), 0)
            self.assertEqual(alpha.getbbox(), (20, 20, 71, 51))

    def test_the_sidebar_host_never_reads_session_logs(self) -> None:
        self.log.user(marked(gradient((96, 64))))
        self.log.call([self.original])
        with patch.dict(os.environ, {"FMAGE_CODEX_SESSIONS": str(self.sessions)}):
            session = AnnotationSession("gpt-image-2.6", sidebar=True)
        self.addCleanup(session.close)
        content = self.succeeded(self.edit(session, [self.original]))
        self.assertNotIn("annotation_applied", content)

    def test_the_deepseek_package_does_not_ship_the_session_reader(self) -> None:
        self.assertNotIn("scripts/codex_marks.py", hp.payload())


if __name__ == "__main__":
    unittest.main()
