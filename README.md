# Fmage：DeepSeek Harness Desktop 图片插件

Fmage 是面向 DeepSeek Harness Desktop 的图片生成与编辑插件，提供五个原生技能
（生图、直传生图、配置、RunningHub 工作流、图片退步）和十个 `mcp__Fmage__*` 工具。
DeepSeek Desktop 是主要宿主，功能与验证都以它为基准；Codex 和 VS Code 智能体继续作为兼容宿主，
构建与安装见 [宿主适配](adapters/README.md)。

## DeepSeek Desktop 安装与刷新

```text
python scripts/refresh_local_runtime.py          # 刷新配置、重建并安装 Desktop 包（同时同步兼容宿主）
python scripts/refresh_local_runtime.py --check  # 只读校验安装、包内容和隔离加载
```

安装只写入 `~/.dsh/profiles/desktop`。Desktop 运行中时，刷新走宿主自己的热插拔：先卸载插件释放目录，
再用登记的运行时安装，最后重新选入组合包，宿主加载新文件后由心跳确认新版本生效，无需重启，
也不会中断正在进行的图片请求（但刷新期间 Fmage 工具会短暂下线，别在生图请求执行中刷新）。
宿主的 HMR 只响应组合包列表变化，所以外部 pnpm 直接替换目录既不生效、还会因目录被占用而失败。

## 设置页面：Fmage 生图

在 Desktop 的「设置」左侧导航中打开「Fmage 生图」，即可可视化编辑 `providers.json`：

- 提供商：添加、删除、启用/关闭，拖动或置顶调整优先级（启用列表从上到下即 `active_providers` 顺序，
  第一个为默认）。也可以直接拖动卡片左侧手柄改顺序，或点卡片右侧的置顶按钮把该提供商移到最前。
  卡片默认折叠，只显示优先级、名称与 transport/model 摘要；展开后名称与 transport 同行，其余是
  Base URL、Model 和 API Key。只显示 `transport`（下拉选择）、`base_url`、`model` 和 `api_key`，
  其他字段保存时原样保留。
- Model 输入框右侧的拉取按钮会按当前 Base URL 和 API Key 请求上游模型列表，结果显示在输入框下方；
  点击某个模型即可切换，输入框本身仍可手动编辑。上游不可用或返回 404 时会明确提示，不会阻塞手工填写。
- API Key 默认以首尾字符加星号显示，点击眼睛图标才向本机 Host 读取并显示完整值；留空不改动即保留原密钥。
- 工作流：管理 RunningHub 连接（仅允许 runninghub.cn / runninghub.ai）、工作流 ID、输出节点、实例类型、
  输入映射，以及默认工作流。
- 目录：`output_dir` 与 `cache_dir`，留空使用默认位置。

### 生成失败重试

`providers.json` 顶层提供两个字段，控制一次生图失败后是否重新提交，以及最多重新提交几次：

```json
{
  "retry_on_failure": false,
  "retry_count": 2
}
```

`retry_on_failure` 是开关，默认 `false`；`retry_count` 是额外尝试次数（0–10），默认 `2`。
每次重试都会启动一次**全新的上游请求**，不会轮询或复用已经失败的请求结果。重试只针对限流、
并发不足、网关 5xx、超时和网络中断等临时失败；鉴权、参数校验等永久失败会立即返回。
已经拿到远端任务 ID 的失败、以及结果未知的提交（`submission_uncertain`）不会重试，
以免重复生成或重复计费。重试间隔按次数递增（约 1 秒、2 秒…），让出并发槽位。
成功后的结果会带 `generation_attempts` 和重试警告；失败时报错会注明总尝试次数。
`get_provider_status` 的 `generation_retry` 字段显示当前生效值。

保存前校验字段；文件在页面打开后被其他程序修改时会拒绝保存并提示重新读取。每次保存先在配置目录
`backups/` 下备份原文件，再原子替换。页面接口 `/api/fmage.config`、`/api/fmage.config.reveal` 和
`/api/fmage.models` 位于 Desktop 的连接认证之内，仅本机所有者会话可访问。

