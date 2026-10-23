from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path
import argparse
import json
import sys
import tempfile
import unittest
from unittest import mock

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
        moderation="low",
        background="auto",
        output_format="png",
        output_compression=None,
    )


class Image2DefaultResolutionTests(unittest.TestCase):
    def test_unframed_image_requests_defer_to_auto_size(self) -> None:
        for model in ("gpt-image-2", "gpt-image-2.5-flare", "gpt-image-2.5-sunburst"):
            with self.subTest(model=model):
                size, notes = transport.resolve_size(shape_args(model), [])
                self.assertEqual(size, "auto")
                self.assertIn("auto_size_without_framing_signal", notes)

    def test_quality_does_not_invent_a_canvas(self) -> None:
        for quality in ("low", "medium", "high", "xhigh", "max"):
            with self.subTest(quality=quality):
                args = shape_args("gpt-image-2.5-sunburst")
                args.quality = quality
                size, _ = transport.resolve_size(args, [])
                self.assertEqual(size, "auto")

    def test_edit_uses_reference_pixels_when_nothing_is_stated(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "reference.png"
            Image.new("RGB", (2048, 1024), (1, 2, 3)).save(image_path)
            openai_size, notes = transport.resolve_size(shape_args("gpt-image-2-token", "edit"), [image_path])
            self.assertEqual(openai_size, "2048x1024")
            self.assertIn("from_first_reference_image", notes)
            self.assertNotIn("default_resolution_2k", notes)

    def test_edit_resolution_keeps_primary_image_aspect(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            images = []
            for index, dimensions in enumerate(((1024, 1024), (1600, 800))):
                path = Path(temp_dir) / f"image-{index}.png"
                Image.new("RGB", dimensions, (1, 2, 3)).save(path)
                images.append(path)
            args = shape_args("gpt-image-2-token", "edit")
            args.resolution = "1k"
            size, _ = transport.resolve_size(args, images, primary_index=1)
            width, height = transport.parse_size(size)
            self.assertGreater(width / height, 1.9)
            self.assertLessEqual(width * height, 1_048_576)

    def test_explicit_resolution_still_overrides_image_2_default(self) -> None:
        args = shape_args("gpt-image-2")
        args.resolution = "1k"
        size, notes = transport.resolve_size(args, [])
        self.assertLessEqual(transport.parse_size(size)[0] * transport.parse_size(size)[1], 1_048_576)
        self.assertIn("square_size_from_resolution_without_aspect", notes)

    def test_quality_maps_resolution_once_an_aspect_exists(self) -> None:
        args = shape_args("gpt-image-2")
        args.quality = "high"
        args.aspect = "16:9"
        size, notes = transport.resolve_size(args, [])
        self.assertEqual(size, "2736x1536")
        self.assertIn("default_resolution_2k", notes)

    def test_low_quality_keeps_the_default_tier(self) -> None:
        args = shape_args("gpt-image-2")
        args.quality = "low"
        args.aspect = "16:9"
        size, notes = transport.resolve_size(args, [])
        self.assertEqual(size, "2736x1536")
        self.assertIn("default_resolution_2k", notes)

    def test_extended_quality_keeps_default_resolution_with_and_without_aspect(self) -> None:
        for quality in ("xhigh", "max"):
            for aspect in (None, "16:9"):
                with self.subTest(quality=quality, aspect=aspect):
                    args = shape_args("openai/gpt-image-2.5-sunburst")
                    args.quality = quality
                    args.aspect = aspect
                    size, notes = transport.resolve_size(args, [])
                    self.assertEqual(size, "2736x1536" if aspect else "auto")
                    if aspect:
                        self.assertIn("default_resolution_2k", notes)


class AspectSelectionTests(unittest.TestCase):
    def test_edit_reference_aspect_beats_expanded_prompt_keywords(self) -> None:
        prompt = (
            "Image 1: edit target - a production-style automotive photograph of a lilac-purple "
            "car on a wet apron with a cinematic wide panoramic sky and landscape behind it."
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            reference = Path(temp_dir) / "reference.png"
            Image.new("RGB", (2095, 726), (1, 2, 3)).save(reference)
            args = shape_args("gpt-image-2.5-sunburst", "edit")
            args.prompt = prompt
            args.resolution = "1k"
            size, notes = transport.resolve_size(args, [reference])
            self.assertEqual(size, "1744x608")
            self.assertIn("from_first_reference_image", notes)
            self.assertFalse([note for note in notes if note.startswith("semantic_")])

    def test_explicit_aspect_still_overrides_reference_image(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            reference = Path(temp_dir) / "reference.png"
            Image.new("RGB", (2095, 726), (1, 2, 3)).save(reference)
            args = shape_args("gpt-image-2.5-sunburst", "edit")
            args.resolution = "1k"
            args.aspect = "16:9"
            size, _ = transport.resolve_size(args, [reference])
            self.assertEqual(size, "1360x768")

    def test_subject_nouns_do_not_select_aspect(self) -> None:
        for prompt in (
            "a car on a wet road",
            "an suv parked beside a vehicle",
            "a landscape with a character in fashion clothing",
            "a room interior, documentary still",
        ):
            with self.subTest(prompt=prompt):
                self.assertEqual(transport.infer_aspect_from_prompt(prompt), (None, None))

    def test_framing_keywords_need_word_boundaries(self) -> None:
        cases = (
            ("an iconic brutalist tower", None),
            ("a cargo ship at dusk", None),
            ("storyboard of a chase", 16 / 9),
            ("a phone wallpaper gradient", 9 / 16),
        )
        for prompt, expected in cases:
            with self.subTest(prompt=prompt):
                aspect, _ = transport.infer_aspect_from_prompt(prompt)
                if expected is None:
                    self.assertIsNone(aspect)
                else:
                    self.assertAlmostEqual(aspect, expected)

    def test_ultrawide_phrase_resolves_to_three_to_one(self) -> None:
        aspect, note = transport.infer_aspect_from_prompt("ultra-wide banner of sand dunes")
        self.assertEqual(aspect, 3.0)
        self.assertEqual(note, "semantic_ultrawide")
        size, _ = transport.size_from_aspect(aspect, "1k")
        width, height = transport.parse_size(size)
        self.assertGreater(width / height, 2.9)


class PromptRatioTests(unittest.TestCase):
    def test_trailing_bare_ratio_sets_edit_canvas(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            reference = Path(temp_dir) / "reference.png"
            Image.new("RGB", (2095, 726), (1, 2, 3)).save(reference)
            args = shape_args("gpt-image-2.5-sunburst", "edit")
            args.prompt = "给天空增加一点蓝 16:9"
            args.resolution = "1k"
            size, notes = transport.resolve_size(args, [reference])
            self.assertEqual(size, "1360x768")
            self.assertIn("explicit_prompt_aspect_16:9", notes)
            self.assertNotIn("from_first_reference_image", notes)

    def test_ratio_wording_overrides_reference_image(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            reference = Path(temp_dir) / "reference.png"
            Image.new("RGB", (2095, 726), (1, 2, 3)).save(reference)
            args = shape_args("gpt-image-2.5-sunburst", "edit")
            args.prompt = "把图片改成4:3"
            args.resolution = "1k"
            size, notes = transport.resolve_size(args, [reference])
            self.assertEqual(size, "1184x880")
            self.assertIn("explicit_prompt_aspect_4:3", notes)

    def test_reference_ratio_survives_without_a_stated_ratio(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            reference = Path(temp_dir) / "reference.png"
            Image.new("RGB", (2095, 726), (1, 2, 3)).save(reference)
            args = shape_args("gpt-image-2.5-sunburst", "edit")
            args.prompt = "拍摄于 19:24 的黄昏，给天空增加一点蓝"
            args.resolution = "1k"
            size, notes = transport.resolve_size(args, [reference])
            self.assertEqual(size, "1744x608")
            self.assertIn("from_first_reference_image", notes)

    def test_prompt_pixel_size_outranks_ratio_wording(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            reference = Path(temp_dir) / "reference.png"
            Image.new("RGB", (2095, 726), (1, 2, 3)).save(reference)
            args = shape_args("gpt-image-2.5-sunburst", "edit")
            args.prompt = "改成 4:3，输出 1920x1080"
            args.resolution = "1k"
            size, notes = transport.resolve_size(args, [reference])
            self.assertEqual(size, "1920x1088")
            self.assertIn("explicit_prompt_size_1920x1080", notes)
            self.assertNotIn("explicit_prompt_aspect_4:3", notes)

    def test_prompt_pixel_size_ignores_the_resolution_tier(self) -> None:
        args = shape_args("gpt-image-2.5-sunburst")
        args.prompt = "3840x2160 的海报"
        args.resolution = "1k"
        size, _ = transport.resolve_size(args, [])
        self.assertEqual(size, "3840x2160")

    def test_small_pairs_remain_ratios_not_pixel_sizes(self) -> None:
        self.assertIsNone(transport.parse_prompt_size("改成 16x9"))
        self.assertEqual(transport.parse_prompt_aspect("改成 16x9"), (16 / 9, "16:9"))

    def test_negated_ratio_is_skipped(self) -> None:
        self.assertEqual(transport.parse_prompt_aspect("不要16比9，要4比3"), (4 / 3, "4:3"))
        self.assertEqual(transport.parse_prompt_aspect("不要 16:9"), (None, None))

    def test_pixel_dimensions_read_as_a_ratio(self) -> None:
        aspect, token = transport.parse_prompt_aspect("输出 1920x1080")
        self.assertAlmostEqual(aspect, 16 / 9)
        self.assertEqual(token, "1920:1080")


class EditReferencePreparationTests(unittest.TestCase):
    @staticmethod
    def horizontal_gradient(path: Path, size: tuple[int, int]) -> None:
        width, height = size
        strip = Image.new("L", (width, 1))
        strip.putdata([round(255 * index / (width - 1)) for index in range(width)])
        strip.resize(size).convert("RGB").save(path)

    def test_extend_replicates_edges_instead_of_padding_transparent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            reference = Path(temp_dir) / "reference.png"
            self.horizontal_gradient(reference, (600, 200))
            output, notes = transport.prepare_edit_reference(reference, "320x240", Path(temp_dir))
            with Image.open(output) as image:
                self.assertEqual(image.size, (320, 240))
                self.assertEqual(image.mode, "RGBA")
                self.assertEqual(image.getpixel((0, 0))[3], 255)
                self.assertLess(image.getpixel((0, 0))[0], 10)
            self.assertIn("reference_image_edges_extended_without_cropping", notes)
            self.assertNotIn("reference_image_padded_without_cropping", notes)

    def test_crop_intent_center_crops_to_the_requested_canvas(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            reference = Path(temp_dir) / "reference.png"
            self.horizontal_gradient(reference, (600, 200))
            output, notes = transport.prepare_edit_reference(
                reference, "320x240", Path(temp_dir), crop=True
            )
            with Image.open(output) as image:
                self.assertEqual(image.size, (320, 240))
                self.assertGreater(image.getpixel((0, 0))[0], 40)
            self.assertIn("reference_center_cropped_to_requested_ratio", notes)

    def test_crop_intent_detection_distinguishes_resize_from_crop(self) -> None:
        self.assertTrue(transport.has_crop_intent("裁成16:9"))
        self.assertTrue(transport.has_crop_intent("crop it to 16:9"))
        self.assertFalse(transport.has_crop_intent("改成16:9"))
        self.assertFalse(transport.has_crop_intent("给天空增加一点蓝"))
        self.assertFalse(transport.has_crop_intent("Do not crop; keep the full frame."))


class Image25QualityTests(unittest.TestCase):
    def test_model_family_recognition_tolerates_decorated_ids(self) -> None:
        cases = (
            ("gpt-image-2", False, True),
            ("gpt-image-2-token", False, True),
            ("openai/gpt-image-2.5-sunburst", True, True),
            ("relay/openai/GPT-IMAGE-2.5-FLARE", True, True),
            ("渠道【GPT_IMAGE_2.5_sunburst】", True, True),
            ("zenmux:gpt image 2.5", True, True),
            ("gpt-image-2.5.1", True, True),
            ("gpt-image-2.50", False, True),
            ("openai/gpt-image-20", False, True),
            ("openai/gpt-image-1", False, True),
            ("openai/gpt-image-3", False, True),
            ("openai/GPT-IMAGE-3.1-preview", False, True),
            ("notgpt-image-2.5", False, False),
            ("gpt-image-2.5ish", False, False),
            ("gpt-image-2.5.1oops", False, False),
            ("gpt-image-2ish", False, False),
            ("gemini-3.1-flash-image-preview", False, False),
            ("", False, False),
            (None, False, False),
        )
        for model, image_2_5, gpt_image in cases:
            with self.subTest(model=model):
                self.assertEqual(transport.is_image_2_5_model(model), image_2_5)
                self.assertEqual(transport.is_gpt_image_model(model), gpt_image)

    def test_namespaced_models_preserve_extended_quality_and_transparency(self) -> None:
        for command in ("generate", "edit"):
            for quality in ("xhigh", "max"):
                with self.subTest(command=command, quality=quality):
                    args = transport.build_parser().parse_args([
                        command, "--prompt", "test", "--model", "openai/gpt-image-2.5-sunburst",
                        "--quality", quality, "--background", "transparent",
                    ])
                    transport.validate_common(args)
                    payload = transport.common_payload(args, "test", "2048x2048")
                    self.assertEqual(payload["model"], "openai/gpt-image-2.5-sunburst")
                    self.assertEqual(payload["quality"], quality)
                    self.assertEqual(payload["background"], "transparent")

    def test_image_2_5_quality_tiers_are_accepted(self) -> None:
        parser = transport.build_parser()
        for quality in ("xhigh", "max"):
            args = parser.parse_args(
                [
                    "generate",
                    "--prompt",
                    "test",
                    "--model",
                    "gpt-image-2.5",
                    "--quality",
                    quality,
                ]
            )
            transport.validate_common(args)

    def test_legacy_image_models_reject_new_quality_tiers(self) -> None:
        parser = transport.build_parser()
        for quality in ("xhigh", "max"):
            args = parser.parse_args(
                [
                    "generate",
                    "--prompt",
                    "test",
                    "--model",
                    "gpt-image-2",
                    "--quality",
                    quality,
                ]
            )
            with self.assertRaisesRegex(ValueError, "supported only for gpt-image-2.5"):
                transport.validate_common(args)

    def test_lookalike_models_reject_extended_quality(self) -> None:
        for model in ("gpt-image-2.50", "openai/gpt-image-2.50", "gpt-image-20", "gpt-image-2.5ish"):
            with self.subTest(model=model):
                args = transport.build_parser().parse_args([
                    "generate", "--prompt", "test", "--model", model, "--quality", "max",
                ])
                with self.assertRaisesRegex(ValueError, "supported only for gpt-image-2.5"):
                    transport.validate_common(args)


class RequestHeaderTests(unittest.TestCase):
    def test_json_request_sets_provider_compatible_headers(self) -> None:
        captured: dict[str, object] = {}

        class FakeResponse(BytesIO):
            def __init__(self):
                super().__init__(b'{"data": []}')

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        def fake_urlopen(request, timeout):
            captured["headers"] = dict(request.headers)
            return FakeResponse()

        with mock.patch.object(transport.urllib.request, "urlopen", fake_urlopen):
            transport.json_request(
                "https://example.test/v1/images/generations",
                {"model": "gpt-image-2", "prompt": "test"},
                "test-key",
                10,
            )

        headers = {str(key).lower(): str(value) for key, value in captured["headers"].items()}
        self.assertEqual(headers["accept"], "application/json")
        self.assertEqual(headers["user-agent"], transport.CLIENT_USER_AGENT)


class EndpointTests(unittest.TestCase):
    def test_image_endpoint_adds_v1_for_host_root(self) -> None:
        self.assertEqual(
            transport.endpoint("https://api-direct.ezaiclub.com", "/images/generations"),
            "https://api-direct.ezaiclub.com/v1/images/generations",
        )

    def test_image_endpoint_does_not_duplicate_v1(self) -> None:
        self.assertEqual(
            transport.endpoint("https://api-direct.ezaiclub.com/v1", "/images/generations"),
            "https://api-direct.ezaiclub.com/v1/images/generations",
        )


class TransparentBackgroundTests(unittest.TestCase):
    def test_transparent_background_is_forwarded_for_gpt_image_models(self) -> None:
        args = shape_args("gpt-image-2")
        args.background = "transparent"
        transport.validate_common(args)

        payload = transport.common_payload(args, "transparent asset", "1024x1024")
        self.assertEqual(payload["background"], "transparent")
        self.assertEqual(payload["output_format"], "png")
        self.assertEqual(transport.protected_output_fields(args), {"background", "output_format"})

    def test_transparent_background_allows_webp_when_explicitly_requested(self) -> None:
        args = shape_args("gpt-image-2")
        args.background = "transparent"
        args.output_format = "webp"
        transport.validate_common(args)

    def test_transparent_background_rejects_jpeg_before_provider_request(self) -> None:
        args = shape_args("gpt-image-2")
        args.background = "transparent"
        args.output_format = "jpeg"
        with self.assertRaisesRegex(ValueError, "requires --output-format png or webp"):
            transport.validate_common(args)

    def test_transparent_background_is_restricted_to_gpt_image_models(self) -> None:
        args = shape_args("legacy-image-model")
        args.background = "transparent"
        with self.assertRaisesRegex(ValueError, "only for GPT Image models"):
            transport.validate_common(args)

    def test_transparent_png_bytes_preserve_the_alpha_channel_when_saved(self) -> None:
        image = Image.new("RGBA", (2, 2), (10, 20, 30, 0))
        image.putpixel((0, 0), (10, 20, 30, 255))
        buffer = BytesIO()
        image.save(buffer, format="PNG")

        with tempfile.TemporaryDirectory() as temp_dir:
            saved = transport.save_response_images(
                {"data": [{"b64_json": base64.b64encode(buffer.getvalue()).decode("ascii")}]},
                Path(temp_dir),
                "png",
                10,
            )
            with Image.open(saved[0]) as loaded:
                self.assertEqual(loaded.mode, "RGBA")
                self.assertEqual(loaded.getchannel("A").getextrema(), (0, 255))


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

    def test_transparent_output_fields_are_not_dropped_on_compatibility_retry(self) -> None:
        calls: list[dict[str, object]] = []
        error = transport.ApiError(400, "Unsupported background")

        def fail(payload: dict[str, object]) -> dict[str, object]:
            calls.append(payload)
            raise error

        with self.assertRaises(transport.ApiError):
            transport.request_with_compat_retry(
                fail,
                {
                    "model": "gpt-image-2",
                    "prompt": "transparent asset",
                    "background": "transparent",
                    "output_format": "png",
                },
                "generate",
                protected_fields={"background", "output_format"},
            )

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["background"], "transparent")
        self.assertEqual(calls[0]["output_format"], "png")


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
