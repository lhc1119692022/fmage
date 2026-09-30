from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SKILLS = ("fmage", "fmage-config", "fmage-direct", "fmage-image-regression", "fmage-workflow")
ADAPTER_HEADER = """# Fmage VS Code adapter

This skill is adapted for VS Code's MCP host. Use the Fmage MCP server tools with
the `Fmage.` prefix, for example `Fmage.generate_image` or `Fmage.edit_image`.
Do not use Codex-only plugin command syntax.

For the direct entry, an explicit user request containing `Fmage 直传生图` or
`直传生图` selects this skill. Otherwise use the normal Fmage entry.

"""


def default_skill_root() -> Path:
    return Path.home() / ".agents" / "skills"


def default_mcp_config() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "Code" / "User" / "mcp.json"
    return Path.home() / ".config" / "Code" / "User" / "mcp.json"


def adapted_skill_text(source: Path) -> str:
    text = source.read_text(encoding="utf-8")
    text = text.replace("mcp__Fmage.", "Fmage.")
    text = text.replace("`$fmage-direct`", "an explicit `Fmage 直传生图` request")
    text = text.replace("`$fmage-workflow`", "an explicit `Fmage 工作流` request")
    text = text.replace("`$fmage-config`", "an explicit `Fmage 配置` request")
    text = text.replace("`$fmage-image-regression`", "an explicit `Fmage 图片退步` request")
    return ADAPTER_HEADER + text


def sync_skills(target_root: Path, check_only: bool) -> list[str]:
    changes: list[str] = []
    for skill_name in SKILLS:
        source_dir = PLUGIN_ROOT / "skills" / skill_name
        target_dir = target_root / skill_name
        source_skill = source_dir / "SKILL.md"
        target_skill = target_dir / "SKILL.md"
        expected = adapted_skill_text(source_skill)
        if not target_skill.exists() or target_skill.read_text(encoding="utf-8") != expected:
            changes.append(f"skill:{target_skill}")
            if not check_only:
                target_dir.mkdir(parents=True, exist_ok=True)
                target_skill.write_text(expected, encoding="utf-8", newline="\n")
        reference_dir = source_dir / "references"
        if not reference_dir.exists():
            continue
        for reference in reference_dir.glob("*"):
            if not reference.is_file():
                continue
            target_reference = target_dir / "references" / reference.name
            source_text = reference.read_text(encoding="utf-8")
            if not target_reference.exists() or target_reference.read_text(encoding="utf-8") != source_text:
                changes.append(f"reference:{target_reference}")
                if not check_only:
                    target_reference.parent.mkdir(parents=True, exist_ok=True)
                    target_reference.write_text(source_text, encoding="utf-8", newline="\n")
    return changes


def expected_server() -> dict[str, object]:
    return {
        "type": "stdio",
        "command": "node",
        "args": [str((PLUGIN_ROOT / "mcp" / "server.mjs").resolve()).replace("\\", "/")],
        "cwd": str(PLUGIN_ROOT.resolve()).replace("\\", "/"),
        "env": {
            "FMAGE_CONFIG": str((Path.home() / ".codex" / "fmage" / "providers.json").resolve()).replace("\\", "/"),
            **({"FMAGE_PYTHON": os.environ["FMAGE_PYTHON"].replace("\\", "/")} if os.environ.get("FMAGE_PYTHON") else {}),
        },
    }


def update_mcp_config(path: Path, check_only: bool) -> bool:
    payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if not isinstance(payload, dict):
        raise ValueError(f"VS Code MCP config must be a JSON object: {path}")
    servers = payload.setdefault("servers", {})
    if not isinstance(servers, dict):
        raise ValueError(f"VS Code MCP config has invalid servers object: {path}")
    expected = expected_server()
    changed = servers.get("Fmage") != expected
    if changed and not check_only:
        path.parent.mkdir(parents=True, exist_ok=True)
        servers["Fmage"] = expected
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return changed


def install(target_root: Path, mcp_config: Path, check_only: bool) -> list[str]:
    changes = sync_skills(target_root, check_only)
    if update_mcp_config(mcp_config, check_only):
        changes.append(f"mcp:{mcp_config}")
    return changes


def main() -> int:
    parser = argparse.ArgumentParser(description="Install the Fmage VS Code MCP/skills adapter.")
    parser.add_argument("--skills-dir", type=Path, default=default_skill_root())
    parser.add_argument("--mcp-config", type=Path, default=default_mcp_config())
    parser.add_argument("--check", action="store_true", help="Only report changes; do not write files.")
    args = parser.parse_args()
    changes = install(args.skills_dir, args.mcp_config, args.check)
    if changes:
        action = "would update" if args.check else "updated"
        print(f"Fmage VS Code adapter {action}:")
        for change in changes:
            print(f"- {change}")
    else:
        print("Fmage VS Code adapter is current.")
    return 0


if __name__ == "__main__":
    sys.exit(main())