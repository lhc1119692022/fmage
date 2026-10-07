from __future__ import annotations

import argparse
import base64
import http.client
import json
from pathlib import Path
import sys
import tempfile
import time
from typing import Any, Callable
import urllib.error
import urllib.parse

import openai_images_transport as openai
from transport_common import (
    download_image, timeout_seconds, request_budget, request_timeout,
    STATUS_QUERY_TIMEOUT_SECONDS, with_request_budget, bounded_deadline,
)


TRANSPORT_NAME = "openai-images"
DEFAULT_RESPONSE_FORMAT = "url"
DEFAULT_PENDING_TOTAL_TIMEOUT = None
DEFAULT_POLL_INTERVAL = 5

PENDING_STATUSES = {"queued", "pending", "processing", "in_progress", "running"}
SUCCESS_STATUSES = {"completed", "complete", "succeeded", "success"}
FAILURE_STATUSES = {"failed", "error", "cancelled", "canceled"}
TRANSIENT_POLL_HTTP_STATUSES = {429, 500, 502, 503, 504}


class RemoteTaskError(RuntimeError):
    def __init__(self, task_id: str, status: str, detail: str):
        normalized_status = status or "unknown"
        super().__init__(
            f'OpenAI Images remote task "{task_id}" {detail} (last status: {normalized_status}).'
        )
        self.task_id = task_id
        self.status = normalized_status
        self.context: dict[str, Any] = {}


class SubmissionUncertain(RuntimeError):
    def __init__(self, checkpoint: Path, timing: dict[str, Any], error: Exception):
        super().__init__(
            f"Image submission connection failed ({type(error).__name__}) before a task ID was received; "
            "the remote generation state is unknown. Check the provider task log for the existing "
            f"task ID before retrieving its result. Checkpoint: {checkpoint}. "
            "No generation request was resubmitted."
        )
        self.context = {
            "stage": "submission_uncertain", "remote_status": "unknown", "remote_task_id": None,
            "checkpoint": str(checkpoint.resolve()), "generation_resubmitted": False, "timing": timing,
        }


