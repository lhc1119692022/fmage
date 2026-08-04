#!/usr/bin/env python3
"""Generate and edit images through OpenAI-compatible chat completion image models."""

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

import banana_models
from transport_common import (
    build_prompt_provenance,
    collect_image_metadata,
    image_dimensions,
    output_size_warnings,
    parse_size,
    request_metadata_without_prompts,
    request_prompt,
    sanitize_provider_response_metadata,
    save_response_images_from_data,
    write_latest_state,
)


DEFAULT_BASE_URL = ""
DEFAULT_MODEL = ""
DEFAULT_RESOLUTION = "2K"
DEFAULT_ASPECT = "1:1"
TRANSPORT_NAME = "chat-completions-image"

# Credentials are injected by the Fmage MCP server from the external
# provider configuration. Never store a real key in this shareable plugin file.
PROVIDER_API_KEY = ""
PROVIDER_BASE_URL = ""
PROVIDER_IMAGE_MODEL = ""


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


def read_prompt(args: argparse.Namespace) -> str:
    if args.prompt_file:
        return Path(args.prompt_file).read_text(encoding="utf-8").strip()
    if args.prompt:
        return args.prompt.strip()
    raise ValueError("Provide --prompt or --prompt-file.")


def output_root(args: argparse.Namespace) -> Path:
    if args.output_dir:
        return Path(args.output_dir)
    return Path.cwd() / "outputs" / "provider-imagegen"


def clean_base_url(base_url: str) -> str:
    return base_url.rstrip("/")


def chat_endpoint(base_url: str) -> str:
    root = clean_base_url(base_url)
    if root.endswith("/v1"):
        return root + "/chat/completions"
    return root + "/v1/chat/completions"


def output_mime_type(output_format: str | None) -> str:
    value = (output_format or "png").strip().lower()
    if value in {"jpg", "jpeg"}:
        return "image/jpeg"
    if value == "webp":
        return "image/webp"
    return "image/png"


def normalized_resolution(value: str | None) -> tuple[str, list[str]]:
    notes: list[str] = []
    if not value or value.strip().lower() == "auto":
        return DEFAULT_RESOLUTION, ["resolution_inferred_from_default_quality"]

    text = value.strip().lower()
    if text in {"512", "512px", "0.5k", ".5k"}:
        return "512", notes
    if text.endswith("px"):
        text = text[:-2]
    if text.endswith("k"):
        number = float(text[:-1])
        if math.isclose(number, 0.5):
            return "512", notes
        if math.isclose(number, 1.0):
            return "1K", notes
        if math.isclose(number, 2.0):
            return "2K", notes
        if math.isclose(number, 4.0):
            return "4K", notes
    if text in {"1024", "1"}:
        return "1K", notes
    if text in {"2048", "2"}:
        return "2K", notes
    if text in {"4096", "4"}:
        return "4K", notes
    return value.upper(), notes


def resolution_from_quality(quality: str | None) -> tuple[str, str]:
    if quality == "low":
        return "1K", "resolution_inferred_from_low_quality"
    if quality == "high":
        return "4K", "resolution_inferred_from_high_quality"
    return DEFAULT_RESOLUTION, "resolution_inferred_from_default_quality"


def resolution_from_size(width: int, height: int) -> tuple[str, str]:
    edge = max(width, height)
    if edge <= 768:
        return "512", "resolution_inferred_from_size"
    if edge <= 1408:
        return "1K", "resolution_inferred_from_size"
    if edge <= 2816:
        return "2K", "resolution_inferred_from_size"
    return "4K", "resolution_inferred_from_size"


def gcd_aspect(width: int, height: int) -> str:
    divisor = math.gcd(width, height)
    return f"{width // divisor}:{height // divisor}"


