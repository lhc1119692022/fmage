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