def submit_request(
    call: Callable[[dict[str, Any]], dict[str, Any]],
    payload: dict[str, Any], command: str, args: argparse.Namespace,
    run_dir: Path, timing: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    write_result_checkpoint(run_dir, "", "unknown", "submitting", timing)
    try:
        return openai.submit_once(call, payload)
    except (urllib.error.URLError, http.client.HTTPException, TimeoutError, ConnectionError) as error:
        timing["provider_submission_failed_at"] = openai.iso_now()
        checkpoint = write_result_checkpoint(
            run_dir, "", "unknown", "submission_uncertain", timing,
            f"Submission connection failed ({type(error).__name__}); remote state unknown.",
        )
        raise SubmissionUncertain(checkpoint, timing, error) from error


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
    path = "/images/edits" if command == "edit" else "/images/generations"
    return endpoint_with_query(base_url, path, {"async": "true"})


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


def common_payload(args: argparse.Namespace, prompt: str, size: str) -> dict[str, Any]:
    payload = openai.common_payload(args, prompt, size)
    payload["response_format"] = args.response_format
    return payload


def response_task_id(response: dict[str, Any]) -> str:
    return str(response.get("task_id") or response.get("id") or "").strip()


def response_status(response: dict[str, Any]) -> str:
    return str(response.get("status") or "").strip().lower()


def has_image_data(response: dict[str, Any]) -> bool:
    return isinstance(response.get("data"), list) and bool(response["data"])


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
            "OpenAI Images submission response contained neither image data nor id/task_id."
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

    budgets = [v for v in (args.timeout, getattr(args, "pending_total_timeout", None)) if v]
    total_timeout = min(budgets) if budgets else None
    poll_interval = max(1, int(getattr(args, "poll_interval", DEFAULT_POLL_INTERVAL) or 0))
    deadline = bounded_deadline(request_started + total_timeout if total_timeout is not None else float("inf"))
    # Submission may request URLs; retrieve the existing task as bytes to avoid
    # an unnecessary CDN request and URL-to-base64 recovery round trip.
    status_url = task_status_endpoint(args.base_url, task_id, "b64_json")
    last_status = first_status
    status_history: list[dict[str, str]] = []
    append_status_change(status_history, last_status, now_fn)
    poll_count = 0
    notes = ["remote_task_pending"]
    first_query = True

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

        if not first_query:
            sleep_fn(min(poll_interval, remaining))
        first_query = False
        remaining = deadline - monotonic_fn()
        if remaining <= 0:
            timing["pending_poll_finished_at"] = now_fn()
            timing["remote_poll_count"] = poll_count
            raise RemoteTaskError(task_id, last_status, f"timed out after {total_timeout} seconds")

        try:
            with request_budget(min(STATUS_QUERY_TIMEOUT_SECONDS, remaining)):
                response = get_fn(status_url, api_key_value, request_timeout(args.timeout))
        except openai.ApiError as error:
            poll_count += 1
            timing["remote_poll_count"] = poll_count
            if error.status in TRANSIENT_POLL_HTTP_STATUSES:
                note = f"remote_task_poll_http_{error.status}"
                if note not in notes:
                    notes.append(note)
                continue
            raise RemoteTaskError(task_id, last_status, f"status query failed with HTTP {error.status}") from error
        except Exception as error:
            timing["pending_poll_finished_at"] = now_fn()
            raise RemoteTaskError(
                task_id, last_status, f"status query failed ({type(error).__name__}); remote state unconfirmed"
            ) from error

        poll_count += 1
        timing["remote_poll_count"] = poll_count
        if monotonic_fn() >= deadline:
            timing["pending_poll_finished_at"] = now_fn()
            raise RemoteTaskError(task_id, last_status, f"timed out after {total_timeout} seconds")
        status = response_status(response)

        if response_task_id(response) and response_task_id(response) != task_id:
            raise RemoteTaskError(task_id, last_status, "status query returned a different task ID")

        if status in FAILURE_STATUSES:
            append_status_change(status_history, status, now_fn)
            timing["pending_poll_finished_at"] = now_fn()
            raise RemoteTaskError(task_id, status, response_error_detail(response))

        if has_image_data(response):
            if status and status not in SUCCESS_STATUSES:
                raise RemoteTaskError(task_id, status, "returned image data before a completed status")
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
    *,
    run_dir: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None, list[str]]:
    remote_task = initial_remote_task(response)
    if remote_task is None:
        return response, None, []
    task_id, first_status = remote_task
    if run_dir is not None:
        write_result_checkpoint(run_dir, task_id, first_status, "remote_pending", timing)
    try:
        return poll_remote_task(
            args, task_id, first_status, api_key_value, request_started, timing,
        )
    except RemoteTaskError as error:
        if run_dir is not None:
            stage = "remote_failed" if error.status in FAILURE_STATUSES else "status_query_failed"
            checkpoint = write_result_checkpoint(run_dir, task_id, error.status, stage, timing)
            error.context = {
                "stage": stage, "remote_task_id": task_id, "remote_status": error.status,
                "checkpoint": str(checkpoint.resolve()), "generation_resubmitted": False, "timing": timing,
            }
        raise


