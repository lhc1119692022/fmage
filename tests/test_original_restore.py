from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from PIL import Image, ImageCms, ImageDraw, ImageOps

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import original_restore  # noqa: E402
from image_fingerprint import fingerprint_of  # noqa: E402


def photo(size: tuple[int, int], seed: int = 1, alpha: bool = False) -> Image.Image:
    """A picture with gradients, shapes and fine grain, like a photo after a camera."""
    width, height = size
    rng = random.Random(seed)
    base = Image.linear_gradient("L").resize(size)
    image = Image.merge("RGB", (base, base.transpose(Image.Transpose.ROTATE_90).resize(size), base.point(lambda v: 255 - v)))
    draw = ImageDraw.Draw(image)
    for _ in range(12):
        x, y = rng.randrange(width), rng.randrange(height)
        r = rng.randrange(max(4, width // 20), max(8, width // 6))
        draw.ellipse((x - r, y - r, x + r, y + r), fill=tuple(rng.randrange(256) for _ in range(3)))
    grain = Image.effect_noise(size, 12).convert("RGB")
    image = Image.blend(image, grain, 0.08)
    if alpha:
        mask = Image.new("L", size, 255)
        ImageDraw.Draw(mask).rectangle((0, 0, width // 3, height), fill=0)
        image = image.convert("RGBA")
        image.putalpha(mask)
    return image


def host_copy(image: Image.Image, path: Path, budget: int = 2048 * 2048) -> Path:
    """What the host stores: oriented, scaled into its pixel budget, re-encoded."""
    image = ImageOps.exif_transpose(image)
    width, height = image.size
    scale = min(1.0, (budget / (width * height)) ** 0.5)
    size = max(1, round(width * scale)), max(1, round(height * scale))
    if "A" in image.getbands():
        image.convert("RGBa").resize(size, Image.Resampling.LANCZOS).convert("RGBA").save(path, format="WEBP", quality=75)
    else:
        image.convert("RGB").resize(size, Image.Resampling.LANCZOS).save(path, format="JPEG", quality=75)
    return path


def candidate(path: Path, displayed: Image.Image) -> dict[str, object]:
    """The record the composer uploads: the file plus what the browser saw."""
    return {
        "path": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "width": displayed.size[0],
        "height": displayed.size[1],
        "fingerprint": fingerprint_of(displayed.convert("RGBA"))["grid"],
    }


class OriginalRestoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.out = self.root / "out"
        self.out.mkdir()

    def restore(self, copy: Path, candidates: list[dict], **limits: int) -> dict:
        return original_restore.restore(
            copy, candidates, self.out,
            limits.get("max_pixels", original_restore.DEFAULT_MAX_PIXELS),
            limits.get("max_bytes", original_restore.DEFAULT_MAX_BYTES),
        )

    def test_the_original_behind_a_scaled_jpeg_copy_is_passed_through(self) -> None:
        source = photo((3000, 2000))
        original = self.root / "original.png"
        source.save(original)
        copy = host_copy(source, self.root / "copy")
        result = self.restore(copy, [candidate(original, source)])
        self.assertTrue(result["matched"], result)
        self.assertEqual((result["width"], result["height"]), (3000, 2000))
        self.assertEqual(result["notes"], [])
        self.assertEqual(Path(result["path"]).read_bytes(), original.read_bytes())
        self.assertEqual(Path(result["path"]).parent, self.out.resolve())
        self.assertLess(result["copy_width"], 3000)

    def test_a_locally_edited_original_is_rejected(self) -> None:
        source = photo((2400, 1600))
        copy = host_copy(source, self.root / "copy")
        for name, box in [("big", (1000, 600, 1300, 900)), ("small", (300, 300, 360, 360))]:
            edited = source.copy()
            ImageDraw.Draw(edited).rectangle(box, fill=(255, 0, 0))
            path = self.root / f"edited-{name}.png"
            edited.save(path)
            result = self.restore(copy, [candidate(path, edited)])
            self.assertFalse(result["matched"], (name, result))
            self.assertEqual(result["reason"], "no_match")
            self.assertEqual(result["rejected"][0]["reason"], "pixels_differ")

    def test_a_different_picture_is_not_a_candidate(self) -> None:
        source = photo((1600, 1200), seed=1)
        other = photo((1600, 1200), seed=2).transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        path = self.root / "other.png"
        other.save(path)
        copy = host_copy(source, self.root / "copy", budget=800 * 600)
        result = self.restore(copy, [candidate(path, other)])
        self.assertEqual(result, {"matched": False, "reason": "no_candidate"})

    def test_the_copy_itself_and_smaller_pictures_are_not_candidates(self) -> None:
        source = photo((800, 600))
        copy = self.root / "copy.png"
        source.save(copy)
        smaller = self.root / "smaller.png"
        source.resize((400, 300)).save(smaller)
        result = self.restore(copy, [candidate(copy, source), candidate(smaller, source.resize((400, 300)))])
        self.assertEqual(result["reason"], "no_candidate")

    def test_exif_orientation_is_applied(self) -> None:
        displayed = photo((1200, 1800))
        stored = displayed.transpose(Image.Transpose.ROTATE_90)  # what the camera wrote
        exif = Image.Exif()
        exif[0x0112] = 6  # rotate 90 CW to display
        original = self.root / "camera.jpg"
        stored.save(original, format="JPEG", quality=95, exif=exif.tobytes())
        with Image.open(original) as opened:
            shown = ImageOps.exif_transpose(opened).convert("RGB")
        self.assertEqual(shown.size, (1200, 1800))
        copy = host_copy(shown, self.root / "copy", budget=600 * 900)
        result = self.restore(copy, [candidate(original, shown)])
        self.assertTrue(result["matched"], result)
        self.assertEqual((result["width"], result["height"]), (1200, 1800))
        self.assertIn("exif_orientation_applied", result["notes"])
        with Image.open(result["path"]) as written:
            self.assertEqual(written.size, (1200, 1800))
            self.assertIn(written.getexif().get(0x0112, 1), (None, 1))

    def test_a_16_bit_original_is_reduced_to_8_bit(self) -> None:
        eight = photo((1000, 800)).convert("L")
        sixteen = eight.convert("I").point(lambda value: value * 256).convert("I;16")
        original = self.root / "deep.png"
        sixteen.save(original)
        copy = host_copy(eight, self.root / "copy", budget=500 * 400)
        result = self.restore(copy, [candidate(original, eight)])
        self.assertTrue(result["matched"], result)
        self.assertIn("depth_reduced_to_8_bit", result["notes"])
        with Image.open(result["path"]) as written:
            self.assertEqual(written.mode, "RGB")

    def test_a_non_srgb_profile_is_converted(self) -> None:
        source = photo((1000, 800))
        original = self.root / "profiled.png"
        srgb = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        source.save(original, icc_profile=srgb)
        copy = host_copy(source, self.root / "copy", budget=500 * 400)
        # Pretend the embedded profile is another space; the conversion path runs.
        with mock.patch.object(original_restore, "is_srgb_profile", return_value=False):
            result = self.restore(copy, [candidate(original, source)])
        self.assertTrue(result["matched"], result)
        self.assertIn("converted_to_srgb", result["notes"])
        self.assertTrue(result["path"].endswith(".png"))

    def test_an_original_over_the_markup_pixel_limit_is_scaled(self) -> None:
        source = photo((5200, 3400))  # 17.7 MP
        original = self.root / "huge.jpg"
        source.save(original, format="JPEG", quality=90)
        copy = host_copy(source, self.root / "copy")
        result = self.restore(copy, [candidate(original, source)])
        self.assertTrue(result["matched"], result)
        self.assertLessEqual(result["width"] * result["height"], original_restore.DEFAULT_MAX_PIXELS)
        self.assertAlmostEqual(result["width"] / result["height"], 5200 / 3400, places=2)
        self.assertIn("scaled_to_pixel_limit", result["notes"])
        self.assertTrue(result["path"].endswith(".jpg"))
        self.assertEqual((result["original_width"], result["original_height"]), (5200, 3400))

    def test_an_original_with_alpha_matches_its_webp_copy(self) -> None:
        source = photo((1600, 1200), alpha=True)
        original = self.root / "cutout.png"
        source.save(original)
        copy = host_copy(source, self.root / "copy", budget=800 * 600)
        result = self.restore(copy, [candidate(original, source)])
        self.assertTrue(result["matched"], result)
        with Image.open(result["path"]) as written:
            self.assertEqual(written.mode, "RGBA")
            self.assertEqual(written.getpixel((10, 10))[3], 0)

    def test_the_byte_limit_falls_back_to_lossless_webp_and_scales(self) -> None:
        source = Image.effect_noise((900, 600), 60).convert("RGB")
        original = self.root / "noise.bmp"
        source.save(original)
        copy = self.root / "copy.png"
        source.resize((450, 300), Image.Resampling.LANCZOS).save(copy)
        result = self.restore(copy, [candidate(original, source)], max_bytes=200_000)
        self.assertTrue(result["matched"], result)
        self.assertLessEqual(result["bytes"], 200_000)
        self.assertTrue(result["path"].endswith(".webp"))
        self.assertIn("reencoded_as_lossless_webp", result["notes"])
        self.assertIn("scaled_to_byte_limit", result["notes"])

    def test_an_animated_candidate_is_skipped(self) -> None:
        source = photo((800, 600))
        original = self.root / "moving.gif"
        frames = [source.convert("P"), source.transpose(Image.Transpose.FLIP_TOP_BOTTOM).convert("P")]
        frames[0].save(original, save_all=True, append_images=frames[1:])
        copy = host_copy(source, self.root / "copy", budget=400 * 300)
        result = self.restore(copy, [candidate(original, source)])
        self.assertFalse(result["matched"])
        self.assertIn("animated", result["rejected"][0]["reason"])

    def test_the_command_line_always_answers_json(self) -> None:
        source = photo((1200, 900))
        original = self.root / "original.png"
        source.save(original)
        copy = host_copy(source, self.root / "copy", budget=600 * 450)
        listing = self.root / "candidates.json"
        listing.write_text(json.dumps([candidate(original, source)]), encoding="utf-8")
        script = str(SCRIPTS / "original_restore.py")
        run = lambda *extra: json.loads(subprocess.run(  # noqa: E731
            [sys.executable, script, *extra], capture_output=True, text=True, check=True,
        ).stdout)
        matched = run("--copy", str(copy), "--candidates", str(listing), "--out-dir", str(self.root / "cli"))
        self.assertTrue(matched["matched"], matched)
        broken = run("--copy", str(self.root / "missing.jpg"), "--candidates", str(listing), "--out-dir", str(self.root / "cli"))
        self.assertFalse(broken["matched"])
        self.assertTrue(broken["reason"].startswith("error:"))


if __name__ == "__main__":
    unittest.main()
