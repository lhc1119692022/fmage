# 宿主适配与本机刷新

DeepSeek Harness Desktop 是主宿主，功能与验证以它为基准；Codex 和 VS Code 为兼容宿主。
维护源只有仓库的 `skills/`、`mcp/`、`scripts/`、`config/` 和 `adapters/`。
Codex 使用原生 `.codex-plugin/plugin.json`；其他宿主由 `scripts/harness_plugins.py` 构建，
不会直接修改 Codex Skill，也不会使用公共技能目录。

| 宿主 | 角色 | 产物 | 本机注册 |
| --- | --- | --- | --- |
| DeepSeek Harness Desktop | 主宿主 | `dsh-fmage-<version>.tgz` Cordis bundle（含设置页） | 仅 `profiles/desktop` 的依赖与 bundles |
| Codex | 兼容 | 原生版本缓存 | `fmage@personal` |
| VS Code 智能体窗口 | 兼容 | Agent Plugin ZIP | 用户设置 `chat.pluginLocations` 的私有目录 |

## DeepSeek 设置页

`adapters/deepseek/` 包含三部分：`index.mjs`（Host 半：技能、MCP、运行心跳与配置路由）、
`config-api.mjs`（`providers.json` 的读写、校验、掩码、备份与原子替换）和 `client.js`
（浏览器半，经 `window.__ModuleLoader__.load` 注册，向 `settings.section` 插槽贡献「Fmage 生图」页）。
包清单声明 `dsh.client.platform = web` 与 `exports["./client"]`，由宿主的 client 模块扫描器加载。
浏览器半只 `require` 宿主共享的 `react` 与 `@deepseek-ai/dsh-client-ui-primitives`，无需构建步骤。

Host 半在 `connection` 服务可用时注册精确路由：`GET/PUT /api/fmage.config` 与
`POST /api/fmage.config.reveal`。它们位于 Connection 认证栅栏内，只有 owner 会话可访问；
读取结果只含掩码密钥，完整值仅在用户点击显示时单独返回。保存要求携带读取时的文件摘要，
避免覆盖其他进程（如 `workflow_image set_default`）的并发修改。`tests/deepseek_config_api.test.mjs`
与 `tests/deepseek_settings_client.test.mjs` 覆盖接口与页面注册；后者需要本机 DSH 源码的 React。
`--check` 另外在隔离 profile 中启动真实 Cordis 服务，要求 client 模块图里有 `dsh-fmage` → `client.js` 这一行，
因此“页面被宿主发现”是每次刷新都会复验的，而不是只看文件是否在包里。

`python scripts/harness_plugins.py --build all` 导出 ZIP/TGZ，默认放在 `%LOCALAPPDATA%/Fmage/packages`。
只打包明确列举的运行文件、示例配置、Skill 和适配器，不包含真实 providers.json、密钥、测试或本机安装记录。
同步目录时，构建清单 `.fmage-build.json` 记录本次拥有的路径；下一次同步会删除上一版拥有、本次已不再打包的文件，
所以移除某个模块（例如退役的传输）后，安装副本里不会留下旧文件。没有该清单的旧安装按 `scripts/*.py` 兜底清理一次。
包仍需要 Node 和带 Pillow 的 Fmage Python 环境；本机优先使用已配置的 FMAGE_PYTHON，
或现有 `~/.codex/runtimes/fmage-3.14/Scripts/python.exe`。不安装全局依赖、不修改 PATH。

DeepSeek 适配的 Harness 最低版本为 `0.2.0-rc.2`，两个 `@deepseek-ai/dsh-*` peer 均声明
`>=0.2.0-rc.2`，不设置未来版本上限。Harness 原生兼容性检查包含预发布版本，因此后续 alpha、
beta、rc、正式版和更高主版本不会仅因版本号被拦截。Cordis 的独立依赖范围保持 `~4.0.4`。
版本放行不代表已验证未来接口兼容；刷新时通过登记运行时的实际加载测试检查当前版本。
适配器使用原生技能服务、`mcp__Fmage__*` 工具名和 Cordis 生命周期。注册五个 Skill，直传仅允许显式调用。
Skill 文件变化后失效技能目录缓存；工具进程和插件 JS 由宿主的 HMR 按文件变化重载。
`.fmage-runtime.json` 每五秒记录已加载版本、内容摘要、进程和 MCP 注册数量。
检查要求心跳新鲜且摘要匹配，旧进程或旧版本记录不能作为当前运行成功的证据；记录不进入包。

安装与校验使用本机明确登记的运行时。Electron 安装使用应用随附的 dsh 命令：

```text
"D:/DeepSeek Harness/resources/runtime/cli/bin/dsh.cmd" plugin --profile desktop add "<TGZ路径>" --offline --ignore-scripts
```

