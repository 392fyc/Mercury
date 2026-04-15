# Phase 4-2 Worktree-per-Task — Mount Path Evaluation

> **Status**: research synthesis (Mercury S54)
> **Date**: 2026-04-16
> **Issue**: #183 (parent) / Phase 4-2 sub-tracks: #246 (4-2.a session_chain DB), #247 (4-2.b worktree-per-task), #248 (Karpathy-pattern)
> **Upstream research protocol**: WebSearch + WebFetch + local skill source inspection

## Executive Summary

Phase 4-2 requires **worktree-per-task isolation** driven programmatically by a Mercury dev-pipeline agent (not by a human CLI invocation). Two mount candidates — OMC `project-session-manager` and `obra/superpowers` — were evaluated for this role.

**Verdict**:

- **Neither candidate provides orchestrator-driven, multi-subagent-parallel worktree creation.** Both are 1:1 (one skill invocation → one worktree, human-triggered).
- **Recommended path**: Mercury self-implements a ~60–80 line bash/Python wrapper, using OMC `worktree.sh` as structural reference and Superpowers `using-git-worktrees` SKILL.md as prompt-discipline reference.
- **No full submodule mount**; selective cherry-pick only (both projects are MIT or have MIT skill subsets).

Additionally, the research scope was extended to:

- **Memory-layer base re-evaluation** (R3 claude-mem, R5 claude-memory-compiler upstream, R4 Karpathy Obsidian workflow).
- **Phase 4-1 status correction**: the originally planned `session_chain` + `handoff-orchestrator` path was superseded by the standalone `claude-handoff` plugin. `session_chain` DB remains a required Phase 4-2 component and will be added as an extension to the `claude-handoff` plugin.

---

## R1: OMC `project-session-manager` (PSM)

**Source**: `<CLAUDE_PLUGINS_DIR>/marketplaces/omc/skills/project-session-manager/` (local install; `CLAUDE_PLUGINS_DIR` defaults to `~/.claude/plugins/`)
(local installed version `4.11.2`; latest `v4.11.6` at 2026-04-13)

### Key findings

| Question | Answer |
|---|---|
| Creation trigger | Explicit human command: `psm fix <ref>` / `psm review <ref>` / `psm feature <proj> <name>` / `omc teleport` |
| Multi-subagent parallel | **Not supported.** 1:1 mapping (one invocation = one worktree + tmux session + Claude Code instance). Multiple subagents require N manual invocations. |
| Cleanup | Explicit `psm kill <session>` or `psm cleanup` (merged-PR polling). `cleanup_after_days: 14` config exists but **is not implemented** in code. No orphan detection. |
| Coupling | Soft; tmux optional (`--no-tmux`); independent state (`~/.psm/sessions.json`). No `notepad`/`team` dependencies. |

### Modular-mount 5-criteria assessment

| Criterion | Score | Evidence |
|---|---|---|
| Community activity | ✅ Pass | 28.9k stars, v4.11.6 released 2026-04-13 |
| Interface stability | ⚠ Medium | CLI stable; internal bash function signatures unversioned |
| Detachability | ✅ High | `worktree.sh` is pure bash; deps: `git`, `jq`, optional `gh`/tmux |
| Maintainer | ✅ Pass | Yeachan-Heo, active OMC maintainer |
| Replacement cost | ✅ Low | Self-implement ~60–80 lines matches the core logic |

### Recommendation

**Selective cherry-pick** from `worktree.sh` (specifically `psm_create_issue_worktree`, `psm_remove_worktree`, `validate_worktree_path`). **No full mount** — PSM is human-triggered and carries tmux/sessions.json state that Mercury does not need.

### Sources

- <https://github.com/Yeachan-Heo/oh-my-claudecode>
- <https://github.com/Yeachan-Heo/oh-my-claudecode/releases>
- <https://github.com/Yeachan-Heo/oh-my-claudecode/blob/main/CHANGELOG.md>

---

## R2: `obra/superpowers` worktree subset

**Source**: `skills/using-git-worktrees/SKILL.md` + `skills/subagent-driven-development/SKILL.md` + `skills/dispatching-parallel-agents/SKILL.md`

### Key findings

