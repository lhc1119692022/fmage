from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import struct
from typing import Any
import urllib.request


DEFAULT_MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024
DOWNLOAD_CHUNK_BYTES = 1024 * 1024
ALLOWED_IMAGE_CONTENT_TYPES = {
    "application/octet-stream",
    "binary/octet-stream",
}


def max_download_bytes() -> int:
    value = os.environ.get("FMAGE_MAX_DOWNLOAD_BYTES", "").strip()
    if not value:
        return DEFAULT_MAX_DOWNLOAD_BYTES
    try:
        parsed = int(value)
    except ValueError:
        return DEFAULT_MAX_DOWNLOAD_BYTES
    return parsed if parsed > 0 else DEFAULT_MAX_DOWNLOAD_BYTES


def parse_size(value: str | None) -> tuple[int, int] | None:
    if not value or value.lower() == "auto":
        return None
    import re

    match = re.fullmatch(r"\s*(\d+)\s*[xX*:,]\s*(\d+)\s*", value)
    if not match:
        raise ValueError(f"Invalid size '{value}'. Use WIDTHxHEIGHT or auto.")
    return int(match.group(1)), int(match.group(2))


def png_dimensions(data: bytes) -> tuple[int, int] | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        return struct.unpack(">II", data[16:24])
    return None


def jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    if not data.startswith(b"\xff\xd8"):
        return None
    index = 2
    while index + 9 < len(data):
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        index += 2
        if marker in {0xD8, 0xD9}:
            continue
        if index + 2 > len(data):
            return None
        length = int.from_bytes(data[index : index + 2], "big")
        if length < 2 or index + length > len(data):
            return None
        if 0xC0 <= marker <= 0xC3 or 0xC5 <= marker <= 0xC7 or 0xC9 <= marker <= 0xCB or 0xCD <= marker <= 0xCF:
            height = int.from_bytes(data[index + 3 : index + 5], "big")
            width = int.from_bytes(data[index + 5 : index + 7], "big")
            return width, height
        index += length
    return None


def webp_dimensions(data: bytes) -> tuple[int, int] | None:
    if len(data) < 30 or not data.startswith(b"RIFF") or data[8:12] != b"WEBP":
        return None
    chunk = data[12:16]
    if chunk == b"VP8X" and len(data) >= 30:
        width = 1 + int.from_bytes(data[24:27], "little")
        height = 1 + int.from_bytes(data[27:30], "little")
        return width, height
    if chunk == b"VP8 " and len(data) >= 30 and data[23:26] == b"\x9d\x01\x2a":
        width = int.from_bytes(data[26:28], "little") & 0x3FFF
        height = int.from_bytes(data[28:30], "little") & 0x3FFF
        return width, height
    if chunk == b"VP8L" and len(data) >= 25 and data[20] == 0x2F:
        b0, b1, b2, b3 = data[21], data[22], data[23], data[24]
        width = 1 + (((b1 & 0x3F) << 8) | b0)
        height = 1 + (((b3 & 0x0F) << 10) | (b2 << 2) | ((b1 & 0xC0) >> 6))
        return width, height
    return None


def image_dimensions(path: Path) -> tuple[int, int] | None:
    data = path.read_bytes()[:512 * 1024]
    return png_dimensions(data) or jpeg_dimensions(data) or webp_dimensions(data)


def sniff_extension(data: bytes, preferred: str = "png") -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8"):
        return "jpg"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "webp"
    return preferred if preferred in {"png", "jpg", "jpeg", "webp"} else "png"


def decode_b64_image(value: str) -> bytes:
    if "," in value and value.strip().lower().startswith("data:"):
        value = value.split(",", 1)[1]
    return base64.b64decode(value)


def validate_content_type(content_type: str | None) -> None:
    if not content_type:
        return
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type.startswith("image/") or media_type in ALLOWED_IMAGE_CONTENT_TYPES:
        return
    raise RuntimeError(f"Image download returned unexpected Content-Type: {content_type}")


def download_image(url: str, timeout: int, user_agent: str | None = None) -> bytes:
    headers = {"User-Agent": user_agent} if user_agent else {}
    request = urllib.request.Request(url, method="GET", headers=headers)
    limit = max_download_bytes()
    with urllib.request.urlopen(request, timeout=timeout) as response:
        validate_content_type(response.headers.get("Content-Type"))
        content_length = response.headers.get("Content-Length")
        if content_length and int(content_length) > limit:
            raise RuntimeError(f"Image download is larger than {limit} bytes.")

        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = response.read(DOWNLOAD_CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise RuntimeError(f"Image download exceeded {limit} bytes.")
            chunks.append(chunk)
    return b"".join(chunks)


def save_response_images_from_data(
    response: dict[str, Any],
    run_dir: Path,
    timeout: int,
    preferred_format: str = "png",
    base64_keys: tuple[str, ...] = ("b64_json",),
    user_agent: str | None = None,
) -> list[Path]:
    data = response.get("data")
    if not isinstance(data, list):
        raise RuntimeError(f"API response does not contain a data list: {json.dumps(response)[:1000]}")

    saved: list[Path] = []
    for index, item in enumerate(data, start=1):
        if not isinstance(item, dict):
            continue

        image_bytes: bytes | None = None
        for key in base64_keys:
            if item.get(key):
                image_bytes = decode_b64_image(str(item[key]))
                break
        if image_bytes is None and item.get("url"):
            image_bytes = download_image(str(item["url"]), timeout, user_agent=user_agent)

        if not image_bytes:
            continue

        ext = sniff_extension(image_bytes, preferred_format)
        path = run_dir / f"image_{index}.{ext}"
        path.write_bytes(image_bytes)
        saved.append(path)

    if not saved:
        raise RuntimeError(f"No images found in API response: {json.dumps(response)[:1000]}")
    return saved


def collect_image_metadata(images: list[Path]) -> list[dict[str, Any]]:
    metadata: list[dict[str, Any]] = []
    for path in images:
        item: dict[str, Any] = {"path": str(path.resolve())}
        dimensions = image_dimensions(path)
        if dimensions:
            width, height = dimensions
            item.update(
                {
                    "width": width,
                    "height": height,
                    "aspect_ratio": round(width / height, 6),
                }
            )
        metadata.append(item)
    return metadata


def output_size_warnings(requested_size: str | None, image_metadata: list[dict[str, Any]]) -> list[str]:
    try:
        requested_dimensions = parse_size(requested_size)
    except ValueError:
        return []
    if not requested_dimensions:
        return []

    requested_width, requested_height = requested_dimensions
    warnings: list[str] = []
    for index, item in enumerate(image_metadata, start=1):
        image_name = Path(str(item.get("path") or f"image_{index}")).name
        width = item.get("width")
        height = item.get("height")
        if not isinstance(width, int) or not isinstance(height, int):
            warnings.append(
                f"Could not determine actual dimensions for {image_name}; saved as-is with no local upscale or resize."
            )
            continue
        if width == requested_width and height == requested_height:
            continue
        relation = "smaller than" if width < requested_width or height < requested_height else "different from"
        warnings.append(
            f"Provider returned {image_name} at {width}x{height}, {relation} requested "
            f"{requested_width}x{requested_height}; saved as-is with no local upscale or resize."
        )
    return warnings


def write_latest_state(root: Path, manifest_path: Path, images: list[Path], updated_at: str) -> None:
    latest_path = root / "latest.json"
    latest_path.write_text(
        json.dumps(
            {
                "manifest": str(manifest_path.resolve()),
                "images": [str(path.resolve()) for path in images],
                "updated_at": updated_at,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
