---
issue: 86
title: "Audit — #86 'Enhanced PR Monitor with autonomous CodeRabbit fix agent' vs Mercury 当前能力 (post-Argus + /pr-flow)"
date: 2026-05-24
session: S132
status: completed
verdict: close-as-subsumed
---

# Audit: #86 PR Monitor 提案 vs Mercury 当前能力

## 背景

#86 (2026-03-26 filed, P1 OPEN) 提案 enhanced PR monitoring workflow autonomously handle **CodeRabbit** review cycles。提案三个核心模块: Thread State Management / CodeRabbit Auto-Notification / Dedicated PR Fix Agent (autonomous sub-agent)。

提案后约两个月里 Mercury 发生了三个材料变化:
1. **Review bot 切换**: CodeRabbit → Argus (Mercury-internal, fix-detection + LLM reply classification + 2-token canonical trigger)
2. **`/pr-flow` skill** 上线 (`.claude/skills/pr-flow/SKILL.md` ~380 行 + Phase 1-7 lifecycle, CronCreate-based polling)
3. **6 个 memory files 文档化最佳实践**: `feedback_pr_flow_canonical.md` / `feedback_argus_canonical_trigger.md` / `feedback_resolve_threads.md` / `feedback_argus_nit_loop.md` / `feedback_review_disagree_strategy.md` / `feedback_argus_quiet_autonomous_approve.md`

#86 提案因此过时, 但其底层"主 agent 不被 review cycles 阻塞"这个目标 **已达成**, 只是通过不同机制。本 audit 系统性比对提案 vs 已有能力。

## 模块 1: Thread State Management