- **Mercury Issue #197 REJECT does NOT cover the worktree dimension.** The REJECT verbatim cites *"strictly weaker than OMC … ships zero `Stop` or `SubagentStop` hooks"* — a Quality-Gate-specific verdict with scope explicitly limited (*"the REJECT applies only to the Phase 2 Quality Gate purpose"*).
- `using-git-worktrees` is a **pure SKILL.md (prompt discipline, no code)** — trivial to cherry-pick single-file.
- **Parallel multi-worktree is NOT implemented.** `dispatching-parallel-agents` is a scheduling directive (context isolation only); Issue #469 proposes Claude Code `TeamCreate`/`SendMessage`-backed parallelism, currently **open**.
- License: MIT.
- Architectural stability: v2.0 split → v5.x re-merge history. Red flag noted in #197 stands.

### Recommendation

**Cherry-pick `using-git-worktrees/SKILL.md` as prompt-discipline reference** (requires cherry-pick manifest entry per CLAUDE.md protocol). **Do NOT rely on it for parallel orchestration** — that capability does not exist upstream.

### Sources

- <https://github.com/obra/superpowers/blob/main/skills/using-git-worktrees/SKILL.md>
- <https://github.com/obra/superpowers/blob/main/skills/subagent-driven-development/SKILL.md>
- <https://github.com/obra/superpowers/issues/469> (open, parallel plan execution proposal)

---

## R3: `claude-mem` (memory-layer replacement candidate)

**Source**: <https://github.com/thedotmack/claude-mem>

### Key findings

| Field | Value |
|---|---|
| License | **AGPL-3.0** (subdir `ragtime/`: PolyForm Noncommercial) |
| Latest version | v12.1.1 (2026-04-15) |
| Interface | npm global install + Claude Code plugin + MCP server + SQLite + ChromaDB vector DB |
| Storage | SQLite + ChromaDB (vector) + web viewer (`localhost:37777`) |

### Verdict

**Do NOT switch.** AGPL-3.0 is a hard blocker per Mercury cherry-pick protocol (`CLAUDE.md` requires MIT / Apache-2.0 / other permissive).

### Sources

- <https://github.com/thedotmack/claude-mem>
- <https://www.npmjs.com/package/claude-mem>
- <https://docs.claude-mem.ai/installation>

---

## R4: Karpathy LLM Wiki pattern (memory-layer architecture)

**Primary source**: Karpathy's Gist — <https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f>

### Core pattern

```
raw/          →  [LLM compiler]  →  wiki/
(immutable        ingest: 1 src       entity pages
 source docs)     touches 10-15 pgs   concept pages
                                      synthesis pages
                       ↑
                    lint pass (periodic audit):
                    - contradictions
                    - orphans
                    - outdated claims
                    - missing cross-refs

schema doc (CLAUDE.md / AGENTS.md)
index.md  — categorised page summaries
log.md    — append-only operation log (ingest/query/lint)
```

Three ops: **ingest** (compile source), **query** (search + synthesise), **lint** (periodic health).

### Mercury AgentKB gap analysis

