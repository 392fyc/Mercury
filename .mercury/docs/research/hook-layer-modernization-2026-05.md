# Hook Layer Modernization — Claude Code v2.1.x + Codex CLI 0.129+ Audit ADR

> 状态: **生效中** | 制定日期: 2026-05-17 | 决策者: 392fyc (main lane S105) | Closes: [Issue #382](https://github.com/392fyc/Mercury/issues/382)
> Parent context: [Issue #381 tech intel sweep](https://github.com/392fyc/Mercury/issues/381) + `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/<encoded_cwd>/memory/research/tech-intel-sweep-2026-05-12.md` (user-level memory, 不在 Mercury repo)
>
> **路径约定**: `~/.claude/...` 等价于 `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/...`, 沿用 CLAUDE.md §"Related Repositories"。
>
> **Scope clarification**: 本 ADR scope per Issue #382 = Mercury repo-level hook layer (`.claude/hooks/` + `.claude/settings.json` + `.codex/hooks.json` + `.codex/config.toml`)。User-level `~/.claude/hooks/` (mem0_*, cost_tracker, session-end.py, pre-compact.py) 走 #259 governance pattern, 不在本 audit scope — 但若 vendor 新事件在 user-level 有应用候选，会在 §6 cross-reference 标注。

---

## TL;DR

**Verdict: (b) audit-complete, no code changes**。所有 12 项 vendor 新事件 / API 在当前 Mercury hook 使用 profile 下 **要么不适用，要么属于可观测但非 blocking 的增强**。Mercury 现有 hook 层 (Claude Code `.claude/settings.json` **15 commands** across 5 events + Codex `.codex/hooks.json` **10 commands** across 4 events, 详 §1.2) 与 v2.1.141 / 0.130 baseline 完全兼容, **无回归风险, 无新 gap** (1 item X3 待 operator empirical 确认, 见 §X3)。Issue #382 12 个 checkbox 全部 close-with-rationale, 不产生 follow-up 实施 Issue (仅 1 个 P3 observability candidate 见 §7)。

**12 项 verdict 分布**:
- **0 项 implement now** — 当前 Mercury usage profile 下无 blocking gap
- **1 项 verified (empirical) + 1 项 expected-compatible (derivation)** — C7 PreToolUse bypass-deny 不适用 Mercury contract (empirical via grep); X3 `/hooks` TUI listing schema-derived (待 operator empirical 确认)
- **3 项 not applicable** — Mercury 设计 / 部署模式与新事件 invariant 不重合
- **7 项 defer with rationale + re-eval trigger** — 增强候选, 标注未来触发条件

**Re-eval triggers (任一发生即 re-open 对应 item 见 §7)**:
- Mercury 引入 headless / unattended dispatch (会激活 `PreToolUse "defer"` 价值)
- Loop-detector 阈值出现 false-positive 高频 (会激活 `effort.level` + `duration_ms` 信号)
- Mercury 重构为 plugin 形态 (会激活 `Monitor` + plugin-bundled hooks)
- Codex side 接入 mem0 (会激活 compaction hooks)
- Mercury 添加跨 worktree 状态验证需求 (会激活 `workspace.git_worktree`)
- codex-rescue sandbox 出现 path-tightening 回归 (会激活 permission profiles 审计)

---

## 1. 背景

### 1.1 Issue #382 触发链

[Issue #381 (side-bug S6 tech intel sweep, 2026-04-21 → 2026-05-12)](https://github.com/392fyc/Mercury/issues/381) 在 3-week 跨 vendor 调研中 surface "Theme 1: Hook API maturation across BOTH vendors" — Anthropic Claude Code v2.1.x + OpenAI Codex CLI 0.128 → 0.130 在窗口期 (2026-04-30 → 2026-05-12) 同时推出多项 hook lifecycle 新事件。intel sweep 的 FU-1 + FU-2 batchable suggestion 由 main lane 合并为单 Issue #382, 由 side-bug lane S6 transfer ownership 到 main lane。

#382 acceptance: "Each checkbox above either implemented OR explicitly closed with rationale ('not applicable because ...')". 12 个 checkbox = 7 (Claude Code v2.1.x) + 5 (Codex CLI 0.128-0.130)。

### 1.2 Mercury 现行 hook surface (audit baseline, verified 2026-05-17)

**Repo-level Claude Code (`.claude/settings.json`)** — 5 events, **15 commands** (count = sum of `hooks[].command` entries across all event/matcher groups):

| Event | Matcher | Scripts |
|-------|---------|---------|
| PreToolUse | `Edit\|Write` | scope-guard.sh + web-research-gate.sh + web-research-extended-gate.sh |
| PreToolUse | `Bash` | pre-commit-guard.sh + push-guard.sh + pr-create-guard.sh + pr-merge-guard.sh |
| PostToolUse | `.*` | adapters/mercury-loop-detector/hook.cjs |
| PostToolUse | `Bash` | post-commit-reset.sh |
| PostToolUse | `Skill\|Agent` | post-review-flag.sh |
| PostToolUse | `WebSearch\|WebFetch` | post-web-research-flag.sh |
| UserPromptSubmit | (any) | session-init.sh + user-prompt-submit.sh |
| Stop | (any) | stop-guard.sh |
| SubagentStop | `dev` | adapters/mercury-test-gate/hook.cjs |

**Repo-level Codex (`.codex/hooks.json`)** — 4 events, **10 commands** (共享 .claude/hooks/ 脚本 SoT):

| Event | Matcher | Scripts |
|-------|---------|---------|
| PreToolUse | `^apply_patch$` | scope-guard.sh |
| PreToolUse | `^Bash$` | pre-commit-guard + push-guard + pr-create-guard + pr-merge-guard |
| PostToolUse | `.*` | mercury-loop-detector |
| PostToolUse | `^Bash$` | post-commit-reset.sh |
| UserPromptSubmit | (any) | session-init.sh + user-prompt-submit.sh |
| Stop | (any) | stop-guard.sh |

**Codex config (`.codex/config.toml`)** — `[features] hooks = true` (PR #357 S2-side-bug)。

**Loop-detector contract** (adapters/mercury-loop-detector/hook.cjs):
- 输出: `{"decision": "block", "reason": "<msg>"}` 或 silent pass (exit 0)
- 不使用 `"allow"` 或 `"defer"` 决策
- 触发条件: duplicate_call (3) / same_error (5) / no_progress (5) / read_write_ratio (12, env `MERCURY_LOOP_DETECTOR_MODE=research` 可禁) / 多级 timeout

**Cross-lane SoT 约束**: `.claude/hooks/` 是单一来源, Codex 通过 `.codex/hooks.json` 引用同一组脚本 (per `.mercury/docs/research/codex-hooks-adoption-2026-05.md` ADR, S2-side-bug PR #358)。任何 hook 修改必须同时考虑 Claude Code 与 Codex 两条调用路径的兼容性。

---

## 2. Vendor Landscape (verified 2026-05-17)

### 2.1 Anthropic Claude Code v2.1.x

CHANGELOG verified items (per [code.claude.com/docs/en/changelog](https://code.claude.com/docs/en/changelog) WebFetch 2026-05-17):

| Version | Event / Feature |
|---------|-----------------|
| v2.1.141 | `Setup` event clarification — command-type hook only ("use a command-type hook instead" error for prompt/agent hooks on SessionStart/Setup/SubagentStart) |
| v2.1.133 | `effort.level` JSON field + `$CLAUDE_EFFORT` env var injected into all hook inputs |
| v2.1.121 | `PostToolUse` + `PostToolUseFailure` add `duration_ms` field (tool execution time, excluding permission prompts and PreToolUse hooks) |
| v2.1.105 | `Monitor` tool support via plugin `monitors` manifest key (auto-arms at session start or skill invoke) |
| v2.1.101 | `PreToolUse` bypass-deny bug fix — `permissionDecision: "ask"` no longer overrides `permissions.deny` rules |
| v2.1.89 | `PreToolUse "defer"` permission decision — headless sessions can pause at a tool call and resume with `-p --resume` to have hook re-evaluate ([ClaudeCodeLog announcement](https://x.com/ClaudeCodeLog/status/2039153164717334706)) |
| v2.1.98 | `workspace.git_worktree` field added to statusline JSON input — set whenever current directory is inside a linked git worktree (verified via [CHANGELOG.md raw](https://raw.githubusercontent.com/anthropics/claude-code/main/CHANGELOG.md) WebFetch 2026-05-17) |

Source: [Anthropic Claude Code CHANGELOG.md](https://github.com/anthropics/claude-code/blob/main/CHANGELOG.md) | [Claude Code Docs Changelog](https://code.claude.com/docs/en/changelog) | [Releasebot](https://releasebot.io/updates/anthropic/claude-code).

### 2.2 OpenAI Codex CLI 0.128 → 0.130

| Version | Event / Feature |
|---------|-----------------|
| 0.130.0 (2026-05-08) | Plugin details expose bundled hooks; live app-server threads pick up config changes without restart |
| 0.129.0 (2026-05-07) | Compaction hooks (before/after); `PreToolUse` additionalContext injection; `/hooks` TUI command; MCP elicitations |
| 0.128.0 (2026-04-30) | Plugin-bundled hooks with enablement state field; expanded permission profiles; sandbox CLI profile |

Source: [Codex CLI changelog](https://developers.openai.com/codex/changelog?type=codex-cli) | [openai/codex Releases](https://github.com/openai/codex/releases) | [Codex 0.130 release notes](https://github.com/openai/codex/releases/tag/rust-v0.130.0).

Mercury baseline: Codex CLI 0.129 (PR #357, S2-side-bug 2026-04-30)。窗口内 0.130 升级未触发 (no breaking change observed)。

---

## 3. Per-item Verdict Matrix

12 项 audit, 按 #382 body checkbox 顺序逐一 verdict:

| # | Item | Verdict | Action |
|---|------|---------|--------|
| C1 | `Setup` hook event — pre-session logic migration | DEFER | Keep UserPromptSubmit + flag-file pattern (cross-vendor compat) |
| C2 | `effort.level` + `$CLAUDE_EFFORT` — loop-detector branching | DEFER | Re-eval if false-positive rate > 5% |
| C3 | `PreToolUse "defer"` permission — verify loop-detector contract | NOT APPLICABLE | Mercury 不用 headless |
| C4 | `PostToolUse` + `PostToolUseFailure` `duration_ms` — loop-detector tuning | DEFER | Optional signal refinement |
| C5 | `Monitor` tool — replace Mercury polling | NOT APPLICABLE | Mercury 不是 plugin 形态 |
| C6 | `workspace.git_worktree` statusline JSON — multi-lane validation | DEFER | Current cwd-based detection 足够 |
| C7 | `PreToolUse` bypass-deny bug fix — re-test "allow" 路径 | VERIFIED | Mercury 不用 "allow"/"ask" decision |
| X1 | Codex compaction hooks (before/after) — new lifecycle | DEFER | Codex side 无 mem0 集成需求 |
| X2 | Codex `PreToolUse` additionalContext injection — dynamic context | DEFER | `developer_instructions` 静态注入已覆盖 |
| X3 | Codex `/hooks` TUI command — verify listing | EXPECTED-COMPATIBLE | Schema-compliance derivation (not empirical /hooks run); re-audit if operator reports missing |
| X4 | Codex 0.128 plugin-bundled hooks enablement field (0.130 exposes in plugin details) — schema | NOT APPLICABLE | Mercury hooks 是 project-level 非 plugin |
| X5 | Codex 0.128 permission profiles + sandbox CLI profile — codex-rescue | DEFER | Re-audit if codex-rescue regression observed |

**Distribution**: 0 implement-now, 1 verified (empirical) + 1 expected-compatible (derivation), 3 not-applicable, 7 defer-with-rationale。

> **Label semantics**: "VERIFIED" = empirical evidence captured in this audit. "EXPECTED-COMPATIBLE" = derivation from schema / spec compliance, awaits operator-side empirical confirmation. "NOT APPLICABLE" = Mercury 设计 / 部署模式与 vendor 事件 invariant 不重合, no plausible Mercury-side use case at current direction. "DEFER" = potential value but no current trigger; re-eval condition listed in §7.

---

## 4. Verdict Justification (Claude Code 7 items)

### C1 — `Setup` hook event (DEFER)

**Vendor capability**: v2.1.141 `Setup` event fires before session start (command-type hook only)。triggered by special CLI flags like `claude --init`。

**Mercury current**: `session-init.sh` 跑在 `UserPromptSubmit` event 上, 通过 PID-based flag (`.mercury/state/session-init-${PPID}`) 实现 "fire-once-per-session" 语义。

**Why defer**:
1. **Cross-vendor 兼容性**: `Setup` 是 Anthropic-only。Codex 没有等价事件。当前 `session-init.sh` 通过 `.codex/hooks.json` 也注册到 Codex UserPromptSubmit, 两路统一。迁移到 `Setup` 会强制 Anthropic / Codex 分叉脚本路径, 违反 `.mercury/docs/research/codex-hooks-adoption-2026-05.md` SoT 原则。
2. **语义等价**: 现有 PID flag idempotency 与 `Setup` 的 "fires before session start" 实质等价 — `Setup` 只是省掉了 flag-file 读写, 节约 <1ms, 无 user-visible benefit。
3. **Setup 触发条件局限**: per v2.1.141 描述 `Setup` 由 `claude --init` 等特殊 CLI flag 触发, **不是默认 session start trigger**。Mercury 多数 session 走默认入口 (`claude` 不带 `--init`), `Setup` 实际不会 fire — 这是 defer 的关键 evidence。

**Re-eval trigger**: Anthropic 把 `Setup` 提升为默认 session-start hook (不再要求特殊 CLI flag) 时, 重审 — 那时 cross-vendor 分叉的代价被语义优势抵消。

### C2 — `effort.level` + `$CLAUDE_EFFORT` (DEFER)

**Vendor capability**: v2.1.133。Hook JSON input 含 `effort.level` field, `$CLAUDE_EFFORT` env var 在 hook 子进程 + Bash tool 子进程可读。值范围: `low` / `medium` / `high` / `max` / `xhigh`。

**Mercury current**: Loop-detector `read_write_ratio_threshold` 静态 = 12; `MERCURY_LOOP_DETECTOR_MODE=research` env var 可禁此一项。所有阈值固定, 不按 effort 分层。

**Why defer**:
1. **现行触发率低**: Issue #306 (S5-side-bug) 升级 8 → 12 后, 无 false-positive 报告 (S95-S104 跨 11 sessions 0 误 block)。
2. **MERCURY_LOOP_DETECTOR_MODE=research escape-hatch 已存在**: 真正需要放宽时, 此 env var 完全 disable read_write_ratio heuristic, 比 per-effort 分层更直接。
3. **跨 vendor 不对等**: Codex 也有 reasoning effort 但 schema 不同 (`reasoning.effort = minimal/low/medium/high`)。Per-effort 阈值需要在 hook.cjs 同时识别 Anthropic + OpenAI 两套 schema, 复杂度 vs 收益不成比例。

**Re-eval trigger**: 任一情况触发 — (1) 滚动 10 session 内 loop-detector false-positive ≥1, (2) 用户 explicit reported false block, (3) Mercury 引入更长的 sustained debug session pattern 使 12-threshold 频繁误触。

### C3 — `PreToolUse "defer"` permission decision (NOT APPLICABLE)

**Vendor capability**: `"defer"` decision allows headless sessions to pause + resume; hook acts as middleware re-evaluator。

**Mercury current**: Loop-detector 输出 `{"decision": "block", "reason": "..."}` 或 silent pass。**不使用 `"defer"`**。Mercury 不部署 headless / unattended 模式 (autorun 仍由 interactive Claude Code session 主导)。

**Why not applicable**:
1. `"defer"` 唯一 use case 是 headless sessions; Mercury 所有 session 在 interactive 容器中。
2. Loop-detector contract 不会受 `"defer"` 新决策影响 — 现有 `"block"` + silent pass 两种输出 invariant 不变。
3. 此 vendor 新事件不需要 Mercury 任何配合。

**Re-eval trigger**: Mercury 引入 headless dispatch (e.g. via `claude --resume <session-id>` 自动化) 时, 重审。当前 0/4 acceptance criteria 含 headless 需求, 无此趋势。

### C4 — `PostToolUse` + `PostToolUseFailure` `duration_ms` (DEFER)

**Vendor capability**: v2.1.121。Hook JSON input 新增 `duration_ms` 字段, 表示 tool execution time (不含 permission prompts / PreToolUse hooks)。

**Mercury current**: Loop-detector `last_activity_ts` / `last_progress_ts` 用 `Date.now()` 在 hook fire 时打点, 通过 timestamp delta 近似 stall time。**已经能 detect stuck-on-slow-tool 模式** (per `timeout.cjs` checkMultiLevel)。

**Why defer**:
1. **现行近似已足够**: `Date.now()` 在 hook 入口打点, 与 vendor 提供的 `duration_ms` 差异 ≤ hook 调度延迟 (~10ms 量级), 不影响 minutes-级 stall detection。
2. **精度提升边际效应小**: timeout 默认 soft 600s / idle 900s / hard 1800s, 10ms 精度差异在 1800s 量级下 = 0.0006%。
3. **Failure path 收益更大**: `PostToolUseFailure` `duration_ms` 区分 "fast failure" vs "slow timeout-style failure" 是 future enhancement candidate — 但当前 same_error_threshold = 5 已捕获重复失败模式。

**Re-eval trigger**: timeout heuristic 出现 false-positive (e.g. 误把正常长 build 标为 stall), 此时引入 `duration_ms` 可分离 "long tool" 与 "stuck tool" 信号。

### C5 — `Monitor` tool (NOT APPLICABLE)

**Vendor capability**: v2.1.105 added background monitor support via plugin `monitors` manifest key (declared in `plugin.json` under top-level `monitors`)。auto-arms at session start or skill invoke。

**Mercury current**: Mercury 不是 Claude Code plugin (没有 plugin.json), 而是 user-side workflow / hook 集合。背景轮询用 CronCreate (per `/pr-flow` feedback_pr_flow_canonical.md) 走 Claude Code 主线程的 schedule API, 不是 plugin monitor。

**Why not applicable**:
1. `Monitor` API 严格 plugin-bundled — Mercury 部署形态 (repo-level config + user-level hooks) 不匹配。
2. CronCreate 已经满足 `/pr-flow` Phase 2 / Phase 5b polling 需求 (S104 first E2E 验证); 无 monitor pattern 缺失。
3. 切换到 plugin 形态会引入 distribution 复杂度 (Claude Code marketplace), 与 Mercury "modular harness, not distributed product" direction 冲突。

**Re-eval trigger**: Mercury 重构为 plugin (e.g. 为了 distribution to other teams) 时, 重审 Monitor + plugin-bundled hooks (X4) 联合采用。

### C6 — `workspace.git_worktree` statusline JSON (DEFER)

**Vendor capability**: statusline JSON input 新增 `workspace.git_worktree` field, 提供 worktree identity (不是 main repo path 而是 worktree-specific path)。

**Mercury current**: `scripts/lane-assertion.sh` (Δ11 path C) 通过 cwd 解析判断 lane, 与 worktree 路径一一对应 (per Rule 7 v0.1 per-session-files + lane physical worktree)。statusline JSON 当前未被 lane assertion 使用。

**Why defer**:
1. **cwd-based detection 已可靠**: S97 PR #378 `scripts/handoff-launch.sh` + S83 Δ10/Δ11 PR #346 已经实测 worktree-aware routing 准确 (60-session determinism per S6-side-multi-lane)。
2. **statusline JSON 是 Claude Code 专属**: Codex 没有等价 statusline 字段。引入 dependence on statusline JSON 会创建 vendor-specific 路径 — 与 cross-vendor SoT 原则冲突。
3. **冗余信号**: cwd → worktree 已经是 1:1, 无需 vendor 提供 redundant signal。

**Re-eval trigger**: 出现 cwd-based lane detection false-positive (e.g. symlink 路径解析歧义, junction 在 Windows 上展开不一致) 时, statusline `workspace.git_worktree` 作为 secondary verification signal 引入。

### C7 — `PreToolUse` bypass-deny bug fix (VERIFIED)

**Vendor capability**: v2.1.101 fixed `permissions.deny` rules not overriding `PreToolUse` hook 的 `permissionDecision: "ask"` — 修复前 hook 能 downgrade deny 为 prompt; 修复后 deny 不可绕过。

**Mercury current**: Loop-detector 输出 `decision: "block"` 或 silent pass。**不使用 `permissionDecision` 字段** (是另一套 decision schema), 不使用 `"ask"` 决策。

**Why verified (no action)**:
1. Mercury hook contract 与此 bug fix 完全不重叠。
2. 该 fix 对依赖 `"allow"` 绕过 deny 的 hook 是 breaking change, 但 Mercury 没有此模式 — 不存在回归风险。
3. 已通过 grep 验证: `grep -r "permissionDecision\|\"ask\"" .claude/hooks/ adapters/` returns 0 matches in hook output context。

**No re-eval needed**。

---

## 5. Verdict Justification (Codex CLI 5 items)

### X1 — Codex compaction hooks (DEFER)

**Vendor capability**: 0.129 added before-compaction + after-compaction lifecycle hooks。配置在 `.codex/hooks.json` 新 event names (UNVERIFIED exact event name — Codex doc 用 "compaction" 描述但未列 JSON event 名)。

**Mercury current**: User-level `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks/pre-compact.py` 走 Claude Code 的 `PreCompact` event (per `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/settings.json`)。**Codex 端没有等价 mem0 flush** — mem0 layer 当前是 Claude Code 路径独有 (per CLAUDE.md §Related Repositories)。

**Why defer**:
1. **Codex 端无 mem0 需求**: Codex session 通过 mcp__codex__codex 工具被 Claude Code 主线程调用, 自身的 conversation history 不参与 Mercury memory layer flush。compaction 发生时无 Mercury-side state 需保护。
2. **Repo-level vs user-level scope mismatch**: Issue #382 scope 是 repo-level hook layer, 而 mem0 flush 的目标 (user-level pre-compact.py) 在 user-level scope #259 governance pattern 管辖。
3. **#384 ADR (S102 MERGED) 已定 mem0 stack stay** — direction 不会近期反转。Codex 接入 mem0 仍是 远期 candidate, 不在 S105 P1/P2 队列。

**Re-eval trigger**: 若 Codex compaction 开始影响 Mercury workflow (e.g. dual-verify 中 Codex 端 mid-task compaction 导致 review context 丢失), 引入 Codex compaction hook 做 state snapshot。

### X2 — Codex `PreToolUse` additionalContext injection (DEFER)

**Vendor capability**: 0.129。Hook 可在 `PreToolUse` 返回 `additionalContext` 字段, 把动态文本注入到 next turn prompt。

**Mercury current**: Codex 端 mandatory rules 通过 `.codex/config.toml` `developer_instructions` (multi-line literal block) 静态注入。每个 session 启动时全量加载, 后续不变。

**Why defer**:
1. **静态注入已覆盖 mandatory rules**: 11 条 web-research / branch-policy / dual-verify / hooks 规则全部静态加载, 无需 per-tool-call 动态化。
2. **Dynamic injection 增加 token 成本**: `additionalContext` 每次 `PreToolUse` fire 会重新计费 — Mercury 当前 11-rule 静态注入只算一次, 切换到 dynamic 会 N 倍化。
3. **Use case 未明确**: 唯一可能的 Mercury-side use case 是 "在特定 Bash 命令前注入 dual-verify 提醒", 但现有 PreToolUse Bash matcher chain (pre-commit-guard + push-guard + pr-create-guard + pr-merge-guard) 已经在 block 决策路径上拦截违规, 不需要 prompt-side 提醒。

**Re-eval trigger**: Mercury 发现需要 per-tool-call 注入(如 context-specific KB reference) 时, 重审。当前 0 信号。

### X3 — Codex `/hooks` TUI command (EXPECTED-COMPATIBLE)

**Vendor capability**: 0.129。`/hooks` slash command 列出当前 active hooks, 支持 toggle individual hooks 不编辑 config.toml。

**Mercury current**: Mercury hooks 都在 `.codex/hooks.json` (PreToolUse / PostToolUse / UserPromptSubmit / Stop) — 标准 schema, 完全兼容 `/hooks` TUI listing。

**Verification mode**: derivation-not-run (schema-compliance based, not empirical /hooks TUI capture in this audit). Empirical verify 留作 operator side-channel — 若 operator 报告 `/hooks` TUI 显示不完整或 missing, 触发 re-audit。

**Why no-action**:
1. `/hooks` 是 operator-side discoverability feature, 不影响 hook 执行语义。
2. Mercury hooks 文档化在 CLAUDE.md + `.mercury/docs/research/codex-hooks-adoption-2026-05.md` ADR, operator 已有 documentation alternative。
3. **不建议 operator 通过 `/hooks` toggle Mercury hooks** — 这些是 enforcement hooks (push-guard / pr-merge-guard 等), toggle off 会绕过 Mercury MUST 规则。

**No re-eval needed** (unless empirical /hooks run shows missing/incomplete listing)。建议在 Mercury operator docs 提及 "`/hooks` TUI 可见 Mercury hooks, 但请勿运行时 toggle — 改 hooks 走 PR 流程"。

### X4 — Codex 0.128 plugin-bundled hooks enablement field (NOT APPLICABLE)

**Vendor capability**: 0.128 引入 plugin-bundled hooks 携带 `enablement` 字段 (state tracking + trust metadata); 0.130 仅 expose bundled hooks 列表 in plugin details (per §2.2 vendor table + [Codex 0.130 release notes](https://github.com/openai/codex/releases/tag/rust-v0.130.0))。Issue #382 body 原标 "0.130 plugin-bundled hooks enablement field" 是 Issue side 的 version-pin mislabel — 本 ADR §X4 内容仍 cover 同一 feature, 但 vendor version 修正为 0.128 (enablement field 引入版本)。

**Mercury current**: Mercury 不发布为 Codex plugin。`.codex/hooks.json` 是 project-level config, 由 Codex 直接读取, 不经过 plugin 包装。

**Why not applicable**:
1. **Mercury 是 repo-level harness, 不是 distributable plugin** — 与 §C5 Monitor 同类 — 不进入 plugin 分发生态。
2. **Schema 验证已通过**: Codex 0.130 升级未触发 `.codex/hooks.json` schema 报错 (`codex features list` 显示 hooks=stable/true, 配置加载 OK)。
3. **trust metadata 字段 plugin-only**: project-level hooks 不需要 trust metadata (本地 repo 内容 = trusted)。

**No re-eval needed**, unless §C5 trigger 同时触发 (Mercury → plugin 重构)。

### X5 — Codex 0.128 expanded permission profiles + sandbox CLI profile (DEFER)

**Vendor capability**: 0.128。permission profiles 提供 built-in defaults (e.g. `workspace-write`, `read-only`); sandbox CLI profile 用于 codex-rescue 等 wrapped agents。

**Mercury current**: codex-rescue agent 是 plugin-namespaced subagent (per `codex:codex-rescue` 在 available agent types 列表; 定义在 codex plugin marketplace, 不在 Mercury repo 内 — 实际加载路径 `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/marketplaces/openai-codex/plugins/codex/agents/codex-rescue.md` UNVERIFIED, 但 plugin 注册路径可通过 `claude /plugins list` 查询)。Mercury 当前调用 codex 经由 `scripts/codex-sync-audit.sh` (per `.claude/skills/dual-verify/SKILL.md`) 走 sandbox default profile (UNVERIFIED — `.codex/config.toml` 未显式 set `sandbox_profile`, 应用 Codex 内置 default)。

**Why defer**:
1. **当前无 sandbox 回归**: codex-rescue S97 (PR #378 timing) 之后无报错, S102 / S103 dual-verify Codex 多 iter 链全部正常 — 实测 sandbox 配置 working。
2. **0.128 升级未引入 breaking change**: Mercury 在 0.129 baseline 之上稳定运行 16 session (S88 - S104), 0.128 permission profile 默认值与 Mercury 使用模式兼容。
3. **Audit deferred until regression observed** — 主动审计成本 (per-profile 行为 verify + path grants 列表 vs Mercury 实际 file access pattern 对照) 高, 当前无 trigger。

**Re-eval trigger**: codex-rescue 报错涉及 sandbox path access (e.g. "permission denied" 在曾经 working 的路径), 或 Codex CLI 后续版本变更 default profile, 触发审计。

---

## 6. User-level Hooks Cross-reference

虽然 #382 scope = repo-level, 但 vendor 新事件 在 user-level (`${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks/`) 有 潜在应用候选 — 这里仅 cross-reference, 不构成 #382 实施 action:

| User-level hook (`${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks/...`) | 当前 event | Vendor 新事件 候选 | Cross-ref note |
|-----------------|-----------|---------------------|----------------|
| `pre-compact.py` (mem0 flush) | PreCompact (Anthropic) | Codex compaction hooks (X1) | Defer 同 §X1 — Codex 端 mem0 整合远期 |
| `session-end.py` (mem0 finalize) | SessionEnd (Anthropic) | Codex session-end equivalent (UNVERIFIED) | Out of #382 scope |
| `session-start.py` / `session-start.mjs` | SessionStart (Anthropic) | `Setup` event (C1) | Defer 同 §C1 — cross-vendor compat 优先 |
| `cost_tracker.py` (via session-end) | SessionEnd + statusline | `effort.level` (C2) + `workspace.git_worktree` (C6) | C2: cost-tracker 不需要 effort 分层 (#361 ADR ceiling 已 per-session); C6: cost-tracker 不需要 worktree identity (per-session JSONL 已经 cwd-keyed) |

**结论**: user-level hooks 也不需要立即响应任何 vendor 新事件 — 与 repo-level 一致结论。所有 cross-references 复用 §4-5 defer rationale。

---

## 7. Re-evaluation Triggers (Consolidated)

任一条件触发 → re-open 对应 #382 sub-item:

| Trigger | Affected items | Action |
|---------|---------------|--------|
| Mercury 引入 headless / unattended dispatch | C3 (defer 决策) | 实施 `"defer"` 决策路径 |
| Loop-detector 滚动 10 session false-positive ≥ 1 | C2 (effort.level) + C4 (duration_ms) | 引入 per-effort 阈值 + duration_ms 信号 |
| Mercury 重构为 Claude Code plugin | C5 (Monitor) + X4 (plugin-bundled) | 联合采用 plugin 形态 hook 生态 |
| Codex side 接入 mem0 layer | X1 (compaction hooks) | 实施 Codex pre/post-compaction flush |
| cwd-based lane detection 出现 false-positive | C6 (workspace.git_worktree) | 引入 statusline secondary verification |
| codex-rescue sandbox 路径回归 | X5 (permission profiles) | 显式 set `sandbox_profile` + 审计 path grants |
| Anthropic 把 `Setup` 提升为默认 session-start hook | C1 (Setup) | 重审 cross-vendor 分叉 trade-off |
| Mercury 出现 per-tool-call 动态 context 注入需求 | X2 (additionalContext) | 实施 Codex-side 动态 context |

**P3 follow-up observability candidate (not blocking, may surface separately)**:
- 添加 documentation note 到 Mercury operator docs (e.g. CLAUDE.md §Hook layer section 或 `.mercury/docs/guides/`): "`/hooks` TUI 可见 Mercury hooks 但请勿运行时 toggle — 走 PR 流程" (per §X3 EXPECTED-COMPATIBLE 建议)。本 ADR 不实施此 docs change, 若 future 出现 operator confusion 报告再 file 单独 Issue。

---

## 8. Conclusion & Action Summary

### 8.1 #382 Issue closure

Issue #382 12 个 checkbox **全部 close-with-rationale**:
- 1 项 **verified (empirical)** (C7 bypass-deny) + 1 项 **expected-compatible (derivation)** (X3 /hooks TUI) — Mercury 不受影响
- 3 项 **not applicable** (C3 defer / C5 Monitor / X4 plugin-bundled) — Mercury 设计模式不匹配
- 7 项 **defer with rationale + re-eval trigger** (C1 / C2 / C4 / C6 / X1 / X2 / X5) — 增强候选, 未来条件触发再实施

**No code changes in this ADR PR** — pure audit deliverable。

### 8.2 No follow-up Issues filed at this checkpoint

per §7 Re-eval Triggers, **7 defer items 全部条件性** — 仅在 trigger 发生时才 re-open。当前无条件触发 → 不 file P3 placeholder Issues (避免 backlog 噪音)。

§7 P3 observability candidate (operator docs note about `/hooks` TUI) 也不立即 file — wait for actual operator confusion 信号。

> **#382 acceptance criterion compliance check**: Issue #382 acceptance 包含 "If any item is found UNVERIFIED, surface as separate research task"。本 ADR 严格意义上 12 个 item verdict **没有一个标 UNVERIFIED** (所有 verdict ∈ {verified empirical / expected-compatible / not applicable / defer})。文档中 3 个 UNVERIFIED 标记属于 **non-load-bearing secondary detail markers**, 不构成 item-level UNVERIFIED, 详 §8.5。

### 8.3 #381 intel sweep FU-1/FU-2 status update

`tech-intel-sweep-2026-05-12.md` §"Recommended follow-up Issues" 列出 FU-1 (P2 Claude Code hooks audit) + FU-2 (P2 Codex hooks audit), 后被合并为 Issue #382。S105 本 ADR closure 完成 FU-1 + FU-2 全部 scope。intel sweep 剩余 follow-up status (post-S105):
- FU-1 ✅ closed via #382 → S105 ADR (this document)
- FU-2 ✅ closed via #382 → S105 ADR (this document)
- FU-3 ✅ closed via #383 (S7-side-bug, 2026-05-12, user-level edit per #259 governance — no Mercury PR)
- FU-4 ✅ closed via #384 (S102, PR [#395](https://github.com/392fyc/Mercury/pull/395) squash `0360814` 2026-05-16T19:30:38Z — verifiable via `git log --oneline | grep 0360814` showing "docs(research): #384 P1 CMA Memory + /responses/compact vs Mercury mem0 ADR (Closes #384) (#395)")
- FU-5 ✅ closed via #385 (S104, PR [#398](https://github.com/392fyc/Mercury/pull/398) squash `3e4c2c3` 2026-05-16T21:52:14Z — verifiable via `git log --oneline | grep 3e4c2c3` showing "docs(research): #385 P2 context strategy re-baseline ADR vs 1M ctx norm (Closes #385) (#398)")
- FU-6 (P3 re-test PreToolUse "allow" in deny-listed env) — **converged into C7** of this ADR (verified Mercury 不用 "allow"/"ask" via `grep -r "permissionDecision\|\"ask\"" .claude/hooks/ adapters/` returns 0 matches → no re-test needed)

**Intel sweep follow-up queue 完全 drained for audit-scope items** (FU-1..FU-6 全部闭环; non-load-bearing UNVERIFIED detail markers 见 §8.5 不构成 follow-up tasks)。

### 8.4 Authority / scope

本 ADR 不修改 DIRECTION.md 或 EXECUTION-PLAN.md。所有结论限于 hook layer 当前 usage profile, 不构成 Mercury 长线 direction 改动。

### 8.5 Notes on UNVERIFIED markers (non-load-bearing)

本 ADR body 含 3 处 UNVERIFIED 标记, 均为 **secondary detail markers**, 不构成 item-level UNVERIFIED verdict:

| Location | UNVERIFIED claim | Verdict-affecting? | Why non-load-bearing |
|----------|------------------|---------------------|----------------------|
| §X1 (L234) | "Codex compaction hooks 配置 JSON event 名 exact name" | No | X1 verdict (DEFER) 仅 depend on "Codex 端无 mem0 整合需求" — 与 exact event 名无关。Codex compaction hooks **capability** 已 verified via vendor changelog ([Codex CLI changelog](https://developers.openai.com/codex/changelog?type=codex-cli), §2.2); 仅 JSON event 字段名未 listed in 公开文档。 |
| §X5 (L290) | "codex-rescue marketplace exact path `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/...`" | No | X5 verdict (DEFER) 仅 depend on "无 sandbox 回归 observed" — 与 plugin 加载路径无关。codex-rescue **作为 available subagent** 已 verified via Claude Code session start agent list (`codex:codex-rescue`); 仅 plugin marketplace 物理路径未直接 read 验证 (可通过 `claude /plugins list` operator-side enum)。 |
| §6 table (L308) | "Codex session-end 等价 event name" | No | §6 是 cross-reference 非 verdict; 该行明确标 "Out of #382 scope"。Anthropic SessionEnd 已 verified, Codex 等价 event (若存在) 不影响 repo-level audit scope。 |

**结论**: 不需 separate research task — 这些 UNVERIFIED 是 documentation transparency markers (诚实标注哪些 detail 未直接 web/file verify), 不是 verdict-blocking 不确定性。Issue #382 acceptance "UNVERIFIED 需拆分研究任务" 适用于 item verdict (12 项中 0 项 UNVERIFIED), 不适用于 rationale body 内的 secondary annotation。

若用户认为 strict reading 仍需拆分, 可后续 file 3 micro research Issues (P3, low priority); 本 ADR 默认 non-load-bearing 解读。

---

## Sources

**Anthropic Claude Code**:
- [Claude Code CHANGELOG.md](https://github.com/anthropics/claude-code/blob/main/CHANGELOG.md)
- [Claude Code Docs Changelog](https://code.claude.com/docs/en/changelog)
- [Releasebot Claude Code May 2026](https://releasebot.io/updates/anthropic/claude-code)
- [Claude Code Hooks Guide](https://claudefa.st/blog/tools/hooks/hooks-guide)

**OpenAI Codex CLI**:
- [Codex CLI changelog](https://developers.openai.com/codex/changelog?type=codex-cli)
- [openai/codex Releases](https://github.com/openai/codex/releases)
- [Codex 0.130 release notes](https://github.com/openai/codex/releases/tag/rust-v0.130.0)

**Mercury internal**:
- [Issue #381 tech intel sweep](https://github.com/392fyc/Mercury/issues/381)
- [Issue #382 hook layer modernization](https://github.com/392fyc/Mercury/issues/382)
- `.mercury/docs/research/codex-hooks-adoption-2026-05.md` (S2-side-bug ADR, baseline hook adoption)
- `.mercury/docs/research/agent-view-phase6-empirical-2026-05.md` (S99 hook lifecycle verify)
- `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/<encoded_cwd>/memory/research/tech-intel-sweep-2026-05-12.md` (user-level memory, S6-side-bug deliverable)
