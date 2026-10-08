from __future__ import annotations
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
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
    def test_runtime_status_requires_current_payload_and_fresh_heartbeat(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            files = hp.payload('deepseek')
            self.assertIn('NOT verified', hp.deepseek_runtime_status(root, files))
            receipt = {**json.loads(files['.fmage-build.json']), 'pid': 123,
                       'skillCount': 5, 'toolCount': 10, 'mcpReady': True,
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
