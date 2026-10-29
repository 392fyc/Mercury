# Issue #579：可拆卸 Harness 候选筛选（2026-09）

> 快照说明：本文记录 2026-09-25 的状态。之后同一 Issue 删除了项目中的 Claude 版 superpowers 文件，其来源记录改为登记在对应的 `.agents/skills/` 镜像路径下。

日期：2026-09-25。目标是让 Mercury 保持轻量、可独立拆卸，并适配 GPT-6 Astra 主代理与 GPT-6 Luna Max 执行者。本文只作资料筛选，没有安装项目、复制上游文件、改配置或联系外部维护者。

## 方法与本地现状

GitHub stars、许可证标识和最近推送时间取自公开 GitHub REST API，于 2026-09-25 02:56 UTC 查询；这些是认可信号和活跃度线索，不证明质量或实际提效。候选能力与安装边界取自各项目 README、LICENSE 和官方文档；没有跑性能或行为基准。源链接指向上游主页/API，数字记录的是本次查询快照。

本地基线与方向一致：[`.mercury/docs/DIRECTION.md`](../DIRECTION.md) 将 Mercury 定义为薄本体，不自建编排层，外挂需可拆卸。`.codex/agents/main.toml` 已指定 Astra 主代理，`dev.toml` 已指定 Luna Max；`.agents/skills/` 已有 Mercury 的 `dev-pipeline`、`dual-verify`、`mercury-subagent-driven-development` 等工作流；`modules/` 目前只有 `.gitkeep`。`adapters/playwright-mcp/` 已留有 Microsoft MCP 的固定版本适配器与安全闸，但当前 [`.mcp.json`](../../../.mcp.json) 的 `mcpServers` 为空；适配器说明记录它曾因存储态环境变量缺失而撤销注册。仓库已有 `obra/superpowers` 的 Claude 文件来源记录，Codex 侧改用不同名称的 Mercury 版本；README 另记录了 Claude Code 的 `oh-my-claudecode` 插件。它与下面的 `oh-my-codex` 是不同项目。

## 优先评估的六项

### 1. upstash/context7

