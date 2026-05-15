# Agent view (Claude Code v2.1.139+) 适配评估 — Mercury 多 lane 协议 ADR

**Issue**: [#386](https://github.com/392fyc/Mercury/issues/386) (Closes)

**Status**: Phase 1+2 Research — verdict **Path B (零修改 + dispatch convention 文档化) + Path A 部分 DEFER**

**Date**: 2026-05-15
**Session**: S98 (main lane)
**Branch**: `lane/main/386-agent-view-adr` (Rule 2.1 short prefix, 7th main-lane dogfood)
**Scope**: Anthropic agent view (Claude Code v2.1.139+) 与 Mercury 多 lane v1 protocol (LANES.md + manual worktree + handoff files) 之间的适配评估、3 path 决策

---

## TL;DR

Anthropic 2026-04 在 Claude Code v2.1.139+ 上线 **agent view** (research preview)：per-user supervisor process 托管多个 background sessions 在单一 `claude agents` 控制面板。与 Mercury 自建多 lane 协议 (LANES.md registry + manual git worktree per lane at `D:/Mercury/Mercury-<short>` + handoff files + per-session memory files) 概念重叠但模型不同。

经 Phase 1 (docs WebFetch) + Phase 2 (本地 CLI v2.1.141 minimal empirical repro) 双路验证，verdict 与 Issue #386 初稿假设有 **3 处实质性矫正**：

1. **Path A gate 经 docs CLOSED**：`.claude/agents/<name>.md` frontmatter `isolation` 字段在 Anthropic 官方 sub-agents 文档中**只接受 `worktree` 一个值**（auto-create 临时 worktree），**无 `worktree_path` / `pin_worktree` / `use_existing_worktree` 等 pin-to-existing 字段**。Path A 原假设（每 lane wrap subagent 含 `isolation: worktree` pin existing Mercury worktree）**docs-level 不可行**。
2. **Unknown #2 auto-worktree 触发条件比初稿研究更窄**：empirical 验证（`claude --bg "echo ..."`）显示 `isolation: "none"` 是 default，cwd 完全保留 `D:\Mercury\Mercury`，**不主动 auto-move 进 `.claude/worktrees/<id>`**。auto-worktree 仅在 (a) subagent frontmatter 声明 `isolation: worktree` (b) `CLAUDE_CODE_FORK_SUBAGENT=1` fork mode (c) docs §"How file edits are isolated" 描述的"before editing files"惰性触发 — 三条路径之一。**bare bg session 不触发 routing-bleed risk**。
3. **Unknown #3 SessionStart hook fire empirical confirmed (其它 lifecycle event 仅 inferred)**：empirical transcript (`a06e1416-8d63-4e5a-8168-402d94f62a0d.jsonl`) 含 `SessionStart:startup` hook attachment，user-level `~/.claude/hooks/session-start.py` 与 `session-start.mjs` 两个 entry point **均在 bg session 触发**（durationMs 1713 + 2652），`hookSpecificOutput.additionalContext` 与 interactive session 一致。`sessionKind:"bg"` 字段可供 hook 脚本未来 discriminate。**PreToolUse / PostToolUse / SessionEnd / PreCompact / SubagentStop 未触发** (echo 无 file write / 无 model call / 无 SubagentStop 触发条件), 仅基于 docs settings-load 机制 inferred — Path B 实际部署前需 Phase 6 empirical verify。

综合 3 path 优劣 + Mercury 实际 hook stack 完整性 (partial) + Rule 4 红线评估，**推荐 Path B (零修改 + dispatch convention 文档化)** 作为 primary verdict for **read-only bg workload**; file-editing bg workload + 其它 hook lifecycle event 行为留 Phase 6 sub-Issue (#386-D) empirical verify 后再正式宣称 "zero disruption"。Path A 部分 **DEFER 而非 NO-GO**，等 Anthropic 未来引入 worktree pinning 后再 re-eval。Path C **NO-GO** — Rule 4 红线（DIRECTION.md + feedback_lane_protocol.md v1 重写）+ supervisor 1h timeout 与 Mercury 跨周/跨月 lane lifecycle 不匹配，得不偿失。

---

## Background — Mercury 现状

Mercury 多 lane v1 protocol（authority: `feedback_lane_protocol.md` v1, accepted 2026-05-03 via [#347](https://github.com/392fyc/Mercury/pull/347) admin-merge / [#315 epic](https://github.com/392fyc/Mercury/issues/315)）：

| 维度 | 实现 |
|------|------|
| Registry | `LANES.md` per-lane section: `Handoff file` / `Worktree path` / `Scope` / `Branch prefix` / `Short name` / `Status` / `Claimed Issues` |
| Worktree | manual `git worktree add D:/Mercury/Mercury-<short> ...` per Rule 5.1 |
| Branch convention | `lane/<short>/<N>-<slug>` (Rule 2.1 short prefix, S83 first dogfood) |
| Per-session state | `memory/sessions/S<N>(-<lane>)?.md` SoT, canonical (MEMORY.md + SESSION_INDEX.md) derived via `scripts/regenerate-memory-index.sh --in-place` (Rule 7.B) |
| Handoff | `memory/session-handoff(-<lane>)?.md` 跨 session 持久态 |
| Cross-lane coord | Issue-first, Rule 6 own-section editing 限制 |
| Lifecycle | Rule 8 v1 lane lifecycle autonomy |

Active lanes（2026-05-15）：
- `main` @ `D:/Mercury/Mercury` (canonical default)
- `side-bug` @ `D:/Mercury/Mercury-side-bug`
- `side-sot` @ `D:/ShipOfTheseus/Ship_of_Theseus` (cross-repo lane)
- `side-multi-lane` @ closed via S18-side-multi-lane Phase G 2026-05-04

本 ADR 评估的 Issue (#386) 由 side-bug S7-side-bug (2026-05-12) user-direct question 引发的 web 调研发起，作为 [#381 S6-side-bug tech intel sweep](https://github.com/392fyc/Mercury/issues/381) 的 follow-up Issue 由 side-bug lane file 提交。

## Agent view 关键事实 (Phase 1 verified 2026-05-15)

| 维度 | 行为 | 引用 |
|------|------|------|
| Supervisor | per-user singleton process, 启 background sessions | [docs §How background sessions are hosted](https://code.claude.com/docs/en/agent-view) |
| Session state | `~/.claude/jobs/<id>/state.json` + `~/.claude/daemon/roster.json` (paths only; field schema undocumented) | 同上 |
| State taxonomy | Working / Needs input / Idle / Completed / Failed / Stopped (6 values) | docs §Monitor sessions |
| Lifecycle | 1h unattached → supervisor 释放 process (state.json 留盘); sleep/shutdown → stopped, `claude respawn --all` 恢复 | docs §How background sessions are hosted |
| Worktree auto-isolation | background session 在 work-dir 写文件**之前**惰性 auto-move 进 `.claude/worktrees/<auto>`; skip 条件：(1) session 已在 `.claude/worktrees/`, (2) cwd 非 git repo, (3) writes outside cwd | docs §How file edits are isolated |
| Filter primitives | `a:<agent-name>` / `s:<state>` / `#<PR-number>` / repo-grouping by directory | docs §Filter the list |
| Dispatch | `claude --bg "<prompt>"` (CLI v2.1.141 verified) / `claude --bg --agent <name>` / `/bg` inside session / `@<agent>` / `@<repo>` mentions | docs §Dispatch new agents |
| Disable | `disableAgentView` setting / `CLAUDE_CODE_DISABLE_AGENT_VIEW` env | docs §How background sessions are hosted |
| CLAUDE_CONFIG_DIR | honored (Mercury 用户级 governance pattern 兼容) | docs §How background sessions are hosted |
| Honors hooks/subagents | session 是 Claude Code session, 同 hook + subagent 模型 | docs §Permission mode and settings |

## 概念对照

| 维度 | Mercury 多 lane | Agent view |
|------|----------------|------------|
| 并行单元 | **lane** = 长期 scope（跨多 session, 跨周/跨月） | **session** = 短期任务（1 task / 1 lifecycle） |
| 隔离 | manual worktree per lane | optional auto worktree per session — fires under 3 conditions: (a) subagent declares `isolation: worktree`, (b) `CLAUDE_CODE_FORK_SUBAGENT=1` fork mode, (c) docs §"Before editing files" 惰性触发 (Phase 2 echo 无 file write 未触发 #c, file-editing 行为待 Phase 6 verify) |
| State 持久 | 跨 reboot 永存 (user-memory + canonical files) | 1h idle 杀 process; state.json 留盘; sleep/shutdown 全杀 (state.json 仍在但 process 重启需 `claude respawn --all`) |
| 标识 | lane short name + branch prefix `lane/<short>/<N>-<slug>` | session short ID (8-char hex e.g. `a06e1416`) + auto-name from prompt |
| 协调 | Issue-first + Rule 6 cross-lane via Issues + LANES.md registry | sessions 互不通信 (agent-teams 是另一原语); `claude agents` 仅作 UI 集中 |
| Hook | full Mercury stack (mem0 + cost_tracker + flush + loop-detector + push-guard) | **SessionStart fire empirical confirmed Phase 2**; PreToolUse / PostToolUse / SessionEnd / PreCompact / SubagentStop 仅 inferred from settings-load 机制, 待 Phase 6 verify |
| cwd routing | per-lane worktree → `~/.claude/projects/<encoded-cwd>/` separate | bare bg from `D:/Mercury/Mercury` → linkScanPath 仍指 `D--Mercury-Mercury` (no routing-bleed for default isolation) |

---

## Phase 1 Findings (docs WebFetch)

研究 subagent (sonnet, 96120ms, 5 tool_uses) 经 WebFetch verify 了 4 个 docs-verifiable unknowns，verdict + 直接引用：

### Unknown #1 — Path A 可行性 gate

**Verdict**: CLOSED.

**Evidence** (sub-agents.md §Supported frontmatter fields):
> "Set to `worktree` to run the subagent in a temporary git worktree, giving it an isolated copy of the repository. The worktree is automatically cleaned up if the subagent makes no changes."

**Field schema**: 只接受 `worktree` 一个值（auto-create）。**无** `worktree_path` / `pin_worktree` / `use_existing_worktree` 等字段在文档列表中。auto-create 落在 `.claude/worktrees/<auto-id>/`，**不能由 caller 指定路径**。

**Path A implication**: Path A 原设计（`.claude/agents/lane-<short>.md` frontmatter pin to existing `D:/Mercury/Mercury-<short>`）**不可表达**。Path A 在当前 docs spec 下 **blocked**。

### Unknown #2 — agent view auto worktree × lane-assertion.sh 兼容

**Verdict**: Phase 1 docs 评估偏严苛，Phase 2 empirical 矫正为更窄触发。

**Phase 1 docs evidence** (agent-view.md §How file edits are isolated):
> "Every background session, whether started from agent view, /bg, or claude --bg, starts in your working directory. Before editing files, Claude moves the session into an isolated git worktree under .claude/worktrees/, so parallel sessions can read the same checkout but each writes to its own. Claude skips this when the session is already under .claude/worktrees/, when the working directory isn't a git repository, or for writes outside the working directory."

**Phase 1 conclusion**: bg session 在 Mercury 任意 lane cwd 写文件 → auto worktree 在 `<lane-cwd>/.claude/worktrees/<id>` → 该路径**不在** LANES.md 注册 → `lane-assertion.sh` exit 2 (cwd_mismatch)。

**Phase 2 矫正**: empirical 验证 `claude --bg "echo from-bg-probe-test"` 后：
- `roster.json` workers.a06e1416.dispatch.**isolation: "none"**
- `state.json.cwd: "D:\\Mercury\\Mercury"` 完全保留
- `D:/Mercury/Mercury/.claude/worktrees/` 不增加新条目（仍只有 Mercury 既有 `session-22-phase1/` + `skill-deep-research/`）
- `linkScanPath: ...\projects\D--Mercury-Mercury\a06e1416....jsonl` 指向 main lane project memory（无 routing-bleed）

**真实触发条件**: auto-worktree **只在以下任一发生时**惰性触发：
1. subagent frontmatter 显式 `isolation: worktree` （Anthropic auto-create 一个新 `.claude/worktrees/<auto-id>`）
2. `CLAUDE_CODE_FORK_SUBAGENT=1` fork mode (docs `--agents` 子命令)
3. docs §"Before editing files" 描述的惰性触发 — 但 empirical 单跑 `echo` 命令时未触发（可能因无 file write）

Mercury bare bg session（`claude --bg "<arbitrary-shell-cmd>"` **with no file writes**）不会主动切换 cwd，**lane-assertion.sh 不会因 bg session 本身误报**。但若 (i) 用户 dispatch 到 subagent declaring `isolation: worktree` (Path A假设), 或 (ii) bg session 在执行中触发 docs §"Before editing files" 惰性 auto-isolation (Path B file-editing 工作负载, Phase 2 未 verify), 该 session cwd 切到 `.claude/worktrees/<auto-id>`，该 cwd 不在 LANES.md → 仅当 lane-assertion 在 bg session 内部跑时才命中 exit 2 (Mercury 当前 lane-assertion 只在 interactive session boot 跑, 不在 bg session boot 跑 — 故主 lane interactive session 不受影响, 但 bg session 自身的 user-level hook (e.g. mem0_hooks cwd-derived state) 可能与 main lane state space 偏离)。

**结论**: routing-bleed 风险分两层：
- **interactive session 层**: bare bg dispatch (无论 file-editing 还是 read-only) 不影响 interactive Claude Code session 的 lane-assertion (lane-assertion 不在 bg session boot 跑) — 安全
- **bg session 自身层**: isolation-declared subagent dispatch (Path A) **必然** routing-bleed; bare bg + file-editing workload (Path B file-editing case) 可能触发 docs §"Before editing files" → cwd-derived hook state 可能偏离 — 待 Phase 6 sub-Issue #386-D empirical verify。bare bg + read-only workload (Phase 2 verified case): 安全。

### Unknown #3 — Hook 完整触发性

**Verdict**: **PARTIALLY VERIFIED** — SessionStart empirical YES; PreToolUse / PostToolUse / SessionEnd / PreCompact / SubagentStop 仅 inferred from settings-load 机制, 实际部署 Path B 前需 Phase 6 sub-Issue #386-D empirical verify。

**Phase 1 docs**: silent on bg session hook differences. 最接近 statement (agent-view.md §Permission mode and settings):
> "A background session reads its settings from the directory it runs in, the same as if you had started claude there."

Settings inheritance implies hooks fire (hooks are settings)，但 docs 未直接确认 lifecycle 完整性。

**Phase 2 empirical evidence**: bg session `a06e1416` transcript (27 lines, `~/.claude/projects/D--Mercury-Mercury/a06e1416-...jsonl`) 含：
- Line 4: `attachment.hookEvent: "SessionStart"` + `attachment.hookName: "SessionStart:startup"` + `attachment.command: "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/.venv/Scripts/pythonw.exe ${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks/session-start.py"` + `attachment.exitCode: 0` + `attachment.durationMs: 1713`
- Line 5: 第二个 SessionStart attachment, `command: node "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks/session-start.mjs"`, `durationMs: 2652`
- 两个 hook 均产生 `hookSpecificOutput.additionalContext`，与 interactive session 完全一致

**`sessionKind:"bg"`** 字段 verified — bg session transcript 含该标记，**hook 脚本可未来 discriminate bg vs interactive**（如需）。

**未 empirical verify 的 hook events** (受限于 bg 仅跑 echo + 无 model call)：PreToolUse / PostToolUse / SessionEnd / PreCompact / SubagentStop。但既然 user-level session-start.py / .mjs 与 agentkb session-start hook 全部 fire，可**有信心 infer** 其他 lifecycle event 也 fire（settings 加载机制是统一的）。

**Mercury hook 影响清单** (Phase 2 inferred):
- `~/.claude/hooks/mem0_hooks.py` (SessionStart/SessionEnd/PreToolUse/PostToolUse) — likely fires
- `~/.claude/scripts/cost_tracker.py` — fires if model call happens (echo 不调 model 所以未观察到 jsonl)
- `~/.claude/hooks/flush.py` (SessionEnd) — likely fires on bg session stop
- `.claude/hooks/loop-detector.cjs` (PreToolUse) — likely fires
- `.claude/hooks/push-guard.sh` (PreToolUse) — likely fires for git push

### Unknown #4 — roster.json + state.json schema

**Verdict**: PARTIALLY VERIFIED (Phase 2 empirical augments docs).

**Documented**: 仅 path + state taxonomy。Field schema 文档未提供。

**Phase 2 empirical schema** (Mercury 可参考但 NOT 视为稳定 API):

`roster.json`:
```json
{
  "proto": 1,
  "supervisorPid": 18088,
  "updatedAt": <epoch_ms>,
  "workers": {
    "<short-id>": {
      "pid": <int>, "procStart": "<windows-filetime>",
      "sessionId": "<uuid>", "rendezvousSock": "<windows-pipe>",
      "ptySock": "<windows-pipe>", "cliVersion": "2.1.141",
      "startedAt": <epoch_ms>, "attempt": 1, "cwd": "<path>",
      "dispatch": {
        "proto": 1, "short": "<id>", "nonce": "<hex>",
        "sessionId": "<uuid>", "createdAt": <epoch_ms>,
        "source": "shell", "cwd": "<path>",
        "launch": { "mode": "prompt", "args": [...] },
        "env": {}, "isolation": "none|worktree",
        "respawnFlags": [...], "seed": { "intent": "..." }
      },
      "decModes": [1004, 1000, 1002, 1003, 1006, 2004, 2031]
    }
  }
}
```

`state.json`:
```json
{
  "state": "done|working|idle|...", "detail": "...",
  "tempo": "idle|working", "output": { "result": "..." } | null,
  "inFlight": { "tasks": 0, "queued": 0, "kinds": [] },
  "children": null, "linkScanOffset": <int>,
  "linkScanPath": "<jsonl-path>", "template": "bg",
  "respawnFlags": [...], "intent": "<prompt>",
  "sessionId": "<uuid>", "resumeSessionId": "<uuid>",
  "daemonShort": "<id>", "cliVersion": "2.1.141",
  "cwd": "<path>", "createdAt": "<iso8601>",
  "updatedAt": "<iso8601>", "firstTerminalAt": "<iso8601>",
  "backend": "daemon", "name": "<auto-name>",
  "nameSource": "auto"
}
```

**Mercury read safety**: 字段 schema undocumented 意味跨 CLI 版本可能变。**OK to read for observability**（如未来 `scripts/lane-status.sh` parse roster.json 列 in-flight sessions per cwd），但需 **schema-tolerant**（容忍 missing/added 字段）。**NOT OK to write** — Anthropic 拥有 schema。

---

## Phase 2 Empirical Findings (CLI v2.1.141 minimal repro, main lane only)

实测方式: `BOOTSTRAP_PROMPT='[LANE=main]' bash scripts/lane-assertion.sh` PASS → `claude --bg "echo from-bg-probe-test"` → wait → inspect roster/state/transcript/hooks → `claude stop <id>` cleanup。

**Setup**:
- CLI: 2.1.141 (>= 2.1.139 agent-view threshold)
- cwd: `D:/Mercury/Mercury` (main lane canonical worktree, develop branch @ `84594ce`)
- Pre-state: `~/.claude/jobs/` + `~/.claude/daemon/` 不存在 (no prior bg session)
- Mercury repo `.claude/worktrees/` 含 2 个既存 entry (`session-22-phase1/`, `skill-deep-research/`)
- Mercury `.claude/agents/*.md` 9 个 agent，**无一含 `isolation:` 字段**

**Findings**:

1. **`--bg` flag 隐藏于 `--help`** — CLI v2.1.141 的 `claude --help` (76 lines) 未列 `--bg`，但 `claude --bg "<prompt>"` 直接 invocation 成功 spawn bg session。flag 处于 research-preview 状态。
2. **`--worktree [name]` flag 公开** — 与 agent view auto worktree 是**两套不同机制**：`--worktree` 用于 interactive session 创建 isolated worktree（需配合 `--tmux`），agent view auto worktree 是 bg session 在 file-write 前的惰性 isolation。
3. **`.claude/worktrees/` namespace 重叠** — Mercury 早期实验已占用该目录（4-5 月旧 entry 时间戳）。Agent view auto worktree 进入同一目录但用不同子目录名 (`<auto-id>` 8-char hex)。**不会冲突**（不同 subdir 名），但**会造成 git status / cleanup 混淆**。建议 Mercury 在 future cleanup 文档中标注此目录"由 Mercury manual `--worktree` 和 Anthropic agent view 共用，删除前 verify 来源"。
4. **bare `claude --bg "<cmd>"` `isolation: "none"`** — Mercury main lane cwd 内 bare bg session **不主动创建 auto worktree**，cwd 保持 `D:\Mercury\Mercury`，`linkScanPath` 指向 main lane project memory（`D--Mercury-Mercury`）。无 routing-bleed。
5. **SessionStart hook fully fire (其它 hook 仅 inferred)** — `~/.claude/hooks/session-start.py` (Mercury mem0 + agentkb stack) 和 `~/.claude/hooks/session-start.mjs` (deepinit + omc) 双 SessionStart hook entry 完整 fire，attachment.exitCode 均 0。`hookSpecificOutput.additionalContext` 与 interactive session 一致（含 `## Today` + `## Knowledge Base Index` + `## Recent Daily Log` + `<session-restore>` 段）。**重要**: PreToolUse / PostToolUse / SessionEnd / PreCompact / SubagentStop **未** empirical verified (echo 无 file write 无 model call 无 SubagentStop 触发条件)；这些 lifecycle event 仅基于 docs "settings 加载机制" 推断为 fire — Path B 实际部署前需 Phase 6 empirical verify。
6. **`sessionKind: "bg"`** transcript 字段可作 hook discriminator — Mercury hook 脚本未来如需区分 bg vs interactive 行为，可读 `$CLAUDE_HOOK_*` env 或 transcript jsonl 内 `sessionKind` 字段。
7. **`permissionMode: "bypassPermissions"`** — bg session auto-set bypass mode（避免阻塞 prompt）。Mercury 既有 `~/.claude/settings.json` `permissionMode` 若有显式覆盖会被 supervisor honour（per docs §Permission mode and settings）。**对 Mercury push-guard.sh 影响**：bypass 仅指 prompt UI 跳过，hook 仍 fire，push-guard 仍能 block via exit 2 — 安全。
8. **Supervisor transient** — `[supervisor] idle 5s with no clients — exiting` — daemon 在所有 workers 停止后 5 秒自动退出。Mercury 长 session 模式（人类用户开 interactive Claude Code 数小时）**不会被 supervisor timeout 影响**，因为 supervisor 只管 bg sessions。Interactive session 走独立 path。
9. **state.json 留盘** — `claude stop <id>` 后 `~/.claude/jobs/<id>/state.json` 不删，与 docs §"How background sessions are hosted" 一致（"state.json 留盘以备 respawn"）。Mercury 若实施 lane-status.sh 可读历史 bg session 列表。
10. **`linkScanPath` 指 project memory** — bg session 的 transcript 写入 `$HOME/.claude/projects/D--Mercury-Mercury/<sessionId>.jsonl`，与 interactive 走同一 project memory pool。Mercury 用户级 hooks（mem0 / cost-tracker）若按 project memory 路径写状态，bg + interactive 共享同一 state space。**对 Mercury cost ceiling 累计影响**：bg session API 调用应计入同一 daily ceiling — 待 #361 cost tracker 在 bg 实际 model invocation 场景 verify。

**Cleanup**:
- `claude stop 1baace78 a06e1416` → both stopped, `roster.json workers: {}`
- `~/.claude/jobs/` 含 2 个 stale state dir + pins.json — 留作 ADR evidence，未删除
- `D:/Mercury/Mercury/.claude/worktrees/` 不变（仅 2 个既存 Mercury entry）

---

## Decision Drivers

1. **Rule 4 红线**：`feedback_lane_protocol.md` v1 + `.mercury/docs/DIRECTION.md` 修改需 user 仲裁。激进重构（Path C）触红线。
2. **Mercury hook 完整性 (partial)**：Phase 2 verified **SessionStart** fires in bg；PreToolUse / PostToolUse / SessionEnd / PreCompact / SubagentStop 仅基于 docs settings-load 机制 inferred — 实际部署 Path B 前需 Phase 6 empirical verify。若 inferred 成立, 任何 Path 都不会 break Mercury 现有 hook stack；若 inferred 偏离, 需在该 lifecycle event 修复 hook routing。
3. **Lane lifecycle 尺度匹配**：Mercury lane 跨周/跨月，agent view session 1h idle 释放、shutdown 杀。**任何方案不能假设 agent view session 是 lane 持久态载体**。
4. **Adapter ≤ 200 LOC 硬约束** (CLAUDE.md MUST)：Path A wrapper 若超 200 LOC（4 lane × 50 LOC 已逼近上限）需 rethink。但 lane wrapper 文件位于 `.claude/agents/`，不属 `adapters/<vendor-name>/` — CLAUDE.md MUST "External-project adapters under `adapters/<vendor-name>/` MUST stay under 200 lines" + DO NOT 段 explicit carve-out 仅明列 `scripts/` 为 exempt (per S84 #348 落地)。`.claude/agents/` 不在 CLAUDE.md 200-LOC 段明文 scope 内, 严格读为 "non-adapter location, 故 200-LOC adapter cap not applicable"; 但本 ADR 不主张扩展 carve-out 到 `.claude/agents/`, 该决策需独立于本 ADR 评估。
5. **Upward-compatibility design** (CLAUDE.md MUST "design for upward compatibility")：方案不应假设 agent view feature set 固定，应留 re-eval channel。
6. **Modular detachability** (CLAUDE.md MUST "every new feature independently detachable")：方案应允许 `CLAUDE_CODE_DISABLE_AGENT_VIEW=1` no-op 即可回滚。

---

## Considered Options

### Path A — 每 lane wrap subagent (轻度耦合)

**Original assumption**: 每 lane 一个 `.claude/agents/lane-<short>.md`, frontmatter 含 `isolation: worktree`, dispatch via `claude --bg --agent lane-<short> "<prompt>"`, 监控 via `claude agents` filter `a:lane-<short>`.

**Phase 1 docs verdict**: **BLOCKED** — `isolation` 字段只接受 `worktree` 值 auto-create 临时 worktree，**无法 pin to existing Mercury worktree `D:/Mercury/Mercury-<short>`**.

**潜在改造方案** (if pursued anyway):
- 方案 A.1: `isolation: worktree` 让 Anthropic auto-create 临时 worktree at `D:/Mercury/Mercury-<short>/.claude/worktrees/<auto-id>` — 但这个 worktree 不是 lane 的 canonical worktree，每次 dispatch 都新建，**违背 Mercury "lane = 长期 scope" 模型**。
- 方案 A.2: 不用 `isolation: worktree`，让 subagent 直接在 lane cwd 跑，靠 lane operator 显式 `cd D:/Mercury/Mercury-<short> && claude --bg --agent lane-<short>` 启动 — 但这本质上是 Path B + 一层 subagent wrapper，wrapper 价值有限。

**Pro**: 利用 agent view 原生 `a:` filter 集中监控 per-lane in-flight sessions.

**Con**:
- Path A.1 与 Mercury lane 持久 worktree 模型冲突
- Path A.2 wrapper 本质多余
- Anthropic isolation 字段未来即便增 `worktree_path` 也需重 Path A 设计
- Path A 假设 lane = subagent type，但 Mercury lane 更接近"长期 scope"概念，与 subagent (短期任务模板) 模型不对等

**Verdict**: **DEFER (not NO-GO)** — 等 Anthropic 引入 worktree pinning (`worktree_path` 或等价 field) 后 re-eval。当前 docs 不支持。

### Path B — Mercury lane 不变, agent view 仅作 UI 层 (保守, RECOMMENDED)

**设计**: LANES.md / handoff / per-session files / regenerate-memory-index.sh 全保留。agent view 仅作 in-flight UI 集中：用户在主 terminal 跑 `claude agents` 看所有 bg sessions（含从 Mercury 任意 lane cwd dispatch 的）。

**Dispatch convention** (新增文档):
```bash
# Lane operator 在自己 lane 的 worktree 内 dispatch bg session:
cd D:/Mercury/Mercury-<short>    # 显式 cd to lane worktree
claude --bg "<prompt>"           # bg session 继承当前 cwd
                                 # → linkScanPath 走 lane project memory (Phase 2 verified)
                                 # → SessionStart hook fires per lane settings (Phase 2 verified;
                                 #   PreToolUse/PostToolUse/SessionEnd/PreCompact/SubagentStop
                                 #   inferred only — Phase 6 #386-D verify)
                                 # → lane-assertion.sh PASS at interactive session boot only
                                 #   (lane-assertion NOT invoked at bg session boot;
                                 #   file-editing bg workload may still trigger
                                 #   docs §"Before editing files" auto-isolation —
                                 #   待 Phase 6 #386-D verify)
```

**Monitoring**:
```bash
claude agents                    # 看所有 active bg sessions (all lanes)
claude agents --cwd D:/Mercury/Mercury           # 仅 main lane sessions
claude agents --cwd D:/Mercury/Mercury-side-bug  # 仅 side-bug lane sessions
```

**Modifications**:
- 0 代码改动
- 0 LANES.md 改动
- 0 `feedback_lane_protocol.md` 改动
- 新增 `.mercury/docs/guides/agent-view-dispatch.md` (或并入 `lane-naming.md`) 文档化 dispatch convention
- 可选：`scripts/lane-status.sh` 新增 (Phase 2 follow-on Issue) read `~/.claude/jobs/*/state.json` + `~/.claude/daemon/roster.json` 列 in-flight bg sessions per lane — schema-tolerant

**Pro**:
- 零 disruption to `feedback_lane_protocol.md` v1
- 零 Rule 4 红线 触碰
- 利用 agent view 集中监控价值（multiple bg sessions across lanes 在单 UI）
- 完全 detachable — `CLAUDE_CODE_DISABLE_AGENT_VIEW=1` 即 no-op
- empirical-verified safe **for read-only bg workload**: SessionStart hook fires, no routing-bleed on bare `claude --bg "echo ..."`, lane-assertion PASS at interactive session boot

**Con**:
- 双 mental model: Mercury lane registry + agent view session list 各管一摊（lane = 长期 scope, session = 短期任务）
- 未利用 agent view 的 unified UX (如 `a:lane-side-bug` filter — Path A 才能用)
- `~/.claude/jobs/` 历史 state.json 不自动归类到 lane (但可由 `lane-status.sh` 后置 group via cwd)
- **未 empirical verify 的关键 scenario** — Path B 实际部署需 Phase 6 后续 verify:
  1. **file-editing bg workload**: docs §"How file edits are isolated" 描述"Before editing files, Claude moves the session into an isolated git worktree under `.claude/worktrees/`". Phase 2 仅 `echo` 无 file write 未触发 auto-isolation。**实际 Claude bg task 若 edit files, 行为待验证** — auto worktree 在 `<lane-cwd>/.claude/worktrees/<id>` 是否会因 bg session 已在 Mercury worktree (即 Mercury lane worktree 不在 `.claude/worktrees/` skip 条件下) 而触发? 此场景下 bg session cwd 会被 supervisor 自动移动, Mercury 用户级 hooks 中 cwd-derived state 可能与 main lane state space 偏离 — 待 Phase 6 实测 git commits + auto worktree 是否丢失 work / 是否需 manual merge
  2. **其它 hook lifecycle** (PreToolUse / PostToolUse / SessionEnd / PreCompact / SubagentStop): Phase 2 未触发, 仅 inferred
  3. **bg session API cost tracking**: bg model invocation 是否计入 Mercury #361 cost-tracker daily ceiling, 待真实 model call 场景 verify

**Verdict**: **PRIMARY RECOMMENDATION (with explicit Phase 6 follow-on verify requirements)** — for read-only bg workload (查询 / 监控 / log inspection), Path B 立即可采纳; 对 file-editing bg workload, Path B dispatch convention 仍然适用但 auto-isolation 副作用需 Phase 6 sub-Issue empirical verify 才能宣称 "zero disruption to Mercury 多 lane v1 protocol"。

### Path C — Lane = long-lived pinned background session (激进重构, NO-GO)

**设计**: 删 LANES.md / handoff manual layer。Lane open = `claude --bg --agent lane-<short> "<bootstrap>"` + `Ctrl+T` pin to 防 1h idle 杀。Lane state 来源 `~/.claude/jobs/<id>/state.json` + `~/.claude/daemon/roster.json`。Lane close = `claude rm <id>` + 归档 state。

**Pro** (if it worked):
- 利用原生 supervisor + roster.json + state.json 替代 Mercury 自建 registry
- Unified UX via `claude agents`

**Con (kill-shots)**:

1. **Rule 4 红线** — `.mercury/docs/DIRECTION.md` + `feedback_lane_protocol.md` v1 (8 rules + 7+ sub-rules) 大量 obsolete，需 user 仲裁重写。
2. **supervisor 1h timeout 不匹配 Mercury lane 跨周/跨月 lifecycle** — Pin 机制（`Ctrl+T` pinning）只覆盖单 supervisor lifecycle，OS reboot / supervisor 崩溃后 pin 失效（state.json 留盘但 process 死，需 `claude respawn --all` 而非自动）。
3. **Mercury cross-session 持久态丢失** — handoff files / per-session memory 持久态由 Mercury 用户级 memory 层 (`~/.claude/projects/<encoded-cwd>/memory/`) 承担，state.json 不替代。强行用 state.json 替代会丢失 handoff 链路。
4. **bg session 模式与 Mercury "main agent" 概念不对等** — Mercury main lane operator 是人类 + Claude Code interactive session，不是 bg subagent dispatch。Path C 试图把 main lane 也降格为 bg session，与 user 实际 workflow 冲突。
5. **修改面 unbounded** — `feedback_lane_protocol.md` 重写、LANES.md 弃用、`scripts/lane-{claim,sweep,close,assertion,cap-check}.sh` 全部弃用或重做、`regenerate-memory-index.sh` 重写。投入大、回滚难。
6. **schema-stability risk** — state.json / roster.json schema undocumented，Mercury 押注未稳定 API 是 anti-pattern。

**Verdict**: **NO-GO** — 风险高、收益不明、Rule 4 红线、empirical verify 表明 supervisor lifecycle 与 Mercury lane lifecycle 量级不匹配。即便 Anthropic 未来引入 pin-permanence 机制，Path C 仍需重新评估（不应作为默认演进方向）。

---

## Recommendation

**PRIMARY**: **Path B (Mercury lane 不变 + agent view 仅作 UI 层 + dispatch convention 文档化)**

**Fallback chain** (per autorun §5):
- Path A docs-blocked → 自动 fallback to Path B (本 ADR primary)
- 若 future Anthropic 引入 worktree pinning → reopen Path A re-eval per `project_agent_view_partial_defer.md` (Phase 5 创建)
- Path C 永久 NO-GO unless DIRECTION.md major rewrite triggered by 其他 reason

**Implementation roadmap** (Path B Phase 5 follow-on):

1. **Sub-Issue #386-A** (P2, lane:main, docs): 新增 `.mercury/docs/guides/agent-view-dispatch.md` 含：
   - Bare bg dispatch convention (`cd <lane-worktree> && claude --bg "..."` pattern, **read-only bg workload safe per Phase 2 empirical**)
   - Monitoring 方法 (`claude agents --cwd <lane-worktree>`)
   - Hook compatibility note (**SessionStart verified Phase 2; 其它 lifecycle inferred — 待 Phase 6 verify**)
   - `.claude/worktrees/` namespace coexistence note (Mercury `--worktree` + agent view auto-iso 共用)
   - Disable channel (`CLAUDE_CODE_DISABLE_AGENT_VIEW=1`)
2. **Sub-Issue #386-B** (P3, lane:main, enhancement, optional): `scripts/lane-status.sh` POC — read `~/.claude/jobs/*/state.json` + `~/.claude/daemon/roster.json`, group by cwd, 输出 per-lane in-flight bg session table。Schema-tolerant（defensive parse + 容忍 schema 变化）。
3. **Sub-Issue #386-C** (P3, lane:main, defer-record): write `memory/project_agent_view_partial_defer.md` 记录 Path A 当前 blocked 状态 + re-eval conditions:
   - Anthropic 在 sub-agents.md frontmatter 引入 `worktree_path` 或 pin-to-existing 等效字段
   - Mercury lane 数增至 ≥ 5 (3 active lanes — `main` + `side-bug` + `side-sot`; +1 closed `side-multi-lane` — 当前为 sweet spot, Path A unified UX 价值随 lane 数线性增长)
   - User 主动请求集中监控功能（当前手动 `claude agents` 已够用）
4. **Sub-Issue #386-D** (P1, lane:main, research): Phase 6 empirical verify — Path B file-editing bg workload + 其它 hook lifecycle event 实测:
   - bg session 执行 file-editing task (Edit/Write tool, 实际触发 docs §"before editing files" 条件) 时, supervisor 是否自动 move cwd 到 `.claude/worktrees/<id>`?
   - 若 move, Mercury 用户级 hooks 中 cwd-derived state (mem0_hooks.py 用 `os.getcwd()` 推导 project memory 路径) 是否会偏离 main lane state space?
   - file-editing bg session 的 git commits 在 auto worktree 产生, 是否会丢失 / 是否需 manual merge 回 Mercury lane?
   - PreToolUse / PostToolUse / SessionEnd / PreCompact hook 是否在 bg session 完整 fire (通过 Edit + Bash + close-session 探针验证)?
   - SubagentStop hook 是否在 `claude --bg --agent <Mercury-subagent>` 场景 fire?
   - bg session API cost tracking 是否计入 Mercury #361 cost-tracker daily ceiling (实际 model call 后 verify cost_tracker jsonl 存在 + ceiling 累计)?

**Out of scope** (本 ADR 不处理):
- Mercury `.claude/worktrees/` 旧 entry (session-22-phase1, skill-deep-research) cleanup —— 单独 Issue
- bg session cost tracking 在 Mercury #361 framework 内的实际行为 verify —— 待 bg session 跑 model invocation 后再 measure
- agent-teams primitive (docs 提及但本 ADR 不评估) —— 独立 Issue 若未来用到

---

## Consequences

### Positive

- 零 disruption to Mercury 多 lane v1 protocol **for read-only bg workload** (file-editing case 待 Phase 6 verify)
- Mercury 操作员可选用 agent view 提升监控体验（多 bg session in single UI）但不强制
- empirical-verified Mercury **SessionStart** hook 在 bg session fire — 其它 lifecycle event (PreToolUse / PostToolUse / SessionEnd / PreCompact / SubagentStop) inferred only, 待 Phase 6 verify
- Path A DEFER 不堵死未来 Anthropic feature roadmap 演进
- 满足 CLAUDE.md MUST: modular detachable / upward compatibility / no self-research (Mercury 不自建 supervisor)

### Negative

- Path B 双 mental model（lane registry + session list）保留 — 操作员需理解两层模型
- Mercury `.claude/worktrees/` 与 agent view auto worktree 共用目录 — cleanup 时需 verify 来源
- `claude --bg` 当前 hidden in `--help` — 文档化时需标注 research-preview 状态
- bg session cost tracking 在 Mercury #361 框架内的行为待 future model-invocation empirical 确认

### Neutral

- `~/.claude/jobs/<id>/state.json` 持续累积 — 不影响 Mercury，但 user 长期使用可能需手动 prune
- `claude respawn --all` 在 OS reboot 后恢复 bg sessions — Mercury 不强制依赖，operator 自决

---

## References

### Anthropic docs (Phase 1 verified 2026-05-15)

- [Manage multiple agents with agent view — Claude Code Docs (`code.claude.com/docs/en/agent-view`)](https://code.claude.com/docs/en/agent-view)
- [Subagents — Claude Code Docs (`code.claude.com/docs/en/sub-agents`)](https://code.claude.com/docs/en/sub-agents)
- [Hooks — Claude Code Docs (`code.claude.com/docs/en/hooks`)](https://code.claude.com/docs/en/hooks)
- [Agent view in Claude Code (announce blog)](https://claude.com/blog/agent-view-in-claude-code)

### Mercury authority

- [Issue #386](https://github.com/392fyc/Mercury/issues/386) — agent view 适配评估 (this ADR's parent)
- [Issue #381](https://github.com/392fyc/Mercury/issues/381) — S6-side-bug tech intel sweep (parent of #386)
- [Issue #315](https://github.com/392fyc/Mercury/issues/315) — multi-lane protocol v1 epic (closed, defines current behavior)
- [PR #347](https://github.com/392fyc/Mercury/pull/347) — `feedback_lane_protocol.md` v1 promotion admin-merge (2026-05-03)
- `memory/feedback_lane_protocol.md` v1 — 8 rules + sub-rules (authority on current lane behavior)
- `memory/LANES.md` — active/closed lane registry
- `.mercury/docs/guides/lane-naming.md` — lane naming + worktree convention
- `scripts/lane-assertion.sh` — Δ11 path C three-way alignment check (S98 Phase 2 PASS @ main lane cwd)
- `.mercury/docs/research/multi-lane-protocol-2026-04-25.md` — multi-lane research baseline

### Empirical evidence files (Phase 2)

- `~/.claude/jobs/a06e1416/state.json` — bg session 1 state record
- `~/.claude/jobs/1baace78/state.json` — bg session 2 state record (idle baseline)
- `~/.claude/projects/D--Mercury-Mercury/a06e1416-8d63-4e5a-8168-402d94f62a0d.jsonl` — bg session transcript (27 lines, hook attachments visible)
- `~/.claude/daemon.log` — supervisor lifecycle log (2026-05-14T16:42-16:44)

### Related Mercury Issues (sister context)

- [#382](https://github.com/392fyc/Mercury/issues/382) — hook layer modernization (Claude Code v2.1.x + Codex CLI 0.129+ event audit)
- [#384](https://github.com/392fyc/Mercury/issues/384) — DIRECTION.md memory re-eval research
- [#385](https://github.com/392fyc/Mercury/issues/385) — context strategy research
