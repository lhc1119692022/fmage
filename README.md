# Fmage：DeepSeek Harness Desktop 图片插件

Fmage 是面向 DeepSeek Harness Desktop 的图片生成与编辑插件，提供七个原生技能
（生图、直传生图、SVG、GIF、配置、RunningHub 工作流、图片退步）和十二个 `mcp__Fmage__*` 工具。
DeepSeek Desktop 是主要宿主，功能与验证都以它为基准；Codex 继续作为兼容宿主，
构建与安装见 [宿主适配](adapters/README.md)。

## DeepSeek Desktop 安装与刷新

```text
python scripts/refresh_local_runtime.py          # 刷新配置、重建并安装 Desktop 包（同时重装 Codex 版本）
python scripts/refresh_local_runtime.py --check  # 只读校验安装、包内容和隔离加载
```

安装只写入 `~/.dsh/profiles/desktop`。Desktop 运行中时，刷新走宿主自己的热插拔：先卸载插件释放目录，
再用登记的运行时安装，最后重新选入组合包，宿主加载新文件后由心跳确认新版本生效，无需重启，
也不会中断正在进行的图片请求（但刷新期间 Fmage 工具会短暂下线，别在生图请求执行中刷新）。
宿主的 HMR 只响应组合包列表变化，所以外部 pnpm 直接替换目录既不生效、还会因目录被占用而失败。

## 数据位置

配置与生成记录按宿主分开，每个宿主只读自己目录下的那一份。MCP 服务端没有默认路径：数据目录由
启动它的宿主通过 `FMAGE_DATA_HOME` 指定。

- DeepSeek Desktop：适配器注入 `<DSH_HOME|~/.dsh>/fmage`；`outputs/`、`cache/`、`workflow-tasks/` 也在该目录下。
- Codex：刷新时把 `env.FMAGE_DATA_HOME = <CODEX_HOME|~/.codex>/fmage` 写进本地已装插件的 MCP 注册项。
- `FMAGE_CONFIG` 可指向任意一份文件，覆盖数据目录（本机需要多处复用时用它）。

数据目录里没有 `providers.json` 时，首次调用就在那里生成 starter 配置；插件没有任何“去别处找配置”的代码路径。

## 设置页面：Fmage 生图

在 Desktop 的「设置」左侧导航中打开「Fmage 生图」，即可可视化编辑 `providers.json`：

- 提供商：添加、删除、启用/关闭，拖动或置顶调整优先级（启用列表从上到下即 `active_providers` 顺序，
  第一个为默认）。也可以直接拖动卡片左侧手柄改顺序，或点卡片右侧的置顶按钮把该提供商移到最前。
  卡片默认折叠，只显示优先级、名称与 transport/model 摘要；展开后名称与 transport 同行，其余按填写顺序
  依次是 Base URL、API Key 和 Model。只显示 `transport`（下拉选择）、`base_url`、`api_key` 和 `model`，
  其他字段保存时原样保留。
- Model 输入框右侧的拉取按钮会按当前 Base URL 和 API Key 请求上游模型列表，结果显示在输入框下方；
  点击某个模型即可切换，输入框本身仍可手动编辑。上游不可用或返回 404 时会明确提示，不会阻塞手工填写。
  Base URL 或密钥缺失时不会再发出上游请求，而是就地提示先补齐这两项。
- API Key 默认以首尾字符加星号显示，点击眼睛图标才向本机 Host 读取并显示完整值；留空不改动即保留原密钥。
- 工作流：RunningHub 连接只保留 API Key 可编辑，不显示名称，Base URL 固定为国际站
  `https://www.runninghub.ai`；工作流配置只有工作流 ID、输出节点、实例类型、输入映射和默认工作流。
- 设置：一个标签内分三组。
  - 目录：`output_dir` 与 `cache_dir`，留空使用默认位置。
  - 重试：`retry_on_failure` 开关与 `retry_count`（额外尝试次数，0–10），规则见下一节。
  - 生成：`complexity`（关闭/低/中/高），只对 gpt-image-2.6 生效，规则见「生成复杂度」一节。
- 默认提供商：提供商卡右侧的星标设为默认；拖动只改变回退顺序。开关控制是否在对话输入栏显示生图胶囊。
- 输入栏胶囊：显示当前默认提供商，点开即可切换。它只写 `default_provider`，与设置页共用同一份配置；
  `default_provider` 为空或不在 `active_providers` 中时，回退到第一个启用的提供商。
  切换后下一次生图请求即按新的默认提供商路由；失败结果同样带 `provider`、`provider_model`，
  不会沿用上一次调用的提供商。没有 DSH 界面的宿主（Codex）不显示胶囊，仍按配置顺序和显式 `provider` 参数工作。