| #86 提案项 | Mercury 现状 | 状态 |
|----------|-------------|------|
| Programmatically list all unresolved review threads on a PR via GitHub API | `/pr-flow` Phase 6 (SKILL.md L316-339) paginated GraphQL `reviewThreads { nodes { isResolved } }` | **IMPLEMENTED** |
| Track thread status (new / addressed / resolved / won't-fix) | Argus 自身维护 thread state; new/addressed/resolved 由 Argus fix-detection 自动处理; won't-fix 由 DISAGREE-cite reply 路径 (Argus LLM classify → ACCEPT / REJECT / ESCALATE) | **SUBSUMED BY ARGUS** (No persistent local DB needed) |
| Auto-resolve threads where the fix has been committed (match file + line range) | pr-flow SKILL.md L17 文档化: "Fix-detection resolve (B-1): Argus compares new commit diff against open threads by file+line. If code at the thread location changed, Argus auto-resolves the thread." | **IMPLEMENTED BY ARGUS** |
| Surface only genuinely unresolved threads to the operator | pr-flow Phase 6 GATE 6 (L316-339): paginated query, counts only `isResolved==false`, blocks merge unless = 0 | **IMPLEMENTED** |

**结论 (模块 1)**: 100% subsumed。

## 模块 2: CodeRabbit Auto-Notification

| #86 提案项 | Mercury 现状 | 状态 |
|----------|-------------|------|
| Parse CodeRabbit review body to extract structured findings (severity, file, line, category) | Mercury 切到 Argus; pr-flow Phase 3c (L163-189) 文档化 Argus 格式: severity emoji + importance: N/10 + Suggestion + Committable suggestion + AI-prompt blocks | **SUBSUMED + EVOLVED** (CodeRabbit → Argus) |
| Auto-classify: Trivial/Nitpick (auto-resolve with acknowledgment) vs Major/Minor (requires fix) | pr-flow Step 3d ("build triage list") + L192-195 severity 映射 (🔴 9-10 MUST / 🟡 7-8 SHOULD / 🔵 1-6 trivial)。Auto-ack 通过 Argus LLM classify DISAGREE replies (ACCEPT/REJECT/ESCALATE), 不是 Mercury 端代码 | **PARTIALLY** (severity 分类 done; "auto-acknowledgment" 路径 由 Argus 而非 Mercury) |
| Generate a concise summary notification for the Main Agent with action items only | 由 agent 在 pr-flow Phase 3 手动 triage 后给出 (Phase 3 GATE 3 "All findings enumerated with action decisions")。No Mercury-side automation 把"汇总通知"包装成 separate notification | **PARTIALLY** (实际目标达成, 但是 in-loop pr-flow 而非 separate notification) |
| Track review round number and delta (new vs duplicate vs resolved findings) | pr-flow Phase 5b cron embeds FIX_COMMIT_TIME, compares `latestArgus.submittedAt > FIX_COMMIT_TIME` for new-activity detection。Round number 由 Argus 自身 footer 维护 (`Iteration N/10` counter, `feedback_argus_canonical_trigger.md`). New vs duplicate vs resolved 增量识别由 Argus 处理 | **PARTIALLY** (Mercury 端有时间戳 delta; round number + new vs duplicate 由 Argus) |

**结论 (模块 2)**: ~75% subsumed。剩余 ~25% 是设计有意分歧 — Mercury 选择把 classification + summarization 嵌进 in-loop pr-flow main agent flow, 而不是 separate notification subsystem。

## 模块 3: Dedicated PR Fix Agent (autonomous sub-agent)

这是 #86 提案的核心架构假设, 也是分歧最大的部分。

| #86 提案项 | Mercury 现状 | 状态 |
|----------|-------------|------|
| **Trigger**: Main Agent dispatches after PR is created and first review arrives | Mercury 直接 main agent drives pr-flow Phase 2 CronCreate polling, 不 dispatch separate sub-agent | **DESIGN DIVERGENT** (实质目标 = main agent 不被阻塞) |
| **Scope**: Read review comments → apply fixes → commit → push → wait for re-review → repeat | pr-flow Phases 3 → 4 → 5 → 5b (iteration 循环 + cap 5) | **IMPLEMENTED** (in pr-flow, 不是 sub-agent) |
| **Isolation**: Runs in its own session/worktree, does not block Main Agent's primary task | CronCreate-based polling 实现 "doesn't block" 目标 — cron fires while main agent does prep work (`feedback_pr_flow_canonical.md` 第 6 条 "Use cron wait windows productively")。 不是 separate session。 | **DESIGN DIVERGENT** (目标达成机制不同; #86 主诉求实质满足) |
| **Escalation policy** by severity (Trivial → auto-handle; Minor → auto-fix; Major → fix+flag; Architectural → escalate) | pr-flow 禁止手动 resolve thread (Argus 做); agent 只做 code-fix 或 DISAGREE-reply。Severity 分级用 pr-flow Step 3d。Architectural / out-of-scope = `feedback_review_disagree_strategy.md` DISAGREE-cite + PR body 明 "Out of scope" (S130 lesson "Scope-creep flag pattern"); iter 3+ all-Minor 触发 `feedback_argus_nit_loop.md` escape-hatch | **PARTIALLY + RE-ARCHITECTED** (escalation 由 DISAGREE-cite path 而非 separate flag-to-main-agent message) |
| **Completion**: Returns summary to Main Agent when PR is approved + merged, or when blocked | pr-flow Phase 6 GATE 6 + Phase 7 cleanup; MAX_ITERATIONS=5 触发 `gh pr comment "Max review iterations reached. Requesting human guidance."` | **IMPLEMENTED** |
| **Communication**: Via Mercury RPC or structured handoff JSON | N/A (main-agent-driven design 不需要) | **N/A** (design divergent) |

**结论 (模块 3)**: 设计有意分歧 — Mercury 选 main-agent-driven + CronCreate, 而非 separate sub-agent dispatch + RPC。但 **底层用户目标 (autorun 不被 review cycles 阻塞 + iterative fix + escalation 路径) 通过不同机制完整达成**。

## Dependencies 项

| #86 列出依赖 | 状态 |
|------------|------|
| Mercury external agent RPC exposure | **N/A** (设计未采用 sub-agent dispatch + RPC 路线) |
| GitHub API thread management (review comments + discussion threads) | **IMPLEMENTED via gh CLI** (REST `pulls/N/comments` + GraphQL `reviewThreads`) |
| CodeRabbit review body parsing (structured extraction) | **SUBSUMED** (CodeRabbit → Argus; Argus 格式 documented in pr-flow Phase 3c) |

## 真实剩余 gap (low-value, 不建议另开 Issue)

1. **Persistent thread-status tracking across PRs** — Argus 自身的 state 已经 authoritative + gh API 始终可查询, 加 local DB 无价值
2. **Bulk-notification dashboard "which PRs need attention NOW"** — Phase 6 GUI Issue/PR dashboard (#416 → PR #425) 已实质覆盖 cross-PR 视图
3. **Severity-based auto-classification 自动化** — pr-flow Phase 3d 手动 triage 实际 only 几秒钟开销, 自动化 ROI 低 (尤其 Argus importance: N/10 已经做了大部分启发式)

## 总结 + 推荐

| 模块 | Subsumed | 备注 |
|------|----------|------|
| 1: Thread State Management | **100%** | 全部 by Argus + pr-flow Phase 6 |
| 2: Auto-Notification | **~75%** | severity 分类 + delta detection done; "main agent summary notification" 由 in-loop pr-flow 替代 |
| 3: Dedicated Sub-Agent | **DESIGN DIVERGENT** | 但底层目标 (autorun 不阻塞 + iterative fix + escalation) 100% 达成, 通过 main-agent + CronCreate 而非 sub-agent dispatch |
| Dependencies | **3/3 resolved** | (1 N/A, 2 implemented, 3 subsumed) |

**Verdict: close-as-subsumed** — #86 提案的实质目标已通过 `/pr-flow` + Argus + CronCreate-based polling + 6 个 memory files 完整达成, 但 architecture 与提案不同 (main-agent-driven 而非 sub-agent dispatch)。剩余 gap 低 value 不值得开 follow-up Issue。

**关闭策略**: PR #441 (Closes #86) merged 后 GitHub 自动 close。无需手动 `gh issue close` 命令; 如运营需要手动关闭则使用显式 repo 限定:
```bash
gh issue close 86 --repo 392fyc/Mercury  # 仅在 PR merge 失败的回退场景使用; 先 gh repo view 确认仓库
```
关闭 comment 链接到本 audit doc + pr-flow SKILL.md + 关键 memory files。

## 关键参考

`$MERCURY_MEMORY_DIR` 是 Mercury canonical 占位符 (默认 `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/D--Mercury-Mercury/memory`; 见 `.mercury/docs/guides/lane-naming.md` L126-127 + `lane-spawn.md` L49-57 + `lane-close.md` L35-38 — 三 guide doc 全部统一用此 env var)。

- `.claude/skills/pr-flow/SKILL.md` (~380 行 — Phase 1-7 lifecycle)
- `$MERCURY_MEMORY_DIR/feedback_pr_flow_canonical.md` (S103 directive)
- `$MERCURY_MEMORY_DIR/feedback_argus_canonical_trigger.md` (S108 canonical 2-token trigger)
- `$MERCURY_MEMORY_DIR/feedback_resolve_threads.md` (S21 fix-detection mode)
- `$MERCURY_MEMORY_DIR/feedback_argus_nit_loop.md` (iter 3+ escape-hatch)
- `$MERCURY_MEMORY_DIR/feedback_review_disagree_strategy.md` (S128 consolidated-comment workaround)
- `$MERCURY_MEMORY_DIR/feedback_argus_quiet_autonomous_approve.md` (S131 8min × 3 quiet ticks)
- `$MERCURY_MEMORY_DIR/feedback_prflow_after_commit.md` (push 后必须等待 — directly supports Module 3 iterative-fix path)
- `$MERCURY_MEMORY_DIR/feedback_cron_safety.md` (cron trigger 上限 — supports Module 2 polling cap claim)
- Phase 6 GUI Issue/PR dashboard #416 → PR #425 (cross-PR visibility, partial "main agent awareness")
- PR #171 (CodeRabbit → generic review-bot migration — supports "CodeRabbit → Argus" framing)
- PR #211 (reviewDecision/latestReviews in pr-flow polling — supports Module 2 delta-detection claim)
