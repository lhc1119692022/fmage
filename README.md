禁用 chatgpt app 原生生图 skill 并向个性化指令中添加下面的内容

## Image Generation And Editing

- For raster image generation, image editing, color correction, color optimization, retouching, background changes, style transfer, or any other task that produces or edits an image, use the Fmage plugin.
- Load and follow the `fmage:fmage` skill, then call `mcp__Fmage.generate_image`, `mcp__Fmage.edit_image`, `mcp__Fmage.generate_image_batch`, or `mcp__Fmage.edit_image_batch` as appropriate.
- Do not use Codex native `image_generation_call`, the `~/.codex/generated_images` output path, or a disabled generic image generation skill for these tasks.
- Do not use Shell/Python/PIL/ImageMagick scripts as a fallback for image generation or image editing unless the user explicitly asks for deterministic local pixel processing.
- If Fmage tools are not visible, first run proper tool discovery and `get_provider_status`. If the tools still cannot be called, report the blocker instead of silently switching to another image tool or local script.
- After any Fmage image call fails or returns partial results, stop and report the failure. Do not retry, change parameters, query providers to find an alternative, or switch providers unless the user explicitly asks or approves it.
- Treat Fmage `1k`/`2k`/`3k`/`4k` values as resolution tiers. For `openai-images` and `json-images`, the normalized `requested_size` is authoritative; when actual dimensions match it and no size warning is present, do not describe the output as undersized or compare it with `4096x4096`.

## Image Entry Points

The plugin exposes four independent skills while sharing one MCP server and one provider/transport layer:

- `Fmage 配置` (`fmage-config`) manages local provider configuration.
- `Fmage 图像生成` (`fmage`) is the default image entry and keeps the original understanding-and-expansion behavior.
- `Fmage 直传生图` (`fmage-direct`) is explicit-only. Invoke `/Fmage 直传生图` or `$fmage-direct` before the image request to preserve the prompt without expansion, translation, polishing, or paraphrase.
- `Fmage 图片退步` (`fmage-image-regression`) runs the fixed local regression pipeline.

The selected image entry is request-local and must be chosen before prompt interpretation or attachment inspection. Image task results use neutral fields such as `prompt_submitted`, `prompts_submitted`, and `prompt_mode`; legacy `revised_*` fields remain readable for older manifests.