### 生成失败重试

`providers.json` 顶层提供两个字段，控制一次生图失败后是否重新提交，以及最多重新提交几次：

```json
{
  "retry_on_failure": false,
  "retry_count": 2
}
```

`retry_on_failure` 是开关，默认 `false`；`retry_count` 是额外尝试次数（0–10），默认 `2`。
这两项也能在设置页「设置」标签的「重试」分组里修改，保存后下一次生图请求即生效。
每次重试都会启动一次**全新的上游请求**，不会轮询或复用已经失败的请求结果。重试只针对限流、
并发不足、网关 5xx、超时和网络中断等临时失败；鉴权、参数校验等永久失败会立即返回。
已经拿到远端任务 ID 的失败、以及结果未知的提交（`submission_uncertain`）不会重试，
以免重复生成或重复计费。重试间隔按次数递增（约 1 秒、2 秒…），让出并发槽位。
成功后的结果会带 `generation_attempts` 和重试警告；失败时报错会注明总尝试次数。
`get_provider_status` 的 `generation_retry` 字段显示当前生效值。

### 生成复杂度（gpt-image-2.6）

`providers.json` 顶层的 `complexity` 是全局开关，取值 `false`（默认）、`"low"`、`"medium"`、`"high"`：

```json
{
  "complexity": false
}
```

- `false` 时请求里不带该字段，由 API 按默认值（high）规划。
- 设为档位后，只有 `openai-images` 且模型属于 gpt-image-2.6 系列的提供商会在生成与编辑请求里带上它；
  2.5 及更早版本、Gemini 和 Midjourney 一律不发送。
- 它不是工具参数，对话里的要求不会改变它；在设置页「设置」标签的「生成」分组或直接编辑文件修改。
- `get_provider_status` 的 `complexity` 字段显示 `setting`（配置值）和 `applied`（对所选提供商实际发送的值，不发送时为 `null`）。

保存前校验字段；文件在页面打开后被其他程序修改时会拒绝保存，并给出「重新读取并覆盖」，
不会被一次冲突卡死。每次保存先在配置目录
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

The plugin exposes seven independent skills while sharing one MCP server and one provider/transport layer:

- `Fmage 配置` (`fmage-config`) manages local provider configuration.
- `Fmage 图像生成` (`fmage`) is the default image entry and keeps the original understanding-and-expansion behavior.
- `Fmage 直传生图` (`fmage-direct`) is explicit-only. Invoke `/Fmage 直传生图` or `$fmage-direct` before the image request to preserve the prompt without expansion, translation, polishing, or paraphrase.
- `Fmage SVG` (`fmage-svg`) and `Fmage GIF` (`fmage-gif`) are explicit-only entries for the `generate_svg` and `generate_gif` tools. See "SVG and GIF output" below.
- `Fmage 图片退步` (`fmage-image-regression`) runs the fixed local regression pipeline.
- `Fmage 工作流` (`fmage-workflow`) runs configured RunningHub workflows.

The selected image entry is request-local and must be chosen before prompt interpretation or attachment inspection. Image task results use neutral fields such as `prompt_submitted`, `prompts_submitted`, and `prompt_mode`; legacy `revised_*` fields remain readable for older manifests.

New image records and tool results put `provider`, `provider_transport`, `provider_model`, and `prompt_mode` first, in that order when present. Other fields keep their relative order; compact tool results still omit transport details. Existing saved records are not rewritten.

## Local Runtime Refresh

`python scripts/refresh_local_runtime.py` refreshes the DeepSeek Desktop package first-class
alongside the Codex plugin cache. Both use host-private locations, never `~/.agents/skills`.
`--check` is read-only. Codex keeps skill and MCP catalogs per task, so start a new Codex task
after a refresh; this does not resubmit any image request.
See [host adapters](adapters/README.md).

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
confirmed for the 2.5 and 2.6 series (Sunburst and Flare, including dated snapshots) using
`openai-images`; other versions remain usable with base tiers and map extended requests to
`high` with a warning until support is confirmed.
Version boundaries distinguish 2.5 from 2.50 and 2.6 from 2.60; this is family recognition,
not typo correction or automatic provider/model switching.