def write_result_checkpoint(
    run_dir: Path,
    task_id: str,
    status: str,
    stage: str,
    timing: dict[str, Any],
    error: str | None = None,
) -> Path:
    path = run_dir / "remote-task.json"
    checkpoint = {
        "transport": TRANSPORT_NAME,
        "async_mode": True,
        "remote_task_id": task_id or None,
        "remote_status": status or "unknown",
        "stage": stage,
        "timing": timing,
    }
    if error:
        checkpoint["error"] = error
    path.write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def save_completed_images(
    args: argparse.Namespace,
    response: dict[str, Any],
    api_key_value: str,
    run_dir: Path,
    remote_metadata: dict[str, Any] | None,
    timing: dict[str, Any],
) -> tuple[list[Path], dict[str, Any], list[str]]:
    task_id = str((remote_metadata or {}).get("remote_task_id") or response_task_id(response))
    status = str((remote_metadata or {}).get("remote_status") or response_status(response) or "completed")
    checkpoint = write_result_checkpoint(run_dir, task_id, status, "downloading", timing)
    notes: list[str] = []
    # Fetch bytes before writing any image, so a failed later URL cannot leave
    # duplicate partial output when the same task is fetched as base64.
    materialized = dict(response)
    try:
        data = []
        for item in response.get("data", []):
            if isinstance(item, dict) and item.get("url") and not item.get("b64_json"):
                item = dict(item)
                image_bytes = download_image(
                    str(item["url"]), args.timeout, user_agent=openai.CLIENT_USER_AGENT
                )
                item["b64_json"] = base64.b64encode(image_bytes).decode("ascii")
            data.append(item)
        materialized["data"] = data
    except (urllib.error.URLError, http.client.HTTPException, TimeoutError, ConnectionError) as error:
        detail = (
            f"image URL download failed with HTTP {error.code}"
            if isinstance(error, urllib.error.HTTPError)
            else "image URL download failed with a network error or timeout"
        )
        notes.append(
            f"remote_result_url_download_http_{error.code}"
            if isinstance(error, urllib.error.HTTPError)
            else "remote_result_url_download_network_error"
        )
        if isinstance(error, urllib.error.HTTPError):
            error.close()
        if not task_id:
            write_result_checkpoint(run_dir, task_id, status, "download_failed", timing, detail)
            raise RuntimeError(
                f"Provider returned image data, but {detail}; no remote task ID was returned. "
                f"Checkpoint: {checkpoint}. No generation request was resubmitted."
            ) from error

        timing["result_recovery_started_at"] = openai.iso_now()
        write_result_checkpoint(run_dir, task_id, status, "result_recovery", timing, detail)
        # One read of the existing task, never another generation/edit POST.
        try:
            recovered = openai.json_get(
                task_status_endpoint(args.base_url, task_id, "b64_json"),
                api_key_value,
                args.timeout,
            )
        except Exception as recovery_error:
            failure = (
                f"base64 result query failed with HTTP {recovery_error.status}"
                if isinstance(recovery_error, openai.ApiError)
                else f"base64 result query failed ({type(recovery_error).__name__})"
            )
            write_result_checkpoint(run_dir, task_id, status, "download_failed", timing, f"{detail}; {failure}")
            raise RemoteTaskError(
                task_id, status, f"returned image data, but {detail}; {failure}. Checkpoint: {checkpoint}"
            ) from recovery_error

        recovered_data = recovered.get("data") if isinstance(recovered, dict) else None
        recovered_id = response_task_id(recovered) if isinstance(recovered, dict) else ""
        if (
            not isinstance(recovered, dict)
            or (recovered_id and recovered_id != task_id)
            or response_status(recovered) in FAILURE_STATUSES | PENDING_STATUSES
            or not isinstance(recovered_data, list)
            or not recovered_data
            or len(recovered_data) != len(response.get("data", []))
            or not all(
                isinstance(item, dict)
                and isinstance(item.get("b64_json"), str)
                and item["b64_json"]
                for item in recovered_data
            )
        ):
            failure = "existing task did not return matching complete base64 image data"
            write_result_checkpoint(run_dir, task_id, status, "download_failed", timing, f"{detail}; {failure}")
            raise RemoteTaskError(
                task_id, status, f"returned image data, but {detail}; {failure}. Checkpoint: {checkpoint}"
            ) from error
        materialized = {**response, **recovered}
        timing["result_recovery_completed_at"] = openai.iso_now()
        notes.append("remote_result_recovered_as_b64_json")

    images = openai.save_response_images(materialized, run_dir, args.output_format, args.timeout)
    write_result_checkpoint(run_dir, task_id, status, "saved", timing)
    return images, materialized, notes


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
    remote_metadata: dict[str, Any] | None,
) -> Path:
    timing["manifest_written_at"] = openai.iso_now()
    manifest: dict[str, Any] = {
        "command": command,
        "transport": TRANSPORT_NAME,
        "async_mode": True,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "request": openai.request_metadata_without_prompts(request_payload),
        "requested_size": request_payload.get("size"),
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
    (run_dir / "remote-task.json").unlink(missing_ok=True)
    return manifest_path


def validate_async_arguments(args: argparse.Namespace) -> None:
    openai.validate_common(args)
    if args.pending_total_timeout is not None:
        args.pending_total_timeout = timeout_seconds(args.pending_total_timeout)
    if int(args.poll_interval) <= 0:
        raise ValueError("--poll-interval must be a positive integer.")


def dry_run_remote_async(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "enabled": True,
        "poll_interval_seconds": args.poll_interval,
        "total_timeout_seconds": args.pending_total_timeout,
        "result_response_format": "b64_json",
        "status_endpoint": task_status_endpoint_template(args.base_url, "b64_json"),
    }


def remote_result_fields(remote_metadata: dict[str, Any] | None) -> dict[str, Any]:
    return dict(remote_metadata) if remote_metadata else {}


@with_request_budget
def run_generate(args: argparse.Namespace) -> dict[str, Any]:
    timing: dict[str, Any] = {"transport_started_at": openai.iso_now()}
    validate_async_arguments(args)
    prompt = openai.read_prompt(args)
    size, size_notes = openai.resolve_size(args, [])
    payload = common_payload(args, prompt, size)
    url = submission_endpoint(args.base_url, "generate")

    if args.dry_run:
        return {
            "dry_run": True,
            "timeout_seconds": args.timeout,
            "endpoint": url,
            "request": payload,
            "remote_async": dry_run_remote_async(args),
            "notes": size_notes,
        }

    root = openai.output_root(args)
    run_dir = root / time.strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    timing["output_dir_created_at"] = openai.iso_now()

    key = openai.api_key(args)
    timing["provider_request_started_at"] = openai.iso_now()
    request_started = time.monotonic()
    response, retry_notes = submit_request(
        lambda body: openai.json_request(url, body, key, args.timeout),
        payload, "generate", args, run_dir, timing,
    )
    timing["provider_submission_completed_at"] = openai.iso_now()
    response, remote_metadata, async_notes = resolve_async_response(
        args,
        response,
        key,
        request_started,
        timing,
        run_dir=run_dir,
    )
    timing["provider_response_completed_at"] = openai.iso_now()
    notes = size_notes + retry_notes + async_notes

    timing["download_started_at"] = openai.iso_now()
    images, response, download_notes = save_completed_images(
        args, response, key, run_dir, remote_metadata, timing
    )
    notes.extend(download_notes)
    timing["download_completed_at"] = openai.iso_now()
    image_metadata = openai.collect_image_metadata(images)
    warnings = openai.output_size_warnings(payload.get("size"), image_metadata)
    timing["manifest_write_started_at"] = openai.iso_now()
    manifest_path = write_manifest(
        root,
        run_dir,
        "generate",
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
        "requested_size": payload.get("size"),
        "manifest": str(manifest_path.resolve()),
        "notes": notes,
        "warnings": warnings,
        "timing": timing,
        **remote_result_fields(remote_metadata),
    }


@with_request_budget
def run_edit(args: argparse.Namespace) -> dict[str, Any]:
    timing: dict[str, Any] = {"transport_started_at": openai.iso_now()}
    validate_async_arguments(args)
    prompt = openai.read_prompt(args)
    root = openai.output_root(args)

    image_paths: list[Path] = []
    if args.image:
        image_paths.extend(Path(item) for item in args.image)
    if args.use_latest:
        image_paths.extend(openai.load_latest_images(root))
    if not image_paths:
        raise ValueError("Provide at least one --image path or use --use-latest.")
    missing = [str(path) for path in image_paths if not path.exists()]
    if missing:
        raise FileNotFoundError(f"Reference image not found: {missing}")

    primary_index = getattr(args, "primary_image_index", 0)
    if primary_index < 0 or primary_index >= len(image_paths):
        raise ValueError("primary_image_index is outside the supplied image list.")
    size, size_notes = openai.resolve_size(args, image_paths, primary_index)
    payload = common_payload(args, prompt, size)
    image_field = openai.resolve_image_field(getattr(args, "image_field", "auto"), len(image_paths))
    url = submission_endpoint(args.base_url, "edit")

    if args.dry_run:
        return {
            "dry_run": True,
            "timeout_seconds": args.timeout,
            "endpoint": url,
            "request": payload,
            "images": [str(path.resolve()) for path in image_paths],
            "image_field": image_field,
            "remote_async": dry_run_remote_async(args),
            "notes": size_notes,
        }

    run_dir = root / time.strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    timing["output_dir_created_at"] = openai.iso_now()

    key = openai.api_key(args)
    files = [(image_field, path) for path in image_paths]

    def call(body: dict[str, Any]) -> dict[str, Any]:
        return openai.multipart_request(
            url,
            {key_: value for key_, value in body.items() if value is not None},
            files,
            key,
            args.timeout,
        )

    timing["provider_request_started_at"] = openai.iso_now()
    request_started = time.monotonic()
    response, retry_notes = submit_request(
        call, payload, "edit", args, run_dir, timing,
    )
    timing["provider_submission_completed_at"] = openai.iso_now()
    response, remote_metadata, async_notes = resolve_async_response(
        args,
        response,
        key,
        request_started,
        timing,
        run_dir=run_dir,
    )
    timing["provider_response_completed_at"] = openai.iso_now()
    notes = size_notes + retry_notes + async_notes

    timing["download_started_at"] = openai.iso_now()
    images, response, download_notes = save_completed_images(
        args, response, key, run_dir, remote_metadata, timing
    )
    notes.extend(download_notes)
    timing["download_completed_at"] = openai.iso_now()
    image_metadata = openai.collect_image_metadata(images)
    warnings = openai.output_size_warnings(payload.get("size"), image_metadata)
    timing["manifest_write_started_at"] = openai.iso_now()
    manifest_path = write_manifest(
        root,
        run_dir,
        "edit",
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
        "requested_size": payload.get("size"),
        "manifest": str(manifest_path.resolve()),
        "notes": notes,
        "warnings": warnings,
        "timing": timing,
        **remote_result_fields(remote_metadata),
    }


@with_request_budget
def run_recover(args: argparse.Namespace) -> dict[str, Any]:
    """Retrieve one known remote task; this path contains no submission POST."""
    validate_async_arguments(args)
    task_id = args.remote_task_id.strip()
    if not task_id:
        raise ValueError("--remote-task-id must contain the full existing remote task ID.")
    status_url = task_status_endpoint(args.base_url, task_id, "b64_json")
    if args.dry_run:
        return {
            "dry_run": True, "method": "GET", "endpoint": status_url,
            "remote_task_id": task_id, "generation_resubmitted": False,
            "timeout_seconds": args.timeout,
        }

    root = openai.output_root(args)
    root.mkdir(parents=True, exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix=time.strftime("%Y%m%d-%H%M%S-recover-"), dir=root))
    timing: dict[str, Any] = {"transport_started_at": openai.iso_now()}
    key = openai.api_key(args)
    response, metadata, notes = resolve_async_response(
        args, {"task_id": task_id, "status": "queued"}, key, time.monotonic(), timing,
        run_dir=run_dir,
    )
    timing["download_started_at"] = openai.iso_now()
    images, response, download_notes = save_completed_images(args, response, key, run_dir, metadata, timing)
    timing["download_completed_at"] = openai.iso_now()
    image_metadata = openai.collect_image_metadata(images)
    notes += ["existing_remote_task_retrieved_without_submission", *download_notes]
    # The original prompt and delivery parameters are unknown; do not invent them.
    manifest = write_manifest(root, run_dir, "recover", {}, images, image_metadata, response,
                              notes, [], timing, metadata)
    return {
        "command": "recover", "status": "completed", "generation_resubmitted": False,
        "images": [str(path.resolve()) for path in images], "image_metadata": image_metadata,
        "manifest": str(manifest.resolve()), "notes": notes, "warnings": [], "timing": timing,
        **remote_result_fields(metadata),
    }


