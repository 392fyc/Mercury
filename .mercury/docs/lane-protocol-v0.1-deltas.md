# Multi-Lane Protocol — v0.1 Delta Proposal (PR-auditable companion)

**Status**: PROPOSED — pending main lane S74+ decision
**Source**: S1-side-multi-lane research (Issue #292)
**Companion to**: `.mercury/docs/research/multi-lane-protocol-2026-04-25.md` (full design doc)
**Mirror of**: user-memory `feedback_lane_protocol.md` v0.1 Delta Proposal section

---

## Why this file exists

The v0 lane protocol authoritative file lives in user-level memory at
`${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/D--Mercury-Mercury/memory/feedback_lane_protocol.md`
(gitignored by design — it is a Claude Code memory-system `feedback` artifact, scoped per-user).

When research recommends protocol revisions, those proposals must be **PR-auditable** so reviewers,
main-lane decision-makers, and future readers can inspect them in repo without needing the
original session's user-memory access. This file is the canonical PR-internal artifact;
`feedback_lane_protocol.md` v0.1 section in user memory is a per-user working cache that may
drift if the user reorganizes memory.

If conflict arises, **this file is the source of truth**.

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
| 7 | (cap) | doc-only | new | HARD-CAP at 5 active lanes |

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

PR reviewers can independently verify the parallel proposal in user-memory layer:

```bash
# Verify the v0.1 delta section exists in user-memory feedback file (gitignored, per-user)
MEM_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/D--Mercury-Mercury/memory"
test -f "$MEM_DIR/feedback_lane_protocol.md" \
  && grep -q "v0.1 Delta Proposal" "$MEM_DIR/feedback_lane_protocol.md" \
  && echo "OK: v0.1 delta section present in user memory" \
  || echo "ABSENT: user-memory file missing or unmodified"
```

If user memory is not accessible (different operator running this PR review), this file alone
is sufficient as the authoritative proposal — it mirrors all content needed for main-lane
decision.

## Cross-references

- Full research: `.mercury/docs/research/multi-lane-protocol-2026-04-25.md`
- Issue: [#292](https://github.com/392fyc/Mercury/issues/292)
- v0 protocol (read-only reference): user-memory `feedback_lane_protocol.md` (no v0.1 section
  yet) OR see "v0 7 rules" snapshot in research doc §"Protocol 7 rules (subject under evaluation)"
