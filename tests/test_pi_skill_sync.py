from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path
import unittest


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SYNC_SCRIPT = PLUGIN_ROOT / "scripts" / "sync_pi_skills.py"


class PiSkillSyncTests(unittest.TestCase):
    def run_sync(self, target: Path, *extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SYNC_SCRIPT), "--target", str(target), *extra],
            text=True,
            capture_output=True,
            check=False,
        )

    def test_sync_renders_current_pi_adapter_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            target = Path(temporary_directory) / "skills"
            agents_file = target.parent / "AGENTS.md"
            agents_file.write_text(
                "## Clarification\n\nKeep this.\n\n"
                "## Image Generation And Editing\n\nOld Fmage rules.\n\n"
                "## Unrelated\n\nKeep this too.\n",
                encoding="utf-8",
            )
            result = self.run_sync(target)
            self.assertEqual(result.returncode, 0, result.stderr)

            main_skill = (target / "fmage" / "SKILL.md").read_text(encoding="utf-8")
            diagnostics = (target / "fmage" / "references" / "diagnostics.md").read_text(
                encoding="utf-8"
            )
            self.assertIn("Pi MCP Adapter's `mcp` bridge", main_skill)
            self.assertIn("`Fmage_generate_image`", main_skill)
            self.assertIn("`Fmage_prepare_prompt_dalle3`", main_skill)
            self.assertIn("including `medium`, always pass that exact `quality`", main_skill)
            self.assertIn("`Fmage_get_provider_status`", diagnostics)
            self.assertIn("include_provider_metadata", diagnostics)
            self.assertNotIn("Fmage_generate_image_ezai_image_2", main_skill)
            self.assertNotIn("provider-specific image tool", main_skill)

            agents = agents_file.read_text(encoding="utf-8")
            self.assertIn("Keep this.", agents)
            self.assertIn("Keep this too.", agents)
            self.assertIn("including `medium`, always pass that exact `quality`", agents)
            self.assertNotIn("Old Fmage rules.", agents)

            source_config = (PLUGIN_ROOT / "skills" / "fmage-config" / "SKILL.md").read_bytes()
            pi_config = (target / "fmage-config" / "SKILL.md").read_bytes()
            self.assertEqual(pi_config, source_config)

            check_result = self.run_sync(target, "--check")
            self.assertEqual(check_result.returncode, 0, check_result.stderr)

    def test_check_detects_pi_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            target = Path(temporary_directory) / "skills"
            self.assertEqual(self.run_sync(target).returncode, 0)
            config = target / "fmage-config" / "SKILL.md"
            config.write_text(config.read_text(encoding="utf-8") + "\nold copy\n", encoding="utf-8")

            result = self.run_sync(target, "--check")
            self.assertEqual(result.returncode, 1)
            self.assertIn("outdated: fmage-config/SKILL.md", result.stderr)


if __name__ == "__main__":
    unittest.main()
