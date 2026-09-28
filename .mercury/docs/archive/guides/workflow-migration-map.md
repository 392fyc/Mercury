# 7 个 Dynamic Workflow 的迁移归档表

> Issue [#571](https://github.com/392fyc/Mercury/issues/571) / G6-2。编制日期 2026-08-14。
> 编排层实现见 `packages/codex-orchestrator/`，重建样例见其 `examples/codebase-audit.js`。

## 为什么需要这张表

Claude Code 的 Dynamic Workflow 在 Codex 上**没有等价机制**：`enable_fanout` 在 CLI 0.147.0 已标 `removed`，而 Codex 的 subagent 由**模型自主决定**何时 spawn，不是脚本指定拓扑。所以每个现役脚本都要单独决定去处，不能整体搬。

好消息是重建门槛比预想低。把 7 个脚本的原语使用全量数过之后：

| 原语 | 使用情况 |
|---|---|
| `agent()` / `schema` | 全部在用 —— 编排层的核心，已实现 |
| `parallel()` / `pipeline()` | 在用，但都是浅形态（两三个阶段串起来） |
| `log()` | **77 次**，全部用于「被丢弃的工作量必须显式报出来」（#385 护栏） |
| `budget.total / spent() / remaining()` | **0 次**（脚本里的 "budget" 都是自己算的普通常量） |
| worktree 隔离 | **0 次，而且是刻意的** —— `mercury-large-migration.js` 自己写明避开它，理由是 runtime 会自动删除隔离 worktree 且合并回写语义未文档化 |
| 断点续跑缓存 | 脚本层 0 次（那是 runtime 行为） |

所以编排层只做到「档二 + per-item 链」就够（实测 309 行有效代码），不必复刻整个 runtime。

## 归档表

| # | 脚本 | 行数 | 依赖 | 去处 | 理由 |
|---|---|---|---|---|---|
| 1 | `mercury-codebase-audit` | 161 | 无特殊 | **已重建** | 无外部依赖，是最典型的 fan-out + 对抗验证结构。已作为编排层的样例重建并跑通，用它审计编排层自己查出 6 个真实缺陷 |
| 2 | `mercury-multi-source-research` | 130 | web、agentType | **重建（优先）** | 各角度天然独立，纯 `parallel` + schema，是编排层已验证的能力。唯一变量是 web 检索质量，见下方风险 |
| 3 | `mercury-adversarial-plan-review` | 117 | agentType | **重建** | 结构最简单（两轮 `parallel`），不依赖 web 与外部仓，重建成本最低 |
| 4 | `talent-validate` | 693 | agentType、SoT 专用、设计库 API | **重建（分两步）** | 693 行里**相当大一块是纯 JS 确定性逻辑**（L1 结构/枚举/tag/规则引用检查，零 LLM），这部分与 harness 无关、原样可跑。先把确定性部分抽成独立模块验证，再接编排层的 LLM 环节 |
| 5 | `mercury-staleness-audit` | 176 | web、agentType | **降级** | 周期性上游漂移审计，属「只产报告不立项」。可退化为 `codex exec` 单次调用 + 人工触发，不必进编排层 |
| 6 | `mercury-ecc-practice-scan` | 196 | web、agentType | **冻结** | ECC 专用、周期性、只产报告不立项，2026-06 首跑后使用频率低。等真正需要下一次扫描时再决定重建与否 |
| 7 | `mercury-large-migration` | 197 | worktree | **冻结** | 唯一用 worktree 隔离的脚本，但它自己写明**刻意没用**那个特性、改用 per-file ownership。真正需要大规模 codemod 时，用编排层 + `git worktree add` 手工搭比预先重建划算 |

**⚠️ 第 5、6、7 三条的「降级 / 冻结」判断基于使用频率，需要项目所有者确认。** 我的依据是 README 里的描述（周期性、只产报告不立项、首跑日期）与脚本本身的自述，不是实际调用记录。若某个其实在常态使用，应当改判为重建。

## 两项不体现在代码行数里的迁移成本

**一、`agentType` 在 Codex 上没有对应机制。** 六个脚本用到它（`design` / `research` / `critic`）。`.codex/agents/*.toml` 只服务于模型自主 spawn，`codex exec` 拿不到。解法是把 `.claude/agents/*.md` 的角色指令拼进 prompt —— 这是**提示词工程量**（每个角色约 30 行文本），不是代码量，但要重新调试角色行为，且无法保证与原角色完全一致。

**二、web 检索接口正在换代。** 三个研究类脚本重度依赖 WebSearch/WebFetch。Codex 侧对应 `-c web_search='"live"'`（本机接受，**真实检索质量未实测**），而 `web_search_request` / `web_search_cached` 在 0.147.0 的 features 列表里已标 deprecated、`search_tool` 已 removed。这块要额外验证并持续跟进。

## 多少行都买不到的三样（要提前接受）

1. **会话内集成**。Claude 的 workflow 跑在会话里 —— 进度出现在 task panel、结果直接落回会话上下文、启动前有审批卡。Codex 上写的是**外部 Node 脚本**，没有宿主会话可挂，可观测面只有 JSONL 日志。这是架构位置的差异，不是代码量问题。
2. **确定性的原生 subagent 调度**。`multi_agent` 虽然 stable，但只能靠自然语言让模型去 delegate。确定性扇出只能靠外层起 N 个 `codex exec` 进程 —— 这反而更可控，但代价是 `.codex/agents/*.toml` 那套生态对编排层完全无用。
3. **权限模型的 runtime 兜底**。「一个 agent 独占一个文件、绝不并发改同一文件」这条约定在 Codex 上同样成立，但没有 runtime 帮忙兜底，全靠自己的调度器保证。

## 重建时必须带上的四条

这些是编排层实测踩出来的，写脚本时容易漏：

1. **schema 要过 `normalizeSchema()`**。OpenAI 结构化输出强制每层 `type: object` 显式写 `additionalProperties: false`，且严格模式要求 `required` 列出**全部** properties。不满足直接被拒，错误还藏在一层 JSON 里。
2. **返回值必须自己 parse 加校验**。`finalResponse` 是**字符串**，SDK 不做 `JSON.parse` 也不校验 schema。现役脚本里 `(r && r.findings) || []` 这类写法全都建立在「返回值一定是合法对象」的假设上。
3. **未识别的错误一律不重试**。SDK 抛的是裸 `Error`、无错误码无类型，只能靠匹配文案分类。默认重试的后果是对着一个不可重试的错误反复烧配额。同时未识别错误要留痕 —— 那是补分类规则的唯一线索。
4. **每次调用重付基础上下文**。实测一句琐碎 prompt 就吃约 1.4 万 input token，12 路扇出就是约 17 万的固定底。扇出规模要比在 Claude Code 上更克制。

## 相关

- 编排层：`packages/codex-orchestrator/`（`src/` 五个模块 + `scripts/smoke.js` 17 项自检 + `examples/`）
- 迁移总台账：[#571](https://github.com/392fyc/Mercury/issues/571)
- 原 workflow 模板库与触发约定：`.claude/workflows/README.md`
