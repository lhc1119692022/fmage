# Fmage VS Code 使用规则

对于图像生成、编辑、调色、背景修改、风格迁移和图片工作流，优先使用 Fmage MCP 工具。

- 普通生成或编辑：使用 `Fmage.generate_image`、`Fmage.edit_image` 及对应 batch 工具。
- Midjourney 使用独立 midjourney 适配器和统一 v8.2 协议，以高速接口为核心、稳定文档补充。模型 ID 原样发送，由服务器选择上游；客户端没有型号路由或白名单。MJ 指令留在提示词，--ar 优先于自然语言比例，不拆成 JSON 参数。不要传通用 quality、resolution、size、aspect 或格式覆盖；输出像素由后台决定。统一最多5张参考图，预期4张单图；继续编辑需明确目标图。不支持生成后的动作或失败后自动切换型号。
- 明确说“Fmage 直传生图”或“直传生图”：保持原提示词，不翻译、不扩写、不润色。
- 明确说“Fmage 图片退步”：使用 `Fmage.regress_image`，不要调用图像生成工具。
- 明确说“Fmage 工作流”：使用 `Fmage.workflow_image`；不要把工作流当成普通生图。
- 配置或诊断：使用 `Fmage.get_provider_status`，不要打印 API 密钥。
- Fmage MCP 工具不可见时，先检查 VS Code MCP 状态和 `Fmage.get_provider_status`，不要静默改用其他生图能力。
- 生成调用失败或返回 partial 结果时停止当前尝试，不要自动重试或切换 provider。

VS Code 使用 `Fmage.*` 工具名，不使用 Codex 专用的 `mcp__Fmage.*`、`$fmage-*` 或 `/Fmage ...` 语法。
