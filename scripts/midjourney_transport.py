#!/usr/bin/env python3
"""Midjourney relay transport: opaque prompts, native dimensions, one submission."""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from transport_common import (
    STATUS_QUERY_TIMEOUT_SECONDS, build_prompt_provenance, collect_image_metadata,
    download_image, read_response_bytes, request_timeout,
    sanitize_provider_response_metadata, sniff_extension, timeout_seconds, with_request_budget,
)

TRANSPORT_NAME = "midjourney"
SPEC_PATH = Path(__file__).resolve().parents[1] / "config" / "midjourney-v8.2-contract.json"
CONTRACT = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
PENDING = {"submitted", "accepted", "queued", "pending", "processing", "in_progress", "running", "waiting"}
SUCCESS = {"completed", "succeeded", "success"}
FAILURE = {"failed", "error", "cancelled", "canceled", "expired"}
POLL_INTERVAL = 3
DOWNLOAD_USER_AGENT = "fmage-midjourney/1.0"


class TransportFailure(RuntimeError):
    def __init__(self, message, checkpoint, state):
        super().__init__(message)
        self.context = {
            "stage": state["stage"], "checkpoint": str(checkpoint.resolve()),
            "remote_task_id": state.get("remote_task_id"),
            "remote_status": state.get("remote_status"), "generation_resubmitted": False,
        }
        if state.get("download_error"):
            self.context["download_error"] = state["download_error"]


def endpoint(base_url, path):
    parsed = urllib.parse.urlsplit(base_url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError("Midjourney base_url must be an HTTP(S) base URL without query or fragment.")
    base_path = parsed.path.rstrip("/")
    if not base_path.endswith("/v1"):
        base_path += "/v1"
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, base_path + path, "", ""))


def read_prompt(args):
    prompt = Path(args.prompt_file).read_text(encoding="utf-8") if args.prompt_file else args.prompt
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("A non-empty prompt is required.")
    return prompt


def reference_value(value):
    if value.startswith("data:image/"):
        return value
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return value
    path = Path(value)
    data = path.read_bytes()
    mime = mimetypes.guess_type(path.name)[0]
    if not mime or not mime.startswith("image/"):
        raise ValueError(f"Unsupported reference image type: {path.name}")
    return f"data:{mime};base64," + base64.b64encode(data).decode("ascii")


def build_payload(args):
    payload = {"model": args.model, "prompt": read_prompt(args)}
    references = args.image or []
    if args.command == "edit" and not references:
        raise ValueError("Reference generation requires at least one image.")
    if len(references) > CONTRACT["max_reference_images"]:
        raise ValueError(f"Midjourney v8.2 accepts at most {CONTRACT['max_reference_images']} reference images; select the intended image(s).")
    if args.primary_image_index < 0 or (args.primary_image_index and args.primary_image_index >= len(references)):
        raise ValueError("primary_image_index is outside the reference image list.")
    # Keep input order: image roles are already described in the opaque prompt.
    values = [reference_value(value) for value in references]
    if len(values) == 1:
        payload["image"] = values[0]
    elif values:
        payload["images"] = values
    return payload


def http_json(url, key, payload=None):
    headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method="POST" if payload is not None else "GET")
    timeout = request_timeout(None if payload is not None else STATUS_QUERY_TIMEOUT_SECONDS)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            task_id = response.headers.get("X-NewAPI-Task-Id", "").strip()
            try:
                raw = read_response_bytes(response)
                body = json.loads(raw.decode("utf-8")) if raw.strip() else {}
                if not isinstance(body, dict):
                    raise ValueError("Midjourney response must be a JSON object.")
            except Exception as error:
                error.remote_task_id = task_id
                raise
            return body, task_id
    except urllib.error.HTTPError as error:
        detail = error.read(4096).decode("utf-8", errors="replace").replace(key, "[redacted]")
        failure = RuntimeError(f"Midjourney HTTP {error.code}: {detail}")
        failure.remote_task_id = error.headers.get("X-NewAPI-Task-Id", "").strip()
        failure.submission_rejected = 400 <= error.code < 500
        raise failure from error


def task_data(response):
    data = response.get("data")
    return data if isinstance(data, dict) else response


def image_urls(response):
    result = task_data(response).get("result", {})
    result = result.get("data", {}) if isinstance(result, dict) else {}
    urls = result.get("image_urls", []) if isinstance(result, dict) else []
    if not isinstance(urls, list) or any(not isinstance(url, str) or not url for url in urls):
        raise ValueError("Invalid Midjourney image_urls list.")
    grid = result.get("grid_image_url") if isinstance(result, dict) else None
    return urls, grid if isinstance(grid, str) and grid else None


