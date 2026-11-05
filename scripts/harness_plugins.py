"""Build the private DeepSeek Desktop plugin from the Fmage repository."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
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
import time

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ("fmage", "fmage-direct", "fmage-config", "fmage-workflow", "fmage-image-regression", "fmage-svg", "fmage-gif")
DESCRIPTIONS = {
    "fmage": "Fmage 图片生成与编辑：理解并扩写本次提示词，支持参考图、批量图片及指定模型。仅用于用户要求交付图片时；直传请求使用 fmage-direct。",
    "fmage-direct": "Fmage 直传生图：仅在用户明确选择此入口时使用。保留提示词原文，不扩写、不翻译、不润色，只提取明确的交付参数。",
    "fmage-config": "Fmage 配置：显示或打开 providers.json，按用户要求配置平台、模型或工作流；不显示密钥，也不发起生图。",
    "fmage-workflow": "Fmage 工作流：运行已配置的 RunningHub 工作流、查询进度和取回图片。入口附带图片时按默认工作流执行，不额外要求提示词。",
    "fmage-image-regression": "Fmage 图片退步：对本地图片执行固定的缩小、模糊与单色噪点处理；通过 regress_image 一次完成，不调用创意生图模型。",
    "fmage-svg": "Fmage SVG：仅在用户明确选择此入口时使用。通过 generate_svg 用 gpt-image-2.6 生成单个静态 SVG 矢量图。",
    "fmage-gif": "Fmage GIF：仅在用户明确选择此入口时使用。通过 generate_gif 用 gpt-image-2.6 生成单个 GIF 动图。",
}
# Entries the model must never select on its own; only the user invokes them.
EXPLICIT_ONLY_SKILLS = frozenset({"fmage-direct", "fmage-svg", "fmage-gif"})
RUNTIME_SCRIPTS = (
    "banana_models.py", "gemini_generate_content_transport.py",
    "midjourney_transport.py", "transport_common.py",
    "openai_images_transport.py", "openai_images_async_transport.py",
)
CONFIG_FILES = ("providers.example.json", "workflows.example.json", "midjourney-v8.2-contract.json", "banana-model-capabilities.json", "gpt-image-model-families.json")
# Harness checks dsh peers with includePrerelease=True. Keep the oldest
# supported runtime as the floor, allowing future releases and prereleases.
DEEPSEEK_PEER_VERSIONS = ">=0.2.0-rc.2"
# The server lists ten of its twelve tools: trace_image_job_plan and probe_image_generation
# stay callable by name but are no longer part of the advertised surface.
MCP_TOOL_COUNT = 10


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


def adapt(text: str) -> str:
    text = text.replace("mcp__Fmage.", "mcp__Fmage__")
    text = re.sub(r"\$fmage([a-z-]*)", r"/fmage\1", text)
    text = text.replace("`/Fmage 直传生图`", "`/fmage-direct`")
    text = text.replace("`/Fmage SVG`", "`/fmage-svg`").replace("`/Fmage GIF`", "`/fmage-gif`")
    text = text.replace("`view_image`", "the host's image inspection tool")
    text = text.replace("config/workflows.example.json", "../../config/workflows.example.json")
    text = text.replace("config/providers.example.json", "../../config/providers.example.json")
    return text.replace("fmage:fmage", "fmage")


def skill_text(name: str) -> str:
    source = (ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
    match = re.match(r"---\r?\n.*?\r?\n---\r?\n", source, re.S)
    if not match:
        raise ValueError(f"Missing skill frontmatter: {name}")
    body = adapt(source[match.end():])
    prelude = (
        "# DeepSeek Harness 桌面版适配\n\n"
        "工具名使用 mcp__Fmage__ 前缀，例如 mcp__Fmage__generate_image、mcp__Fmage__edit_image。\n"
        "只使用当前请求选择的入口；/fmage-direct 或用户明确说“Fmage 直传生图”才选择直传；"
        "/fmage-svg、/fmage-gif 或用户明确说“Fmage SVG”“Fmage GIF”才使用 generate_svg、generate_gif。"
        "普通生图使用 fmage。命令的展示名称以当前宿主的技能菜单为准。\n"
        "附件使用宿主给出的可读取本地路径；若只有附件标识，先用宿主的附件能力解析路径，"
        "不得把标识当作文件路径。输出使用工具返回的图片和文件链接；不能预览时交付实际保存路径。\n"
        "提供商配置位于 DeepSeek 自己的数据目录（默认 `~/.dsh/fmage/providers.json`）；"
        "`FMAGE_CONFIG` 可指定其他文件。\n\n"
    )
    if name == "fmage-image-regression":
        body = (
            "# Fmage 图片退步\n\n回复“处理中。”，将附件的绝对本地路径作为 image 参数，调用 regress_image 一次。\n"
            "该工具执行固定本地像素处理，不调用生图服务。成功后展示返回的结果路径和图片。"
            "失败立即停止并报告错误，不重试、不调用 generate_image/edit_image，也不额外运行脚本。\n"
        )
    return (
        f"---\nname: {name}\ndescription: {json.dumps(DESCRIPTIONS[name], ensure_ascii=False)}\n"
        f"user-invocable: true\ndisable-model-invocation: {'true' if name in EXPLICIT_ONLY_SKILLS else 'false'}\n"
        f"---\n\n{prelude}{body}"
    )


def payload() -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for directory, names in (("mcp", ("server.mjs", "workflows.mjs", "timeout-policy.mjs", "retry-policy.mjs")),
                             ("scripts", RUNTIME_SCRIPTS), ("config", CONFIG_FILES)):
        for name in names:
            files[f"{directory}/{name}"] = (ROOT / directory / name).read_bytes()
    files["skills/fmage-image-regression/scripts/degrade_image.py"] = (ROOT / "skills/fmage-image-regression/scripts/degrade_image.py").read_bytes()
    catalog = []
    for name in SKILLS:
        files[f"skills/{name}/SKILL.md"] = skill_text(name).encode("utf-8")
        catalog.append({"name": name, "description": DESCRIPTIONS[name], "modelInvocable": name not in EXPLICIT_ONLY_SKILLS})
        for path in sorted((ROOT / "skills" / name / "references").glob("*.md")):
            files[f"skills/{name}/references/{path.name}"] = adapt(path.read_text(encoding="utf-8")).encode("utf-8")
    version = read_json(ROOT / ".codex-plugin/plugin.json")["version"]
    files["package.json"] = json_bytes({
        "name": "dsh-fmage", "version": version,
        "description": "Fmage：图片生成、直传生图、SVG 与 GIF 生成、图片退步、RunningHub 工作流与配置；DeepSeek Harness 桌面版专用。",
        "type": "module", "main": "index.mjs",
        "exports": {".": "./index.mjs", "./client": "./client.js", "./package.json": "./package.json"},
        # dsh.client publishes the settings page into the Desktop/Web client module graph.
        "dsh": {"bundle": {"patch": "./cordis.patch.yml"}, "client": {"platform": "web"}},
        "peerDependencies": {"@deepseek-ai/dsh-skill": DEEPSEEK_PEER_VERSIONS, "@deepseek-ai/dsh-mcp-client": DEEPSEEK_PEER_VERSIONS, "@deepseek-ai/cordis": "~4.0.4"},
    })
    files["cordis.patch.yml"] = b'- insert:\n    - id: fmage\n      name: dsh-fmage\n'
    for name in ("index.mjs", "config-api.mjs", "client.js"):
        files[name] = (ROOT / "adapters/deepseek" / name).read_bytes()
    files["catalog.json"] = json_bytes(catalog)
    files["mcp/launch.mjs"] = (ROOT / "adapters/launch.mjs").read_bytes()
    digest = hashlib.sha256()
    for name, data in sorted(files.items()):
        digest.update(name.encode() + b"\0" + data)
    # Record the owned paths so a later build can delete files it stops shipping.
    files[".fmage-build.json"] = json_bytes({
        "host": "deepseek", "version": version, "sha256": digest.hexdigest(), "files": sorted(files),
    })
    return files


def stale_payload(files: dict[str, bytes], target: Path) -> list[str]:
    """Files this payload owned before and no longer ships."""
    try:
        previous = read_json(target / ".fmage-build.json").get("files")
    except (OSError, ValueError):
        previous = None
    # Markers written before the file list existed fall back to the scripts it owned.
    owned = {name for name in previous if isinstance(name, str)} if isinstance(previous, list) else {
        f"scripts/{path.name}" for path in (target / "scripts").glob("*.py")
    }
    return sorted(name for name in owned if name not in files and (target / name).is_file())


def sync_payload(files: dict[str, bytes], target: Path, check: bool = False) -> list[str]:
    changed = [name for name, data in files.items() if not (target / name).is_file() or (target / name).read_bytes() != data]
    stale = stale_payload(files, target)
    if not check:
        # Drop retired files first so a removed module cannot linger in an installed copy.
        for name in stale:
            (target / name).unlink(missing_ok=True)
        # The marker is published last; watchers never see a partially written skill.
        for name in changed:
            if name != ".fmage-build.json":
                atomic_write(target / name, files[name])
        atomic_write(target / ".fmage-build.json", files[".fmage-build.json"])
    return changed + stale


def build_archive(files: dict[str, bytes], out: Path) -> Path:
    version = json.loads(files[".fmage-build.json"])["version"]
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"dsh-fmage-{version}.tgz"
    temporary = path.with_name(path.name + ".fmage-tmp")
    with tarfile.open(temporary, "w:gz") as archive:
        for name, data in sorted(files.items()):
            info = tarfile.TarInfo(f"package/{name}")
            info.size = len(data)
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(data))
    os.replace(temporary, path)
    return path


def verify_archive(files: dict[str, bytes], directory: Path) -> None:
    version = json.loads(files[".fmage-build.json"])["version"]
    path = directory / f"dsh-fmage-{version}.tgz"
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
    return removed


def retire_vscode(state: dict, backup_root: Path) -> list[str]:
    """Archive the retired VS Code agent plugin and drop its settings entry."""
    settings = state.get("hosts", {}).pop("vscode", None)
    removed: list[str] = []
    if not isinstance(settings, dict) or not settings.get("path"):
        return removed
    target = Path(settings["path"])
    if target.is_dir():
        archived = backup_root / "vscode-agent-plugin"
        archived.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(target), str(archived))
        removed.append(f"{target} -> {archived}")
    user_settings = target.parents[1] / "settings.json"
    if user_settings.is_file():
        config = json.loads(jsonc_clean(user_settings.read_text(encoding="utf-8-sig")))
        locations = config.get("chat.pluginLocations")
        if isinstance(locations, dict) and target.as_posix() in locations:
            del locations[target.as_posix()]
            backup(user_settings, backup_root)
            set_jsonc_fields(user_settings, {"chat.pluginLocations": locations})
            removed.append(f"{user_settings}#chat.pluginLocations")
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


@dataclass(frozen=True)
class DeepSeekRuntime:
    kind: str
    cli: list[str]
    probe: list[str]
    cwd: Path
    anchor: Path


def deepseek_runtime(settings: dict) -> DeepSeekRuntime:
    runtime = settings.get("runtime")
    if runtime is not None:
        if not isinstance(runtime, dict) or runtime.get("kind") != "source" or not runtime.get("root"):
            raise RuntimeError("DeepSeek runtime must declare kind=source and an explicit source root.")
        source = Path(runtime["root"]).expanduser().resolve()
        cli = source / "apps/cli/src/bin.ts"
        anchor = source / "apps/cli/package.json"
        for path in (cli, anchor, source / "node_modules/tsx/package.json",
                     source / "package.json", source / "node_modules/pnpm/package.json",
                     source / "node_modules/pnpm/bin/pnpm.cjs"):
            if not path.is_file():
                raise RuntimeError(f"DeepSeek source runtime dependency is missing: {path}")
        if read_json(anchor).get("name") != "@deepseek-ai/dsh":
            raise RuntimeError(f"DeepSeek source CLI manifest is invalid: {anchor}")
        manager = read_json(source / "package.json").get("packageManager", "")
        pnpm = read_json(source / "node_modules/pnpm/package.json")
        if pnpm.get("name") != "pnpm" or manager.split("+", 1)[0] != f"pnpm@{pnpm.get('version')}":
            raise RuntimeError("DeepSeek source pnpm must match the repository's packageManager pin.")
        node = shutil.which("node")
        if node is None:
            raise RuntimeError("Node.js is required for the DeepSeek source runtime.")
        # Match the source launcher's cwd and tsx preload; never use a global dsh.
        command = [node, "--import", "tsx/esm"]
        return DeepSeekRuntime(
            "source", [*command, str(ROOT / "scripts/deepseek_source_cli.mjs"), str(source)],
            command, source, anchor,
        )

    cli = Path(settings.get("cli", ""))
    if not cli.is_file():
        raise RuntimeError(
            f"Registered DeepSeek Desktop CLI is missing: {cli}. "
            "For a source launcher, register its repository with --register-deepseek-source."
        )
    resources = cli.resolve().parents[3]
    executable = resources.parent / "DeepSeek Harness.exe"
    unpacked_anchor = resources / "app/dsh/node_modules/@deepseek-ai/dsh/package.json"
    asar_anchor = resources / "app.asar/dsh/node_modules/@deepseek-ai/dsh/package.json"
    if not executable.is_file() or not (unpacked_anchor.is_file() or (resources / "app.asar").is_file()):
        raise RuntimeError("Cannot locate the installed Desktop Electron runtime from its CLI")
    return DeepSeekRuntime(
        "electron", [str(cli)], [str(executable), "--expose-internals"], ROOT,
        unpacked_anchor if unpacked_anchor.is_file() else asar_anchor,
    )


def deepseek_environment(profile: Path, *, probe: bool = False, kind: str = "source") -> dict[str, str]:
    profile = profile.resolve()
    if profile.name != "desktop" or profile.parent.name != "profiles":
        raise RuntimeError(f"DeepSeek Fmage installation must target profiles/desktop: {profile}")
    environment = {**os.environ, "DSH_HOME": str(profile.parent.parent), "DSH_TELEMETRY_DISABLED": "1"}
    environment.pop("ELECTRON_RUN_AS_NODE", None)
    if probe and kind == "electron":
        environment["ELECTRON_RUN_AS_NODE"] = "1"
    return environment


def register_deepseek_source(source: Path) -> None:
    path = state_path()
    state = read_json(path)
    if Path(state["source"]).resolve() != ROOT.resolve():
        raise RuntimeError("Registered harness source differs from this repository.")
    settings = state["hosts"]["deepseek"]
    updated = {**settings, "runtime": {"kind": "source", "root": str(source.expanduser().resolve())}}
    updated.pop("cli", None)
    runtime = deepseek_runtime(updated)
    profile = Path(settings["profile"])
    deepseek_environment(profile)
    if not (profile / "package.json").is_file():
        raise RuntimeError(f"Existing Desktop profile is missing: {profile}")
    if settings == updated:
        print(f"DeepSeek source runtime already registered: {runtime.cwd}")
        return
    rollback = local_root() / "backups" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup(path, rollback)
    state["hosts"]["deepseek"] = updated
    atomic_write(path, json_bytes(state))
    print(f"DeepSeek source runtime registered: {runtime.cwd}; Desktop profile preserved: {profile}; backup: {rollback}")


def runtime_receipt(installed: Path) -> dict | None:
    """The live plugin's heartbeat record, or None when no host has it loaded."""
    try:
        loaded = read_json(installed / ".fmage-runtime.json")
    except (OSError, ValueError):
        return None
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(loaded["heartbeatAt"].replace("Z", "+00:00"))).total_seconds()
    except (KeyError, ValueError):
        return None
    return loaded if 0 <= age <= 15 else None