def parse_aspect_label(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip().lower()
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*[:x/]\s*(\d+(?:\.\d+)?)", text)
    if match:
        left = float(match.group(1))
        right = float(match.group(2))
        if left <= 0 or right <= 0:
            raise ValueError(f"Invalid aspect ratio '{value}'.")
        if left.is_integer() and right.is_integer():
            return gcd_aspect(int(left), int(right))
        ratio = left / right
    else:
        ratio = float(text)
        if ratio <= 0:
            raise ValueError(f"Invalid aspect ratio '{value}'.")

    candidates = {
        "1:1": 1,
        "1:4": 1 / 4,
        "1:8": 1 / 8,
        "2:3": 2 / 3,
        "3:2": 3 / 2,
        "3:4": 3 / 4,
        "4:1": 4,
        "4:3": 4 / 3,
        "4:5": 4 / 5,
        "5:4": 5 / 4,
        "8:1": 8,
        "9:16": 9 / 16,
        "16:9": 16 / 9,
        "21:9": 21 / 9,
    }
    return min(candidates, key=lambda item: abs(math.log(candidates[item] / ratio)))


def infer_aspect_from_prompt(prompt: str) -> tuple[str | None, str | None]:
    text = prompt.lower()
    rules: list[tuple[str, str, tuple[str, ...]]] = [
        ("9:16", "semantic_mobile_story", ("9:16", "story", "reel", "tiktok", "shorts", "mobile wallpaper", "phone wallpaper", "vertical poster", "phone screen")),
        ("3:4", "semantic_portrait", ("3:4", "portrait", "headshot", "full-body", "full body", "character", "fashion", "editorial portrait", "vertical product")),
        ("16:9", "semantic_widescreen", ("16:9", "cinematic", "widescreen", "film still", "landscape", "vehicle", "car", "suv", "environment", "panorama", "storyboard")),
        ("4:3", "semantic_standard_landscape", ("4:3", "interior", "room", "documentary", "catalog", "standard horizontal")),
        ("1:1", "semantic_square", ("1:1", "square", "icon", "avatar", "logo mark", "pattern tile", "sticker", "centered product")),
        ("21:9", "semantic_ultrawide", ("21:9", "ultra-wide", "ultrawide", "banner", "header image", "wide panorama")),
    ]
    for aspect, note, keywords in rules:
        if any(keyword in text for keyword in keywords):
            return aspect, note
    return None, None


def resolve_shape(args: argparse.Namespace, references: list[str]) -> tuple[str, str, str | None, list[str]]:
    notes: list[str] = []
    requested_size = None
    if args.size and args.size.lower() != "auto":
        width, height = parse_size(args.size) or (0, 0)
        aspect = gcd_aspect(width, height)
        resolution, note = resolution_from_size(width, height)
        requested_size = args.size
        notes.extend(["size_mapped_to_aspect_and_resolution", note])
        return aspect, resolution, requested_size, notes

    aspect = parse_aspect_label(args.aspect) if args.aspect else None
    if aspect is None:
        inferred, note = infer_aspect_from_prompt(read_prompt(args))
        if inferred:
            aspect = inferred
            if note:
                notes.append(note)

    if aspect is None and args.command == "edit" and references:
        first = references[0]
        if not is_url(first):
            dimensions = image_dimensions(Path(first))
            if dimensions:
                aspect = gcd_aspect(dimensions[0], dimensions[1])
                notes.append("aspect_from_first_reference_image")

    if aspect is None:
        aspect = DEFAULT_ASPECT
        notes.append("fallback_square_aspect")

    if args.resolution and args.resolution.lower() != "auto":
        resolution, resolution_notes = normalized_resolution(args.resolution)
        notes.extend(resolution_notes)
    else:
        resolution, note = resolution_from_quality(args.quality)
        notes.append(note)

    return aspect, resolution, requested_size, notes


def validate_banana_shape(args: argparse.Namespace, aspect: str, resolution: str) -> tuple[str, str]:
    if banana_models.resolve_model(TRANSPORT_NAME, args.model, required=False) is None:
        return aspect, resolution
    explicit_dimensions = parse_size(args.size)
    if explicit_dimensions:
        aspect = banana_models.aspect_from_dimensions(TRANSPORT_NAME, args.model, *explicit_dimensions)
    elif args.aspect:
        aspect = banana_models.validate_aspect(TRANSPORT_NAME, args.model, args.aspect)
    else:
        aspect = banana_models.match_aspect_ratio(TRANSPORT_NAME, args.model, aspect)
    return (
        aspect,
        banana_models.validate_resolution(TRANSPORT_NAME, args.model, resolution),
    )


def chat_resolution_value(model: str, resolution: str) -> str:
    if resolution != "512px":
        return resolution
    if banana_models.resolve_model(TRANSPORT_NAME, model, required=False) is None:
        return resolution
    return "512"


def augment_prompt(prompt: str, aspect: str, resolution: str, mime_type: str, editing: bool) -> str:
    task = "Edit the provided reference image(s)" if editing else "Generate one image"
    requirements = (
        f"{task}. Output requirements: aspect ratio {aspect}; resolution tier {resolution}; "
        f"image MIME type {mime_type}. Return the final image as a URL or data URL if possible."
    )
    return f"{prompt}\n\n{requirements}"


def is_url(value: str) -> bool:
    parsed = urllib.parse.urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def reference_to_url(reference: str) -> str:
    if is_url(reference):
        return reference
    path = Path(reference)
    if not path.exists():
        raise FileNotFoundError(f"Reference image not found: {reference}")
    mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def api_key(args: argparse.Namespace) -> str:
    value = configured_value(PROVIDER_API_KEY, args.api_key_env)
    if not value:
        raise RuntimeError(
            "Missing provider API key. "
            f"{credential_help()} Environment variable {args.api_key_env} is still supported as a fallback."
        )
    return value


def json_request(url: str, body: dict[str, Any], api_key_value: str, timeout: int) -> dict[str, Any]:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key_value}",
            "Content-Type": "application/json",
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


