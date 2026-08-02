---
name: fmage-image-regression
description: "Use for ‘Fmage 图片退步’: run the bundled skill script once to apply the fixed 1K-area cap, 1.2 px blur, and 0.5% monochrome noise, then present the processed image without exposing raw script metadata."
---

# Fmage 图片退步

Say `处理中。`, then run the bundled `scripts/degrade_image.py` exactly once with the attachment path as `--input`. Resolve the script path relative to this `SKILL.md`, enable UTF-8 Python I/O, capture stdout, and parse it as JSON.

On success, treat `output` as authoritative. Never expose the raw JSON unless the user asks. Return one short completion sentence and the output path. In Codex desktop or another host that supports absolute local-image Markdown, normalize only the display path to forward slashes and add an inline image preview. In Pi/Pix, follow its local-artifact rules: use relative Markdown only when immediately derivable; otherwise show the absolute path as code without a preview. On failure, report stderr and stop.

Do not load the main `fmage` skill, call an MCP image tool, retry, or perform additional image processing.
