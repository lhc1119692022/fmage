#!/usr/bin/env python3
"""Refresh the local Fmage plugin, Pi skills, and provider configuration."""

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


def verify_plugin(cli: Path, marketplace: str) -> None:
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
    cache_path = (
        Path.home()
        / ".codex"
        / "plugins"
        / "cache"
        / marketplace
        / PLUGIN_NAME
        / expected_version
    )
    required_files = [
        cache_path / "scripts" / "gemini_generate_content_transport.py",
    ]
    for required_file in required_files:
        if not required_file.is_file():
            raise RuntimeError(f"Installed plugin cache is missing {required_file}.")


def refresh_plugin(*, check_only: bool) -> None:
    cli = resolve_codex_cli()
    marketplace = read_marketplace_name()
    if check_only:
        verify_plugin(cli, marketplace)
        print(f"Plugin is current and enabled: {PLUGIN_NAME}@{marketplace} {source_version()}")
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
    verify_plugin(cli, marketplace)
    print(f"Plugin refreshed and verified: {PLUGIN_NAME}@{marketplace} {source_version()}")


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


def refresh_pi(*, target: str | None, agents_file: str | None, check_only: bool) -> None:
    command = [sys.executable, str(PLUGIN_ROOT / "scripts" / "sync_pi_skills.py")]
    if target:
        command.extend(["--target", target])
    if agents_file:
        command.extend(["--agents-file", agents_file])
    if check_only:
        command.append("--check")
    result = run_command(command)
    require_success(result, "synchronizing Pi skills")


def refresh_scnet(*, target: str | None, check_only: bool) -> None:
    command = [sys.executable, str(PLUGIN_ROOT / "scripts" / "sync_scnet_plugin.py")]
    if target:
        command.extend(["--target", target])
    if check_only:
        command.append("--check")
    result = run_command(command)
    require_success(result, "synchronizing SCNet plugin")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", help="Path to the local providers.json")
    parser.add_argument("--pi-target", help="Pi/Pix skills root")
    parser.add_argument("--pi-agents-file", help="Pi/Pix AGENTS.md path")
    parser.add_argument("--scnet-target", help="SCNet plugin directory")
    parser.add_argument("--check", action="store_true", help="Only verify local runtime state")
    parser.add_argument("--skip-plugin", action="store_true")
    parser.add_argument("--skip-config", action="store_true")
    parser.add_argument("--skip-pi", action="store_true")
    parser.add_argument("--skip-scnet", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.skip_config:
        refresh_config(config=args.config, check_only=args.check)
    if not args.skip_pi:
        refresh_pi(target=args.pi_target, agents_file=args.pi_agents_file, check_only=args.check)
    if not args.skip_scnet:
        refresh_scnet(target=args.scnet_target, check_only=args.check)
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
