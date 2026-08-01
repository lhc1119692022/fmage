from __future__ import annotations

from pathlib import Path
import argparse
import sys
import tempfile
import unittest

from PIL import Image


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import openai_images_transport as transport
import json_images_transport as json_transport


def shape_args(model: str, command: str = "generate") -> argparse.Namespace:
    return argparse.Namespace(
        size=None,
        aspect=None,
        command=command,
        prompt="plain test image",
        prompt_file=None,
        resolution=None,
        quality=None,
        model=model,
    )


class Image2DefaultResolutionTests(unittest.TestCase):
    def test_openai_image_2_generation_defaults_to_1k(self) -> None:
        size, notes = transport.resolve_size(shape_args("gpt-image-2"), [])
        self.assertEqual(size, "1024x1024")
        self.assertIn("fallback_1k_square", notes)

    def test_json_image_2_variants_default_to_1k(self) -> None:
        size, notes = json_transport.resolve_size(shape_args("gpt-image-2-vip"), [])
        self.assertEqual(size, "1024x1024")
        self.assertIn("fallback_1k_square", notes)

    def test_image_2_edit_uses_1k_with_reference_aspect(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "reference.png"
            Image.new("RGB", (2048, 1024), (1, 2, 3)).save(image_path)
            openai_size, _ = transport.resolve_size(
                shape_args("gpt-image-2-token", "edit"),
                [image_path],
            )
            json_size, _ = json_transport.resolve_size(
                shape_args("gpt-image-2-vip", "edit"),
                [str(image_path)],
            )
            self.assertLessEqual(
                transport.parse_size(openai_size)[0] * transport.parse_size(openai_size)[1],
                1_048_576,
            )
            self.assertLessEqual(
                json_transport.parse_size(json_size)[0] * json_transport.parse_size(json_size)[1],
                1_048_576,
            )

    def test_explicit_resolution_still_overrides_image_2_default(self) -> None:
        args = shape_args("gpt-image-2")
        args.resolution = "2k"
        size, _ = transport.resolve_size(args, [])
        self.assertGreater(transport.parse_size(size)[0] * transport.parse_size(size)[1], 1_048_576)


class ImageFieldSelectionTests(unittest.TestCase):
    def test_auto_uses_singular_field_for_one_image(self) -> None:
        self.assertEqual(transport.resolve_image_field("auto", 1), "image")

    def test_auto_uses_array_field_for_multiple_images(self) -> None:
        self.assertEqual(transport.resolve_image_field("auto", 2), "image[]")

    def test_explicit_field_is_preserved(self) -> None:
        self.assertEqual(transport.resolve_image_field("image[]", 1), "image[]")
        self.assertEqual(transport.resolve_image_field("image", 3), "image")


class ImageFieldRetryTests(unittest.TestCase):
    def test_retries_explicit_missing_image_parameter_error(self) -> None:
        error = transport.ApiError(
            400,
            '{"error":{"message":"Missing required parameter: image","param":"image"}}',
        )
        self.assertTrue(transport.should_retry_image_field(error))

    def test_retries_multipart_array_shape_error(self) -> None:
        error = transport.ApiError(422, "Expected image[] field to be an array of files.")
        self.assertTrue(transport.should_retry_image_field(error))

    def test_does_not_retry_unrelated_request_error(self) -> None:
        error = transport.ApiError(400, '{"error":{"message":"Invalid model","param":"model"}}')
        self.assertFalse(transport.should_retry_image_field(error))

    def test_does_not_retry_auth_or_server_errors(self) -> None:
        self.assertFalse(transport.should_retry_image_field(transport.ApiError(401, "Missing image field.")))
        self.assertFalse(transport.should_retry_image_field(transport.ApiError(500, "Missing image field.")))

    def test_field_error_bypasses_optional_parameter_retries(self) -> None:
        calls: list[dict[str, object]] = []
        error = transport.ApiError(400, "Missing required multipart field image.")

        def fail(payload: dict[str, object]) -> dict[str, object]:
            calls.append(payload)
            raise error

        with self.assertRaises(transport.ApiError):
            transport.request_with_compat_retry(
                fail,
                {"prompt": "edit", "quality": "high"},
                "edit",
                abort_retry=transport.should_retry_image_field,
            )

        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