### Markup annotations (gpt-image-2.5 / 2.6)

GPT Image 2.5 and 2.6 accept a separate transparent PNG as the `markup` field of
`/images/edits`. The target image is sent untouched and the annotations arrive as their own
layer, so the model removes the marks instead of reproducing them. `edit_image` exposes this
as `markup` (a local path); `generate_image` and both batch tools do not accept it.

- The layer and the first input image must share one canvas. Fmage plans the reference fit once
  and applies it to both: an extension leaves the layer's added bars transparent, and a crop
  cuts both files with the same box.
- Rejected before any provider request: models outside 2.5/2.6, transports other than
  `openai-images`, `output_format` gif or svg, `primary_image_index` other than 0, a layer that
  is not a transparent PNG, mismatched dimensions, and the 16,777,216-pixel / 50 MB caps.
- Omitting `markup` appends no flag and writes no multipart part, so a flattened edit cannot
  carry a stray empty layer.

The Desktop sidebar adds an annotation workbench. It registers as a card on the right sidebar's
start page, not as a composer button, so the input rows stay as they were. It works on an image
that is already in the composer: drag its thumbnail in (or pick it from the list), draw with the
floating toolbar, and save.

Saving writes one transparent layer and nothing else — no copy of the image, no path in the
conversation:

- The layer lands in `<cache_dir>/annotations/<id>/` when the settings name a cache folder, and in
  the system temp folder (`%TEMP%\fmage\annotations` on Windows) when they don't or that folder
  cannot be written. A `meta.json` beside it records the mode, the canvas, and the comments.
- An unused layer stops attaching and is deleted after a day; a used one is deleted a day after
  its request. Both the next save and the next `edit_image` sweep expired entries.
- `edit_image` looks for the newest unused layer drawn on its first input image (by SHA-256, or
  by a coarse luma fingerprint for a resized copy) and attaches it by itself: `--markup` on a
  2.5/2.6 `openai-images` provider, or a local composite (`--flatten-markup`) on any other model.
  A layer saved for another picture is left alone. A used layer is retired; a failed request
  leaves it in place.
- Comments are inserted into the composer as ordinary prose in the shape the user would type,
  `图像 1： 1. (x: 22.1%, y: 26%) …`, so they reach the prompt without a machine path.
- The annotated image has to be the first attachment, because that is the image the API treats as
  the edit target; the panel refuses to save a markup layer otherwise and points at "flatten".

Nothing is added to the draft except that comment block, and the panel never edits an attachment
in place. A flatten composite over the host's 50 MiB per-image limit is saved as WebP (quality
100), then scaled down if it is still too large.

The conversation hands tools a normalized copy of each attachment (at most 2048×2048 and 4 MiB).
On `openai-images` providers `edit_image` sends the original instead: the sidebar uploads every
composer picture to `<cache_dir>/originals/` (or `%TEMP%\fmage\originals`), and the edit swaps a
host copy for the stored original only when its pixels match block by block. The original is
oriented, converted to 8-bit sRGB, and kept within 16,777,216 pixels and 50 MiB. Any miss sends the
copy as before; the result's `original_restored` field records what was sent. Stored originals are
dropped after 7 days unused or when they pass 2 GB. Pictures already in a conversation before the
Desktop app restarts with this build have no stored original.

### SVG and GIF output

`generate_svg` and `generate_gif` are separate tools so the standard `generate_image`,
`edit_image`, and batch tools never produce these formats; they reject `output_format` svg/gif
outright. Both tools produce one result (n=1) and require the selected provider to use
`openai-images` with a gpt-image-2.6 model. They have no size, background, or compression
parameters. Low quality still needs `quality_user_requested=true`.

- SVG: generation-only, with no reference-image parameters. The returned payload is saved as
  UTF-8 XML in a `.svg` file.
- GIF: without references it uses `/images/generations`. With `images` (reference paths or URLs)
  or `use_latest=true` it uses `/images/edits`, the same multipart route as `edit_image`. The
  OpenAI guide says the edits endpoint does not output GIF, but the edit API reference lists
  `gif` and community tests report it works, so Fmage sends it and reports any upstream error.
- GIF: defaults to 16 frames at 80 ms per frame. `frame_count` accepts 2–16 (the current API
  limit; older docs said 64). `duration_seconds`
  is converted at 12 fps, and a conversion above 16 or below 2 frames is an error. Passing both
  `frame_count` and `duration_seconds` is also an error. `frame_duration_ms` accepts 20–2000 and
  is rounded to 10 ms. Frame size is always `auto`, which keeps the request within the API's
  8,294,400-pixel total budget.
