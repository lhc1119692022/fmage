# Cursor compatibility

Cursor uses Fmage through its native stdio MCP support. The bridge keeps the same three Agent Skill
names as the Codex plugin:

- `fmage`
- `fmage-config`
- `fmage-image-regression`

Run `scripts/sync_cursor_skills.py` to merge the `Fmage` server into `~/.cursor/mcp.json` and render
the skills into `~/.cursor/skills/`. The generated Cursor skills replace host-specific Codex/Pi
wording while retaining the provider, prompt, quality, resolution, failure, and delivery policies.

Cursor calls the server's native tool names, such as `generate_image`, `edit_image`,
`generate_image_batch`, and `edit_image_batch`. It does not use Pi MCP Adapter's `Fmage_*` gateway
names or Pi's `mcp-cache.json`.

The MCP entry points directly at this repository's `mcp/server.mjs` and shares the existing
`~/.codex/fmage/providers.json`, so provider configuration and generated outputs remain consistent
across Codex, Pi, and Cursor.