def wait_for(predicate, timeout: float, interval: float = 0.5) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def select_bundle(profile: Path, selected: bool) -> None:
    """Add or remove dsh-fmage in the profile's ordered bundle list."""
    path = profile / "package.json"
    config = read_json(path)
    bundles = config.setdefault("dsh", {}).setdefault("profile", {}).setdefault("bundles", [])
    if selected == ("dsh-fmage" in bundles):
        return
    if selected:
        bundles.append("dsh-fmage")
    else:
        bundles.remove("dsh-fmage")
    atomic_write(path, json_bytes(config))


def unload_deepseek_bundle(profile: Path, installed: Path, attempts: int = 3, per_attempt: float = 20) -> None:
    """Deselect the bundle until the running host has disposed it.

    HMR normally unloads within a couple of seconds, but a missed manifest
    notification leaves the old selection running. Cycling the selection emits
    a real bundle-list change each time, unlike rewriting the same deselection.
    """
    for attempt in range(1, attempts + 1):
        select_bundle(profile, False)
        if wait_for(lambda: runtime_receipt(installed) is None, per_attempt):
            return
        print(f"DeepSeek Desktop kept Fmage loaded after deselect (attempt {attempt}/{attempts}); cycling the bundle selection.")
        select_bundle(profile, True)
        wait_for(lambda: runtime_receipt(installed) is not None, per_attempt)
    raise RuntimeError(
        "DeepSeek Desktop did not unload Fmage after three deselections, so nothing was replaced; "
        f"quit the application and retry. Backup: {local_root() / 'backups'}")


