# 三宿主适配与本机刷新

维护源只有仓库的 `skills/`、`mcp/`、`scripts/` 和 `config/`。
Codex 使用原生 `.codex-plugin/plugin.json`；其他宿主由 `scripts/harness_plugins.py` 构建，
不会直接修改 Codex Skill，也不会使用公共技能目录。

| 宿主 | 产物 | 本机注册 |
| --- | --- | --- |
| Codex | 原生版本缓存 | `fmage@personal` |
| VS Code 智能体窗口 | Agent Plugin ZIP | 用户设置 `chat.pluginLocations` 的私有目录 |
| DeepSeek Harness Desktop | `dsh-fmage-<version>.tgz` Cordis bundle | 仅 `profiles/desktop` 的依赖与 bundles |

`python scripts/harness_plugins.py --build all` 导出 ZIP/TGZ，默认放在 `%LOCALAPPDATA%/Fmage/packages`。
只打包明确列举的运行文件、示例配置、Skill 和适配器，不包含真实 providers.json、密钥、测试或本机安装记录。
包仍需要 Node 和带 Pillow 的 Fmage Python 环境；本机优先使用已配置的 FMAGE_PYTHON，
或现有 `~/.codex/runtimes/fmage-3.14/Scripts/python.exe`。不安装全局依赖、不修改 PATH。

DeepSeek 适配以本机已验证的桌面运行时 `0.2.0-rc.2` 为接口版本，使用原生技能服务、
`mcp__Fmage__*` 工具名和 Cordis 生命周期。注册五个 Skill，直传仅允许显式调用。
Skill 文件变化后失效技能目录缓存；工具进程和插件 JS 的更新需宿主重载才能确认生效。
`.fmage-runtime.json` 每五秒记录已加载版本、内容摘要、进程和 MCP 注册数量。
检查要求心跳新鲜且摘要匹配，旧进程或旧版本记录不能作为当前运行成功的证据；记录不进入包。

桌面版离线安装使用应用随附的 dsh 命令：

```text
"D:/DeepSeek Harness/resources/runtime/cli/bin/dsh.cmd" plugin --profile desktop add "<TGZ路径>" --offline --ignore-scripts
```

本机同步也使用版本化 TGZ，通过包管理器安装为 `profiles/desktop/node_modules/dsh-fmage` 实体目录。
不要使用 `link:` 或 Windows Junction：本机桌面版曾在重启后无法解析链接包，实体目录对照可正常解析。
`%LOCALAPPDATA%/Fmage/deepseek/dsh-fmage` 只作为构建副本，运行路径在 Desktop profile 内。
后续刷新同时更新 TGZ 和实体安装内容，不会复制到 Web profile 或源码版。退出桌面应用后安装，再重新打开。
不应使用其他 npm 安装的 dsh CLI 替代桌面应用随附版本。

`%LOCALAPPDATA%/Fmage/harness-installations.json` 记录本机源仓库、两个目标目录和导出目录。
完成首次配置后，现有 `refresh_local_runtime.py` 先刷新 Codex 版本与配置，再同步两套产物。
`--check` 验证源文件和压缩包一致性、VS Code 注册、DeepSeek 实体目录及 TGZ 依赖，
并使用桌面程序内置的加载器在临时 profile 中启动真实 Cordis 服务，核对五个 Skill 和十个 MCP 工具。
该测试不使用解析器替换或服务 mock，不加载用户会话，不调用付费模型，也不会写入实际桌面的运行记录。
当前桌面进程的状态另外通过运行心跳报告；版本不一致明确报告需要重载。
