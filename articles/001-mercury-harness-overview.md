> 作者: 392fyc | 日期: 2026-05-11 | 状态: 草稿，待 main session 审阅

# Mercury harness 设计：让 AI Agent 持续自主工作的轻量框架

---

## 为什么需要 harness 而不是 orchestrator

### Claude Code 已经做得很好的事

2025 年底到 2026 年的 Claude Code 已经是一个能力相当强的 AI coding agent。它原生支持：

- 在单次 session 内自主完成多文件变更、测试、提交、推送
- 通过 sub-agent 机制在同一 session 内并行分发子任务（dev / acceptance 角色分离）
- 通过 hook 系统拦截工具调用前后的生命周期事件（PreToolUse / PostToolUse / SessionStart / SessionEnd / SubagentStop / PreCompact）
- 原生 agent teams 功能（`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`）在单进程内协调多 agent
- 通过 `.claude/agents/*.md` 文件定义可重用的 sub-agent 角色

在单次 session 的边界内，Claude Code 的原生能力已经足够应对绝大多数开发任务。

### Claude Code 做不到的事

但当任务跨越 session 边界，或者需要长时间无人值守运行时，一些关键能力缺失就会暴露出来：

**session context 耗尽后的延续**。Claude Code 的 context window 有上限，耗尽后需要人类手动触发 `/compact` 或新开 session，并手工注入上下文。对于持续 4-8 小时的自主工作来说，这意味着不可避免的人工干预。

**跨 session、跨项目的长期记忆**。Claude Code 内置的 memory 容量有限，不可结构化查询，不跨项目。每次新 session 都需要从头注入背景知识。

**并行 session 之间的防冲突协调**。当同一个 repo 有多个并行进行中的 session（不同功能分支、不同角色的任务）时，没有机制防止 Issue 重复声明、branch 命名冲突、memory 文件互相覆盖。

**关键节点的人类最小干预通知**。Agent 长时间自主工作时，人类无法得知何时完成了 PR、何时需要决策、何时遇到了阻塞。

**质量门禁的机械化强制**。Agent 倾向于早期自我退出（self-approval），或在验证失败时仍声称完成。纯靠 LLM 的自律无法防止这类退化。

这五个问题是 Mercury 存在的原因。不是因为 Claude Code 不够好，而是因为这些问题处于 Claude Code 设计边界之外——它们需要外部基础设施来解决。

### Harness vs Orchestrator：一个关键区分

"orchestrator"这个词在 AI agent 圈子里有特定含义：一个控制多个 agent 做什么、何时做、如何协调的中心层。Mercury **不是** orchestrator。

原因是实用主义的：Claude Code 已经是一个优秀的 orchestrator。它知道何时调用哪个工具，如何拆分任务，如何分发给 sub-agent。在 Claude Code 之上再构建一个 orchestrator，既是重复劳动，也会产生与底层 agent 能力的摩擦——你无法比 Claude Code 更了解怎么协调 Claude Code。

Mercury 的设计选择是做"**harness**"而非 orchestrator。Harness 这个词来自工程领域：它是约束、接口、配套设施的集合，让工具能在更大的系统中工作，而不接管工具的控制流。具体来说，Mercury 的 harness 角色意味着：

- 提供 session 延续的基础设施，但不控制 session 内部的决策
- 提供质量门禁的机械化强制层，但不替代 agent 的判断能力
- 提供并行 session 的防冲突协议，但不集中调度哪个 session 做什么
- 提供通知路由，但不决定何时需要通知

这个区分决定了 Mercury 的整个架构走向：**本体尽可能薄，重功能通过外部项目挂载**。Mercury 自研的，只有外部项目做不到的那部分。

### Mercury 不是什么

明确不是什么有时比明确是什么更重要：

- **不是 CLI wrapper**。Mercury 不包装 Claude Code 的 CLI 接口。session 的启动、停止、内部执行——这些都是 Claude Code 自己的事。
- **不是封闭系统**。每个模块可独立拆卸，可嵌入任何使用 Claude Code 的项目。
- **不是假设模型弱点的设计**。所有架构决策必须在模型能力变强后仍然成立——质量门禁在 Claude 4.7 时有意义，在 Claude 5 时也要有意义，只是触发频率可能降低。

这三条"不是"构成了 Mercury 设计约束的核心（参见 `.mercury/docs/DIRECTION.md` §一"Mercury 不是什么"）。

### Mental Model：模块化、可拆卸、轻量本体

理解 Mercury 的最好方式是把它想象成一套**工具箱**，而不是一个平台：

```
Mercury 本体 (自研，最小化)
├── session-continuity/    # 外部项目无此能力
├── memory-layer/          # 外部项目无此能力
├── notify-hub/            # 外部项目无此能力
├── adapters/              # 外部项目接口转换（唯一耦合点）
└── dev-pipeline/          # 预设开发组编排

外部挂载 (git submodule 或 uvx-pinned-SHA)
├── modules/ohmyclaudecode/ # Stop hook 拦截
├── modules/superpowers/    # 自检清单 + TDD
└── adapters/gpt-image-2/   # 图像生成 (uvx-pinned-SHA mount)
```

每个模块有独立的 README.md（是什么、为什么、怎么用）和 PHILOSOPHY.md（方法论解释）。这不只是文档规范——PHILOSOPHY.md 是让模块能被独立分发和讲解的关键。

---

## Multi-lane 并行 session 协议 v1

### 单链 handoff 的局限

