"""Build private VS Code and DeepSeek Desktop plugins from the Fmage repository."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ("fmage", "fmage-direct", "fmage-config", "fmage-workflow", "fmage-image-regression")
DESCRIPTIONS = {
    "fmage": "Fmage 图片生成与编辑：理解并扩写本次提示词，支持参考图、批量图片及指定模型。仅用于用户要求交付图片时；直传请求使用 fmage-direct。",
    "fmage-direct": "Fmage 直传生图：仅在用户明确选择此入口时使用。保留提示词原文，不扩写、不翻译、不润色，只提取明确的交付参数。",
    "fmage-config": "Fmage 配置：显示或打开 providers.json，按用户要求配置平台、模型或工作流；不显示密钥，也不发起生图。",
    "fmage-workflow": "Fmage 工作流：运行已配置的 RunningHub 工作流、查询进度和取回图片。入口附带图片时按默认工作流执行，不额外要求提示词。",
    "fmage-image-regression": "Fmage 图片退步：对本地图片执行固定的缩小、模糊与单色噪点处理；通过 regress_image 一次完成，不调用创意生图模型。",
}
RUNTIME_SCRIPTS = (
    "banana_models.py", "gemini_generate_content_transport.py", "json_images_transport.py",
    "midjourney_transport.py", "json_images_support.py", "transport_common.py",
    "openai_images_transport.py", "openai_images_async_transport.py",
)
CONFIG_FILES = ("providers.example.json", "workflows.example.json", "midjourney-v8.2-contract.json", "banana-model-capabilities.json")


def local_root() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share")) / "Fmage"


def state_path() -> Path:
    return local_root() / "harness-installations.json"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def atomic_write(path: Path, data: bytes) -> None:
    if path.is_file() and path.read_bytes() == data:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".fmage-tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def adapt(text: str, host: str) -> str:
    text = text.replace("mcp__Fmage.", "mcp__Fmage__" if host == "deepseek" else "Fmage / ")
    text = re.sub(r"\$fmage([a-z-]*)", r"/fmage\1", text)
    text = text.replace("`/Fmage 直传生图`", "`/fmage-direct`")
    text = text.replace("`view_image`", "the host's image inspection tool")
    text = text.replace("config/workflows.example.json", "../../config/workflows.example.json")
    text = text.replace("config/providers.example.json", "../../config/providers.example.json")
    return text.replace("fmage:fmage", "fmage")


def skill_text(name: str, host: str) -> str:
    source = (ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
    match = re.match(r"---\r?\n.*?\r?\n---\r?\n", source, re.S)
    if not match:
        raise ValueError(f"Missing skill frontmatter: {name}")
    body = adapt(source[match.end():], host)
    host_name = "VS Code 智能体" if host == "vscode" else "DeepSeek Harness 桌面版"
    tool_note = (
        "在本机工具目录中选择 Fmage 服务器下对应的工具名，例如 generate_image、edit_image。"
        "完整前缀由 VS Code 提供，不要编造 Fmage.generate_image 这样的调用标识。"
        if host == "vscode" else
        "工具名使用 mcp__Fmage__ 前缀，例如 mcp__Fmage__generate_image、mcp__Fmage__edit_image。"
    )
    prelude = (
        f"# {host_name}适配\n\n{tool_note}\n"
        "只使用当前请求选择的入口；/fmage-direct 或用户明确说“Fmage 直传生图”才选择直传。"
        "普通生图使用 fmage。命令的展示名称以当前宿主的技能菜单为准。\n"
        "附件使用宿主给出的可读取本地路径；若只有附件标识，先用宿主的附件能力解析路径，"
        "不得把标识当作文件路径。输出使用工具返回的图片和文件链接；不能预览时交付实际保存路径。\n"
        "提供商配置由外部 FMAGE_CONFIG 指定，默认兼容现有 ~/.codex/fmage/providers.json；"
        "该路径只是共享配置，不要求在 Codex 中执行。\n\n"
    )
    if name == "fmage-image-regression":
        body = (
            "# Fmage 图片退步\n\n回复“处理中。”，将附件的绝对本地路径作为 image 参数，调用 regress_image 一次。\n"
            "该工具执行固定本地像素处理，不调用生图服务。成功后展示返回的结果路径和图片。"
            "失败立即停止并报告错误，不重试、不调用 generate_image/edit_image，也不额外运行脚本。\n"
        )
    return (
        f"---\nname: {name}\ndescription: {json.dumps(DESCRIPTIONS[name], ensure_ascii=False)}\n"
        f"user-invocable: true\ndisable-model-invocation: {'true' if name == 'fmage-direct' else 'false'}\n"
        f"---\n\n{prelude}{body}"
    )


def payload(host: str) -> dict[str, bytes]:
    if host not in ("vscode", "deepseek"):
        raise ValueError(f"Unknown harness: {host}")
    files: dict[str, bytes] = {}
    for directory, names in (("mcp", ("server.mjs", "workflows.mjs", "timeout-policy.mjs")),
                             ("scripts", RUNTIME_SCRIPTS), ("config", CONFIG_FILES)):
        for name in names:
            files[f"{directory}/{name}"] = (ROOT / directory / name).read_bytes()
    files["skills/fmage-image-regression/scripts/degrade_image.py"] = (ROOT / "skills/fmage-image-regression/scripts/degrade_image.py").read_bytes()
    catalog = []
    for name in SKILLS:
        files[f"skills/{name}/SKILL.md"] = skill_text(name, host).encode("utf-8")
        catalog.append({"name": name, "description": DESCRIPTIONS[name], "modelInvocable": name != "fmage-direct"})
        for path in sorted((ROOT / "skills" / name / "references").glob("*.md")):
            files[f"skills/{name}/references/{path.name}"] = adapt(path.read_text(encoding="utf-8"), host).encode("utf-8")
    version = read_json(ROOT / ".codex-plugin/plugin.json")["version"]
    if host == "vscode":
        files[".plugin/plugin.json"] = json_bytes({
            "name": "fmage", "version": version, "description": "Fmage 图片生成、直传生图、图片退步、工作流与配置。VS Code 智能体专用插件。",
            "skills": "./skills/", "mcpServers": "./.mcp.json",
        })
        files[".mcp.json"] = json_bytes({"mcpServers": {"Fmage": {
            "command": "node", "args": ["${PLUGIN_ROOT}/mcp/launch.mjs"], "cwd": "${PLUGIN_ROOT}",
        }}})
    else:
        files["package.json"] = json_bytes({
            "name": "dsh-fmage", "version": version,
            "description": "Fmage：图片生成、直传生图、图片退步、RunningHub 工作流与配置；DeepSeek Harness 桌面版专用。",
            "type": "module", "main": "index.mjs", "exports": {".": "./index.mjs", "./package.json": "./package.json"},
            "dsh": {"bundle": {"patch": "./cordis.patch.yml"}},
            "peerDependencies": {"@deepseek-ai/dsh-skill": "0.2.0-rc.2", "@deepseek-ai/dsh-mcp-client": "0.2.0-rc.2", "@deepseek-ai/cordis": "~4.0.4"},
        })
        files["cordis.patch.yml"] = b'- insert:\n    - id: fmage\n      name: dsh-fmage\n'
        files["index.mjs"] = (ROOT / "adapters/deepseek/index.mjs").read_bytes()
        files["catalog.json"] = json_bytes(catalog)
    files["mcp/launch.mjs"] = (ROOT / "adapters/launch.mjs").read_bytes()
    digest = hashlib.sha256()
    for name, data in sorted(files.items()):
        digest.update(name.encode() + b"\0" + data)
    files[".fmage-build.json"] = json_bytes({"host": host, "version": version, "sha256": digest.hexdigest()})
    return files


def sync_payload(files: dict[str, bytes], target: Path, check: bool = False) -> list[str]:
    changed = [name for name, data in files.items() if not (target / name).is_file() or (target / name).read_bytes() != data]
    if not check:
        # The marker is published last; watchers never see a partially written skill.
        for name in changed:
            if name != ".fmage-build.json":
                atomic_write(target / name, files[name])
        atomic_write(target / ".fmage-build.json", files[".fmage-build.json"])
    return changed


def build_archive(host: str, files: dict[str, bytes], out: Path) -> Path:
    version = json.loads(files[".fmage-build.json"])["version"]
    out.mkdir(parents=True, exist_ok=True)
    path = out / (f"fmage-vscode-{version}.zip" if host == "vscode" else f"dsh-fmage-{version}.tgz")
    temporary = path.with_name(path.name + ".fmage-tmp")
    if host == "vscode":
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data in sorted(files.items()):
                archive.writestr(f"fmage/{name}", data)
    else:
        with tarfile.open(temporary, "w:gz") as archive:
            for name, data in sorted(files.items()):
                info = tarfile.TarInfo(f"package/{name}")
                info.size = len(data)
                info.mode = 0o644
                archive.addfile(info, io.BytesIO(data))
    os.replace(temporary, path)
    return path


def verify_archive(host: str, files: dict[str, bytes], directory: Path) -> None:
    version = json.loads(files[".fmage-build.json"])["version"]
    path = directory / (f"fmage-vscode-{version}.zip" if host == "vscode" else f"dsh-fmage-{version}.tgz")
    if host == "vscode":
        with zipfile.ZipFile(path) as archive:
            actual = {name.removeprefix("fmage/"): archive.read(name) for name in archive.namelist()}
    else:
        with tarfile.open(path) as archive:
            actual = {entry.name.removeprefix("package/"): archive.extractfile(entry).read() for entry in archive.getmembers() if entry.isfile()}
    if actual != files:
        raise RuntimeError(f"Package content differs from the current source: {path}")


def jsonc_clean(text: str) -> str:
    # Preserve offsets for edits that retain unrelated comments and formatting.
    pattern = r'"(?:\\.|[^"\\])*"|//[^\r\n]*|/\*[\s\S]*?\*/'
    clean = re.sub(pattern, lambda m: m[0] if m[0].startswith('"') else re.sub(r'[^\r\n]', ' ', m[0]), text)
    return re.sub(r'"(?:\\.|[^"\\])*"|,(\s*[}\]])', lambda m: ' ' + m[1] if m[1] else m[0], clean)


def set_jsonc_fields(path: Path, fields: dict) -> None:
    text = path.read_text(encoding="utf-8-sig") if path.exists() else "{}\n"
    for key, value in fields.items():
        clean = jsonc_clean(text)
        obj = json.loads(clean)
        if key in obj and obj[key] == value:
            continue
        decoder = json.JSONDecoder()
        pos = clean.index("{") + 1
        found = False
        while True:
            while pos < len(clean) and clean[pos] in " \r\n\t,":
                pos += 1
            if clean[pos] == "}":
                break
            field, end = decoder.raw_decode(clean, pos)
            pos = end
            while clean[pos] in " \r\n\t:":
                pos += 1
            _, end = decoder.raw_decode(clean, pos)
            if field == key:
                text = text[:pos] + json.dumps(value, ensure_ascii=False, indent=4) + text[end:]
                found = True
                break
            pos = end
        if not found:
            start = clean.index("{") + 1
            entry = "\n    " + json.dumps(key) + ": " + json.dumps(value, ensure_ascii=False, indent=4)
            text = text[:start] + entry + ("," if obj else "") + "\n" + text[start:]
    atomic_write(path, text.encode("utf-8"))


def backup(path: Path, root: Path) -> None:
    if path.is_file():
        target = root / (hashlib.sha256(str(path).encode()).hexdigest()[:12] + "-" + path.name)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def clean_legacy(backup_root: Path) -> list[str]:
    removed = []
    shared = Path.home() / ".agents/skills"
    for name in SKILLS:
        directory = shared / name
        skill = directory / "SKILL.md"
        if not skill.is_file():
            continue
        if directory.is_symlink() or directory.resolve().parent != shared.resolve():
            raise RuntimeError(f"Refusing to move unexpected shared skill path: {directory}")
        if "# Fmage VS Code adapter" not in skill.read_text(encoding="utf-8"):
            raise RuntimeError(f"Shared skill is not the known Fmage adapter; preserved: {directory}")
        target = backup_root / "shared-skills" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(directory), str(target))
        removed.append(str(directory))
    user = Path(os.environ["APPDATA"]) / "Code/User"
    mcp = user / "mcp.json"
    if mcp.exists():
        data = json.loads(jsonc_clean(mcp.read_text(encoding="utf-8-sig")))
        servers = data.get("servers", {})
        if "Fmage" in servers:
            args = servers["Fmage"].get("args", [])
            if not any(str(ROOT).replace("\\", "/").lower() in str(arg).replace("\\", "/").lower() for arg in args):
                raise RuntimeError("Standalone Fmage MCP registration has an unknown source; preserved.")
            backup(mcp, backup_root)
            del servers["Fmage"]
            set_jsonc_fields(mcp, {"servers": servers})
            removed.append(str(mcp) + "#servers.Fmage")
    return removed


def deepseek_installation_errors(files: dict[str, bytes], profile: Path, package: Path) -> list[str]:
    config = read_json(profile / "package.json")
    installed = profile / "node_modules/dsh-fmage"
    failures = []
    if "dsh-fmage" not in config.get("dsh", {}).get("profile", {}).get("bundles", []):
        failures.append("DeepSeek Desktop bundle is not enabled")
    spec = config.get("dependencies", {}).get("dsh-fmage", "")
    if spec.replace("\\", "/") != "file:" + package.as_posix():
        failures.append("DeepSeek Desktop must use the current private TGZ dependency, not link:")
    if installed.is_symlink() or installed.is_junction() or installed.resolve() != installed.absolute():
        failures.append("DeepSeek Desktop requires a physical profile-local package; linked packages are not supported")
    elif sync_payload(files, installed, True):
        failures.append("DeepSeek Desktop installed package differs from the repository payload")
    return failures


def install_deepseek(files: dict[str, bytes], profile: Path, cli: Path, package: Path) -> None:
    if not deepseek_installation_errors(files, profile, package):
        return
    rollback = local_root() / "backups" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    for name in ("package.json", "pnpm-lock.yaml", "pnpm-workspace.yaml", "cordis.patch.yml"):
        backup(profile / name, rollback)
    result = subprocess.run([str(cli), "plugin", "--profile", "desktop", "add",
                             "file:" + package.as_posix(), "--offline", "--ignore-scripts"],
                            cwd=ROOT, check=False)
    if result.returncode:
        raise RuntimeError(f"DeepSeek Desktop TGZ installation failed (exit {result.returncode}); backup: {rollback}")
    failures = deepseek_installation_errors(files, profile, package)
    if failures:
        raise RuntimeError("; ".join(failures))


def verify_deepseek_loader(cli: Path, profile: Path) -> None:
    resources = cli.parents[3]
    runtime = resources / "app.asar/dsh"
    executable = resources.parent / "DeepSeek Harness.exe"
    if not executable.is_file() or not (resources / "app.asar").is_file():
        raise RuntimeError("Cannot locate the installed Desktop Electron runtime from its CLI")
    with tempfile.TemporaryDirectory(prefix="fmage-desktop-profile-") as temporary:
        result = subprocess.run([
            str(executable), "--expose-internals", str(ROOT / "tests/deepseek_profile_smoke.mjs"),
            str(runtime), str(profile), temporary,
        ], env={**os.environ, "ELECTRON_RUN_AS_NODE": "1", "DSH_TELEMETRY_DISABLED": "1",
                "FMAGE_CONFIG": str(Path(temporary) / "no-credentials.json")},
            capture_output=True, text=True, encoding="utf-8", timeout=45, check=False)
    if result.returncode or '"nativeProfileLoader":true' not in result.stdout:
        raise RuntimeError(f"DeepSeek native profile activation probe failed: {result.stderr or result.stdout}")
    print("DeepSeek isolated native profile activation verified: 5 skills, 10 MCP tools (not the live desktop process)")


def deepseek_runtime_status(installed: Path, files: dict[str, bytes]) -> str:
    marker = installed / ".fmage-runtime.json"
    if not marker.is_file():
        return "NOT verified: no successful activation receipt"
    try:
        loaded = read_json(marker)
        expected = json.loads(files[".fmage-build.json"])
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(loaded["heartbeatAt"].replace("Z", "+00:00"))).total_seconds()
        if not 0 <= age <= 15:
            return "NOT verified: activation heartbeat is stale"
        if loaded.get("sha256") != expected["sha256"] or loaded.get("version") != expected["version"]:
            return "RELOAD REQUIRED: desktop still has a previous payload loaded"
        if loaded.get("skillCount") != 5 or loaded.get("toolCount") != 10 or not loaded.get("mcpReady"):
            return "NOT ready: skill/MCP registration is incomplete"
        return f"live activation verified: {loaded['version']}, pid={loaded['pid']}, 5 skills, 10 tools"
    except (OSError, ValueError, KeyError):
        return "NOT verified: invalid or legacy activation receipt"


def refresh_registered(*, check_only: bool = False) -> None:
    path = state_path()
    if not path.exists():
        return
    state = read_json(path)
    if Path(state["source"]).resolve() != ROOT.resolve():
        raise RuntimeError("Registered harness source differs from this repository.")
    failures = []
    for host, settings in state["hosts"].items():
        target = Path(settings["path"])
        files = payload(host)
        changes = sync_payload(files, target, check_only)
        if check_only and changes:
            failures.append(f"{host}: {len(changes)} outdated files")
        if not check_only:
            package = build_archive(host, files, Path(state["packages"]))
            print(f"{host} plugin synchronized: {target}; package: {package}")
        elif not changes:
            print(f"{host} plugin matches repository: {target}")
        verify_archive(host, files, Path(state["packages"]))
        if host == "vscode":
            user_settings = target.parents[1] / "settings.json"
            config = json.loads(jsonc_clean(user_settings.read_text(encoding="utf-8-sig")))
            if config.get("chat.pluginLocations", {}).get(target.as_posix()) is not True or config.get("chat.plugins.enabled") is not True:
                failures.append("VS Code private plugin is not enabled in settings")
        else:
            profile = Path(settings["profile"])
            installed = profile / "node_modules/dsh-fmage"
            package = Path(state["packages"]) / f"dsh-fmage-{json.loads(files['.fmage-build.json'])['version']}.tgz"
            if not check_only:
                install_deepseek(files, profile, Path(settings["cli"]), package)
            problems = deepseek_installation_errors(files, profile, package)
            failures.extend(problems)
            print(f"DeepSeek Desktop physical installation checked: {installed}")
            if not problems:
                verify_deepseek_loader(Path(settings["cli"]), profile)
            print("DeepSeek Desktop runtime " + deepseek_runtime_status(installed, files))
    if failures:
        raise RuntimeError("; ".join(failures))
    if not check_only:
        print("Installed artifacts updated. Existing host processes may require plugin/window reload; no active requests were interrupted.")


def configure_installations(deepseek_cli: Path) -> None:
    user = Path(os.environ["APPDATA"]) / "Code/User"
    targets = {"vscode": user / "agent-plugins/fmage", "deepseek": local_root() / "deepseek/dsh-fmage"}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    rollback = local_root() / "backups" / stamp
    settings_path = user / "settings.json"
    backup(settings_path, rollback)
    config = json.loads(jsonc_clean(settings_path.read_text(encoding="utf-8-sig"))) if settings_path.exists() else {}
    locations = config.get("chat.pluginLocations", {})
    locations[targets["vscode"].as_posix()] = True
    for host, target in targets.items():
        sync_payload(payload(host), target)
    profile = Path(os.environ.get("DSH_HOME", str(Path.home() / ".dsh"))) / "profiles/desktop"
    if not (profile / "package.json").is_file():
        raise RuntimeError("DeepSeek Desktop profile is not initialized; launch the desktop application first.")
    for name in ("package.json", "pnpm-lock.yaml", "pnpm-workspace.yaml", "cordis.patch.yml"):
        backup(profile / name, rollback)
    files = payload("deepseek")
    package = build_archive("deepseek", files, local_root() / "packages")
    install_deepseek(files, profile, deepseek_cli, package)
    profile_config = read_json(profile / "package.json")
    if "dsh-fmage" not in profile_config.get("dsh", {}).get("profile", {}).get("bundles", []):
        raise RuntimeError("DeepSeek package installed but its bundle is not enabled; legacy installs preserved.")
    set_jsonc_fields(settings_path, {"chat.pluginLocations": locations, "chat.plugins.enabled": True})
    atomic_write(state_path(), json_bytes({"source": str(ROOT), "packages": str(local_root() / "packages"), "hosts": {
        host: {"path": str(target), **({"cli": str(deepseek_cli), "profile": str(profile)} if host == "deepseek" else {})} for host, target in targets.items()
    }}))
    for removed in clean_legacy(rollback):
        print(f"Legacy Fmage installation archived: {removed}")
    print(f"Rollback files: {rollback}")
    refresh_registered()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--configure", action="store_true", help="Install private payloads and migrate the old shared adapter")
    parser.add_argument("--deepseek-cli", type=Path)
    parser.add_argument("--build", choices=("vscode", "deepseek", "all"))
    parser.add_argument("--output", type=Path, default=local_root() / "packages")
    args = parser.parse_args()
    if args.configure:
        if args.check or not args.deepseek_cli or not args.deepseek_cli.is_file():
            parser.error("--configure requires the existing Desktop --deepseek-cli and cannot use --check")
        configure_installations(args.deepseek_cli)
    elif args.build:
        for host in ("vscode", "deepseek") if args.build == "all" else (args.build,):
            print(build_archive(host, payload(host), args.output))
    else:
        refresh_registered(check_only=args.check)


if __name__ == "__main__":
    main()
