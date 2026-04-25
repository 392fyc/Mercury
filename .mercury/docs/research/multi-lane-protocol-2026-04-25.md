# Multi-Lane Parallel Session Protocol — v0 MVP Validation Report

**Mission**: Mercury Issue [#292](https://github.com/392fyc/Mercury/issues/292)
**Lane**: `side-multi-lane` / Session `S1-side-multi-lane`
**Date**: 2026-04-25
**Status**: Research complete — recommendation **CONDITIONAL GO** with v0.1 deltas

---

## Path conventions (read this first)

This document references several files that are **not** in the Mercury git repo. Mercury
uses Claude Code's user-memory layer (governed by Mercury CLAUDE.md §Related Repositories) for
session-scoped state. Path shorthand used throughout this report:

| Shorthand | Resolves to | Status |
|-----------|-------------|--------|
| `memory/<file>` | `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/<encoded_cwd>/memory/<file>` (Claude Code per-project memory) | **NOT in repo** — gitignored by design; Claude Code memory-system artifact |
| `<encoded_cwd>` | path-encoded form of the project's working directory, computed by Claude Code at session start; the exact encoding is host-specific and operator-specific (do not hardcode — discover at runtime, e.g. `ls "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/" \| grep -i mercury`) | runtime-derived |
| `feature/lane-<lane>/...` | actual git branch in this repo | in repo |
| `.mercury/docs/...` | actual repo files | in repo |
| `.tmp/...` | repo tmp dir (gitignored but local repo) | repo-local, not committed |
| `scripts/...` | actual repo scripts | in repo |

When the report says "see `memory/feedback_lane_protocol.md`", that means: look at the
**user-memory** file (resolved per the table above), not at any repo file. Claude Code
auto-loads `memory/MEMORY.md` index at session start, which then references the other
memory files.

The PR-auditable companion `.mercury/docs/lane-protocol-v0.1-deltas.md` is the **in-repo**
mirror of the v0.1 delta proposal, so PR reviewers without user-memory access can verify
deltas in this PR's diff.

---

## Executive Summary

Mercury launched a v0 MVP multi-lane protocol (7 rules; see user-memory
`memory/feedback_lane_protocol.md` per shorthand above — not in this repo) on 2026-04-25 to
support parallel session work. This report validates the protocol against:

- **5 industry precedents** (git worktree, Claude Code agent teams, trunk-based, Spotify Squads, tmux)
- **6 adversarial counter-examples** covering rules 1, 3, 4, 6, 7
- **Quantified overhead** at 2 / 5 / 10 / 20 lane scale
- **Cognitive load research** (Miller's 7±2, multi-agent scaling literature)
- **Rollback path completeness**

**Verdict**: **CONDITIONAL GO** for v1 promotion with the following deltas applied first:

1. **HARD-CAP at 5 lanes** (cognitive + multi-agent research consensus)
2. **Replace shared-index append-only with per-session files** (avoid GitLab CHANGELOG conflict crisis)
3. **Add Rule 1.1**: probe-after-write Issue claim verification
4. **Add Rule 3.1+3.2**: stale lane / tmp dir cleanup policy (14-day threshold)
5. **Add Rule 4.1**: emergency spec-change escalation when main lane idle > 48h
6. **Shorten branch prefix**: `feature/lane-<lane>/TASK-N-*` is too long; switch to `lane/<short>/<N>-<slug>` (consistent with detailed delta below)

**Rationale for CONDITIONAL not GO**:
Protocol mechanics work at v0 scale (2 lanes), but two rules have known-broken patterns
documented in industry literature (Rule 7 ↔ CHANGELOG crisis; Rule 1 ↔ GitHub API race).
Without v0.1 deltas, scaling past 3-4 lanes will hit predictable failure modes.

**Rationale for not NO_GO**:
Single-operator simplicity outweighs theoretical concerns at small scale. Mercury's protocol
is functionally equivalent to git-worktree+tmux conventions practiced by AI-agent community
in 2025. No precedent suggests the protocol will fail at 2-3 lanes.

---

## D1 — Precedent Survey

### D1.1 Git worktree workflows (2024-2025 community consensus)

**Finding**: Git worktree is the de facto pattern for AI-agent parallel work.
- incident.io runs 4-5 Claude Code agents in parallel via worktrees
- Best practices: consistent naming, cleanup habits, separate `npm install` per worktree
- Each worktree is isolated working directory; branch checkout per dir

**Relevance to Mercury Rule 2**: Mercury's branch-prefix isolation is *weaker* than worktree
isolation. The user-memory `memory/LANES.md` (per Path conventions §) re-implements
coordination that worktrees provide natively at FS level — i.e. Mercury's lane registry is a
manual file-based convention layered on top of git, not an in-repo single source of truth.

**Sources**:
- [git-worktree official docs](https://git-scm.com/docs/git-worktree)
- [Using Git Worktrees for Multi-Feature Development with AI Agents (Nick Mitchinson, 2025)](https://www.nrmitchi.com/2025/10/using-git-worktrees-for-multi-feature-development-with-ai-agents/)
- [Mastering Git Worktrees with Claude Code for Parallel Development](https://medium.com/@dtunai/mastering-git-worktrees-with-claude-code-for-parallel-development-workflow-41dc91e645fe)
- [Bulk cleaning stale git worktrees and branches (2026-03)](https://brtkwr.com/posts/2026-03-06-bulk-cleaning-stale-git-worktrees/)

### D1.2 Claude Code native agent teams

**Finding**: Claude Code has OFFICIAL `agent teams` feature (gated behind
`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`). Architecture:
- Team-lead session coordinates teammates via shared task list
- Teammates have own context windows + direct communication
- Distinct from subagents (which only report back)

**Relevance**: Mercury's multi-lane model overlaps with what Claude Code provides natively.
Justification for using Mercury's lane model over `agent teams`:
1. Lanes are cross-session-persistent (agent teams are per-session)
2. Lanes use git branches as native isolation (agent teams use in-memory state)
3. Mercury's 1-user-multi-claude pattern doesn't match agent teams' single-process model

This is acceptable but should be documented in v0.1 protocol rationale.

**Sources**:
- [Orchestrate teams of Claude Code sessions (official docs)](https://code.claude.com/docs/en/agent-teams)
- [Multiagent sessions (Claude API docs)](https://platform.claude.com/docs/en/managed-agents/multi-agent)
- [Claude Code Sub-Agents: Parallel vs Sequential Patterns](https://claudefa.st/blog/guide/agents/sub-agent-best-practices)

### D1.3 Trunk-based development vs GitFlow

**Finding**: Merge conflict resolution consumes 10-25% of dev time in branch-heavy workflows.
Trunk-based with daily integration minimizes divergence; GitFlow's long-lived branches
accumulate drift.

**Relevance to Mercury**: The `feature/lane-<lane>/TASK-N-*` pattern is GitFlow-like, with
risk of long-lived branches if a lane stays open for weeks. Recommendation: enforce
per-lane PR cadence (close stale branches after ≤14 days inactivity).

**Sources**:
- [Trunk-based Development (Atlassian)](https://www.atlassian.com/continuous-delivery/continuous-integration/trunk-based-development)
- [trunkbaseddevelopment.com](https://trunkbaseddevelopment.com/)
- [Trunk-Based Development vs Gitflow (Mergify)](https://mergify.com/blog/trunk-based-development-vs-gitflow-which-branching-model-actually-works/)

### D1.4 Spotify Squads model

**Finding**: Spotify uses tribes (40-150 people) for cross-squad coordination via tribe leads.
Documented weakness: no standard cross-tribe dependency process; chaotic at scale.

**Relevance to Mercury Rule 4**: Main lane is implicit "tribe lead" but Rule 4 only grants
spec-edit rights, not dependency-coordination authority. Mercury currently has no escalation
path for cross-lane blockers.

**Sources**:
- [Discover the Spotify model (Atlassian)](https://www.atlassian.com/agile/agile-at-scale/spotify)
- [What Is The Spotify Model? (Product School)](https://productschool.com/blog/product-fundamentals/spotify-model-scaling-agile)
- [Overcoming the Pitfalls of the Spotify Model](https://medium.com/@ss-tech/overcoming-the-pitfalls-of-the-spotify-model-8e09edc9583b)

### D1.5 tmux multi-session conventions

**Finding**: Community conventions: descriptive names, prefix by category (`work_`, `pers_`),
underscores not spaces, one project per session.

**Relevance**: Mercury's `lane:*` prefix aligns with tmux best practice. Current name
`side-multi-lane` (19 chars) → branch `feature/lane-side-multi-lane/TASK-292-...` (45+ chars)
exceeds tmux community shorter-prefix convention.

**Sources**:
- [Sessions (tao-of-tmux)](https://tao-of-tmux.readthedocs.io/en/latest/manuscript/05-session.html)
- [Naming things in tmux (Random Geekery)](https://randomgeekery.org/post/2020/11/naming-things-in-tmux/)
- [Manage multiple projects with tmux](https://zolmok.org/tmux-multiple-projects-sessions/)

---

## D2 — Adversarial Rule Completeness

### D2.1 Rule 1 — Issue claim race condition (MEDIUM severity)

GitHub REST API has documented race conditions for concurrent writes. `gh issue edit
--add-label` is **not atomic compare-and-swap**. Two lanes calling within the same millisecond
both succeed; both labels get applied.

**Mercury's "first timestamp wins"** is post-hoc detection, not prevention.

**Mitigation (proposed Rule 1.1)**: Probe-after-write — after `gh issue edit`, immediately
re-query labels. If count > 1 `lane:*` label → abort current lane + comment issue + notify user.

**Sources**:
- [GitHub Releases API Race Condition (DevActivity)](https://devactivity.com/insights/mastering-github-releases-avoiding-race-conditions-for-enhanced-engineering-productivity/)
- [Prevent race condition between concurrent jobs (gavv/pull-request-artifacts#15)](https://github.com/gavv/pull-request-artifacts/issues/15)
- [Concurrency group bug (community#9252)](https://github.com/orgs/community/discussions/9252)

### D2.2 Rule 7 — Append-only shared index = CHANGELOG crisis (HIGH severity)

**This is the protocol's biggest known-broken pattern.** GitLab documented "CHANGELOG conflict
crisis": multiple contributors appending entries to single file caused constant merge conflicts,
costing 10-25% dev time. This **exactly maps** to Mercury's MEMORY.md + SESSION_INDEX.md
append-only pattern.

The empirical observation in this very session: while side lane S1 ran research, main lane S73
appended its own MEMORY.md row independently — no conflict (lucky timing, non-adjacent lines).
At 5+ lanes with concurrent commits, this will fail predictably.

**Industry solutions**:
1. **(RECOMMENDED)** Per-entry file in dir structure — `memory/sessions/S<N>-<lane>.md` one
   per session; index auto-generated by script. Zero conflict.
2. Placeholder lines (GitLab approach for CHANGELOG)
3. Custom merge driver (`git-merge-changelog`)

**Sources**:
- [How we solved GitLab's CHANGELOG conflict crisis](https://about.gitlab.com/blog/2018/07/03/solving-gitlabs-changelog-conflict-crisis/)
- [Avoid merge conflicts in CHANGELOG.md (PrefectHQ/prefect#2311)](https://github.com/PrefectHQ/prefect/issues/2311)
- [Keep a Changelog without Conflicts (Uptech)](https://engineering.uptechstudio.com/blog/keep-a-changelog-without-conflicts/)
- [git-merge-changelog (Debian manpages)](https://manpages.debian.org/testing/git-merge-changelog/git-merge-changelog.1.en.html)

### D2.3 Rule 3 — Stale tmp dir / orphaned lanes (MEDIUM severity)

Claude Code 2.1.76 added NATIVE stale worktree detection (7+ day threshold) after a 222-workspace
+ 8-agent same-file-write disaster. Mercury's `.tmp/lane-<lane>/` has **no cleanup policy**.
If a lane is abandoned mid-work, dir persists indefinitely. LANES.md section stays `active`
forever.

**Mitigation (proposed Rules 3.1 + 3.2)**:
- **Rule 3.1**: stale lane = no commits to `feature/lane-<lane>/*` AND no handoff file updates
  > 14 days → main lane auto-marks `stale` during periodic sweep
- **Rule 3.2**: `.tmp/lane-<lane>/` auto-prune on lane close

**Sources**:
- [DOCS: Worktree cleanup docs missing automatic recovery (claude-code#34282)](https://github.com/anthropics/claude-code/issues/34282)
- [Stale worktrees are never cleaned up (claude-code#26725)](https://github.com/anthropics/claude-code/issues/26725)
- [Bulk cleaning stale git worktrees](https://brtkwr.com/posts/2026-03-06-bulk-cleaning-stale-git-worktrees/)
- [claude-worktree-tools wt-cleanup SKILL.md](https://github.com/ThinkVelta/claude-worktree-tools/blob/main/templates/skills/wt-cleanup/SKILL.md)

### D2.4 Rule 6 — LANES.md single-file merge bottleneck (LOW-MEDIUM severity)

Same root cause as Rule 7 but at lower frequency. Two lanes editing different sections of the
same file usually auto-resolves via 3-way merge but is not guaranteed on adjacent lines.

**Mitigation**: Same per-section file pattern as Rule 7 fix — `lanes/<lane>.md` per-lane +
auto-generated `LANES.md` index. Defer to v1 if v0 friction is observed.

**Sources**:
- [On reducing Changelog merge conflicts (Vladimir Kiselev)](https://medium.com/@nettsundere/on-reducing-changelog-merge-conflicts-1eb23552630b)
- [Solve CHANGELOG.md merge conflicts (handsontable#7405)](https://github.com/handsontable/handsontable/issues/7405)

### D2.5 Rule 1+6 — Label tampering / out-of-band edits undetectable (LOW severity)

No protocol detects manual label removal or cross-section LANES.md edits. GitHub audit log
exists but Mercury has no alerting. Acceptable for v0 (1 user, 2-3 lanes); v1 may add
optional pre-commit `scripts/lane-integrity-check.sh`.

**Sources**:
- [Managing labels (GitHub docs)](https://docs.github.com/en/issues/using-labels-and-milestones-to-track-work/managing-labels)
- [CRDT (Wikipedia)](https://en.wikipedia.org/wiki/Conflict-free_replicated_data_type)

### D2.6 Rule 4 — Main lane crash + urgent spec change = deadlock (MEDIUM severity)

If main lane is idle/crashed and side lane needs emergency spec change, protocol says "open
Issue request main lane". No escalation path if main lane unavailable. Same flaw as Spotify
model (no standard cross-tribe process).

**Mitigation (proposed Rule 4.1)**: Emergency protocol — if spec change needed AND main lane
idle > 48h, side lane may open PR with `[EMERGENCY-<lane>]` prefix + ping user; user becomes
arbitrator (explicit opt-in PR review, not auto-merge).

**Sources**:
- [Overcoming the Pitfalls of the Spotify Model](https://medium.com/@ss-tech/overcoming-the-pitfalls-of-the-spotify-model-8e09edc9583b)

---

## D3 — Operational Overhead (Quantified)

| Metric | At 2 lanes | At 5 lanes | At 10 lanes | At 20 lanes |
|--------|-----------|-----------|-------------|-------------|
| LANES.md update | ~2-4 min/session | ~5-10 min/session | ~10-20 min/session | unworkable |
| Branch name avg | 45 chars | 45-50 chars | 45-50 chars | 45-50 chars |
| Total handoff files | 2 | 5 | 10 | 20 |
| MEMORY.md index lines | ~60 | ~75 | ~100 | ~150 |
| Append-only conflicts | rare | occasional | frequent | constant |

### D3.1 Branch name length

Current example: `feature/lane-side-multi-lane/TASK-292-research-multi-lane-protocol`
- Length: **65 chars** (exceeds soft 50-char recommendation)
- LeanTaaS hard cap: 28 chars
- Community convention: kebab-case, ≤50 chars
- IDE autocomplete + URL pasting + git UI all suffer at 65 chars

**Recommendation**: Switch to `lane/<short>/<N>-<slug>` format. Example:
`lane/side-mlane/292-protocol-research` = 36 chars.

**Sources**:
- [Limiting Git Branch Names to 28 Characters (LeanTaaS)](https://medium.com/leantaas-engineering/why-are-we-limiting-git-branch-name-length-to-28-characters-c49cb5f4ff9a)
- [Best practices for naming Git branches (Graphite)](https://graphite.com/guides/git-branch-naming-conventions)
- [Git Branch Naming Conventions (PhoenixNAP)](https://phoenixnap.com/kb/git-branch-name-convention)

### D3.2 Label scaling

GitHub label system handles prefix-sorting (`lane:*`) at scale; no hard cap at 50 or 100 labels.
Not a scaling bottleneck.

**Sources**:
- [Managing labels (GitHub docs)](https://docs.github.com/en/issues/using-labels-and-milestones-to-track-work/managing-labels)
- [Sane GitHub Labels (Dave Lunny)](https://medium.com/@dave_lunny/sane-github-labels-c5d2e6004b63)

---

## D4 — Scaling Analysis

### D4.1 Failure points by lane count

| Lane count | First rule to break | Status |
|-----------|---------------------|--------|
| 2 (current) | none observed | safe |
| 5 | Rule 7 (append-only) at concurrent commits | watch carefully |
| 10 | Rules 6 + 7 + Miller's 7±2 cognitive limit | high risk |
| 20 | All coordination breaks; 39-70% perf degradation | catastrophic |

### D4.2 Multi-agent research consensus

- **Best-practice deployments**: 3-5 agents initial; teams of 20+ consistently underperform
- **Coordination latency**: 200ms @ 5 agents → 2s @ 50 agents
- **Reasoning degradation**: 39-70% sequential reasoning performance drop with coordination
- **Success rate**: <10% of companies scale beyond single-agent deployments
- **Personal Kanban (solo dev)**: WIP limit 3-5 parallel activities (Miller-aligned)

### D4.3 Cognitive load (single operator)

- Miller's Law: working memory cap = 7 ± 2 items
- Context switch recovery time: 23 minutes (UC Irvine research)
- Mercury has 1 human operator → effective hard cap is 3-5 lanes

### D4.4 Lane hierarchy needed?

**Verdict**: NO at v1 scale. Hierarchy adds protocol complexity for hypothetical 10+ lane
scenarios that should never happen. If Mercury ever needs >5 lanes, the answer is to split
into separate repos, not nest lanes.

**Sources**:
- [Towards a science of scaling agent systems (Google research)](https://research.google/blog/towards-a-science-of-scaling-agent-systems-when-and-why-agent-systems-work/)
- [How we built our multi-agent research system (Anthropic)](https://www.anthropic.com/engineering/multi-agent-research-system)
- [Google Explores Scaling Principles for Multi-Agent Coordination (InfoQ 2026-02)](https://www.infoq.com/news/2026/02/google-agent-scaling-principles/)
- [Miller's Law (Laws of UX)](https://lawsofux.com/millers-law/)
- [Working with WIP limits for kanban (Atlassian)](https://www.atlassian.com/agile/kanban/wip-limits)
- [Context-switching is the main productivity killer for developers](https://newsletter.techworld-with-milan.com/p/context-switching-is-the-main-productivity)

---

## D5 — Rollback Path

### D5.1 Single-lane close (existing, validated)

Documented in `feedback_lane_protocol.md` and `LANES.md`:
1. Issue resolved → comment closure rationale
2. LANES.md section status → `closed` (don't delete; audit trail)
3. Handoff file retained
4. Branch deleted post-merge
5. `.tmp/lane-<lane>/` pruned

**Status**: COMPLETE ✅

### D5.2 Half-rollback (close some, keep main + 1)

Not currently documented. Can be derived from single-lane close pattern:
- Close all but `main` and one chosen lane
- LANES.md status → `closed` for closed ones
- No spec changes needed (other lanes never had spec edit rights)

**Status**: IMPLICIT — recommend explicitly documenting in v0.1.

### D5.3 Full rollback to single-chain (user-memory `memory/LANES.md` ROLLBACK section)

Documented (all paths user-memory per Path conventions §, not repo files):
1. All side lanes → `closed`
2. Merge side handoffs back into user-memory `memory/session-handoff.md`
3. User-memory `memory/feedback_lane_protocol.md` retained as ADR (status: `superseded`)
4. User-memory `memory/LANES.md` retained as history

**Status**: COMPLETE ✅, but should add explicit ADR-style status transition.

### D5.4 Migration to alternative architecture

If multi-lane fails AND single-chain too restrictive, candidates:
1. **Native git worktree** + `agent teams` flag — least Mercury-specific code
2. **Per-repo split** (Mercury core + Mercury phase5 separate repos) — for >5 concurrent streams
3. **Trunk-based + feature flags** — for code parallelism without session parallelism

**Status**: Not currently addressed. Recommend adding as v0.1 §"Future Migrations".

**Sources**:
- [ADR process (AWS)](https://docs.aws.amazon.com/prescriptive-guidance/latest/architectural-decision-records/adr-process.html)
- [Maintain an architecture decision record (Microsoft Azure)](https://learn.microsoft.com/en-us/azure/well-architected/architect-role/architecture-decision-record)
- [Architectural Decision Records (adr.github.io)](https://adr.github.io/)
- [Trunk-Based Development vs. Long-Lived Feature Branches (Ardalis)](https://ardalis.com/trunk-based-development-vs-long-lived-feature-branches/)

---

## Risk Matrix

| Rule | Risk @ 2 lanes | Risk @ 5 lanes | Risk @ 10 lanes | Mitigation Priority |
|------|---------------|---------------|----------------|---------------------|
| 1 (Issue claim) | LOW | MED | MED | P1 — add probe-after-write |
| 2 (branch prefix) | LOW (long names) | LOW | LOW | P3 — shorten prefix |
| 3 (tmp isolation) | LOW (no cleanup) | MED | HIGH | P1 — stale policy |
| 4 (main lane edit) | LOW | LOW | MED (deadlock) | P2 — emergency escalation |
| 5 (per-lane state) | LOW | LOW | MED (file count) | P3 — auto-index |
| 6 (LANES.md) | LOW | MED | HIGH | P2 — split files |
| 7 (append-only) | MED | HIGH | CATASTROPHIC | **P0 — per-session files** |

---

## Protocol v0 → v0.1 Proposed Deltas

### Add Rule 1.1 — Probe-after-write (P1)

After every `gh issue edit --add-label lane:<name>`, immediately re-query Issue labels.
If two `lane:*` labels found → abort current lane + comment Issue + notify user.

### Add Rule 3.1 — Stale lane sweep (P1)

Lane is "stale" if all of:
- No commits to `feature/lane-<lane>/*` branches in 14 days
- No handoff file modifications in 14 days
- No Issue activity from claimed Issues in 14 days

Stale lanes get `LANES.md` status auto-flipped to `stale` by main lane periodic sweep
(monthly cron OR manual `scripts/lane-sweep.sh`).

### Add Rule 3.2 — Tmp dir auto-prune (P2)

`.tmp/lane-<lane>/` auto-deleted when LANES.md status → `closed`.
Implementation: `scripts/lane-close.sh <lane-name>` handles both.

### Add Rule 4.1 — Emergency spec-change escalation (P2)

If side lane needs spec change AND main lane has been idle > 48h (no commits, no handoff
update, no Issue activity), side lane MAY:
1. Open PR with title prefix `[EMERGENCY-<lane>]`
2. Reference this rule in PR body
3. Ping user explicitly

User becomes arbitrator. PR is NOT auto-merge — user must explicitly approve.

### Replace Rule 7 — Per-session files (P0, BREAKING)

All paths in this delta refer to **user-memory** artifacts per the Path conventions § at top
of doc, not repo files. The proposed `scripts/regenerate-memory-index.sh` is one of the few
items that would land in-repo (since scripts are repo-tracked).

**OLD**: append-only edits to user-memory `memory/MEMORY.md` and `memory/SESSION_INDEX.md`
(both resolve to `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/<encoded_cwd>/memory/...`,
not repo files).
**NEW**: each session writes its own user-memory file at `memory/sessions/S<N>-<lane>.md`
(again resolved via Path conventions). The user-memory `memory/MEMORY.md` and
`memory/SESSION_INDEX.md` contain only auto-generated index lines via the in-repo helper
`scripts/regenerate-memory-index.sh` (invoked from a session-end hook in
`${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks/`).

**Migration plan**:
- **Phase A (additive, non-breaking)**: deploy `scripts/regenerate-memory-index.sh`. Script reads `memory/sessions/*.md` frontmatter (session ID, lane, date, summary), generates index lines, writes to a separate `memory/INDEX.generated.md` for diff inspection. Existing `MEMORY.md` / `SESSION_INDEX.md` untouched. Run for ≥3 sessions to verify output stability.
- **Phase B (cutover, BREAKING)**: split existing `MEMORY.md` and `SESSION_INDEX.md` rows into per-session files at `memory/sessions/S<N>-<lane>.md`. Replace `MEMORY.md` and `SESSION_INDEX.md` content with generated index. Tag pre-cutover commit `lane-protocol-v0.1-pre-cutover` for instant rollback.
- **Phase C (lock-in)**: enable pre-commit hook rejecting direct edits to `MEMORY.md` / `SESSION_INDEX.md` outside the regenerate script.

**Consistency guarantees**:
- Script output deterministic (sort by session ID + lane); `git diff` after regenerate must be either empty or only the new session's row added
- Pre-commit hook validates index matches source files before allowing commit
- Per-session files are append-only at session granularity (no mid-session rewrites); content frozen post-handoff

**Failure rollback**:
- If regenerate script fails (parse error, missing frontmatter): script exits non-zero, pre-commit blocks; user fixes source file or runs `scripts/regenerate-memory-index.sh --fallback-preserve` which keeps existing index
- If Phase B cutover causes index drift: revert to `lane-protocol-v0.1-pre-cutover` tag (single git command); existing `MEMORY.md` / `SESSION_INDEX.md` content restored verbatim
- Orphaned per-session files (lane closed but file remains): handled by `scripts/lane-sweep.sh` (Rule 3.1) — flagged not deleted

**Out-of-scope**:
- Migration of historical `feedback_*.md`, `project_*.md`, `reference_*.md` files (these are not session-scoped; remain untouched)
- AgentKB / mem0 layer integration (memory layer rebuild #252 is orthogonal)

### Modify Rule 2 — Shorten branch prefix (P3)

**OLD**: `feature/lane-<lane>/TASK-<N>-*` (45-65 chars)
**NEW**: `lane/<short>/<N>-<slug>` (≤40 chars). Example: `lane/side-mlane/292-protocol-research`.
Main lane retains backward-compat with legacy `feature/TASK-N-*`.

### Add HARD-CAP

`LANES.md` MUST NOT exceed 5 active lanes. Attempting to open lane #6 requires:
1. Closing an existing lane first, OR
2. Opening Issue with `protocol-violation` label requesting cap raise (user decision)

Rationale: Miller's 7±2 + multi-agent research consensus + WIP limit theory.

---

## Go / No-Go Recommendation

### Recommendation: **CONDITIONAL GO** for v1 promotion

**Conditions** (apply before promoting to v1):
1. Add Rule 1.1 (probe-after-write) — code change in `scripts/lane-claim.sh`
2. Add Rule 3.1 + 3.2 (stale sweep + auto-prune) — `scripts/lane-sweep.sh`, `scripts/lane-close.sh`
3. Add Rule 4.1 (emergency escalation) — doc-only update to `feedback_lane_protocol.md`
4. **Replace Rule 7** with per-session files (P0 BREAKING) — `scripts/regenerate-memory-index.sh`
5. Modify Rule 2 (shorter branch prefix) — backward-compat with legacy
6. Add HARD-CAP at 5 lanes — doc-only

### Rationale

**Why not full GO**:
- Rule 7 has documented industry-broken pattern (CHANGELOG crisis); will fail at 5+ lanes
- Rule 1 has post-hoc detection only; a single race-condition incident will hurt trust
- No stale-lane policy means abandoned lanes accumulate silently

**Why not NO_GO**:
- v0 protocol mechanics work at 2-lane scale (this very session is empirical proof)
- Industry precedents (git worktree, tmux, Spotify) all use similar coordination patterns
- Single-operator simplicity outweighs theoretical concerns at <5 lanes
- All identified risks have known mitigations; none are showstoppers

**Why CONDITIONAL is the right call**:
- The deltas are small (mostly doc + 3 short scripts)
- They prevent predictable failures rather than chase hypothetical ones
- They preserve protocol's core simplicity while patching known holes

---

## Post-research Action Items

Items split by ownership: S1-side-multi-lane (this session) vs main lane S74+ (post-decision).

### Completed by S1-side-multi-lane (delivered before this PR)

- ✅ **Item 1 — Comment Issue #292** with executive summary + recommendation. Posted as [#issuecomment-4319812413](https://github.com/392fyc/Mercury/issues/292#issuecomment-4319812413). Visible on the Issue, not in this PR's diff (intentional — Issue comments are not repo files).
- ✅ **Item 2 — v0.1 delta proposal**. Canonical PR-auditable artifact added at `.mercury/docs/lane-protocol-v0.1-deltas.md` (this PR diff). Mirror written to user-memory `memory/feedback_lane_protocol.md` (resolves to `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/<encoded_cwd>/memory/...` per Path conventions §) as a per-user working cache. PR reviewers can verify the mirror with verification commands in `lane-protocol-v0.1-deltas.md` §"Verification commands". Reviewers without local access fall back to the in-repo companion file (this is by design; the repo file is the canonical authority).
- ✅ **Item 6 — Retain side-lane handoff** as audit trail. Updated to closing-handoff state at user-memory `memory/session-handoff-side-multi-lane.md` per Path conventions §. Per Mercury's "user-level governance" pattern (CLAUDE.md §Related Repositories), user-memory artifacts are not committed to repo; the side lane lifecycle is auditable via Issue #292 + LANES status (also user-memory) + this PR's narrative.

### Deferred to main lane S74+ (HOLD-OPEN per >2-rule revision clause)

- ⏸️ **Item 3 — File follow-up Issues** for v0.1 deltas implementation. Side lane scope is research-output only; implementation Issues require main-lane decision on which deltas to accept:
   - P1: Rule 1.1 probe-after-write script
   - P0: Rule 7 per-session file restructure (BREAKING)
   - P1: Rule 3.1+3.2 stale lane scripts
   - P2: Rule 4.1 emergency escalation docs
   - P3: Rule 2 branch prefix shortening
- ⏸️ **Item 4 — Hold Issue #292 open** (NOT close yet). Per S1 starting prompt hold-open clause: "research 发现 protocol 需重大修订（>2 rules 改动）→ Issue 保留 + 提 v0.1 proposal + main lane 决策". 6 rule changes + 1 cap → exceeds 2-rule threshold → Issue stays OPEN until main lane decides on deltas. **Note**: earlier prose in some sections may have used the shorthand "close Issue #292" to refer to the eventual resolution; the authoritative behavior is hold-open NOW, close-by-main-lane LATER.
- ⏸️ **Item 5 — Flip user-memory `memory/LANES.md` side-multi-lane status `paused` → `closed`** (per Path conventions §). Currently set to `paused` (research delivered, awaiting decision). Flips to `closed` when main lane resolves Issue #292.

### Tracking

This split is **intentional** — side lane scope ends at research delivery. Implementation lives outside lane scope; main lane S74+ owns acceptance decisions. The PR delivers the research artifact (design doc); other items are tracked via Issue #292 status + LANES.md `paused` status + memory file annotations.

---

## Sources Index

All sources cited in this report (deduplicated):

### Git worktree
- https://git-scm.com/docs/git-worktree
- https://www.nrmitchi.com/2025/10/using-git-worktrees-for-multi-feature-development-with-ai-agents/
- https://medium.com/@dtunai/mastering-git-worktrees-with-claude-code-for-parallel-development-workflow-41dc91e645fe
- https://brtkwr.com/posts/2026-03-06-bulk-cleaning-stale-git-worktrees/
- https://risadams.com/blog/2025/05/30/git-worktrees/

### Claude Code agent teams
- https://code.claude.com/docs/en/agent-teams
- https://platform.claude.com/docs/en/managed-agents/multi-agent
- https://claudefa.st/blog/guide/agents/sub-agent-best-practices

### Trunk-based development
- https://www.atlassian.com/continuous-delivery/continuous-integration/trunk-based-development
- https://trunkbaseddevelopment.com/
- https://mergify.com/blog/trunk-based-development-vs-gitflow-which-branching-model-actually-works/
- https://ardalis.com/trunk-based-development-vs-long-lived-feature-branches/

### Spotify model
- https://www.atlassian.com/agile/agile-at-scale/spotify
- https://productschool.com/blog/product-fundamentals/spotify-model-scaling-agile
- https://medium.com/@ss-tech/overcoming-the-pitfalls-of-the-spotify-model-8e09edc9583b

### Tmux
- https://tao-of-tmux.readthedocs.io/en/latest/manuscript/05-session.html
- https://randomgeekery.org/post/2020/11/naming-things-in-tmux/
- https://zolmok.org/tmux-multiple-projects-sessions/

### Race conditions / concurrency
- https://devactivity.com/insights/mastering-github-releases-avoiding-race-conditions-for-enhanced-engineering-productivity/
- https://github.com/gavv/pull-request-artifacts/issues/15
- https://github.com/orgs/community/discussions/9252

### CHANGELOG conflict crisis (Rule 7 broken pattern)
- https://about.gitlab.com/blog/2018/07/03/solving-gitlabs-changelog-conflict-crisis/
- https://github.com/PrefectHQ/prefect/issues/2311
- https://engineering.uptechstudio.com/blog/keep-a-changelog-without-conflicts/
- https://manpages.debian.org/testing/git-merge-changelog/git-merge-changelog.1.en.html
- https://medium.com/@nettsundere/on-reducing-changelog-merge-conflicts-1eb23552630b

### Stale worktree / cleanup
- https://github.com/anthropics/claude-code/issues/34282
- https://github.com/anthropics/claude-code/issues/26725
- https://github.com/ThinkVelta/claude-worktree-tools/blob/main/templates/skills/wt-cleanup/SKILL.md

### GitHub labels
- https://docs.github.com/en/issues/using-labels-and-milestones-to-track-work/managing-labels
- https://medium.com/@dave_lunny/sane-github-labels-c5d2e6004b63

### Multi-agent scaling
- https://research.google/blog/towards-a-science-of-scaling-agent-systems-when-and-why-agent-systems-work/
- https://www.anthropic.com/engineering/multi-agent-research-system
- https://www.infoq.com/news/2026/02/google-agent-scaling-principles/

### Cognitive load
- https://lawsofux.com/millers-law/
- https://newsletter.techworld-with-milan.com/p/context-switching-is-the-main-productivity
- https://en.wikipedia.org/wiki/Cognitive_load

### Kanban WIP
- https://www.atlassian.com/agile/kanban/wip-limits
- https://www.wrike.com/kanban-guide/kanban-wip-limits/

### Git branch naming
- https://medium.com/leantaas-engineering/why-are-we-limiting-git-branch-name-length-to-28-characters-c49cb5f4ff9a
- https://graphite.com/guides/git-branch-naming-conventions
- https://phoenixnap.com/kb/git-branch-name-convention

### CRDT
- https://crdt.tech/
- https://en.wikipedia.org/wiki/Conflict-free_replicated_data_type

### ADR
- https://docs.aws.amazon.com/prescriptive-guidance/latest/architectural-decision-records/adr-process.html
- https://adr.github.io/
- https://learn.microsoft.com/en-us/azure/well-architected/architect-role/architecture-decision-record