Mercury 早期使用线性 session 链：每次 session 结束时生成一个 handoff 文档，下一个 session 读取该文档继续工作。这个模式在单一工作流下工作得很好，但当需要并行推进时就会崩溃。

具体问题出现在 2026 年 4 月底，当需要同时推进主线开发（main lane）和 multi-lane 协议设计（side-multi-lane）时：

- main lane 的 session-handoff.md 和 side lane 的 session-handoff.md 如果共用同一个 cwd，Claude Code 的 SessionStart hook 在载入 handoff 时会按 mtime 选择最新的文件，导致 **路由 bleed**——main session 启动时误载入 side lane 的 handoff，反之亦然
- Issue 可能被两个 session 同时声明（claim）
- branch 命名没有隔离，容易碰撞
- memory 文件（MEMORY.md / SESSION_INDEX.md）没有写入所有者，两个 session 都可能覆盖

这个根因在 S13-side-multi-lane session 实测确认（参见 `.mercury/docs/research/multi-lane-protocol-2026-04-25.md` §D1 "路由 bleed 失败模式"），推动了 multi-lane 协议的完整设计。

### 8 条规则全景（v1，2026-05-03 接受）

v1 multi-lane 协议通过 8 条规则在技术和流程两个层面防止并行 session 之间的冲突（完整规则见 `memory/feedback_lane_protocol.md`）：

**Rule 1: Issue claim 先行**。工作前必须用 `scripts/lane-claim.sh <lane> <issue>` 给 Issue 打 `lane:<name>` 标签。脚本在 add-label 后立刻 re-query，检测到多个 `lane:*` 标签时退出并通知用户。这解决了 GitHub REST API 非原子性导致的并发 claim 竞争问题。

**Rule 2: Branch 前缀隔离**。新 lane 使用短前缀 `lane/<short>/<N>-<slug>`，总长 ≤40 字符。每个 lane 在 LANES.md 中声明 ≤8 字符的 short name（如 `side-mlane`、`side-bug`）。旧格式 `feature/lane-<lane>/TASK-<N>-*` 向后兼容保留。

**Rule 3: Tmp 目录隔离**。每个 lane 使用 `.tmp/lane-<lane>/` 隔离临时文件。`scripts/lane-sweep.sh` 用三信号（branch commit date + handoff mtime + Issue updatedAt）检测 stale lanes，14 天阈值，report-only，永不自动变更 LANES.md（Rule 6 红线）。Lane 关闭时 `scripts/lane-close.sh <lane>` 原子执行 Status flip + tmp prune。

**Rule 4: Main lane 独占 shared spec 编辑权**。`.mercury/docs/DIRECTION.md` 和 `.mercury/docs/EXECUTION-PLAN.md` 只有 main lane 可以修改。Side lane 只读。想修改 → 开 Issue 请 main 处理。紧急情况（main idle > 48h + 协调 Issue 已开 ≥48h）可通过 `[EMERGENCY-<lane>]` 前缀 PR 升级，但用户是仲裁者。

**Rule 5: Per-lane state 分离**。每个 lane 有自己的 `session-handoff[-<lane>].md` 和 `project_session<N>-<lane>_state.md`。关键 sub-rule **5.1**：side lanes 应使用独立 git worktree（`D:/Mercury/Mercury-<short-name>`），利用 Claude Code 基于 cwd 的项目状态目录命名机制，获得完全独立的 MEMORY.md / SESSION_INDEX.md / session transcripts。

**Rule 6: Lane 注册簿唯一来源**。`memory/LANES.md` 是所有活跃 lane 的唯一来源。每个 lane 只能编辑自己的 section，Status 值为 `active / closed / paused`。

**Rule 7: Per-session-files**（ADR，Phase F.B）。每个 session 写自己的文件 `memory/sessions/S<N>(-<lane>)?.md`，带 YAML frontmatter。MEMORY.md 和 SESSION_INDEX.md 的相关区域通过 `scripts/regenerate-memory-index.sh --in-place` 从 per-session 文件自动重生成，marker-bounded regions 确保幂等性。Phase F.C 通过两个 Claude Code hooks 机械化强制这个纪律：PreToolUse 的 `mercury-memory-index-write-guard.py` 阻止直接写入 canonical 区域，SessionEnd 的 `mercury-memory-index-validator.py` 检测 drift。

**Rule 8: Lane lifecycle autonomy**。每个 lane 拥有其声明 Issue 的完整生命周期——研究、设计、实现、follow-up sub-Issues、关闭。Lane 不是另一个 lane 的子代理，而是并行链。唯一边界：Rule 4（shared spec 独占权）和显式跨 lane 协调 Issue。采用 Spotify "silence as consent" 模式：main lane 在 S74-S82 的不反对即为接受。

### 关键 sub-rules 实证

**Rule 1.1 probe-after-write**（Issue #309 via PR #317）：GitHub REST API 的 add-label 操作不是原子的，两个并发 session 都可能 claim 成功。wrapper 脚本 post-hoc 检测比 v0 的 first-timestamp-wins 策略早很多。

**Rule 2.1 短前缀**（Issue #313 via PR #328）：`feature/lane-side-multi-lane/TASK-313-314-phase-c`（51 字符）在 Mercury S3-S5 实测中已经在窄终端下截断。`lane/side-mlane/313-phase-c`（27 字符）解决了这个问题。

**Rule 5.1 worktree isolation**（Issue #342，S15-side-multi-lane 落地）：在 S13-side-multi-lane 实测了路由 bleed 的失败模式——shared cwd 下 main lane 的 SessionStart hook 按 mtime 载入了 side lane 的 handoff。物理 worktree 隔离从根本上消除了这个风险。

