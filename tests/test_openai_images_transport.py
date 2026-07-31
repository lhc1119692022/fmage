from __future__ import annotations

from pathlib import Path
import sys
import unittest


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import openai_images_transport as transport


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
