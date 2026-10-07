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

- Workflow configuration shares this file: `workflow_connections` holds RunningHub base_url and
  api_key/api_key_env; `workflows` holds named workflow_id, connection, input mappings, output_node_ids
  and optional instance_type; `active_workflow` selects the default. Preserve these sections during
  provider edits. Never print connection keys. See config/workflows.example.json for a keyless example.

- Before editing, inspect the file and preserve existing providers.
- If missing and creation is requested, copy from plugin `config/providers.example.json`.
- Edit non-secret fields normally: active providers, transport, base_url, model, response_format,
  async_mode, auth_scheme, generation_config_format, image_field, timeout, and provider names.
- Supported protocols are openai-images, json-images, and gemini-generate-content.
  Choose the protocol from the provider's API contract, never its name or hostname.
  Old channel transport names and transport_profile are migration inputs only; do not create them.
- For OpenAI Images async submission and /images/tasks/{task_id} polling, set async_mode=true.
  Any provider/model may use this protocol. image_field accepts auto, image, or image[].
- Gemini uses x-goog-api-key by default; set auth_scheme=bearer when required by the API.
  Explicit /v1 and /v1beta base paths are preserved; unversioned URLs default to /v1beta.
  generation_config_format defaults to image-config (generationConfig.imageConfig).
  Set response-format for APIs using generationConfig.responseFormat.image, as in the supplied v1 documentation.
- Nano Banana supports only Pro, 2, and 2.1. Aliases share capabilities across transports:
  nano-banana-pro and gemini-3-pro-image[-preview]; nano-banana-2 and
  gemini-3.1-flash-image[-preview]; nano-banana-2.1 and gemini-nano-banana-2.1.
  Always preserve the configured wire model ID. Pro supports 1K/2K/4K; 2 adds 512px;
  2.1 supports 1K/2K/4K and minimal/medium/high thinking (default medium).
- Omit timeout to use runtime defaults. Explicit positive values are preserved and request-level
  values override provider values. Migration removes the former forced 600 value; it never adds
  a timeout. Explicit budgets cover download, submission, polling and delivery together.
  Status queries retain a 30-second cap; foreground batch/workflow waits retain their 40-second window.
- Preserve api_key/api_key_env. Do not configure removed prompt-preparation fields.
  Provider errors stop the attempt; never strip fields or change multipart fields and resubmit.
- Never print existing API keys or ask the user to paste keys into chat; tell them to edit keys directly in `providers.json`.
