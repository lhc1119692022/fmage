#!/usr/bin/env python3
"""Generate and edit images through provider's draw Image API."""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import json
import math
import mimetypes
import os
from pathlib import Path
import re
import sys
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

from transport_common import (
    collect_image_metadata,
    image_dimensions,
    output_size_warnings,
    parse_size,
    save_response_images_from_data,
    write_latest_state,
)


DEFAULT_BASE_URL = ""
DEFAULT_MODEL = ""

# Credentials are injected by the Fmage MCP server from the external
# provider configuration. Never store a real key in this shareable plugin file.
PROVIDER_API_KEY = ""
PROVIDER_BASE_URL = ""
PROVIDER_IMAGE_MODEL = ""

DEFAULT_RESOLUTION = "2k"
FALLBACK_SIZE = "2048x2048"

MULTIPLE = 16
MAX_EDGE = 4096
MAX_PIXELS = 4096 * 4096
MAX_RATIO = 3.0


def is_image_2_model(model: str | None) -> bool:
    return (model or "").strip().lower().startswith("gpt-image-2")


def default_resolution_for_model(model: str | None) -> str:
    return "1k" if is_image_2_model(model) else DEFAULT_RESOLUTION


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def credential_help() -> str:
    return "Configure the selected provider in ~/.codex/fmage/providers.json."


def configured_value(file_value: str, env_name: str, default: str = "") -> str:
    value = (file_value or "").strip()
    if value:
        return value
    return (os.environ.get(env_name, "") or default).strip()


class ApiError(RuntimeError):
    def __init__(self, status: int, body: str):
        message = f"HTTP {status}: {body[:1000]}"
        if status in {401, 403}:
            message = (
                "provider rejected the API key or access is forbidden. "
                f"{credential_help()}\nProvider response: {body[:1000]}"
            )
        super().__init__(message)
        self.status = status
        self.body = body


def ceil_multiple(value: float, multiple: int = MULTIPLE) -> int:
    return max(multiple, int(math.ceil(value / multiple) * multiple))


def floor_multiple(value: float, multiple: int = MULTIPLE) -> int:
    return max(multiple, int(math.floor(value / multiple) * multiple))


def parse_aspect(value: str | None) -> float | None:
    if not value:
        return None
    text = value.strip().lower()
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*[:x/]\s*(\d+(?:\.\d+)?)", text)
    if match:
        left = float(match.group(1))
        right = float(match.group(2))
        if right <= 0:
            raise ValueError(f"Invalid aspect ratio '{value}'.")
        return left / right
    numeric = float(text)
    if numeric <= 0:
        raise ValueError(f"Invalid aspect ratio '{value}'.")
    return numeric


def is_valid_size(width: int, height: int) -> bool:
    if width % MULTIPLE or height % MULTIPLE:
        return False
    if max(width, height) > MAX_EDGE:
        return False
    if max(width, height) / min(width, height) > MAX_RATIO:
        return False
    return width * height <= MAX_PIXELS


def normalize_size(width: int | float, height: int | float) -> tuple[int, int, list[str]]:
    notes: list[str] = []
    width = ceil_multiple(width)
    height = ceil_multiple(height)

    for _ in range(20):
        before = (width, height)

        if max(width, height) > MAX_EDGE:
            scale = MAX_EDGE / max(width, height)
            width = floor_multiple(width * scale)
            height = floor_multiple(height * scale)
            if "scaled_down_to_max_edge" not in notes:
                notes.append("scaled_down_to_max_edge")

        if width * height > MAX_PIXELS:
            scale = math.sqrt(MAX_PIXELS / (width * height))
            width = floor_multiple(width * scale)
            height = floor_multiple(height * scale)
            if "scaled_down_to_max_pixels" not in notes:
                notes.append("scaled_down_to_max_pixels")

        if max(width, height) / min(width, height) > MAX_RATIO:
            if width >= height:
                height = ceil_multiple(width / MAX_RATIO)
            else:
                width = ceil_multiple(height / MAX_RATIO)
            if "padded_short_edge_to_ratio" not in notes:
                notes.append("padded_short_edge_to_ratio")

        if before == (width, height):
            break

    if not is_valid_size(width, height):
        width, height = 1024, 1024
        notes.append("fallback_to_1024_square")

    return width, height, notes


