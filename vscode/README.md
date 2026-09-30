# Fmage VS Code 适配层

Fmage 的 Codex 插件清单、技能入口和 VS Code MCP 服务器是三个不同层次：

- `mcp.json` 只负责让 VS Code 发现 `Fmage.*` 工具。
- 本适配层把 Fmage skills 同步到 VS Code/Copilot 能识别的 `~/.agents/skills`。
- 适配后的 skills 使用 `Fmage.*` 工具名，并把 `$fmage-*` 改为自然语言入口。

## 安装

在仓库根目录运行：

```text
python scripts/install_vscode_adapter.py
```

安装器会：

1. 同步五个 Fmage skills 到 `%USERPROFILE%\.agents\skills\fmage*`；
2. 保留 VS Code `mcp.json` 中已有的其他服务器；
3. 创建或更新 `Fmage` stdio MCP 服务器配置；
4. 使用 `FMAGE_CONFIG` 指向现有的 `providers.json`，不会复制或打印 API 密钥。

只检查不写入：

```text
python scripts/install_vscode_adapter.py --check
```

安装后执行 VS Code 的 `Developer: Reload Window`，再在 Agent 模式的工具列表中确认 `Fmage`。默认图像请求直接说“使用 Fmage 生成图片”；直传入口使用“Fmage 直传生图”；工作流和图片退步也使用对应中文名称，不再依赖 Codex 专用的 `$fmage-*` 命令。

## 配置路径

默认路径为：

- skills：`%USERPROFILE%\.agents\skills`
- MCP：`%APPDATA%\Code\User\mcp.json`
- providers：`%USERPROFILE%\.codex\fmage\providers.json`

可通过 `--skills-dir` 和 `--mcp-config` 覆盖前两个路径，便于工作区级安装或测试。