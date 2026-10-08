# Fmage Local Runtime Lifecycle

After modifying any project file that affects the plugin, transports, skills, tests, or
configuration migration, refresh the local runtime before reporting completion:

```text
python scripts/refresh_local_runtime.py
```

The refresh must update the local `providers.json` migration fields without printing API keys and
reinstall the local ChatGPT/Codex app plugin through the user-writable Codex app-server CLI. Use
`--check` to verify the state without changing files.

Keep unrelated pre-existing working-tree changes out of commits. After the refresh, verify that the
plugin is installed and enabled at the manifest version and that the local configuration contains
the current provider schema.

## Host-private plugin adapters

`skills/` and shared MCP/transports are the source of truth. Build VS Code and DeepSeek
Desktop artifacts with `scripts/harness_plugins.py`; never restore Fmage under `~/.agents/skills`
or a standalone VS Code user MCP registration. VS Code uses a native agent plugin registered
through `chat.pluginLocations`; DeepSeek uses a native Cordis bundle in the Desktop profile only.
`refresh_local_runtime.py` also synchronizes registered host-private payloads and packages.
Check both adapters with `--check`, and distinguish installed files from a running host's loaded
runtime. Never kill an active image request to reload an adapter. Keep package output, backups,
local installation paths, and credentials outside this repository.
