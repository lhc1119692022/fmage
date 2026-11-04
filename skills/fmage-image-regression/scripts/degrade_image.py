#!/usr/bin/env python3
"""Apply the fixed Fmage image-regression pipeline in one local pass."""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime
from pathlib import Path

try:
    from PIL import Image, ImageChops, ImageFilter, ImageOps
except ModuleNotFoundError as error:
    if error.name == "PIL":
        raise SystemExit("Pillow is required: install the 'Pillow' package and run once again.") from error
    raise


MAX_PIXELS = 1_048_576
BLUR_SIGMA_PX = 1.2
NOISE_AMOUNT_PERCENT = 0.5
NOISE_SIGMA_LEVELS = 255.0 * NOISE_AMOUNT_PERCENT / 100.0


def target_size(width: int, height: int) -> tuple[int, int]:
    area = width * height
    if area <= MAX_PIXELS:
        return width, height

    scale = math.sqrt(MAX_PIXELS / area)
    target_width = max(1, math.floor(width * scale))
    target_height = max(1, math.floor(height * scale))

    if target_width * target_height > MAX_PIXELS:
        if target_width >= target_height:
            target_width = max(1, MAX_PIXELS // target_height)
        else:
            target_height = max(1, MAX_PIXELS // target_width)

    return target_width, target_height


def unique_output_path(output_dir: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    attempt = 0
    while True:
        suffix = "" if attempt == 0 else f"-{attempt}"
        candidate = output_dir / f"{timestamp}-001{suffix}.png"
        if not candidate.exists():
            return candidate
        attempt += 1


def add_monochrome_luminance_noise(image: Image.Image) -> Image.Image:
    has_alpha = image.mode == "RGBA"
    rgb = image.convert("RGB")
    alpha = image.getchannel("A") if has_alpha else None

    noise = Image.effect_noise(image.size, NOISE_SIGMA_LEVELS)
    shared_noise = Image.merge("RGB", (noise, noise, noise))
    noisy_rgb = ImageChops.add(rgb, shared_noise, scale=1.0, offset=-128)

    if alpha is None:
        return noisy_rgb
    red, green, blue = noisy_rgb.split()
    return Image.merge("RGBA", (red, green, blue, alpha))


def process_image(input_path: Path, output_path: Path) -> dict[str, object]:
    input_path = input_path.expanduser().resolve(strict=True)
    output_path = output_path.expanduser().resolve(strict=False)
    if output_path.suffix.lower() != ".png":
        raise ValueError("Output path must end in .png")

    with Image.open(input_path) as source:
        icc_profile = source.info.get("icc_profile")
        oriented = ImageOps.exif_transpose(source)
        oriented.load()

    original_width, original_height = oriented.size
    output_width, output_height = target_size(original_width, original_height)
    has_alpha = "A" in oriented.getbands() or (
        oriented.mode == "P" and "transparency" in oriented.info
    )
    working = oriented.convert("RGBA" if has_alpha else "RGB")

    resized = (output_width, output_height) != (original_width, original_height)
    if resized:
        working = working.resize((output_width, output_height), Image.Resampling.LANCZOS)

    blurred = working.filter(ImageFilter.GaussianBlur(radius=BLUR_SIGMA_PX))
    processed = add_monochrome_luminance_noise(blurred)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    save_options: dict[str, object] = {
        "format": "PNG",
        "optimize": False,
        "compress_level": 1,
    }
    if icc_profile:
        save_options["icc_profile"] = icc_profile
    processed.save(output_path, **save_options)

    return {
        "input": str(input_path),
        "output": str(output_path),
        "original_size": [original_width, original_height],
        "output_size": [output_width, output_height],
        "resized": resized,
        "max_pixels": MAX_PIXELS,
        "blur_sigma_px": BLUR_SIGMA_PX,
        "noise_amount_percent": NOISE_AMOUNT_PERCENT,
        "noise_sigma_levels": NOISE_SIGMA_LEVELS,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Shrink to at most 1K total pixels, blur, and add monochrome luminance noise."
    )
    parser.add_argument("--input", required=True, type=Path, help="Source raster image.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Destination directory; defaults to the input file's directory.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        output_dir = (args.output_dir or args.input.parent).expanduser().resolve(strict=False)
        output_path = unique_output_path(output_dir)
        result = process_image(args.input, output_path)
        result["output_dir"] = str(output_dir)
    except Exception as error:
        print(f"Fmage image regression failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