def resolution_target_area(value: str | None) -> int:
    if not value or value.lower() == "auto":
        value = DEFAULT_RESOLUTION
    text = value.strip().lower()
    if text.endswith("k"):
        number = float(text[:-1])
        if number <= 0:
            raise ValueError(f"Invalid resolution '{value}'.")
        edge = int(number * 1024)
        return min(MAX_PIXELS, edge * edge)
    if text.endswith("px"):
        text = text[:-2]
    edge = int(text)
    if edge <= 0:
        raise ValueError(f"Invalid resolution '{value}'.")
    return min(MAX_PIXELS, edge * edge)


def best_size_for_target_area(aspect: float, target_area: int) -> tuple[int, int]:
    aspect = min(max(aspect, 1 / MAX_RATIO), MAX_RATIO)
    target_area = min(max(target_area, MULTIPLE * MULTIPLE), MAX_PIXELS)

    best: tuple[float, float, float, int, int, int] | None = None
    for width in range(MULTIPLE, MAX_EDGE + MULTIPLE, MULTIPLE):
        for height in range(MULTIPLE, MAX_EDGE + MULTIPLE, MULTIPLE):
            if not is_valid_size(width, height):
                continue
            ratio = width / height
            if aspect > 1 and ratio < 1:
                continue
            if aspect < 1 and ratio > 1:
                continue
            area = width * height
            area_error = abs(math.log(area / target_area))
            ratio_error = abs(math.log(ratio / aspect))
            score = (area_error + 2 * ratio_error, ratio_error, area_error, -area, width, height)
            if best is None or score < best:
                best = score

    if best is None:
        return 1024, 1024
    return best[4], best[5]


def size_from_aspect(aspect: float, resolution: str | None) -> tuple[str, list[str]]:
    notes: list[str] = []
    if aspect > MAX_RATIO:
        aspect = MAX_RATIO
        notes.append("aspect_clamped_to_3_to_1")
    elif aspect < 1 / MAX_RATIO:
        aspect = 1 / MAX_RATIO
        notes.append("aspect_clamped_to_1_to_3")

    target_area = resolution_target_area(resolution)
    width, height = best_size_for_target_area(aspect, target_area)
    notes.append(f"resolution_{(resolution or DEFAULT_RESOLUTION).lower()}_as_target_area")
    return f"{width}x{height}", notes


def infer_aspect_from_prompt(prompt: str) -> tuple[float | None, str | None]:
    text = prompt.lower()
    rules: list[tuple[float, str, tuple[str, ...]]] = [
        (9 / 16, "semantic_mobile_story", ("9:16", "story", "reel", "tiktok", "shorts", "mobile wallpaper", "phone wallpaper", "vertical poster", "phone screen")),
        (3 / 4, "semantic_portrait", ("3:4", "portrait", "headshot", "full-body", "full body", "character", "fashion", "editorial portrait", "vertical product")),
        (16 / 9, "semantic_widescreen", ("16:9", "cinematic", "widescreen", "film still", "landscape", "vehicle", "car", "suv", "environment", "panorama", "storyboard")),
        (4 / 3, "semantic_standard_landscape", ("4:3", "interior", "room", "documentary", "catalog", "standard horizontal")),
        (1.0, "semantic_square", ("1:1", "square", "icon", "avatar", "logo mark", "pattern tile", "sticker", "centered product")),
        (3.0, "semantic_ultrawide", ("3:1", "ultra-wide", "ultrawide", "banner", "header image", "wide panorama")),
    ]
    for aspect, note, keywords in rules:
        if any(keyword in text for keyword in keywords):
            return aspect, note
    return None, None


def infer_resolution_from_quality(quality: str | None, model: str | None = None) -> tuple[str, str]:
    if quality == "low":
        return "1k", "resolution_inferred_from_low_quality"
    if quality == "high":
        return "4k", "resolution_inferred_from_high_quality"
    resolution = default_resolution_for_model(model)
    return resolution, f"resolution_{resolution}_inferred_from_model_default"


def read_prompt(args: argparse.Namespace) -> str:
    if args.prompt_file:
        return Path(args.prompt_file).read_text(encoding="utf-8").strip()
    if args.prompt:
        return args.prompt.strip()
    raise ValueError("Provide --prompt or --prompt-file.")


