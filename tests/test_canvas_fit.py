from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import banana_models
import ezai_banana_transport as ezai
import gemini_generate_content_transport as gemini


class CanvasFitTests(unittest.TestCase):
    def test_first_request_fits_explicit_ratio_size_and_reference(self):
        for transport, model in ((gemini, "gemini-3.1-flash-image-preview"),
                                 (ezai, "nano-banana-2")):
            for command in ("generate", "edit"):
                for mode in ("ratio", "size", "reference"):
                    if command == "generate" and mode == "reference":
                        continue
                    with self.subTest(transport=transport.TRANSPORT_NAME, command=command, mode=mode), tempfile.TemporaryDirectory() as directory:
                        reference = Path(directory) / "reference.png"
                        # A local reference is read by the real payload builder; mocked inline encoding
                        # keeps this test independent of image decoding and provider access.
                        reference.write_bytes(b"test-reference")
                        argv = [command, "--model", model, "--base-url", "https://example.invalid",
                                "--prompt", "Keep the lettering unchanged.", "--resolution", "4k",
                                "--output-dir", directory, "--dry-run"]
                        if mode == "ratio":
                            argv.extend(["--aspect", "2:1"])
                        elif mode == "size":
                            argv.extend(["--size", "4096x2048"])
                        if command == "edit":
                            argv.extend(["--image", str(reference)])
                        args = transport.build_parser().parse_args(argv)
                        with mock.patch.object(transport, "image_dimensions", return_value=(4096, 2048)), mock.patch.object(
                            gemini, "inline_image_part", return_value={"inlineData": {"mimeType": "image/png", "data": "dGVzdA=="}}
                        ), mock.patch.object(gemini, "json_request", side_effect=AssertionError("network forbidden")):
                            if transport is gemini:
                                result = transport.run_request(args, [str(reference)] if command == "edit" else [])
                                prompt = result["request"]["contents"][0]["parts"][0]["text"]
                                wire_aspect = result["request"]["generationConfig"]["imageConfig"]["aspectRatio"]
                            else:
                                result = transport.run_edit(args) if command == "edit" else transport.run_generate(args)
                                prompt = result["request"]["prompt"]
                                wire_aspect = result["request"]["aspect_ratio"]
                        self.assertEqual(wire_aspect, "16:9")
                        self.assertEqual(result["requested_size"], "4K@16:9")
                        self.assertTrue(prompt.startswith("Keep the lettering unchanged."))
                        self.assertIn("by outpainting only", prompt)
                        self.assertIn("Do not crop, stretch, squeeze", prompt)

    def test_supported_ratios_and_equivalent_tokens_do_not_change_prompt(self):
        for ratio in ("16:9", "32:18", "21:9", "7:3"):
            notes = []
            aspect = banana_models.fit_canvas_aspect(gemini.TRANSPORT_NAME, "gemini-3.1-flash-image-preview", ratio, notes)
            self.assertIn(aspect, ("16:9", "21:9"))
            self.assertEqual(notes, [])
            self.assertEqual(banana_models.apply_canvas_fit("exact prompt", aspect, notes), "exact prompt")

    def test_invalid_ratios_are_not_silently_fitted(self):
        for ratio in ("0:1", "-2:1", "2:0", "garbage", "nan", "inf"):
            with self.subTest(ratio=ratio), self.assertRaises(ValueError):
                banana_models.fit_canvas_aspect(gemini.TRANSPORT_NAME, "gemini-3.1-flash-image-preview", ratio, [])


if __name__ == "__main__":
    unittest.main()