def activate_deepseek_version(profile: Path, installed: Path, version: str, attempts: int = 2, per_attempt: float = 45) -> bool:
    """Wait for the running host to load the installed version, retriggering once if needed."""
    def active() -> bool:
        return (runtime_receipt(installed) or {}).get("version") == version

    if wait_for(active, per_attempt):
        return True
    for _ in range(attempts - 1):
        print("DeepSeek Desktop did not activate the installed version; cycling the bundle selection.")
        select_bundle(profile, False)
        wait_for(lambda: runtime_receipt(installed) is None, 20)
        select_bundle(profile, True)
        if wait_for(active, per_attempt):
            return True
    return False


def install_deepseek_hot(files: dict[str, bytes], profile: Path, runtime: DeepSeekRuntime, package: Path) -> str:
    """Replace a loaded plugin through the host's own hot-reload path.

    A running Desktop keeps the installed package open, so replacing the
    directory behind its back fails and its HMR ignores dependency-only
    manifest edits. Deselecting the bundle makes the host unload the plugin and
    disposes its MCP process, the CLI then installs and reselects it, and the
    host reloads the new files without a restart. Returns "hot" or "cold".
    """
    installed = profile / "node_modules/dsh-fmage"
    if runtime_receipt(installed) is None:
        return "cold"
    version = json.loads(files[".fmage-build.json"])["version"]
    rollback = local_root() / "backups" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    for name in ("package.json", "pnpm-lock.yaml", "cordis.patch.yml"):
        backup(profile / name, rollback)
    try:
        unload_deepseek_bundle(profile, installed)
        # The plugin's MCP child exits with its fiber; give the handle a moment to close.
        time.sleep(2)
        run_deepseek_pnpm(profile, runtime, package)
    finally:
        # Restore the selection even when the install failed, so Fmage keeps working;
        # the reselect is also what makes the running host reload the new files.
        try:
            select_bundle(profile, True)
        except (OSError, ValueError) as error:
            print(f"DeepSeek Desktop bundle reselection failed: {error}")
    if not activate_deepseek_version(profile, installed, version):
        raise RuntimeError(
            "DeepSeek Desktop installed the package but did not activate the new version; "
            f"quit the application and retry. Backup: {rollback}")
    failures = deepseek_installation_errors(files, profile, package)
    if failures:
        raise RuntimeError("; ".join(failures))
    return "hot"


