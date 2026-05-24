# Issue #289 — Claude Design + Claude Code Routines: Phase 1 决策调研

> **Status**: Phase 1 research deliverable (Issue #289 stays OPEN as watch tracker)
> **Date**: 2026-05-24 (S133, main lane)
> **Scope**: 研究 + 决策文档,不做实装。本 doc 解决 #289 body 的 4 个 UNVERIFIED open questions,并给出 Phase 2 决策建议。
> **Research protocol**: 所有结论对照官方 Anthropic / Claude 文档核实 (MANDATORY RESEARCH PROTOCOL)。核实日期 2026-05-24。

## 1. 背景

Anthropic 2026-04 发布两个与 Mercury 可能正向协同的功能:

- **Claude Design** — Anthropic Labs research preview (2026-04-17)
- **Claude Code Routines** — cloud-hosted scheduled / API / GitHub-triggered agent runs (2026-04-14)

#289 body 已完成 Part A (Claude Design) / Part B (Routines) / Part C (组合设想) 的能力调研与重合度评估,但遗留 4 个 UNVERIFIED open questions。本 doc 逐一核实并据此给出 Phase 2 决策。

## 2. 4 个 open questions 核实结果

### Q1 — Claude Design 自动化接口 → **VERIFIED: 纯 web UI,无 API/CLI/automation**

Claude Design 当前是纯 Web UI,**无 API、CLI 或任何自动化接口**。

- 官方公告页描述的全部交互方式: claude.ai/design 上的对话、内联注释、直接编辑、自定义滑块 (adjustment knobs)。
- 唯一程序化衔接点是 **"handoff bundle"**: 设计完成后打包成 bundle,用一条指令传给 Claude Code。这是手工触发,不是 API。
- 公告原文: *"over the coming weeks, we'll make it easier to build integrations with Claude Design, so you can connect it to more of the tools your team already uses"* — 集成能力是**计划中**,截至发布时**尚不存在**。

**对 Mercury 的意义**: Claude Design 无法被 Mercury 脚本化/自动化调用,只能经 handoff bundle → Claude Code 手工路径使用。#289 body 中 A3 (periodic state deck) / A5 (UI mockup automation) 在当前阶段**无法 automation 化**,确认了 body 中对应的 UNVERIFIED 推测。

来源: <https://www.anthropic.com/news/claude-design-anthropic-labs>

### Q2 — Routines 对 `.claude/agents/*.md` + skills 的支持 → **PARTIALLY VERIFIED**

**Skills (`.claude/skills/*/SKILL.md`): VERIFIED 支持。**

Routines 官方文档原文: *"The session can run shell commands, use **skills committed to the cloned repository**, and call any connectors you include."* 项目级 skills 存放在 `.claude/skills/<name>/SKILL.md`,属 "Project" 范围,可 commit 到版本控制;Routine 克隆仓库后自动加载。

**Custom Agents (`.claude/agents/*.md`): UNVERIFIED — 官方文档未直接确认 Routine 启动时主动加载。**

- Routines 文档只列举 "skills",未单独说明 `.claude/agents/` 是否被加载。
- Skills 文档有一条相关限制 (上下文是 `--add-dir` flag,非 Routine 克隆行为): *"Other `.claude/` configuration such as **subagents**, commands, and output styles is **not loaded from additional directories**."* 表明 subagent 加载存在额外约束。
- Skills 文档的 `agent:` 字段确认 skill 执行时可引用 *"any custom subagent from `.claude/agents/`"* — 即 skill 主动 invoke 时 custom agent 可用。但 **Routine 启动时是否默认加载** `.claude/agents/*.md` 官方未明说。

**对 Mercury 的意义**: Mercury 的 9 个 agents (`.claude/agents/*.md`) 在 Routine 环境中能否原样工作存在不确定性。Skills (12 个) 可用。若 Phase 2 PoC 依赖 dev/acceptance/critic 等 subagent,需先实测 Routine 是否加载它们;若只依赖 skill (如 `schedule` / `kb-lint`),则确定可用。

来源: <https://code.claude.com/docs/en/routines> + <https://code.claude.com/docs/en/skills>

### Q3 — Routines beta/版本状态 → **VERIFIED: 仍 research preview,有文档化破坏性变更策略**

- **Beta header `experimental-cc-routine-2026-04-01` 仍为当前有效 header**。`/fire` 端点每次请求必须携带,缺失返回 `400 invalid_request_error`。截至 2026-05-24 无更新版本。
- **稳定性标签**: 仍处于 **"research preview"**。
  - code.claude.com 文档 Note: *"Routines are in research preview. Behavior, limits, and the API surface may change."*
  - platform.claude.com Warning: *"This is an experimental API. Request and response shapes, rate limits, and token semantics may change."*
- **破坏性变更策略 (已文档化)**: *"Breaking changes ship behind new dated beta header versions, and the two most recent previous header versions continue to work so that callers have time to migrate."* — 破坏性变更发布新 `experimental-cc-routine-YYYY-MM-DD` header,旧 2 版继续工作。目前仅 1 个版本,无历史迁移记录。

**对 Mercury 的意义**: API 稳定性风险**可控** — 有 2-版本兼容窗口,不会一夜 break。但 "research preview" 标签未摘,#289 的 "Routines drop research preview" re-check trigger **尚未满足**。

来源: <https://code.claude.com/docs/en/routines> + <https://platform.claude.com/docs/en/api/claude-code/routines-fire>

### Q4 — Routines 分支限制与推送权限 → **VERIFIED**

- **默认行为**: *"By default, Claude can only push to branches prefixed with `claude/`. This prevents routines from accidentally modifying protected or long-lived branches."*
- **解除限制**: *"To remove this restriction for a specific repository, enable **Allow unrestricted branch pushes** for that repository when creating or editing the routine."* — **按仓库**粒度 (Connectors and Permissions → Permissions),非全局开关。
- **"unrestricted" 含义**: 允许 push 到**任意现有分支**,不限 `claude/` 前缀。官方文档未细化是否能直推 `main`/`master`/`develop` 等受保护分支 (这些另受 GitHub branch protection 约束)。
- **安全前提**: MCP connector 文档说明 *"Claude can use every tool from an included connector, including writes, without asking for permission during a run"* — Routine 整体设计是**无审批运行**,分支限制是主要安全护栏;通用原则是 *"Scope each of those to what the routine actually needs"* (最小授权)。

**对 Mercury 的意义**:
- Mercury 用 `feature/TASK-*` / `feat/issue-N-*` / `fix/issue-N-*` 分支约定 — 默认 Routine 模式**无法推送**这些分支 (非 `claude/` 前缀)。
- 两条路径: (a) 让 Routine 推 `claude/*` 分支再人工/另一 routine 转 PR;(b) 对 Mercury repo 开 unrestricted。
- **hook 兼容性 (project-level vs user-level — 部分 UNVERIFIED)**: Routine 是 Anthropic cloud 上的完整 Claude Code session,会克隆 repo。需区分两类 hook:
  - **project-level** (`.claude/hooks/push-guard.sh`,经已提交的 `.claude/settings.json` 注册) — 随 repo 进入 cloud checkout。Claude Code 通常从 repo 的 `.claude/` 读取 hooks,故 cloud Routine **很可能**加载它;但官方 Routines 文档只显式确认 skills,未确认 hook 执行 → **列为 UNVERIFIED,PoC 实测**。
  - **user-level** (`~/.claude/` 下的 hooks/scripts,见 CLAUDE.md "Related Repositories") — *(推断: cloud session 不挂载用户本机文件系统)* cloud 中应不存在用户本机的 `~/.claude/`,故 user-level hook 不会在 cloud 执行。
- 不依赖上述 UNVERIFIED 点的可靠护栏是: 默认 `claude/*` 分支限制 + GitHub branch protection (develop/master require PR + 1 approval,见 `.mercury/docs/guides/git-flow.md`),二者使 Routine 即便 unrestricted 也无法绕过 PR 流程直推受保护分支。最小风险路径 = **保持默认 `claude/*` 限制**,Routine 只产出 `claude/*` 分支 + 开 PR,人工/Argus review 后再 merge。

来源: <https://code.claude.com/docs/en/routines>

### URL 有效性核查

| URL | 状态 |
|-----|------|
| <https://www.anthropic.com/news/claude-design-anthropic-labs> | 有效 |
| <https://code.claude.com/docs/en/routines> | 有效 |
| <https://platform.claude.com/docs/en/api/claude-code/routines-fire> | 有效 |
| <https://code.claude.com/docs/en/skills> | 有效 |
| <https://claude.ai/design> | 403 (需登录,非死链) |

#289 body 引用的全部官方 URL 均有效,无死链/移动。

## 3. 决策建议

### Claude Design (Part A)

**结论: 现阶段不介入 Mercury 架构层。** 无 automation API,只能 ad-hoc web 使用 + handoff bundle → Claude Code。#289 body 的 A 系列场景 (A1-A5) 在无 API 前均无法自动化。维持 #289 的 re-check trigger: **Claude Design 发布 automation API / CLI 时**再评估。用户可随时手工经 web UI + handoff bundle 使用,无需 Mercury 介入。

### Claude Code Routines (Part B)

**结论: 有限度 PoC 可行。** 下列 3 个约束**塑造 PoC 选型**,而非由单个 PoC 一次性全部实测 (见 §4: 推荐的 B3 只观察 Q3 + 运行成本,Q2/Q4 另开独立探针)。

约束:
1. **Custom agents 加载未确认** (Q2) — PoC 选型应避开**依赖 `.claude/agents/` subagent** 的场景;custom-agent 加载行为留独立探针实测,不在首个 PoC 内顺带验证。
2. **research preview 标签未摘** (Q3) — 接受 API 可能变动 (有 2-版本兼容窗口缓冲);不做长期硬依赖。这是任何 routine PoC 都共同承受的操作风险,可在首个 PoC 中观察。
3. **分支推送限制 + project-level hook 在 cloud 的执行行为未确认** (Q4) — PoC 保持默认 `claude/*` 限制,Routine 只产出 `claude/*` 分支,不开 unrestricted,依赖 GitHub branch protection 作护栏;project-level hook (push-guard) 是否在 cloud 执行留独立探针实测。

> ⚠️ **结论的适用边界 (避免被误用为通用结论)**: 本节 "PoC 可行" 的结论**仅适用于不依赖 Q2 (custom agents) / Q4 (project-level hook) 这两项未实测能力的 routine**。Q2 与 Q4 的 cloud 行为目前是 **UNVERIFIED 待实测状态**,不能据本 doc 推断 "Mercury 的 agents/hooks 在 Routine 中可用"。任何依赖 custom agents 或 project-level hook 的 Routine 用法,必须先经 §4 列出的独立探针验证后才能下结论。

**推荐 PoC 场景 (沿用 #289 body Phase 2 建议): B3 — nightly Issue triage。**
- 理由: 低风险 (只读 Issue + post comment,不改代码不推分支) / 输出可验证 (列 "30+ days no activity" 清单) / 不触 push-guard (无分支推送) / 不依赖 custom agents (纯对话 + GitHub MCP connector)。
- B3 **规避 Q2 (custom-agent 加载) + Q4 (分支推送) 两个约束**;Q3 research-preview 约束**仍然适用** (B3 仍是一个 routine,无法消除该约束),但 B3 是该约束下风险最低的首个 PoC。
- 替代候选 B1 (PR review fallback) 风险更高: 与 Argus 重合 + 需 PR 写权限 + quality/cost/latency 需长跑对比,留作 B3 成功后的第二步。

### 与 Mercury 现有调度设施的关系 (确认 #289 body 评估)

| Mercury 现有 | Routines 关系 | 建议 |
|------|------|------|
| `schedule` skill (wraps `/schedule` CLI) | 同一物的 CLI 前端 | 保留 |
| NAS cron (argus-selfcheck 等) | 互补 — *(推断: cloud 执行)* Routine 跑在 Anthropic cloud,访问不到 NAS 内网/`~/.claude/`/`D:/Mercury/` 本机路径 | NAS-local 留 cron |
| GitHub Actions | 互补 — Actions 适合 deterministic CI,Routine 适合对话式 agent | 并用 |
| Argus (NAS Docker) | 可并跑对比 (B1,PoC 第二步) | 暂不动 |

## 4. Phase 2 行动项 (本 doc 不实施,留 follow-up)

若决定 PoC,开 follow-up Issue 实施:
1. 选 **B3 nightly Issue triage** routine (只读 Issue + post comment,不推分支、不调 subagent)
2. 默认 `claude/*` 分支限制 (不开 unrestricted)
3. GitHub MCP connector,最小授权 (读 Issue + 写 comment)
4. 跑 1 周,评估 **B3 实际触及的维度**: daily-run quota 消耗 (具体上限数值见官方 routines docs / #289 body;本轮聚焦 4 个 open question,未独立复核该数字) / 输出质量 / API 稳定性观察 (Q3)
5. **B3 范围外的 UNVERIFIED 项需另开探针** (B3 不触及,无法顺带验证):
   - Q2 custom-agent 加载 → 需一个会调用 `.claude/agents/` subagent 的最小 routine 验证
   - Q4 project-level hook (push-guard) 是否在 cloud 执行 → 需一个会触发分支推送的 routine 验证
6. 评估结果回填本 doc 或 follow-up Issue

## 5. #289 watch-tracker re-check triggers (维持 OPEN)

- Claude Design 发布 automation API / CLI (Q1 状态翻转)
- Routines drop "research preview" 标签 (Q3 状态翻转)
- Mercury 账号 tier 变化影响 daily quota
- 任何 user-level scripts (`~/.claude/scripts/`) 迁移到 Routines 的动议

## 参考来源

**Claude Design**:
- <https://www.anthropic.com/news/claude-design-anthropic-labs>

**Claude Code Routines**:
- <https://code.claude.com/docs/en/routines>
- <https://platform.claude.com/docs/en/api/claude-code/routines-fire>
- <https://code.claude.com/docs/en/skills>

> §2 的 URL 有效性表额外列了 <https://claude.ai/design> — 那是 Claude Design 的**产品 app 入口** (login-gated,匿名访问返回 403),不是可引用的文档源,故未列入本"参考来源"内容引用清单。
>
> **Mercury-内部引用** (非外部 vendor 源): branch protection 规则见 `.mercury/docs/guides/git-flow.md`;user-level hooks/scripts 布局见 `CLAUDE.md` "Related Repositories"。文中标注 *(推断)* 的句子 (如 cloud Routine 访问不到本机路径) 是从 "Routine 在 Anthropic cloud 执行" 这一已核实事实推导,非 vendor 直接陈述。
