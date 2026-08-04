from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = (
    PLUGIN_ROOT / "skills" / "fmage-image-regression" / "scripts" / "degrade_image.py"
)
SERVER_PATH = PLUGIN_ROOT / "mcp" / "server.mjs"


def load_module():
    spec = importlib.util.spec_from_file_location("fmage_image_regression", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load image-regression script")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FmageImageRegressionTests(unittest.TestCase):
    def test_skill_requires_the_single_script_fast_path(self) -> None:
        skill = (
            PLUGIN_ROOT / "skills" / "fmage-image-regression" / "SKILL.md"
        ).read_text(encoding="utf-8")
        self.assertIn("scripts/degrade_image.py", skill)
        self.assertIn("exactly once", skill)
        self.assertIn("处理中。", skill)
        self.assertIn("parse it as JSON", skill)
        self.assertIn("Never expose the raw JSON", skill)
        self.assertIn("treat `output` as authoritative", skill)
        self.assertIn("inline image preview", skill)
        self.assertIn("In Pi/Pix", skill)
        self.assertIn("show the absolute path as code", skill)
        self.assertIn("Do not load the main `fmage` skill", skill)
        self.assertIn("call an MCP image tool", skill)
        self.assertNotIn("mcp__Fmage.regress_image", skill)
        self.assertNotIn("stdout verbatim", skill)
        self.assertLessEqual(len(skill.split()), 175)

    def test_target_size_never_enlarges_and_caps_total_pixels(self) -> None:
        module = load_module()
        self.assertEqual(module.target_size(800, 600), (800, 600))

        width, height = module.target_size(1600, 1200)
        self.assertLessEqual(width * height, module.MAX_PIXELS)
        self.assertAlmostEqual(width / height, 4 / 3, places=2)

    def test_cli_runs_one_pass_and_uses_shared_rgb_noise(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            source_path = temp_path / "source.png"
            configured_output_dir = temp_path / "configured-output"
            config_path = temp_path / "providers.json"
            Image.new("RGB", (1600, 1200), (80, 120, 160)).save(source_path)
            config_path.write_text(
                json.dumps(
                    {
                        "active_providers": ["test-provider"],
                        "output_dir": str(configured_output_dir),
                        "providers": {"test-provider": {}},
                    }
                ),
                encoding="utf-8",
            )

            environment = os.environ.copy()
            environment["FMAGE_CONFIG"] = str(config_path)

            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT_PATH),
                    "--input",
                    str(source_path),
                ],
                check=True,
                capture_output=True,
                text=True,
                env=environment,
            )
            result = json.loads(completed.stdout)
            output_path = Path(result["output"])

            self.assertTrue(result["resized"])
            self.assertLessEqual(result["output_size"][0] * result["output_size"][1], 1_048_576)
            self.assertTrue(os.path.samefile(Path(result["output_dir"]), configured_output_dir))
            self.assertTrue(os.path.samefile(output_path.parent, configured_output_dir))
            self.assertRegex(output_path.name, r"^\d{8}-\d{6}-001(?:-\d+)?\.png$")
            with Image.open(output_path) as output:
                self.assertEqual(list(output.size), result["output_size"])
                red, green, blue = output.getpixel((output.width // 2, output.height // 2))
                self.assertEqual(green - red, 40)
                self.assertEqual(blue - green, 40)

    def test_mcp_regress_image_is_a_single_typed_call(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            source_path = temp_path / "source.png"
            output_dir = temp_path / "final"
            config_path = temp_path / "providers.json"
            Image.new("RGB", (1200, 900), (80, 120, 160)).save(source_path)
            config_path.write_text(
                json.dumps(
                    {
                        "active_providers": ["test-provider"],
                        "output_dir": str(output_dir),
                        "providers": {"test-provider": {}},
                    }
                ),
                encoding="utf-8",
            )
            environment = {
                **os.environ,
                "FMAGE_CONFIG": str(config_path),
                "FMAGE_PYTHON": sys.executable,
            }
            request = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "regress_image",
                    "arguments": {"image": str(source_path)},
                },
            }
            completed = subprocess.run(
                ["node", str(SERVER_PATH)],
                input=json.dumps(request) + "\n",
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=environment,
            )
            response = json.loads(completed.stdout)
            result = response["result"]["structuredContent"]
            saved_path = Path(result["output"])

            self.assertTrue(os.path.samefile(saved_path.parent, output_dir))
            self.assertTrue(saved_path.is_file())
            self.assertEqual(result["display_images"], [saved_path.as_posix()])
            self.assertIn("处理完成", response["result"]["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
