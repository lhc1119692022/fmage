#!/usr/bin/env python3
"""Refresh the local Fmage plugin and provider configuration."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_NAME = "fmage"
DEFAULT_MARKETPLACE = "personal"
MAX_DEFAULT_PROMPTS = 3
EXPECTED_MCP_TOOL_NAMES = {
    "regress_image",
    "trace_image_job_plan",
    "probe_image_generation",
    "generate_image",
    "generate_image_batch",
    "edit_image",
    "edit_image_batch",
    "get_image_task_status",
    "get_provider_status",
}
REQUIRED_SKILLS = {
    "fmage": True,
    "fmage-config": True,
    "fmage-direct": False,
    "fmage-image-regression": True,
}


def run_command(command: list[str], *, capture_output: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=PLUGIN_ROOT,
        check=False,
        capture_output=capture_output,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def require_success(result: subprocess.CompletedProcess[str], label: str) -> None:
    if result.returncode == 0:
        return
    output = (result.stdout or "") + (result.stderr or "")
    detail = output.strip()
    raise RuntimeError(f"{label} failed{': ' + detail if detail else ''}")


def resolve_codex_cli() -> Path:
    configured = os.environ.get("FMAGE_CODEX_CLI", "").strip()
    candidates = [
        Path(configured).expanduser() if configured else None,
        Path.home() / ".codex" / "plugins" / ".plugin-appserver" / "codex.exe",
    ]
    found = shutil.which("codex")
    if found:
        candidates.append(Path(found))
    for candidate in candidates:
        if candidate and candidate.is_file():
            return candidate
    raise RuntimeError(
        "No user-writable Codex CLI was found. Set FMAGE_CODEX_CLI to the app-server codex.exe."
    )


def resolve_power_shell() -> str:
    for executable in ("pwsh.exe", "powershell.exe", "pwsh", "powershell"):
        found = shutil.which(executable)
        if found:
            return found
    raise RuntimeError("PowerShell is required to validate the local Codex plugin.")


def helper_path(name: str) -> Path:
    path = Path.home() / ".codex" / "skills" / ".system" / "plugin-creator" / "scripts" / name
    if not path.is_file():
        raise RuntimeError(f"Codex plugin helper is missing: {path}")
    return path


def read_marketplace_name() -> str:
    result = run_command(
        [sys.executable, str(helper_path("read_marketplace_name.py"))],
        capture_output=True,
    )
    require_success(result, "reading marketplace name")
    name = (result.stdout or "").strip()
    if not name:
        raise RuntimeError("The personal marketplace name is empty.")
    return name


def source_version() -> str:
    manifest_path = PLUGIN_ROOT / ".codex-plugin" / "plugin.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    version = payload.get("version")
    if not isinstance(version, str) or not version.strip():
        raise RuntimeError(f"Plugin manifest has no usable version: {manifest_path}")
    return version


def plugin_list(cli: Path) -> dict[str, Any]:
    result = run_command([str(cli), "plugin", "list", "--json"], capture_output=True)
    require_success(result, "listing installed plugins")
    try:
        payload = json.loads(result.stdout or "")
    except json.JSONDecodeError as error:
        raise RuntimeError("Codex plugin list did not return valid JSON.") from error
    if not isinstance(payload, dict):
        raise RuntimeError("Codex plugin list returned an invalid payload.")
    return payload


def cache_path_for_version(marketplace: str, version: str) -> Path:
    return (
        Path.home()
        / ".codex"
        / "plugins"
        / "cache"
        / marketplace
        / PLUGIN_NAME
        / version
    )


def verify_mcp_handshake(server_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        raise RuntimeError("Node.js is required to verify the Fmage MCP server.")
    requests = "\n".join(
        [
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-11-25",
                        "capabilities": {},
                        "clientInfo": {"name": "fmage-runtime-check", "version": "1"},
                    },
                }
            ),
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized",
                    "params": {},
                }
            ),
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/list",
                    "params": {},
                }
            ),
        ]
    ) + "\n"
    try:
        result = subprocess.run(
            [node, str(server_path)],
            cwd=server_path.parents[1],
            input=requests,
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(f"Fmage MCP handshake timed out: {server_path}") from error
    if result.returncode != 0:
        raise RuntimeError(f"Fmage MCP server exited with code {result.returncode}: {server_path}")

    responses: dict[int, dict[str, Any]] = {}
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(message, dict) and isinstance(message.get("id"), int):
            responses[message["id"]] = message

    initialize = responses.get(1, {}).get("result")
    server_info = initialize.get("serverInfo") if isinstance(initialize, dict) else None
    if not isinstance(server_info, dict) or server_info.get("name") != "Fmage":
        raise RuntimeError(f"Fmage MCP initialize response is invalid: {server_path}")

    tools_result = responses.get(2, {}).get("result")
    tools = tools_result.get("tools") if isinstance(tools_result, dict) else None
    tool_names = {tool.get("name") for tool in tools if isinstance(tool, dict)} if isinstance(tools, list) else set()
    if tool_names != EXPECTED_MCP_TOOL_NAMES:
        raise RuntimeError(
            f"Fmage MCP tool list mismatch: expected {sorted(EXPECTED_MCP_TOOL_NAMES)}, got {sorted(tool_names)}"
        )


def verify_cached_plugin(cache_path: Path) -> None:
    required_files = [
        cache_path / ".codex-plugin" / "plugin.json",
        cache_path / ".mcp.json",
        cache_path / "mcp" / "server.mjs",
        cache_path / "scripts" / "gemini_generate_content_transport.py",
    ]
    for skill_name in REQUIRED_SKILLS:
        required_files.extend(
            [
                cache_path / "skills" / skill_name / "SKILL.md",
                cache_path / "skills" / skill_name / "agents" / "openai.yaml",
            ]
        )
    for required_file in required_files:
        if not required_file.is_file():
            raise RuntimeError(f"Installed plugin cache is missing {required_file}.")

    try:
        manifest = json.loads((cache_path / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        mcp_config = json.loads((cache_path / ".mcp.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Installed plugin cache metadata is invalid: {cache_path}") from error

    if manifest.get("mcpServers") != "./.mcp.json":
        raise RuntimeError(f"Installed plugin manifest does not point to ./.mcp.json: {cache_path}")
    if manifest.get("skills") != "./skills/":
        raise RuntimeError(f"Installed plugin manifest does not point to ./skills/: {cache_path}")
    default_prompts = manifest.get("interface", {}).get("defaultPrompt")
    if not isinstance(default_prompts, list) or len(default_prompts) > MAX_DEFAULT_PROMPTS:
        raise RuntimeError(
            f"Installed plugin has more than {MAX_DEFAULT_PROMPTS} supported default prompts: {cache_path}"
        )
    server = mcp_config.get("mcpServers", {}).get("Fmage")
    if server != {"command": "node", "args": ["./mcp/server.mjs"], "cwd": "."}:
        raise RuntimeError(f"Installed Fmage MCP registration is invalid: {cache_path / '.mcp.json'}")

    for skill_name, allow_implicit in REQUIRED_SKILLS.items():
        metadata_path = cache_path / "skills" / skill_name / "agents" / "openai.yaml"
        metadata = metadata_path.read_text(encoding="utf-8")
        expected_value = "true" if allow_implicit else "false"
        if f"allow_implicit_invocation: {expected_value}" not in metadata:
            raise RuntimeError(f"Skill {skill_name} has an unexpected invocation policy: {metadata_path}")

    verify_mcp_handshake(cache_path / "mcp" / "server.mjs")


def verify_plugin(cli: Path, marketplace: str) -> Path:
    payload = plugin_list(cli)
    expected_id = f"{PLUGIN_NAME}@{marketplace}"
    records = payload.get("installed")
    if not isinstance(records, list):
        raise RuntimeError("Codex plugin list has no installed plugin array.")
    record = next(
        (item for item in records if isinstance(item, dict) and item.get("pluginId") == expected_id),
        None,
    )
    expected_version = source_version()
    if not isinstance(record, dict):
        raise RuntimeError(f"Plugin {expected_id} is not installed.")
    if record.get("version") != expected_version:
        raise RuntimeError(
            f"Plugin {expected_id} has version {record.get('version')!r}; expected {expected_version!r}."
        )
    if record.get("installed") is not True or record.get("enabled") is not True:
        raise RuntimeError(f"Plugin {expected_id} is not both installed and enabled.")
    cache_path = cache_path_for_version(marketplace, expected_version)
    verify_cached_plugin(cache_path)
    return cache_path


def refresh_plugin(*, check_only: bool) -> None:
    cli = resolve_codex_cli()
    marketplace = read_marketplace_name()
    if check_only:
        cache_path = verify_plugin(cli, marketplace)
        print(
            f"Plugin is current, enabled, and MCP-verified: {PLUGIN_NAME}@{marketplace} "
            f"{source_version()} ({cache_path})"
        )
        return

    validator = Path.home() / ".codex" / "tools" / "validate-plugin.ps1"
    if not validator.is_file():
        raise RuntimeError(f"Plugin validator is missing: {validator}")
    validation = run_command(
        [resolve_power_shell(), "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(validator), str(PLUGIN_ROOT)]
    )
    require_success(validation, "validating plugin")

    cachebuster = run_command(
        [sys.executable, str(helper_path("update_plugin_cachebuster.py")), str(PLUGIN_ROOT)]
    )
    require_success(cachebuster, "updating plugin cachebuster")

    install = run_command([str(cli), "plugin", "add", f"{PLUGIN_NAME}@{marketplace}"])
    require_success(install, "reinstalling plugin")
    cache_path = verify_plugin(cli, marketplace)
    print(
        f"Plugin refreshed, enabled, and MCP-verified: {PLUGIN_NAME}@{marketplace} "
        f"{source_version()} ({cache_path})"
    )
    print("Start a new Codex task so Desktop reloads the updated skill and MCP catalogs.")


def refresh_config(*, config: str | None, check_only: bool) -> None:
    command = [sys.executable, str(PLUGIN_ROOT / "scripts" / "migrate_local_config.py")]
    if config:
        command.extend(["--config", config])
    if check_only:
        command.append("--check")
    result = run_command(command)
    if check_only and result.returncode == 1:
        raise RuntimeError("Local Fmage configuration needs migration.")
    require_success(result, "migrating local Fmage configuration")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", help="Path to the local providers.json")
    parser.add_argument("--check", action="store_true", help="Only verify local runtime state")
    parser.add_argument("--skip-plugin", action="store_true")
    parser.add_argument("--skip-config", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.skip_config:
        refresh_config(config=args.config, check_only=args.check)
    if not args.skip_plugin:
        refresh_plugin(check_only=args.check)
    print("Fmage local runtime refresh complete.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from error
