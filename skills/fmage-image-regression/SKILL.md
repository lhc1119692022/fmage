---
name: fmage-image-regression
description: "Use for ‘Fmage 图片退步’: run the bundled skill script once to apply the fixed 1K-area cap, 1.2 px blur, and 0.5% monochrome noise, then return its result."
---

# Fmage 图片退步

Say `处理中。`, then run the bundled `scripts/degrade_image.py` exactly once with the attachment path as `--input`. Resolve the script path relative to this `SKILL.md`, enable UTF-8 Python I/O, and return the script's stdout verbatim. Do not load the main `fmage` skill, call an MCP image tool, retry, or perform additional image processing.
