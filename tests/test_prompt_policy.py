from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = PLUGIN_ROOT / "mcp" / "server.mjs"
SKILL_PATH = PLUGIN_ROOT / "skills" / "fmage" / "SKILL.md"
PROVIDERS_EXAMPLE_PATH = PLUGIN_ROOT / "config" / "providers.example.json"
EZAI = "DE3限制版-image-2"
EZAI_NORMAL = "ezai-image-2"
EZAI_BANANA = "ezai-banana"
EZAI_PREPARE_TOOL = "prepare_prompt_dalle3"
STANDARD_IMAGE_TOOLS = {
    "generate_image",
    "generate_image_batch",
    "edit_image",
    "edit_image_batch",
}
EZAI_TOOLS = {EZAI_PREPARE_TOOL}
BASELINE_TOOL_NAMES = STANDARD_IMAGE_TOOLS | {
    "regress_image",
    "trace_image_job_plan",
    "get_image_task_status",
    "get_provider_status",
}
BASELINE_INSTRUCTIONS = (
    "Use Fmage image tools. Complete the unrestricted prompt with the active model before provider "
    "adaptation. For edits, identify reference-image roles and preserve required text, layout, and "
    "other locked details."
)


def provider(name: str, prompt_policy: dict[str, object] | None = None) -> dict[str, object]:
    value: dict[str, object] = {
        "transport": "openai-images",
        "base_url": "https://example.invalid/v1",
        "model": "gpt-image-2",
        "api_key": "",
    }
    if prompt_policy is not None:
        value["prompt_policy"] = prompt_policy
    return value


def banana_provider(model: str = "nano-banana-2") -> dict[str, object]:
    return {
        "transport": "ezai-banana-images",
        "base_url": "https://api-direct.ezaiclub.com",
        "model": model,
        "response_format": "url",
        "api_key": "",
    }