def write_checkpoint(path, state, stage, **updates):
    state.update(updates, stage=stage, updated_at=datetime.now(timezone.utc).isoformat())
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def await_result(response, header_id, args, key, checkpoint, state):
    task_id = header_id or state.get("remote_task_id")
    poll_count = 0
    while True:
        task = task_data(response)
        response_id = task.get("task_id")
        if response_id and task_id and str(response_id) != task_id:
            raise ValueError("Midjourney response task ID does not match the submitted task.")
        task_id = str(response_id or task_id or "")
        status = str(task.get("status") or "").lower()
        write_checkpoint(checkpoint, state, "waiting", remote_task_id=task_id or None,
                         remote_status=status or "unknown", remote_poll_count=poll_count)
        if status in FAILURE or task.get("error_code") or response.get("error"):
            detail = task.get("error_message") or response.get("error") or task.get("error_code") or status
            raise RuntimeError(f"Midjourney task failed: {detail}")
        urls, grid = image_urls(response)
        if urls and (status in SUCCESS or not status):
            return response, urls, grid
        if status in SUCCESS:
            raise RuntimeError("Midjourney task completed without single-image URLs.")
        if not task_id:
            raise RuntimeError("Midjourney response contained neither image URLs nor a task ID.")
        if status and status not in PENDING:
            raise RuntimeError(f"Unsupported Midjourney task status: {status}")
        time.sleep(request_timeout(POLL_INTERVAL))
        response, returned_header = http_json(endpoint(args.base_url, "/tasks/" + urllib.parse.quote(task_id, safe="")), key)
        if returned_header and returned_header != task_id:
            raise ValueError("Midjourney status response header has a different task ID.")
        poll_count += 1


def save_bytes(data, run_dir, index, *, grid=False):
    extension = sniff_extension(data)
    output = os.environ.get("FMAGE_DIRECT_OUTPUT_DIR", "").strip()
    directory = Path(output) if output else run_dir
    directory.mkdir(parents=True, exist_ok=True)
    stamp = os.environ.get("FMAGE_OUTPUT_TIMESTAMP") or time.strftime("%Y%m%d-%H%M%S")
    sequence = max(1, int(os.environ.get("FMAGE_OUTPUT_SEQUENCE", "1"))) + index
    stem = f"{stamp}-{'grid-' if grid else ''}{sequence:03d}" if output else f"{'grid' if grid else 'image'}_{index + 1}"
    suffix = 0
    while True:
        path = directory / f"{stem}{'-' + str(suffix) if suffix else ''}.{extension}"
        try:
            with path.open("xb") as handle:
                handle.write(data)
            return path.resolve()
        except FileExistsError:
            suffix += 1


def download_result_image(url, timeout, index, *, asset_type="image"):
    try:
        return download_image(url, timeout, user_agent=DOWNLOAD_USER_AGENT)
    except urllib.error.HTTPError as error:
        # Retain actionable diagnostics, never signed URLs or arbitrary response bodies.
        diagnostic = {"http_status": error.code, "host": urllib.parse.urlsplit(url).hostname,
                      "asset_type": asset_type, "image_index": index}
        try:
            body = error.read(4096).decode("utf-8", "replace")
            if "cloudflare" in error.headers.get("Server", "").lower():
                code = re.search(r"\berror code:\s*(1\d{3})\b", body, re.IGNORECASE)
                if code:
                    diagnostic["cloudflare_error_code"] = code.group(1)
        except Exception:
            pass  # An unreadable error body must not hide the original HTTP failure.
        finally:
            error.close()
        message = f"Midjourney {asset_type} {index} download failed: HTTP {error.code} (host={diagnostic['host']})"
        if diagnostic.get("cloudflare_error_code"):
            message += f"; Cloudflare {diagnostic['cloudflare_error_code']}"
        failure = RuntimeError(message)
        failure.download_error = diagnostic
        raise failure from error


def finish(args, response, urls, grid, checkpoint, state):
    write_checkpoint(checkpoint, state, "downloading", result_urls=urls, grid_image_url=grid)
    # Download all bytes first. No CDN retry, flag rewriting, or generation replay.
    images_bytes = [download_result_image(url, args.timeout, i + 1) for i, url in enumerate(urls)]
    grid_bytes = download_result_image(grid, args.timeout, 1, asset_type="grid") if grid else None
    if any(not data for data in images_bytes) or (grid and not grid_bytes):
        raise RuntimeError("Midjourney returned an empty image download.")
    images = [save_bytes(data, checkpoint.parent, i) for i, data in enumerate(images_bytes)]
    grid_path = save_bytes(grid_bytes, checkpoint.parent, 0, grid=True) if grid_bytes else None
    expected = CONTRACT["expected_image_count"]
    warnings = []
    if len(images) != expected:
        warnings.append(f"Midjourney expected {expected} single images but returned {len(images)}; the grid is not a single image. No request was resubmitted.")
    request = {"model": state["model"]}
    if state.get("prompt") is not None:
        request["prompt"] = state["prompt"]
    result = {
        "transport": TRANSPORT_NAME, "command": args.command, "model": state["model"],
        "async_mode": state["async_mode"], "request": request,
        "images": [str(path) for path in images], "image_metadata": collect_image_metadata(images),
        "grid_image_path": str(grid_path) if grid_path else None,
        "grid_image_metadata": collect_image_metadata([grid_path]) if grid_path else [],
        "image_count": len(images), "expected_image_count": expected,
        "status": "partial" if warnings else "completed",
        "requested_size": None, "requested_resolution": None,
        "warnings": warnings, "remote_task_id": state.get("remote_task_id"),
        "remote_status": "completed", "remote_poll_count": state.get("remote_poll_count", 0),
        "checkpoint": str(checkpoint.resolve()), "generation_resubmitted": False,
        "prompt_provenance": {**build_prompt_provenance(state.get("prompt")), "submitted": state.get("prompt")},
        "provider_response_metadata": sanitize_provider_response_metadata(response),
    }
    manifest = checkpoint.parent / "manifest.json"
    result["manifest"] = str(manifest.resolve())
    manifest.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    write_checkpoint(checkpoint, state, "saved", remote_status="completed", images=result["images"],
                     grid_image_path=result["grid_image_path"])
    return result


