#!/usr/bin/env python3
"""Generate Midjourney images through the 808 asynchronous image endpoint."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
import time
from typing import Any, Callable
import urllib.parse

import openai_images_transport as openai


TRANSPORT_NAME = "808-midjourney"
DEFAULT_RESPONSE_FORMAT = "url"
DEFAULT_PENDING_TOTAL_TIMEOUT = 600
DEFAULT_POLL_INTERVAL = 5
SUPPORTED_MODELS = {"midjourney-v8.2"}
FIXED_RESULT_COUNT = 4
PENDING_STATUSES = {"queued", "pending", "processing", "in_progress", "running"}
SUCCESS_STATUSES = {"completed", "complete", "succeeded", "success"}
FAILURE_STATUSES = {"failed", "error", "cancelled", "canceled"}
TRANSIENT_POLL_HTTP_STATUSES = {429, 500, 502, 503, 504}

DIRECTIVE_TOKEN = re.compile(r"(?<!\S)--[A-Za-z][A-Za-z0-9_-]*(?=\s|$)")


class RemoteTaskError(RuntimeError):
    def __init__(self, task_id: str, status: str, detail: str):
        normalized_status = status or "unknown"
        super().__init__(
            f'808 Midjourney remote task "{task_id}" {detail} (last status: {normalized_status}).'
        )
        self.task_id = task_id
        self.status = normalized_status


def endpoint_with_query(base_url: str, path: str, query: dict[str, str]) -> str:
    parsed = urllib.parse.urlsplit(base_url.strip())
    base_path = parsed.path.rstrip("/")
    joined_path = f"{base_path}/{path.lstrip('/')}"
    query_keys = set(query)
    existing = [
        (key, value)
        for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        if key not in query_keys
    ]
    existing.extend((key, str(value)) for key, value in query.items())
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, joined_path, urllib.parse.urlencode(existing), parsed.fragment)
    )


def submission_endpoint(base_url: str, command: str) -> str:
    return endpoint_with_query(base_url, "/images/generations", {"async": "true"})


def task_status_endpoint(base_url: str, task_id: str, response_format: str) -> str:
    quoted_task_id = urllib.parse.quote(task_id, safe="")
    return endpoint_with_query(
        base_url,
        f"/images/tasks/{quoted_task_id}",
        {"response_format": response_format},
    )


def task_status_endpoint_template(base_url: str, response_format: str) -> str:
    return endpoint_with_query(
        base_url,
        "/images/tasks/{task_id}",
        {"response_format": response_format},
    )


def response_task_id(response: dict[str, Any]) -> str:
    return str(response.get("task_id") or response.get("id") or "").strip()


def response_status(response: dict[str, Any]) -> str:
    return str(response.get("status") or "").strip().lower()


def has_image_data(response: dict[str, Any]) -> bool:
    return isinstance(response.get("data"), list)


def response_error_detail(response: dict[str, Any]) -> str:
    value = response.get("error") or response.get("message")
    if value is None:
        return "failed"
    if isinstance(value, str):
        detail = value.strip()
    else:
        detail = json.dumps(value, ensure_ascii=False)
    return f"failed: {detail[:500]}" if detail else "failed"


def initial_remote_task(response: dict[str, Any]) -> tuple[str, str] | None:
    if has_image_data(response):
        return None

    task_id = response_task_id(response)
    status = response_status(response)
    if not task_id:
        raise RuntimeError(
            "808 Midjourney submission response contained neither image data nor id/task_id."
        )
    if status in FAILURE_STATUSES:
        raise RemoteTaskError(task_id, status, response_error_detail(response))
    if status in SUCCESS_STATUSES:
        raise RemoteTaskError(task_id, status, "completed without image data")
    if status and status not in PENDING_STATUSES:
        raise RemoteTaskError(task_id, status, "returned an unsupported task status")
    return task_id, status or "queued"


def append_status_change(
    history: list[dict[str, str]],
    status: str,
    now_fn: Callable[[], str],
) -> None:
    if history and history[-1]["status"] == status:
        return
    history.append({"status": status, "observed_at": now_fn()})


def remote_task_metadata(
    task_id: str,
    status: str,
    status_history: list[dict[str, str]],
    poll_count: int,
    poll_interval: int,
    status_endpoint: str,
) -> dict[str, Any]:
    return {
        "remote_task_id": task_id,
        "remote_status": status,
        "remote_status_history": status_history,
        "remote_poll_count": poll_count,
        "remote_poll_interval_seconds": poll_interval,
        "remote_status_endpoint": status_endpoint,
    }


def poll_remote_task(
    args: argparse.Namespace,
    task_id: str,
    first_status: str,
    api_key_value: str,
    request_started: float,
    timing: dict[str, Any],
    *,
    get_fn: Callable[[str, str, int], dict[str, Any]] | None = None,
    sleep_fn: Callable[[float], None] | None = None,
    monotonic_fn: Callable[[], float] | None = None,
    now_fn: Callable[[], str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    get_fn = get_fn or openai.json_get
    sleep_fn = sleep_fn or time.sleep
    monotonic_fn = monotonic_fn or time.monotonic
    now_fn = now_fn or openai.iso_now

    total_timeout = max(1, int(args.pending_total_timeout or 0))
    poll_interval = max(1, int(args.poll_interval or 0))
    deadline = request_started + total_timeout
    status_url = task_status_endpoint(args.base_url, task_id, args.response_format)
    last_status = first_status
    status_history: list[dict[str, str]] = []
    append_status_change(status_history, last_status, now_fn)
    poll_count = 0
    notes = ["remote_task_pending"]

    timing["pending_poll_started_at"] = now_fn()
    timing["remote_task_id"] = task_id
    timing["remote_poll_interval_seconds"] = poll_interval
    timing["remote_pending_timeout_seconds"] = total_timeout

    while True:
        remaining = deadline - monotonic_fn()
        if remaining <= 0:
            timing["pending_poll_finished_at"] = now_fn()
            timing["remote_poll_count"] = poll_count
            raise RemoteTaskError(task_id, last_status, f"timed out after {total_timeout} seconds")

        sleep_fn(min(poll_interval, remaining))
        remaining = deadline - monotonic_fn()
        if remaining <= 0:
            timing["pending_poll_finished_at"] = now_fn()
            timing["remote_poll_count"] = poll_count
            raise RemoteTaskError(task_id, last_status, f"timed out after {total_timeout} seconds")

        request_timeout = min(max(1, int(args.timeout)), max(1, int(remaining)))
        try:
            response = get_fn(status_url, api_key_value, request_timeout)
        except openai.ApiError as error:
            poll_count += 1
            timing["remote_poll_count"] = poll_count
            if error.status in TRANSIENT_POLL_HTTP_STATUSES:
                note = f"remote_task_poll_http_{error.status}"
                if note not in notes:
                    notes.append(note)
                continue
            raise RemoteTaskError(
                task_id,
                last_status,
                f"status query failed with HTTP {error.status}",
            ) from error
        except Exception as error:
            raise RemoteTaskError(task_id, last_status, f"status query failed: {error}") from error

        poll_count += 1
        timing["remote_poll_count"] = poll_count
        status = response_status(response)

        if has_image_data(response):
            final_status = status or "completed"
            append_status_change(status_history, final_status, now_fn)
            timing["pending_poll_finished_at"] = now_fn()
            return (
                response,
                remote_task_metadata(
                    task_id,
                    final_status,
                    status_history,
                    poll_count,
                    poll_interval,
                    status_url,
                ),
                notes + ["remote_task_completed"],
            )

        if status in FAILURE_STATUSES:
            append_status_change(status_history, status, now_fn)
            timing["pending_poll_finished_at"] = now_fn()
            raise RemoteTaskError(task_id, status, response_error_detail(response))
        if status in SUCCESS_STATUSES:
            append_status_change(status_history, status, now_fn)
            timing["pending_poll_finished_at"] = now_fn()
            raise RemoteTaskError(task_id, status, "completed without image data")
        if status not in PENDING_STATUSES:
            timing["pending_poll_finished_at"] = now_fn()
            raise RemoteTaskError(
                task_id,
                status or last_status,
                "returned an invalid status response without image data",
            )

        last_status = status
        append_status_change(status_history, last_status, now_fn)


def resolve_async_response(
    args: argparse.Namespace,
    response: dict[str, Any],
    api_key_value: str,
    request_started: float,
    timing: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None, list[str]]:
    remote_task = initial_remote_task(response)
    if remote_task is None:
        return response, None, []
    task_id, first_status = remote_task
    return poll_remote_task(
        args,
        task_id,
        first_status,
        api_key_value,
        request_started,
        timing,
    )


def one_line(value: str) -> str:
    return value.replace("\r\n", " ").replace("\r", " ").replace("\n", " ").strip()


def split_prompt_suffix(prompt: str) -> tuple[str, str]:
    normalized = one_line(prompt)
    match = DIRECTIVE_TOKEN.search(normalized)
    if not match:
        return normalized, ""
    return normalized[: match.start()].rstrip(), normalized[match.start() :].strip()


def build_prompt(
    prompt: str,
    aspect: str | None = None,
    midjourney_parameters: str | None = None,
) -> str:
    natural_prompt, inline_parameters = split_prompt_suffix(prompt)
    parts = [natural_prompt]

    normalized_aspect = one_line(aspect or "")
    if normalized_aspect:
        parts.append(f"--ar {normalized_aspect}")

    if inline_parameters:
        parts.append(inline_parameters)

    explicit_parameters = one_line(midjourney_parameters or "")
    if explicit_parameters:
        parts.append(explicit_parameters)

    return " ".join(part for part in parts if part).strip()


def common_payload(args: argparse.Namespace, prompt: str) -> dict[str, Any]:
    return {
        "model": args.model,
        "prompt": build_prompt(
            prompt,
            getattr(args, "aspect", None),
            getattr(args, "midjourney_parameters", None),
        ),
        "response_format": args.response_format,
    }


def validate_arguments(args: argparse.Namespace) -> None:
    if args.model not in SUPPORTED_MODELS:
        supported = ", ".join(sorted(SUPPORTED_MODELS))
        raise ValueError(f"This transport supports only: {supported}.")
    if int(args.pending_total_timeout) <= 0:
        raise ValueError("--pending-total-timeout must be a positive integer.")
    if int(args.poll_interval) <= 0:
        raise ValueError("--poll-interval must be a positive integer.")
    if args.response_format not in {"url", "b64_json"}:
        raise ValueError('Use response_format "url" or "b64_json".')


def dry_run_remote_async(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "enabled": True,
        "poll_interval_seconds": args.poll_interval,
        "total_timeout_seconds": args.pending_total_timeout,
        "status_endpoint": task_status_endpoint_template(
            args.base_url,
            args.response_format,
        ),
    }


def write_manifest(
    root: Path,
    run_dir: Path,
    request_payload: dict[str, Any],
    images: list[Path],
    image_metadata: list[dict[str, Any]],
    response: dict[str, Any],
    notes: list[str],
    warnings: list[str],
    timing: dict[str, Any],
    remote_metadata: dict[str, Any] | None,
) -> Path:
    timing["manifest_written_at"] = openai.iso_now()
    manifest: dict[str, Any] = {
        "command": "generate",
        "transport": TRANSPORT_NAME,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "request": openai.request_metadata_without_prompts(request_payload),
        "requested_size": None,
        "fixed_result_count": FIXED_RESULT_COUNT,
        "images": [str(path.resolve()) for path in images],
        "image_metadata": image_metadata,
        "prompt_provenance": openai.build_prompt_provenance(
            openai.request_prompt(request_payload), response
        ),
        "provider_response_metadata": openai.sanitize_provider_response_metadata(response),
        "timing": timing,
        "notes": notes,
        "warnings": warnings,
    }
    if remote_metadata:
        manifest.update(remote_metadata)

    manifest_path = run_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    openai.write_latest_state(root, manifest_path, images, manifest["created_at"])
    return manifest_path


def run_generate(args: argparse.Namespace) -> dict[str, Any]:
    timing: dict[str, Any] = {"transport_started_at": openai.iso_now()}
    validate_arguments(args)
    prompt = openai.read_prompt(args)
    payload = common_payload(args, prompt)
    endpoint = submission_endpoint(args.base_url, "generate")
    notes = ["midjourney_fixed_resolution", f"midjourney_fixed_result_count_{FIXED_RESULT_COUNT}"]

    if args.dry_run:
        return {
            "dry_run": True,
            "endpoint": endpoint,
            "request": payload,
            "requested_size": None,
            "fixed_result_count": FIXED_RESULT_COUNT,
            "remote_async": dry_run_remote_async(args),
            "notes": notes,
        }

    root = openai.output_root(args)
    run_dir = root / time.strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    timing["output_dir_created_at"] = openai.iso_now()

    key = openai.api_key(args)
    timing["provider_request_started_at"] = openai.iso_now()
    request_started = time.monotonic()
    response = openai.json_request(endpoint, payload, key, args.timeout)
    timing["provider_submission_completed_at"] = openai.iso_now()
    response, remote_metadata, async_notes = resolve_async_response(
        args,
        response,
        key,
        request_started,
        timing,
    )
    timing["provider_response_completed_at"] = openai.iso_now()
    notes.extend(async_notes)

    timing["download_started_at"] = openai.iso_now()
    images = openai.save_response_images(response, run_dir, args.output_format, args.timeout)
    timing["download_completed_at"] = openai.iso_now()
    image_metadata = openai.collect_image_metadata(images)
    warnings = []
    if len(images) != FIXED_RESULT_COUNT:
        warnings.append(
            f"Midjourney returned {len(images)} results; expected {FIXED_RESULT_COUNT}."
        )
    timing["manifest_write_started_at"] = openai.iso_now()
    manifest_path = write_manifest(
        root,
        run_dir,
        payload,
        images,
        image_metadata,
        response,
        notes,
        warnings,
        timing,
        remote_metadata,
    )
    return {
        "images": [str(path.resolve()) for path in images],
        "image_metadata": image_metadata,
        "requested_size": None,
        "fixed_result_count": FIXED_RESULT_COUNT,
        "manifest": str(manifest_path.resolve()),
        "notes": notes,
        "warnings": warnings,
        "timing": timing,
        **(dict(remote_metadata) if remote_metadata else {}),
    }


def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--prompt", help="Complete revised prompt.")
    parser.add_argument("--prompt-file", help="Path to a UTF-8 prompt file.")
    parser.add_argument("--aspect", help="Raw aspect value appended as --ar VALUE.")
    parser.add_argument(
        "--midjourney-parameters",
        help="Raw trailing Midjourney instruction block; preserve it verbatim.",
    )
    parser.add_argument("--size")
    parser.add_argument("--resolution")
    parser.add_argument("--quality")
    parser.add_argument("--moderation")
    parser.add_argument("--background")
    parser.add_argument("--output-format", choices=["png", "jpeg", "webp"], default="png")
    parser.add_argument("--output-compression", type=int)
    parser.add_argument("--response-format", choices=["url", "b64_json"], default=DEFAULT_RESPONSE_FORMAT)
    parser.add_argument("--base-url", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--api-key-env", default="PROVIDER_API_KEY")
    parser.add_argument("--output-dir")
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--pending-total-timeout", type=int, default=DEFAULT_PENDING_TOTAL_TIMEOUT)
    parser.add_argument("--poll-interval", type=int, default=DEFAULT_POLL_INTERVAL)
    parser.add_argument("--dry-run", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="808 Midjourney asynchronous generator.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate = subparsers.add_parser("generate", help="Generate four Midjourney results.")
    add_common_arguments(generate)

    edit = subparsers.add_parser("edit", help="Compatibility alias routed to generate.")
    add_common_arguments(edit)
    edit.add_argument("--image", action="append")
    edit.add_argument("--use-latest", action="store_true")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = run_generate(args)
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