def is_url(value: str) -> bool:
    parsed = urllib.parse.urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def resolve_size(args: argparse.Namespace, references: list[str]) -> tuple[str | None, list[str]]:
    if args.size:
        if args.size.lower() == "auto":
            return None, ["explicit_auto_size_omitted"]
        width, height = parse_size(args.size) or (0, 0)
        width, height, notes = normalize_size(width, height)
        return f"{width}x{height}", notes

    notes: list[str] = []
    prompt = read_prompt(args)
    aspect = parse_aspect(args.aspect) if args.aspect else None
    if aspect is None:
        inferred_aspect, aspect_note = infer_aspect_from_prompt(prompt)
        if inferred_aspect is not None:
            aspect = inferred_aspect
            if aspect_note:
                notes.append(aspect_note)

    if aspect is None and args.command == "edit" and references:
        first = references[0]
        if not is_url(first):
            path = Path(first)
            if path.exists():
                dimensions = image_dimensions(path)
                if dimensions:
                    aspect = dimensions[0] / dimensions[1]
                    notes.append("aspect_from_first_reference_image")

    if aspect is not None:
        resolution = args.resolution
        if not resolution or resolution.lower() == "auto":
            resolution, resolution_note = infer_resolution_from_quality(args.quality, args.model)
            notes.append(resolution_note)
        size, size_notes = size_from_aspect(aspect, resolution)
        return size, notes + size_notes

    if args.resolution and args.resolution.lower() != "auto":
        size, size_notes = size_from_aspect(1.0, args.resolution)
        return size, ["square_size_from_resolution_without_aspect"] + size_notes

    default_resolution = default_resolution_for_model(args.model)
    size, size_notes = size_from_aspect(1.0, default_resolution)
    return size, [f"fallback_{default_resolution}_square"] + size_notes


def output_root(args: argparse.Namespace) -> Path:
    if args.output_dir:
        return Path(args.output_dir)
    return Path.cwd() / "outputs" / "provider-imagegen"


def load_latest_images(root: Path) -> list[str]:
    latest_path = root / "latest.json"
    if not latest_path.exists():
        raise FileNotFoundError(f"No latest provider image state found at {latest_path}")
    latest = json.loads(latest_path.read_text(encoding="utf-8"))
    images = [str(item) for item in latest.get("images", [])]
    images = [item for item in images if Path(item).exists()]
    if not images:
        raise FileNotFoundError(f"latest.json exists but contains no existing image files: {latest_path}")
    return images


def clean_base_url(base_url: str) -> str:
    return base_url.rstrip("/")


def generation_endpoint(base_url: str) -> str:
    cleaned = clean_base_url(base_url)
    if cleaned.endswith("/v1"):
        return cleaned + "/images/generations"
    return cleaned + "/v1/images/generations"


def pending_status(response: dict[str, Any]) -> tuple[str, str] | None:
    task_id = str(response.get("task_id") or response.get("id") or "").strip()
    status = str(response.get("status") or "").strip().lower()
    if task_id and status in {"processing", "pending", "queued", "running"}:
        return task_id, status
    return None


def task_status_endpoints(base_url: str, task_id: str) -> list[str]:
    cleaned = clean_base_url(base_url)
    roots = [cleaned]
    if not cleaned.endswith("/v1"):
        roots.append(cleaned + "/v1")
    quoted = urllib.parse.quote(task_id, safe="")
    candidates: list[str] = []
    for root in roots:
        candidates.extend(
            [
                f"{root}/tasks/{quoted}",
                f"{root}/task/{quoted}",
                f"{root}/images/generations/{quoted}",
            ]
        )
    seen: set[str] = set()
    return [item for item in candidates if not (item in seen or seen.add(item))]


def json_request(url: str, body: dict[str, Any], api_key: str, timeout: int) -> dict[str, Any]:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    return perform_request(request, timeout)


def json_get(url: str, api_key: str, timeout: int) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        method="GET",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        },
    )
    return perform_request(request, timeout)


def perform_request(request: urllib.request.Request, timeout: int) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read()
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise ApiError(error.code, body) from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"Network error: {error}") from error

    try:
        return json.loads(payload.decode("utf-8"))
    except json.JSONDecodeError as error:
        raise RuntimeError(f"API did not return JSON. First bytes: {payload[:200]!r}") from error


