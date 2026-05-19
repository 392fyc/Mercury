# Phase 6 GUI MVP — Agent view backend data source schema (empirical)

**Issue**: [#411](https://github.com/392fyc/Mercury/issues/411) (research-front, sub-task 1-3 + 5)

**Status**: Phase 1 — empirical schema record (S120, 2026-05-19)

**Session**: S120 (main lane)

**Companion ADR**: `phase6-gui-mvp-tech-stack-2026-05.md`

**Prior art**: [`agent-view-multi-lane-adaptation-2026-05.md`](agent-view-multi-lane-adaptation-2026-05.md) (PR #387, Closes #386) Phase 1+2 docs+empirical
| [`agent-view-phase6-empirical-2026-05.md`](agent-view-phase6-empirical-2026-05.md) (PR #391, Closes #391) Phase 6 empirical

---

## TL;DR

Empirical inspection of 4 sample `~/.claude/jobs/<id>/state.json` files + `~/.claude/daemon/roster.json` + `~/.claude/jobs/pins.json` + `claude agents` CLI 验证 + `timeline.jsonl` 新发现, 为 #411 GUI MVP 的 read-side data layer 建立 schema baseline。

**3 个关键发现矫正 #411 body 描述**:

1. **state.json `state` 字段实际枚举值** ≠ 6-value display taxonomy (Working / Needs input / Idle / Completed / Failed / Stopped). 4 samples 全部 `state: "done"` — 推断 docs taxonomy 是 GUI display layer, raw state.json 用底层 token。Mercury GUI 不能简单 string match docs taxonomy, 需建立 state-token → display-label 映射。
2. **`claude agents` CLI 不可在 non-interactive shell parse** — 报错 `"requires an interactive terminal (stdout is not a TTY)"`. Filter primitives `a:<agent>` / `s:<state>` / `#<PR>` 报错 `"too many arguments for 'agents'. Expected 0 arguments but got 1."` → 推断这些是 interactive REPL 输入, 不是 CLI flag。**Mercury GUI 不能 spawn `claude agents` parse stdout** — 必须直接读 `~/.claude/jobs/*/state.json` + `~/.claude/daemon/roster.json` 自己构建 view。
3. **新发现 `timeline.jsonl` 字段** — 同目录 sibling file, 含 `{at, state, detail, text}` 转换历史, 适合 GUI 时间轴 (scenario 2 of #411 body)。**`~/.claude/jobs/<id>/timeline.jsonl` 应纳入 read-side data source list**。

---

## state.json 字段 schema (4 samples, 2026-05-14 ~ 2026-05-15)

### Sample identification

> **PII note**: Raw JSON sample values below preserve `D:\Mercury\Mercury` (project-wide Mercury convention path, not user-specific) but mask Windows user directory `C:\Users\<user>\` (originally `C:\Users\392fy\`) per Mercury PII redaction convention. Mercury GUI implementation should normalize to `${HOME}` / `${CLAUDE_CONFIG_DIR}` / `${USERPROFILE}` env-vars rather than hardcoding any specific user path.

| Sample id | template | intent | cliVersion |
|-----------|----------|--------|------------|
| `1baace78` | `bg` (--help respawn) | (empty) | (absent — older session) |
| `40726463` | `research` (subagent) | `What is 2+2? Answer in one sentence then stop.` | `2.1.142` |
| `a06e1416` | `bg` | `echo from-bg-probe-test` | `2.1.141` |
| `a8c58664` | `bg` (Edit tool fire) | `Use the Edit tool to modify ... probe1-target.txt ...` | `2.1.142` |

### Field schema — required (present in all 4)

| Field | Type | Sample value | Notes |
|-------|------|--------------|-------|
| `state` | string | `"done"` (all 4) | **底层 token, NOT display taxonomy**. 推断其它 token: `working` / `idle` / `needs_input` / `failed` / `stopped` — 需 empirical capture (Phase 2). |
| `detail` | string | `"answered 2+2=4 in one sentence"`, `"echoed from-bg-probe-test"`, `"(idle — send a prompt to start)"`, `"Edit applied to probe1-target.txt in worktree; file written with both target lines; worktree preserved on worktree-phase6-probe-1 branch; session returned to main"` | 一句话总结, 适合 GUI list item subtitle |
| `tempo` | string | `"idle"` (all 4) | 似 state 的延迟版 — 推断有 `"active"` / `"slow"` 等 token (UNVERIFIED) |
| `output` | object\|null | `{"result": "2+2 = 4"}`, `{"result": "from-bg-probe-test"}`, `{"result": "probe1-target.txt edited and written; worktree phase6-probe-1 preserved"}`, `null` | 终态 output payload; null = 未完成 OR template 不产出 |
| `children` | null | `null` (all 4) | 推断 subagent nested dispatch 才填 — agent-teams 原语 |
| `linkScanOffset` | int | `0`, `17321`, `53522`, `142682` | byte offset 进 transcript jsonl, internal pagination cursor |
| `template` | string | `"bg"` (3), `"research"` (1) | dispatch template — `bg` (`claude --bg`) vs `research`/`dev`/etc (`claude --bg --agent <name>`) |
| `respawnFlags` | string[] | `["--help"]`, `["--agent", "research"]`, `[]` (2) | restart 时复用的 CLI args |
| `intent` | string | first-line prompt | 适合 GUI list item title |
| `sessionId` | string (UUID) | `1baace78-6b3a-478b-829b-0def2187745c` | full UUID, daemonShort 是 8-char prefix |
| `resumeSessionId` | string (UUID) | == sessionId | resume target — 多数 case 等于 sessionId, 但理论可不同 (`claude --resume <id>`) |
| `daemonShort` | string | `"1baace78"` (8 hex) | UUID prefix; `claude agents` filter 用这个 |
| `cwd` | string | `"D:\\Mercury\\Mercury"` (all 4) | Windows backslash (JSON-escaped), **NOT POSIX**. GUI 需 normalize 用于 LANES.md cross-reference |
| `createdAt` | ISO 8601 | `"2026-05-14T16:42:55.110Z"` | UTC, ms precision |
| `updatedAt` | ISO 8601 | 同上 | 最近字段写入时间 |
| `firstTerminalAt` | ISO 8601 | 同上 | 第一次 reach terminal state — elapsed = firstTerminalAt - createdAt |
| `backend` | string | `"daemon"` (all 4) | supervisor backend; 推断有 `"local"` 等 token (UNVERIFIED) |

### Field schema — optional (newer sessions only, cliVersion 2.1.141+)

| Field | Type | Sample value | Notes |
|-------|------|--------------|-------|
| `inFlight` | object | `{"tasks": 0, "queued": 0, "kinds": []}` | mid-flight task counter; idle session = all zeros. 3/4 samples 含, 最老 `1baace78` 缺 (pre-2.1.141) |
| `linkScanPath` | string | `"C:\\Users\\<user>\\.claude\\projects\\D--Mercury-Mercury\\<id>.jsonl"` | transcript file path; linkScanOffset 进该文件 |
| `cliVersion` | string | `"2.1.141"` / `"2.1.142"` | CLI version when session created |
| `name` | string | `"math calculation response"`, `"from-bg-probe-test"`, `"phase6-probe file editing"` | auto-generated display name (LLM-sourced from intent) |
| `nameSource` | string | `"auto"` (all 3 with name field) | 推断有 `"manual"` token if user supplies via `claude --bg --name <x>` |

### Field schema — mid-flight only (per #386 ADR Phase 6 §state worktreePath empirical)

| Field | Type | Notes |
|-------|------|-------|
| `worktreePath` | string | 仅 PreToolUse-trigger Edit + EnterWorktree path 之间 mid-flight 期间出现 (per `agent-view-phase6-empirical-2026-05.md`); terminal state 时移除 (S120 4 samples 全 terminal, 故全部 absent) |
| `worktreeBranch` | string | 同上 mid-flight only |

**Schema-tolerant parsing 必须**: Mercury GUI 读 state.json 必须 defensive (missing inFlight / linkScanPath / cliVersion / name / nameSource on older sessions; missing worktreePath / worktreeBranch on terminal sessions; potentially missing children / output / respawnFlags on edge cases)。

---

## roster.json 字段 schema

```json
{
  "proto": 1,
  "supervisorPid": 2240,
  "updatedAt": 1778822198033,
  "workers": {}
}
```

| Field | Type | Notes |
|-------|------|-------|
| `proto` | int | schema version (currently 1) |
| `supervisorPid` | int | per-user supervisor process pid |
| `updatedAt` | unix ms | 最后 supervisor heartbeat — staleness 指标 (e.g. >1h → 1h-timeout 已 fire, supervisor process 释放) |
| `workers` | object (map) | active worker process map; key 推断是 daemonShort / sessionId, 当前为空 (no active session at inspection time) |

**关系**: `roster.json.workers[<key>]` ↔ `jobs/<id>/state.json` 1:1 (active sessions); jobs/<id>/state.json 在 supervisor 释放后 persist (1h timeout 后 process die 但 state.json 留盘). Mercury GUI 用 `roster.json.workers` 判断 "live" vs "stopped" sessions。

---

## pins.json 字段 schema

```json
[]
```

`~/.claude/jobs/pins.json` 当前为空 array。推断: user pin 操作 (e.g. `claude agents` 内 pin shortcut) 将 sessionId / daemonShort 加入。**Mercury GUI v1 可忽略**, v2+ 若实现 pin 功能再纳入。

---

## timeline.jsonl 字段 schema (新发现, NOT in #411 body)

`~/.claude/jobs/<id>/timeline.jsonl` — 每行 1 个 JSON event:

```json
{"at":"2026-05-15T05:10:53.100Z","state":"done","detail":"answered 2+2=4 in one sentence","text":"2+2 等于 4。"}
{"at":"2026-05-15T05:12:10.337Z","state":"done","detail":"Edit applied to probe1-target.txt in worktree; ...","text":"Edit applied in isolated worktree at `.claude/worktrees/phase6-probe-1/.tmp/phase6-probe/probe1-target.txt`. Exiting per instruction.\n\nProbe-1 complete. ..."}
{"at":"2026-05-14T16:43:12.094Z","state":"done","detail":"echoed from-bg-probe-test","text":"from-bg-probe-test"}
```

| Field | Type | Notes |
|-------|------|-------|
| `at` | ISO 8601 | event timestamp |
| `state` | string | state token at event (匹配 state.json.state) |
| `detail` | string | 同 state.json.detail |
| `text` | string | full LLM output (state.json.output.result 是缩略, text 是 raw) |

**3 samples 全部仅含 terminal event** — 推断仅 terminal transitions logged (没有 mid-flight `state: "working"` event). UNVERIFIED — 需 catch mid-flight session 验证。

**Mercury GUI relevance**: timeline.jsonl 适合 #411 scenario 2 "跨 session 时间轴" 视觉化 — per-session event log → tree/timeline UI. **MUST add to read-side data source list**。

---

## `claude agents` CLI 验证 — 关键负面发现

| Command | Result | Implication |
|---------|--------|-------------|
| `claude agents` | `'claude agents' requires an interactive terminal (stdout is not a TTY) — open a new terminal and run it there.` | non-TTY 不可调用; Mercury GUI 不能 spawn-and-parse |
| `claude agents a:dev` | `error: too many arguments for 'agents'. Expected 0 arguments but got 1.` | filter 不是 CLI arg |
| `claude agents s:idle` | 同上 | 同上 |

**结论**: `claude agents` 是 interactive TUI / REPL 命令, 不是 batch CLI。**Mercury GUI 的读 path 必须 bypass 该 CLI**, 直接读 `~/.claude/jobs/*/state.json` + `~/.claude/daemon/roster.json` + `~/.claude/jobs/<id>/timeline.jsonl` 自己 aggregate。Filter primitives `a:` / `s:` / `#` 由 Mercury GUI 自己实现 (用 state.json.template / state.json.state / state.json.intent regex 等)。

**对 #389 (lane-status.sh POC) 的 implication**: #389 已正确选择 "read state.json + roster.json group by cwd" 路径 (不依赖 `claude agents` CLI). 该路径已 docs-evidence 验证 (无 CLI 可用) — #389 仍 strictly viable, NOT 被 `claude agents` CLI replace。

---

## #389 prior-art audit

`gh issue view 389` 检查 (S120 2026-05-19):

| Aspect | #389 (shell POC) | #411 (GUI MVP) |
|--------|------------------|----------------|
| 标题 | "(optional) lane-status.sh POC: read state.json + roster.json group by cwd" | "Phase 6 GUI MVP — Mercury self-built GUI consuming agent view backend as data source" |
| 数据源 | `~/.claude/jobs/*/state.json` + `~/.claude/daemon/roster.json` | **同左** + timeline.jsonl + LANES.md + cost-tracker jsonl + mem0 + gh CLI |
| 分组 key | by `cwd` field | by lane (cross-ref LANES.md.Worktree path) |
| 输出 | 终端表格 (shell stdout) | GUI (form factor TBD) |
| Scope | shell script POC | full Mode A planning + ADR + implementation Issue chain |
| Priority | P3 OPTIONAL | P2 research |
| State | OPEN | OPEN |

**Verdict**: **#389 NOT superseded by #411**. 关系是 layered:

- **#389 = #411 的 data-layer POC / CLI-fallback alternative**
- 若 #411 GUI 落地, #389 仍价值 (shell-script monitor for headless / SSH session use case)
- 若 #389 先落地 (e.g. autorun-safe shell-only path), 可作为 #411 GUI 的 backend data adapter

**S120 recommendation**: keep #389 OPEN as P3 OPTIONAL. **不 close**, **不 fold into #411**. 在 #411 implementation Issue chain file 时 ([未来 sub-Issue]), 可引用 #389 作为 prior-art / CLI 副产品。

---

## 综合 — Mercury GUI read-side data source 清单 (per v1 scope user decision 2026-05-20)

User S120 Mode A 决策 (2026-05-20): v1 scope = scenarios **1 + 5 only** (cross-lane snapshot + Issue/PR dashboard); scenarios 2 + 3 + 4 推迟 v2+。

### v1 in-scope data sources (scenarios 1 + 5)

```
~/.claude/jobs/<id>/state.json       — per-session 主状态 (22 fields, schema-tolerant 必需) — scenario 1
~/.claude/daemon/roster.json          — supervisor heartbeat + active-worker registry — scenario 1
LANES.md                              — Mercury lane registry (group key = cwd → lane) — scenario 1
gh CLI (Issue/PR state)               — scenario 5
```

### v2+ deferred data sources (scenarios 2 / 3 / 4)

```
~/.claude/jobs/<id>/timeline.jsonl    — per-session 事件流 (4 fields, NEW finding S120) — scenario 2 跨 session 时间轴
memory/sessions/S<N>(-<lane>)?.md     — per-session SoT (Mercury 自建) — scenario 2
memory/session-handoff(-<lane>)?.md   — handoff doc (跨 session 持久态) — scenario 2
~/.claude/scripts/cost-tracker/<id>.jsonl — cost-tracker (#361) — scenario 4 cost trend chart
[Phase 5 Notify Hub IPC]              — scenario 3 桌面端反向控制台
~/.claude/jobs/pins.json              — pin 列表 (v1 可忽略, v2+ 若实现 pin 功能再纳入)
```

**read-only 严格**: 所有 `~/.claude/` 下文件 Anthropic owned, Mercury GUI **不可 write/delete**。Mercury 自建文件 (memory/, .mercury/) Mercury owned, GUI 可 read/edit (但 v1 推荐 read-only)。

**Polling vs file-watcher** (Mode A blocker, 留 user 决策):
- chokidar (npm, ESM-only v5 from Nov 2025, node 20+ min) 是 cross-platform 标准方案
- Windows recursive watching 已 native support (chokidar README)
- 备选: 5-15s polling — 简单但 CPU/IO 浪费
- 推荐预选: chokidar 监听 `~/.claude/jobs/*/state.json` + `~/.claude/jobs/*/timeline.jsonl` + `~/.claude/daemon/roster.json` (3 path glob, debounce 200-500ms)

---

## Open questions (carry into tech stack ADR)

1. state.json `state` 完整 token 集 (除 `"done"` 外的 `"working"` / `"idle"` / `"failed"` / 等具体字符串) — 需 mid-flight + failed + stopped session 各 capture 1 个。S120 仅 4 terminal samples, 不充分。
2. `tempo` 字段除 `"idle"` 外的其它 token (推断 `"active"` / `"slow"` 但 UNVERIFIED)
3. `backend` 字段除 `"daemon"` 外的 token (UNVERIFIED)
4. timeline.jsonl mid-flight events 是否 logged (S120 3 samples 全 terminal-only)
5. roster.json.workers map 结构 (S120 captured 时全空, 需 active session 期间 capture)

**这些 UNVERIFIED 项不阻塞 #411 Mode A 决策** — GUI 设计阶段 schema-tolerant 即可。Phase 2+ 实现期间 empirical capture 补全。

---

## References

- [`agent-view-multi-lane-adaptation-2026-05.md`](agent-view-multi-lane-adaptation-2026-05.md) — Phase 1+2 docs+empirical, PR #387 Closes #386
- [`agent-view-phase6-empirical-2026-05.md`](agent-view-phase6-empirical-2026-05.md) — Phase 6 empirical, PR #391 Closes #391
- [Anthropic docs: agent view](https://code.claude.com/docs/en/agent-view) — official agent view docs
- [Issue #389](https://github.com/392fyc/Mercury/issues/389) — lane-status.sh POC (prior-art / CLI alternative, OPEN, keep)
- [Issue #411](https://github.com/392fyc/Mercury/issues/411) — Phase 6 GUI MVP scoping (本 ADR target)
- 4 sample state.json files inspected: `1baace78` / `40726463` / `a06e1416` / `a8c58664` (2026-05-14 ~ 2026-05-15)