def text_content(prompt: str) -> str:
    return prompt


def vision_content(prompt: str, references: list[str]) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for reference in references:
        content.append({"type": "image_url", "image_url": {"url": reference_to_url(reference)}})
    return content


def build_payload(
    args: argparse.Namespace,
    prompt: str,
    references: list[str],
    aspect: str,
    resolution: str,
    requested_size: str | None,
    *,
    rich: bool,
    force_vision_content: bool,
) -> dict[str, Any]:
    use_vision_content = force_vision_content or bool(references)
    message_content: str | list[dict[str, Any]]
    if use_vision_content:
        message_content = vision_content(prompt, references)
    else:
        message_content = text_content(prompt)

    payload: dict[str, Any] = {
        "model": args.model,
        "stream": False,
        "messages": [{"role": "user", "content": message_content}],
    }
    if rich:
        mime_type = output_mime_type(args.output_format)
        wire_resolution = chat_resolution_value(args.model, resolution)
        payload["aspect_ratio"] = aspect
        payload["image_size"] = wire_resolution
        payload["output_mime_type"] = mime_type
        payload["image_config"] = {
            "aspect_ratio": aspect,
            "image_size": wire_resolution,
            "mime_type": mime_type,
        }
        if requested_size:
            payload["size"] = requested_size
            payload["image_config"]["size"] = requested_size
        if args.output_compression is not None and mime_type == "image/jpeg":
            payload["image_config"]["compression_quality"] = args.output_compression
        if args.response_format:
            payload["response_format"] = args.response_format
    return payload


