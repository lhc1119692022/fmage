# Diagnostics And Setup

- A failed or partial image call is terminal for the current attempt. Stop and report it. Do not retry the same provider, change parameters and retry, select another provider, or query status/discovery tools to find an alternative provider. Only make another image call after the user explicitly asks for or approves it.
- For `ezai-image-2` only, `ready=false` with `provider_request_sent=false` means the complete source has already been staged and found over the limit; it is pre-request prompt preparation, not an image failure. Continue with the returned `prompt_session_id` and follow `next_action`. When that already-finalized staged source is English-dominant, translate it to Chinese first and compact only if the Chinese result remains over the limit. Submit the final `prompt_check_id` without resending either prompt.
- A submitted asynchronous task is not a retry. Use `get_image_task_status` only to follow that same task; never submit a replacement merely because it remains pending.
- Use `trace_image_job_plan`, `get_provider_status`, `verbose: true`, `dry_run`, or `embed_images: "auto"` only when the user requests their specific behavior or diagnosis requires it.
- For failed or partial image requests, lead with the error or warning and omit success-only fields that are not actionable.
- Use `get_provider_status` for provider/config questions. Never print API keys.
- If setup is required, relay the config path and required fields in Chinese. Configuration stays outside the plugin at `~/.codex/fmage/providers.json`.
