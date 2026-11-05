from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

from PIL import Image

from test_prompt_policy import call_server, provider, provider_config, tool_map


class MarkupContractTests(unittest.TestCase):
    """The markup layer is gated before any provider request is submitted."""

    @staticmethod
    def config(model: str = "gpt-image-2.6", transport: str = "openai-images"):
        return provider_config(["test"], {"test": provider(model, transport)})

    @staticmethod
    def reference(directory: str, size: tuple[int, int] = (64, 64)) -> Path:
        path = Path(directory) / "reference.png"
        Image.new("RGB", size, (40, 70, 100)).save(path)
        return path

    @staticmethod
    def overlay(directory: str, size: tuple[int, int] = (64, 64)) -> Path:
        path = Path(directory) / "markup.png"
        layer = Image.new("RGBA", size, (0, 0, 0, 0))
        layer.putpixel((10, 10), (255, 0, 0, 255))
        layer.save(path)
        return path

    def call(self, config, arguments: dict[str, object], name: str = "edit_image"):
        return call_server(config, "tools/call", {"name": name, "arguments": arguments})

    def assert_rejected(self, response: dict[str, object], pattern: str) -> None:
        payload = json.dumps(response)
        rejected = (
            response.get("result", {}).get("isError") is True
            or isinstance(response.get("error"), dict)
        )
        self.assertTrue(rejected, payload)
        self.assertIn(pattern, payload)

    def test_edit_image_exposes_markup(self) -> None:
        tools = tool_map(call_server(self.config(), "tools/list", {}))
        self.assertIn("markup", tools["edit_image"]["inputSchema"]["properties"])
        self.assertNotIn("markup", tools["edit_image_batch"]["inputSchema"]["properties"])
        self.assertNotIn("markup", tools["generate_image"]["inputSchema"]["properties"])

    def test_markup_is_forwarded_to_the_transport_for_a_supported_model(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            reference = self.reference(directory)
            overlay = self.overlay(directory)
            response = self.call(
                self.config(),
                {
                    "prompt": "recolor the marked area",
                    "images": [str(reference)],
                    "markup": str(overlay),
                    "dry_run": True,
                },
            )
            self.assertNotIn("error", response, json.dumps(response))
            self.assertFalse(response["result"].get("isError"), json.dumps(response))
            content = response["result"]["structuredContent"]
            self.assertEqual(content["markup"], str(overlay.resolve()))
            self.assertEqual(content["endpoint"].rsplit("/", 1)[-1], "edits")

    def test_markup_is_rejected_for_older_models_before_the_provider_call(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            reference = self.reference(directory)
            overlay = self.overlay(directory)
            response = self.call(
                self.config("gpt-image-2"),
                {"prompt": "x", "images": [str(reference)], "markup": str(overlay), "dry_run": True},
            )
            self.assert_rejected(response, "gpt-image-2.5 or gpt-image-2.6")

    def test_markup_is_rejected_outside_the_openai_images_transport(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            reference = self.reference(directory)
            overlay = self.overlay(directory)
            response = self.call(
                self.config("gemini-3-pro-image-preview", "gemini-generate-content"),
                {"prompt": "x", "images": [str(reference)], "markup": str(overlay), "dry_run": True},
            )
            self.assert_rejected(response, "openai-images transport")

    def test_markup_is_rejected_for_a_non_first_primary_image(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            reference = self.reference(directory)
            overlay = self.overlay(directory)
            response = self.call(
                self.config(),
                {
                    "prompt": "x",
                    "images": [str(reference), str(reference)],
                    "markup": str(overlay),
                    "primary_image_index": 1,
                    "dry_run": True,
                },
            )
            self.assert_rejected(response, "only to the first input image")

    def test_batch_rejects_markup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            reference = self.reference(directory)
            overlay = self.overlay(directory)
            response = self.call(
                self.config(),
                {
                    "jobs": [{"prompt": "x", "images": [str(reference)]}],
                    "markup": str(overlay),
                    "dry_run": True,
                },
                name="edit_image_batch",
            )
            self.assert_rejected(response, "only by edit_image")


if __name__ == "__main__":
    unittest.main()
