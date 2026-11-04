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

`skills/` and shared MCP/transports are the source of truth. Build the DeepSeek Desktop artifact
with `scripts/harness_plugins.py`; never restore Fmage under `~/.agents/skills` or a standalone
MCP registration. DeepSeek uses a native Cordis bundle in the Desktop profile only; VS Code
support is retired and must not be reintroduced.
`refresh_local_runtime.py` also synchronizes the registered host-private payload and package.
Check the adapter with `--check`, and distinguish installed files from a running host's loaded
runtime. Never kill an active image request to reload an adapter. Keep package output, backups,
local installation paths, and credentials outside this repository.

Each host owns its data directory: DeepSeek keeps `providers.json` under `~/.dsh/fmage`, Codex
under `~/.codex/fmage`. Do not add code that searches for, copies, or falls back to another
host's file; `FMAGE_CONFIG` is the only explicit override.