- **认可信号：** 62,403 stars；MIT；最近推送 2026-09-24。见 [GitHub 元数据](https://api.github.com/repos/upstash/context7) 与[仓库](https://github.com/upstash/context7)。
- **能力：** 通过 MCP 或 CLI + Skill 查找随库版本更新的文档；上游文档列有 Codex 插件接入。它调用的是 Context7 托管服务，API key/OAuth 与服务限流属于额外条件；仓库的 MIT 许可证不代表托管服务本身由该许可证覆盖。见[客户端接入文档](https://github.com/upstash/context7/blob/master/docs/resources/all-clients.mdx)。
- **引用与卸载：** 只在查第三方库接口时将结果当作线索，结论继续引用库作者的官方文档。若后续试用，优先用独立 Codex 插件/MCP 连接；从插件管理器移除插件，若另加了 marketplace 再单独移除来源。上游 CLI 的生成式 setup 可用 `ctx7 remove` 清理，独立全局 CLI 则需另行卸载。
- **判定：** **有条件优先试用。** 当前研究流程已有网页检索，但 Context7 对版本化库文档有明确增益；只在 API/SDK 任务中启用，避免常驻工具与额外上下文。它不替代一手文档。

### 2. microsoft/playwright-cli

- **认可信号：** 13,547 stars；Apache-2.0；最近推送 2026-09-18。见 [GitHub 元数据](https://api.github.com/repos/microsoft/playwright-cli)、[README](https://github.com/microsoft/playwright-cli/blob/main/README.md) 与 [LICENSE](https://github.com/microsoft/playwright-cli/blob/main/LICENSE)。
- **能力：** 用 CLI 和随附 Skill 驱动浏览器。Microsoft 说明 CLI + Skill 避免加载大型 MCP 工具定义和冗长页面快照，对代码代理更省上下文；MCP 对需持续状态和详细页面结构的流程仍有用。默认浏览器状态留在内存，关闭即消失；`--persistent`、自定义 profile 和 attach/CDP 属于另行启用的能力。见[上游比较与会话说明](https://github.com/microsoft/playwright-cli/blob/main/README.md#playwright-cli-vs-playwright-mcp)。
- **引用与卸载：** 若试用，按 Mercury 规则固定确切 npm 版本，将 CLI 与 Skill 作为独立 adapter/项目依赖记录；卸载时分别移除该依赖与随附 Skill，并清除显式持久化的浏览器数据。试验限制为内存隔离会话，避免默认启用 profile 或 attach 类选项。
- **判定：** **仅当浏览器测试是近期真实缺口时做小范围比较。** 它比当前 MCP 的交互面更轻，但没有证明可以替代 Mercury 对认证存储态的安全控制；不要与 MCP 同时常驻。

### 3. openai/plugins（替代旧 `openai/skills` 的当前官方参考库）

- **认可信号：** 7,151 stars；GitHub 未给仓库级许可证标识；最近推送 2026-09-24。见 [GitHub 元数据](https://api.github.com/repos/openai/plugins) 与[仓库说明](https://github.com/openai/plugins/blob/main/README.md)。各插件须分别核查许可证。
- **能力：** OpenAI 整理的 Codex 插件示例，覆盖 Skill、MCP、hooks 等组合；官方插件文档强调从最小插件形态开始，可只打包一个 Skill，也可单独注册本地或仓库 marketplace。见[官方插件架构](https://developers.openai.com/plugins/concepts/plugins)和[官方打包指南](https://developers.openai.com/plugins/build/plugins)。
- **引用与卸载：** 作为插件结构和官方工作流的文档参考；不要把整个无仓库级许可的目录复制进 Mercury。若某个具体插件确有价值，再逐项核查其 LICENSE/来源并通过 marketplace 独立安装；卸载该插件和 marketplace 来源，不影响 Mercury 自有 `.agents/skills/`。官方 Codex 插件页面说明插件可以独立卸载。
- **判定：** **优先作为官方格式与插件来源参考，不建议整库挂载。** 这是比旧 `openai/skills` 更合时的上游入口，但并非必须常驻的 Harness 能力。

### 4. obra/superpowers

- **认可信号：** 291,257 stars；MIT；最近推送 2026-09-22。见 [GitHub 元数据](https://api.github.com/repos/obra/superpowers)、[README](https://github.com/obra/superpowers/blob/main/README.md)与[LICENSE](https://github.com/obra/superpowers/blob/main/LICENSE)。
- **能力：** 多代理可用的开发方法与 Skills，涉及计划、TDD、调试、协作和交付；当前上游包括 Codex 插件支持。它是行为方法集合，可能增加每项工作的流程和上下文负担。
- **引用与卸载：** 引用具体 Skill 或方法时链接上游文件及版本；若需导入文件，走 Mercury 的 SHA、许可证、manifest、归因和 drift 流程。Codex 插件应从插件管理器移除；不要因此删除 `.agents/skills/mercury-subagent-driven-development/` 或 Claude 专用来源文件，它们是分别维护的项目资产。
- **判定：** **保留现有按需使用方式，不再叠加整套框架或复制镜像。** 仓库已记录 Codex 依赖上游用户级插件，Mercury 自有流程已有独立实现。用户在任务中对工作流的明确要求应继续约束其使用范围。

### 5. Yeachan-Heo/oh-my-codex（OMX）

- **认可信号：** 33,360 stars；MIT；最近推送 2026-09-23。见 [GitHub 元数据](https://api.github.com/repos/Yeachan-Heo/oh-my-codex)、[README](https://github.com/Yeachan-Heo/oh-my-codex)与[LICENSE](https://github.com/Yeachan-Heo/oh-my-codex/blob/main/LICENSE)。
- **能力：** Codex CLI 工作流层，含 prompts、Skills、agent teams、hooks、状态目录和 HUD。上游 README 明确说主要面向 macOS/Linux 的 Codex CLI；原生 Windows 与 Codex App 不是默认支持路径，可能不稳定。`omx setup` 会写项目 guidance、Skills、`.codex/config.toml`、hooks 和 `.omx/` 状态；`omx uninstall` 文档只承诺移除其 hooks wrapper，不能视为整体反安装。
- **引用与卸载：** 仅引用某项独立能力和其公开实现；若将来试验，限定一次性副本并逐项记下 setup 写入。卸载 CLI 包与清理由 setup 产生的文件是不同步骤，保留用户原有 hook/config 内容。
- **判定：** **不纳入当前 Windows Codex App 的 Mercury Harness。** 星数很高，但平台支持、额外文件面和编排能力都与当前本地角色设置及轻量方向重叠。

### 6. donvito/codex-astra-luna-orchestrator

- **认可信号：** 1,602 stars；Apache-2.0；最近推送 2026-09-23。见 [GitHub 元数据](https://api.github.com/repos/donvito/codex-astra-luna-orchestrator)、[README](https://github.com/donvito/codex-astra-luna-orchestrator)与[LICENSE](https://github.com/donvito/codex-astra-luna-orchestrator/blob/main/LICENSE)。
- **能力：** Astra/Sol 统筹、Luna 子代理的 profile 和安装脚本，附 Codex 配置、agent、Skill、指南及测试；候选主题与当前模型架构直接相关。
- **引用与卸载：** 只对照其角色文件和模型参数，并链接到上游版本；不要运行会写入用户 profile 的 setup。README 没有核实到完整反安装流程，若日后试用，应在独立 `CODEX_HOME` 中验证安装与清理，不覆盖当前配置。
- **判定：** **只作比较基准，不增加一层编排。** Mercury 已有 Astra `main` 与 Luna Max `dev` 定义。外部 profile 若比现状多出角色，只会增加配置面，尚无证据证明收益。

## 快照中另外三项的处理

- **microsoft/playwright-mcp：** 37,547 stars、Apache-2.0、最近推送 2026-09-18；适合有状态、结构化页面浏览。已有 `adapters/playwright-mcp/`，文档注明固定 npm 版本、配置安全闸与此前撤销注册的原因，当前 `.mcp.json` 也未注册。引用应连到[上游 README](https://github.com/microsoft/playwright-mcp/blob/main/README.md)和本地 ADR/adapter；如未来退役，连同注册项、adapter、manifest 条目一起按 Issue 清理。**不作为新候选重复引入**，先判断 CLI 是否满足真实浏览器用例。
- **openai/skills：** 27,610 stars；仓库级许可证标识为空；最近推送 2026-09-08。当前 [README 明确标记仓库已弃用](https://github.com/openai/skills/blob/main/README.md)，并指向 `openai/plugins`；其说明单个 Skill 的许可证在各自 `LICENSE.txt`。**不再按当前推荐目录引用或整体复制**，具体 Skill 需检查自己的许可与适用性。
- **irons163/three-tier-agent-orchestrator：** 53 stars；仓库级许可证标识为空；最近推送 2026-09-23。README 提供 Sol Max、Luna Max 分层执行的 Skill，但它会把一套多阶段委派流程注入任务，且本地角色已覆盖核心模型分工。引用限于上游 README 说明；不导入未确认许可的文件。若个人试用其独立 Skill，应只删除对应 Skill 目录，不碰其他角色文件。**当前不采纳。**

## 建议优先级

1. **保持模型分工原生化。** 继续使用当前 Astra 主代理和 Luna Max 执行者定义，不加 OMX 或另一套 orchestrator。`openai/plugins` 用于查询最新官方打包方式；具体角色变化先比较本地文件再讨论。
2. **Context7 是最值得按任务启用的通用补充。** 若遇到依赖版本/API 文档检索摩擦，再做独立、可卸载的插件或 MCP 小试；Context7 结果提供检索入口，最终引用库的官方文档。
3. **浏览器任务只比较一种实现。** 若实际 UI 验收频繁，先在隔离工作目录试 Playwright CLI 的内存会话；与当前 dormant MCP adapter 比较上下文量和认证需求后再决定是否替换。没有此类任务时不增加工具。

OpenAI 信息的来源以 [Codex Skills 官方文档](https://developers.openai.com/codex/skills/)、[插件打包文档](https://developers.openai.com/plugins/build/plugins)及 [OpenAI Docs MCP 官方介绍](https://developers.openai.com/learn/docs-mcp)为准。补充的官方 Docs MCP 是只读 OpenAI 文档服务，公开地址为 `https://developers.openai.com/mcp`，可按需作为连接而非 GitHub 子模块评估；它没有 GitHub stars/仓库许可证，故不计入以上六项。技术与能力描述未使用第三方博客或榜单。