def recovery_state(args, key):
    root = Path(args.output_dir)
    candidates = sorted(root.rglob("remote-task.json"), key=lambda p: p.stat().st_mtime, reverse=True) if root.exists() else []
    for candidate in candidates:
        state = json.loads(candidate.read_text(encoding="utf-8"))
        if state.get("transport") != TRANSPORT_NAME or state.get("remote_task_id") != args.remote_task_id:
            continue
        if state["base_url"] != endpoint(args.base_url, "") or state["credential_fingerprint"] != hashlib.sha256(key.encode()).hexdigest():
            raise ValueError("Task recovery requires the original service URL and submission API key.")
        return state
    return {"model": args.model, "async_mode": True, "prompt": None}


@with_request_budget
def run(args):
    if not isinstance(args.model, str) or not args.model.strip():
        raise ValueError("A non-empty Midjourney server model ID is required.")
    recovering = args.command == "recover"
    payload = None if recovering else build_payload(args)
    url = endpoint(args.base_url, "/tasks/" + urllib.parse.quote(args.remote_task_id, safe="")) if recovering else endpoint(args.base_url, CONTRACT["submission_path"])
    if args.dry_run:
        return {"dry_run": True, "transport": TRANSPORT_NAME, "endpoint": url,
                "method": "GET" if recovering else "POST", "request": payload,
                "async_mode": True, "generation_resubmitted": False,
                "expected_image_count": CONTRACT["expected_image_count"],
                "remote_status_endpoint": endpoint(args.base_url, "/tasks/{task_id}"),
                "requested_size": None, "requested_resolution": None}
    key = os.environ.get(args.api_key_env, "").strip()
    if not key:
        raise ValueError("Missing Midjourney provider API key.")
    state = recovery_state(args, key) if recovering else {
        "model": args.model, "async_mode": True, "prompt": payload["prompt"],
    }
    # The original checkpoint retains its failure; the new attempt starts clean.
    state.pop("error", None)
    state.pop("download_error", None)
    run_dir = Path(args.output_dir) / ("mj-" + uuid.uuid4().hex)
    run_dir.mkdir(parents=True, exist_ok=False)
    checkpoint = run_dir / "remote-task.json"
    state.update(transport=TRANSPORT_NAME, base_url=endpoint(args.base_url, ""),
                 credential_fingerprint=hashlib.sha256(key.encode()).hexdigest(),
                 remote_task_id=args.remote_task_id if recovering else None)
    write_checkpoint(checkpoint, state, "querying" if recovering else "submitting")
    try:
        response, header_id = http_json(url, key, payload)
        response, urls, grid = await_result(response, header_id, args, key, checkpoint, state)
        return finish(args, response, urls, grid, checkpoint, state)
    except Exception as error:
        task_id = getattr(error, "remote_task_id", None) or state.get("remote_task_id")
        if getattr(error, "download_error", None):
            state["download_error"] = error.download_error
        previous_stage = state["stage"]
        stage = ("submission_failed" if previous_stage == "submitting" and getattr(error, "submission_rejected", False) else
                 "submission_uncertain" if previous_stage == "submitting" else
                 "download_failed" if previous_stage == "downloading" else "query_failed")
        detail = str(error).replace(key, "[redacted]")
        write_checkpoint(checkpoint, state, stage, remote_task_id=task_id, error=detail)
        raise TransportFailure(detail, checkpoint, state) from error


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["generate", "edit", "recover"])
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--api-key-env", default="FMAGE_ACTIVE_API_KEY")
    parser.add_argument("--prompt")
    parser.add_argument("--prompt-file")
    parser.add_argument("--image", action="append")
    parser.add_argument("--primary-image-index", type=int, default=0)
    parser.add_argument("--remote-task-id")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--timeout", type=timeout_seconds)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.command == "recover" and not args.remote_task_id:
            raise ValueError("Recovery requires a full remote task ID.")
        result = run(args)
    except Exception as error:
        if getattr(error, "context", None):
            print(json.dumps({"failure_type": "fmage_transport_failure", "error": str(error), **error.context}, ensure_ascii=False))
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
