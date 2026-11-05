---
name: fmage-svg
description: Explicit-only Fmage SVG entry. Generate one static SVG vector graphic (icon, logo, illustration) with the generate_svg tool and a gpt-image-2.6 series model. Never select it for ordinary image requests.
---

# Fmage SVG

## Entry Selection

- Use this skill only when the user explicitly invokes `/Fmage SVG` or `$fmage-svg` for the current request. Select it before any prompt interpretation. Do not load the `fmage` or `fmage-direct` policies for that request.
- A request that merely mentions a vector, logo, or icon without the explicit entry stays with the default `fmage` entry, which produces raster images.
- The selection applies only to the current request; it is not a persistent setting.

## Tool Call

- Call `generate_svg` once. It generates on the generations endpoint with `n=1`; there is no SVG edit, batch, or reference-image path. If the user asks to edit an SVG or supplies reference images, explain that the API does not support SVG edits or SVG/reference inputs and stop.
- The tool requires an `openai-images` provider with a `gpt-image-2.6` series model (for example `gpt-image-2.6-sunburst` or `gpt-image-2.6-flare`). Omit `provider` unless the user names one. If the tool rejects the provider, report the error; do not switch providers on your own.
- `quality` controls the internal reference image the SVG is traced from. Omit it unless the user selects a tier; pass `quality_user_requested: true` when the selected tier differs from `high`.
- The tool takes no format, compression, background, or transparency arguments. SVG has no streaming, partial images, or `output_compression`. Size is not checked.
- The global `complexity` setting from `providers.json` is applied by the server; never pass it.

## Prompt Strategy

- Write one complete prompt in the user's language. Keep every element the user asked for: subject, text/wordmark (exact spelling), colors, style.
- Add only what an SVG needs when the user left it open: a clean flat vector style, a limited solid-color palette, clear geometric shapes, a centered composition on a plain background, and legible text. Do not add photographic, texture, gradient-heavy, or raster-effect language.
- The returned document is static vector content without scripts, external resources, or embedded raster images; do not ask for animation or photos.

## Delivery

- Reply in Chinese unless asked otherwise.
- The result is never written to `latest.json`, so it cannot be used as `use_latest` input for edits.
- Show the result with Markdown from `display_images`, for example `![SVG](C:/Users/name/outputs/20260101-120000-001.svg)`, so hosts that render SVG show a preview. Always also give the saved file path, and the manifest path. Report provider/model. SVG has no pixel dimensions to report.
- Treat a failed call as terminal: report the error and stop. Do not retry, change parameters, or resubmit unless the user asks.