def api_key(args: argparse.Namespace) -> str:
    value = configured_value(PROVIDER_API_KEY, args.api_key_env)
    if not value:
        raise RuntimeError(
            "Missing provider API key. "
            f"{credential_help()} Environment variable {args.api_key_env} is still supported as a fallback."
        )
    return value


def reference_to_value(reference: str, raw_base64: bool = False) -> str:
    if is_url(reference):
        return reference
    path = Path(reference)
    if not path.exists():
        raise FileNotFoundError(f"Reference image not found: {reference}")
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    if raw_base64:
        return encoded
    mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
    return f"data:{mime_type};base64,{encoded}"


def build_payload(
    args: argparse.Namespace,
    prompt: str,
    size: str | None,
    image_values: list[str],
    include_empty_image: bool,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": args.model,
        "prompt": prompt,
    }
    if image_values or include_empty_image:
        payload["image"] = image_values
    if size:
        payload["size"] = size
    if args.response_format:
        payload["response_format"] = args.response_format
    return payload


def sanitize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    def clean_image(value: str) -> str:
        if value.startswith("data:") or len(value) > 500:
            return "[base64 image omitted]"
        return value

    sanitized = dict(payload)
    images = sanitized.get("image")
    if isinstance(images, list):
        sanitized["image"] = [clean_image(str(item)) for item in images]
    return sanitized


