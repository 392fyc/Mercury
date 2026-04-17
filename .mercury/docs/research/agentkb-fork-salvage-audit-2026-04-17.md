# AgentKB Fork Salvage Audit

> **Context**: Mercury #252 mem0 adoption complete (Phases A+B+C merged 2026-04-17). The AgentKB fork `392fyc/claude-memory-compiler` (fork of `coleam00/claude-memory-compiler`) is slated for maintenance-mode / archival. Before archiving, inventory Mercury-specific additions for possible re-use.
>
> **Scope**: 14 fork-only commits, 19 changed files, +2,710 / -341 lines vs upstream `origin/main`.

## Commit inventory (fork/main vs origin/main)

| SHA | Scope | Mercury issue |
|---|---|---|
| b61ff0d | feat: Mercury customizations (timezone, schema, knowledge dirs) | bootstrap |
| 3b3ac22 | diagnose(flush): capture bundled CLI stderr + env vars | #232 |
| a0e1f0b | feat(scripts): rsync one-way mirror to NAS | infra |
| d11e6a5 | Merge branch 'main' | merge |
| 4cb9b78 | fix(flush): strip CLAUDE_CODE_USE_POWERSHELL_TOOL | #232 |
| 8c8af29 | feat: S41-S47 Mercury AgentKB infra updates | infra |
| b8f6f73 | docs: fix AGENTS.md drift | docs |
| 5fd201f | fix(rsync): off-LAN fallback via CF Tunnel | #242 |
| f11358a | feat(phase4-1): re-merge session continuity + flush terminal fix | #238 |
| 7fd15d5 | feat(orchestrator): --visible mode + UTF-8 stdout fix | #241 |
| e10476f | fix(flush): bypass Agent SDK for PreCompact reliability | #232 |
| 436459f | fix(flush): CREATE_NO_WINDOW vs DETACHED_PROCESS | #232 |
| d0ad7d3 | feat(memory/phase-b): wire mem0 ingest into flush | **#252** |
| 4ebf9a7 | test(memory/phase-c): cross-session + telemetry + regression validation | **#252** |

## Re-use classification

### Category 1 — Already migrated to Mercury (safe to retire on AgentKB)

| File | State |
|---|---|
| `scripts/mem0_hooks.py` | Cherry-picked from Mercury 599d313. Mercury is source of truth. |
| `scripts/mem0_bridge.py` | AgentKB-only glue; only valuable if AgentKB hooks stay alive. Retire if archiving. |
| `scripts/mem0_phase_c.py` | AgentKB-only validation. Rebuild path exists in Mercury if needed later. |

### Category 2 — HIGH salvage value (re-use in Mercury before archival)

**`scripts/handoff-orchestrator.py` (267 LOC)**
- Spawns continuation Claude Code session via Agent SDK, writes `session_chain` SQLite entries.
- Already referenced in Mercury `/handoff` skill docs and project state; the runtime script lives in AgentKB.
- **Action**: move into Mercury (`scripts/handoff-orchestrator.py`) since `/handoff` is a Mercury-owned skill. Update `/handoff:auto` to invoke from Mercury path. Drop the AgentKB copy once the Mercury path is live. (Follow-up issue needed.)

**`scripts/skill_stats.py` (195 LOC)**
- Parses Claude Code transcript JSONL, extracts Skill tool invocations, writes to `stats/skill-usage.db`.
- Cross-project telemetry — valuable in Mercury and any other repo, not AgentKB-specific.
- **Action**: move into Mercury (`scripts/skill_stats.py`) or publish as standalone utility. Schema (SQLite: skill, args, session_id, timestamp, project, invocation_seq) is already stable. (Follow-up issue needed.)

**`scripts/flush.py` (392 LOC, of which ~205 lines are Mercury-added fixes)**
- Mercury-added fixes: Agent SDK bypass, CLAUDE_* env sanitization, CREATE_NO_WINDOW, auto-memory checkpoint write, compile trigger gated by `COMPILE_AFTER_HOUR`, mem0_bridge call, terminal-flash fix.
- Tightly coupled to AgentKB's daily-log archive + compile pipeline. Only reuseable if Mercury ever builds its own flush equivalent.
- **Action**: leave in AgentKB for now (coupled). Extract `_find_claude_exe()` + env-sanitization pattern as a Mercury utility later if needed.

### Category 3 — MEDIUM salvage value (repo-specific but reusable pattern)

