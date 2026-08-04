from __future__ import annotations

from pathlib import Path
import argparse
import json
import sys
import tempfile
import unittest

from PIL import Image


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import openai_images_transport as transport
from transport_common import (
    build_prompt_provenance,
    request_metadata_without_prompts,
    request_prompt,
    sanitize_provider_response_metadata,
)


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
    def test_openai_image_2_generation_defaults_to_4k(self) -> None:
        size, notes = transport.resolve_size(shape_args("gpt-image-2"), [])
        self.assertEqual(size, "2880x2880")
        self.assertIn("fallback_4k_square", notes)

    def test_image_2_edit_uses_4k_with_reference_aspect(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "reference.png"
            Image.new("RGB", (2048, 1024), (1, 2, 3)).save(image_path)
            openai_size, _ = transport.resolve_size(
                shape_args("gpt-image-2-token", "edit"),
                [image_path],
            )
            self.assertGreater(
                transport.parse_size(openai_size)[0] * transport.parse_size(openai_size)[1],
                2048 * 2048,
            )

    def test_explicit_resolution_still_overrides_image_2_default(self) -> None:
        args = shape_args("gpt-image-2")
        args.resolution = "1k"
        size, _ = transport.resolve_size(args, [])
        self.assertLessEqual(transport.parse_size(size)[0] * transport.parse_size(size)[1], 1_048_576)

    def test_explicit_medium_quality_keeps_image_2_at_2k(self) -> None:
        args = shape_args("gpt-image-2")
        args.quality = "medium"
        size, notes = transport.resolve_size(args, [])
        self.assertEqual(size, "2048x2048")
        self.assertIn("resolution_inferred_from_medium_quality", notes)

    def test_quality_maps_resolution_without_explicit_aspect(self) -> None:
        openai_args = shape_args("gpt-image-2")
        openai_args.quality = "high"
        openai_size, openai_notes = transport.resolve_size(openai_args, [])
        self.assertGreater(
            transport.parse_size(openai_size)[0] * transport.parse_size(openai_size)[1],
            2048 * 2048,
        )
        self.assertEqual(openai_size, "2880x2880")
        self.assertIn("resolution_inferred_from_high_quality", openai_notes)

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


class PromptProvenanceTests(unittest.TestCase):
    def test_echoed_provider_prompt_is_classified_without_duplicate_text(self) -> None:
        prompt = "Use case: cover\nComposition/framing: centered"
        provenance = build_prompt_provenance(
            prompt,
            {"data": [{"revised_prompt": prompt}]},
            source_prompt=prompt,
        )

        self.assertEqual(
            provenance,
            {
                "submitted": prompt,
                "changed": False,
                "provider_prompt_status": "echoed",
            },
        )
        self.assertEqual(list(provenance.values()).count(prompt), 1)

    def test_only_distinct_source_and_provider_rewrite_are_preserved(self) -> None:
        provenance = build_prompt_provenance(
            "submitted prompt",
            {"revised_prompt": "provider rewrite"},
            source_prompt="source prompt",
        )

        self.assertEqual(provenance["source"], "source prompt")
        self.assertEqual(provenance["submitted"], "submitted prompt")
        self.assertEqual(provenance["provider_revised"], "provider rewrite")
        self.assertEqual(provenance["provider_prompt_status"], "rewritten")
        self.assertTrue(provenance["changed"])

    def test_request_prompt_extraction_and_prompt_removal_support_nested_payloads(self) -> None:
        request = {
            "model": "image-model",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "nested prompt"},
                        {"type": "image_url", "image_url": {"url": "reference.png"}},
                    ],
                }
            ],
        }

        self.assertEqual(request_prompt(request), "nested prompt")
        sanitized = request_metadata_without_prompts(request)
        self.assertEqual(sanitized["model"], "image-model")
        self.assertNotIn("nested prompt", json.dumps(sanitized))
        self.assertNotIn("text", sanitized["messages"][0]["content"][0])


class ProviderResponseMetadataTests(unittest.TestCase):
    def test_sensitive_image_payload_and_prompt_fields_are_removed(self) -> None:
        metadata = sanitize_provider_response_metadata(
            {
                "id": "request-123",
                "model": "gpt-image-1",
                "usage": {"input_tokens": 10, "output_tokens": 20},
                "authorization": "secret",
                "api_key": "secret",
                "data": [
                    {
                        "index": 0,
                        "b64_json": "very-large-image-data",
                        "url": "https://signed.example.invalid/image",
                        "revised_prompt": "provider prompt",
                    }
                ],
            }
        )

        self.assertEqual(
            metadata,
            {
                "id": "request-123",
                "model": "gpt-image-1",
                "usage": {"input_tokens": 10, "output_tokens": 20},
                "data": [{"index": 0}],
            },
        )


if __name__ == "__main__":
    unittest.main()