配置改动**下一次请求即生效，不需要重启 DSH**：MCP 服务端每次工具调用都重新读取 `providers.json`
（服务端与工作流各自按调用读取，没有进程内缓存），所以页面保存或手工编辑后，下一次生图就会用新值。
原子替换保证读取方要么看到旧文件、要么看到完整的新文件。需要重启的只有：改 `FMAGE_CONFIG` 路径、
改 `api_key_env` 指向的环境变量，或改插件代码本身。手工把 JSON 改坏时，下一次请求会直接报解析错误，
不会静默沿用旧配置。

## Codex 兼容：个性化指令

在 Codex / ChatGPT 应用中使用时，禁用原生生图 skill 并向个性化指令中添加下面的内容。

### Image Generation And Editing

- For raster image generation, image editing, color correction, color optimization, retouching, background changes, style transfer, or any other task that produces or edits an image, use the Fmage plugin.
- Load and follow the `fmage:fmage` skill, then call `mcp__Fmage.generate_image`, `mcp__Fmage.edit_image`, `mcp__Fmage.generate_image_batch`, or `mcp__Fmage.edit_image_batch` as appropriate.
- Do not use Codex native `image_generation_call`, the `~/.codex/generated_images` output path, or a disabled generic image generation skill for these tasks.
- Do not use Shell/Python/PIL/ImageMagick scripts as a fallback for image generation or image editing unless the user explicitly asks for deterministic local pixel processing.
- If Fmage tools are not visible, first run proper tool discovery and `get_provider_status`. If the tools still cannot be called, report the blocker instead of silently switching to another image tool or local script.
- After any Fmage image call fails or returns partial results, stop and report the failure. Do not retry, change parameters, query providers to find an alternative, or switch providers unless the user explicitly asks or approves it. The only exception is Fmage's own `retry_on_failure`/`retry_count` configuration, which resubmits a fresh request internally before returning a failure.
- Treat Fmage `1k`/`2k`/`3k`/`4k` values as resolution tiers. For `openai-images`, the normalized `requested_size` is authoritative; when actual dimensions match it and no size warning is present, do not describe the output as undersized or compare it with `4096x4096`.
- Pixel dimensions outrank ratios: a stated `1920x1080` is sent as pixels rather than re-derived from a tier. A generate request defaults to the `2k` tier at the chosen aspect, while an edit with no explicit canvas keeps the reference's own pixels normalized to a legal size. `quality` never moves the tier. When a generate request names no canvas, `requested_size` is `auto` and the provider's returned dimensions stand as delivered.

## Image Entry Points

The plugin exposes five independent skills while sharing one MCP server and one provider/transport layer:

- `Fmage 配置` (`fmage-config`) manages local provider configuration.
- `Fmage 图像生成` (`fmage`) is the default image entry and keeps the original understanding-and-expansion behavior.
- `Fmage 直传生图` (`fmage-direct`) is explicit-only. Invoke `/Fmage 直传生图` or `$fmage-direct` before the image request to preserve the prompt without expansion, translation, polishing, or paraphrase.
- `Fmage 图片退步` (`fmage-image-regression`) runs the fixed local regression pipeline.
- `Fmage 工作流` (`fmage-workflow`) runs configured RunningHub workflows.

The selected image entry is request-local and must be chosen before prompt interpretation or attachment inspection. Image task results use neutral fields such as `prompt_submitted`, `prompts_submitted`, and `prompt_mode`; legacy `revised_*` fields remain readable for older manifests.

New image records and tool results put `provider`, `provider_transport`, `provider_model`, and `prompt_mode` first, in that order when present. Other fields keep their relative order; compact tool results still omit transport details. Existing saved records are not rewritten.

## Local Runtime Refresh