- Results skip local size and dimension checks and are returned as a file path with a markdown
  preview, not as embedded MCP image content. They never replace `latest.json`, so `use_latest`
  edits keep pointing at the last raster image.

The plugin card supports at most three `interface.defaultPrompt` entries. Those card actions are separate from the seven skill entry points above, which are declared by each skill's `agents/openai.yaml`. To prevent a missing Fmage MCP catalog from silently falling back to native image generation, keep the generic image skill disabled and disable the host feature as well:

```text
codex features disable image_generation
```
# RunningHub 图片工作流

使用快捷命令 **Fmage 工作流**（`$fmage-workflow`）。默认配置“SeedVR2 放大”，分辨率档位为4（4K），
附上图片并选择命令即可直接运行，无需额外提示词或确认；未指定参数使用配置中的默认值。
明确要求列出、配置或查询任务时仅执行该操作；缺少必需图片时才询问图片。
在本机 providers.json 的 `workflow_connections.runninghub.api_key` 填入密钥即可；
设置页的 RunningHub 连接不显示名称，只保留 API Key 可编辑，Base URL 固定使用国际站
`https://www.runninghub.ai`。不必把密钥发送到聊天。配置示例见 `config/workflows.example.json`，
首次刷新自动补充缺失字段。

- “用SeedVR2放大这张图，6K”：仅本次使用 resolution_k=6，未指定时使用配置默认值4。
- “列出工作流”：显示名称和输入参数。
- “把默认工作流改为某名称”：更新 `active_workflow`。
- “运行这个公共工作流 <链接>”：使用它的 `workflow_id` 和 API 节点映射，不要用 `/ai-detail/`
  的 app ID。若页面只给 app ID，需要找到对应的 workflow ID，或导出该工作流的 API JSON。
- “继续获取任务 request_id 的图片”：恢复结果查询，不重新提交付费任务。

每个工作流保存 `workflow_id`、输入节点映射和 `output_node_ids`。公共工作流和私有工作流走同一个
ComfyUI 工作流接口 `/task/openapi/create`：公共工作流使用作者发布的 `workflow_id`，私有工作流使用
自己创建或复制后调整的 `workflow_id`。插件不按归属做限制，由 RunningHub 校验该 ID 是否可访问。

平台发布公共工作流时可能要求先创建 app，但 **app ID 和 workflow ID 是两个资源**。app 只暴露作者
挑选的少量输入；要获得工作流级的节点控制，必须使用 workflow ID 和 API 格式的 `nodeInfoList`。

多个工作流时，每次只执行一条。用户明确命名时按名称选择；未命名时使用 `active_workflow`。
插件本身不做语义匹配：模型根据 `list` 返回的名称和输入选择；无法唯一对应时会先询问或列出，
不会自动猜测，不会按内容给工作流排名，也不会在失败后切换到另一个工作流。

多个工作流共用 connection。同一节点结构且只替换 ID 时可以直接修改。输入支持 PNG/JPEG/WebP
（每张最多 30 MB）；输出支持 PNG/JPEG/WebP（每张最多 100 MB），按配置节点收取全部图片并保留原始字节。
`output_node_ids` 为必填，插件只下载这些节点返回的图片，避免把预览图一起取回。
输出使用本次 output_dir、全局 output_dir 或配置目录下 outputs/workflows，图片直接保存到该目录，
使用任务编号、图片序号和随机后缀避免重名，不再创建任务子文件夹。已保存的历史路径保持有效。
status 调用内部每10秒查询一次，等待窗口约40秒，省去对话中的单独 sleep 命令。
PNG 结果同时返回宽高和格式，避免再执行命令读取尺寸。
任务记录位于配置目录 workflow-tasks，不保存密钥。返回本地文件路径供对话预览和打开。
提交遇到不确定网络错误不会自动重发；先在 RunningHub 任务记录中核对任务 ID。
下载失败保留已保存图片和任务 ID，经用户确认后重取结果。结果链接失效或平台清理结果后，
不能保证恢复，所以成功后应及时下载。当前只接收图片输出；
视频、音频、加密工作流密码、webhook 和输出重定向暂不支持。
测试使用模拟 API，不代表已对账号权限、模型环境和真实 CDN 完成验证。