**`scripts/rsync-agentkb-to-nas.sh` (109 LOC) + `rsync-invoke.ps1` (113 LOC) + `rsync-exclude.list`**
- One-way NAS mirror with off-LAN CF Tunnel fallback, Windows Task Scheduler hourly trigger, single-instance lock, Cygwin/MSYS2 fd-incompat workaround via PowerShell proxy.
- Pattern worth preserving; the scripts themselves are AgentKB-pathed.
- **Action**: copy as templates to Mercury `scripts/` with `${SOURCE_DIR}` parameter and doc the Cygwin quirk + CF Tunnel fallback in Mercury guides. Record `AgentKB/scripts/rsync-*` as the original reference. (Follow-up issue needed.)

**`hooks/session-end.py` (251 LOC) + Mercury additions to `pre-compact.py`**
- Mercury's live integration point for auto-memory. Same design pattern could apply to any Claude Code hook consumer.
- **Action**: pattern documented in `.mercury/docs/guides/mem0-setup.md` already. If Mercury ever replaces AgentKB with a native hook runner, adapt these. Not urgent.

### Category 4 — LOW salvage value (AgentKB-specific / already in Mercury)

| File | Reason |
|---|---|
| `schema/README.md` | AgentKB/Karpathy-KB architecture doc; not relevant once archived. |
| `AGENTS.md` changes | AgentKB identity; Mercury has its own CLAUDE.md. |
| `pyproject.toml` (mem0ai + qdrant-client deps) | Dependency list specific to AgentKB venv. Mercury already has `requirements-mem0.txt`. |
| `scripts/config.py` (small path change) | Trivial. |
| `.gitignore`, `.gitattributes`, `uv.lock` | Repo mechanics, no portable value. |

### Category 5 — OBSOLETED by mem0 migration

| File | Reason |
|---|---|
| `scripts/compile.py` (unchanged from upstream) | Will no longer be the primary knowledge path — mem0 stores atomic facts continuously instead of end-of-day batch compile. Can stay in AgentKB as legacy; Mercury does not need it. |
| `scripts/lint.py`, `scripts/query.py` | Lint/query against daily logs — same obsolescence. Leave in AgentKB for archive-mode reads. |

## Recommended follow-ups (post-archival prep)

Before marking AgentKB fork archived, file Mercury issues for:

1. **Issue: port `handoff-orchestrator.py` to Mercury** — reads `session-checkpoint.md`, writes `session_chain` SQLite, spawns new Claude Code. Referenced by `/handoff:auto` skill. Medium effort, ~1 session.
2. **Issue: port `skill_stats.py` to Mercury** — transcript JSONL → SQLite telemetry. Standalone, reusable across repos. Small effort, ~30 min.
3. **Issue: template-ize `rsync-*nas*` as Mercury `scripts/sync-to-nas.*`** — parameterize `${SOURCE_DIR}`, preserve Cygwin fd-fix + CF Tunnel fallback. Small effort, ~30 min.
4. **Issue: AgentKB fork archival notice** — README banner "archived; see Mercury #252 / mem0 memory layer for replacement". No code change.

## Decision matrix

| Action | When to do |
|---|---|
| Port handoff-orchestrator + skill_stats + rsync templates to Mercury | Before archiving AgentKB. Otherwise hooks/skills that depend on them break. |
| Archive AgentKB fork with README banner | After the three ports land. |
| Remove AgentKB dependencies from Mercury global `settings.json` (hooks) | After Mercury owns `handoff-orchestrator.py` + `flush.py` equivalent OR after accepting that live flush is no longer desired. |
| Update Mercury CLAUDE.md `Related Repositories` table | After archival to reflect maintenance-mode status. |

## Unverified / open questions

- Does Mercury still need file-based daily log archive (via AgentKB `flush.py`) alongside mem0, or is mem0 + git history sufficient? If dropping, the flush chain + compile.py can archive cleanly. If keeping, Mercury must own a flush equivalent OR keep AgentKB fork alive as a runtime dependency.
- `handoff-orchestrator.py` reads `stats/skill-usage.db` path hard-coded to AgentKB dir; on port to Mercury, needs parameterization.
- `rsync-invoke.ps1` assumes scoop-installed cwRsync; Mercury port may want to support multiple install paths.

---

**Bottom line**: three script groups are worth preserving (~470 LOC of handoff orchestrator + skill stats + rsync templates). Everything else is either already-in-Mercury, trivially adaptable, or obsoleted by mem0. Proposal: land three small Mercury PRs (ports) over the next session or two, then flip AgentKB fork to archived-with-banner status.

---

## Addendum 2026-04-17 — Destination check for each salvageable item

After user-supplied context review, each salvage target gets re-homed rather than all landing in Mercury proper.

### 1. `handoff-orchestrator.py` → **`claude-handoff` plugin repo** (not Mercury)

