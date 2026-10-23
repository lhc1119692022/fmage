from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import refresh_local_runtime as rlr


class MarketplaceResolutionTests(unittest.TestCase):
    def test_uses_the_installed_fmage_record(self):
        listing = {
            "installed": [
                {"name": "documents", "marketplaceName": "openai-primary-runtime"},
                {"name": "fmage", "pluginId": "fmage@personal", "marketplaceName": "personal"},
            ]
        }
        with patch.object(rlr, "plugin_list", return_value=listing):
            self.assertEqual(rlr.resolve_marketplace(Path("codex.exe")), "personal")

    def test_defaults_before_the_first_install(self):
        with patch.object(rlr, "plugin_list", return_value={"installed": []}):
            self.assertEqual(rlr.resolve_marketplace(Path("codex.exe")), rlr.DEFAULT_MARKETPLACE)

    def test_ignores_a_blank_marketplace_name(self):
        listing = {"installed": [{"name": "fmage", "marketplaceName": "  "}]}
        with patch.object(rlr, "plugin_list", return_value=listing):
            self.assertEqual(rlr.resolve_marketplace(Path("codex.exe")), rlr.DEFAULT_MARKETPLACE)


class CachebusterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / ".codex-plugin").mkdir()
        self.manifest = self.root / ".codex-plugin" / "plugin.json"
        self.write({"name": "fmage", "version": "0.1.0+codex.20260101000000", "mcpServers": "./.mcp.json"})
        patcher = patch.object(rlr, "PLUGIN_ROOT", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, payload):
        self.manifest.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
        )

    def read(self):
        return json.loads(self.manifest.read_text(encoding="utf-8"))

    def test_keeps_the_base_version_and_the_other_fields(self):
        rlr.bump_cachebuster("20261023010101")
        payload = self.read()
        self.assertEqual(payload["version"], "0.1.0+codex.20261023010101")
        self.assertEqual(payload["name"], "fmage")
        self.assertEqual(payload["mcpServers"], "./.mcp.json")

    def test_writes_lf_endings(self):
        rlr.bump_cachebuster("20261023010101")
        data = self.manifest.read_bytes()
        self.assertNotIn(b"\r\n", data)
        self.assertTrue(data.endswith(b"\n"))

    def test_default_stamp_is_a_fresh_fourteen_digit_stamp(self):
        before = self.read()["version"].rsplit(".", 1)[-1]
        rlr.bump_cachebuster()
        after = self.read()["version"].rsplit(".", 1)[-1]
        self.assertEqual(len(after), 14)
        self.assertTrue(after.isdigit())
        self.assertGreater(after, before)

    def test_rejects_a_manifest_without_a_version(self):
        self.write({"name": "fmage"})
        with self.assertRaisesRegex(RuntimeError, "no usable version"):
            rlr.bump_cachebuster()


if __name__ == "__main__":
    unittest.main()