### HARD-CAP at 5 lanes

并发 lane 数量上限设为 5，理论依据三层：

1. **Miller's Law**：人类工作记忆 7±2 单元，下界 5
2. **Google multi-agent research**：3-5 个 agent 最优，20+ 个出现 catastrophic 推理退化（39-70% 准确率下降）
3. **Personal Kanban WIP limits**：3-5 个是持续吞吐量 vs 上下文切换成本的甜蜜区间

`scripts/lane-cap-check.sh` 读 LANES.md 统计 `Status: active` 数量，report-only——不阻塞提交（因为 side lane 无法轻易安装或修改共享 hook，cap 是社会-技术约束而非机械门）。

### 实证：三 lane 共存

截至 2026 年 5 月，Mercury 实际运行过三个并发 lane：

- **main**（`D:/Mercury/Mercury`）：主线开发，负责 production-readiness gates、direction spec
- **side-multi-lane**（`D:/Mercury/Mercury-side-mlane`）：#292 epic，multi-lane 协议自身的研究与实现，7 个 sub-Issues，Phase A-G 全部完成
- **side-bug**（独立 worktree）：#349 / #357 / #374 等 bug 修复 sprint

Side-multi-lane 在 S15+S18 两个 session 完成 path A 端到端验证，S18 关闭 lane，Phase G promotion 落地。这是 Rule 8 "lane lifecycle autonomy"的最重要实证：side lane 全程自主推进 15 步 close checklist，无需 main lane 干预。

### Trade-off：worktree-isolated vs share-cwd

早期（v0）的 share-cwd 模式成本更低（无需 worktree 管理），但 S13-side-multi-lane 的路由 bleed 事件证明这不可靠。Issue #342 作为 forensic record 记录了完整的失败路径分析。

Rule 5.1 选择 worktree isolation 的代价是：每个 side lane 需要额外的 `git worktree add` + worktree-aware path handling（Windows 路径大小写等细节）。收益是：消除了整个类别的 session 路由 bleed 失败。对于长期运行的并行 lane 来说，这个 trade-off 是值得的。

---

## Dev-pipeline + dual-verify + Argus 集成

### Dev-pipeline preset：为什么用 preset 而非动态编排

Mercury 的开发工作流核心是一条三段式线性链：**Main → Dev sub-agent → Acceptance sub-agent**。

这个设计有意使用固定 preset 而非动态编排，原因来自实践教训：动态编排（"Main 根据任务类型决定调用哪些 agent"）引入了 Main 对编排本身的推理负担，降低了可预测性，也难以复现失败场景。Preset 的好处是：

- **行为可预测**：每次运行的 phase 结构相同，易于 debug
- **职责边界清晰**：Main 不写代码，Dev 不做验收，Acceptance 不知道 Dev 的推理过程
- **独立可复用**：整个 skill（`.claude/skills/dev-pipeline/SKILL.md`）可移植到任何使用 GitHub + Claude Code 的 repo

### TaskBundle 作 contract：约束跨 iter carry-verbatim

Dev-pipeline 的核心机制是 TaskBundle——一个在 Phase 1 构建、在整个 pipeline 生命周期内保持不变的 JSON contract：

```json
{
  "taskId": "362-subagent-return-size",
  "issue": "392fyc/Mercury#362",
  "definitionOfDone": [
    "autoresearch Return Contract section added to SKILL.md",
    "dev-pipeline receipt slim format implemented (drop evidence+risks, add dodChecklist[])"
  ],
  "allowedWriteScope": [
    ".claude/skills/autoresearch/SKILL.md",
    ".claude/skills/dev-pipeline/SKILL.md",
    ".claude/agents/dev.md"
  ],
  "mustNotTouch": ["CLAUDE.md", ".claude/agents/acceptance.md"],
  "verifyCommands": ["pnpm run validate-skill-metadata"]
}
```

约束（`definitionOfDone`、`allowedWriteScope`、`mustNotTouch`、`readScope`）在每次 iter 重派时 carry-verbatim。只能添加新约束，不能扩宽或删除已有约束——否则 bundle 作为 contract 的意义就消失了。

在 #362（S92）的实战中，这个机制防止了 dev agent 在 iter-2 修复时 scope creep 到未授权文件。

### Blind acceptance：做和验分离

Acceptance sub-agent 在 Phase 4 接收的是**盲 receipt**（blindReceipt），不包含 dev 的推理、叙述、自我评估或风险判断。它只拿到：

- 变更文件列表（`changedFiles`）
- 验证命令结果（`verifyResults`，包含 exitCode）
- 结构化 dodChecklist（每条 DoD criterion 的 file:line citation，不是叙述）

这个设计解决了 AI agent 质量保障中的核心问题：**让验收者独立于实现者的叙事**。如果 acceptance 能看到 dev 的完整推理，它会被 anchoring bias 影响——倾向于认同 dev 的自我评估。Blind review 强制 acceptance 从代码和测试结果出发独立得出结论。

S92 的实战验证：acceptance 在 Phase 4 PASS 6/6 AC，零 findings——不是因为代码完美，而是因为 dev 在 iter-1 就实现了所有 DoD（在 TaskBundle 约束的严格边界内）。

### Receipt return-size discipline（#362 的工程化动机）

Sub-agent 的返回值是 main session context 膨胀的主要来源。在 #362 实施前，一个典型的 dev receipt 包含 free-form `evidence`（叙述性证明）和 `risks`（风险评估），每次 iter 注入 main context 5-15K tokens。对于一个需要 3-5 iter 的 PR，这意味着 15-75K tokens 的 context 消耗在 receipt 传输上。

