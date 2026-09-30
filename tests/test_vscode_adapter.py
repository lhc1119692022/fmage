from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

from install_vscode_adapter import install


class VscodeAdapterTests(unittest.TestCase):
    def test_install_syncs_skills_and_preserves_other_mcp_servers(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            skills_dir = root / "skills"
            mcp_config = root / "mcp.json"
            mcp_config.write_text(json.dumps({"servers": {"Other": {"command": "other"}}}), encoding="utf-8")
            changes = install(skills_dir, mcp_config, check_only=False)
            self.assertTrue(changes)
            skill_text = (skills_dir / "fmage" / "SKILL.md").read_text(encoding="utf-8")
            self.assertIn("Fmage.", skill_text)
            self.assertNotIn("mcp__Fmage.", skill_text)
            payload = json.loads(mcp_config.read_text(encoding="utf-8"))
            self.assertEqual(payload["servers"]["Other"], {"command": "other"})
            self.assertEqual(payload["servers"]["Fmage"]["command"], "node")
            self.assertEqual(install(skills_dir, mcp_config, check_only=True), [])

    def test_check_does_not_write(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            skills_dir = root / "skills"
            mcp_config = root / "mcp.json"
            changes = install(skills_dir, mcp_config, check_only=True)
            self.assertTrue(changes)
            self.assertFalse(skills_dir.exists())
            self.assertFalse(mcp_config.exists())


if __name__ == "__main__":
    unittest.main()