| Karpathy element | Mercury AgentKB status |
|---|---|
| Immutable `raw/` layer | ❌ Missing |
| LLM-written `wiki/` layer | 🟡 Partial (KB exists, compile is not per-source) |
| Schema doc (CLAUDE.md/AGENTS.md) | ✅ Implemented |
| `index.md` summary catalogue | 🟡 Partial (KB `00-index/` exists but link-style, not summary-style) |
| `log.md` append-only operation log | ❌ Missing |
| Per-source `ingest` op | ❌ Missing |
| `query` → wiki write-back | ❌ Missing (research outputs sit in `.mercury/docs/research/`, don't update KB) |
| `lint` periodic audit | ❌ Missing (we have `kb-lint` skill but manual) |
| Cross-reference maintenance | ❌ Missing |

### Recommended improvements (high-value)

**P1**:

1. `log.md` append-only operation log — audit chain for each ingest/compile/lint op.
2. `query` → wiki write-back — research agent outputs should update KB wiki pages instead of sitting in isolated research reports.

**P2**:

3. `raw/` immutable source layer — prevent LLM from overwriting source docs.
4. Periodic `lint` op — hook `kb-lint` skill to a scheduled trigger.

### Sources

- <https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f>
- <https://venturebeat.com/data/karpathy-shares-llm-knowledge-base-architecture-that-bypasses-rag-with-an>

---

## R5: `claude-memory-compiler` upstream solidity

**Source**: <https://github.com/coleam00/claude-memory-compiler> (Mercury AgentKB's fork origin)

### Key findings

| Field | Value |
|---|---|
| Stars / Forks | ~714 / 196 |
| Published | 2026-04-06 (10 days old as of this research) |
| Total commits | **2** (initial + README fix) |
| Releases / Tags | **none** |
| License | **none** (Issue #11 requested 2026-04-13, unresolved) |
| Contributors | 1 (coleam00) |
| Open issues | 9 (6 bugs in 10 days) |

### Verdict

**Continue using the Mercury AgentKB fork; freeze upstream cherry-picking.**

- **Hard blocker**: no license = non-compliant to cherry-pick further upstream changes per Mercury protocol.
- Mercury's AgentKB fork has already diverged significantly (rsync NAS, session-continuity, handoff-orchestrator, flush bypass, env-strip fixes) — quality now exceeds upstream.
- Action: mark `upstream_license` = `UNLICENSED` in `.mercury/state/upstream-manifest.json`; monitor upstream Issue #11 for license clarification.

### Sources

- <https://github.com/coleam00/claude-memory-compiler>
- <https://github.com/coleam00/claude-memory-compiler/issues>
- <https://github.com/coleam00/claude-memory-compiler/issues/11> (license request)

---

## Phase 4-2 Implementation Path (synthesised)

### 4-2.a `session_chain` DB

**Decision**: extension to `claude-handoff` plugin (separate repo: <https://github.com/392fyc/claude-handoff>).

**Rationale**: Phase 4-1 landed as the `claude-handoff` plugin (replacing the originally planned `session_chain` + `handoff-orchestrator` path). `session_chain` DB is the missing tracking layer for cross-session continuity and logically belongs inside that plugin, not in Mercury proper.

**Scope (initial)**:

- SQLite schema: `session_chains (chain_id, parent_session_id, child_session_id, handoff_ts, project_dir, task_ref)`.
- Read/write API used by the plugin's `session-start.py` and `handoff` skill.
- Migration-safe (use `INSERT OR IGNORE ... ON CONFLICT DO UPDATE`; never `INSERT OR REPLACE`, per KB concept `sqlite-upsert-semantics`).

**Deliverable**: separate PR on `github.com/392fyc/claude-handoff` (cross-workspace work from Mercury session).

### 4-2.b Worktree-per-task

**Decision**: **self-implement**, ~60–80 lines of bash/Python in Mercury's `scripts/` or `adapters/`.

**Reference materials**:

- OMC `worktree.sh` — structural reference for `git worktree add`, path validation, prune.
- Superpowers `using-git-worktrees/SKILL.md` — prompt discipline for dev agents operating inside a worktree.

**Scope (MVP)**:

- Programmatic API: `create_worktree(task_id, branch_slug)` → returns path; `remove_worktree(task_id)`.
- Integration: dev-pipeline skill injects `worktreePath` into TaskBundle before spawning the dev subagent.
- Orphan detection: `list_orphans()` cross-refs active task state vs. `.worktrees/` entries.

**Out of scope for MVP**: tmux, session.json registry, GitHub API polling cleanup (PSM's extras).

### 4-2.c OMC PSM evaluation

**Decision**: document-only (this report). No mount PR. Selective cherry-pick of `worktree.sh` logic folds into 4-2.b.

---

## Cross-Repo Deliverables

| # | Repo | Artifact | Status |
|---|---|---|---|
| 1 | Mercury | `.mercury/docs/research/phase4-2-worktree-mount-eval.md` (this file) | ⬜ PR pending |
| 2 | Mercury | `.mercury/docs/EXECUTION-PLAN.md` — Phase 4-1 correction + Phase 4-2 expansion | ⬜ PR pending |
| 3 | Mercury | Issues: (a) Phase 4-2.a session_chain DB → #246, (b) Phase 4-2.b worktree-per-task impl → #247, (c) Phase 3 Karpathy-pattern improvements → #248 | ✅ filed (S54) |
| 4 | Mercury | `.mercury/state/upstream-manifest.json` — mark `claude-memory-compiler` as UNLICENSED | ⬜ PR pending |
| 5 | AgentKB | `scripts/flush.py:244-245` — `DETACHED_PROCESS` → `CREATE_NO_WINDOW` (terminal-flash root fix) | ⬜ cross-workspace PR |
| 6 | claude-handoff | `session_chain` DB MVP | ⬜ cross-workspace PR (separate scope) |

## UNVERIFIED flags

- PSM `cleanup_after_days` implementation gap: observed in 4.11.2 cached source; not re-verified in 4.11.6.
- `dispatching-parallel-agents` skill presence in Superpowers `917e5f5` evaluation commit: current `main` has it, original commit unverified.
- Karpathy X post exact date 2026-04-03: community-restated, not verified via X directly.
- claude-mem star count (44k–56k range): WebSearch sources disagree; snapshot only.
- `claude-memory-compiler` contributor count: GitHub /contributors endpoint load failed; inferred from commit history as 1.