`python scripts/refresh_local_runtime.py` refreshes the DeepSeek Desktop package first-class
alongside the Codex plugin cache and the VS Code Agent Plugin. All of them use host-private
locations, never `~/.agents/skills`. `--check` is read-only. Codex keeps skill and MCP catalogs
per task, so start a new Codex task after a refresh; this does not resubmit any image request.
See [host adapters](adapters/README.md) and [VS Code installation](vscode/README.md).

Providers select a protocol: openai-images, gemini-generate-content, or midjourney.
There are no channel-specific execution profiles or model allowlists for OpenAI Images.
For asynchronous OpenAI Images APIs, configure async_mode=true; this submits with
async=true and polls /images/tasks/{task_id}. Configure image_field as auto,
image, or image[] to match the edit API before submission. Names and hostnames do not select behavior.
Gemini supports explicit auth_scheme values x-goog-api-key (default) and bearer.
Explicit /v1 and /v1beta paths are preserved; an unversioned base URL uses /v1beta.
generation_config_format selects image-config (default, imageConfig) or response-format
(responseFormat.image, used by the supplied v1 documentation). The example 2.1 provider uses the latter.

Midjourney uses one independent adapter and one shared protocol contract that does not depend on
the model ID. Configure provider KC-MJ with transport=midjourney,
base_url=https://newapi.prompt-hubs.com, and the model ID taken from the relay's own model list
(for example mj-v8.2). Keep the API key in the local providers.json. The configured model
ID is an opaque server identifier: it is sent unchanged, without a local model allowlist or
model-dependent routing. Switching to another server-side ID does not change the client contract.
The relay selects the upstream. All requests submit JSON to
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
Remote provider status requests retain a 30-second cap and batch/workflow foreground waits retain a 40-second window.

For image batches, `return_when: "completed"` means wait up to that 40-second window.
It may return `status: "running"`, `terminal: false`, the existing `task_id`, and an explicit
`next_action` calling `get_image_task_status` with that ID and `wait_seconds: 40`.
Follow this action with one waiter at a time; never repeat the image submission or replace it
with single-image calls to wait, since those create new billable requests. Increasing `timeout`
only changes the provider request budget. Local status calls default to a 40-second wait;
`wait_seconds: 0` reads immediately. These calls never submit generation.
`running_count` counts unsettled jobs and `pending_count` counts transport-returned pending jobs.
An unfinished batch remains `running` even if some images are ready. A terminal failed,
partial, or pending result ends this attempt; report it without automatic resubmission.
Here `terminal` describes the local batch lifecycle, not proof that an upstream pending task stopped.

Async tasks are queried immediately. No provider error resubmits generation or strips request fields.
Submission connection loss before a task ID is received records submission_uncertain and remote_status=unknown.
Once the existing full task ID is known, get_image_task_status with provider and remote_task_id
retrieves it using GET only; it never generates again. Local tasks still use task_id.
A keyless dry_run validates routing without contacting a provider.

GPT Image family checks use shared keyword patterns in
`config/gpt-image-model-families.json` for MCP and both OpenAI Images transports.
They accept provider namespaces, channel labels, case differences, and hyphen/underscore/space
variants, including `openai/gpt-image-2.5-sunburst`, while preserving the configured model ID
in the request. The GPT Image family defaults to `quality=high` across legacy, current,
and future numeric versions; the default does not depend on a `gpt-image-2` prefix.
An explicit supported quality selection is preserved. Extended `xhigh`/`max` quality is
confirmed for the 2.5 series using `openai-images`; other versions remain usable with base
tiers and map extended requests to `high` with a warning until support is confirmed.
Version boundaries distinguish 2.5 from 2.50; this is family recognition, not typo
correction or automatic provider/model switching.

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

## VS Code adapter (compatibility)

VS Code's MCP integration discovers Fmage tools but does not automatically load the Codex plugin manifest or its skills. Install the VS Code adapter to synchronize adapted skills and configure the local MCP server without replacing other servers:

```text
python scripts/install_vscode_adapter.py
```

Use `python scripts/install_vscode_adapter.py --check` for a read-only check. See `vscode/README.md` for the supported paths, natural-language entry points, and reload step.
