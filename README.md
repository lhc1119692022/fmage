禁用 chatgpt app 原生生图 skill 并向个性化指令中添加下面的内容

## Image Generation And Editing

- For raster image generation, image editing, color correction, color optimization, retouching, background changes, style transfer, or any other task that produces or edits an image, use the Fmage plugin.
- Load and follow the `fmage:fmage` skill, then call `mcp__Fmage.generate_image`, `mcp__Fmage.edit_image`, `mcp__Fmage.generate_image_batch`, or `mcp__Fmage.edit_image_batch` as appropriate.
- Do not use Codex native `image_generation_call`, the `~/.codex/generated_images` output path, or a disabled generic image generation skill for these tasks.
- Do not use Shell/Python/PIL/ImageMagick scripts as a fallback for image generation or image editing unless the user explicitly asks for deterministic local pixel processing.
- If Fmage tools are not visible, first run proper tool discovery and `get_provider_status`. If the tools still cannot be called, report the blocker instead of silently switching to another image tool or local script.
- After any Fmage image call fails or returns partial results, stop and report the failure. Do not retry, change parameters, query providers to find an alternative, or switch providers unless the user explicitly asks or approves it.
- Treat Fmage `1k`/`2k`/`3k`/`4k` values as resolution tiers. For `openai-images`, the normalized `requested_size` is authoritative; when actual dimensions match it and no size warning is present, do not describe the output as undersized or compare it with `4096x4096`.

## Image Entry Points

The plugin exposes five independent skills while sharing one MCP server and one provider/transport layer:

- `Fmage 配置` (`fmage-config`) manages local provider configuration.
- `Fmage 图像生成` (`fmage`) is the default image entry and keeps the original understanding-and-expansion behavior.
- `Fmage 直传生图` (`fmage-direct`) is explicit-only. Invoke `/Fmage 直传生图` or `$fmage-direct` before the image request to preserve the prompt without expansion, translation, polishing, or paraphrase.
- `Fmage 图片退步` (`fmage-image-regression`) runs the fixed local regression pipeline.
- `Fmage 工作流` (`fmage-workflow`) runs configured RunningHub workflows.

The selected image entry is request-local and must be chosen before prompt interpretation or attachment inspection. Image task results use neutral fields such as `prompt_submitted`, `prompts_submitted`, and `prompt_mode`; legacy `revised_*` fields remain readable for older manifests.

## Local Runtime Refresh

After changing the plugin manifest, skills, MCP server, or local transport code, refresh the installed cache and run the keyless MCP handshake:

```text
python scripts/refresh_local_runtime.py
```

Use `python scripts/refresh_local_runtime.py --check` for a read-only verification. The host keeps skill and MCP catalogs per task, so start a new Codex task after a refresh; this does not resubmit any image request.

Providers select a protocol: openai-images, json-images, gemini-generate-content, or midjourney.
There are no channel-specific execution profiles or model allowlists for OpenAI Images.
For asynchronous OpenAI Images APIs, configure async_mode=true; this submits with
async=true and polls /images/tasks/{task_id}. Configure image_field as auto,
image, or image[] to match the edit API before submission. Names and hostnames do not select behavior.
Gemini supports explicit auth_scheme values x-goog-api-key (default) and bearer.
Explicit /v1 and /v1beta paths are preserved; an unversioned base URL uses /v1beta.
generation_config_format selects image-config (default, imageConfig) or response-format
(responseFormat.image, used by the supplied v1 documentation). The example 2.1 provider uses the latter.

Midjourney uses one independent v8.2 adapter and a shared protocol contract, based on the
high-speed API with the stable documentation as supplementary information. Configure provider
KC-MJ with transport=midjourney, base_url=https://newapi.prompt-hubs.com, and
model=Midjourney v8.2 高速. Keep the API key in the local providers.json. The configured model
ID is an opaque server identifier: it is sent unchanged, without a local model allowlist or
model-dependent routing. Switching to mj-v8.2 or another server-side ID does not change the
client contract. The server selects the upstream. All requests submit JSON to
/v1/midjourney/generations and query /v1/tasks/{task_id}; no automatic model fallback occurs.
Generation controls remain in prompt, including --ar, --raw, --stylize, --q and unknown flags.
Existing --ar wins over natural-language ratios. No size, quality, resolution, n, or raw JSON
controls are added. MJ has native pixel dimensions, not configurable 2K/4K tiers. Reference
generation adds only image/images, preserving URL/data-URL/local-file input order (5 maximum).
Local files become data URLs; edit and generate use the same generation endpoint.
Post-generation actions are not implemented.

