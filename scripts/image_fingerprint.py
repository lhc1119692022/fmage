#!/usr/bin/env python3
"""Coarse content fingerprint that ties a sidebar annotation to its image.

The sidebar computes the same grid in the browser when it saves a layer
(adapters/deepseek/client.js, `annFingerprint`). The two only need to agree
closely, not exactly: the conversation may hand the edit a resized or
re-encoded copy of the picture the user drew on, so the server compares the
grids with a tolerance instead of hashing bytes.

Grid: the oriented image is split into GRID x GRID cells with integer
boundaries floor(i * size / GRID); each cell holds the mean of the ITU-R 601
luma premultiplied by alpha (transparent pixels count as black).

Usage: image_fingerprint.py IMAGE  ->  {"width": W, "height": H, "grid": [...]}
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

GRID = 16


def cell_bounds(size: int) -> list[tuple[int, int]]:
    bounds = []
    for index in range(GRID):
        start = index * size // GRID
        end = max(start + 1, (index + 1) * size // GRID)
        bounds.append((min(start, size - 1), min(end, size)))
    return bounds


def fingerprint(path: Path) -> dict[str, object]:
    from PIL import Image, ImageChops, ImageOps, ImageStat

    with Image.open(path) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGBA")
    width, height = image.size
    luma = ImageChops.multiply(image.convert("RGB").convert("L"), image.getchannel("A"))
    grid = []
    for top, bottom in cell_bounds(height):
        for left, right in cell_bounds(width):
            mean = ImageStat.Stat(luma.crop((left, top, right, bottom))).mean[0]
            grid.append(round(mean, 1))
    return {"width": width, "height": height, "grid": grid}


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: image_fingerprint.py IMAGE", file=sys.stderr)
        return 2
    try:
        print(json.dumps(fingerprint(Path(argv[1]))))
    except Exception as error:  # an unreadable image simply has no fingerprint
        print(f"fingerprint failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
