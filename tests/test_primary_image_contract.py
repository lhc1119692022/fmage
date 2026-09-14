from __future__ import annotations

import importlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

from PIL import Image

from test_prompt_policy import call_server, provider, provider_config

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))


class PrimaryImageContractTests(unittest.TestCase):
    def test_all_edit_transports_accept_and_use_primary_image(self) -> None:
        cases = (
            ("gemini_generate_content_transport", "gemini-generate-content", "gemini-3.1-flash-image", None),
            ("ezai_banana_transport", "ezai-banana-images", "nano-banana-2", None),
            ("openai_images_transport", "openai-images", "gpt-image-2.5", None),
            ("openai_images_808_transport", "openai-images", "gpt-image-2.5", "808"),
        )
        with tempfile.TemporaryDirectory() as directory:
            images = [str(Path(directory) / "square.png"), str(Path(directory) / "wide.png")]
            Image.new("RGB", (32, 32)).save(images[0])
            Image.new("RGB", (64, 36)).save(images[1])
            for module_name, transport, model, profile in cases:
                module = importlib.import_module(module_name)
                for index in (0, 1, -1, 2):
                    with self.subTest(transport=transport, profile=profile, index=index):
                        args = module.build_parser().parse_args([
                            "edit", "--base-url", "https://example.invalid/v1", "--model", model,
                            "--prompt", "test", "--image", images[0], "--image", images[1],
                            "--primary-image-index", str(index), "--resolution", "2k",
                            "--output-dir", directory, "--dry-run",
                        ])
                        run = lambda: module.run_request(args, images) if module_name == "gemini_generate_content_transport" else module.run_edit(args)
                        if index in (-1, 2):
                            with self.assertRaisesRegex(ValueError, "primary_image_index"):
                                run()
                            continue
                        result = run()
                        if transport == "openai-images":
                            width, height = map(int, result["request"]["size"].split("x"))
                            self.assertEqual(width == height, index == 0)
                            self.assertEqual(result["images"], images)
                        else:
                            aspect = result.get("requested_aspect_ratio") or result.get("requested_size", "")
                            self.assertIn("1:1" if index == 0 else "16:9", aspect)
                        self.assertEqual(args.image, images)
                configured = provider(model, transport)
                if profile:
                    configured["transport_profile"] = profile
                config = provider_config(["test"], {"test": configured})
                for tool in ("edit_image", "edit_image_batch"):
                    with self.subTest(tool=tool, transport=transport, profile=profile):
                        job = {"prompt": "test", "images": images, "primary_image_index": 0}
                        arguments = {"dry_run": True, "verbose": True, "resolution": "4k", "resolution_user_requested": True, "quality": "high"}
                        arguments.update({"jobs": [job]} if tool.endswith("_batch") else job)
                        response = call_server(config, "tools/call", {"name": tool, "arguments": arguments})
                        self.assertNotIn("error", response, json.dumps(response))
                        self.assertFalse(response.get("result", {}).get("isError"), json.dumps(response))


if __name__ == "__main__":
    unittest.main()
