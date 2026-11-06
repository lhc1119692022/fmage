#!/usr/bin/env python3
"""Recover a transparent annotation layer from a Codex marked-image preview.

Codex hands tools the original image's path, but its own markup tool leaves the
marks only in an inline preview with no path. The session log keeps that
preview, so the layer is the difference between it and the original.

The session is found by the edit call itself: a call in the log with no output
yet, carrying these images and prompts. Only the user message right before that
call counts, so marks from an earlier turn are never reused. A batch is one call
and one message, so all of its jobs share the one layer written here.

Usage: codex_marks.py --sessions DIR --request JSON --output PNG
Request: {"jobs": [{"images": [...], "prompt": "..."}, ...]}
Prints one JSON object whose "status" is:
  none        no marks belong to this edit
  applied     the layer was written to --output ("image" is the picture it fits)
  not_first   the marks belong to a later input image ("image_index")
  unconfirmed the call never appeared in a session log
  failed      marks exist but the layer could not be written
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import math
import sys
import time
from pathlib import Path

RECENT_SESSIONS = 8
RECENT_SECONDS = 30 * 60
WAIT_SECONDS = 2.5
POLL_SECONDS = 0.1
# Channel difference that counts as a drawn mark, not a re-encoding artifact.
MARK_THRESHOLD = 24
MIN_MARK_PIXELS = 16
# Above this the two pictures differ as images; they are not one with marks.
MAX_MARK_RATIO = 0.15
ASPECT_TOLERANCE = 0.002


def recent_sessions(root: Path) -> list[Path]:
    now = time.time()
    found = []
    for path in root.rglob("rollout-*.jsonl"):
        try:
            modified = path.stat().st_mtime
        except OSError:
            continue
        if now - modified <= RECENT_SECONDS:
            found.append((modified, path))
    return [path for _, path in sorted(found, key=lambda item: item[0], reverse=True)[:RECENT_SESSIONS]]


def path_key(value: object) -> str:
    return str(value).replace("\\", "/").casefold()


def job_keys(jobs: list[object]) -> set[tuple[object, tuple[str, ...]]]:
    """Distinct (prompt, images) pairs; repeating a job changes nothing."""
    keys = set()
    for job in jobs:
        if not isinstance(job, dict):
            continue
        images = job.get("images") or []
        images = [images] if isinstance(images, str) else images
        keys.add((job.get("prompt"), tuple(path_key(image) for image in images)))
    return keys


def matches(call: dict[str, object], request: dict[str, object]) -> bool:
    try:
        arguments = json.loads(str(call.get("arguments")))
    except json.JSONDecodeError:
        return False
    jobs = arguments.get("jobs")
    logged = [{**arguments, **job} for job in jobs if isinstance(job, dict)] if isinstance(jobs, list) else [arguments]
    return job_keys(logged) == job_keys(request["jobs"])


def pending_message(path: Path, request: dict[str, object]) -> list[object] | None:
    """The user message before the matching unanswered call, or None."""
    user_content: list[object] = []
    pending: dict[object, list[object]] = {}
    with path.open("rb") as stream:
        for line in stream:
            if not any(key in line for key in (b'"UserMessage"', b'"function_call"', b'"function_call_output"')):
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue  # a line still being written
            payload = record.get("payload")
            if not isinstance(payload, dict):
                continue
            item = payload.get("item")
            if isinstance(item, dict) and item.get("type") == "UserMessage":
                content = item.get("content")
                user_content = content if isinstance(content, list) else []
            elif payload.get("type") == "function_call" and "edit_image" in str(payload.get("name")):
                if matches(payload, request):
                    pending[payload.get("call_id")] = user_content
            elif payload.get("type") == "function_call_output":
                pending.pop(payload.get("call_id"), None)
    return next(reversed(pending.values()), None)


def find_message(root: Path, request: dict[str, object]) -> list[object] | None:
    deadline = time.monotonic() + WAIT_SECONDS
    while True:
        for path in recent_sessions(root):
            try:
                content = pending_message(path, request)
            except OSError:
                continue
            if content is not None:
                return content
        if time.monotonic() >= deadline:
            return None
        time.sleep(POLL_SECONDS)


def inline_previews(content: list[object]) -> list[object]:
    from PIL import Image

    previews = []
    for item in content:
        url = item.get("image_url") if isinstance(item, dict) and item.get("type") == "image" else None
        if not isinstance(url, str) or not url.startswith("data:image") or "," not in url:
            continue
        image = Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1])))
        previews.append(image.convert("RGBA"))
    return previews


def same_shape(left: tuple[int, int], right: tuple[int, int]) -> bool:
    return abs(math.log((left[0] / left[1]) / (right[0] / right[1]))) <= ASPECT_TOLERANCE


def mark_mask(original, preview):
    """The drawn pixels as an L mask, or None when these are not one picture."""
    from PIL import Image, ImageChops

    base = original.convert("RGB")
    if base.size != preview.size:
        base = base.resize(preview.size, Image.Resampling.LANCZOS)
    red, green, blue = ImageChops.difference(base, preview.convert("RGB")).split()
    difference = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    histogram = difference.histogram()
    marked = sum(histogram[MARK_THRESHOLD + 1:])
    if marked < MIN_MARK_PIXELS or marked / sum(histogram) > MAX_MARK_RATIO:
        return None
    return difference.point(lambda value: 255 if value > MARK_THRESHOLD else 0)


def open_input(value: object):
    from PIL import Image, ImageOps

    path = Path(str(value))
    if "://" in str(value) or not path.is_file():
        return None
    with Image.open(path) as opened:
        return ImageOps.exif_transpose(opened).convert("RGBA")


def recover(content: list[object], request: dict[str, object], output: Path) -> dict[str, object]:
    previews = inline_previews(content)
    outcome: dict[str, object] = {"status": "none"}
    seen = set()
    for job in request["jobs"] if previews else []:
        images = job["images"]
        if tuple(images) in seen:
            continue
        seen.add(tuple(images))
        result = recover_for(previews, images, output)
        if result["status"] == "applied":
            return {**result, "image": images[0]}
        if result["status"] == "not_first":
            outcome = result
    return outcome


def recover_for(previews: list[object], images: list[object], output: Path) -> dict[str, object]:
    originals = [open_input(value) for value in images]
    layer = None
    for preview in previews:
        for index, original in enumerate(originals):
            if original is None or not same_shape(original.size, preview.size):
                continue
            mask = mark_mask(original, preview)
            if mask is None:
                continue
            if index != 0:
                return {"status": "not_first", "image_index": index + 1}
            layer = preview.copy()
            layer.putalpha(mask)
            break
    if layer is None:
        return {"status": "none"}
    layer.save(output, format="PNG")
    return {"status": "applied"}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sessions", required=True)
    parser.add_argument("--request", required=True)
    parser.add_argument("--output", required=True)
    options = parser.parse_args(argv)
    request = json.loads(Path(options.request).read_text(encoding="utf-8"))
    content = find_message(Path(options.sessions), request)
    if content is None:
        print(json.dumps({"status": "unconfirmed"}))
        return 0
    try:
        print(json.dumps(recover(content, request, Path(options.output))))
    except Exception as error:  # marks exist, so the caller must not edit blindly
        print(json.dumps({"status": "failed", "error": str(error)}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
