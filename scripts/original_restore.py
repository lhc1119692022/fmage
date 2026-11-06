#!/usr/bin/env python3
"""Recognize a host-normalized copy and write the original it came from.

DeepSeek Harness hands tools a normalized copy of every attached picture: at
most 2048x2048 pixels and 4 MiB, oriented, 8-bit sRGB, metadata stripped, and
re-encoded as JPEG (opaque) or WebP (with alpha) when it had to change. The
Desktop sidebar stores the file the user actually picked (see
adapters/deepseek/originals-api.mjs). This script decides whether one of those
stored originals is the picture behind a copy and, if so, writes it into the
run's directory in a form every transport accepts.

A candidate is accepted only when all of these hold:
  * its stored 16x16 luma grid is within FINGERPRINT_TOLERANCE of the copy's
    and its aspect ratio within ASPECT_TOLERANCE;
  * it has at least as many pixels as the copy and different bytes;
  * after orientation, sRGB conversion and scaling to the copy's size, every
    block of the two images agrees within the error a JPEG/WebP re-encode
    leaves (BLOCK_MEAN_TOLERANCE, BLOCK_ERROR_TOLERANCE).

The written file is the original's own bytes when they need no change.
Otherwise it is re-encoded: oriented, 8-bit sRGB, at most --max-pixels pixels,
PNG (JPEG at quality 95 for an opaque JPEG source), then lossless WebP when
that exceeds --max-bytes, scaled down further if still too large.

Usage:
  original_restore.py --copy COPY --candidates LIST.json --out-dir DIR
                      [--max-pixels N] [--max-bytes N]
LIST.json is [{"path", "sha256", "width", "height", "fingerprint"}, ...].
Prints {"matched": true, "path", "sha256", ...} or {"matched": false, "reason"}.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from image_fingerprint import fingerprint_of  # noqa: E402

FINGERPRINT_TOLERANCE = 3.0
ASPECT_TOLERANCE = 0.02
# Blocks per image, roughly; each block is at least MIN_BLOCK pixels a side.
BLOCKS = 1024
MIN_BLOCK = 8
# Per block and channel, on premultiplied 0-255 values: the mean may drift
# this far, and the mean absolute pixel error may reach this.
BLOCK_MEAN_TOLERANCE = 8.0
BLOCK_ERROR_TOLERANCE = 24.0
# Over the whole image.
GLOBAL_ERROR_TOLERANCE = 6.0
MAX_CANDIDATES = 3
DEFAULT_MAX_PIXELS = 16_777_216
DEFAULT_MAX_BYTES = 50 * 1024 * 1024
WEBP_MAX_SIDE = 16383
PASSTHROUGH_FORMATS = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp"}


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint_distance(left: list, right: list) -> float:
    if not isinstance(left, list) or not isinstance(right, list) or len(left) != len(right) or not left:
        return math.inf
    return sum(abs(float(a) - float(b)) for a, b in zip(left, right)) / len(left)


def same_shape(left: tuple[int, int], right: tuple[int, int]) -> bool:
    return abs(math.log((left[0] / left[1]) / (right[0] / right[1]))) <= ASPECT_TOLERANCE


def has_alpha(image) -> bool:
    return "A" in image.getbands() or "transparency" in image.info


def is_srgb_profile(icc: bytes) -> bool:
    try:
        from PIL import ImageCms

        profile = ImageCms.ImageCmsProfile(io.BytesIO(icc))
        description = ImageCms.getProfileDescription(profile) or ""
    except Exception:
        return False
    return "srgb" in description.lower().replace(" ", "")


def to_srgb_8bit(image) -> tuple[object, list[str]]:
    """The image as 8-bit RGB or RGBA in sRGB, plus what had to change.

    Raises when an embedded colour profile cannot be applied: guessing would
    send different colours than the user sees.
    """
    from PIL import Image, ImageCms

    changes: list[str] = []
    if image.mode in {"I;16", "I;16B", "I;16L", "I;16N", "I"}:
        image = image.convert("I").point(lambda value: value / 256).convert("L")
        changes.append("depth_reduced_to_8_bit")
    elif image.mode == "F":
        image = image.point(lambda value: value * 255).convert("L")
        changes.append("depth_reduced_to_8_bit")
    alpha = has_alpha(image)
    icc = image.info.get("icc_profile")
    if icc and not is_srgb_profile(icc):
        if image.mode not in {"RGB", "RGBA", "L", "LA", "CMYK"}:
            image = image.convert("RGBA" if alpha else "RGB")
        source = ImageCms.ImageCmsProfile(io.BytesIO(icc))
        target = ImageCms.createProfile("sRGB")
        alpha_band = image.getchannel("A") if "A" in image.getbands() else None
        base = image.convert("RGB") if image.mode in {"RGBA", "LA"} else image
        if base.mode == "L":
            base = base.convert("RGB")
        converted = ImageCms.profileToProfile(base, source, target, outputMode="RGB")
        if converted is None:
            raise ValueError("colour profile conversion failed")
        if alpha_band is not None:
            converted.putalpha(alpha_band)
        image = converted
        changes.append("converted_to_srgb")
    wanted = "RGBA" if alpha else "RGB"
    if image.mode != wanted:
        if image.mode not in {"RGB", "RGBA", "L", "P"}:
            changes.append(f"mode_{image.mode.lower().replace(';', '_')}_converted")
        image = image.convert(wanted)
    return image, changes


def premultiplied(image):
    """RGBA with colour scaled by alpha, so hidden colour under transparency is ignored."""
    from PIL import Image, ImageChops

    rgba = image.convert("RGBA")
    alpha = rgba.getchannel("A")
    bands = [ImageChops.multiply(band, alpha) for band in rgba.split()[:3]]
    return Image.merge("RGBA", (*bands, alpha))


def resized(image, size: tuple[int, int]):
    from PIL import Image

    if image.size == size:
        return image
    if image.mode == "RGBA":
        return image.convert("RGBa").resize(size, Image.Resampling.LANCZOS).convert("RGBA")
    return image.resize(size, Image.Resampling.LANCZOS)


def block_grid(size: tuple[int, int]) -> tuple[int, int]:
    width, height = size
    side = max(MIN_BLOCK, math.sqrt(width * height / BLOCKS))
    return max(1, round(width / side)), max(1, round(height / side))


def compare(original, copy) -> dict[str, float]:
    """Block statistics between the original scaled to the copy and the copy.

    Each premultiplied band is compared as its own greyscale image: Pillow
    resizes RGBA by premultiplying it, which would zero a difference image
    whose alpha is mostly 0.
    """
    from PIL import Image, ImageChops, ImageStat

    left = premultiplied(resized(original, copy.size)).split()
    right = premultiplied(copy).split()
    grid = block_grid(copy.size)
    mean_drift = block_error = global_error = 0.0
    for a, b in zip(left, right):
        means = ImageChops.difference(a.resize(grid, Image.Resampling.BOX), b.resize(grid, Image.Resampling.BOX))
        mean_drift = max(mean_drift, means.getextrema()[1])
        difference = ImageChops.difference(a, b)
        block_error = max(block_error, difference.resize(grid, Image.Resampling.BOX).getextrema()[1])
        global_error = max(global_error, ImageStat.Stat(difference).mean[0])
    return {"block_mean_drift": float(mean_drift), "block_error": float(block_error), "global_error": round(global_error, 3)}


def within_tolerance(stats: dict[str, float]) -> bool:
    return (
        stats["block_mean_drift"] <= BLOCK_MEAN_TOLERANCE
        and stats["block_error"] <= BLOCK_ERROR_TOLERANCE
        and stats["global_error"] <= GLOBAL_ERROR_TOLERANCE
    )


def load_oriented(path: Path):
    """Open, refuse animations, apply EXIF orientation. Returns (image, format, rotated)."""
    from PIL import Image, ImageOps

    with Image.open(path) as opened:
        if getattr(opened, "n_frames", 1) > 1:
            raise ValueError("animated images are not restored")
        source_format = opened.format
        orientation = opened.getexif().get(0x0112, 1)
        image = ImageOps.exif_transpose(opened)
        image.load()
        if image is opened:
            image = opened.copy()
        icc = opened.info.get("icc_profile")
        if icc:
            image.info["icc_profile"] = icc
    return image, source_format, orientation not in (None, 1)


def fit_pixels(size: tuple[int, int], max_pixels: int, max_side: int | None = None) -> tuple[int, int]:
    width, height = size
    scale = min(1.0, math.sqrt(max_pixels / (width * height)))
    if max_side:
        scale = min(scale, max_side / max(width, height))
    if scale >= 1.0:
        return size
    fitted = max(1, math.floor(width * scale)), max(1, math.floor(height * scale))
    while fitted[0] * fitted[1] > max_pixels:
        fitted = max(1, fitted[0] - 1), max(1, fitted[1] - 1)
    return fitted


def encode(image, path: Path, kind: str) -> Path:
    if kind == "jpg":
        image.convert("RGB").save(path, format="JPEG", quality=95, subsampling=0, optimize=True)
    elif kind == "webp":
        image.save(path, format="WEBP", lossless=True, quality=80, method=4)
    else:
        image.save(path, format="PNG", compress_level=6)
    return path


def write_restored(
    original_path: Path,
    image,
    source_format: str | None,
    changes: list[str],
    out_dir: Path,
    stem: str,
    max_pixels: int,
    max_bytes: int,
) -> tuple[Path, tuple[int, int], list[str]]:
    """The file to send: the original bytes when they need nothing, else a clean re-encode."""
    extension = PASSTHROUGH_FORMATS.get(source_format or "")
    pixels = image.size[0] * image.size[1]
    if not changes and extension and pixels <= max_pixels and original_path.stat().st_size <= max_bytes:
        target = out_dir / f"{stem}.{extension}"
        shutil.copyfile(original_path, target)
        return target, image.size, []

    notes = list(changes)
    size = fit_pixels(image.size, max_pixels)
    if size != image.size:
        image = resized(image, size)
        notes.append("scaled_to_pixel_limit")
    kind = "jpg" if source_format == "JPEG" and image.mode == "RGB" else "png"
    target = encode(image, out_dir / f"{stem}.{kind}", kind)
    if target.stat().st_size <= max_bytes:
        return target, image.size, notes
    target.unlink(missing_ok=True)
    notes.append("reencoded_as_lossless_webp")
    current = image
    size = fit_pixels(image.size, max_pixels, WEBP_MAX_SIDE)
    for attempt in range(6):
        if size != current.size:
            current = resized(image, size)
        target = encode(current, out_dir / f"{stem}.webp", "webp")
        written = target.stat().st_size
        if written <= max_bytes:
            if current.size != image.size:
                notes.append("scaled_to_byte_limit")
            return target, current.size, notes
        target.unlink(missing_ok=True)
        scale = math.sqrt(max_bytes / written) * 0.95
        size = max(1, math.floor(current.size[0] * scale)), max(1, math.floor(current.size[1] * scale))
    raise ValueError("the original could not be encoded under the byte limit")


def restore(copy_path: Path, candidates: list[dict], out_dir: Path, max_pixels: int, max_bytes: int) -> dict:
    from PIL import Image

    copy_sha = sha256_of(copy_path)
    with Image.open(copy_path) as opened:
        if getattr(opened, "n_frames", 1) > 1:
            return {"matched": False, "reason": "copy_is_animated"}
        copy, _ = to_srgb_8bit(opened.convert("RGBA") if has_alpha(opened) else opened.convert("RGB"))
    copy_size = copy.size
    copy_grid = fingerprint_of(copy.convert("RGBA"))["grid"]

    ranked = []
    for candidate in candidates:
        try:
            size = int(candidate["width"]), int(candidate["height"])
            path = Path(candidate["path"])
        except (KeyError, TypeError, ValueError):
            continue
        if min(size) <= 0 or candidate.get("sha256") == copy_sha or not path.is_file():
            continue
        if size[0] * size[1] < copy_size[0] * copy_size[1] or not same_shape(size, copy_size):
            continue
        distance = fingerprint_distance(candidate.get("fingerprint"), copy_grid)
        if distance <= FINGERPRINT_TOLERANCE:
            ranked.append((distance, candidate))
    if not ranked:
        return {"matched": False, "reason": "no_candidate"}
    ranked.sort(key=lambda item: item[0])

    rejected = []
    for distance, candidate in ranked[:MAX_CANDIDATES]:
        path = Path(candidate["path"])
        try:
            image, source_format, rotated = load_oriented(path)
            image, changes = to_srgb_8bit(image)
            if rotated:
                changes.insert(0, "exif_orientation_applied")
            if not same_shape(image.size, copy_size) or image.size[0] * image.size[1] < copy_size[0] * copy_size[1]:
                rejected.append({"sha256": candidate.get("sha256"), "reason": "shape"})
                continue
            stats = compare(image, copy)
            if not within_tolerance(stats):
                rejected.append({"sha256": candidate.get("sha256"), "reason": "pixels_differ", **stats})
                continue
            stem = f"original-{str(candidate.get('sha256') or 'image')[:12]}"
            target, size, notes = write_restored(
                path, image, source_format, changes, out_dir, stem, max_pixels, max_bytes,
            )
        except Exception as error:  # an unreadable candidate is skipped, never fatal
            rejected.append({"sha256": candidate.get("sha256"), "reason": f"error: {error}"[:200]})
            continue
        return {
            "matched": True,
            "sha256": candidate.get("sha256"),
            "path": str(target.resolve()),
            "width": size[0],
            "height": size[1],
            "bytes": target.stat().st_size,
            "original_width": image.size[0],
            "original_height": image.size[1],
            "original_bytes": path.stat().st_size,
            "copy_width": copy_size[0],
            "copy_height": copy_size[1],
            "copy_bytes": copy_path.stat().st_size,
            "fingerprint_distance": round(distance, 3),
            "comparison": stats,
            "notes": notes,
        }
    return {"matched": False, "reason": "no_match", "rejected": rejected}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--copy", required=True)
    parser.add_argument("--candidates", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--max-pixels", type=int, default=DEFAULT_MAX_PIXELS)
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    args = parser.parse_args(argv[1:])
    try:
        candidates = json.loads(Path(args.candidates).read_text(encoding="utf-8"))
        if not isinstance(candidates, list):
            raise ValueError("candidates must be a JSON list")
        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        result = restore(Path(args.copy), candidates, out_dir, args.max_pixels, args.max_bytes)
    except Exception as error:  # the caller keeps the copy on any failure
        result = {"matched": False, "reason": f"error: {error}"[:300]}
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
