#!/usr/bin/env python3
"""Render the repository's Fmage skills for Pi MCP Adapter."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PLUGIN_ROOT / "skills"
PI_AGENTS_SECTION = PLUGIN_ROOT / "compat" / "pi" / "image-generation-and-editing.md"
PI_CACHE_REFRESH = PLUGIN_ROOT / "compat" / "pi" / "refresh_fmage_mcp_cache.mjs"
SKILL_NAMES = ("fmage", "fmage-config", "fmage-image-regression")
PI_AGENTS_HEADING = "## Image Generation And Editing"


def source_files() -> list[Path]:
    files: list[Path] = []
    for skill_name in SKILL_NAMES:
        skill_root = SOURCE_ROOT / skill_name
        files.extend(
            path
            for path in skill_root.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
        )
    return sorted(files)


def replace_required(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise RuntimeError(f"Pi compatibility transform is stale: missing {label}")
    return text.replace(old, new)


def render_main_skill(text: str) -> str:
    text = replace_required(
        text,
        "description: Use Fmage MCP tools for raster image generation/editing",
        "description: Use Fmage through the Pi MCP Adapter for raster image generation/editing",
        "main skill description",
    )
    text = replace_required(
        text,
        "- Use Fmage MCP tools, not scripts. Choose `generate_image` or `edit_image` for one request and the matching batch tool for multiple independent requests. Provider `transport`, `transport_profile`, and `prompt_profile` routing is automatic after provider selection; do not look for provider-specific image-tool variants. Call the selected base tool directly without enumerating or rediscovering tools; discover only when an expected Fmage tool is unavailable.",
        "- Use Fmage MCP tools through Pi MCP Adapter's `mcp` bridge, not scripts. The `tool` value must be the exact full name exposed by the gateway, for example `mcp({ server: \"Fmage\", tool: \"Fmage_generate_image\", args: { ... } })`. Current image tools are `Fmage_generate_image`, `Fmage_edit_image`, `Fmage_generate_image_batch`, and `Fmage_edit_image_batch`; provider routing remains automatic inside those base tools. Never use provider-specific image-tool variants, Codex-style `mcp__Fmage.*` names, or unprefixed logical names. Discover tools only when an expected full name is unavailable.",
        "Pi MCP bridge rule",
    )
    replacements = {
        "same active Codex model": "same active Pix/Pi model",
        "active Codex model": "active Pix/Pi model",
        "named Codex model": "named model",
        "Codex must not simulate failover": "Pi must not simulate failover",
        "`prepare_prompt_dalle3`": "`Fmage_prepare_prompt_dalle3`",
        "directly to `edit_image`": "directly to `Fmage_edit_image`",
        "Use `display_images` for inline Markdown": "Render paths as inline Markdown images",
    }
    for old, new in replacements.items():
        text = replace_required(text, old, new, old)
    return text


def render_diagnostics(text: str) -> str:
    replacements = {
        "`prepare_prompt_dalle3`": "`Fmage_prepare_prompt_dalle3`",
        "`get_image_task_status`": "`Fmage_get_image_task_status`",
        "`trace_image_job_plan`": "`Fmage_trace_image_job_plan`",
        "`get_provider_status`": "`Fmage_get_provider_status`",
    }
    for old, new in replacements.items():
        text = replace_required(text, old, new, old)
    return text


def render(path: Path) -> bytes:
    data = path.read_bytes()
    relative = path.relative_to(SOURCE_ROOT).as_posix()
    if relative == "fmage/SKILL.md":
        return render_main_skill(data.decode("utf-8")).encode("utf-8")
    if relative == "fmage/references/diagnostics.md":
        return render_diagnostics(data.decode("utf-8")).encode("utf-8")
    return data


def expected_files() -> dict[Path, bytes]:
    return {
        path.relative_to(SOURCE_ROOT): render(path)
        for path in source_files()
    }


def target_files(target_root: Path) -> set[Path]:
    result: set[Path] = set()
    for skill_name in SKILL_NAMES:
        skill_root = target_root / skill_name
        if not skill_root.exists():
            continue
        result.update(
            path.relative_to(target_root)
            for path in skill_root.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
        )
    return result


def replace_agents_section(text: str) -> str:
    start = text.find(PI_AGENTS_HEADING)
    if start < 0:
        raise RuntimeError(f"Pi AGENTS.md is missing {PI_AGENTS_HEADING}")
    next_heading = text.find("\n## ", start + len(PI_AGENTS_HEADING))
    end = len(text) if next_heading < 0 else next_heading + 1
    section = PI_AGENTS_SECTION.read_text(encoding="utf-8").rstrip() + "\n\n"
    return text[:start] + section + text[end:]


def check(
    target_root: Path,
    expected: dict[Path, bytes],
    agents_file: Path | None = None,
) -> list[str]:
    problems: list[str] = []
    expected_paths = set(expected)
    actual_paths = target_files(target_root)
    for relative in sorted(expected_paths - actual_paths):
        problems.append(f"missing: {relative.as_posix()}")
    for relative in sorted(actual_paths - expected_paths):
        problems.append(f"unexpected: {relative.as_posix()}")
    for relative in sorted(expected_paths & actual_paths):
        if (target_root / relative).read_bytes() != expected[relative]:
            problems.append(f"outdated: {relative.as_posix()}")
    if agents_file and agents_file.exists():
        current = agents_file.read_text(encoding="utf-8")
        if current != replace_agents_section(current):
            problems.append("outdated: AGENTS.md Image Generation And Editing section")
    cache_file = target_root.parent / "mcp-cache.json"
    if cache_file.exists():
        cache = json.loads(cache_file.read_text(encoding="utf-8"))
        fmage_cache = cache.get("servers", {}).get("Fmage", {})
        serialized = json.dumps(fmage_cache, ensure_ascii=False)
        if "True only when the user explicitly requested a non-medium tier" in serialized:
            problems.append("outdated: mcp-cache.json Fmage quality schema")
        if "Always pass an explicitly requested tier, including medium" not in serialized:
            problems.append("missing: mcp-cache.json current Fmage quality schema")
        if "resolution_user_requested" not in serialized:
            problems.append("missing: mcp-cache.json current Fmage resolution schema")
        if "thinking_level_user_requested" not in serialized:
            problems.append("missing: mcp-cache.json current Fmage model-capability schema")
    return problems


def sync(
    target_root: Path,
    expected: dict[Path, bytes],
    agents_file: Path | None = None,
) -> None:
    target_root.mkdir(parents=True, exist_ok=True)
    for relative, data in expected.items():
        destination = target_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists() or destination.read_bytes() != data:
            destination.write_bytes(data)

    for relative in sorted(target_files(target_root) - set(expected)):
        destination = (target_root / relative).resolve()
        if not destination.is_relative_to(target_root.resolve()):
            raise RuntimeError(f"Refusing to remove path outside Pi skill root: {destination}")
        destination.unlink()
    if agents_file and agents_file.exists():
        current = agents_file.read_text(encoding="utf-8")
        updated = replace_agents_section(current)
        if updated != current:
            agents_file.write_text(updated, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--target",
        type=Path,
        default=Path.home() / ".pi" / "agent" / "skills",
        help="Pi skills root (default: ~/.pi/agent/skills)",
    )
    parser.add_argument(
        "--skip-mcp-cache",
        action="store_true",
        help="Do not refresh Pi's live Fmage MCP metadata cache",
    )
    parser.add_argument("--check", action="store_true", help="Report drift without writing")
    parser.add_argument(
        "--agents-file",
        type=Path,
        help="Pi AGENTS.md (default: sibling of the target skills directory when present)",
    )
    args = parser.parse_args()
    agents_file = args.agents_file or args.target.parent / "AGENTS.md"

    expected = expected_files()
    if args.check:
        problems = check(args.target, expected, agents_file)
        if problems:
            print("Pi skill compatibility drift detected:", file=sys.stderr)
            for problem in problems:
                print(f"- {problem}", file=sys.stderr)
            return 1
        print("Pi skill compatibility check passed.")
        return 0

    sync(args.target, expected, agents_file)
    mcp_config = args.target.parent / "mcp.json"
    if not args.skip_mcp_cache and mcp_config.exists():
        subprocess.run(
            ["node", str(PI_CACHE_REFRESH), "--agent-dir", str(args.target.parent)],
            check=True,
        )
    problems = check(args.target, expected, agents_file)
    if problems:
        raise RuntimeError("Pi skill synchronization did not converge")
    print(f"Synchronized {len(expected)} Fmage skill files to {args.target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