def run_deepseek_pnpm(profile: Path, runtime: DeepSeekRuntime, package: Path) -> None:
    """Install the TGZ into the profile; `dsh plugin` forwards to pnpm and never selects bundles."""
    rollback = local_root() / "backups" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    for name in ("package.json", "pnpm-lock.yaml", "pnpm-workspace.yaml", "cordis.patch.yml"):
        backup(profile / name, rollback)
    # Source pnpm may need registry metadata for policy checks on existing
    # dependencies, even when the Fmage tarball is local. Do not disable those
    # checks or automatically retry a failed installation.
    cache_policy = "--prefer-offline" if runtime.kind == "source" else "--offline"
    result = subprocess.run([*runtime.cli, "plugin", "--profile", "desktop", "add",
                             "file:" + package.as_posix(), cache_policy, "--ignore-scripts"],
                            cwd=runtime.cwd, env=deepseek_environment(profile), check=False)
    if result.returncode:
        raise RuntimeError(f"DeepSeek Desktop TGZ installation failed (exit {result.returncode}); backup: {rollback}")


def install_deepseek(files: dict[str, bytes], profile: Path, runtime: DeepSeekRuntime, package: Path) -> None:
    if not deepseek_installation_errors(files, profile, package):
        return
    run_deepseek_pnpm(profile, runtime, package)
    failures = deepseek_installation_errors(files, profile, package)
    if failures:
        raise RuntimeError("; ".join(failures))


