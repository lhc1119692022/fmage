#!/usr/bin/env python3
"""Generate and edit Nano Banana images through EzAI's Images endpoints."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
from typing import Any
import urllib.parse
import uuid

import banana_models
import ezai_banana_support as support
from transport_common import (
    build_prompt_provenance,
    collect_image_metadata,
    image_dimensions,
    output_size_warnings,
    parse_size,
    request_metadata_without_prompts,
    request_prompt,
    sanitize_provider_response_metadata,
    write_latest_state,
)


TRANSPORT_NAME = "ezai-banana-images"
EZAI_BANANA_WIRE_MODELS = frozenset(banana_models.wire_models_for(TRANSPORT_NAME))
SUPPORTED_RESPONSE_FORMATS = {"url", "b64_json"}
DEFAULT_RESPONSE_FORMAT = "url"
DEFAULT_RESOLUTION = "2K"
DEFAULT_ASPECT_RATIO = "1:1"
REFERENCE_ASPECT_REMAPPED_NOTE = "reference_aspect_mapped_to_nearest_supported"
REFERENCE_OUTPAINT_NOTE = "reference_aspect_fit_outpaint_preserve_content"


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_url(value: str) -> bool:
    parsed = urllib.parse.urlparse(value)
    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.netloc)


def endpoint(base_url: str, command: str) -> str:
    parsed = urllib.parse.urlsplit(base_url.strip())
    base_path = parsed.path.rstrip("/")
    if not base_path.endswith("/v1"):
        base_path += "/v1"
    operation = "edits" if command == "edit" else "generations"
    path = f"{base_path}/images/{operation}"
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, parsed.fragment))


def normalize_aspect(value: str) -> str:
    return banana_models.normalize_aspect(value)


def aspect_from_dimensions(model: str, width: int, height: int) -> str:
    return banana_models.aspect_from_dimensions(TRANSPORT_NAME, model, width, height)


def closest_reference_aspect(model: str, width: int, height: int) -> tuple[str, bool]:
    try:
        return aspect_from_dimensions(model, width, height), False
    except ValueError:
        return banana_models.nearest_aspect_ratio(TRANSPORT_NAME, model, width / height), True


def apply_reference_aspect_fit(prompt: str, aspect: str, notes: list[str]) -> str:
    if REFERENCE_ASPECT_REMAPPED_NOTE not in notes:
        return prompt
    return (
        f"{prompt}\n\n"
        f"Automatic canvas-fit requirement: adapt the output canvas to the supported {aspect} "
        "aspect ratio by outpainting only. Preserve all existing visible content, subjects, "
        "objects, composition, framing, relative geometry, and original image area. Do not crop, "
        "stretch, squeeze, remove, or cover any original content. Extend only the necessary canvas "
        "edges with visually coherent continuation that matches the source perspective, lighting, "
        "colors, texture, and depth."
    )


def normalize_resolution(value: str) -> tuple[str, list[str]]:
    text = value.strip()
    if not text or text.lower() == "auto":
        raise ValueError("Resolution auto must be inferred, not normalized directly.")
    resolution = banana_models.normalize_resolution(text)
    canonical_input = text.upper().replace(" ", "")
    normalized_note = resolution.lower().replace("px", "px")
    if canonical_input == resolution.upper():
        return resolution, [f"resolution_explicit_{normalized_note}"]
    return resolution, [f"resolution_{canonical_input.lower()}_normalized_to_{normalized_note}"]


def resolution_from_quality(quality: str | None) -> tuple[str, str]:
    if quality == "low":
        return "1K", "resolution_inferred_from_low_quality"
    if quality == "high":
        return "4K", "resolution_inferred_from_high_quality"
    return DEFAULT_RESOLUTION, "resolution_inferred_from_default_quality"


def resolve_shape(
    args: argparse.Namespace,
    references: list[str],
) -> tuple[str, str, str, list[str]]:
    notes: list[str] = []
    explicit_dimensions = parse_size(args.size)

    if explicit_dimensions:
        width, height = explicit_dimensions
        aspect = aspect_from_dimensions(args.model, width, height)
        notes.append("aspect_from_explicit_size")
        if args.aspect:
            notes.append("explicit_size_overrode_aspect")
    elif args.aspect:
        aspect = normalize_aspect(args.aspect)
        notes.append("aspect_explicit")
    else:
        aspect = ""
        if args.command == "edit" and references:
            first = references[0]
            if not is_url(first):
                dimensions = image_dimensions(Path(first))
                if dimensions:
                    aspect, remapped = closest_reference_aspect(args.model, *dimensions)
                    notes.append("aspect_from_first_reference_image")
                    if remapped:
                        notes.extend(
                            [
                                REFERENCE_ASPECT_REMAPPED_NOTE,
                                REFERENCE_OUTPAINT_NOTE,
                                f"reference_dimensions_{dimensions[0]}x{dimensions[1]}",
                                f"reference_aspect_target_{aspect}",
                            ]
                        )
        if not aspect:
            inferred_aspect, inferred_note = support.infer_aspect_from_prompt(support.read_prompt(args))
            if inferred_aspect is not None:
                aspect = normalize_aspect(str(inferred_aspect))
                if inferred_note:
                    notes.append(inferred_note)
        if not aspect:
            aspect = DEFAULT_ASPECT_RATIO
            notes.append("fallback_square_aspect")

    if args.resolution and args.resolution.lower() != "auto":
        resolution, resolution_notes = normalize_resolution(args.resolution)
        notes.extend(resolution_notes)
    elif explicit_dimensions:
        resolution = banana_models.resolution_from_edge(max(explicit_dimensions))
        notes.append(f"explicit_size_mapped_to_{resolution.lower()}_tier")
    else:
        resolution, resolution_note = resolution_from_quality(args.quality)
        notes.append(resolution_note)

    aspect = banana_models.validate_aspect(TRANSPORT_NAME, args.model, aspect)
    resolution = banana_models.validate_resolution(TRANSPORT_NAME, args.model, resolution)

    requested_size = (
        f"{explicit_dimensions[0]}x{explicit_dimensions[1]}"
        if explicit_dimensions
        else f"{resolution}@{aspect}"
    )
    return resolution, aspect, requested_size, notes


def build_payload(
    args: argparse.Namespace,
    prompt: str,
    resolution: str,
    aspect: str,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": args.model,
        "prompt": prompt,
        "resolution": resolution,
        "aspect_ratio": aspect,
        "n": 1,
        "response_format": args.response_format,
    }
    thinking_level = banana_models.resolve_thinking_level(TRANSPORT_NAME, args.model, args.thinking_level)
    if thinking_level is not None:
        payload["thinking_level"] = thinking_level
    return payload


def validate_common(args: argparse.Namespace) -> None:
    if args.model not in EZAI_BANANA_WIRE_MODELS:
        supported = ", ".join(sorted(EZAI_BANANA_WIRE_MODELS))
        raise ValueError(
            f"EzAI Banana accepts only these provider wire model IDs: {supported}."
        )
    banana_models.resolve_model(TRANSPORT_NAME, args.model)
    if args.response_format not in SUPPORTED_RESPONSE_FORMATS:
        supported = ", ".join(sorted(SUPPORTED_RESPONSE_FORMATS))
        raise ValueError(f"Unsupported response_format '{args.response_format}'. Use: {supported}.")


def idempotency_key(args: argparse.Namespace, command: str) -> str:
    configured = (args.idempotency_key or "").strip()
    return configured or f"fmage-{command}-{uuid.uuid4().hex}"


def dry_run_headers(args: argparse.Namespace) -> dict[str, str]:
    return {
        "Idempotency-Key": (args.idempotency_key or "").strip() or "[generated-per-request]",
    }


def write_manifest(
    root: Path,
    run_dir: Path,
    command: str,
    request_mode: str,
    request_payload: dict[str, Any],
    input_images: list[str],
    requested_size: str,
    images: list[Path],
    image_metadata: list[dict[str, Any]],
    response: dict[str, Any],
    notes: list[str],
    warnings: list[str],
    timing: dict[str, Any],
) -> Path:
    timing["manifest_written_at"] = iso_now()
    manifest = {
        "command": command,
        "transport": TRANSPORT_NAME,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "request_mode": request_mode,
        "request": request_metadata_without_prompts(request_payload),
        "input_images": input_images,
        "requested_size": requested_size,
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


def finish_response(
    args: argparse.Namespace,
    command: str,
    request_mode: str,
    payload: dict[str, Any],
    input_images: list[str],
    requested_size: str,
    notes: list[str],
    response: dict[str, Any],
    root: Path,
    run_dir: Path,
    timing: dict[str, Any],
) -> dict[str, Any]:
    timing["download_started_at"] = iso_now()
    images = support.save_response_images(response, run_dir, args.output_format, args.timeout)
    timing["download_completed_at"] = iso_now()
    image_metadata = collect_image_metadata(images)
    warnings = output_size_warnings(requested_size, image_metadata)
    timing["manifest_write_started_at"] = iso_now()
    manifest_path = write_manifest(
        root,
        run_dir,
        command,
        request_mode,
        payload,
        input_images,
        requested_size,
        images,
        image_metadata,
        response,
        notes,
        warnings,
        timing,
    )
    return {
        "images": [str(path.resolve()) for path in images],
        "image_metadata": image_metadata,
        "requested_size": requested_size,
        "requested_resolution": payload["resolution"],
        "requested_aspect_ratio": payload["aspect_ratio"],
        "request_mode": request_mode,
        "manifest": str(manifest_path.resolve()),
        "notes": notes,
        "warnings": warnings,
        "timing": timing,
    }


def run_generate(args: argparse.Namespace) -> dict[str, Any]:
    timing: dict[str, Any] = {"transport_started_at": iso_now()}
    validate_common(args)
    prompt = support.read_prompt(args)
    resolution, aspect, requested_size, shape_notes = resolve_shape(args, [])
    payload = build_payload(args, prompt, resolution, aspect)
    url = endpoint(args.base_url, "generate")

    if args.dry_run:
        return {
            "dry_run": True,
            "endpoint": url,
            "request": payload,
            "request_headers": dry_run_headers(args),
            "request_mode": "json",
            "requested_size": requested_size,
            "requested_resolution": resolution,
            "requested_aspect_ratio": aspect,
            "notes": shape_notes,
        }

    root = support.output_root(args)
    run_dir = root / time.strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    timing["output_dir_created_at"] = iso_now()

    key = support.api_key(args)
    timing["provider_request_started_at"] = iso_now()
    response = support.json_request(
        url,
        payload,
        key,
        args.timeout,
        extra_headers={"Idempotency-Key": idempotency_key(args, "generate")},
    )
    timing["provider_response_completed_at"] = iso_now()
    return finish_response(
        args,
        "generate",
        "json",
        payload,
        [],
        requested_size,
        shape_notes,
        response,
        root,
        run_dir,
        timing,
    )


def run_edit(args: argparse.Namespace) -> dict[str, Any]:
    timing: dict[str, Any] = {"transport_started_at": iso_now()}
    validate_common(args)
    prompt = support.read_prompt(args)
    root = support.output_root(args)

    references = list(args.image or [])
    if args.use_latest:
        references.extend(str(path) for path in support.load_latest_images(root))
    if not references:
        raise ValueError("Provide at least one --image path/URL or use --use-latest.")

    url_references = [item for item in references if is_url(item)]
    local_references = [item for item in references if not is_url(item)]
    if url_references and local_references:
        raise ValueError(
            "EzAI Banana edits cannot mix URL and local-file references in one request; "
            "use all URLs or all local files."
        )

    local_paths = [Path(item) for item in local_references]
    missing = [str(path) for path in local_paths if not path.exists()]
    if missing:
        raise FileNotFoundError(f"Reference image not found: {missing}")

    resolution, aspect, requested_size, shape_notes = resolve_shape(args, references)
    prompt = apply_reference_aspect_fit(prompt, aspect, shape_notes)
    payload = build_payload(args, prompt, resolution, aspect)
    if url_references:
        payload["image_urls"] = url_references
        request_mode = "json"
    else:
        request_mode = "multipart"
    url = endpoint(args.base_url, "edit")

    if args.dry_run:
        result: dict[str, Any] = {
            "dry_run": True,
            "endpoint": url,
            "request": payload,
            "request_headers": dry_run_headers(args),
            "request_mode": request_mode,
            "requested_size": requested_size,
            "requested_resolution": resolution,
            "requested_aspect_ratio": aspect,
            "notes": shape_notes,
        }
        if local_paths:
            result["images"] = [str(path.resolve()) for path in local_paths]
            result["image_field"] = "image"
        return result

    run_dir = root / time.strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    timing["output_dir_created_at"] = iso_now()

    key = support.api_key(args)
    headers = {"Idempotency-Key": idempotency_key(args, "edit")}
    timing["provider_request_started_at"] = iso_now()
    if url_references:
        response = support.json_request(
            url,
            payload,
            key,
            args.timeout,
            extra_headers=headers,
        )
    else:
        response = support.multipart_request(
            url,
            payload,
            [("image", path) for path in local_paths],
            key,
            args.timeout,
            extra_headers=headers,
        )
    timing["provider_response_completed_at"] = iso_now()
    return finish_response(
        args,
        "edit",
        request_mode,
        payload,
        references,
        requested_size,
        shape_notes,
        response,
        root,
        run_dir,
        timing,
    )


def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--prompt", help="Complete revised prompt text.")
    parser.add_argument("--prompt-file", help="Path to a UTF-8 prompt file.")
    parser.add_argument("--size", help="Exact WIDTHxHEIGHT request mapped to EzAI resolution and aspect ratio.")
    parser.add_argument("--aspect", help="Aspect ratio such as 1:1, 16:9, or 9:16.")
    parser.add_argument("--resolution", help="Model-supported resolution tier: 512px, 1k, 2k, or 4k.")
    parser.add_argument("--quality", choices=["low", "medium", "high", "auto"], default="high")
    parser.add_argument("--response-format", choices=sorted(SUPPORTED_RESPONSE_FORMATS), default=DEFAULT_RESPONSE_FORMAT)
    parser.add_argument(
        "--thinking-level",
        choices=["minimal", "high"],
        help="Optional Nano Banana 2 thinking_level; Nano Banana Pro does not support it.",
    )
    parser.add_argument("--output-format", choices=["png", "jpeg", "webp"], default="png")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--api-key-env", default="PROVIDER_API_KEY")
    parser.add_argument("--output-dir")
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--pending-total-timeout", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--pending-fast-window", type=int, default=120, help=argparse.SUPPRESS)
    parser.add_argument("--pending-fast-interval", type=int, default=20, help=argparse.SUPPRESS)
    parser.add_argument("--pending-slow-interval", type=int, default=45, help=argparse.SUPPRESS)
    parser.add_argument("--idempotency-key", help=argparse.SUPPRESS)
    parser.add_argument("--dry-run", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="EzAI Nano Banana Images transport.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate = subparsers.add_parser("generate", help="Generate images from text.")
    add_common_arguments(generate)

    edit = subparsers.add_parser("edit", help="Edit images using URL or local-file references.")
    add_common_arguments(edit)
    edit.add_argument("--image", action="append", help="Reference image path or URL. Repeat for multiple references.")
    edit.add_argument("--use-latest", action="store_true", help="Use latest locally saved image output.")

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
