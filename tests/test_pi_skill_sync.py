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
                "## Pix | Local Artifact Display\n\nOld Pix display rules.\n\n"
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
            self.assertIn("`display_images` path as the Markdown preview", main_skill)
            self.assertIn("render every `display_images[]` Markdown preview first", main_skill)
            self.assertIn("dirname(images[])", main_skill)
            self.assertIn("dirname(display_manifests[] / display_manifest / manifest)", main_skill)
            self.assertIn("生成质量/分辨率：low / 1k", main_skill)
            self.assertIn("omit both display fields rather than splitting them", main_skill)
            self.assertIn("A project workspace is optional", main_skill)
            self.assertIn("D%3A/Downloads/image%20name.png", main_skill)
            self.assertIn("omit `output_dir`", main_skill)
            self.assertIn("Pix's project-less conversation-storage path", main_skill)
            self.assertNotIn("display_output_dir", main_skill)
            self.assertIn("`Fmage_prepare_prompt_dalle3`", main_skill)
            self.assertIn("including `medium`, always pass that exact `quality`", main_skill)
            self.assertIn("set `resolution_user_requested: true`", main_skill)
            self.assertIn("`thinking_level_user_requested: true`", main_skill)
            self.assertIn("`Fmage_get_provider_status`", diagnostics)
            self.assertIn("include_provider_metadata", diagnostics)
            self.assertNotIn("Fmage_generate_image_ezai_image_2", main_skill)
            self.assertNotIn("provider-specific image tool", main_skill)

            agents = agents_file.read_text(encoding="utf-8")
            self.assertIn("Keep this.", agents)
            self.assertIn("Keep this too.", agents)
            self.assertIn("including `medium`, always pass that exact `quality`", agents)
            self.assertIn("with `resolution_user_requested: true`", agents)
            self.assertIn("with `thinking_level_user_requested: true`", agents)
            self.assertIn("A project workspace is optional", agents)
            self.assertIn("display_manifests[]", agents)
            self.assertIn("Never put the parameter bullets before the preview", agents)
            self.assertIn("dirname(images[])", agents)
            self.assertIn("dirname(display_manifests[] / display_manifest / manifest)", agents)
            self.assertIn("生成质量/分辨率：low / 1k", agents)
            self.assertIn("conversation-storage directory", agents)
            self.assertIn("omit `output_dir`", agents)
            self.assertIn("Pix's project-less conversation-storage path", agents)
            self.assertIn("D%3A/Downloads/image%20name.png", agents)
            self.assertNotIn("display_output_dir", agents)
            self.assertNotIn("Old Pix display rules.", agents)
            self.assertNotIn("Old Fmage rules.", agents)

            regression = (target / "fmage-image-regression" / "SKILL.md").read_text(encoding="utf-8")
            self.assertIn("use the project workspace when one exists", regression)
            self.assertIn("never resolve a path relative to the conversation directory", regression)
            self.assertIn("preview first", regression)
            self.assertIn("dirname(output)", regression)
            self.assertNotIn("[Open image]", regression)

            source_config = (PLUGIN_ROOT / "skills" / "fmage-config" / "SKILL.md").read_bytes()
            pi_config = (target / "fmage-config" / "SKILL.md").read_bytes()
            self.assertEqual(pi_config, source_config)

            self.assertFalse((target.parent / "mcp.json").exists())

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