def verify_deepseek_loader(runtime: DeepSeekRuntime, profile: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="fmage-desktop-profile-") as temporary:
        environment = deepseek_environment(Path(temporary) / "profiles/desktop", probe=True, kind=runtime.kind)
        environment["FMAGE_CONFIG"] = str(Path(temporary) / "no-credentials.json")
        result = subprocess.run([
            *runtime.probe, str(ROOT / "tests/deepseek_profile_smoke.mjs"),
            str(runtime.anchor), str(profile), temporary,
        ], cwd=runtime.cwd, env=environment,
            capture_output=True, text=True, encoding="utf-8", timeout=45, check=False)
    if result.returncode or '"nativeProfileLoader":true' not in result.stdout:
        raise RuntimeError(f"DeepSeek native profile activation probe failed: {result.stderr or result.stdout}")
    print(f"DeepSeek {runtime.kind} isolated native profile activation verified: {len(SKILLS)} skills, {MCP_TOOL_COUNT} MCP tools (not the live desktop process)")
    graph_temp = Path(temporary) / "client-graph"
    graph = subprocess.run([
        *runtime.probe, str(ROOT / "tests/deepseek_client_graph.mjs"),
        str(runtime.anchor), str(profile), str(graph_temp),
    ], cwd=runtime.cwd, env=environment, capture_output=True, text=True, encoding="utf-8", timeout=45, check=False)
    if graph.returncode or '"clientModuleRow":"dsh-fmage"' not in graph.stdout:
        raise RuntimeError(f"DeepSeek settings page is not discovered by the client module graph: {graph.stderr or graph.stdout}")
    print("DeepSeek settings page verified: client module row dsh-fmage -> client.js")


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
        if loaded.get("skillCount") != len(SKILLS) or loaded.get("toolCount") != MCP_TOOL_COUNT or not loaded.get("mcpReady"):
            return "NOT ready: skill/MCP registration is incomplete"
        return f"live activation verified: {loaded['version']}, pid={loaded['pid']}, {len(SKILLS)} skills, {MCP_TOOL_COUNT} tools"
    except (OSError, ValueError, KeyError):
        return "NOT verified: invalid or legacy activation receipt"


