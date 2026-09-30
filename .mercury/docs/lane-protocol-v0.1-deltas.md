# Multi-Lane Protocol — v0.1 Delta Proposal (PR-auditable companion)

**Status**: HISTORICAL — the protocol (v1, including accepted deltas) was accepted on 2026-05-03 (#347); the rules in force are indexed in [guides/lane-protocol.md](guides/lane-protocol.md) (#618)
**Source**: S1-side-multi-lane research (Issue #292)
**Companion to**: `.mercury/docs/research/multi-lane-protocol-2026-04-25.md` (full design doc)
**Mirror of** (historical): the user-memory `feedback_lane_protocol.md` v0.1 Delta Proposal section. That file was lost in the #579 migration; the rule authority is now [guides/lane-protocol.md](guides/lane-protocol.md) (#618)

---

## Why this file exists

When research recommends protocol revisions, those proposals must be **PR-auditable** so reviewers,
main-lane decision-makers, and future readers can inspect them in repo without needing access to
the original session's user-memory layer.

## Authority scoping (single conflict-resolution rule)

There are two related artifacts with **non-overlapping scopes**:

| Artifact | Location | Scope | Lifecycle |
|----------|----------|-------|-----------|
| **Rules in force** (Rules 1–8 and sub-rules) | repo `.mercury/docs/guides/lane-protocol.md` (#618; replaces the lost user-memory `feedback_lane_protocol.md`) | **AUTHORITATIVE** for the rules currently in force and where each is enforced | Updated in the same PR as the guide or script that changes a rule |
| **v0.1 delta proposal** (this file) | repo `.mercury/docs/lane-protocol-v0.1-deltas.md` | Record of the proposed deltas and their evidence | Historical: decided with v1 on 2026-05-03 (#347) |

**Single precedence rule**: for what is in force today, the rule index wins; this file is the
record of why each delta was proposed.

- "What is Rule 5 today?" → answer from [guides/lane-protocol.md](guides/lane-protocol.md) (rule authority)
- "What does the v0.1 proposal change about Rule 5?" → answer from this file (delta authority)
- "What is Rule 5 in v1?" → v1 (v0 rules plus the accepted deltas) is what the rule index records

Historical note: the lost user-memory file also held a "v0.1 Delta Proposal" working-cache
section mirroring this file. Where any surviving copy differs, this file wins for the deltas and
the rule index wins for the rules in force.

## Verdict

CONDITIONAL_GO for v1 promotion. v0 mechanics work at 2-lane scale (empirical: S1-side-multi-lane
ran in parallel with main lane S73 with no merge conflict on shared index). Two rules have known
industry-broken patterns: Rule 7 ↔ GitLab CHANGELOG conflict crisis; Rule 1 ↔ GitHub API
non-atomic claim. 7 deltas required for safe v1 promotion.

## Delta inventory

| # | Rule | Priority | Type | Summary |
|---|------|----------|------|---------|
| 1 | 1.1 | P1 | new | Probe-after-write Issue claim verification |
| 2 | 3.1 | P1 | new | 14-day stale lane sweep |
| 3 | 3.2 | P2 | new | Tmp dir auto-prune on close |
| 4 | 4.1 | P2 | new | Emergency spec-change escalation if main idle > 48h |
| 5 | 7 | **P0 BREAKING** | replace | Per-session files instead of append-only index |
| 6 | 2 | P3 | modify | Shorter branch prefix `lane/<short>/<N>-*` |
| 7 | (cap) | doc-only | new | HARD-CAP at 5 active lanes (**superseded by #605: no cap**) |

## Delta 1 — Rule 1.1 probe-after-write (P1)

**Mechanism**: After every `gh issue edit --add-label lane:<name>`, immediately re-query Issue
labels. If count of `lane:*` labels > 1 → abort current lane + comment Issue + ping user.
Implementation: ~5 LOC bash wrapper `scripts/lane-claim.sh`.

**Why**: GitHub REST API non-atomic; concurrent calls both succeed silently. v0 first-timestamp-wins
is post-hoc, not preventive.

**Sources**:
- [GitHub Releases API Race Condition](https://devactivity.com/insights/mastering-github-releases-avoiding-race-conditions-for-enhanced-engineering-productivity/)
- [Concurrency group bug (community#9252)](https://github.com/orgs/community/discussions/9252)

## Delta 2 — Rule 3.1 stale lane sweep (P1)

**Mechanism**: Lane is "stale" if all of: no commits to `feature/lane-<lane>/*` in 14 days AND
no handoff updates in 14 days AND no Issue activity in 14 days → main lane auto-marks `stale`
during periodic sweep.

**Why**: Claude Code 2.1.76 already added native stale worktree detection (7+ day threshold)
post 222-workspace + 8-agent same-file-write disasters. Mercury has no equivalent.

**Implementation**: monthly cron `scripts/lane-sweep.sh` OR manual run.

**Sources**:
- [DOCS Worktree cleanup recovery (claude-code#34282)](https://github.com/anthropics/claude-code/issues/34282)
- [Stale worktrees never cleaned up (claude-code#26725)](https://github.com/anthropics/claude-code/issues/26725)

## Delta 3 — Rule 3.2 tmp dir auto-prune (P2)

**Mechanism**: `.tmp/lane-<lane>/` auto-deleted when `LANES.md` status flips to `closed`.
Implementation: `scripts/lane-close.sh <lane-name>` handles both atomic operations.

**Why**: orphan tmp dirs accumulate silently; no cleanup policy in v0.

## Delta 4 — Rule 4.1 emergency spec-change escalation (P2)

**Mechanism**: If side lane needs spec change AND main lane idle > 48h (no commits / no handoff
updates / no Issue activity in claimed Issues), side lane MAY:
1. Open PR with title prefix `[EMERGENCY-<lane>]`
2. Reference this rule in PR body
3. Ping user explicitly

User becomes arbitrator (explicit opt-in PR review, not auto-merge).

**Why**: Spotify model documented this exact deadlock. Rule 4 currently has no escalation path.

**Sources**:
- [Overcoming the Pitfalls of the Spotify Model](https://medium.com/@ss-tech/overcoming-the-pitfalls-of-the-spotify-model-8e09edc9583b)

## Delta 5 — Rule 7 REPLACEMENT (P0, BREAKING)

**Mechanism**:
- **OLD Rule 7**: append-only edits to `MEMORY.md` and `SESSION_INDEX.md`
- **NEW Rule 7**: each session writes its own file `memory/sessions/S<N>-<lane>.md`. Index files
  contain only auto-generated lines via `scripts/regenerate-memory-index.sh` (pre-commit or
  post-merge hook).

**3-phase migration**:
- **Phase A (additive)**: deploy regenerate script. Generate to separate file
  `memory/INDEX.generated.md` for diff inspection. Existing files untouched. Run for ≥3 sessions
  to verify output stability.
- **Phase B (cutover, BREAKING)**: split existing rows into per-session files. Replace
  `MEMORY.md`/`SESSION_INDEX.md` with generated index. Tag pre-cutover commit
  `lane-protocol-v0.1-pre-cutover` for instant rollback.
- **Phase C (lock-in)**: pre-commit hook rejects direct edits to index files outside script.

**Consistency guarantees**:
- Script output deterministic (sort by session ID + lane); `git diff` after regenerate must be
  empty or only the new session's row added
- Pre-commit hook validates index matches source files before allowing commit
- Per-session files are append-only at session granularity (no mid-session rewrites);
  content frozen post-handoff

**Failure rollback**:
- Regenerate script fail (parse error, missing frontmatter): script exits non-zero, pre-commit
  blocks; user fixes source or runs `scripts/regenerate-memory-index.sh --fallback-preserve`
- Phase B cutover causes index drift: revert to `lane-protocol-v0.1-pre-cutover` tag (single
  git command); existing files restored verbatim
- Orphaned per-session files (lane closed but file remains): handled by Rule 3.1 stale sweep

**Out-of-scope**:
- Migration of `feedback_*.md`, `project_*.md`, `reference_*.md` (not session-scoped)
- AgentKB / mem0 layer integration (orthogonal to memory layer rebuild #252)

**Why**: GitLab CHANGELOG conflict crisis is the canonical industry-broken pattern. Mercury's
append-only shared index will fail predictably at 5+ lanes with concurrent commits. Already
empirically observed: this very session + main lane S73 happened to not conflict by luck.

**Sources**:
- [How we solved GitLab's CHANGELOG conflict crisis](https://about.gitlab.com/blog/2018/07/03/solving-gitlabs-changelog-conflict-crisis/)
- [git-merge-changelog driver](https://manpages.debian.org/testing/git-merge-changelog/git-merge-changelog.1.en.html)

## Delta 6 — Rule 2 shorter branch prefix (P3)

**Mechanism**:
- **OLD**: `feature/lane-<lane>/TASK-<N>-*` (45-65 chars)
- **NEW**: `lane/<short>/<N>-<slug>` (≤40 chars). Example: `lane/side-mlane/292-protocol-research`

**Why**: 65-char branches exceed community 50-char soft cap, way over LeanTaaS 28-char hard cap.
IDE autocomplete + URL pasting suffer.

**Backward-compat**: legacy `feature/TASK-N-*` retained for main lane.

**Sources**:
- [Limiting Git Branch Names to 28 Characters (LeanTaaS)](https://medium.com/leantaas-engineering/why-are-we-limiting-git-branch-name-length-to-28-characters-c49cb5f4ff9a)
- [Best practices for naming Git branches (Graphite)](https://graphite.com/guides/git-branch-naming-conventions)

## Delta 7 — HARD-CAP at 5 active lanes (doc-only)

> **Superseded (2026-09, Issue [#605](https://github.com/392fyc/Mercury/issues/605)):** the lane-count cap was removed; see `.mercury/docs/research/issue-599-cross-harness-lane-isolation-2026-09.md` D1. Kept as history.

**Mechanism**: `LANES.md` MUST NOT exceed 5 active lanes. Attempting to open lane #6 requires:
1. Closing existing lane first, OR
2. Opening Issue with `protocol-violation` label requesting cap raise (user decision)

**Why**:
- Miller's 7±2 working memory cap (Laws of UX)
- Google multi-agent research: 3-5 agents optimal; 20+ catastrophic; 39-70% reasoning
  performance drop
- Personal Kanban WIP limit: 3-5 max parallel activities

**Sources**:
- [Miller's Law (Laws of UX)](https://lawsofux.com/millers-law/)
- [Towards a science of scaling agent systems (Google research)](https://research.google/blog/towards-a-science-of-scaling-agent-systems-when-and-why-agent-systems-work/)
- [Working with WIP limits for kanban (Atlassian)](https://www.atlassian.com/agile/kanban/wip-limits)

## Acceptance path

Main lane S74+ to decide:

1. **Accept all 7 deltas** → file 7 implementation Issues per priority (P0 first); promote to v1 once
   P0 lands. Estimated: P0 = 1 PR (~50 LOC bash + hook), P1×2 = 2 PRs (~30 LOC each),
   P2×2 = 2 PRs (~20 LOC each), P3 = 1 PR (~10 LOC), HARD-CAP = doc-only.
2. **Cherry-pick subset** → file Issues only for accepted deltas; document rejected deltas in
   this file under "Rejected deltas" with rationale.
3. **Reject** → keep v0 as-is with documented residual risk acceptance; this file moves to
   `.mercury/docs/research/` archive.

## Verification commands

The original check compared this file with the user-memory copy on the operator's machine. That
copy is gone (#579), so there is nothing to compare against: this repo file is the proposal of
record, and [guides/lane-protocol.md](guides/lane-protocol.md) records which deltas are in force.

## Cross-references

- Full research: `.mercury/docs/research/multi-lane-protocol-2026-04-25.md`
- Issue: [#292](https://github.com/392fyc/Mercury/issues/292)
- Rules in force: [guides/lane-protocol.md](guides/lane-protocol.md) (#618). It replaces the lost user-memory `feedback_lane_protocol.md`.