- Verified: `github.com/392fyc/claude-handoff` already exists as a dedicated plugin repo with:
  - `hooks/` containing `session-start.py` + `hooks.json` (no `handoff-orchestrator.py` yet)
  - `session_chain/` containing `__init__.py` + `db.py` + `tests/` (the SQLite layer already ported here)
  - `skills/handoff/`
- **Action**: port `scripts/handoff-orchestrator.py` from AgentKB into `claude-handoff/hooks/` or a new `claude-handoff/orchestrator/` directory. Coupling is natural — the orchestrator already imports `session_chain.db`, which lives in that repo.
- Mercury then only consumes the plugin, never owns the orchestrator code itself.

### 2. `skill_stats.py` → **user-level** (`~/.claude/`), not project-level

- Verified: `~/.claude/` has no `skill_stats*` / `skill-usage.db`. Current hook registration in `~/.claude/settings.json` runs the AgentKB-pathed script, scoping writes to a Mercury-only DB.
- **Action**: move `skill_stats.py` + its SQLite to `~/.claude/scripts/skill_stats.py` + `~/.claude/stats/skill-usage.db`. Hook registration stays in the user-level `settings.json` (already the right scope) — just re-point the command path. Cross-project analytics come for free once the AgentKB prefix is gone.
- Open question: is the DB value-worth-preserving? Current rows are Mercury-only and arguably disposable. If so, archive the file and start fresh at user-level.

### 3. `rsync-*` (mem2nas) → **re-target to mem0-state**, keep in AgentKB until archival, then move

- Current rsync mirrors the entire AgentKB tree. Post-mem0 adoption, the value-bearing payload is:
  - `AgentKB/scripts/mem0-state/qdrant/` (vector store)
  - `AgentKB/scripts/mem0-state/history.db` (SQLite history)
- **Action**: parameterize via `SOURCE_DIR` env var and move the scripts into wherever owns the hooks (likely `~/.claude/scripts/`). Target NAS path stays the same. Cygwin/MSYS2 fd-fix + CF Tunnel off-LAN fallback remain intact.

### 4. `session-end.py` / `pre-compact.py` — **user-level `~/.claude/hooks/`**, not Mercury-level

- Verified: `~/.claude/settings.json` registers `SessionEnd` + `PreCompact` with commands pointing at `$AGENTKB_DIR/hooks/`. When AgentKB archives, these commands BREAK silently unless re-homed first.
- **Action**: move both hook scripts + `flush.py` + `mem0_hooks.py` + `mem0_bridge.py` to `~/.claude/hooks/` (or `~/.claude/scripts/`). Update `settings.json` command paths from `$AGENTKB_DIR/...` to the new user-level locations.
- This makes the hooks repo-agnostic (they no longer need an AgentKB checkout) and removes the AgentKB runtime dependency entirely.
- Mercury CLAUDE.md `Related Repositories` table should drop the `$AGENTKB_DIR` row once this is done.

### 5. `claude-mem` as alternative daily-log source — **REJECT**

- Verified via web 2026-04-17: `claude-mem` (thedotmack) captures to `memory/auto-capture/YYYY-MM-DD.md` with its own hybrid SQLite+Chroma pipeline and Bun HTTP worker on `localhost:37777`.
- Research #250 already evaluated and REJECTED claude-mem (AGPL + solo maintainer + 10-version-in-10-days churn + no clean mount path). Running it alongside mem0 would double-ingest session content and add a second runtime (Bun) for no benefit.
- **Action**: no adoption. Daily-log archiving remains with AgentKB `flush.py` path until a lighter in-Mercury / in-claude-handoff alternative is needed. If the file-archive dimension is still desired after re-homing hooks (step 4), keep the `append_to_daily_log` branch in the ported `flush.py` writing to `~/.claude/daily/` or similar — no external dependency needed.

## Revised recommended follow-ups

File these issues before archival banner:

1. **Port `handoff-orchestrator.py` into `claude-handoff` repo** — natural home, `session_chain/db.py` already sits there. Medium effort.
2. **Promote `skill_stats.py` to user-level (`~/.claude/`)** — cross-project analytics. Small effort.
3. **Re-home `session-end.py` + `pre-compact.py` + `flush.py` + `mem0_hooks.py` + `mem0_bridge.py` to user-level `~/.claude/hooks/` + `~/.claude/scripts/`**, repoint `~/.claude/settings.json`. Medium effort — this is the critical path for removing AgentKB as a runtime dependency.
4. **Parameterize and move `rsync-*` (mem2nas)** — new target is `~/.claude/scripts/mem0-state/` after step 3 completes. Small effort.
5. **Only AFTER steps 3+4**: archive AgentKB fork with README banner.

## Critical path dependency

Steps 1, 3, and 4 must land before AgentKB can be archived without breaking live workflow. Step 2 and step 5 (claude-mem) are independent.
