from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import struct
import time
from typing import Any
import urllib.request


DEFAULT_MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024
DOWNLOAD_CHUNK_BYTES = 1024 * 1024
ALLOWED_IMAGE_CONTENT_TYPES = {
    "application/octet-stream",
    "binary/octet-stream",
}

_PROVIDER_METADATA_OMIT_KEYS = {
    "authorization",
    "b64_json",
    "base64",
    "binary",
    "bytes",
    "content",
    "cookie",
    "cookies",
    "credential",
    "credentials",
    "headers",
    "image",
    "image_bytes",
    "image_data",
    "image_url",
    "image_urls",
    "images",
    "input_text",
    "prompt",
    "provider_prompt",
    "revised_prompt",
    "secret",
    "secrets",
    "set_cookie",
    "text",
    "token",
    "tokens",
    "url",
    "urls",
}
_PROVIDER_METADATA_SECRET_FRAGMENTS = (
    "api_key",
    "apikey",
    "access_token",
    "auth_token",
    "refresh_token",
    "signature",
    "signed_url",
)
_PROVIDER_METADATA_MAX_DEPTH = 6
_PROVIDER_METADATA_MAX_ITEMS = 50
_PROVIDER_METADATA_MAX_STRING_CHARS = 2048


def max_download_bytes() -> int:
    value = os.environ.get("FMAGE_MAX_DOWNLOAD_BYTES", "").strip()
    if not value:
        return DEFAULT_MAX_DOWNLOAD_BYTES
    try:
        parsed = int(value)
    except ValueError:
        return DEFAULT_MAX_DOWNLOAD_BYTES
    return parsed if parsed > 0 else DEFAULT_MAX_DOWNLOAD_BYTES


def _non_empty_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def request_prompt(request: Any) -> str | None:
    if not isinstance(request, dict):
        return None

    direct = _non_empty_string(request.get("prompt"))
    if direct:
        return direct

    messages = request.get("messages")
    if isinstance(messages, list):
        parts: list[str] = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if isinstance(content, str):
                text = _non_empty_string(content)
                if text:
                    parts.append(text)
            elif isinstance(content, list):
                for item in content:
                    if not isinstance(item, dict):
                        continue
                    text = _non_empty_string(item.get("text")) or _non_empty_string(
                        item.get("input_text")
                    )
                    if text:
                        parts.append(text)
        if parts:
            return "\n\n".join(parts)

    instances = request.get("instances")
    if isinstance(instances, list):
        parts = [
            prompt
            for item in instances
            if isinstance(item, dict)
            for prompt in [_non_empty_string(item.get("prompt"))]
            if prompt
        ]
        if parts:
            return "\n\n".join(parts)

    contents = request.get("contents")
    if isinstance(contents, list):
        parts = []
        for content in contents:
            if not isinstance(content, dict):
                continue
            content_parts = content.get("parts")
            if not isinstance(content_parts, list):
                continue
            for item in content_parts:
                if not isinstance(item, dict):
                    continue
                text = _non_empty_string(item.get("text"))
                if text:
                    parts.append(text)
        if parts:
            return "\n\n".join(parts)
    return None


def request_metadata_without_prompts(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            normalized_key = str(key).strip().lower().replace("-", "_")
            if normalized_key in {
                "prompt",
                "provider_prompt",
                "revised_prompt",
                "text",
                "input_text",
            }:
                continue
            cleaned = request_metadata_without_prompts(item)
            if cleaned in ({}, [], None):
                continue
            sanitized[key] = cleaned
        return sanitized
    if isinstance(value, list):
        sanitized_items = [request_metadata_without_prompts(item) for item in value]
        return [item for item in sanitized_items if item not in ({}, [], None)]
    return value


def provider_revised_prompts(response: Any) -> list[str]:
    if not isinstance(response, dict):
        return []
    candidates: list[Any] = [response.get("revised_prompt")]
    data = response.get("data")
    if isinstance(data, list):
        candidates.extend(
            item.get("revised_prompt")
            for item in data
            if isinstance(item, dict)
        )

    prompts: list[str] = []
    for candidate in candidates:
        prompt = _non_empty_string(candidate)
        if prompt and prompt not in prompts:
            prompts.append(prompt)
    return prompts


def build_prompt_provenance(
    submitted_prompt: str | None,
    response: Any = None,
    source_prompt: str | None = None,
) -> dict[str, Any]:
    submitted = _non_empty_string(submitted_prompt)
    source = _non_empty_string(source_prompt)
    provider_prompts = provider_revised_prompts(response)
    rewritten_prompts = [prompt for prompt in provider_prompts if prompt != submitted]

    if not provider_prompts:
        provider_status = "not_returned"
    elif rewritten_prompts:
        provider_status = "rewritten"
    else:
        provider_status = "echoed"

    provenance: dict[str, Any] = {
        "submitted": submitted,
        "changed": bool((source and source != submitted) or rewritten_prompts),
        "provider_prompt_status": provider_status,
    }
    if source and source != submitted:
        provenance["source"] = source
    if rewritten_prompts:
        provenance["provider_revised"] = rewritten_prompts[0]
        if len(rewritten_prompts) > 1:
            provenance["provider_revised_prompts"] = rewritten_prompts
    return {key: value for key, value in provenance.items() if value is not None}


def _sanitize_provider_metadata(value: Any, depth: int = 0) -> Any:
    if depth > _PROVIDER_METADATA_MAX_DEPTH:
        return None
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            normalized_key = str(key).strip().lower().replace("-", "_")
            if normalized_key in _PROVIDER_METADATA_OMIT_KEYS or any(
                fragment in normalized_key for fragment in _PROVIDER_METADATA_SECRET_FRAGMENTS
            ):
                continue
            cleaned = _sanitize_provider_metadata(item, depth + 1)
            if cleaned in ({}, [], None):
                continue
            sanitized[key] = cleaned
        return sanitized
    if isinstance(value, list):
        sanitized_items = [
            _sanitize_provider_metadata(item, depth + 1)
            for item in value[:_PROVIDER_METADATA_MAX_ITEMS]
        ]
        return [item for item in sanitized_items if item not in ({}, [], None)]
    if isinstance(value, str):
        if len(value) <= _PROVIDER_METADATA_MAX_STRING_CHARS:
            return value
        return value[:_PROVIDER_METADATA_MAX_STRING_CHARS] + "…[truncated]"
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    return str(value)[:_PROVIDER_METADATA_MAX_STRING_CHARS]


def sanitize_provider_response_metadata(response: Any) -> dict[str, Any]:
    sanitized = _sanitize_provider_metadata(response)
    return sanitized if isinstance(sanitized, dict) else {}


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
        direct_output_dir = os.environ.get("FMAGE_DIRECT_OUTPUT_DIR", "").strip()
        if direct_output_dir:
            output_dir = Path(direct_output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            timestamp = os.environ.get("FMAGE_OUTPUT_TIMESTAMP", "").strip() or time.strftime(
                "%Y%m%d-%H%M%S"
            )
            try:
                first_sequence = max(1, int(os.environ.get("FMAGE_OUTPUT_SEQUENCE", "1")))
            except ValueError:
                first_sequence = 1
            sequence = first_sequence + index - 1
            base_name = f"{timestamp}-{sequence:03d}"
            path = output_dir / f"{base_name}.{ext}"
            attempt = 0
            while path.exists():
                attempt += 1
                path = output_dir / f"{base_name}-{attempt}.{ext}"
        else:
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
