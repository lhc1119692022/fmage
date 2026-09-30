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

The plugin exposes four independent skills while sharing one MCP server and one provider/transport layer:

- `Fmage 配置` (`fmage-config`) manages local provider configuration.
- `Fmage 图像生成` (`fmage`) is the default image entry and keeps the original understanding-and-expansion behavior.
- `Fmage 直传生图` (`fmage-direct`) is explicit-only. Invoke `/Fmage 直传生图` or `$fmage-direct` before the image request to preserve the prompt without expansion, translation, polishing, or paraphrase.
- `Fmage 图片退步` (`fmage-image-regression`) runs the fixed local regression pipeline.

The selected image entry is request-local and must be chosen before prompt interpretation or attachment inspection. Image task results use neutral fields such as `prompt_submitted`, `prompts_submitted`, and `prompt_mode`; legacy `revised_*` fields remain readable for older manifests.

## Local Runtime Refresh

After changing the plugin manifest, skills, MCP server, or local transport code, refresh the installed cache and run the keyless MCP handshake:

```text
python scripts/refresh_local_runtime.py
```

Use `python scripts/refresh_local_runtime.py --check` for a read-only verification. The host keeps skill and MCP catalogs per task, so start a new Codex task after a refresh; this does not resubmit any image request.

Provider `timeout` and the overall image-task budget have a 600-second minimum; larger values are preserved. Reference downloads, submission, polling, and result delivery share that budget, so each HTTP operation uses only the remaining time. Status queries are capped at 30 seconds. A slowly streamed response also consumes the same budget. The helper process keeps an additional 60-second cleanup allowance. Batch foreground waits and workflow status polling windows are at most 40 seconds; a running image batch continues in the background. Completed workflow output downloads retain their 600-second request allowance. Refresh adds or raises provider `timeout` fields in the local configuration, and dry-runs/provider status report the configured request timeout.

Asynchronous image tasks are queried immediately after their ID is received. Generic OpenAI-compatible polling then uses 5-second intervals for the first 120 seconds of polling and 10-second intervals thereafter; 808 keeps its 5-second interval. 808 status queries retrieve base64 results directly while preserving the configured submission response format. Provider errors do not trigger another generation/edit POST, parameter stripping, or multipart-field switching. For generic `openai-images` providers, an optional provider `image_field` setting selects `auto`, `image`, or `image[]` before the first edit submission. 808 retains its fixed `image[]` contract. Resolve compatibility before a new user-authorized request.

An 808 connection can close before that timeout even when the server has accepted the task. A submission failure before the task ID arrives now records `submission_uncertain` and `remote_status=unknown` in `remote-task.json`, with stage timing preserved in batch results. No generation is resubmitted. Once the full existing task ID is known, explicitly retrieve it with `get_image_task_status({"provider":"808-image","remote_task_id":"task_..."})`; this uses GET queries and defaults to base64 results. The original prompt, model, and delivery settings are not assumed from the current provider configuration. Local Fmage task status remains available through `task_id`. Use `dry_run=true` for a keyless remote-routing check.

The plugin card supports at most three `interface.defaultPrompt` entries. Those card actions are separate from the four skill entry points above, which are declared by each skill's `agents/openai.yaml`. To prevent a missing Fmage MCP catalog from silently falling back to native image generation, keep the generic image skill disabled and disable the host feature as well:

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