def dalle3_provider(prompt_policy: dict[str, object] | None = None) -> dict[str, object]:
    value = provider(EZAI, prompt_policy)
    value["prompt_profile"] = "dall-e3"
    return value


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
    policy = {
        "max_chars": 5,
        "target_chars": 4,
        "overflow_strategy": "language_aware_compact",
        "preferred_compact_language": "zh-CN",
    }

    def ordinary_config(self) -> dict[str, object]:
        return provider_config(
            ["ordinary-image"],
            {
                "ordinary-image": provider("ordinary-image", self.policy),
                EZAI: dalle3_provider(self.policy),
            },
        )

    def ezai_config(self) -> dict[str, object]:
        return provider_config(
            [EZAI],
            {EZAI: dalle3_provider(self.policy)},
        )

    def ezai_multi_provider_config(self) -> dict[str, object]:
        return provider_config(
            [EZAI, "ordinary-image"],
            {
                EZAI: dalle3_provider(self.policy),
                "ordinary-image": provider("ordinary-image"),
            },
        )

    def ezai_nondefault_config(self) -> dict[str, object]:
        return provider_config(
            ["ordinary-image", EZAI],
            {
                "ordinary-image": provider("ordinary-image"),
                EZAI: dalle3_provider(self.policy),
            },
        )

    def ezai_and_banana_config(self) -> dict[str, object]:
        return provider_config(
            [EZAI, EZAI_BANANA],
            {
                EZAI: dalle3_provider(self.policy),
                EZAI_BANANA: banana_provider(),
            },
        )

    def ezai_normal_config(self) -> dict[str, object]:
        return provider_config(
            [EZAI_NORMAL],
            {EZAI_NORMAL: provider(EZAI_NORMAL, self.policy)},
        )

    def call_image_tool(
        self,
        config: dict[str, object],
        name: str,
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

    def prepare_ezai_prompt(
        self,
        provider_prompt: str,
        prompt_session_id: str,
    ) -> dict[str, object]:
        response = call_server(
            self.ezai_config(),
            "tools/call",
            {
                "name": EZAI_PREPARE_TOOL,
                "arguments": {
                    "provider_prompt": provider_prompt,
                    "prompt_session_id": prompt_session_id,
                },
            },
        )
        self.assertNotIn("error", response)
        return response["result"]["structuredContent"]

    def ready_prompt_check_id(self, prompt: str, provider_prompt: str) -> str:
        initial = self.start_over_limit_image_prompt(prompt)
        result = self.prepare_ezai_prompt(
            provider_prompt,
            str(initial["prompt_session_id"]),
        )
        self.assertTrue(result["ready"])
        return str(result["prompt_check_id"])

    def start_over_limit_image_prompt(self, prompt: str) -> dict[str, object]:
        response = self.call_image_tool(
            self.ezai_config(),
            "generate_image",
            prompt=prompt,
        )
        self.assertNotIn("error", response)
        result = response["result"]["structuredContent"]
        self.assertFalse(result["ready"])
        self.assertFalse(result["provider_request_sent"])
        self.assertIn("prompt_session_id", result)
        return result

    def test_ordinary_tools_and_initialize_match_pre_update_baseline(self) -> None:
        config = self.ordinary_config()
        current_tools = tool_map(call_server(config, "tools/list", {}))
        self.assertEqual(set(current_tools), BASELINE_TOOL_NAMES)
        self.assertNotIn(EZAI, json.dumps(current_tools))
        ordinary_schema = json.dumps(current_tools, ensure_ascii=False)
        self.assertNotIn('"maxLength"', ordinary_schema)
        for ezai_only_guidance in (
            "Stage 1 source",
            "Stage 2 transport-only",
            "English-dominant",
            "translate it meaning-for-meaning into Chinese",
            "provider_prompt",
        ):
            self.assertNotIn(ezai_only_guidance, ordinary_schema)
        for name in STANDARD_IMAGE_TOOLS:
            self.assertNotIn("provider_prompt", all_property_names(current_tools[name]))
            self.assertIn("resolution_user_requested", all_property_names(current_tools[name]))
            self.assertIn("thinking_level_user_requested", all_property_names(current_tools[name]))
        self.assertEqual(
            current_tools["generate_image"]["inputSchema"]["properties"]["prompt"]["description"],
            "One complete provider-appropriate image prompt revised by the active Codex model. "
            "Shape it for the image use case, preserve the requested composition and style, and "
            "quote required text verbatim.",
        )
        self.assertEqual(
            current_tools["edit_image"]["inputSchema"]["properties"]["prompt"]["description"],
            "One complete provider-appropriate image prompt revised by the active Codex model. "
            "Identify each input image by index and role, state change-only and keep-unchanged "
            "invariants, and quote required text verbatim.",
        )

        current_initialize = call_server(
            config,
            "initialize",
            {"protocolVersion": "2025-11-25"},
        )
        self.assertEqual(current_initialize["result"]["instructions"], BASELINE_INSTRUCTIONS)
        for ezai_only_guidance in (
            "Stage 1 source",
            "Stage 2 transport-only",
            "English-dominant",
            "meaning-for-meaning into Chinese",
            "provider_prompt",
        ):
            self.assertNotIn(ezai_only_guidance, current_initialize["result"]["instructions"])

    def test_inactive_ezai_is_invisible_and_ordinary_policy_is_ignored(self) -> None:
        config = self.ordinary_config()
        tools = tool_map(call_server(config, "tools/list", {}))
        self.assertTrue(EZAI_TOOLS.isdisjoint(tools))

        response = self.call_image_tool(
            config,
            "generate_image",
            prompt="complete ordinary prompt",
        )
        result = response["result"]["structuredContent"]
        self.assertEqual(result["request"]["prompt"], "complete ordinary prompt")
        self.assertEqual(result["revised_prompt_submitted"], "complete ordinary prompt")
        forbidden = {
            "revised_prompt_source",
            "source_prompt_chars",
            "submitted_prompt_chars",
            "prompt_policy",
            "prompt_policy_applied",
        }
        self.assertTrue(forbidden.isdisjoint(result))

        status = call_server(
            config,
            "tools/call",
            {"name": "get_provider_status", "arguments": {}},
        )["result"]
        self.assertNotIn("Prompt policy:", status["content"][0]["text"])
        self.assertNotIn("prompt_policy", status["structuredContent"])

    def test_ezai_tools_appear_only_with_active_policy(self) -> None:
        response = call_server(self.ezai_config(), "tools/list", {})
        listed_tools = response["result"]["tools"]
        tools = tool_map(response)
        self.assertTrue(EZAI_TOOLS.issubset(tools))
        self.assertTrue(STANDARD_IMAGE_TOOLS.issubset(tools))
        self.assertEqual(len(listed_tools), 9)
        self.assertLess(
            len(json.dumps(listed_tools, ensure_ascii=False, separators=(",", ":"))),
            25000,
        )
        self.assertIn("prompt_check_id", all_property_names(tools["generate_image"]))
        self.assertIn("prompt_check_id", all_property_names(tools["generate_image_batch"]))

        prepare_schema = tools[EZAI_PREPARE_TOOL]["inputSchema"]
        self.assertEqual(
            prepare_schema["required"],
            ["prompt_session_id", "provider_prompt"],
        )
        self.assertNotIn("maxLength", prepare_schema["properties"]["provider_prompt"])
        self.assertIn("ready=false", tools[EZAI_PREPARE_TOOL]["description"])
        self.assertIn("sends no provider request", tools[EZAI_PREPARE_TOOL]["description"])

        transport_description = prepare_schema["properties"]["provider_prompt"]["description"]
        self.assertIn("used only with prompt_session_id", transport_description)
        self.assertIn("Follow the latest preparation result's next_action", transport_description)
        self.assertNotIn("English-dominant", transport_description)
        self.assertNotIn("Chinese", transport_description)
        self.assertNotIn(str(self.policy["max_chars"]), transport_description)
        self.assertNotIn(str(self.policy["target_chars"]), transport_description)

        visible_ezai_schema = json.dumps(tools[EZAI_PREPARE_TOOL], ensure_ascii=False)
        for hidden_stage_two_detail in (
            "English-dominant",
            "Chinese-dominant",
            "meaning-for-meaning into Chinese",
            "preferred_compact_language",
        ):
            self.assertNotIn(hidden_stage_two_detail, visible_ezai_schema)

        initialize = call_server(
            self.ezai_config(),
            "initialize",
            {"protocolVersion": "2025-11-25"},
        )["result"]["instructions"]
        self.assertIn("selected provider may apply a prompt profile automatically", initialize)
        self.assertIn("Keep the complete source prompt unchanged", initialize)
        self.assertIn("ready=false with provider_request_sent=false", initialize)
        self.assertIn("prompt_check_id only", initialize)
        self.assertNotIn(EZAI, initialize)
        self.assertNotIn("gpt-image-2", initialize)
        self.assertIn(EZAI_PREPARE_TOOL, initialize)
        self.assertLess(len(initialize), 1000)
        self.assertNotIn("English-dominant", initialize)
        self.assertNotIn("translate", initialize)

        skill = SKILL_PATH.read_text(encoding="utf-8")
        self.assertIn("prompt_profile", skill)
        self.assertIn(EZAI_PREPARE_TOOL, skill)
        self.assertIn("exactly the same complete unrestricted source prompt", skill)
        self.assertIn("do not shorten, summarize, pad, repeat, or change its language", skill)
        self.assertIn("complete meaning-for-meaning Chinese translation", skill)
        self.assertIn("Only if the complete Chinese translation itself still exceeds the limit", skill)
        self.assertIn("initial tool result determines whether adaptation is needed", skill)
        self.assertIn("same active Codex model in the next continuation", skill)
        self.assertIn("Do not use a subagent or external model", skill)
        self.assertNotIn("For a Chinese request, write the complete source prompt in Chinese", skill)
        self.assertNotIn("keep it concise when no meaning is lost", skill)
        self.assertIn(
            'For a provider explicitly configured with `prompt_profile: "dall-e3"`, never pass `provider_prompt`',
            skill,
        )
        self.assertIn("never resend `prompt` or `provider_prompt`", skill)
        self.assertIn("require no user approval", skill)
        self.assertIn("do not call `view_image`", skill)
        self.assertIn("never issue concurrent or duplicate status checks", skill)
        self.assertIn("Do not create subagents", skill)
        self.assertIn("explicitly selects any tier, including `medium`", skill)
        self.assertIn("always pass that exact `quality`", skill)
        self.assertIn("set `resolution_user_requested: true`", skill)
        self.assertIn("Never derive image-model `thinking_level`", skill)
        self.assertIn("`thinking_level_user_requested: true`", skill)
        self.assertNotIn("delivery-language.md", skill)
        self.assertNotIn("resolution-tiers.md", skill)

        status = call_server(
            self.ezai_config(),
            "tools/call",
            {"name": "get_provider_status", "arguments": {}},
        )["result"]["structuredContent"]
        self.assertEqual(status["prompt_profile"], "dall-e3")
        self.assertEqual(status["prompt_policy"]["max_chars"], self.policy["max_chars"])

        without_policy = self.ezai_normal_config()
        tools_without_policy = tool_map(call_server(without_policy, "tools/list", {}))
        self.assertTrue(EZAI_TOOLS.isdisjoint(tools_without_policy))

    def test_ezai_default_keeps_standard_tools_for_alternate_providers(self) -> None:
        response = call_server(self.ezai_multi_provider_config(), "tools/list", {})
        listed_tools = response["result"]["tools"]
        tools = tool_map(response)
        self.assertEqual(len(listed_tools), 9)
        self.assertTrue(STANDARD_IMAGE_TOOLS.issubset(tools))
        self.assertIn(EZAI_PREPARE_TOOL, tools)
        for name in STANDARD_IMAGE_TOOLS:
            self.assertNotIn("provider_prompt", all_property_names(tools[name]))
            self.assertIn("prompt_check_id", all_property_names(tools[name]))
            self.assertIn(
                "selected provider may apply a prompt profile automatically",
                tools[name]["description"],
            )
            self.assertNotIn(EZAI, tools[name]["description"])
            self.assertNotIn("gpt-image-2", tools[name]["description"])

    def test_nondefault_policy_provider_is_not_named_in_shared_guidance(self) -> None:
        config = self.ezai_nondefault_config()
        initialize = call_server(
            config,
            "initialize",
            {"protocolVersion": "2025-11-25"},
        )["result"]["instructions"]
        tools = tool_map(call_server(config, "tools/list", {}))

        self.assertIn("selected provider", initialize)
        self.assertNotIn(EZAI, initialize)
        self.assertNotIn("gpt-image-2", initialize)
        self.assertTrue(EZAI_TOOLS.issubset(tools))
        for name in STANDARD_IMAGE_TOOLS:
            self.assertIn("selected provider", tools[name]["description"])
            self.assertNotIn(EZAI, tools[name]["description"])
            self.assertNotIn("gpt-image-2", tools[name]["description"])

    def test_banana_standard_route_never_enters_image_prompt_policy(self) -> None:
        response = self.call_image_tool(
            self.ezai_and_banana_config(),
            "generate_image",
            provider=EZAI_BANANA,
            prompt="complete Nano prompt",
            resolution="2k",
            aspect="1:1",
        )
        self.assertNotIn("error", response)
        result = response["result"]["structuredContent"]
        self.assertEqual(result["provider"], EZAI_BANANA)
        self.assertEqual(result["provider_transport"], "ezai-banana-images")
        self.assertEqual(result["request"]["model"], "nano-banana-2")
        self.assertEqual(result["request"]["prompt"], "complete Nano prompt")
        forbidden = {
            "revised_prompt_source",
            "source_prompt_chars",
            "submitted_prompt_chars",
            "prompt_policy",
            "prompt_policy_applied",
            "prompt_preparation",
        }
        self.assertTrue(forbidden.isdisjoint(result))

    def test_dalle3_prompt_policy_rejects_non_openai_transport(self) -> None:
        wrong_transport = banana_provider()
        wrong_transport["prompt_profile"] = "dall-e3"
        wrong_transport["prompt_policy"] = self.policy
        config = provider_config([EZAI], {EZAI: wrong_transport})

        response = self.call_image_tool(
            config,
            "generate_image",
            provider=EZAI,
            prompt="short",
        )
        self.assertIn("error", response)
        self.assertIn('prompt_profile "dall-e3" currently requires transport "openai-images"', response["error"]["message"])

    def test_explicit_prompt_profile_accepts_custom_relay_and_model(self) -> None:
        provider_name = "custom-image-relay"
        custom_provider = provider(provider_name)
        custom_provider.update(
            {
                "prompt_profile": "dall-e3",
                "base_url": "https://relay.example.invalid/custom/v1",
                "model": "vendor/dalle-compatible-latest",
            }
        )
        config = provider_config([provider_name], {provider_name: custom_provider})

        tools = tool_map(call_server(config, "tools/list", {}))
        self.assertTrue(EZAI_TOOLS.issubset(tools))

        status = call_server(
            config,
            "tools/call",
            {"name": "get_provider_status", "arguments": {}},
        )["result"]["structuredContent"]
        self.assertEqual(status["prompt_profile"], "dall-e3")
        self.assertEqual(status["prompt_policy"]["max_chars"], 4000)
        self.assertEqual(status["prompt_policy"]["target_chars"], 3900)

        response = self.call_image_tool(
            config,
            "generate_image",
            prompt="custom relay prompt",
        )
        self.assertNotIn("error", response)
        result = response["result"]["structuredContent"]
        self.assertEqual(result["provider"], provider_name)
        self.assertEqual(result["provider_base_url"], custom_provider["base_url"])
        self.assertEqual(result["provider_model"], custom_provider["model"])
        self.assertEqual(result["request"]["model"], custom_provider["model"])
        self.assertFalse(result["prompt_policy_applied"])

    def test_provider_name_does_not_enable_prompt_profile(self) -> None:
        config = provider_config(
            [EZAI],
            {EZAI: provider(EZAI, self.policy)},
        )
        tools = tool_map(call_server(config, "tools/list", {}))
        self.assertTrue(EZAI_TOOLS.isdisjoint(tools))

        response = self.call_image_tool(
            config,
            "generate_image",
            prompt="普通提示",
            provider_prompt="不应触发 DALL-E 限制",
        )
        self.assertNotIn("error", response)
        result = response["result"]["structuredContent"]
        self.assertEqual(result["request"]["prompt"], "普通提示")
        self.assertNotIn("prompt_profile", result)
        self.assertNotIn("prompt_policy_applied", result)

    def test_legacy_prompt_compatibility_fields_are_rejected(self) -> None:
        for legacy_field in ("compatibility", "compatibility_profile"):
            with self.subTest(legacy_field=legacy_field):
                legacy_provider = provider("legacy-provider")
                legacy_provider[legacy_field] = "dall-e3"
                config = provider_config(
                    ["legacy-provider"],
                    {"legacy-provider": legacy_provider},
                )
                response = self.call_image_tool(
                    config,
                    "generate_image",
                    prompt="test",
                )
                self.assertIn("error", response)
                self.assertIn(f'unsupported legacy field "{legacy_field}"', response["error"]["message"])
                self.assertIn('prompt_profile: "dall-e3"', response["error"]["message"])

    def test_prompt_policy_without_prompt_profile_keeps_provider_normal(self) -> None:
        config = self.ezai_normal_config()
        tools = tool_map(call_server(config, "tools/list", {}))
        self.assertTrue(EZAI_TOOLS.isdisjoint(tools))

        response = self.call_image_tool(
            config,
            "generate_image",
            prompt="完整普通提示",
        )
        self.assertNotIn("error", response)
        result = response["result"]["structuredContent"]
        self.assertEqual(result["provider"], EZAI_NORMAL)
        self.assertEqual(result["request"]["prompt"], "完整普通提示")
        self.assertNotIn("prompt_policy_applied", result)
        self.assertNotIn("revised_prompt_source", result)

    def test_example_config_uses_profile_defaults_and_removes_closed_providers(self) -> None:
        config = json.loads(PROVIDERS_EXAMPLE_PATH.read_text(encoding="utf-8"))
        providers = config["providers"]

        self.assertNotIn("prompt_policy", providers[EZAI_NORMAL])
        self.assertEqual(providers[EZAI]["prompt_profile"], "dall-e3")
        self.assertNotIn("prompt_policy", providers[EZAI])
        self.assertEqual(providers["808-image-2"]["transport"], "openai-images")
        self.assertEqual(providers["808-image-2"]["transport_profile"], "808")
        self.assertNotIn("yunwu-image-2", providers)
        self.assertNotIn("right-image-2", providers)
        self.assertNotIn("right-banana-2", providers)

    def test_explicit_auto_quality_is_preserved_for_standard_providers(self) -> None:
        config = provider_config(
            ["ordinary-image"],
            {"ordinary-image": provider("ordinary-image")},
        )
        response = self.call_image_tool(
            config,
            "generate_image",
            prompt="quality routing",
            quality="auto",
            quality_user_requested=True,
        )
        self.assertNotIn("error", response)
        result = response["result"]["structuredContent"]
        self.assertEqual(result["request"]["quality"], "auto")

    def test_unmarked_resolution_override_is_ignored_for_image_2(self) -> None:
        response = self.call_image_tool(
            self.ordinary_config(),
            "generate_image",
            prompt="default resolution routing",
            aspect="3:4",
            resolution="2k",
        )
        self.assertNotIn("error", response)
        result = response["result"]["structuredContent"]
        self.assertEqual(result["request"]["size"], "2496x3312")
        self.assertEqual(result["request"]["quality"], "high")
        self.assertTrue(
            any("resolution_user_requested was not true" in item for item in result["warnings"])
        )

    def test_user_requested_resolution_override_is_preserved_for_image_2(self) -> None:
        response = self.call_image_tool(
            self.ordinary_config(),
            "generate_image",
            prompt="explicit resolution routing",
            aspect="3:4",
            resolution="2k",
            resolution_user_requested=True,
        )
        self.assertNotIn("error", response)
        result = response["result"]["structuredContent"]
        self.assertEqual(result["request"]["size"], "1776x2368")
        self.assertEqual(result["request"]["quality"], "high")
        self.assertFalse(result.get("warnings"))

    def test_unmarked_incompatible_thinking_level_is_removed_after_model_resolution(self) -> None:
        config = provider_config(
            [EZAI_BANANA],
            {EZAI_BANANA: banana_provider("nano-banana-pro")},
        )
        response = self.call_image_tool(
            config,
            "generate_image",
            prompt="model capability routing",
            thinking_level="high",
        )
        self.assertNotIn("error", response)
        result = response["result"]["structuredContent"]
        self.assertNotIn("thinking_level", result["request"])
        self.assertTrue(
            any("thinking_level_user_requested was not true" in item for item in result["warnings"])
        )

    def test_explicit_incompatible_thinking_level_is_rejected_before_provider_request(self) -> None:
        config = provider_config(
            [EZAI_BANANA],
            {EZAI_BANANA: banana_provider("nano-banana-pro")},
        )
        response = self.call_image_tool(
            config,
            "generate_image",
            prompt="model capability routing",
            thinking_level="high",
            thinking_level_user_requested=True,
        )
        self.assertIn("error", response)
        self.assertIn("explicitly requested thinking_level", response["error"]["message"])
        self.assertIn("nano-banana-pro", response["error"]["message"])

    def test_batch_rejects_explicit_incompatible_thinking_before_task_submission(self) -> None:
        config = provider_config(
            [EZAI_BANANA],
            {EZAI_BANANA: banana_provider("nano-banana-pro")},
        )
        response = call_server(
            config,
            "tools/call",
            {
                "name": "generate_image_batch",
                "arguments": {
                    "dry_run": True,
                    "provider": EZAI_BANANA,
                    "jobs": [
                        {
                            "prompt": "model capability routing",
                            "thinking_level": "high",
                            "thinking_level_user_requested": True,
                        }
                    ],
                },
            },
        )
        self.assertIn("error", response)
        self.assertIn("explicitly requested thinking_level", response["error"]["message"])

    def test_unmarked_exact_size_override_is_ignored_for_image_2(self) -> None:
        response = self.call_image_tool(
            self.ordinary_config(),
            "generate_image",
            prompt="default exact-size routing",
            aspect="3:4",
            size="1024x1024",
        )
        self.assertNotIn("error", response)
        result = response["result"]["structuredContent"]
        self.assertEqual(result["request"]["size"], "2496x3312")
        self.assertTrue(
            any("size=\"1024x1024\"" in item for item in result["warnings"])
        )

    def test_ezai_under_limit_keeps_complete_prompt(self) -> None:
        response = self.call_image_tool(
            self.ezai_config(),
            "generate_image",
            prompt="完整提示",
        )
        result = response["result"]["structuredContent"]
        self.assertEqual(result["request"]["prompt"], "完整提示")
        self.assertEqual(result["revised_prompt_source"], "完整提示")
        self.assertEqual(result["source_prompt_chars"], 4)
        self.assertEqual(result["submitted_prompt_chars"], 4)
        self.assertFalse(result["prompt_policy_applied"])
        self.assertEqual(result["prompt_preparation"]["mode"], "direct_source_within_limit")
        self.assertEqual(result["prompt_preparation"]["preparation_call_count"], 0)

    def test_dalle3_profile_image_tool_rejects_direct_provider_prompt(self) -> None:
        response = self.call_image_tool(
            self.ezai_config(),
            "generate_image",
            prompt="短提示",
            provider_prompt="候选文本",
        )
        self.assertIn("error", response)
        self.assertIn('prompt_profile "dall-e3"', response["error"]["message"])
        self.assertIn("provider_prompt", response["error"]["message"])

    def test_ezai_over_limit_uses_transport_only_prompt(self) -> None:
        initial = self.start_over_limit_image_prompt("完整中文提示词")
        prepared = self.prepare_ezai_prompt(
            provider_prompt="紧凑中文",
            prompt_session_id=str(initial["prompt_session_id"]),
        )
        self.assertTrue(prepared["ready"])
        response = self.call_image_tool(
            self.ezai_config(),
            "generate_image",
            prompt_check_id=str(prepared["prompt_check_id"]),
        )
        result = response["result"]["structuredContent"]
        self.assertEqual(result["request"]["prompt"], "紧凑中文")
        self.assertEqual(result["revised_prompt_source"], "完整中文提示词")
        self.assertEqual(result["revised_prompt_submitted"], "紧凑中文")
        self.assertEqual(result["source_prompt_chars"], 7)
        self.assertEqual(result["submitted_prompt_chars"], 4)
        self.assertTrue(result["prompt_policy_applied"])
        self.assertEqual(result["prompt_preparation"]["mode"], "staged_transport")
        self.assertEqual(result["prompt_preparation"]["preparation_call_count"], 2)

    def test_ezai_preserves_over_limit_english_source_and_submits_chinese_transport(self) -> None:
        source_prompt = "Detailed English visual direction. " * 124 + "Detailed English visual direction."
        self.assertGreater(len(source_prompt), 4_000)
        transport_prompt = "完整中文"
        initial = self.start_over_limit_image_prompt(source_prompt)
        self.assertEqual(initial["status"], "english_translation_required")
        prepared = self.prepare_ezai_prompt(
            provider_prompt=transport_prompt,
            prompt_session_id=str(initial["prompt_session_id"]),
        )
        response = self.call_image_tool(
            self.ezai_config(),
            "generate_image",
            prompt_check_id=str(prepared["prompt_check_id"]),
        )
        result = response["result"]["structuredContent"]
        self.assertEqual(result["revised_prompt_source"], source_prompt)
        self.assertEqual(result["revised_prompt_submitted"], transport_prompt)
        self.assertEqual(result["request"]["prompt"], transport_prompt)
        self.assertEqual(result["source_prompt_chars"], len(source_prompt))
        self.assertEqual(result["submitted_prompt_chars"], len(transport_prompt))
        self.assertTrue(result["prompt_policy_applied"])

    def test_ezai_over_limit_requires_valid_transport_prompt(self) -> None:
        missing = self.start_over_limit_image_prompt("完整中文提示词")
        self.assertFalse(missing["ready"])
        self.assertEqual(missing["status"], "chinese_compaction_required")
        self.assertEqual(missing["source_language"]["dominant_language"], "chinese_dominant")
        self.assertFalse(missing["provider_request_sent"])
        self.assertNotIn("prompt_check_id", missing)

        oversized = self.prepare_ezai_prompt(
            provider_prompt="仍然超过限制",
            prompt_session_id=str(missing["prompt_session_id"]),
        )
        self.assertFalse(oversized["ready"])
        self.assertEqual(oversized["status"], "chinese_compaction_required")
        self.assertEqual(oversized["provider_prompt_chars"], 6)
        self.assertEqual(
            oversized["provider_prompt_language"]["dominant_language"],
            "chinese_dominant",
        )
        self.assertIn("exceeds the limit by 1", oversized["next_action"])
        self.assertFalse(oversized["provider_request_sent"])
        self.assertEqual(oversized["prompt_session_id"], missing["prompt_session_id"])

        english_missing = self.start_over_limit_image_prompt("Detailed English source prompt")
        self.assertEqual(english_missing["status"], "english_translation_required")
        self.assertIn("First translate", english_missing["next_action"])
        self.assertIn("Only compact the Chinese translation if", english_missing["next_action"])

        untranslated = self.prepare_ezai_prompt(
            provider_prompt="English candidate",
            prompt_session_id=str(english_missing["prompt_session_id"]),
        )
        self.assertEqual(untranslated["status"], "english_translation_required")
        self.assertIn("before any compaction", untranslated["next_action"])

        compacted_english_under_limit = self.prepare_ezai_prompt(
            provider_prompt="short",
            prompt_session_id=str(untranslated["prompt_session_id"]),
        )
        self.assertFalse(compacted_english_under_limit["ready"])
        self.assertEqual(
            compacted_english_under_limit["status"],
            "english_translation_required",
        )
        self.assertIn("not English compaction", compacted_english_under_limit["next_action"])

        translated_too_long = self.prepare_ezai_prompt(
            provider_prompt="完整中文译文",
            prompt_session_id=str(compacted_english_under_limit["prompt_session_id"]),
        )
        self.assertEqual(
            translated_too_long["status"],
            "translated_chinese_compaction_required",
        )
        self.assertIn("now compact that translated Chinese", translated_too_long["next_action"])

        translated_ready = self.prepare_ezai_prompt(
            provider_prompt="中文译文",
            prompt_session_id=str(translated_too_long["prompt_session_id"]),
        )
        self.assertTrue(translated_ready["ready"])
        self.assertEqual(translated_ready["status"], "ready_transport_valid")

        without_check = self.call_image_tool(
            self.ezai_config(),
            "generate_image",
            provider_prompt="完整提示",
        )
        self.assertIn("provider_prompt", without_check["error"]["message"])

        valid_check = str(translated_ready["prompt_check_id"])
        tampered = self.call_image_tool(
            self.ezai_config(),
            "generate_image",
            prompt="重复源提示",
            prompt_check_id=valid_check,
        )
        self.assertIn("do not resend prompt", tampered["error"]["message"])

        accepted = self.call_image_tool(
            self.ezai_config(),
            "generate_image",
            prompt_check_id=valid_check,
        )
        self.assertNotIn("error", accepted)
        reused = self.call_image_tool(
            self.ezai_config(),
            "generate_image",
            prompt_check_id=valid_check,
        )
        self.assertIn("invalid or expired", reused["error"]["message"])

    def test_ezai_batch_applies_policy_per_job(self) -> None:
        first_check = self.ready_prompt_check_id("完整中文提示词", "紧凑中文")
        second_check = self.ready_prompt_check_id("第二个完整提示", "第二短版")
        response = self.call_image_tool(
            self.ezai_config(),
            "generate_image_batch",
            return_when="completed",
            jobs=[
                {"prompt_check_id": first_check},
                {"prompt_check_id": second_check},
            ],
        )
        result = response["result"]["structuredContent"]
        self.assertEqual(
            [job["prompt"] for job in result["request"]["jobs"]],
            ["紧凑中文", "第二短版"],
        )
        self.assertEqual(result["revised_prompts_source"], ["完整中文提示词", "第二个完整提示"])
        self.assertEqual(result["prompt_policies_applied"], [True, True])

    def test_ezai_batch_mixes_direct_and_staged_transport_jobs(self) -> None:
        staged_check = self.ready_prompt_check_id("完整中文提示词", "紧凑中文")
        response = self.call_image_tool(
            self.ezai_config(),
            "generate_image_batch",
            return_when="completed",
            jobs=[
                {"prompt": "短提示"},
                {"prompt_check_id": staged_check},
            ],
        )
        self.assertNotIn("error", response)
        result = response["result"]["structuredContent"]
        self.assertEqual(
            [job["prompt"] for job in result["request"]["jobs"]],
            ["短提示", "紧凑中文"],
        )
        self.assertEqual(result["revised_prompts_source"], ["短提示", "完整中文提示词"])
        self.assertEqual(result["prompt_policies_applied"], [False, True])
        self.assertEqual(
            [item["mode"] for item in result["prompt_preparations"]],
            ["direct_source_within_limit", "staged_transport"],
        )

    def test_ezai_batch_stages_over_limit_jobs_before_any_provider_request(self) -> None:
        response = self.call_image_tool(
            self.ezai_config(),
            "generate_image_batch",
            return_when="completed",
            jobs=[
                {"prompt": "短提示"},
                {"prompt": "完整中文提示词"},
            ],
        )
        self.assertNotIn("error", response)
        result = response["result"]["structuredContent"]
        self.assertFalse(result["ready"])
        self.assertFalse(result["provider_request_sent"])
        self.assertTrue(result["jobs"][0]["ready"])
        self.assertFalse(result["jobs"][1]["ready"])
        self.assertIn("prompt_session_id", result["jobs"][1])

    def test_accidental_standard_ezai_edit_is_routed_internally(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            reference = Path(temporary_directory) / "reference.png"
            reference.write_bytes(b"")
            response = self.call_image_tool(
                self.ezai_config(),
                "edit_image",
                prompt="完整提示",
                images=[str(reference)],
                size="1024x1024",
            )
        self.assertNotIn("error", response)
        result = response["result"]["structuredContent"]
        self.assertEqual(result["provider"], EZAI)
        self.assertEqual(result["request"]["prompt"], "完整提示")
        self.assertFalse(result["prompt_policy_applied"])

    def test_accidental_standard_ezai_batch_is_routed_internally(self) -> None:
        response = self.call_image_tool(
            self.ezai_config(),
            "generate_image_batch",
            return_when="completed",
            jobs=[{"prompt": "完整提示"}],
        )
        self.assertNotIn("error", response)
        result = response["result"]["structuredContent"]
        self.assertEqual(result["provider"], EZAI)
        self.assertEqual(result["request"]["jobs"][0]["prompt"], "完整提示")
        self.assertEqual(result["prompt_policies_applied"], [False])


if __name__ == "__main__":
    unittest.main()