def build_payload_variants(
    args: argparse.Namespace,
    prompt: str,
    references: list[str],
    aspect: str,
    resolution: str,
    requested_size: str | None,
) -> list[tuple[str, dict[str, Any]]]:
    variants = [
        ("rich", build_payload(args, prompt, references, aspect, resolution, requested_size, rich=True, force_vision_content=False)),
        ("lean", build_payload(args, prompt, references, aspect, resolution, requested_size, rich=False, force_vision_content=False)),
    ]
    if not references:
        variants.append(("lean_vision_content", build_payload(args, prompt, references, aspect, resolution, requested_size, rich=False, force_vision_content=True)))

    seen: set[str] = set()
    unique: list[tuple[str, dict[str, Any]]] = []
    for label, payload in variants:
        key = json.dumps(sanitize_payload(payload), sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        unique.append((label, payload))
    return unique


def request_with_variants(
    url: str,
    variants: list[tuple[str, dict[str, Any]]],
    api_key_value: str,
    timeout: int,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    retryable_statuses = {400, 404, 415, 422}
    notes: list[str] = []
    last_error: ApiError | None = None
    for index, (label, payload) in enumerate(variants):
        try:
            response = json_request(url, payload, api_key_value, timeout)
            if index:
                notes.append(f"used_compatibility_variant_{label}")
            return response, payload, notes
        except ApiError as error:
            last_error = error
            if error.status not in retryable_statuses:
                raise
            notes.append(f"variant_{label}_failed_http_{error.status}")
    if last_error:
        raise last_error
    raise RuntimeError("No request variants were available.")


def sanitize_payload(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            if key in {"url", "data", "b64_json", "base64"} and isinstance(item, str) and (
                item.startswith("data:") or len(item) > 500
            ):
                sanitized[key] = "[base64 image omitted]"
            else:
                sanitized[key] = sanitize_payload(item)
        return sanitized
    if isinstance(value, list):
        return [sanitize_payload(item) for item in value]
    if isinstance(value, str) and value.startswith("data:"):
        return "[base64 image omitted]"
    return value


def data_item_from_string(value: str) -> dict[str, Any] | None:
    text = value.strip()
    if text.startswith("data:image/"):
        return {"b64_json": text}
    if text.startswith(("http://", "https://")):
        return {"url": text}
    return None


def collect_image_items(value: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if isinstance(value, dict):
        if isinstance(value.get("url"), str):
            item = data_item_from_string(value["url"])
            if item:
                items.append(item)
        image_url = value.get("image_url")
        if isinstance(image_url, dict) and isinstance(image_url.get("url"), str):
            item = data_item_from_string(image_url["url"])
            if item:
                items.append(item)
        for key in ("b64_json", "base64", "data"):
            if isinstance(value.get(key), str) and value[key].strip():
                items.append({"b64_json": value[key]})
        for item_value in value.values():
            items.extend(collect_image_items(item_value))
    elif isinstance(value, list):
        for item_value in value:
            items.extend(collect_image_items(item_value))
    elif isinstance(value, str):
        items.extend(extract_image_items_from_text(value))
    return dedupe_items(items)


def extract_image_items_from_text(text: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    stripped = text.strip()
    if stripped.startswith(("{", "[")):
        try:
            decoded = json.loads(stripped)
            items.extend(collect_image_items(decoded))
        except json.JSONDecodeError:
            pass

    for match in re.finditer(r"data:image/[a-zA-Z0-9.+-]+;base64,[A-Za-z0-9+/=\s]+", text):
        items.append({"b64_json": re.sub(r"\s+", "", match.group(0))})

    markdown_urls = re.findall(r"!\[[^\]]*\]\(([^)\s]+)\)", text)
    for url in markdown_urls:
        item = data_item_from_string(url)
        if item:
            items.append(item)

    for url in re.findall(r"https?://[^\s\"'<>),]+", text):
        item = data_item_from_string(url)
        if item:
            items.append(item)
    return dedupe_items(items)


def dedupe_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for item in items:
        key = json.dumps(item, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def data_response_from_chat(response: dict[str, Any]) -> dict[str, Any]:
    if isinstance(response.get("data"), list):
        return response

    items: list[dict[str, Any]] = []
    choices = response.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
            items.extend(collect_image_items(message.get("content")))
            items.extend(collect_image_items(message))
    items.extend(collect_image_items(response.get("images")))
    items = dedupe_items(items)
    if not items:
        raise RuntimeError(f"No image URLs or base64 data found in chat response: {json.dumps(response)[:1000]}")
    return {"data": items}


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
    timing: dict[str, Any],
    requested_shape: str,
) -> Path:
    timing["manifest_written_at"] = iso_now()
    manifest = {
        "command": command,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "request": request_metadata_without_prompts(sanitize_payload(request_payload)),
        "requested_size": requested_shape,
        "images": [str(path.resolve()) for path in images],
        "image_metadata": image_metadata,
        "prompt_provenance": build_prompt_provenance(request_prompt(request_payload), response),
        "provider_response_metadata": sanitize_provider_response_metadata(response),
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
    aspect, resolution, requested_size, shape_notes = resolve_shape(args, references)
    aspect, resolution = validate_banana_shape(args, aspect, resolution)
    requested_shape = requested_size or f"{aspect}@{resolution}"
    mime_type = output_mime_type(args.output_format)
    prompt = augment_prompt(prompt, aspect, resolution, mime_type, bool(references))
    variants = build_payload_variants(args, prompt, references, aspect, resolution, requested_size)
    url = chat_endpoint(args.base_url)

    if args.dry_run:
        return {
            "dry_run": True,
            "endpoint": url,
            "request": sanitize_payload(variants[0][1]),
            "requested_size": requested_shape,
            "compatibility_variants": [label for label, _ in variants],
            "notes": shape_notes,
        }

    root = output_root(args)
    run_dir = root / time.strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    timing["output_dir_created_at"] = iso_now()

    key = api_key(args)
    timing["provider_request_started_at"] = iso_now()
    response, used_payload, retry_notes = request_with_variants(url, variants, key, args.timeout)
    timing["provider_response_completed_at"] = iso_now()

    timing["download_started_at"] = iso_now()
    data_response = data_response_from_chat(response)
    images = save_response_images_from_data(
        data_response,
        run_dir,
        args.timeout,
        preferred_format=args.output_format,
        base64_keys=("b64_json", "base64", "data"),
        user_agent="codex-provider-imagegen/1.0",
    )
    timing["download_completed_at"] = iso_now()
    image_metadata = collect_image_metadata(images)
    warnings = output_size_warnings(requested_size, image_metadata)
    timing["manifest_write_started_at"] = iso_now()
    manifest_path = write_manifest(
        root,
        run_dir,
        args.command,
        used_payload,
        images,
        image_metadata,
        response,
        shape_notes + retry_notes,
        warnings,
        timing,
        requested_shape,
    )
    return {
        "images": [str(path.resolve()) for path in images],
        "image_metadata": image_metadata,
        "requested_size": requested_shape,
        "manifest": str(manifest_path.resolve()),
        "notes": shape_notes + retry_notes,
        "warnings": warnings,
        "timing": timing,
    }


def run_generate(args: argparse.Namespace) -> dict[str, Any]:
    return run_request(args, [])


def run_edit(args: argparse.Namespace) -> dict[str, Any]:
    references: list[str] = []
    if args.image:
        references.extend(args.image)
    if not references:
        raise ValueError("Provide at least one --image path/URL.")
    return run_request(args, references)


def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--prompt", help="Optimized prompt text.")
    parser.add_argument("--prompt-file", help="Path to a UTF-8 prompt file.")
    parser.add_argument("--size", help="Explicit WIDTHxHEIGHT or auto.")
    parser.add_argument("--aspect", help="Aspect ratio such as 1:1, 16:9, 3:4, or 21:9.")
    parser.add_argument("--resolution", help="Resolution tier such as 512, 1k, 2k, or 4k.")
    parser.add_argument("--quality", choices=["low", "medium", "high", "auto"], default="medium")
    parser.add_argument("--output-format", choices=["png", "jpeg", "webp"], default="png")
    parser.add_argument("--output-compression", type=int)
    parser.add_argument("--response-format", help="Optional provider response_format passthrough.")
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
    parser = argparse.ArgumentParser(description="Chat completions image transport.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate = subparsers.add_parser("generate", help="Generate images from text.")
    add_common_arguments(generate)

    edit = subparsers.add_parser("edit", help="Edit images using one or more references.")
    add_common_arguments(edit)
    edit.add_argument("--image", action="append", help="Reference image path or URL. Repeat for multiple references.")

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