def add_async_arguments(parser: argparse.ArgumentParser) -> None:
    openai.add_common_arguments(parser)
    parser.set_defaults(pending_total_timeout=DEFAULT_PENDING_TOTAL_TIMEOUT)
    parser.add_argument(
        "--response-format",
        choices=["url", "b64_json"],
        default=DEFAULT_RESPONSE_FORMAT,
    )
    parser.add_argument("--poll-interval", type=int, default=DEFAULT_POLL_INTERVAL)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="OpenAI Images asynchronous generator/editor."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate = subparsers.add_parser("generate", help="Generate images from text.")
    add_async_arguments(generate)

    edit = subparsers.add_parser("edit", help="Edit local images using one or more references.")
    add_async_arguments(edit)
    edit.add_argument("--image-field", choices=["auto", "image", "image[]"], default="auto")
    edit.add_argument("--image", action="append", help="Reference image path. Repeat for multiple images.")
    edit.add_argument("--primary-image-index", type=int, default=0, help="Zero-based index of the image being edited.")
    edit.add_argument("--use-latest", action="store_true", help="Use latest image saved by this skill.")

    recover = subparsers.add_parser("recover", help="Retrieve an existing remote task without generating again.")
    add_async_arguments(recover)
    recover.add_argument("--remote-task-id", required=True)
    recover.set_defaults(response_format="b64_json")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "generate":
            result = run_generate(args)
        elif args.command == "edit":
            result = run_edit(args)
        elif args.command == "recover":
            result = run_recover(args)
        else:
            parser.error("Unknown command.")
            return 2
    except Exception as error:
        context = getattr(error, "context", None)
        if context:
            print(json.dumps({"failure_type": "fmage_transport_failure", "error": str(error), **context},
                             ensure_ascii=False))
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
