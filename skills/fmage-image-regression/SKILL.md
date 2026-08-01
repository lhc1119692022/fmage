---
name: fmage-image-regression
description: "Use for ‘Fmage 图片退步’: run the dedicated local regress_image tool once to apply the fixed 1K-area cap, 1.2 px blur, and 0.5% monochrome noise, then return its result."
---

# Fmage 图片退步

Say `处理中。`, call `mcp__Fmage.regress_image` once with the attachment path as `image`, then return the tool text verbatim. Do nothing else.
