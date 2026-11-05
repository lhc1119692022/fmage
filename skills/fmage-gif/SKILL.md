---
name: fmage-gif
description: Explicit-only Fmage GIF entry. Generate one animated GIF with the generate_gif tool and a gpt-image-2.6 series model. Never select it for ordinary image requests.
---

# Fmage GIF

## Entry Selection

- Use this skill only when the user explicitly invokes `/Fmage GIF` or `$fmage-gif` for the current request. Select it before any prompt interpretation. Do not load the `fmage` or `fmage-direct` policies for that request.
- A request that merely mentions animation or motion without the explicit entry stays with the default `fmage` entry, which produces still images.
- The selection applies only to the current request; it is not a persistent setting.

## Tool Call

- Call `generate_gif` once with `n=1`; there is no batch path.
  - Without reference images it uses the generations endpoint.
  - When the user supplies reference images (or attaches images to the request), pass their paths or URLs in `images`; the tool then uses the image edits endpoint, like `edit_image`. Pass `use_latest: true` only when the user explicitly asks to animate the latest still image.
  - In the prompt, say how the references are used (subject, style, or starting frame) and describe the motion.
- The tool requires an `openai-images` provider with a `gpt-image-2.6` series model (for example `gpt-image-2.6-sunburst` or `gpt-image-2.6-flare`). Omit `provider` unless the user names one. If the tool rejects the provider, report the error; do not switch providers on your own.
- Animation parameters:
  - Defaults when the user specifies nothing: 16 frames, 80 ms per frame (about 1.3 seconds). Omit both fields to use them.
  - A frame count (`12帧`) maps to `frame_count` (2–16).
  - A length in seconds (`1秒`) maps to `duration_seconds`; the server converts it at 12 fps. Never pass both `frame_count` and `duration_seconds`. The server rejects a conversion above 16 frames (more than about 1.3 seconds) or below 2 frames before any provider call; report that error instead of picking another value. To make a longer loop, the user can raise `frame_duration_ms` instead.
  - A per-frame time or speed in milliseconds maps to `frame_duration_ms` (20–2000, rounded to 10).
- Frame size is always `auto`; the tool has no size, aspect, or resolution argument. Ignore user frame-size requests and tell the user the API chooses the frame size within its total pixel budget (8,294,400 pixels per request).
- `quality`: omit it unless the user selects a tier; pass `quality_user_requested: true` when the selected tier differs from `high`.
- GIF has no streaming, partial images, or custom compression. The global `complexity` setting is applied by the server; never pass it.

## Prompt Strategy

- Write one complete prompt in the user's language. Keep every element the user asked for.
- Describe the subject, the motion across the frames, and how the loop closes (for example, the subject returns to its starting pose). Add a seamless loop and a stable camera/background when the user left them open. Keep the motion simple enough for the frame count.

## Delivery

- Reply in Chinese unless asked otherwise.
- The result is never written to `latest.json`, so it cannot be used as `use_latest` input for edits.
- Show the result with Markdown from `display_images`, for example `![GIF](C:/Users/name/outputs/20260101-120000-001.gif)`, so hosts that render GIF show the animation. Always also give the saved file path, and the manifest path. Report provider/model, frame count, and frame duration from the returned request.
- Treat a failed call as terminal: report the error and stop. Do not retry, change parameters, or resubmit unless the user asks.