#362 将 receipt schema 改为 slim 格式（Issue #362，PR #363，S92）：

```json
{
  "taskId": "362-subagent-return-size",
  "status": "completed",
  "branch": "lane/main/362-subagent-return-size",
  "commitSha": "af50e56",
  "changedFiles": ["..."],
  "verifyResults": [{"command": "...", "exitCode": 0, "summary": "4 tests passed"}],
  "dodChecklist": [
    {"criterion": "Return Contract section added", "met": true, "citation": ".claude/skills/autoresearch/SKILL.md:47"}
  ]
}
```

删除了 `evidence` 和 `risks` 字段，用结构化的 `dodChecklist[]`（每条 DoD 的 citation pointer）替代叙述性证明。Acceptance 仍能验证，但 main session 不再承载 dev 的推理 overhead。Target：每次 receipt < 2K tokens（post-merge soak 测量中）。

### Dual-verify：不同模型、不同视角

每次 PR 创建前必须通过 `/dual-verify`（`.claude/skills/dual-verify/SKILL.md`）——Claude Code deep-review 和 Codex code-audit **并行**执行：

| 责任 | 负责方 |
|---|---|
| TypeScript `tsc --noEmit` / 架构 / 逻辑正确性 | Claude Code |
| 代码风格 / edge cases / error handling | Codex |
| Metrics completeness（所有路径都打线了吗？） | Codex |
| Memory leak（所有 terminal 路径都清理了 Map 吗？） | Codex |
| Windows/PowerShell 兼容性 | Codex |

两个 reviewer 的分工不是随机的：Claude 擅长架构级推理，Codex 擅长穷举式 edge case 检查和平台兼容性。两者视角互补——实测中 split verdict（一个 PASS 一个 NEEDS-CHANGES）是常见的，每次都要修完才能继续。

Codex 通过 `bash scripts/codex-sync-audit.sh` 调用——这是一个同步包装器，阻塞直到 verdict 返回，返回结构化 stdout markers（`===CODEX-SYNC-AUDIT RESULT===`）。这个设计在 Issue #326 中有详细背景：异步路径（通过 codex:rescue subagent 转发）有时导致 verdict 在线程外到达，dual-verify 静默 fall back。同步调用消除了这个 race condition。

### Argus 自托管 PR review bot：iter loop + escape-hatch

PR 创建后，进入 Argus review bot 的 iter 循环。Argus 是一个托管在 NAS Docker 的自建 review bot（参见 `memory/reference_argus_review.md`），行为模型：

- **Fix-detection auto-resolve（B-1）**：新 commit diff 如果覆盖了 thread 所在的 file+line，Argus 自动 resolve 该 thread
- **Reply-aware resolution（C）**：thread 有 agent reply 时，Argus 用 LLM 分类 ACCEPT / REJECT / ESCALATE，最多 3 轮
- **Zero findings → APPROVE**：只有零新 findings + 所有 threads resolved 才 APPROVE

这个行为模型决定了 agent 的策略：**不 pre-resolve thread，不 reply fix comments**（diff 本身就是解释），只在 disagree 时 reply，push 后等 Argus 增量 review。

**iter-3+ escape-hatch protocol**（`memory/feedback_argus_nit_loop.md`）是 13 次跨 PR 应用后沉淀的实战协议：

```bash
# 1. 查 thread IDs
gh api graphql -f query='query { repository(owner: "392fyc", name: "Mercury") {
  pullRequest(number: N) {
    reviewThreads(first: 100) { nodes { id isResolved comments(first: 1) {
      nodes { path line body } } } } } } }'

# 2. resolve all unresolved threads
gh api graphql -f query='mutation { resolveReviewThread(
  input: {threadId: "THREAD_ID"}) { thread { id isResolved } } }'

# 3. 触发 fresh review
# PR 评论: @argus-review review
```

**Argus nit-loop 的根本原因**：Argus 的 review 是 per-line-position 的，不是 per-finding-content 的。如果连续几次 commit 移动了代码行，Argus 会把同一个 scope 问题在新的行号上重新 thread——无法识别这是上一轮已经 ACK 的重复。S96 的 5-iter Argus 链就来自这个原因：PROGRESS_TOOLS scope 争议在 iter-1（line 115）、iter-2（line 83）、iter-4（line 84）各被 threaded 一次。缓解方法：在 PR body 预先说明 scope 决策（S96 已这样做），在 repeat-disagree reply 中引用上一轮 ACK 的 comment ID。

### Trade-off：多 iter 的 wall-clock 成本 vs 早期 bug 发现的 ROI

每次 PR iter 约 5 分钟 wall-clock（3 分钟 Argus eval + 2 分钟 cron 轮询 + reply round-trip）。对于 5-iter PR，这是 25 分钟额外开销。

但 S92 的 Copilot batch 提供了反证：iter-2 后 Copilot 提交了一个新 thread，指出 Phase 3 branch 检查用 `git branch --show-current` 在 worktree flow 下永远 false-negative——因为 Main 的 cwd 在父 branch，不在 dev worktree。这是一个真实 bug（`9c09fc6` 修复），在生产前被发现。如果没有 multi-iter review，这个 bug 会进入生产并在真实 pipeline 运行中引起静默错误。

Mercury 的选择：接受多 iter wall-clock 开销，换取跨 reviewer 的多视角覆盖。

---

## Production-readiness gate 工程化

### 可工程化的 readiness 评估：4-gap 框架