def refresh_registered(*, check_only: bool = False) -> None:
    path = state_path()
    if not path.exists():
        return
    state = read_json(path)
    if Path(state["source"]).resolve() != ROOT.resolve():
        raise RuntimeError("Registered harness source differs from this repository.")
    if "vscode" in state["hosts"]:
        if check_only:
            print("Retired VS Code installation is still registered; rerun without --check to archive it.")
        else:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            for item in retire_vscode(state, local_root() / "backups" / stamp):
                print(f"Retired VS Code installation: {item}")
            atomic_write(path, json_bytes(state))
    # Resolve the selected runtime before writing payloads or invoking an installer.
    deepseek = deepseek_runtime(state["hosts"]["deepseek"]) if "deepseek" in state["hosts"] else None
    failures = []
    hot_verified = False
    for host, settings in state["hosts"].items():
        if host == "vscode":
            # Retired above; a check-only run reports it without failing the rest.
            continue
        if host != "deepseek":
            raise RuntimeError(f"Unsupported registered host: {host}")
        target = Path(settings["path"])
        files = payload()
        changes = sync_payload(files, target, check_only)
        if check_only and changes:
            failures.append(f"{host}: {len(changes)} outdated files")
        if not check_only:
            package = build_archive(files, Path(state["packages"]))
            print(f"{host} plugin synchronized: {target}; package: {package}")
        elif not changes:
            print(f"{host} plugin matches repository: {target}")
        verify_archive(files, Path(state["packages"]))
        profile = Path(settings["profile"])
        installed = profile / "node_modules/dsh-fmage"
        package = Path(state["packages"]) / f"dsh-fmage-{json.loads(files['.fmage-build.json'])['version']}.tgz"
        if not check_only:
            mode = install_deepseek_hot(files, profile, deepseek, package)
            hot_verified = hot_verified or mode == "hot"
            print("DeepSeek Desktop install path: " + (
                "hot reload through the running host (host half; client.js changes need an app restart)"
                if mode == "hot" else "offline CLI (host not loaded)"))
        problems = deepseek_installation_errors(files, profile, package)
        failures.extend(problems)
        print(f"DeepSeek Desktop physical installation checked: {installed}")
        if not problems:
            verify_deepseek_loader(deepseek, profile)
        print("DeepSeek Desktop runtime " + deepseek_runtime_status(installed, files))
    if failures:
        raise RuntimeError("; ".join(failures))
    if not check_only:
        print(
            "Host half hot-reloaded. If this build changed client.js, fully quit and reopen the Desktop app "
            "so the renderer loads the new client bundle; Electron does not use the Web SSE live path."
            if hot_verified else
            "Installed artifacts updated. Existing host processes may require plugin/window reload; no active requests were interrupted."
        )


