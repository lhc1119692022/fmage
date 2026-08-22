# Fmage Local Runtime Lifecycle

After modifying any project file that affects the plugin, transports, skills, tests, or
compatibility layers, refresh the local runtime before reporting completion:

```text
python scripts/refresh_local_runtime.py
```

The refresh must update the local `providers.json` compatibility fields without printing API keys,
reinstall the local Codex plugin through the user-writable Codex app-server CLI, and synchronize
the default Pi skill root. Use `--check` to verify the state without changing files.

Keep unrelated pre-existing working-tree changes out of commits. After the refresh, verify that the
plugin is installed and enabled at the manifest version, Pi reports no compatibility drift, and the
local configuration contains the current provider schema.
