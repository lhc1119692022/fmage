import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SYNC_SCRIPT = PLUGIN_ROOT / "scripts" / "sync_scnet_plugin.py"


class ScnetPluginSyncTests(unittest.TestCase):
    def run_sync(self, target: Path, *extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(SYNC_SCRIPT), "--target", str(target), *extra], capture_output=True, text=True)

    def test_sync_writes_native_manifest_skills_and_mcp(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "fmage"
            result = self.run_sync(target)
            self.assertEqual(result.returncode, 0, result.stderr)
            manifest = json.loads((target / ".scnet-plugin" / "plugin.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["name"], "fmage")
            self.assertIn("mcpServers", manifest)
            self.assertTrue((target / "skills" / "fmage" / "SKILL.md").is_file())
            self.assertIn("SCNet", (target / "skills" / "fmage" / "SKILL.md").read_text(encoding="utf-8"))
            self.assertEqual(self.run_sync(target, "--check").returncode, 0)


if __name__ == "__main__":
    unittest.main()