def build_payload_variants(
    args: argparse.Namespace,
    prompt: str,
    size: str | None,
    references: list[str],
    *,
    raw_base64: bool = False,
) -> list[tuple[str, dict[str, Any]]]:
    variants: list[tuple[str, dict[str, Any]]] = []
    seen: set[str] = set()

    label = "raw_base64_images" if raw_base64 else "standard"
    image_values = [reference_to_value(item, raw_base64=raw_base64) for item in references]
    include_empty_image = not references
    payload = build_payload(args, prompt, size, image_values, include_empty_image)
    candidates: list[tuple[str, dict[str, Any]]] = [(label, payload)]

    if "response_format" in payload:
        without_format = dict(payload)
        without_format.pop("response_format", None)
        candidates.append((label + "_without_response_format", without_format))

    if payload.get("image") == []:
        without_empty_image = dict(payload)
        without_empty_image.pop("image", None)
        candidates.append((label + "_without_empty_image", without_empty_image))

    for candidate_label, candidate in candidates:
        key = json.dumps(candidate, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        variants.append((candidate_label, candidate))

    return variants


def needs_raw_base64_fallback(references: list[str]) -> bool:
    return any(not is_url(item) for item in references)


def request_with_variants(
    url: str,
    variants: list[tuple[str, dict[str, Any]]],
    api_key_value: str,
    timeout: int,
    fallback_factory=None,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    retryable_statuses = {400, 404, 415, 422}
    notes: list[str] = []
    last_error: ApiError | None = None

    def try_variants(candidates: list[tuple[str, dict[str, Any]]], compatibility: bool) -> tuple[dict[str, Any], dict[str, Any]] | None:
        nonlocal last_error
        for index, (label, payload) in enumerate(candidates):
            try:
                response = json_request(url, payload, api_key_value, timeout)
                if compatibility or index:
                    notes.append(f"used_compatibility_variant_{label}")
                return response, payload
            except ApiError as error:
                last_error = error
                if error.status not in retryable_statuses:
                    raise
                notes.append(f"variant_{label}_failed_http_{error.status}")
        return None

    result = try_variants(variants, False)
    if result:
        return result[0], result[1], notes

    if fallback_factory is not None:
        result = try_variants(fallback_factory(), True)
        if result:
            return result[0], result[1], notes

    if last_error:
        raise last_error
    raise RuntimeError("No request variants were available.")


def poll_pending_response(
    args: argparse.Namespace,
    task_id: str,
    first_status: str,
    api_key_value: str,
    request_started: float,
) -> tuple[dict[str, Any] | None, list[str], str, bool]:
    total_timeout = max(0, int(getattr(args, "pending_total_timeout", 0) or 0))
    if total_timeout <= 0:
        return None, ["remote_task_pending"], first_status, False

    endpoints = task_status_endpoints(args.base_url, task_id)
    if not endpoints:
        return None, ["remote_task_pending_no_status_endpoint"], first_status, False

    deadline = request_started + total_timeout
    fast_until = request_started + max(0, int(args.pending_fast_window or 0))
    fast_interval = max(1, int(args.pending_fast_interval or 20))
    slow_interval = max(1, int(args.pending_slow_interval or 45))
    selected_endpoint: str | None = None
    last_status = first_status
    notes = ["remote_task_pending"]

    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None, notes + ["remote_task_pending_timeout"], last_status, True

        interval = fast_interval if time.monotonic() < fast_until else slow_interval
        time.sleep(min(interval, max(0.0, remaining)))

        candidates = [selected_endpoint] if selected_endpoint else endpoints
        endpoint_errors: list[int] = []
        for endpoint in [item for item in candidates if item]:
            try:
                response = json_get(endpoint, api_key_value, min(args.timeout, max(1, int(deadline - time.monotonic()))))
            except ApiError as error:
                endpoint_errors.append(error.status)
                if error.status in {404, 405, 410}:
                    continue
                if error.status in {429, 500, 502, 503, 504}:
                    continue
                raise
            selected_endpoint = endpoint
            pending = pending_status(response)
            if pending:
                last_status = pending[1]
                break
            if isinstance(response.get("data"), list):
                return response, notes + ["remote_task_completed"], last_status, False
            status = str(response.get("status") or "").strip().lower()
            if status:
                last_status = status
            if status in {"failed", "error", "cancelled", "canceled"}:
                return None, notes + [f"remote_task_{status}"], last_status, True

        if not selected_endpoint and endpoint_errors and all(status in {404, 405, 410} for status in endpoint_errors):
            return None, notes + ["remote_task_status_endpoint_unavailable"], last_status, False


def save_response_images(response: dict[str, Any], run_dir: Path, timeout: int) -> list[Path]:
    return save_response_images_from_data(
        response,
        run_dir,
        timeout,
        base64_keys=("b64_json", "base64"),
        user_agent="codex-provider-imagegen/1.0",
    )


def write_manifest(
    root: Path,
    run_dir: Path,
    command: str,
    request_payload: dict[str, Any],
    images: list[Path],
    image_metadata: list[dict[str, Any]],
    response: dict[str, Any],
    notes: list[str],
    warnings: list[str],
    timing: dict[str, Any] | None = None,
) -> Path:
    if timing is None:
        timing = {}
    timing["manifest_written_at"] = iso_now()
    manifest = {
        "command": command,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "request": sanitize_payload(request_payload),
        "requested_size": request_payload.get("size"),
        "images": [str(path.resolve()) for path in images],
        "image_metadata": image_metadata,
        "response_metadata": {
            "created": response.get("created"),
            "usage": response.get("usage"),
            "data_urls": [
                item.get("url")
                for item in response.get("data", [])
                if isinstance(item, dict) and item.get("url")
            ],
        },
        "timing": timing,
        "notes": notes,
        "warnings": warnings,
    }
    manifest_path = run_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    write_latest_state(root, manifest_path, images, manifest["created_at"])
    return manifest_path


def run_request(args: argparse.Namespace, references: list[str]) -> dict[str, Any]:
    timing: dict[str, Any] = {"transport_started_at": iso_now()}
    prompt = read_prompt(args)
    size, size_notes = resolve_size(args, references)
    variants = build_payload_variants(args, prompt, size, references)
    raw_fallback_enabled = needs_raw_base64_fallback(references)
    url = generation_endpoint(args.base_url)

    if args.dry_run:
        notes = list(size_notes)
        if raw_fallback_enabled:
            notes.append("raw_base64_fallback_is_lazy")
        return {
            "dry_run": True,
            "endpoint": url,
            "request": sanitize_payload(variants[0][1]),
            "compatibility_variants": [label for label, _ in variants],
            "notes": notes,
        }

    root = output_root(args)
    run_dir = root / time.strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    timing["output_dir_created_at"] = iso_now()

    key = api_key(args)
    timing["provider_request_started_at"] = iso_now()
    request_started = time.monotonic()
    response, used_payload, retry_notes = request_with_variants(
        url,
        variants,
        key,
        args.timeout,
        fallback_factory=(
            lambda: build_payload_variants(args, prompt, size, references, raw_base64=True)
            if raw_fallback_enabled
            else []
        ),
    )
    timing["provider_response_completed_at"] = iso_now()
    pending = pending_status(response)
    if pending:
        task_id, status = pending
        timing["pending_poll_started_at"] = iso_now()
        completed_response, pending_notes, final_status, expired = poll_pending_response(
            args,
            task_id,
            status,
            key,
            request_started,
        )
        timing["pending_poll_finished_at"] = iso_now()
        if completed_response is None:
            return {
                "pending": True,
                "pending_expired": expired,
                "remote_task_id": task_id,
                "remote_status": final_status,
                "requested_size": used_payload.get("size"),
                "notes": size_notes + retry_notes + pending_notes,
                "warnings": [],
                "timing": timing,
            }
        response = completed_response
    timing["download_started_at"] = iso_now()
    images = save_response_images(response, run_dir, args.timeout)
    timing["download_completed_at"] = iso_now()
    image_metadata = collect_image_metadata(images)
    warnings = output_size_warnings(used_payload.get("size"), image_metadata)
    timing["manifest_write_started_at"] = iso_now()
    manifest_path = write_manifest(
        root,
        run_dir,
        args.command,
        used_payload,
        images,
        image_metadata,
        response,
        size_notes + retry_notes,
        warnings,
        timing,
    )
    return {
        "images": [str(path.resolve()) for path in images],
        "image_metadata": image_metadata,
        "requested_size": used_payload.get("size"),
        "manifest": str(manifest_path.resolve()),
        "notes": size_notes + retry_notes,
        "warnings": warnings,
        "timing": timing,
    }


def run_generate(args: argparse.Namespace) -> dict[str, Any]:
    return run_request(args, [])


def run_edit(args: argparse.Namespace) -> dict[str, Any]:
    root = output_root(args)
    references: list[str] = []
    if args.image:
        references.extend(args.image)
    if args.use_latest:
        references.extend(load_latest_images(root))
    if not references:
        raise ValueError("Provide at least one --image path/URL or use --use-latest.")
    return run_request(args, references)


def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--prompt", help="Optimized prompt text.")
    parser.add_argument("--prompt-file", help="Path to a UTF-8 prompt file.")
    parser.add_argument("--size", help="Explicit WIDTHxHEIGHT or auto.")
    parser.add_argument("--aspect", help="Aspect ratio such as 1:1, 16:9, 3:4.")
    parser.add_argument("--resolution", help="Resolution tier such as 1k, 2k, 4k, or a long edge in px.")
    parser.add_argument("--quality", choices=["low", "medium", "high"], default="medium", help="Only used to infer size; not sent to provider.")
    parser.add_argument("--response-format", default="url", help="provider response_format field. Use an empty string to omit.")
    parser.add_argument("--base-url", default=configured_value(PROVIDER_BASE_URL, "PROVIDER_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--model", default=configured_value(PROVIDER_IMAGE_MODEL, "PROVIDER_IMAGE_MODEL", DEFAULT_MODEL))
    parser.add_argument("--api-key-env", default="PROVIDER_API_KEY")
    parser.add_argument("--output-dir")
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--pending-total-timeout", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--pending-fast-window", type=int, default=120, help=argparse.SUPPRESS)
    parser.add_argument("--pending-fast-interval", type=int, default=20, help=argparse.SUPPRESS)
    parser.add_argument("--pending-slow-interval", type=int, default=45, help=argparse.SUPPRESS)
    parser.add_argument("--dry-run", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="provider gpt-image-2-vip generator/editor.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate = subparsers.add_parser("generate", help="Generate images from text.")
    add_common_arguments(generate)

    edit = subparsers.add_parser("edit", help="Edit images using one or more references.")
    add_common_arguments(edit)
    edit.add_argument("--image", action="append", help="Reference image path or URL. Repeat for multiple references.")
    edit.add_argument("--use-latest", action="store_true", help="Use latest image saved by this skill.")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "generate":
            result = run_generate(args)
        elif args.command == "edit":
            result = run_edit(args)
        else:
            parser.error("Unknown command.")
            return 2
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
