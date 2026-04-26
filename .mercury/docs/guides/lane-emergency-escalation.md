# Lane Emergency Spec-Change Escalation — Rule 4.1

Implements **Rule 4.1 emergency spec-change escalation** of the multi-lane
protocol (v0.1 Delta 4, Issue [#312](https://github.com/392fyc/Mercury/issues/312)).

This is a **doc-only protocol delta** plus an optional helper script
(`scripts/check-main-idle.sh`). No mandatory code path.

## Why

Rule 4 grants the main lane exclusive edit rights over `.mercury/docs/DIRECTION.md`
and `.mercury/docs/EXECUTION-PLAN.md`. Side lanes that need a spec change must
file an Issue and wait for main to act on it.

This works fine when main is responsive. It fails when main is unresponsive
for an extended period — the side lane is then deadlocked: it cannot proceed
without the spec change, cannot make the change itself (Rule 4 red line), and
has no escalation path. The Spotify model documented this exact failure mode
in 2018 (see Sources below); without an explicit escape hatch, lanes either
stall indefinitely or quietly violate Rule 4.

Rule 4.1 closes the gap with a **user-arbitrated** escalation: side lane
opens a clearly-flagged PR, the user (not the side lane) decides whether to
merge.

## When the rule applies

All of:

1. Side lane needs a change to `DIRECTION.md` or `EXECUTION-PLAN.md` to make
   forward progress on its claimed Issues.
2. The blocking concern was filed on a coordination Issue (`lane:main` +
   `coordination` labels) at least 48 hours before the escalation PR is opened.
3. Main lane has been idle for 48+ hours — defined objectively as **all three**
   of:
   - No commits to `feature/lane-main/*` or legacy `feature/TASK-*` branches
   - No edits to `<memory-dir>/session-handoff.md`
   - No `updatedAt` change on any Issue carrying the `lane:main` label

The optional helper `scripts/check-main-idle.sh` checks all three signals and
returns exit `0` (idle) or exit `1` (active). Use it to verify the
precondition before opening the escalation PR; do not skip the check based on
intuition.

## Procedure

1. **Verify the 48h precondition** (do not skip):

   ```bash
   scripts/check-main-idle.sh --hours 48
   # exit 0 → precondition met
   # exit 1 → main has activity within 48h, escalation NOT permitted
   ```

2. **Open the PR with the emergency prefix**. Title format:

   ```
   [EMERGENCY-<lane>] <short summary>
   ```

   Example: `[EMERGENCY-side-multi-lane] DIRECTION.md §3 cap-5 bypass for #292 close`

3. **PR body MUST include**:
   - Reference to this rule: `Per Rule 4.1 (lane-emergency-escalation.md)`.
   - Reference to the coordination Issue filed ≥48h earlier.
   - Output of `scripts/check-main-idle.sh --format json` as evidence the
     precondition was met at the moment the PR was opened (paste verbatim).
   - Explicit ping to the user — escalation is **opt-in**; the user decides
     whether to merge, not the bot.
   - A rollback statement: what to revert if the spec change turns out to be
     wrong.

4. **Do NOT** auto-merge or admin-merge. The user is the arbitrator. If
   review bots block the PR, that is a feature, not a bug — the user can
   bypass after reviewing.

5. **If main lane resumes activity** during the PR's review window, **close
   the PR without merging**, hand the change request back to main lane via
   the original coordination Issue, and proceed without the spec change. The
   precondition (48h idle) was the entire justification; once main is active,
   it is no longer met.

## What this rule is NOT

- **Not** a way for side lanes to bypass main on substantive disagreements.
  Rule 4 still applies; this is purely a deadlock breaker for absent owners.
- **Not** auto-merge. The user must explicitly approve.
- **Not** retroactive. Once main resumes activity the escalation lapses.
- **Not** a license to claim Issues outside the side lane's scope. The
  spec-change PR must be the minimum viable change to unblock the lane's
  existing claimed work.

## Suggested PR body template

```markdown
## Emergency spec-change escalation (Rule 4.1)

**Lane**: <lane-name>
**Coordination Issue**: #NNN (filed YYYY-MM-DD, >=48h before this PR)
**Blocked work**: #MMM (claimed by `lane:<lane-name>`)

### Idleness evidence

`scripts/check-main-idle.sh --format json` output at PR open:
\`\`\`json
{...paste verbatim...}
\`\`\`

### Spec change

<diff summary>

### Rollback

<one-line description of what to revert>

### Arbitration

@<user> — Rule 4.1 opt-in escalation. Merge only if you accept the change
on main lane's behalf; close without merge if main resumes activity during
the review window.
```

## `scripts/check-main-idle.sh` reference

```bash
scripts/check-main-idle.sh [--hours N] [--memory-dir PATH]
                           [--repo OWNER/REPO] [--repo-root PATH]
                           [--no-issue-check] [--format text|json]
```

| Flag | Effect |
|------|--------|
| `--hours N` | Idleness threshold in hours (default 48). |
| `--memory-dir PATH` | Override memory dir. Defaults to `MERCURY_MEMORY_DIR` env, then `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/D--Mercury-Mercury/memory`. |
| `--repo OWNER/REPO` | Pin the GitHub repo. Defaults to `gh repo view` resolution. |
| `--repo-root PATH` | Pin the local checkout for `git for-each-ref` branch-activity probing. Defaults to `git rev-parse --show-toplevel` from cwd; pass explicitly when invoking outside the Mercury checkout. |
| `--no-issue-check` | Skip GitHub Issue probe. Useful in CI without `gh` auth. Note: skipping this signal makes the verdict less reliable — issue activity is one of three signals, and absence is treated as "stale" for that signal. |
| `--format text\|json` | Output format. |

Exit `0` if all three signals are stale past the threshold (idle); exit `1`
otherwise (active); exit `2` on argument or environment errors.

## Tests

```bash
scripts/test-check-main-idle.sh
```

Verifies arg validation, fresh-handoff verdict (exit 1), 60d-old-handoff
fixture report fields, and JSON output structure.

## Source references

- Issue [#312](https://github.com/392fyc/Mercury/issues/312) — acceptance criteria
- [v0.1 Delta companion](../lane-protocol-v0.1-deltas.md#delta-4--rule-41-emergency-spec-change-escalation-p2)
- [Overcoming the Pitfalls of the Spotify Model](https://medium.com/@ss-tech/overcoming-the-pitfalls-of-the-spotify-model-8e09edc9583b)
- `feedback_lane_protocol.md` Rule 4 (DIRECTION/EXECUTION-PLAN exclusivity)