2026 年 5 月初，Mercury 主线开发到了一个关键节点——通过 [Issue #101](https://github.com/392fyc/Mercury/issues/101) evergreen roadmap 的 re-status，识别出 4 个阻碍生产部署的 gaps。这 4 个 gap 不是大型重构，每个都是 5-30 LOC 的精准修复——但必须全部到位，系统才算生产就绪。

这个"4-gap 框架"本身是一种方法论：**不靠"感觉差不多了"来判断生产就绪，而是枚举可验证的技术 gap，每个 gap 对应一个 Issue，关闭 Issue = gap 消除，全部关闭 = 可进行生产部署决策**。

### Gap 1: Cost tracking（#361，S93）

AI agent 长时间自主工作时，token 消耗不透明是运营风险。Gap 1 在 user-level memory layer 加入了 cost tracker：每个 session 的 token 用量和 USD 成本写入 `~/.claude/scripts/cost-tracker/<session>.jsonl`，statusline 显示颜色梯度（绿 < 70% ceiling / 黄 70-89% / 红 ≥90%）。

关键工程决策：cost tracker 实现在 `~/.claude/scripts/cost_tracker.py`（user-level，不在 Mercury repo），通过 SessionEnd hook 注册，与 mem0 memory layer 共享同一个 Python virtual env（`~/.claude/.venv`）。这遵循了 #259 cross-repo governance pattern——变更在 Mercury 内开 Issue 记录（`~/.claude` 路径的变更不走 Mercury PR 流程），Issue 关闭成为该变更的权威记录。

可以用 env var 软关：`MERCURY_COST_TRACKER_DISABLED=1`。

### Gap 2: Sub-agent return-size scoping（#362，S92）

如前文 Section 3 所述，这个 gap 对应 dev receipt 格式从 free-form 改为 slim structured。技术上很小（4 files +95/-11 LOC），但对 main session 的 context 可持续性影响显著。

实测 soak 目标：5+ sessions post-merge 后 main context delta avg < 2K tokens per receipt（vs 之前的 5-15K）。

### Gap 3: Dev-pipeline → notify-hub wire（#369，S95）

Mercury 的 notify-hub（`adapters/mercury-channel-router/` + `adapters/mercury-notify/`）在 Phase 5 PR #295 落地后，到 S95 为止零 callers——只有基础设施，没有任何模块实际发出通知。

Gap 3 是连接 dev-pipeline 和 notify-hub 的最后一跳。在 dev-pipeline Phase 6（pass 后）加入：

```bash
bash scripts/notify-event.sh info "Dev pipeline complete: <taskId>" \
  "verdict=pass | files=<N> | branch=<branch>"
```

`scripts/notify-event.sh` 是一个 fail-safe wrapper——始终 exit 0，router 不可达时返回 `{ok:false,error:"transport"}`，不阻塞 pipeline。这个设计遵循了 Issue #316 的 Acceptable Callers 约束：loop-detector stalls、hook failures、heartbeat 等 anti-patterns 不应调用 notify。

S95 是 mercury-notify 的**第一个真实 caller**，标志着整个 notify-hub 从基础设施变成了有实际用途的模块。

### Gap 4: Loop-detector hard-timeout false-positive reduction（#372，S96）

Mercury 的 loop-detector（`adapters/mercury-loop-detector/`）负责检测 agent 卡死——通过多种信号（连续读操作比、重复 error、重复 input 等）和多级超时（soft → hard）触发缓冲区 reset，防止 agent 无限 spin。

S95 session 触发了 3 次 hard-timeout false positive，forensic 数据见 [Issue #372](https://github.com/392fyc/Mercury/issues/372) body：

| 时间 | elapsed | last_tool | np_count | 触发原因 |
|---|---|---|---|---|
| 2026-05-09T16:30:57Z | 5671s | Read | 0 | last_write_ts stale |
| 2026-05-09T14:00:27Z | 4017s | Skill | 0 | last_write_ts stale |
| 2026-05-09T12:19:48Z | 24768s | Bash | 0 | last_write_ts 6.9h stale |

`np_count=0` 表明 #325（S80）的 PROGRESS_TOOLS fix 工作正常——这些工具被正确分类为 non-stall progress，不触发 np_count stall counter。但 `last_write_ts`（上次写操作时间戳）到 hard-timeout 触发点的距离太长，导致另一个检测路径误触。

Sister-fix #372 引入了 `last_progress_ts` 字段——在 write **或** PROGRESS_TOOLS 任一发生时更新，用 `last_progress_ts` 替代 `last_write_ts` 作为 hard-timeout 的参考点。PR #373 squash-merged 为 commit `44a8bce`，新增 22 个 unit tests，将 timeout 相关测试从 44 增加到 66。

### Gate 4/4 完成：生产部署候选时机

S96 PR #373 合并后，4 个 gap 全部关闭，production-readiness gate **4/4 FULL PASS**：

1. ✅ Gap 1 (#361, S93) — Cost tracking user-level layer
2. ✅ Gap 2 (#362, S92) — Sub-agent return-size scoping
3. ✅ Gap 3 (#369, S95) — Dev-pipeline → notify-hub wire (first user-actionable notify caller)
4. ✅ Gap 4 (#372, S96) — Loop-detector hard-timeout false-positive reduction

用户选择"推广文章 / 方法论编写"作为 S97 方向——这篇文章就是那个决策的产物。

### 工程化渐进 hardening 的方法论价值

这 4 个 gap 每个都是小体量变更，但形成了一个可管理的 roadmap：每个 gap 是独立的 Issue，可以独立实施、独立验证、独立关闭。这与"big-bang 重写"形成对比：

- Big-bang：某天决定"系统需要生产化"，一次大型重构，风险集中，难以回滚
- Gap-by-gap：每次 session 关闭一个 gap，每个 gap 有 forensic record（Issue → PR → commit SHA），随时可以看到当前 readiness 状态

4-gap 框架不是 Mercury 特有的——任何需要评估 AI agent 系统生产就绪度的工程师都可以使用相同的方法：**枚举 gap → 每个 gap 对应 Issue → Issue-first 实施 → 全部关闭 = readiness 达成**。

---

## External adapter ≤200 LOC 硬约束 + cherry-pick 治理

### "不重新发明轮子"原则的工程化

Mercury DIRECTION.md P1 原则的表述非常直接：**能挂载的绝不自研**。这不是口号，而是一个有工程约束对应的规则：

> 如果适配层超过 200 行，说明耦合过深，需要重新评估挂载方式。
> — `.mercury/docs/DIRECTION.md` §四"适配层规范"

200 LOC 边界是 Phase 2-1 的实证产物。在评估 mercury-test-gate adapter（SubagentStop hook enforcement）时，实现完成后的 LOC 是 198——刚好低于 200，但已经"无余量"。这个数字锚定了"适配层可行但不可再扩展"的直觉，从此成为 `adapters/<vendor-name>/` 目录下所有外部集成的硬约束（参见 DIRECTION.md §8-2 line 385）。

**重要 carve-out（Issue #348，PR via S84）**：200 LOC 上限仅适用于 `adapters/<vendor-name>/` 下的外部项目接口转换层，不适用于 `scripts/` 下的 Mercury 内部工具。`scripts/codex-sync-audit.sh`（~360 LOC）和 `scripts/lane-assertion.sh`（~440 LOC）是实现 Mercury 自有协议的内部工具，不受此限制。这个 carve-out 解决了 Argus 反复将 adapter size rule 误用于 scripts/ 的 nit-loop 问题（PR #338 和 PR #346 各自触发了 iter-3+ escape-hatch）。

### adapters/ 目录规范

每个 adapter 的结构：

```
adapters/{project-name}/
  README.md      # 挂载了什么、为什么、怎么用、auth delegation 说明
  invoke.py      # 或 adapter.cjs — 接口转换代码（≤200 LOC 硬约束）
  UPSTREAM.md    # 上游版本记录、已知不兼容项、drift policy
```

Adapter 只做接口转换，不包含业务逻辑。如果需要业务逻辑，说明挂载方式选错了，需要重新设计。

### Cherry-pick 治理协议：6 字段 + 4 文件

当从外部项目 cherry-pick 文件到 Mercury 时，同一个 commit 必须包含所有治理记录（CLAUDE.md §"Cherry-pick protocol"）：

**1. Manifest 条目**（`.mercury/state/upstream-manifest.json`）：

```json
{
  "path": "adapters/gpt-image-2/invoke.py",
  "scope": "project",
  "upstream_repo": "wuyoscar/gpt_image_2_skill",
  "upstream_path": "pyproject.toml",
  "upstream_sha_at_import": "6fdd7243dc9605efcf6d66e9394d3d10fc5141f6",
  "upstream_license": "MIT",
  "import_pr": 354,
  "import_date": "2026-05-08",
  "import_rationale": "Phase 2 Slice A — uvx-pinned-SHA mount per ADR §7.2.1",
  "last_drift_check": null
}
```

**2. SKILL.md frontmatter**：`upstream_source`、`upstream_sha`、`upstream_license`、`cherry_picked_in`、`cherry_picked_at` 字段。

**3. Script header**（shebang 后 5 行注释）：`UPSTREAM / SOURCE / SHA / DATE / ISSUE`。

**4. License gate**：只 cherry-pick MIT、Apache-2.0 或其他 permissive license。`upstream_sha_at_import` 必须通过 `gh api repos/{owner}/{repo}/commits/{sha}` 验证，不从记忆中录入。

这个协议确保了三件事：溯源（知道代码从哪来）、漂移检测（知道上游是否变了）、法律合规（license gate）。

### 实证案例：5 种挂载模式

Mercury 目前落地了多种挂载模式，构成了实践中的"适配层谱系"：

**git submodule（未实际启用，被 uvx-pinned-SHA 替代）**：最初 ADR 建议用 submodule 挂载 `wuyoscar/gpt_image_2_skill`。S87 verify 时发现 uvx-pinned-SHA 模式更轻量，无需 `.gitmodules` + `git submodule init/update`，adapter LOC 自然保持低位。DIRECTION.md §4 目前仍写 submodule，uvx-pinned-SHA 是 ADR 级别的例外（P3 followup if more adapters adopt it）。

**uvx-pinned-SHA mount**（`adapters/gpt-image-2/`，Issue #351 Phase 2 Slice A，S88）：84 LOC invoke.py，通过 uvx 在运行时按 pinned SHA 拉取并隔离运行上游工具。第一次调用约 5-15s 冷启动，后续缓存。`upstream_path: "pyproject.toml"` 追踪上游依赖版本合同，`scripts/upstream-drift-check.sh` 可检测上游版本 bump 影响 adapter 合同的情况。

**claude-handoff plugin**（`https://github.com/392fyc/claude-handoff`）：通过 Claude Code marketplace 以本地插件形式挂载，包含 session_chain SQLite 追踪。用户级安装，不入 Mercury repo。

**mem0 user-level memory**（`~/.claude/scripts/mem0_bridge.py` 等）：跨项目的向量记忆层，通过 SessionEnd hook 注入。不在 Mercury repo，通过 #259 governance pattern 在 Mercury Issue 中记录变更。

**Codex CLI integration**（`.codex/hooks.json`，Issue #357 via PR #358，S2-side-bug）：Codex CLI 0.128+ 的 hooks 格式（JSON），9/13 个 `.claude/hooks/` 脚本被移植。这不是 adapter，是配置 cherry-pick——但同样遵循 upstream-manifest 追踪原则。

**Argus review bot**（NAS Docker，参见 `memory/reference_argus_review.md`）：自托管的 PR review bot，不在 Mercury repo，通过 webhook 集成。最重要的"外部项目挂载"之一，但完全不在 `adapters/` 目录下——因为它是独立运行的服务，不是接口转换层。

### Drift monitoring

`scripts/upstream-drift-check.sh` 定期运行，对 `.mercury/state/upstream-manifest.json` 中每个条目检查上游是否有变更影响当前 adapter 合同。当 `last_drift_check` 为 null 时表示从未检查过。

### Trade-off：维护成本 vs 自研开发量

挂载外部项目的主要维护成本是：需要追上游的变更，adapter 可能因上游 API 变动而失效。

这与自研的 trade-off：自研的维护成本完全由 Mercury 承担，且往往高估了"自研=掌控"——自研代码同样会腐化，且没有外部社区维护。

Mercury 的经验：对于有活跃维护者（stars 数量 + commit 频率 + issue 响应）的外部项目，挂载的 TCO 通常低于自研。关键是 adapter ≤200 LOC 约束——这确保了"适配层"不会变成"二次实现"，当上游出现破坏性变更时，adapter 的修改量是可控的。

---

## 方法论先于工具（P4 原则）

### 代码是方法论的副产品

Mercury DIRECTION.md P4 原则的表述：

> Mercury 的核心价值不在代码里，在方法论里。
> 每个模块都有配套的"为什么这样做"文档，这是开源分享和视频内容的素材来源。

这不是一句谦虚的话，而是一个架构决策：**方法论文档（PHILOSOPHY.md）是一等公民，和代码一起发布，且在代码发布前就必须存在**。因为如果不能说清楚"为什么这样做"，那么代码本身的设计很可能是错的。

Mercury 每个模块的文档要求：

- `README.md`：是什么、为什么、怎么用
- `PHILOSOPHY.md`：方法论解释（视频和文章素材）
- `CHANGELOG.md`：变更记录

其中 PHILOSOPHY.md 是区分"有思考的工具"和"有功能的工具"的分水岭。

### 模块可拆卸性赋能开源分享

P3 原则（模块可拆卸）与 P4 协同工作：任何 skill、hook、agent 定义都可以从 Mercury 中取出，独立用于其他项目。模块间无隐式依赖——如果 A 需要 B，必须在 A 的文档中显式声明。

这意味着：
- `dev-pipeline` skill 可以被任何使用 GitHub + Claude Code 的项目采用，只需复制 `.claude/skills/dev-pipeline/` 目录和两个 agent 文件（`dev.md`、`acceptance.md`）
- `dual-verify` skill 对 Codex CLI 有依赖，但 SKILL.md 中明确列出了 fallback 路径（Codex 不可用时降级为 Claude-only 并在 PR body 中注明）
- `pr-flow` skill 对 Argus bot 有依赖，但 Argus 不在时流程仍可运行（手动 review 替代）

可拆卸性的检验标准很简单：**如果 A 不能独立于 Mercury 其他模块运行，说明耦合过深**（DIRECTION.md §七"开发准则"）。

### Karpathy LLM Knowledge Bases 模式

Mercury 的 memory layer 设计理念来自 Andrej Karpathy 的 LLM Knowledge Bases（2026-04-02），描述了一个渐进式知识沉淀模式：

```
raw data → LLM compile → structured wiki → LLM Q&A → incrementally enhance
```

Mercury 的实践化版本：session 结束时 agent 将本次 session 的关键发现写入 `memory/sessions/S<N>.md`（raw data），`scripts/regenerate-memory-index.sh` 将所有 per-session 文件编译成结构化的 MEMORY.md 索引（structured wiki），下次 session 开始时通过 `system-reminder` 注入相关上下文（LLM Q&A），feedback 文件记录可泛化的行为准则（incrementally enhance）。

这个模式的关键是：**不要试图在一个 session 里建立完整的知识体系，而是每次 session 增量贡献，知识随时间自然沉淀**。session S1 的 feedback 文件（如 `feedback_lane_protocol.md`）到 S96 时已经历了 8 条 rules + 若干 sub-rules 的演化，每一步演化都有 Issue 和 PR 作为 audit trail。

### P5 向上兼容：避免假设模型弱点

P5 原则是整个设计哲学中最反直觉的部分：**架构设计必须确保，模型能力变强 → 模块自然受益（而非失效）**。

具体来说，避免的反模式：
- "因为模型会出错，所以我们用 3 个 agent 投票来提高准确率"——如果模型变得更准确，这个投票机制变成了冗余，但已经集成到架构深处，无法移除
- "因为模型无法处理大 context，所以我们拆分任务"——如果 context window 扩展到 1M tokens，这个拆分逻辑变成了不必要的复杂性
- "因为模型不理解复杂约束，所以我们用状态机监控它的行为"——如果模型的 instruction-following 能力大幅提升，状态机变成了过设计

Mercury 的 dev-pipeline / dual-verify / 质量门禁是否符合 P5？这是一个需要持续追问的问题。当前的判断：

- Dev-pipeline 的 preset chain 主要价值在**职责分离**（做和验不是同一个 agent），这在模型更强时仍然有意义
- Dual-verify 的主要价值在**不同视角**（Claude + Codex 的差异来自训练差异，不来自能力弱点），这在两个模型都变强时仍然有意义
- 质量门禁（acceptance 独立验证、mechanical stop hook）的主要价值在**减少 self-approval bias**，这在 agent 更自信时可能更重要，不会因能力提升而失效

### 演化路径：实证 → 沉淀 → 迭代

Mercury 的 96 个 session（截至 S96）构成了一个清晰的演化轨迹：

- **S1-S20**：探索阶段，方向摇摆（orchestrator → harness 转向发生在 S20），大量架构探索
- **S20-S60**：基础设施建设，Phase 1-3 落地，memory layer、pr-flow、autoresearch skill 等核心模块成形
- **S60-S91**：精炼阶段，multi-lane protocol v1、质量门禁、cost tracker、sub-agent return-size scoping
- **S91-S96**：Production-readiness gate，4-gap framework 逐一关闭，进入可部署状态
- **S97+**：方法论输出阶段（本文即首篇）

每个阶段的结束都不是"完成"，而是到达了一个新的稳定状态，可以从中进一步演化。方法论文章是这个演化的"结晶态"——把已经稳定的实践沉淀成可复用的知识，而不是等到所有东西都完美再发布。

工程实证优先于理论的原则，在 Mercury 的 issue history 里有明确体现：每个方法论决策（lane protocol / cherry-pick governance / adapter ≤200 LOC / receipt slim format）都来自一次实际的失败或边界测试，有 Issue 号、PR 号、commit SHA 可以追溯。这是工程方法论的核心特征——**可证伪、可追溯、可演化**。

---

## References

### In-repo 路径

| 文件 | 说明 |
|---|---|
| `.mercury/docs/DIRECTION.md` | Mercury 项目方向最高准则（P1-P5，模块定义，开发准则，Post-Phase-2 观察） |
| `.mercury/docs/research/multi-lane-protocol-2026-04-25.md` | Multi-lane protocol v0 MVP validation report（5 precedents + 6 adversarial examples） |
| `.mercury/docs/guides/lane-naming.md` | Δ6 短前缀规范 + Δ7 HARD-CAP 5 lanes |
| `.claude/skills/dev-pipeline/SKILL.md` | Dev-pipeline preset chain（Phase 1-6，TaskBundle schema，receipt slim format） |
| `.claude/skills/dual-verify/SKILL.md` | Dual-verify skill（Claude deep-review + Codex sync audit，escape-hatch exit codes） |
| `.claude/skills/pr-flow/SKILL.md` | PR-flow skill（Argus behavior model，escape-hatch protocol，GraphQL resolveReviewThread） |
| `adapters/gpt-image-2/README.md` | uvx-pinned-SHA mount 案例（MIT upstream，env allowlist，drift-check policy） |
| `adapters/gpt-image-2/invoke.py` | Slice A adapter（84 LOC，uvx 包装，env 过滤） |
| `.mercury/state/upstream-manifest.json` | Cherry-pick 治理 manifest（所有外部引用的 SHA + license + PR 记录） |
| `scripts/upstream-drift-check.sh` | Drift monitoring 工具 |

### GitHub Issues / PRs（实证案例）

| Reference | 内容 |
|---|---|
| Issue #292 / PR #340 | Multi-lane protocol research + Phase F.B per-session-files（side-multi-lane）|
| Issue #309-#314, #329-#331 | Lane protocol v0.1 deltas（Rule 1.1 / 2.1 / 3.1 / 3.2 / 4.1 / HARD-CAP / per-session-files + hooks）|
| Issue #342 / PR #344 | Rule 5.1 worktree isolation（routing-bleed 失败模式 forensic record）|
| Issue #351 / PR #354-#356 | Phase 2 Slice A-C（gpt-image-2 uvx mount + image_gen pipeline + animate-frames skill）|
| Issue #361 / PR #367 | Gap 3 cost-tracker user-level layer（#259 governance pattern）|
| Issue #362 / PR #363 | Gap 4 sub-agent return-size scoping（receipt slim format，dodChecklist schema）|
| Issue #369 / PR #370 | Phase 5-3 dev-pipeline → notify-hub wire（first mercury-notify caller）|
| Issue #372 / PR #373 | Gap 1 loop-detector hard-timeout false-positive（last_progress_ts sister-fix to #325）|
| Issue #348 / PR #350 | CLAUDE.md adapter-size internal-tooling carve-out（scripts/ 豁免）|

### 外部资源

| URL | 说明 |
|---|---|
| [git-worktree official docs](https://git-scm.com/docs/git-worktree) | Rule 5.1 worktree isolation 技术基础 |
| [Karpathy LLM Knowledge Bases (2026-04-02)](https://karpathy.ai) | Memory layer 设计理念（raw data → LLM compile → structured wiki）|
| [trunkbaseddevelopment.com](https://trunkbaseddevelopment.com/) | Trunk-based dev vs GitFlow，lane protocol 分支策略背景 |
| [Spotify Squads model (Atlassian)](https://www.atlassian.com/agile/agile-at-scale/spotify) | Rule 4 main-lane spec-exclusivity 的组织参照 |
| [uvx docs (astral.sh)](https://docs.astral.sh/uv/) | gpt-image-2 adapter uvx-pinned-SHA mount 工具 |