def configure_installations(deepseek_cli: Path) -> None:
    runtime = deepseek_runtime({"cli": str(deepseek_cli)})
    target = local_root() / "deepseek/dsh-fmage"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    rollback = local_root() / "backups" / stamp
    sync_payload(payload(), target)
    profile = Path(os.environ.get("DSH_HOME", str(Path.home() / ".dsh"))) / "profiles/desktop"
    if not (profile / "package.json").is_file():
        raise RuntimeError("DeepSeek Desktop profile is not initialized; launch the desktop application first.")
    for name in ("package.json", "pnpm-lock.yaml", "pnpm-workspace.yaml", "cordis.patch.yml"):
        backup(profile / name, rollback)
    files = payload()
    package = build_archive(files, local_root() / "packages")
    install_deepseek(files, profile, runtime, package)
    profile_config = read_json(profile / "package.json")
    if "dsh-fmage" not in profile_config.get("dsh", {}).get("profile", {}).get("bundles", []):
        raise RuntimeError("DeepSeek package installed but its bundle is not enabled; legacy installs preserved.")
    atomic_write(state_path(), json_bytes({"source": str(ROOT), "packages": str(local_root() / "packages"), "hosts": {
        "deepseek": {"path": str(target), "cli": str(deepseek_cli), "profile": str(profile)},
    }}))
    for removed in clean_legacy(rollback):
        print(f"Legacy Fmage installation archived: {removed}")
    print(f"Rollback files: {rollback}")
    refresh_registered()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--configure", action="store_true", help="Install the private payload and migrate the old shared adapter")
    parser.add_argument("--deepseek-cli", type=Path)
    parser.add_argument("--register-deepseek-source", type=Path,
                        help="Bind an existing Desktop registration to its source repository; preserves the profile and does not install")
    parser.add_argument("--build", action="store_true", help="Export the Desktop package")
    parser.add_argument("--output", type=Path, default=local_root() / "packages")
    args = parser.parse_args()
    if args.register_deepseek_source:
        if args.check or args.configure or args.build or args.deepseek_cli:
            parser.error("--register-deepseek-source cannot be combined with check, configure, build, or deepseek-cli")
        register_deepseek_source(args.register_deepseek_source)
    elif args.configure:
        if args.check or not args.deepseek_cli or not args.deepseek_cli.is_file():
            parser.error("--configure requires the existing Desktop --deepseek-cli and cannot use --check")
        configure_installations(args.deepseek_cli)
    elif args.build:
        print(build_archive(payload(), args.output))
    else:
        refresh_registered(check_only=args.check)


if __name__ == "__main__":
    main()