Midjourney saves every returned single-image slot (normally 4), records an optional grid
separately, and exposes count mismatches. Pending/query/download failures keep remote-task.json
without resubmitting. Explicit get_image_task_status(provider, remote_task_id) retrieves the
same task. Saved task identity retains the original model across model switches and requires
the original service and credential. Retrieval is not a new generation request.

Nano Banana capabilities are shared across protocols and preserve the configured model ID:

| Family | Accepted model IDs | Resolution tiers | Thinking |
| --- | --- | --- | --- |
| Pro | nano-banana-pro, gemini-3-pro-image, gemini-3-pro-image-preview | 1K, 2K, 4K | No exposed control |
| 2 | nano-banana-2, gemini-3.1-flash-image, gemini-3.1-flash-image-preview | 512px, 1K, 2K, 4K | minimal (default), high |
| 2.1 | gemini-nano-banana-2.1, nano-banana-2.1 | 1K, 2K, 4K | minimal, medium (default), high |

The capability table follows the supplied Nano Banana documentation. Other Nano Banana families
are not supported. Alias recognition does not rewrite the model sent to a relay.

Omitted timeout values use runtime defaults. Explicit positive request timeouts override provider
settings without a minimum floor. Explicit budgets are shared by reference download, submission,
polling and delivery; the helper allows a further 60 seconds for cleanup only when a budget exists.
Refresh removes the former forced timeout value 600 and never inserts a replacement.
Status queries retain a 30-second cap and batch/workflow foreground waits retain a 40-second window.

Async tasks are queried immediately. No provider error resubmits generation or strips request fields.
Submission connection loss before a task ID is received records submission_uncertain and remote_status=unknown.
Once the existing full task ID is known, get_image_task_status with provider and remote_task_id
retrieves it using GET only; it never generates again. Local tasks still use task_id.
A keyless dry_run validates routing without contacting a provider.

The plugin card supports at most three `interface.defaultPrompt` entries. Those card actions are separate from the five skill entry points above, which are declared by each skill's `agents/openai.yaml`. To prevent a missing Fmage MCP catalog from silently falling back to native image generation, keep the generic image skill disabled and disable the host feature as well:

```text
codex features disable image_generation
```
# RunningHub 图片工作流

使用快捷命令 **Fmage 工作流**（`$fmage-workflow`）。默认配置“SeedVR2 放大”，分辨率档位为4（4K），
附上图片并选择命令即可直接运行，无需额外提示词或确认；未指定参数使用配置中的默认值。
明确要求列出、配置或查询任务时仅执行该操作；缺少必需图片时才询问图片。
在本机 providers.json 的 `workflow_connections.runninghub.api_key` 填入密钥即可。
不必把密钥发送到聊天。配置示例见 `config/workflows.example.json`，首次刷新自动补充缺失字段。

- “用SeedVR2放大这张图，6K”：仅本次使用 resolution_k=6，未指定时使用配置默认值4。
- “列出工作流”：显示名称和输入参数。
- “把默认工作流改为某名称”：更新 `active_workflow`。
- “继续获取任务 request_id 的图片”：恢复结果查询，不重新提交付费任务。

每个工作流保存独立 ID、输入节点映射和 `output_node_ids`。多个工作流共用 connection。
仅在节点结构一致时可以只替换 ID。输入支持 PNG/JPEG/WebP（每张最多 30 MB）；
输出支持 PNG/JPEG/WebP（每张最多 100 MB），按配置节点收取全部图片并保留原始字节。
输出使用本次 output_dir、全局 output_dir 或配置目录下 outputs/workflows，图片直接保存到该目录，
使用任务编号、图片序号和随机后缀避免重名，不再创建任务子文件夹。已保存的历史路径保持有效。
status 调用内部每10秒查询一次，等待窗口约40秒，省去对话中的单独 sleep 命令。
PNG 结果同时返回宽高和格式，避免再执行命令读取尺寸。
任务记录位于配置目录 workflow-tasks，不保存密钥。返回本地文件路径供对话预览和打开。
提交遇到不确定网络错误不会自动重发；先在 RunningHub 任务记录中核对任务 ID。
下载失败保留已保存图片和任务 ID，经用户确认后重取结果。结果链接失效或平台清理结果后，
不能保证恢复，所以成功后应及时下载。第一版不支持视频、音频、加密工作流密码和输出重定向。
测试使用模拟 API，不代表已对账号权限、模型环境和真实 CDN 完成验证。

## VS Code adapter

VS Code's MCP integration discovers Fmage tools but does not automatically load the Codex plugin manifest or its skills. Install the VS Code adapter to synchronize adapted skills and configure the local MCP server without replacing other servers:

```text
python scripts/install_vscode_adapter.py
```

Use `python scripts/install_vscode_adapter.py --check` for a read-only check. See `vscode/README.md` for the supported paths, natural-language entry points, and reload step.
