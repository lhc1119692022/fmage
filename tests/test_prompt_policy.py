from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = PLUGIN_ROOT / "mcp" / "server.mjs"
STANDARD_IMAGE_TOOLS = {
    "generate_image",
    "generate_image_batch",
    "edit_image",
    "edit_image_batch",
}
BASELINE_TOOL_NAMES = STANDARD_IMAGE_TOOLS | {
    "workflow_image",
    "regress_image",
    "trace_image_job_plan",
    "probe_image_generation",
    "get_image_task_status",
    "get_provider_status",
}
BASELINE_INSTRUCTIONS = (
    "For Fmage 工作流 or configured workflow requests, use fmage-workflow and workflow_image; the following generation prompt rules apply only to ordinary generation/edit entries. "
    "Select the image skill before interpreting the request or inspecting attachments. Use fmage by default; "
    "use fmage-direct only when the user explicitly invokes Fmage 直传生图 for this request. "
    "Quoted command text and earlier invocations do not select an entry. Read only the selected skill and its own references, never both image policies. "
    "Follow that skill's complete prompt policy, then call the matching base image tool immediately. "
    "Set prompt_mode to expand for fmage or direct for fmage-direct. This records the entry already selected, not a later prompt-processing step. "
    "The selection is request-local; polling follows the submitted task without changing its mode. "
    "Do not create a separate plan, preparation pass, trace/status preflight, or repeated rewrite."
)
TRANSPARENT_TEST_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9WlD7xkAAAAASUVORK5CYII="
)


def provider(model: str = "gpt-image-2", transport: str = "openai-images") -> dict[str, object]:
    return {
        "transport": transport,
        "base_url": "https://example.invalid/v1",
        "model": model,
        "api_key": "",
    }


def provider_config(
    active_providers: list[str],
    providers: dict[str, dict[str, object]],
) -> dict[str, object]:
    return {
        "active_providers": active_providers,
        "output_dir": "outputs",
        "cache_dir": "cache",
        "providers": providers,
    }


