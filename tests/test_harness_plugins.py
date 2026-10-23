from __future__ import annotations
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import harness_plugins as hp
from refresh_local_runtime import verify_mcp_handshake


class HarnessPluginsTests(unittest.TestCase):
    def source_runtime_fixture(self, root):
        source = root / "source checkout"
        for name, data in {
            "apps/cli/src/bin.ts": b"// source CLI",
            "apps/cli/package.json": hp.json_bytes({"name": "@deepseek-ai/dsh"}),
            "node_modules/tsx/package.json": b"{}",
            "package.json": hp.json_bytes({"packageManager": "pnpm@11.28.5"}),
            "node_modules/pnpm/package.json": hp.json_bytes({"name": "pnpm", "version": "11.28.5"}),
            "node_modules/pnpm/bin/pnpm.cjs": b"// pinned pnpm",
        }.items():
            hp.atomic_write(source / name, data)
        return source

    def test_register_source_preserves_profile_and_other_hosts_with_rollback(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = self.source_runtime_fixture(root)
            profile = root / "home/profiles/desktop"
            hp.atomic_write(profile / "package.json", b'{"private":true}')
            state_path = root / "harness-installations.json"
            state = {"source": str(hp.ROOT), "packages": "unchanged-packages", "hosts": {
                "vscode": {"path": "unchanged-vscode"},
                "deepseek": {"path": "unchanged-staging", "cli": "removed-desktop/dsh.cmd", "profile": str(profile)},
            }}
            hp.atomic_write(state_path, hp.json_bytes(state))
            with patch.object(hp, "state_path", return_value=state_path), \
                 patch.object(hp, "local_root", return_value=root), \
                 patch.object(hp.shutil, "which", return_value="node.exe"):
                hp.register_deepseek_source(source)
                updated = hp.read_json(state_path)
                self.assertEqual(updated["hosts"]["vscode"], state["hosts"]["vscode"])
                self.assertEqual(updated["packages"], state["packages"])
                self.assertEqual(updated["hosts"]["deepseek"], {
                    "path": "unchanged-staging", "profile": str(profile),
                    "runtime": {"kind": "source", "root": str(source.resolve())},
                })
                backups = list((root / "backups").rglob("*harness-installations.json"))
                self.assertEqual(len(backups), 1)
                self.assertEqual(hp.read_json(backups[0]), state)
                hp.register_deepseek_source(source)
                self.assertEqual(list((root / "backups").rglob("*harness-installations.json")), backups)
                with self.assertRaisesRegex(RuntimeError, "dependency is missing"):
                    hp.register_deepseek_source(root / "missing")
                self.assertEqual(hp.read_json(state_path), updated)
                self.assertEqual((profile / "package.json").read_bytes(), b'{"private":true}')

    def test_source_installer_uses_selected_cli_cwd_and_desktop_home(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = self.source_runtime_fixture(root)
            with patch.object(hp.shutil, "which", return_value="node.exe"):
                runtime = hp.deepseek_runtime({"runtime": {"kind": "source", "root": str(source)}})
            profile = root / "selected-home/profiles/desktop"
            package = root / "fmage.tgz"
            with patch.object(hp, "deepseek_installation_errors", side_effect=[["outdated"], []]), \
                 patch.object(hp, "local_root", return_value=root), \
                 patch.dict(hp.os.environ, {"DSH_HOME": "wrong-home", "ELECTRON_RUN_AS_NODE": "1"}), \
                 patch.object(hp.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run:
                hp.install_deepseek({}, profile, runtime, package)
            argv = run.call_args.args[0]
            self.assertEqual(argv[:5], ["node.exe", "--import", "tsx/esm", str(hp.ROOT / "scripts/deepseek_source_cli.mjs"), str(source)])
            self.assertEqual(argv[5:], ["plugin", "--profile", "desktop", "add", "file:" + package.as_posix(), "--prefer-offline", "--ignore-scripts"])
            self.assertEqual(run.call_args.kwargs["cwd"], source)
            self.assertEqual(run.call_args.kwargs["env"]["DSH_HOME"], str((root / "selected-home").resolve()))
            self.assertNotIn("ELECTRON_RUN_AS_NODE", run.call_args.kwargs["env"])
            with self.assertRaisesRegex(RuntimeError, "profiles/desktop"):
                hp.deepseek_environment(root / "selected-home/profiles/web")
            hp.atomic_write(source / "node_modules/pnpm/package.json", hp.json_bytes({"name": "pnpm", "version": "12.10.1"}))
            with self.assertRaisesRegex(RuntimeError, "packageManager pin"):
                hp.deepseek_runtime({"runtime": {"kind": "source", "root": str(source)}})

    def test_source_probe_uses_cli_anchor_and_a_keyless_isolated_home(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = self.source_runtime_fixture(root)
            with patch.object(hp.shutil, "which", return_value="node.exe"):
                runtime = hp.deepseek_runtime({"runtime": {"kind": "source", "root": str(source)}})
            profile = root / "real-home/profiles/desktop"
            calls = []

            def fake_run(argv, **kwargs):
                calls.append((argv, kwargs))
                output = (
                    '{"clientModuleRow":"dsh-fmage","inject":[],"bundle":"client.js"}'
                    if "deepseek_client_graph.mjs" in argv[3] else '{"nativeProfileLoader":true}')
                return subprocess.CompletedProcess([], 0, output, "")

            with patch.dict(hp.os.environ, {"FMAGE_CONFIG": "real-providers.json", "ELECTRON_RUN_AS_NODE": "1"}), \
                 patch.object(hp.subprocess, "run", side_effect=fake_run):
                hp.verify_deepseek_loader(runtime, profile)
            argv, kwargs = calls[0]
            self.assertEqual(argv[:3], ["node.exe", "--import", "tsx/esm"])
            self.assertEqual(argv[4], str(source / "apps/cli/package.json"))
            self.assertEqual(argv[5], str(profile))
            environment = kwargs["env"]
            self.assertEqual(environment["DSH_HOME"], argv[6])
            self.assertEqual(environment["FMAGE_CONFIG"], str(Path(argv[6]) / "no-credentials.json"))
            self.assertNotIn("ELECTRON_RUN_AS_NODE", environment)
            self.assertEqual(kwargs["cwd"], source)
            # The settings-page probe reuses the same anchor/profile with its own empty directory.
            graph, graph_kwargs = calls[1]
            self.assertTrue(graph[3].endswith("deepseek_client_graph.mjs"))
            self.assertEqual(graph[4], argv[4])
            self.assertEqual(graph[5], str(profile))
            self.assertEqual(graph[6], str(Path(argv[6]) / "client-graph"))
            self.assertEqual(graph_kwargs["env"], environment)

    def test_electron_registration_remains_supported_and_missing_cli_never_falls_back(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cli = root / "resources/runtime/cli/bin/dsh.cmd"
            for path in (cli, root / "DeepSeek Harness.exe", root / "resources/app.asar"):
                hp.atomic_write(path, b"fixture")
            runtime = hp.deepseek_runtime({"cli": str(cli)})
            self.assertEqual(runtime.kind, "electron")
            self.assertEqual(runtime.cli, [str(cli)])
            self.assertEqual(runtime.anchor, root / "resources/app.asar/dsh/node_modules/@deepseek-ai/dsh/package.json")
            with self.assertRaisesRegex(RuntimeError, "CLI is missing"):
                hp.deepseek_runtime({"cli": str(root / "missing.cmd")})
            with self.assertRaisesRegex(RuntimeError, "kind=source"):
                hp.deepseek_runtime({"cli": str(cli), "runtime": {"kind": "unknown"}})

    def test_runtime_status_requires_current_payload_and_fresh_heartbeat(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            files = hp.payload('deepseek')
            self.assertIn('NOT verified', hp.deepseek_runtime_status(root, files))
            receipt = {**json.loads(files['.fmage-build.json']), 'pid': 123,
                       'skillCount': 5, 'toolCount': hp.MCP_TOOL_COUNT, 'mcpReady': True,
                       'heartbeatAt': datetime.now(timezone.utc).isoformat()}
            hp.atomic_write(root / '.fmage-runtime.json', hp.json_bytes(receipt))
            self.assertIn('live activation verified', hp.deepseek_runtime_status(root, files))
            receipt['sha256'] = 'previous payload'
            hp.atomic_write(root / '.fmage-runtime.json', hp.json_bytes(receipt))
            self.assertIn('RELOAD REQUIRED', hp.deepseek_runtime_status(root, files))
            receipt['heartbeatAt'] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
            hp.atomic_write(root / '.fmage-runtime.json', hp.json_bytes(receipt))
            self.assertIn('stale', hp.deepseek_runtime_status(root, files))

    def test_deepseek_rejects_link_registration_and_checks_installed_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            profile = Path(temp) / 'desktop'
            files = hp.payload('deepseek')
            installed = profile / 'node_modules/dsh-fmage'
            hp.sync_payload(files, installed)
            package = Path(temp) / 'fmage.tgz'
            manifest = {'dependencies': {'dsh-fmage': 'link:/staging'},
                        'dsh': {'profile': {'bundles': ['dsh-fmage']}}}
            hp.atomic_write(profile / 'package.json', hp.json_bytes(manifest))
            self.assertTrue(hp.deepseek_installation_errors(files, profile, package))
            manifest['dependencies']['dsh-fmage'] = 'file:' + package.as_posix()
            hp.atomic_write(profile / 'package.json', hp.json_bytes(manifest))
            self.assertEqual(hp.deepseek_installation_errors(files, profile, package), [])
            (installed / 'index.mjs').write_text('stale', encoding='utf-8')
            self.assertTrue(hp.deepseek_installation_errors(files, profile, package))

    def test_hot_unload_cycles_the_bundle_selection_when_a_notification_is_missed(self):
        with tempfile.TemporaryDirectory() as temp:
            profile = Path(temp)
            installed = profile / "node_modules/dsh-fmage"
            selection: list[bool] = []
            deselects = {"count": 0}
            loaded = {"value": True}

            def select_bundle(_profile, selected):
                selection.append(selected)
                if selected:
                    loaded["value"] = True
                    return
                deselects["count"] += 1
                # The first deselect is missed; only a fresh list change unloads.
                loaded["value"] = deselects["count"] < 2

            with patch.object(hp, "select_bundle", side_effect=select_bundle), \
                 patch.object(hp, "wait_for", side_effect=lambda predicate, timeout, interval=0.5: predicate()), \
                 patch.object(hp, "runtime_receipt", side_effect=lambda _i: {"version": "old"} if loaded["value"] else None), \
                 patch("builtins.print"):
                hp.unload_deepseek_bundle(profile, installed)
            self.assertEqual(selection, [False, True, False])

            loaded["value"] = True
            with patch.object(hp, "select_bundle", side_effect=lambda _p, _s: None), \
                 patch.object(hp, "wait_for", side_effect=lambda predicate, timeout, interval=0.5: predicate()), \
                 patch.object(hp, "runtime_receipt", side_effect=lambda _i: {"version": "old"}), \
                 patch("builtins.print"):
                with self.assertRaisesRegex(RuntimeError, "did not unload Fmage after three deselections"):
                    hp.unload_deepseek_bundle(profile, installed)

    def test_hot_activation_retries_once_before_reporting_a_stale_host(self):
        with tempfile.TemporaryDirectory() as temp:
            profile = Path(temp)
            installed = profile / "node_modules/dsh-fmage"
            selection: list[bool] = []
            loaded = {"version": "old"}

            def select_bundle(_profile, selected):
                selection.append(selected)
                loaded["version"] = "old"
                if selected:
                    loaded["version"] = "new"

            with patch.object(hp, "select_bundle", side_effect=select_bundle), \
                 patch.object(hp, "wait_for", side_effect=lambda predicate, timeout, interval=0.5: predicate()), \
                 patch.object(hp, "runtime_receipt", side_effect=lambda _i: {"version": loaded["version"]}), \
                 patch("builtins.print"):
                self.assertTrue(hp.activate_deepseek_version(profile, installed, "new"))
            self.assertEqual(selection, [False, True])

            loaded["version"] = "old"
            with patch.object(hp, "select_bundle", side_effect=lambda _p, _s: None), \
                 patch.object(hp, "wait_for", side_effect=lambda predicate, timeout, interval=0.5: predicate()), \
                 patch.object(hp, "runtime_receipt", side_effect=lambda _i: {"version": "old"}), \
                 patch("builtins.print"):
                self.assertFalse(hp.activate_deepseek_version(profile, installed, "new"))

    def test_private_skill_invocation_and_descriptions(self):
        for host in ("vscode", "deepseek"):
            files = hp.payload(host)
            for name in hp.SKILLS:
                text = files[f"skills/{name}/SKILL.md"].decode()
                self.assertTrue(text.startswith(f"---\nname: {name}\n"))
                self.assertIn(hp.DESCRIPTIONS[name], text)
                self.assertIn(f"disable-model-invocation: {'true' if name == 'fmage-direct' else 'false'}", text)
                self.assertNotIn("$fmage", text)
                self.assertNotIn("mcp__Fmage.", text)
            regression = files["skills/fmage-image-regression/SKILL.md"].decode()
            self.assertIn("regress_image", regression)
            self.assertNotIn("degrade_image.py", regression)
            self.assertNotIn("agents/openai.yaml", "\n".join(files))

    def test_archive_allowlist_and_host_formats(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            for host in ("vscode", "deepseek"):
                files = hp.payload(host)
                self.assertNotIn("config/providers.json", files)
                self.assertNotIn("scripts/harness_plugins.py", files)
                self.assertNotIn("scripts/install_vscode_adapter.py", files)
                self.assertIn("config/gpt-image-model-families.json", files)
                archive = hp.build_archive(host, files, out)
                hp.verify_archive(host, files, out)
                if host == "vscode":
                    with zipfile.ZipFile(archive) as z:
                        self.assertEqual(set(z.namelist()), {"fmage/" + n for n in files})
                        self.assertIn("fmage/.plugin/plugin.json", z.namelist())
                else:
                    with tarfile.open(archive) as z:
                        self.assertEqual(set(z.getnames()), {"package/" + n for n in files})
                    self.assertFalse(json.loads(files["catalog.json"])[1]["modelInvocable"])
                changed = dict(files)
                changed["skills/fmage/SKILL.md"] = b"stale"
                with self.assertRaises(RuntimeError):
                    hp.verify_archive(host, changed, out)

    def test_sync_is_read_only_when_checking_and_detects_drift(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"
            files = hp.payload("vscode")
            self.assertTrue(hp.sync_payload(files, root, True))
            self.assertFalse(root.exists())
            hp.sync_payload(files, root)
            self.assertEqual(hp.sync_payload(files, root, True), [])
            (root / "skills/fmage/SKILL.md").write_text("outdated", encoding="utf-8")
            self.assertIn("skills/fmage/SKILL.md", hp.sync_payload(files, root, True))

    def test_sync_removes_files_the_payload_stops_shipping(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"
            retired = "scripts/retired_transport.py"
            first = hp.payload("vscode")
            first[retired] = b"print('retired')\n"
            marker = json.loads(first[".fmage-build.json"])
            marker["files"] = sorted(first)
            first[".fmage-build.json"] = hp.json_bytes(marker)
            hp.sync_payload(first, root)
            self.assertTrue((root / retired).is_file())
            # The next build no longer ships it: check reports it, sync deletes it.
            second = hp.payload("vscode")
            self.assertIn(retired, hp.sync_payload(second, root, True))
            hp.sync_payload(second, root)
            self.assertFalse((root / retired).is_file())
            self.assertEqual(hp.sync_payload(second, root, True), [])

    def test_sync_drops_stray_scripts_from_a_legacy_marker(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"
            legacy = hp.payload("vscode")
            marker = json.loads(legacy[".fmage-build.json"])
            del marker["files"]
            legacy[".fmage-build.json"] = hp.json_bytes(marker)
            hp.sync_payload(legacy, root)
            stray = root / "scripts/json_images_transport.py"
            stray.write_text("retired", encoding="utf-8")
            self.assertIn("scripts/json_images_transport.py", hp.sync_payload(legacy, root, True))
            hp.sync_payload(legacy, root)
            self.assertFalse(stray.is_file())

    def test_jsonc_changes_preserve_unrelated_comments_strings_and_trailing_comma(self):
        cases = [
            '{\n // keep this\n "url": "https://host/,}",\n "arr": [1, 2,],\n}',
            '{ "other": 1 // keep ending comment\n}',
            '{ /* empty object comment */ }',
            '{"chat.pluginLocations": {"old":true}, "keep": "//literal"}',
        ]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "settings.json"
            for text in cases:
                path.write_text(text, encoding="utf-8")
                original = json.loads(hp.jsonc_clean(text))
                hp.set_jsonc_fields(path, {"chat.pluginLocations": {"private": True}})
                result = path.read_text(encoding="utf-8")
                expected = {**original, "chat.pluginLocations": {"private": True}}
                self.assertEqual(json.loads(hp.jsonc_clean(result)), expected)
                for comment in ("// keep this", "// keep ending comment", "/* empty object comment */"):
                    if comment in text:
                        self.assertIn(comment, result)
                hp.set_jsonc_fields(path, {"chat.pluginLocations": {"private": True}})
                self.assertEqual(path.read_text(encoding="utf-8"), result)

    def test_cleanup_preserves_other_skills_servers_and_backups(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            shared = home / ".agents/skills"
            (shared / "other").mkdir(parents=True)
            for name in hp.SKILLS:
                (shared / name).mkdir()
                (shared / name / "SKILL.md").write_text("# Fmage VS Code adapter\nold", encoding="utf-8")
            mcp = home / "Code/User/mcp.json"
            mcp.parent.mkdir(parents=True)
            mcp.write_text(json.dumps({"servers": {"Fmage": {"args": [str(hp.ROOT / "mcp/server.mjs")]}, "Other": {"command": "other"}}}), encoding="utf-8")
            with patch.object(hp.Path, "home", return_value=home), patch.dict(hp.os.environ, {"APPDATA": str(home)}):
                changes = hp.clean_legacy(home / "backups")
            self.assertEqual(len(changes), 6)
            self.assertTrue((shared / "other").exists())
            self.assertTrue((home / "backups/shared-skills/fmage/SKILL.md").exists())
            self.assertEqual(json.loads(mcp.read_text())["servers"], {"Other": {"command": "other"}})

    def test_both_packaged_mcp_launchers_initialize_without_keys(self):
        with tempfile.TemporaryDirectory() as temp:
            for host in ("vscode", "deepseek"):
                root = Path(temp) / host
                hp.sync_payload(hp.payload(host), root)
                verify_mcp_handshake(root / "mcp/launch.mjs")


if __name__ == "__main__":
    unittest.main()
