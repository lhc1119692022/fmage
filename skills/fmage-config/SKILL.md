---
name: fmage-config
description: Locate, show, open, create, or edit the local Fmage providers.json. Use when explicitly invoked alone, when the user asks where the config is, or when updating active providers, transports, models, provider names, or provider keys.
---

# Fmage Config

- Reply in Chinese unless the user asks otherwise.
- If invoked with no extra request, do only the default action.

## Default action

Resolve the effective `providers.json` path and immediately reply with a clickable local file link plus the raw path. Do not say the skill is loaded.

Path resolution:
1. Use non-empty `FMAGE_CONFIG` exactly.
2. Else use non-empty `CODEX_HOME`.
3. Else use the current user's home directory plus `.codex`.
4. Unless step 1 was used, append `fmage/providers.json`.

Output:
```text
providers.json: [providers.json](ABSOLUTE_PATH_WITH_FORWARD_SLASHES)
路径: ABSOLUTE_NATIVE_PATH
```

If `FMAGE_CONFIG` was used, add one short note that it overrides the default.

## Edit rules

- Before editing, inspect the file and preserve existing providers.
- If missing and creation is requested, copy from plugin `config/providers.example.json`.
- Edit non-secret fields normally: `active_providers`, legacy `active_provider`, `transport`,
  `transport_profile`, `base_url`, `model`, `response_format`, `timeout`, and provider names.
  `transport: "808-openai-images"`, `compatibility`, `compatibility_profile`, `prompt_profile`,
  and `prompt_policy` are obsolete; remove them before use.
- The primary transports are `openai-images`, `ezai-banana-images`,
  `gemini-generate-content`, and `zenmux-vertex`. Use `gemini-generate-content` for providers that
  expose Google's native `/v1beta/models/{model}:generateContent` protocol; it supports generation
  and edits through text and inline image parts.
  `transport_profile: "808"` may be attached only to `openai-images` and supplies the relay-specific
  asynchronous submission and polling contract.
  `response_format: "url"`, and `timeout: 600`. Preserve the existing `api_key` or `api_key_env`.
- Providers use the normal direct prompt path. Do not configure prompt-profile or prompt-preparation fields.
- Never print existing API keys or ask the user to paste keys into chat; tell them to edit keys directly in `providers.json`.
