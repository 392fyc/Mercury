# Agent view Phase 6 — file-editing bg + 完整 hook lifecycle + cost tracking 实证 (#386-D)

**Issue**: [#391](https://github.com/392fyc/Mercury/issues/391) (Closes) — sub-Issue of [#386](https://github.com/392fyc/Mercury/issues/386) (Path B follow-on per S98 ADR `agent-view-multi-lane-adaptation-2026-05.md`)

**Status**: Phase 6 empirical complete — Path B 仍是 PRIMARY, but 4 处 mitigation 必须 inline 进 S98 ADR §Path B (本 ADR 同 PR 内 patch S98 ADR)。

**Date**: 2026-05-15
**Session**: S99 (main lane)
**Branch**: `lane/main/391-phase6-empirical` (Rule 2.1 short prefix, 8th main-lane dogfood)
**Scope**: 实测 S98 ADR §Sub-Issue #386-D 的 6 项未验证 unknown — file-editing bg workload + 完整 hook lifecycle + bg cost-tracking integration

---

## TL;DR

S99 sessions empirical 跑两个 bg session probe: probe 1 = file-editing workload (Edit + Write tool 触发 in `D:/Mercury/Mercury/.tmp/phase6-probe/probe1-target.txt`); probe 2 = `--bg --agent research` subagent dispatch ("What is 2+2?")。CLI v2.1.142 (S98 baseline 2.1.141 — minor patch within 30 hours, behavior-equivalent for本 ADR scope)。

6 项 verify 结果：

| # | Sub-Issue 问题 | Verdict | Mechanism |
|---|----------------|---------|-----------|
| 1 | file-editing bg auto-isolation 触发? | ✅ **YES via HYBRID enforce-then-comply mechanism** | platform PreToolUse 拒第一次 Edit on shared checkout 返 `tool_use_error` literal: "This background session hasn't isolated its changes yet. Call EnterWorktree first…" → bg agent 读 error → ToolSearch `EnterWorktree` deferred tool → 调用 `EnterWorktree(name="phase6-probe-1")` → state.json `worktreePath`+`worktreeBranch` populated mid-flight → 重试 Edit in worktree → `ExitWorktree(action:"keep")` 后 fields nullified |
| 2 | mem0_hooks cwd-derived state divergence 风险? | ✅ **NO RISK** | empirical: `~/.claude/scripts/mem0_hooks.py` zero `cwd`/`os.getcwd` references; `~/.claude/scripts/cost_tracker.py` 用 sessionId-keyed storage (line 267 `session_log_path(session_id)`) — 都 cwd-independent |
| 3 | git commits in auto-worktree fate? | ✅ **isolated by design (inferred — probe did not commit)** | auto-worktree 创建在 `<lane-cwd>/.claude/worktrees/<name>/` on `worktree-<name>` branch — branch separation 由 EnterWorktree mechanics guaranteed; reconciliation behavior on `git commit` 未 empirical 测 (probe intent 仅 file edit, no commit verb) — future probe 4 needed for commit-fate verify |
| 4 | 完整 hook lifecycle fire? | ⚠️ **PARTIAL — Stop fires NOT SessionEnd** | empirical observed: SessionStart × 5, UserPromptSubmit, PreToolUse {Read, ToolSearch, EnterWorktree, ExitWorktree, Bash, Write, Edit}, PostToolUse 同集合, PostToolUseFailure, **Stop × N (probe 2 stop_hook_summary hookCount:5, all OMC plugins; Mercury Stop matcher empty)**。NO SessionEnd. PreCompact 不触发 (短 session no compaction needed) — UNVERIFIED |
| 5 | SubagentStop hook fire 在 `--bg --agent X`? | ❌ **NO** | probe 2 transcript 仅 SessionStart + UserPromptSubmit + Stop。SubagentStop 是 nested-subagent 退出 event, `--agent X` 是 bg session template selection 而非 subagent dispatch — 故 SubagentStop 不应 fire (符合 docs design) |
| 6 | bg session API cost tracking 在 #361 framework? | ❌ **NOT INTEGRATED** | empirical: `~/.claude/scripts/cost-tracker/` 在 probe 1+2 完后 +0 jsonl (sessionId 40726463/a8c58664 都无对应 file)。Root cause: Mercury `~/.claude/settings.json` line 58 SessionEnd matcher 注册 cost_tracker / mem0 flush; line 120 Stop matcher 是 `[]`。bg 走 Stop 不走 SessionEnd → cost-tracker write_session_summary 永不 invoke for bg |

**3 处 ADR mitigation** (本 PR 同时 patch S98 ADR §Path B):
1. file-editing bg workload **会** 进入 auto-worktree (HYBRID: platform 拒 + agent 补 EnterWorktree) — 操作员需理解 edits 落在 `worktree-<name>` branch 隔离, NOT main develop
2. bg session 终止 fire **Stop** 不 fire SessionEnd → Mercury 任何 SessionEnd-only hook (含 #361 cost-tracker `write_session_summary`) 对 bg 失效
3. cost-tracker integration 失效已独立 file [#392](https://github.com/392fyc/Mercury/issues/392) follow-on (S99 同 PR 内 filed before merge)

**1 处 NEW sub-Issue filed** (S99 内):
- [**#392 cost-tracker bg session integration gap**](https://github.com/392fyc/Mercury/issues/392) (P2, lane:main): bg session API costs 不计入 daily ceiling — `MERCURY_SESSION_COST_CEILING_USD` 防护对 bg 失效。3 修复 option (A settings.json Stop matcher / B periodic sweep / C accept gap + statusline marker) 待 user 仲裁

整体 verdict: Path B 仍为 PRIMARY for read-only + file-editing bg workload (auto-iso 是 documented behavior, agent 自管, edits 隔离 — 不破 Mercury main lane state)。NO Path A reopen 触发 (worktree pinning 字段仍 docs-blocked)。**S98 ADR §Path B 不需 BLOCK-level 修订, 仅需 inline 4 处 fact 矫正**(本 PR 顺手 patch — 见 §"S98 ADR patches" 段)。

---

## Phase 6 scope vs S98 ADR Phase 2

S98 Phase 2 验证 bare `claude --bg "echo from-bg-probe-test"` (read-only, 无 file write, 无 model call): cwd preserved, isolation:none, SessionStart fires, no routing-bleed。Phase 2 conclusion 留 5 项 unknown 至 Phase 6 sub-Issue #386-D。

S99 Phase 6 跑 2 个 bg probe 直接覆盖那 5 项 unknown + 1 项新 unknown (mechanism of auto-iso):

| Probe | Dispatch | Workload | 目的 |
|-------|----------|----------|------|
| 1 (`a8c58664`) | `claude --bg "Use the Edit tool to modify ... probe1-target.txt: ... Then exit."` | Edit + Write tool 实际 invoke + intermediate Bash + ToolSearch | 触发 docs §"Before editing files" auto-isolation behavior + observe complete hook lifecycle in file-editing path |
| 2 (`40726463`) | `claude --bg --agent research "What is 2+2? Answer in one sentence then stop."` | LLM single-turn response, no tool use | verify SubagentStop fire-or-not + minimal hook lifecycle baseline |

Total elapsed: probe 1 ~106s (5:10:24Z spawn → 5:12:10Z firstTerminalAt); probe 2 ~7s (5:10:46 → 5:10:53)。

---

## Findings (per #391 6-point checklist)

### #1 — file-editing bg auto-isolation: **HYBRID enforce-then-comply (platform refuses + agent补救)**

**S98 ADR phrasing** (lines 58, 70, 99-101): docs §"How file edits are isolated" describes "Before editing files, Claude moves the session into an isolated git worktree under `.claude/worktrees/`". Phase 2 read-only echo 未触发, 待 Phase 6 verify.

**Phase 6 mechanism** (probe 1 transcript timeline `~/.claude/projects/D--Mercury-Mercury/a8c58664-1a9d-4781-9a92-172cfae30ed0.jsonl`):

```
05:10:36 PreToolUse:Read   → Read main lane file (success)
05:10:42 (Edit attempted on main lane path)
05:10:46 PreToolUse:ToolSearch → bg agent loads `EnterWorktree` deferred tool
05:10:53 PreToolUse:EnterWorktree → tool call EnterWorktree(name="phase6-probe-1")
   ↓ state.json begins to expose:
     "worktreePath":"D:\\Mercury\\Mercury\\.claude\\worktrees\\phase6-probe-1",
     "worktreeBranch":"worktree-phase6-probe-1"
05:10:58 PreToolUse:Read on auto-worktree path
05:11:51 PreToolUse:Write to D:/Mercury/Mercury/.claude/worktrees/phase6-probe-1/.tmp/phase6-probe/probe1-target.txt
05:12:03 PreToolUse:ExitWorktree → tool call ExitWorktree(action:"keep")
   ↓ state.json fields nullified, but dir + branch 留盘
05:12:10 Stop × 4 hooks
```

Mechanism refinement vs S98 ADR assumption (HYBRID, NOT pure agent autonomy nor pure supervisor magic):
- **Platform side**: PreToolUse Edit on shared-checkout path returns literal `<tool_use_error>This background session hasn't isolated its changes yet. Call EnterWorktree first so edits land in a worktree instead of the shared checkout, then retry this edit using the worktree path.</tool_use_error>` (probe 1 jsonl 05:10:42.072Z, verbatim) — 这是 Anthropic platform-injected enforcement gate, NOT Mercury hook + NOT pure system prompt
- **Agent side**: 读 error → ToolSearch `select:EnterWorktree` (05:10:46) → 调用 `EnterWorktree` (05:10:53) → 重试 Edit in worktree path (05:11:51 Write success) → 任务完成后 `ExitWorktree(action:"keep")` (05:12:03)
- top-level `cwd` field in state.json 始终保持 `D:\Mercury\Mercury` — only `worktreePath` 字段表征 in-worktree state (process cwd 未实际 OS-level 移动, 是 logical cwd shift)

**Implication for Mercury**: 非 invasive — Mercury PreToolUse / PostToolUse hooks 完整 fire 到 EnterWorktree / ExitWorktree event, 操作员可在 hook 内 detect + intervene if needed (e.g. add policy: "deny EnterWorktree from bg session in side-bug lane")。但当前 Mercury 没有此类 policy hook, 默认行为 acceptable。

**Sub-finding — auto-worktree branch HEAD origin UNVERIFIED Anthropic-internal**: probe 1 `worktree-state` event 记录 `originalHeadCommit:"e89292037c5db673ff2390e80e756a92442a9b22"` (即 `e892920` `Merge pull request #17 from 392fyc/task/HOOK-001`)。但 work branch `lane/main/391-phase6-empirical` 当前 (post-probe + commit-time) HEAD 是 `8ffb17a` — `git log --oneline lane/main/391-phase6-empirical -3` 验证。两者矛盾 → Anthropic supervisor 选取 originalHeadCommit 的 mechanism **未 empirical 解释** (work branch 创建自 develop@8ffb17a, 无法解释为何 worktree-state 记录 e892920)。e892920 在 develop ancestry 内 (`git branch -a --contains e892920` 多 branch contains 包括 develop), 即不是 random commit。**对 Mercury 影响**: file-editing bg 在 auto-worktree 可能看到 outdated repo state — 若 task 依赖最新 develop 文件可能 missing。**判定**: 不 file Issue against Anthropic — 是 schema-undocumented behavior, mechanism UNVERIFIED。Mitigation: bg session 操作员 prompt 内 explicit cite 文件 path + Mercury commit SHA, 让 agent 自检; future probe 内 explicit `git rev-parse HEAD` in auto-worktree 可进一步明确选取规则。

### #2 — mem0_hooks cwd-derived state divergence: **NO RISK**

S98 ADR §Sub-Issue #386-D #2 假设: 若 #1 auto-iso 触发, mem0_hooks 用 `os.getcwd()` 推导 project memory 路径会偏离 main lane state。

Empirical:
- `grep -nE "os\.getcwd|cwd|project.*dir|encode" ~/.claude/scripts/mem0_hooks.py` → **zero matches** — 该 hook **不**用 cwd 推导任何路径 (推测用 `~/.claude/scripts/mem0-state/` Qdrant 固定路径 + sessionId 寻址)
- `cost_tracker.py` line 267 `session_log_path(session_id) -> Path` 接受 sessionId 参数, write to `$HOME/.claude/scripts/cost-tracker/<session_id>.jsonl` — 也 cwd-independent

**Verdict**: even if bg session via EnterWorktree shifts effective cwd, Mercury 用户级 hooks 不 derive state from cwd, 故 NO routing-bleed via this vector。sessionId-keyed storage 是 architecturally robust。

### #3 — git commits in auto-worktree fate: **isolated to branch, NO automatic reconciliation**

Probe 1 没 trigger `git commit` (intent only Edit/Write file, no commit verb)。但可推断:

- auto-worktree 在 `worktree-phase6-probe-1` branch (独立 ref)
- 任何 `git commit` 在 auto-worktree 内会落在该 branch, NOT develop
- ExitWorktree action:"keep" 保留 dir + branch
- Mercury operator 须显式 `cd .claude/worktrees/<name> && git log` audit + 决定 cherry-pick / merge / discard

**实测 file fate (with explicit timeline T0 = observation, T1 = post-probe operator cleanup)**:

T0 = 05:12:10Z (probe 1 firstTerminalAt, post `ExitWorktree(action:"keep")`):
- Main lane `.tmp/phase6-probe/probe1-target.txt`: still "baseline content line 1" (untouched ✅)
- Auto-worktree `.claude/worktrees/phase6-probe-1/.tmp/phase6-probe/probe1-target.txt`: "baseline content line 1\nphase6-probe-1 file-editing bg workload Edit tool fire at probe-1" (Edit applied ✅ via Write call, untracked status; verified by S99 operator at T0+30s via `cat`)
- `git worktree list` 显示 `D:/Mercury/Mercury/.claude/worktrees/phase6-probe-1 e892920 [worktree-phase6-probe-1]` (registered ✅)

T1 ≈ 05:14:XX (S99 operator cleanup pre-commit):
- S99 operator ran `git worktree remove --force .claude/worktrees/phase6-probe-1 && git branch -D worktree-phase6-probe-1 && rm -rf .tmp/phase6-probe` to restore clean repo state for commit (**audit-trail narrative — DO NOT copy-paste this command in unrelated context: paths are S99-probe-specific; `--force` + `rm -rf` are destructive and operator pre-confirmed only these specific S99 probe artifacts existed at the targets per `git worktree list` snapshot at T0**)
- Post-T1: dir + branch absent (audit-time inspection by reviewer of this PR will confirm absent — this is operator choice, NOT automatic GC)

**Implication**: Path B 操作员 mental model 调整 — bg file-editing 不污染 main lane 任何 file, 所有 edits 在隔离 branch + 隔离 dir。**这是 feature, 不是 bug** — 完全 align Mercury 多 lane 隔离精神。**Operator responsibility**: ExitWorktree(keep) preserves dir + branch on disk; cleanup (worktree remove + branch -D + tmp prune) 是 operator 主动 step, not automatic — 长期使用需 periodic sweep。

### #4 — 完整 hook lifecycle: **PARTIAL — Stop NOT SessionEnd**

Empirical observed events in probe 1 transcript (61 hook attachment lines, summarized):

| Event | Fire? | Count | Mercury hook activated? |
|-------|-------|-------|------------------------|
| SessionStart | ✅ | 5 entries (Mercury python + Mercury mjs + OMC plugin × 3) | mem0_hooks SessionStart, agentkb session-start, OMC SessionStart |
| UserPromptSubmit | ✅ | 1 | Mercury `.claude/hooks/user-prompt-submit.sh` |
| PreToolUse | ✅ | 多次 (Read, ToolSearch, EnterWorktree, ExitWorktree, Bash, Write, Edit) | loop-detector + push-guard fire per tool |
| PostToolUse | ✅ | 同集合 | mem0_hooks PostToolUse, OMC post-tool-use |
| PostToolUseFailure | ✅ | observed once on Read | OMC post-tool-use-failure |
| **SessionEnd** | ❌ | **0** | Mercury cost_tracker.write_session_summary registered HERE — **不 fire for bg** |
| Stop | ✅ | 5 (per probe 2 stop_hook_summary hookCount:5, all OMC plugins — code-simplifier / context / persist / stop-review-gate / 1 more) | settings.json line 120 `Stop: []` empty → Mercury Stop matcher 注册 0 hook; only OMC plugins fire (correctly noted Mercury 不依赖 Stop event) |
| PreCompact | unverified | 0 | short session no compaction needed |
| SubagentStop | ❌ | 0 | bg session 不是 sub-agent dispatch caller, even with `--agent X` template selector |

**KEY**: bg session 终止 fire `Stop` (per Claude Code lifecycle for bg / dispatched session) 不 fire `SessionEnd` (后者是 user-driven interactive session 退出 event)。

Mercury `~/.claude/settings.json`:
- Line 58: `"SessionEnd": [...]` — registers cost_tracker, mem0 flush, etc.
- Line 120: `"Stop": []` — empty array, no Mercury hooks registered

→ bg session 终止时 Mercury 任何 SessionEnd-registered hook 都 SKIP。这是 #6 cost-tracker 0-jsonl 的 root cause (见 #6)。

### #5 — SubagentStop in `--bg --agent X`: **NO**

Probe 2 (`claude --bg --agent research "What is 2+2?"`) transcript 仅含: SessionStart × 5 + UserPromptSubmit + Stop × 4 — **no SubagentStop event**。

理解: Anthropic SubagentStop 设计是 nested subagent 完成 event (parent session 内 dispatch 的 sub-agent 退出时 fire)。`--agent X` 是 bg session 启动时的 template selection (类似 interactive `--agent` 标志), 整个 bg session 即是该 agent 的 instance — 它的退出对应整体 Stop event, 不存在 "parent session 收到 sub 完成通知" 的语义。

**Verdict**: 设计一致, 无 bug — Mercury 不依赖 SubagentStop 对 `--bg --agent X` workload 做任何特殊处理。

### #6 — bg session API cost tracking integration: **NOT INTEGRATED**

Empirical (post probe 1+2 完成):
- `ls ~/.claude/scripts/cost-tracker/` 13 个 jsonl 全部是 May 9-12 历史 interactive session
- `ls ~/.claude/scripts/cost-tracker/40726463*` → No such file (probe 2 sessionId)
- `ls ~/.claude/scripts/cost-tracker/a8c58664*` → No such file (probe 1 sessionId)

Root cause (per #4): Mercury `cost_tracker.write_session_summary` 在 `~/.claude/hooks/session-end.py` cost_tracker import block 内 invoke; session-end.py 注册在 settings.json line 58 `SessionEnd` matcher; bg session 不 fire SessionEnd → write_session_summary 永不 invoke for bg。

**Implication**:
- bg session 触发 real model invocation (e.g. probe 2 调研 + answer "2+2=4" 的 LLM cost) 完全 invisible to Mercury #361 framework
- `MERCURY_SESSION_COST_CEILING_USD` daily ceiling 不 protect bg session API spend
- Statusline 颜色阶梯 (绿 <70% / 黄 70-89% / 红 ≥90%) 仅反映 interactive session 累积, bg session 暗 spend
- 用户 long-term 大量 bg dispatch → 真实 daily cost 远超 ceiling perception

**Sub-Issue follow-on FILED** ([#392](https://github.com/392fyc/Mercury/issues/392) **P2 lane:main enhancement**, S99 内 created): "#361 cost-tracker bg session integration gap" — 修复路径选项 in Issue body:
- (A) 在 settings.json `Stop` matcher 加 `cost_tracker.write_session_summary` registration (parallel to SessionEnd) — 简单, 但需 verify Stop hook contract 兼容 (event payload 结构 SessionEnd vs Stop 是否字段一致)
- (B) `cost_tracker.py` 内增 monkey-patch detect mechanism (e.g. periodic background sweep of `~/.claude/jobs/*/state.json` extracting `linkScanPath` jsonl + parse usage events) — 更 robust 但 invasive
- (C) 接受 bg session 不计入 ceiling, 单独 statusline marker 报 "bg sessions outside ceiling tracking" (least intervention)

User 仲裁后 implement on #392。

---

## S98 ADR patches (本 PR 同 commit 内 inline)

S98 ADR `agent-view-multi-lane-adaptation-2026-05.md` 4 处需 fact 矫正 (本 PR 同 commit Edit):

1. **L70 表格 "Worktree auto-isolation" 行** — describes HYBRID enforce-then-comply mechanism (platform refuses + agent EnterWorktree) replacing "fires under 3 conditions"
2. **L74 表格 "Hook" 行** — Phase 6 confirmed: PreToolUse/PostToolUse/Stop fire ✅; SessionEnd does NOT fire for bg → Mercury SessionEnd-registered hooks SKIP
3. **L304 §Path B Pro line** — extend "for read-only AND file-editing bg workload" + cwd-independent hook reasoning
4. **L310-313 §Path B Con** — replace "未 empirical verify 的关键 scenario" with Phase 6 verified bullets + cite [#392](https://github.com/392fyc/Mercury/issues/392) follow-on

---

## Production-readiness gate impact

S98 ADR Phase 2 confirmed gate 4/4 ✅ FULL PASS post-S96。Phase 6 不动 production-readiness gate scope (gate 衡量 production code health, 本 ADR 是 research / docs)。新发现 cost-tracker gap 是 follow-on enhancement (P2), 不 regress gate。

---

## Out of scope (本 ADR 不处理)

- bg session 内 fork subagent (即 dispatch nested) 触发 SubagentStop 的 case — 需独立 probe (P3, low priority unless Mercury subagent dispatch 模式扩展到 bg)
- PreCompact hook 在长 bg session triggered compaction 场景的 fire 行为 — 需 long-running probe (P3, defer until 真实 multi-hour bg workload)
- agent-teams primitive (S98 ADR § already deferred)
- agent view UI experience 评估 (操作员主观, 不在本 ADR scope)
- `claude agents` CLI subcommand 在 non-interactive shell 不可用 (本 session 实测 `'claude agents' is not available in this environment`) — 已知 TUI-only, 文档化 in #386-A docs 时附录提及

---

## Action Items (S99 末 execute)

1. **Patch S98 ADR** (本 PR commit 内 4 处 inline fix per §"S98 ADR patches")
2. **File new sub-Issue [#392](https://github.com/392fyc/Mercury/issues/392) cost-tracker bg session gap** (P2, lane:main) — body 含 3 修复 option (A/B/C) for user 仲裁— **DONE pre-merge** (filed 2026-05-15)
3. **Update sub-Issue mapping comment on #386** — add #386-D progress: "Phase 6 ADR (this PR) MERGED → 6/6 verified (with mitigations); follow-on sub-Issue #392 for cost-tracker integration filed pre-merge"
4. **CLOSE #391** via PR `Closes #391` keyword

---

## References

### S98 ADR (parent)
- [.mercury/docs/research/agent-view-multi-lane-adaptation-2026-05.md](agent-view-multi-lane-adaptation-2026-05.md) @ develop `8ffb17a`

### Issue chain
- [#386](https://github.com/392fyc/Mercury/issues/386) — agent view 适配评估 (CLOSED via S98 PR #387)
- [#391](https://github.com/392fyc/Mercury/issues/391) — Phase 6 empirical verify (THIS PR Closes)
- [#388](https://github.com/392fyc/Mercury/issues/388) — #386-A docs (sister, parallel)
- [#389](https://github.com/392fyc/Mercury/issues/389) — #386-B optional POC (sister)
- [#390](https://github.com/392fyc/Mercury/issues/390) — #386-C defer-record (sister)
- (TBD #392 follow-on): #361 cost-tracker bg session integration gap

### Mercury authority
- `feedback_lane_protocol.md` v1 @ user-memory canonical
- `~/.claude/scripts/mem0_hooks.py` (cwd-independent — verified zero cwd refs)
- `~/.claude/scripts/cost_tracker.py` line 267 `session_log_path` (sessionId-keyed)
- `~/.claude/hooks/session-end.py` cost_tracker import block (`from cost_tracker import write_session_summary` 调用)
- `~/.claude/settings.json` line 58 `SessionEnd:[...]` + line 120 `Stop:[]`

### Empirical evidence files (Phase 6)

**Note on reproducibility**: Empirical evidence below lives on the S99 author's local machine (`~/.claude/projects/...` per-cwd transcript dirs + `~/.claude/jobs/...` supervisor state). These are operational artifacts, not Mercury repo files — they cannot be checked in (size + sensitive session content + cross-machine path encoding). For independent audit / replication, run a fresh Phase 6 probe per the §"Phase 6 scope" section (\\`claude --bg "Edit ..."\\` + \\`claude --bg --agent research "..."\\`) and inspect the equivalent paths under your own \\`~/.claude/\\` tree. Mercury research convention is empirical-replicable via probe steps, not artifact-checked-in. (See `.mercury/docs/research/agent-view-multi-lane-adaptation-2026-05.md` S98 ADR for prior precedent.)

- `~/.claude/jobs/a8c58664/state.json` — probe 1 final state (`worktreePath:null` post-ExitWorktree, output result 显示 Edit applied + worktree preserved)
- `~/.claude/jobs/40726463/state.json` — probe 2 final state ("answered 2+2=4", 7s elapsed)
- `~/.claude/projects/D--Mercury-Mercury/a8c58664-1a9d-4781-9a92-172cfae30ed0.jsonl` — probe 1 full transcript (61 hook attachment lines)
- `~/.claude/projects/D--Mercury-Mercury/40726463-c331-4b04-b2b9-d4e31e38a800.jsonl` — probe 2 transcript (minimal SessionStart + UserPromptSubmit + Stop only)
- `~/.claude/daemon/roster.json` (mid-flight snapshot during dual-probe) — both workers visible with `dispatch.isolation:"none"` even for probe 1 file-editing
- `D:/Mercury/Mercury/.claude/worktrees/phase6-probe-1/` — auto-worktree existed at observation T0=05:12:10Z (post `ExitWorktree(action:"keep")`) with branch `worktree-phase6-probe-1` HEAD `e892920` per `worktree-state` event; **subsequently removed by S99 operator at T1≈05:14:XX** via `git worktree remove --force` + `git branch -D` for clean repo state pre-commit (operator choice, NOT automatic GC). Audit-time inspection of this PR will find dir absent — that's expected per timeline. Evidence preserved in transcript jsonl + state.json.

### Anthropic docs (cross-ref from S98 Phase 1)
- [Manage multiple agents with agent view](https://code.claude.com/docs/en/agent-view) §How file edits are isolated
- [Subagents](https://code.claude.com/docs/en/sub-agents) §Supported frontmatter fields
- [Hooks](https://code.claude.com/docs/en/hooks) §Lifecycle events

### Related Mercury Issues (sister context, post-Phase 6 follow-on candidates)
- [#382](https://github.com/392fyc/Mercury/issues/382) — hook layer modernization (#386-D #4 partial input — bg `Stop` vs `SessionEnd` gap could feed this)
- [#361](https://github.com/392fyc/Mercury/issues/361) — original cost-tracker landing (bg gap is direct follow-on candidate)
