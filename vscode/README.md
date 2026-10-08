# Fmage VS Code 智能体插件

这是智能体窗口 Customizations → 插件识别的 Agent Plugin，不是 VSIX 扩展。
生成目录有 `.plugin/plugin.json`、五个 `skills/*/SKILL.md`、`.mcp.json` 和独立运行代码。
不再写入公共 `~/.agents/skills`，也不再注册用户级独立 Fmage MCP。

构建 ZIP：

```text
python scripts/harness_plugins.py --build vscode
```

解压后，在 VS Code 用户设置 `chat.pluginLocations` 中将解压后的 `fmage` 目录设置为 `true`，
并启用 `chat.plugins.enabled`。本机自动安装路径为 `%APPDATA%/Code/User/agent-plugins/fmage`。
此路径仅由 VS Code 插件设置注册，不进入其他 Harness 的共享技能发现目录。

五个技能采用中文描述和标准前置元数据；`fmage-direct` 设置 `disable-model-invocation: true`。
其他四个入口可按请求匹配。工具按 Fmage 服务器和原始工具名选择，完整前缀以 VS Code 提供的名称为准。
图片退步调用本地 `regress_image` 工具；创意生图仍遵守失败停止、不自动重试的规则。

首次在本机配置两个适配器并迁移旧共享安装：

```text
python scripts/harness_plugins.py --configure --deepseek-cli "D:/DeepSeek Harness/resources/runtime/cli/bin/dsh.cmd"
```

迁移只归档已识别的 Fmage 旧 Skill 和旧 MCP 注册，备份保存在 `%LOCALAPPDATA%/Fmage/backups`。
以后运行 `python scripts/refresh_local_runtime.py` 会同时刷新 Codex 和已注册的两个适配器。
已运行的工具进程可能需要重载插件或窗口；安装文件更新不等于当前会话已重新加载。
不自动中断正在执行的图片任务。
