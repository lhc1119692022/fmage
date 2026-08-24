#!/usr/bin/env python3
"""Synchronize Fmage as a native SCNet agent plugin."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PLUGIN_ROOT / "skills"
SKILL_NAMES = ("fmage", "fmage-config", "fmage-image-regression")


def source_files() -> list[Path]:
    files: list[Path] = []
    for name in SKILL_NAMES:
        root = SOURCE_ROOT / name
        files.extend(p for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc")
    return sorted(files)


def render(path: Path) -> bytes:
    data = path.read_bytes()
    rel = path.relative_to(SOURCE_ROOT).as_posix()
    if rel == "fmage/SKILL.md":
        text = data.decode("utf-8")
        text = text.replace("description: Use Fmage MCP tools for raster image generation/editing", "description: Use Fmage in SCNet for raster image generation and editing")
        for old, new in {
            "same active Codex model": "same active SCNet model",
            "active Codex model": "active SCNet model",
            "named Codex model": "named model",
            "Codex must not simulate failover": "SCNet must not simulate failover",
            "Codex/Pi session reasoning level": "SCNet session reasoning level",
            "Use Fmage MCP tools": "Use Fmage tools",
        }.items():
            text = text.replace(old, new)
        return text.encode("utf-8")
    if rel == "fmage-image-regression/SKILL.md":
        text = data.decode("utf-8").replace("In Codex desktop or another host that supports absolute local-image Markdown, normalize only the display path to forward slashes and add an inline image preview. In Pi/Pix, follow its local-artifact rules: use relative Markdown only when immediately derivable; otherwise show the absolute path as code without a preview.", "In SCNet, normalize the display path to forward slashes and add an inline Markdown image preview using the absolute local path.")
        return text.encode("utf-8")
    return data


def expected_files() -> dict[Path, bytes]:
    return {Path("skills") / p.relative_to(SOURCE_ROOT): render(p) for p in source_files()}


def manifest() -> dict[str, object]:
    return {
        "name": "fmage",
        "version": json.loads((PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"],
        "description": "通过可配置 OpenAI-compatible providers 在 SCNet 中生成、编辑和回归处理图片。",
        "keywords": ["image", "generation", "editing", "fmage", "mcp"],
        "paths": {"skills": [f"./skills/{name}" for name in SKILL_NAMES]},
        "interface": {
            "displayName": "Fmage",
            "shortDescription": "可配置 provider 的图片生成、编辑与本地回归",
            "category": "media",
            "capabilities": ["media:image", "image:edit", "image:regression", "mcp"],
            "developer": "Local developer",
            "starterPrompts": ["用 Fmage 生成一张图片", "用 Fmage 编辑这张参考图", "显示当前 Fmage provider"],
        },
        "mcpServers": {
            "Fmage": {
                "command": "node",
                "args": [str((PLUGIN_ROOT / "mcp" / "server.mjs").resolve())],
                "env": {"FMAGE_CONFIG": str(Path(os.environ.get("FMAGE_CONFIG", Path.home() / ".codex" / "fmage" / "providers.json").__str__()).resolve())},
            }
        },
    }


def actual_files(target: Path) -> set[Path]:
    return {p.relative_to(target) for p in target.rglob("*") if p.is_file() and ".scnet-plugin" not in p.parts}


def check(target: Path, expected: dict[Path, bytes]) -> list[str]:
    problems: list[str] = []
    plugin_manifest = target / ".scnet-plugin" / "plugin.json"
    if not plugin_manifest.is_file() or json.loads(plugin_manifest.read_text(encoding="utf-8")) != manifest():
        problems.append("missing or outdated: .scnet-plugin/plugin.json")
    actual = actual_files(target)
    for p in sorted(set(expected) - actual): problems.append(f"missing: {p.as_posix()}")
    for p in sorted(actual - set(expected)): problems.append(f"unexpected: {p.as_posix()}")
    for p in sorted(set(expected) & actual):
        if (target / p).read_bytes() != expected[p]: problems.append(f"outdated: {p.as_posix()}")
    return problems


def sync(target: Path) -> None:
    expected = expected_files()
    (target / ".scnet-plugin").mkdir(parents=True, exist_ok=True)
    (target / ".scnet-plugin" / "plugin.json").write_text(json.dumps(manifest(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for rel, data in expected.items():
        dest = target / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    for rel in actual_files(target):
        if rel.parts[0] == "skills" and rel not in expected:
            dest = (target / rel).resolve()
            if dest.is_relative_to(target.resolve()): dest.unlink()
    problems = check(target, expected)
    if problems: raise RuntimeError("SCNet synchronization did not converge: " + ", ".join(problems))
    print(f"Synchronized Fmage SCNet plugin to {target}")


def main() -> int:
    default = Path(os.environ.get("SCNET_PLUGIN_ROOT", "D:/scnet-client/resources/agent-plugins/fmage"))
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", type=Path, default=default)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = expected_files()
    if args.check:
        problems = check(args.target, expected)
        if problems:
            print("SCNet Fmage compatibility drift detected:", file=sys.stderr)
            print("\n".join(f"- {p}" for p in problems), file=sys.stderr)
            return 1
        print("SCNet Fmage compatibility check passed.")
        return 0
    sync(args.target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
