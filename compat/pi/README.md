# Fmage compatibility for Pi/Pix

This directory defines the host-facing compatibility contract for Pi and Pix.

## Codex → Pi/Pix name mapping

| Codex concept | Pi/Pix equivalent |
| --- | --- |
| `mcp__Fmage.generate_image` | `mcp({ server: "Fmage", tool: "Fmage_generate_image", args: { ... } })` |
| `mcp__Fmage.edit_image` | `mcp({ server: "Fmage", tool: "Fmage_edit_image", args: { ... } })` |
| `mcp__Fmage.generate_image_batch` | `mcp({ server: "Fmage", tool: "Fmage_generate_image_batch", args: { ... } })` |
| `mcp__Fmage.edit_image_batch` | `mcp({ server: "Fmage", tool: "Fmage_edit_image_batch", args: { ... } })` |
| `get_image_task_status` | `Fmage_get_image_task_status` |
| `get_provider_status` | `Fmage_get_provider_status` |
| active Codex model | active Pix/Pi model |

The underlying provider names, model IDs, output paths, manifests, and
`requested_size` semantics are shared. Only the host bridge, host wording, and
final Markdown rendering instructions change.

## Workspace-aware path policy

A project-attached task is helpful but not required. When Pix provides a project
workspace root, convert Fmage's absolute paths to paths relative to that root.
When no project is attached, preserve Fmage's absolute paths. Do not resolve
them against Pix's conversation-storage directory, and do not replace them
with `file://` URLs. Before putting a Windows absolute path in Markdown,
percent-encode its drive colon and reserved characters, for example
`D%3A/Downloads/image%20name.png`.

When the user has not explicitly named a save folder, omit `output_dir` so the
Fmage configuration's `output_dir` is authoritative. The MCP server also
ignores the known Pix project-less conversation-storage path
(`Documents/Pix/conversations`) when it is accidentally passed as an override;
other explicitly requested custom folders remain supported.

## Completed output contract

Pi/Pix should use the text portion of Fmage's MCP result and render the local
paths in the final assistant message. Fmage provides:

1. normalized display paths that can be rendered as Markdown image previews;
2. requested and actual dimensions;
3. a warning whenever the returned dimensions differ from the requested size;
4. the saved image path for a file-location link;
5. the manifest path containing the generation record (and a task-state path
   for asynchronous jobs when available).

The agent, not the MCP server, selects the destination form: workspace-relative
when a project root exists, absolute when it does not.

The three output mappings are:

```text
display_images[]          -> ![Preview](relative/path/to/image.png)
images[]                  -> [图片所在位置](dirname(relative/path/to/image.png))
display_manifest(s) / manifest -> [生成记录所在位置](dirname(relative/path/to/manifest.json))
```

Keep the response order as preview first, compact image information second, and
the two location links last. If quality and resolution are shown, render them
on one line (`生成质量/分辨率：low / 1k`); if that cannot be kept on one line,
omit both display fields instead of splitting them into separate bullets. The
location links intentionally point to parent directories so Pix opens the file
explorer at the saved files rather than opening the files themselves. In the
current Pix build, this directory link does not select the child file: Markdown
links use `workspace.openFile`/Electron `shell.openPath`, while the existing
selection behavior lives behind the separate `revealInFolder` API. Enabling
selection needs a small Pix host-side link action; it cannot be expressed by
AGENTS.md, Skill text, or an MCP path mapping alone.

With a project root, convert Windows absolute paths to forward-slash relative
paths; `..` segments are valid when Fmage stores an artifact outside the
workspace. Without a project root, keep the absolute forward-slash path such
as `D%3A/Downloads/image.png`. Percent-encode spaces and other reserved
characters as well. Do not use `file://` in Pix Markdown.

Run `scripts/sync_pi_skills.py` to copy the host-compatible skills and add or
replace the Fmage section in the Pi/Pix `AGENTS.md` file. When an existing
`mcp.json` and `mcp-cache.json` are present, it can refresh the live Fmage tool
metadata; it does not rewrite the MCP server configuration.
