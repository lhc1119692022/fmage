from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SYNC_SCRIPT = PLUGIN_ROOT / "scripts" / "sync_cursor_skills.py"


class CursorSkillSyncTests(unittest.TestCase):
    def run_sync(
        self,
        target: Path,
        mcp_config: Path,
        fmage_config: Path,
        *extra: str,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(SYNC_SCRIPT),
                "--target",
                str(target),
                "--mcp-config",
                str(mcp_config),
                "--fmage-config",
                str(fmage_config),
                *extra,
            ],
            text=True,
            capture_output=True,
            check=False,
        )

    def test_sync_renders_native_cursor_contract_and_merges_mcp(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            target = root / "skills"
            mcp_config = root / "mcp.json"
            fmage_config = root / "providers.json"
            mcp_config.write_text(
                json.dumps({"mcpServers": {"Existing": {"url": "https://example.test/mcp"}}}),
                encoding="utf-8",
            )

            result = self.run_sync(target, mcp_config, fmage_config)
            self.assertEqual(result.returncode, 0, result.stderr)

            main_skill = (target / "fmage" / "SKILL.md").read_text(encoding="utf-8")
            regression = (target / "fmage-image-regression" / "SKILL.md").read_text(
                encoding="utf-8"
            )
            self.assertIn("native MCP tools in Cursor", main_skill)
            self.assertIn("one concise understanding-and-expansion pass", main_skill)
            self.assertIn("Cursor session reasoning level", main_skill)
            self.assertIn("`generate_image`", main_skill)
            self.assertNotIn("Pi MCP Adapter", main_skill)
            self.assertNotIn("Codex/Pi session reasoning level", main_skill)
            self.assertIn("In Cursor", regression)
            self.assertNotIn("In Pi/Pix", regression)

            config = json.loads(mcp_config.read_text(encoding="utf-8"))
            self.assertIn("Existing", config["mcpServers"])
            fmage = config["mcpServers"]["Fmage"]
            self.assertEqual(fmage["command"], "node")
            self.assertEqual(fmage["args"], [str((PLUGIN_ROOT / "mcp" / "server.mjs").resolve())])
            self.assertEqual(fmage["env"]["FMAGE_CONFIG"], str(fmage_config.resolve()))

            for skill_name in ("fmage", "fmage-config", "fmage-image-regression"):
                skill = (target / skill_name / "SKILL.md").read_text(encoding="utf-8")
                self.assertIn(f"name: {skill_name}", skill)

            check_result = self.run_sync(target, mcp_config, fmage_config, "--check")
            self.assertEqual(check_result.returncode, 0, check_result.stderr)

    def test_check_detects_skill_and_mcp_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            target = root / "skills"
            mcp_config = root / "mcp.json"
            fmage_config = root / "providers.json"
            self.assertEqual(self.run_sync(target, mcp_config, fmage_config).returncode, 0)

            skill = target / "fmage-config" / "SKILL.md"
            skill.write_text(skill.read_text(encoding="utf-8") + "\nold copy\n", encoding="utf-8")
            config = json.loads(mcp_config.read_text(encoding="utf-8"))
            config["mcpServers"]["Fmage"]["args"] = ["old-server.mjs"]
            mcp_config.write_text(json.dumps(config), encoding="utf-8")

            result = self.run_sync(target, mcp_config, fmage_config, "--check")
            self.assertEqual(result.returncode, 1)
            self.assertIn("outdated: fmage-config/SKILL.md", result.stderr)
            self.assertIn("missing or outdated: mcp.json Fmage server", result.stderr)


if __name__ == "__main__":
    unittest.main()