class ServerSession:
    def __init__(self, config: dict[str, object]) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        config_path = Path(self.temporary_directory.name) / "providers.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        environment = {
            **os.environ,
            "FMAGE_CONFIG": str(config_path),
            "FMAGE_PYTHON": sys.executable,
        }
        self.process = subprocess.Popen(
            ["node", str(SERVER_PATH)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            env=environment,
        )
        self.next_id = 1

    def call(self, method: str, params: dict[str, object]) -> dict[str, object]:
        if self.process.stdin is None or self.process.stdout is None:
            raise AssertionError("Fmage test server pipes are unavailable")
        request_id = self.next_id
        self.next_id += 1
        message = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        if not line:
            stderr = self.process.stderr.read() if self.process.stderr is not None else ""
            raise AssertionError(stderr or "Fmage test server exited without a response")
        response = json.loads(line)
        if response.get("id") != request_id:
            raise AssertionError(f"Expected response id {request_id}, got: {response!r}")
        return response

    def close(self) -> None:
        if self.process.stdin is not None:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
        if self.process.stdout is not None:
            self.process.stdout.close()
        if self.process.stderr is not None:
            self.process.stderr.close()
        self.temporary_directory.cleanup()


_SERVER_SESSIONS: dict[str, ServerSession] = {}


def call_server(
    config: dict[str, object],
    method: str,
    params: dict[str, object],
) -> dict[str, object]:
    key = json.dumps(config, sort_keys=True)
    session = _SERVER_SESSIONS.get(key)
    if session is None:
        session = ServerSession(config)
        _SERVER_SESSIONS[key] = session
    return session.call(method, params)


def tearDownModule() -> None:
    for session in _SERVER_SESSIONS.values():
        session.close()
    _SERVER_SESSIONS.clear()


def tool_map(response: dict[str, object]) -> dict[str, dict[str, object]]:
    tools = response["result"]["tools"]
    return {tool["name"]: tool for tool in tools}


def all_property_names(value: object) -> set[str]:
    names: set[str] = set()
    if isinstance(value, dict):
        properties = value.get("properties")
        if isinstance(properties, dict):
            names.update(properties)
        for child in value.values():
            names.update(all_property_names(child))
    elif isinstance(value, list):
        for child in value:
            names.update(all_property_names(child))
    return names


class PromptPolicyIsolationTests(unittest.TestCase):
    def clean_config(self) -> dict[str, object]:
        return provider_config(["image-2"], {"image-2": provider()})

    def test_image_entry_skills_keep_separate_policy_contracts(self) -> None:
        default_skill = (PLUGIN_ROOT / "skills" / "fmage" / "SKILL.md").read_text(encoding="utf-8")
        direct_skill = (PLUGIN_ROOT / "skills" / "fmage-direct" / "SKILL.md").read_text(encoding="utf-8")
        direct_metadata = (PLUGIN_ROOT / "skills" / "fmage-direct" / "agents" / "openai.yaml").read_text(encoding="utf-8")
        self.assertIn("one concise understanding-and-expansion pass", default_skill)
        self.assertNotIn("Do not add, translate, summarize, explain, polish, paraphrase, or otherwise rewrite", default_skill)
        self.assertIn("Do not add, translate, summarize, explain, polish, paraphrase, or otherwise rewrite", direct_skill)
        self.assertIn("Select this entry before any prompt interpretation", direct_skill)
        self.assertIn("allow_implicit_invocation: false", direct_metadata)

    def call_image_tool(
        self,
        config: dict[str, object],
        name: str = "generate_image",
        **arguments: object,
    ) -> dict[str, object]:
        return call_server(
            config,
            "tools/call",
            {
                "name": name,
                "arguments": {"dry_run": True, "verbose": True, **arguments},
            },
        )

    def test_tools_and_initialize_use_one_pass_fast_path(self) -> None:
        config = self.clean_config()
        response = call_server(config, "tools/list", {})
        tools = tool_map(response)

        self.assertEqual(set(tools), BASELINE_TOOL_NAMES)
        serialized = json.dumps(tools, ensure_ascii=False).lower()
        for removed_detail in (
            "dall-e",
            "prepare_prompt",
            "prompt_profile",
            "prompt_policy",
            "provider_prompt",
            "prompt_check_id",
            "prompt_session_id",
        ):
            self.assertNotIn(removed_detail, serialized)

        for name in STANDARD_IMAGE_TOOLS:
            schema = tools[name]["inputSchema"]
            self.assertIn("prompt", all_property_names(schema))
            self.assertNotIn("provider_prompt", all_property_names(schema))
            self.assertNotIn("prompt_check_id", all_property_names(schema))
            self.assertIn("resolution_user_requested", all_property_names(schema))
            self.assertIn("output_format_user_requested", all_property_names(schema))
            self.assertIn("output_compression_user_requested", all_property_names(schema))
            self.assertIn("thinking_level_user_requested", all_property_names(schema))

        prompt_description = tools["generate_image"]["inputSchema"]["properties"]["prompt"]["description"]
        self.assertIn("already-selected image skill", prompt_description)
        self.assertIn("without host-model rewriting", prompt_description)
        self.assertIn("Do not include analysis or a planning preamble", prompt_description)

        prompt_mode = tools["generate_image"]["inputSchema"]["properties"]["prompt_mode"]
        self.assertEqual(prompt_mode["enum"], ["expand", "direct"])
        self.assertEqual(prompt_mode["default"], "expand")
        batch_job_properties = tools["generate_image_batch"]["inputSchema"]["properties"]["jobs"]["items"]["properties"]
        self.assertNotIn("prompt_mode", batch_job_properties)

        trace_properties = tools["trace_image_job_plan"]["inputSchema"]["properties"]
        self.assertIn("prompts", trace_properties)
        self.assertIn("input_images", trace_properties)
        self.assertNotIn("revised_prompts", trace_properties)
        self.assertNotIn("reference_images", trace_properties)

        initialize = call_server(
            config,
            "initialize",
            {"protocolVersion": "2025-11-25"},
        )
        self.assertEqual(initialize["result"]["instructions"], BASELINE_INSTRUCTIONS)
        self.assertIn("Select the image skill before interpreting the request", BASELINE_INSTRUCTIONS)
        self.assertIn("explicitly invokes Fmage 直传生图", BASELINE_INSTRUCTIONS)
        self.assertIn("Set prompt_mode to expand for fmage or direct for fmage-direct", BASELINE_INSTRUCTIONS)
        self.assertNotIn("prepare_prompt", initialize["result"]["instructions"])
        self.assertNotIn("DALL-E", initialize["result"]["instructions"])

    def test_default_entry_submits_prompt_without_server_prompt_preparation(self) -> None:
        config = self.clean_config()
        prompt = "A finished editorial product photograph with controlled studio lighting."
        response = self.call_image_tool(config, prompt=prompt)
        self.assertNotIn("error", response)
        result = response["result"]["structuredContent"]
        self.assertEqual(result["request"]["prompt"], prompt)
        self.assertEqual(result["prompt_submitted"], prompt)
        self.assertEqual(result["prompt_mode"], "expand")
        self.assertEqual(
            list(result)[:4], ["provider", "provider_transport", "provider_model", "prompt_mode"],
        )
        self.assertNotIn("revised_prompt_submitted", result)
        self.assertNotIn("prompt_preparation", result)
        self.assertNotIn("prompt_policy", result)
        self.assertNotIn("prompt_profile", result)
        self.assertNotIn("provider_prompt", result)

    def test_direct_entry_submits_prompt_without_rewriting(self) -> None:
        config = self.clean_config()
        prompt = "一张原文提示词，不添加任何视觉描述。"
        response = self.call_image_tool(config, prompt=prompt, prompt_mode="direct")
        self.assertNotIn("error", response)
        result = response["result"]["structuredContent"]
        self.assertEqual(result["request"]["prompt"], prompt)
        self.assertEqual(result["prompt_submitted"], prompt)
        self.assertEqual(result["prompt_mode"], "direct")
        self.assertEqual(
            list(result)[:4], ["provider", "provider_transport", "provider_model", "prompt_mode"],
        )
        self.assertNotIn("revised_prompt_submitted", result)

    def test_invalid_prompt_mode_is_rejected(self) -> None:
        response = self.call_image_tool(self.clean_config(), prompt="test", prompt_mode="other")
        self.assertIn("error", response)
        self.assertIn("prompt_mode must be", response["error"]["message"])

    def test_default_quality_high_keeps_resolution_at_2k(self) -> None:
        config = self.clean_config()
        response = self.call_image_tool(config, prompt="plain test image")
        self.assertNotIn("error", response)
        request = response["result"]["structuredContent"]["request"]
        self.assertEqual(request["quality"], "high")
        self.assertEqual(request["size"], "2048x2048")

    def test_image_family_versions_keep_default_quality_and_wire_id(self) -> None:
        for model in (
            "gpt-image-1",
            "openai/gpt-image-1-mini",
            "gpt-image-2",
            "openai/gpt-image-2.5-sunburst",
            "relay/openai/GPT-IMAGE-2.5-FLARE",
            "渠道【GPT_IMAGE_2.5_sunburst】",
            "zenmux:gpt image 2.5",
            "openai/gpt-image-3",
            "openai/GPT-IMAGE-3.1-preview",
            "openai/gpt-image-20",
        ):
            with self.subTest(model=model):
                config = provider_config(["test"], {"test": provider(model)})
                response = self.call_image_tool(config, prompt="plain test image", aspect="16:9")
                self.assertNotIn("error", response)
                request = response["result"]["structuredContent"]["request"]
                self.assertEqual(request["model"], model)
                self.assertEqual(request["quality"], "high")
                self.assertEqual(request["size"], "2736x1536")

    def test_namespaced_quality_survives_all_image_routes(self) -> None:
        model = "openai/gpt-image-2.5-sunburst"
        with tempfile.TemporaryDirectory() as directory:
            reference = Path(directory) / "reference.png"
            reference.write_bytes(TRANSPARENT_TEST_PNG)
            for async_mode in (False, True):
                config = provider_config(["test"], {"test": {**provider(model), "async_mode": async_mode}})
                for tool in sorted(STANDARD_IMAGE_TOOLS):
                    for quality in (None, "medium", "max"):
                        with self.subTest(async_mode=async_mode, tool=tool, quality=quality):
                            arguments = {"aspect": "16:9"}
                            if tool.endswith("_batch"):
                                arguments["jobs"] = [{"prompt": "proposal one"}, {"prompt": "proposal two"}]
                            else:
                                arguments["prompt"] = "plain test image"
                            if tool.startswith("edit_"):
                                arguments["images"] = [str(reference)]
                            if quality is not None:
                                arguments.update(quality=quality, quality_user_requested=True)
                            response = self.call_image_tool(config, name=tool, **arguments)
                            self.assertNotIn("error", response, json.dumps(response))
                            result = response["result"]["structuredContent"]
                            request = result["request"]
                            jobs = request["jobs"] if tool.endswith("_batch") else [request]
                            for job in jobs:
                                self.assertEqual(job["model"], model)
                                if tool.endswith("_batch"):
                                    if quality is not None:
                                        self.assertEqual(job["quality"], quality)
                                else:
                                    self.assertEqual(job["quality"], quality or "high")
                                    self.assertEqual(job["size"], "2736x1536")
                            if tool.endswith("_batch"):
                                self.assertEqual(result["completed_count"], 2)
                                self.assertEqual(result["failed_count"], 0)
                                self.assertIn(f"resolution_inferred_from_{quality or 'high'}_quality", result["notes"])
                            self.assertNotIn("Mapped quality=", json.dumps(response))

    def test_model_lookalikes_do_not_inherit_image_family_default_quality(self) -> None:
        for model in ("notgpt-image-2.5", "gpt-image-2ish", "gpt-image-2.5ish"):
            with self.subTest(model=model):
                config = provider_config(["test"], {"test": provider(model)})
                response = self.call_image_tool(config, prompt="plain test image")
                self.assertNotIn("error", response)
                request = response["result"]["structuredContent"]["request"]
                self.assertEqual(request["model"], model)
                self.assertEqual(request["quality"], "medium")

    def test_high_quality_prompt_semantic_uses_4k(self) -> None:
        config = self.clean_config()
        response = self.call_image_tool(config, prompt="A high-quality product image.")
        self.assertNotIn("error", response)
        request = response["result"]["structuredContent"]["request"]
        self.assertEqual(request["quality"], "high")
        self.assertEqual(request["size"], "2880x2880")

    def test_visual_8k_language_does_not_become_delivery_resolution(self) -> None:
        config = self.clean_config()
        prompt = "一张产品摄影图，具备8K超高分辨率、极致清晰细节和真实材质。"
        response = self.call_image_tool(
            config,
            prompt=prompt,
            resolution="4k",
            size="4096x4096",
            resolution_user_requested=True,
        )
        self.assertNotIn("error", response)
        result = response["result"]["structuredContent"]
        self.assertEqual(result["request"]["prompt"], prompt)
        self.assertNotIn("resolution", result["request"])
        self.assertEqual(result["request"]["size"], "2048x2048")
        self.assertTrue(any("visual-quality language" in warning for warning in result["warnings"]))

    def test_unrequested_jpeg_falls_back_to_png(self) -> None:
        config = self.clean_config()
        response = self.call_image_tool(config, prompt="plain test image", output_format="jpeg")
        self.assertNotIn("error", response)
        result = response["result"]["structuredContent"]
        self.assertEqual(result["request"]["output_format"], "png")
        self.assertTrue(any("output_format_user_requested" in warning for warning in result["warnings"]))

    def test_explicit_jpeg_remains_a_parameter(self) -> None:
        config = self.clean_config()
        response = self.call_image_tool(
            config,
            prompt="plain test image",
            output_format="jpeg",
            output_format_user_requested=True,
        )
        self.assertNotIn("error", response)
        result = response["result"]["structuredContent"]
        self.assertEqual(result["request"]["output_format"], "jpeg")

    def test_explicit_delivery_resolution_remains_a_parameter(self) -> None:
        config = self.clean_config()
        response = self.call_image_tool(
            config,
            prompt="一张具备8K超高分辨率效果的产品图，输出分辨率为4k。",
            resolution="4k",
            resolution_user_requested=True,
        )
        self.assertNotIn("error", response)
        request = response["result"]["structuredContent"]["request"]
        self.assertEqual(request["size"], "2880x2880")

    def test_unrequested_compression_is_removed_across_image_routes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            reference = Path(directory) / "reference.png"
            reference.write_bytes(TRANSPARENT_TEST_PNG)
            for async_mode in (False, True):
                config = provider_config(
                    ["test"], {"test": {**provider("gpt-image-2.5"), "async_mode": async_mode}},
                )
                for tool in sorted(STANDARD_IMAGE_TOOLS):
                    for mode in ("expand", "direct"):
                        with self.subTest(async_mode=async_mode, tool=tool, mode=mode):
                            arguments = {
                                "prompt_mode": mode,
                                "quality": "max", "quality_user_requested": True,
                                "resolution": "2k", "resolution_user_requested": True,
                                "aspect": "16:9", "output_format": "png",
                                "output_compression": 100,
                            }
                            if tool.endswith("_batch"):
                                arguments["jobs"] = [
                                    {"prompt": f"proposal {index}", "output_format": "png",
                                     "output_compression": 100}
                                    for index in range(4)
                                ]
                            else:
                                arguments["prompt"] = "plain test image"
                            if tool.startswith("edit_"):
                                arguments["images"] = [str(reference)]
                            response = self.call_image_tool(config, name=tool, **arguments)
                            self.assertNotIn("error", response, json.dumps(response))
                            result = response["result"]["structuredContent"]
                            request = result["request"]
                            jobs = request["jobs"] if tool.endswith("_batch") else [request]
                            for job in jobs:
                                self.assertEqual(job["output_format"], "png")
                                self.assertEqual(job["quality"], "max")
                                self.assertNotIn("output_compression", job)
                            self.assertTrue(any("output_compression_user_requested" in w for w in result["warnings"]))
                            if tool.endswith("_batch"):
                                self.assertEqual(result["completed_count"], 4)
                                self.assertEqual(result["failed_count"], 0)
                            else:
                                self.assertEqual(request["size"], "2736x1536")

    def test_format_and_compression_require_independent_explicit_requests(self) -> None:
        for async_mode in (False, True):
            config = provider_config(
                ["test"], {"test": {**provider("gpt-image-2.5"), "async_mode": async_mode}},
            )
            for output_format in ("jpeg", "webp"):
                for compression in (0, 80, 100):
                    for consent in (None, False, True):
                        with self.subTest(async_mode=async_mode, format=output_format,
                                          compression=compression, consent=consent):
                            arguments = {
                                "output_format": output_format, "output_format_user_requested": True,
                                "output_compression": compression,
                            }
                            if consent is not None:
                                arguments["output_compression_user_requested"] = consent
                            response = self.call_image_tool(config, prompt="test image", **arguments)
                            self.assertNotIn("error", response, json.dumps(response))
                            request = response["result"]["structuredContent"]["request"]
                            self.assertEqual(request["output_format"], output_format)
                            if consent:
                                self.assertEqual(request["output_compression"], compression)
                            else:
                                self.assertNotIn("output_compression", request)

    def test_unrequested_format_and_compression_use_png_without_compression(self) -> None:
        for output_format in (None, "png", "jpeg", "webp"):
            arguments = {"output_compression": 100}
            if output_format:
                arguments["output_format"] = output_format
            response = self.call_image_tool(self.clean_config(), prompt="test image", **arguments)
            self.assertNotIn("error", response, json.dumps(response))
            request = response["result"]["structuredContent"]["request"]
            self.assertEqual(request["output_format"], "png")
            self.assertNotIn("output_compression", request)

    def test_explicit_compression_cannot_switch_png_to_another_format(self) -> None:
        for output_format in ("png", "jpeg"):
            response = self.call_image_tool(
                self.clean_config(), prompt="test image",
                output_format=output_format,
                output_compression=80, output_compression_user_requested=True,
            )
            self.assertIn("error", response)
            self.assertIn("can only be used with --output-format jpeg or webp", response["error"]["message"])

    def test_batch_override_does_not_inherit_consent_for_a_different_value(self) -> None:
        response = self.call_image_tool(
            self.clean_config(), name="generate_image_batch",
            output_format="jpeg", output_format_user_requested=True,
            output_compression=80, output_compression_user_requested=True,
            jobs=[
                {"prompt": "inherit explicit defaults"},
                {"prompt": "unrequested compression override", "output_compression": 100},
                {"prompt": "unrequested format override", "output_format": "webp",
                 "output_compression_user_requested": False},
                {"prompt": "explicit overrides", "output_format": "webp",
                 "output_format_user_requested": True, "output_compression": 0,
                 "output_compression_user_requested": True},
            ],
        )
        self.assertNotIn("error", response, json.dumps(response))
        result = response["result"]["structuredContent"]
        self.assertEqual(result["completed_count"], 4)
        self.assertEqual(result["failed_count"], 0)
        jobs = result["request"]["jobs"]
        self.assertEqual(jobs[0]["output_format"], "jpeg")
        self.assertEqual(jobs[0]["output_compression"], 80)
        self.assertEqual(jobs[1]["output_format"], "jpeg")
        self.assertNotIn("output_compression", jobs[1])
        self.assertEqual(jobs[2]["output_format"], "png")
        self.assertNotIn("output_compression", jobs[2])
        self.assertEqual(jobs[3]["output_format"], "webp")
        self.assertEqual(jobs[3]["output_compression"], 0)

    def test_max_in_provider_or_prompt_does_not_select_quality_or_compression(self) -> None:
        config = provider_config(["Max"], {"Max": provider("gpt-image-2.5")})
        response = self.call_image_tool(config, provider="Max", prompt="A box labelled Max.")
        self.assertNotIn("error", response, json.dumps(response))
        result = response["result"]["structuredContent"]
        self.assertEqual(result["provider"], "Max")
        self.assertEqual(result["request"]["quality"], "high")
        self.assertEqual(result["request"]["output_format"], "png")
        self.assertNotIn("output_compression", result["request"])
        self.assertEqual(result["request"]["prompt"], "A box labelled Max.")

    def test_visual_resolution_language_is_documented_in_tool_schema(self) -> None:
        config = self.clean_config()
        tool = tool_map(call_server(config, "tools/list", {}))["generate_image"]
        resolution_description = tool["inputSchema"]["properties"]["resolution"]["description"]
        self.assertIn("8K超高分辨率", resolution_description)
        self.assertIn("do not map it here", resolution_description)

    def test_extended_quality_is_mapped_before_transport(self) -> None:
        for model, transport in (
            ("gpt-image-2", "openai-images"),
            ("gemini-3.1-flash-image-preview", "gemini-generate-content"),
            ("gpt-image-2.50", "openai-images"),
            ("openai/gpt-image-2.50", "openai-images"),
            ("openai/gpt-image-3", "openai-images"),
            ("openai/gpt-image-20", "openai-images"),
        ):
            config = provider_config(["test"], {"test": provider(model, transport)})
            for quality in ("xhigh", "max"):
                with self.subTest(model=model, transport=transport, quality=quality):
                    response = self.call_image_tool(
                        config,
                        prompt="test image",
                        quality=quality,
                        quality_user_requested=True,
                    )
                    self.assertNotIn("error", response)
                    structured = response["result"]["structuredContent"]
                    request = structured["request"]
                    if transport == "openai-images":
                        self.assertEqual(request["quality"], "high")
                    else:
                        self.assertEqual(structured["requested_resolution"], "4K")
                    self.assertIn("Mapped quality=", json.dumps(response))

    def test_batch_quality_override_is_mapped_per_job_before_submission(self) -> None:
        response = self.call_image_tool(
            provider_config(["test"], {"test": provider("gpt-image-2", "openai-images")}),
            name="generate_image_batch",
            quality="high",
            jobs=[{"prompt": "valid"}, {"prompt": "mapped", "quality": "max", "quality_user_requested": True}],
        )
        self.assertNotIn("error", response)
        jobs = response["result"]["structuredContent"]["request"]["jobs"]
        self.assertEqual([job["quality"] for job in jobs], ["high", "high"])
        self.assertIn("Mapped quality=", json.dumps(response))

    def test_edit_batch_shares_top_level_reference_image_with_every_job(self) -> None:
        config = provider_config(["test"], {"test": provider("gpt-image-2", "openai-images")})
        tools = tool_map(call_server(config, "tools/list", {}))
        properties = tools["edit_image_batch"]["inputSchema"]["properties"]
        self.assertEqual(
            [option.get("type") for option in properties["images"]["anyOf"]],
            ["string", "array"],
        )

        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "reference.png"
            image_path.write_bytes(TRANSPARENT_TEST_PNG)
            response = self.call_image_tool(
                config,
                name="edit_image_batch",
                images=str(image_path),
                jobs=[
                    {"prompt": "proposal one"},
                    {"prompt": "proposal two"},
                    {"prompt": "proposal three"},
                ],
            )

        self.assertNotIn("error", response, json.dumps(response))
        result = response["result"]["structuredContent"]
        self.assertEqual(result["completed_count"], 3)
        self.assertEqual(result["failed_count"], 0)
        self.assertEqual([job["status"] for job in result["job_statuses"]], ["completed"] * 3)

    def test_image_2_5_preserves_explicit_extended_quality(self) -> None:
        for model in (
            "gpt-image-2.5", "gpt-image-2.5-flare", "gpt-image-2.5-sunburst",
            "openai/gpt-image-2.5-sunburst", "渠道【GPT_IMAGE_2.5_sunburst】",
        ):
            config = provider_config(["test"], {"test": provider(model)})
            for quality in ("xhigh", "max"):
                with self.subTest(model=model, quality=quality):
                    response = self.call_image_tool(
                        config, prompt="test image", quality=quality, quality_user_requested=True,
                    )
                    request = response["result"]["structuredContent"]["request"]
                    self.assertEqual(request["quality"], quality)

    def test_batch_route_keeps_each_prompt_direct(self) -> None:
        config = self.clean_config()
        response = call_server(
            config,
            "tools/call",
            {
                "name": "generate_image_batch",
                "arguments": {
                    "dry_run": True,
                    "verbose": True,
                    "jobs": [
                        {"prompt": "first complete prompt"},
                        {"prompt": "second complete prompt"},
                    ],
                },
            },
        )
        self.assertNotIn("error", response)
        result = response["result"]["structuredContent"]
        self.assertEqual(
            [job["prompt"] for job in result["request"]["jobs"]],
            ["first complete prompt", "second complete prompt"],
        )
        self.assertEqual(result["prompt_mode"], "expand")
        self.assertEqual(result["prompts_submitted"], ["first complete prompt", "second complete prompt"])
        self.assertEqual(
            list(result)[:4], ["provider", "provider_transport", "provider_model", "prompt_mode"],
        )
        self.assertNotIn("prompt_preparations", result)

    def test_trace_uses_canonical_prompt_fields(self) -> None:
        config = self.clean_config()
        response = call_server(
            config,
            "tools/call",
            {
                "name": "trace_image_job_plan",
                "arguments": {
                    "image_job_plan": {"mode": "direct"},
                    "image_tool": "generate_image",
                    "prompts": ["原文"],
                    "input_images": ["C:/input.png"],
                    "prompt_mode": "direct",
                },
            },
        )
        self.assertNotIn("error", response)
        trace = response["result"]["structuredContent"]
        self.assertEqual(trace["prompts"], ["原文"])
        self.assertEqual(trace["input_images"], ["C:/input.png"])
        self.assertEqual(trace["prompt_mode"], "direct")

    def test_transparent_background_is_forwarded_for_standard_tools(self) -> None:
        config = self.clean_config()
        response = self.call_image_tool(
            config,
            prompt="transparent product cutout",
            background="transparent",
        )
        self.assertNotIn("error", response)
        request = response["result"]["structuredContent"]["request"]
        self.assertEqual(request["background"], "transparent")
        self.assertEqual(request["output_format"], "png")

        with tempfile.TemporaryDirectory() as temporary_directory:
            reference = Path(temporary_directory) / "reference.png"
            reference.write_bytes(TRANSPARENT_TEST_PNG)
            edit = self.call_image_tool(
                config,
                name="edit_image",
                prompt="turn the reference into a transparent product cutout",
                images=[str(reference)],
                background="transparent",
            )
            self.assertNotIn("error", edit)
            edit_request = edit["result"]["structuredContent"]["request"]
            self.assertEqual(edit_request["background"], "transparent")
            self.assertEqual(edit_request["output_format"], "png")

    def test_removed_prompt_preparation_fields_are_rejected(self) -> None:
        for field in ("compatibility", "compatibility_profile", "prompt_profile", "prompt_policy"):
            config = provider_config(
                ["legacy"],
                {"legacy": {**provider(), field: "obsolete"}},
            )
            response = self.call_image_tool(config, prompt="complete prompt")
            self.assertIn("error", response)
            self.assertIn("removed prompt-preparation fields", response["error"]["message"])
            self.assertIn(field, response["error"]["message"])


if __name__ == "__main__":
    unittest.main()