本机同步也使用版本化 TGZ，通过包管理器安装为 `profiles/desktop/node_modules/dsh-fmage` 实体目录。
不要使用 `link:` 或 Windows Junction：本机桌面版曾在重启后无法解析链接包，实体目录对照可正常解析。
`%LOCALAPPDATA%/Fmage/deepseek/dsh-fmage` 只作为构建副本，运行路径在 Desktop profile 内。
后续刷新同时更新 TGZ 和实体安装内容，只操作登记的 Desktop profile，不复制到 Web profile 或宿主源码仓库。
不使用其他 npm 安装的全局 dsh CLI，也不终止运行中的图像请求。

### 热插拔更新（宿主运行中）

Desktop 运行中时，直接在它背后替换 `node_modules/dsh-fmage` 会失败：宿主里的 MCP 子进程占着这个目录
（插件的 `cwd` 就是包根目录），Windows 不允许重命名被占用的目录；而且 HMR 只监听 `dsh.profile.bundles`
这类有序列表变化，单纯的依赖清单变化不会触发重新组合，所以外部 pnpm 装完也不会生效。

因此宿主运行时的安装走宿主自己的重载语义：先从 `dsh.profile.bundles` 移除 `dsh-fmage`，宿主随即卸载插件、
释放目录（插件的 disposer 会删除 `.fmage-runtime.json`，这就是卸载完成的握手信号）；再用登记的运行时执行
`dsh plugin add`；最后把 `dsh-fmage` 放回列表，宿主重新组合并加载新版本。
脚本等待新版本心跳出现才报告 `hot`，因此“安装成功”与“运行中已生效”一致。
卸载后 45 秒内没等到卸载信号、或重装后 60 秒内没等到新版本，都会明确失败而不是假装成功；
`finally` 会恢复 bundle 选择，安装失败时旧版本继续可用。宿主未加载该插件时退回原有的 CLI 安装路径。

`dsh plugin` 只把参数转发给 pnpm，不会选入组合包，所以选择状态由脚本自己维护。
刷新过程中 Fmage 的 MCP 工具会短暂下线；不要在生图请求进行时执行刷新。

从 Electron 改为源码启动时，显式登记现有源码目录，再执行正常刷新：

```text
python scripts/harness_plugins.py --register-deepseek-source "D:/deepseek-harness"
python scripts/refresh_local_runtime.py
python scripts/refresh_local_runtime.py --check
```

只同步或检查 VS Code 与 DeepSeek 时，可分别运行 `python scripts/harness_plugins.py`
和 `python scripts/harness_plugins.py --check`；这条独立入口不依赖 Codex 重装辅助脚本。

登记会备份并更新本机 `harness-installations.json`，保留 profile、VS Code 和产物路径；
源码路径只保存在本机记录，不进入插件包。源码安装在该仓库目录中通过 `node --import tsx/esm`
运行安装入口 `scripts/deepseek_source_cli.mjs`，调用源码 `apps/cli/src/bin.ts` 的 `runCli`，
传入 `plugin --profile desktop add <TGZ> --prefer-offline --ignore-scripts`。
入口使用 CLI 原生的 `packageManager` 参数指定源码 `node_modules/pnpm/bin/pnpm.cjs`；
运行前校验它与仓库 `packageManager` 固定版本一致，避免全局 pnpm 版本与缓存格式差异。
CLI 自身的参数解析、profile 文件锁和兼容性检查保持生效。
源码模式优先使用缓存；缺少已有依赖的 registry 元数据时允许 pnpm 查询，以完成其供应链检查。
不禁用策略检查、不自动重试失败的安装，也不执行依赖生命周期脚本。Electron 模式仍使用 `--offline`。
与源码启动脚本使用相同的 CLI 和 tsx 依赖。`DSH_HOME` 按登记的 profile 明确设置，
避免调用者的环境变量将安装写入另一个 profile。
必须预先具备该源码的依赖与构建结果；脚本不会切换全局 CLI、构建宿主或绕过宿主兼容性检查。
旧 Electron 登记继续受支持；路径缺失时明确报错，不按相似目录名称自动切换运行时。

`%LOCALAPPDATA%/Fmage/harness-installations.json` 记录本机源仓库、两个目标目录和导出目录。
完成首次配置后，现有 `refresh_local_runtime.py` 先刷新 Codex 版本与配置，再同步两套产物。
`--check` 验证源文件和压缩包一致性、VS Code 注册、DeepSeek 实体目录及 TGZ 依赖，
并使用登记运行时的原生加载器在临时 profile 中启动真实 Cordis 服务，核对五个 Skill 和十个 MCP 工具。
宿主提供版本检查器时，同一测试还验证最低版本边界、后续预发布版本和跨主版本的放行规则，不添加兼容性豁免。
源码模式使用 CLI 的 `apps/cli/package.json` 作为依赖解析入口；Electron 模式使用随附 CLI 的入口。
该测试不使用解析器替换或服务 mock，不加载用户会话，不调用付费模型，也不会写入实际桌面的运行记录。
当前桌面进程的状态另外通过运行心跳报告；版本不一致明确报告需要重载。
