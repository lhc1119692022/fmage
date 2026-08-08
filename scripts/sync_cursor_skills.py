#!/usr/bin/env python3
"""Install and verify Fmage's native MCP and Agent Skills bridge for Cursor."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PLUGIN_ROOT / "skills"
SKILL_NAMES = ("fmage", "fmage-config", "fmage-image-regression")


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
        raise RuntimeError(f"Cursor compatibility transform is stale: missing {label}")
    return text.replace(old, new)


def render_main_skill(text: str) -> str:
    text = replace_required(
        text,
        "description: Use Fmage MCP tools for raster image generation/editing",
        "description: Use Fmage's native MCP tools in Cursor for raster image generation/editing",
        "main skill description",
    )
    replacements = {
        "same active Codex model": "same active Cursor model",
        "active Codex model": "active Cursor model",
        "named Codex model": "named model",
        "Codex must not simulate failover": "Cursor must not simulate failover",
        "Codex/Pi session reasoning level": "Cursor session reasoning level",
        "Use `display_images` for inline Markdown": "Render saved local paths as inline Markdown images",
    }
    for old, new in replacements.items():
        text = replace_required(text, old, new, old)
    return text


def render_regression_skill(text: str) -> str:
    return replace_required(
        text,
        "In Codex desktop or another host that supports absolute local-image Markdown, normalize only the display path to forward slashes and add an inline image preview. In Pi/Pix, follow its local-artifact rules: use relative Markdown only when immediately derivable; otherwise show the absolute path as code without a preview.",
        "In Cursor, normalize only the display path to forward slashes and add an inline Markdown image preview using the absolute local path.",
        "local image delivery rule",
    )


def render(path: Path) -> bytes:
    data = path.read_bytes()
    relative = path.relative_to(SOURCE_ROOT).as_posix()
    if relative == "fmage/SKILL.md":
        return render_main_skill(data.decode("utf-8")).encode("utf-8")
    if relative == "fmage-image-regression/SKILL.md":
        return render_regression_skill(data.decode("utf-8")).encode("utf-8")
    return data


def expected_files() -> dict[Path, bytes]:
    return {path.relative_to(SOURCE_ROOT): render(path) for path in source_files()}


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


def fmage_server_definition(fmage_config: Path) -> dict[str, object]:
    return {
        "command": "node",
        "args": [str((PLUGIN_ROOT / "mcp" / "server.mjs").resolve())],
        "env": {"FMAGE_CONFIG": str(fmage_config.resolve())},
    }


def read_mcp_config(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError(f"Cursor MCP config must contain a JSON object: {path}")
    return data


def check(
    target_root: Path,
    expected: dict[Path, bytes],
    mcp_config: Path | None,
    fmage_config: Path,
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

    if mcp_config is not None:
        config = read_mcp_config(mcp_config)
        actual = config.get("mcpServers", {})
        if not isinstance(actual, dict):
            problems.append("invalid: mcp.json mcpServers must be an object")
        elif actual.get("Fmage") != fmage_server_definition(fmage_config):
            problems.append("missing or outdated: mcp.json Fmage server")
    return problems


def sync_skills(target_root: Path, expected: dict[Path, bytes]) -> None:
    target_root.mkdir(parents=True, exist_ok=True)
    for relative, data in expected.items():
        destination = target_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists() or destination.read_bytes() != data:
            destination.write_bytes(data)

    for relative in sorted(target_files(target_root) - set(expected)):
        destination = (target_root / relative).resolve()
        if not destination.is_relative_to(target_root.resolve()):
            raise RuntimeError(f"Refusing to remove path outside Cursor skill root: {destination}")
        destination.unlink()


def sync_mcp_config(path: Path, fmage_config: Path) -> None:
    config = read_mcp_config(path)
    servers = config.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise RuntimeError(f"Cursor MCP config mcpServers must be an object: {path}")
    servers["Fmage"] = fmage_server_definition(fmage_config)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(f"{json.dumps(config, ensure_ascii=False, indent=2)}\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    default_cursor_root = Path.home() / ".cursor"
    default_fmage_config = Path(
        os.environ.get("FMAGE_CONFIG", Path.home() / ".codex" / "fmage" / "providers.json")
    )
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--target",
        type=Path,
        default=default_cursor_root / "skills",
        help="Cursor personal skills root (default: ~/.cursor/skills)",
    )
    parser.add_argument(
        "--mcp-config",
        type=Path,
        default=default_cursor_root / "mcp.json",
        help="Cursor user MCP config (default: ~/.cursor/mcp.json)",
    )
    parser.add_argument(
        "--fmage-config",
        type=Path,
        default=default_fmage_config,
        help="Shared Fmage providers.json path",
    )
    parser.add_argument(
        "--skip-mcp-config",
        action="store_true",
        help="Synchronize skills without changing Cursor's MCP config",
    )
    parser.add_argument("--check", action="store_true", help="Report drift without writing")
    args = parser.parse_args()

    expected = expected_files()
    mcp_config = None if args.skip_mcp_config else args.mcp_config
    if args.check:
        problems = check(args.target, expected, mcp_config, args.fmage_config)
        if problems:
            print("Cursor Fmage compatibility drift detected:", file=sys.stderr)
            for problem in problems:
                print(f"- {problem}", file=sys.stderr)
            return 1
        print("Cursor Fmage compatibility check passed.")
        return 0

    sync_skills(args.target, expected)
    if mcp_config is not None:
        sync_mcp_config(mcp_config, args.fmage_config)
    problems = check(args.target, expected, mcp_config, args.fmage_config)
    if problems:
        raise RuntimeError("Cursor Fmage synchronization did not converge")
    print(f"Synchronized {len(expected)} Fmage skill files to {args.target}")
    if mcp_config is not None:
        print(f"Configured native Fmage MCP in {mcp_config}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
