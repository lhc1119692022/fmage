---
name: fmage-config
description: Locate, show, open, create, or edit the local Fmage providers.json. Use when explicitly invoked alone, when the user asks where the config is, or when updating active_provider, base_url, model, provider names, DALL-E 3 compatibility, or provider keys.
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
- Edit non-secret fields normally: `active_providers`, legacy `active_provider`, `transport`, `base_url`,
  `model`, `response_format`, `timeout`, `compatibility`, `compatibility_profile`, `prompt_policy`, and
  provider names.
- A provider named `DALLE3` automatically uses DALL-E 3 compatibility. Any other provider name may opt
  in with `"compatibility": "dalle3"`; this profile is independent of `transport`, `base_url`, and `model`.
  When `prompt_policy` is omitted, the profile defaults to a 4000-character limit with a 3900-character
  target. Providers without this profile, including `ezai-image-2`, always use the normal prompt path.
- Never print existing API keys or ask the user to paste keys into chat; tell them to edit keys directly in `providers.json`.
