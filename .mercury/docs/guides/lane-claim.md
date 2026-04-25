# Lane Claim Wrapper — `scripts/lane-claim.sh`

Implements **Rule 1.1 probe-after-write Issue claim verification** of the multi-lane protocol
(v0.1 Delta 1, Issue [#309](https://github.com/392fyc/Mercury/issues/309)).

## Why

Mercury Rule 1 (v0) requires every lane to claim an Issue with `lane:<name>` label before working
on it. The raw command was:

```bash
gh issue edit <N> --add-assignee @me --add-label "lane:<name>"
```

But the GitHub REST API is **not atomic** for label edits. If two lanes claim the same Issue
within milliseconds (concurrent CI runs, parallel session starts, two operators), both writes
succeed and both lanes silently believe they own the Issue. v0's first-timestamp-wins resolution
is post-hoc only — neither lane learns of the conflict until a human notices.

Rule 1.1 closes the gap: after every claim write, immediately re-query the Issue's labels and
verify exactly one `lane:*` prefix is present. Conflict → abort + comment + non-zero exit.

## Usage

```bash
scripts/lane-claim.sh <lane-name> <issue-number> [--dry-run] [--no-assignee]
```

| Flag / Env var | Effect |
|----------------|--------|
| `--dry-run` | Print the intended actions without calling `gh`/`jq`. Strict offline — works without `gh` installed. |
| `--no-assignee` | Skip `--add-assignee @me`. Use when the wrapper runs in CI/bot context where `@me` resolves to the bot account. |
| `-h`, `--help` | Print usage from this script's header. |
| `GH_REPO=<owner>/<repo>` (env) | Override repo target. Without this, the wrapper resolves the repo from the current cwd via `gh repo view`. Set this in CI / off-cwd contexts to prevent accidentally writing to the wrong repo. |

### Repo target pinning

The repo is resolved **once** at script start and passed explicitly to every subsequent `gh issue *`
call as `--repo <owner>/<repo>`. This defends against:

- **Cross-repo writes**: a `cd` mid-script (impossible in this single-file script, but a future
  refactor could introduce one) cannot redirect writes to a different repo.
- **CI / fork contexts**: where `gh` may auto-resolve to an unexpected repo because of
  `git remote` configuration. CI should always set `GH_REPO` explicitly.
- **Wrong-cwd execution**: running the script from a sibling repo silently writes to that repo
  unless `GH_REPO` is set.

If the wrapper cannot resolve the repo (no git repo + no `GH_REPO`), it exits with code `2`
before any issue edit/comment is attempted (`gh repo view` itself may hit the API during
resolution; only the issue write/probe/comment paths are gated by successful resolution).

### Known limitation: residual race window

Rule 1.1 reduces but does not eliminate the GitHub REST non-atomic race. There is a small
residual window between the probe-pass and the follow-up `--add-assignee` call (step 3) during
which a different lane could add a competing `lane:*` label. If that happens, the assignee will
still be added before this wrapper returns, even though the Issue effectively has a conflict.

This is accepted residual risk. Adding a second probe would only shift the window — there is no
atomic compare-and-swap on the GitHub Issues label API. The probe-after-write design is "best
effort post-hoc detection at the closest point to the write," and the second-write (assignee) is
a follow-up that should be treated as a non-binding side-effect (assignees can be removed by any
lane during conflict arbitration without affecting label ownership).

### Exit codes

| Exit | Meaning |
|------|---------|
| `0` | Clean claim — exactly one `lane:*` label after probe AND it matches the requested lane (assignee added in a follow-up call after the probe) |
| `1` | Verification failed — multiple `lane:*` labels (conflict — Issue comment posted), OR zero `lane:*` labels (silent edit failure — warn only), OR exactly one `lane:*` label that belongs to a different lane (existing owner — warn only) |
| `2` | Invalid args, `gh`/`jq` missing, cannot resolve target repo, or `gh` API error |

### Examples

```bash
# Standard claim — current user becomes assignee
scripts/lane-claim.sh side-multi-lane 309

# CI/bot context — skip @me assignment
scripts/lane-claim.sh side-multi-lane 309 --no-assignee

# Validate without calling GitHub
scripts/lane-claim.sh side-multi-lane 309 --dry-run
```

## Conflict resolution

When the wrapper detects a conflict (exit code `1`), it posts a comment on the Issue listing the
detected `lane:*` labels and asking for human arbitration. The comment template:

> :rotating_light: Lane claim conflict detected by `scripts/lane-claim.sh` (Rule 1.1
> probe-after-write).
>
> Post-write label set contains N `lane:*` labels:
> - `lane:main`
> - `lane:other`
>
> This invocation attempted to add `lane:other` as `@user` (ordering of past claims is not
> knowable from this side — multiple lane labels are simply present after the probe).
>
> GitHub REST API non-atomic — concurrent claims both succeeded silently. Manual arbitration
> required:
>
> 1. Decide which lane owns Issue #N
> 2. Other lane(s): `gh issue edit N --repo $REPO --remove-label lane:<other>`
> 3. Loser lanes close their session and fall back to non-conflicting work

The wrapper does **not** auto-remove either label — that decision belongs to the user, since
"first claim should win" is a heuristic that breaks when the first claim came from a stale
process or a misconfigured agent.

## Tests

```bash
scripts/test-lane-claim.sh
```

Stubs `gh` via PATH manipulation and exercises:

- Argument validation (missing args, invalid lane name, non-numeric Issue)
- `--help` and `--dry-run` paths (no API calls)
- Happy path (single `lane:*` label) → exit `0`
- Race detection (two `lane:*` labels) → exit `1`
- Zero-label edge case (silent edit failure) → exit `1`
- `gh issue edit` failure → exit `2`

Tests do not touch real GitHub — safe to run in CI on every commit.

## Source references

- Issue [#309](https://github.com/392fyc/Mercury/issues/309) — acceptance criteria
- [Multi-lane protocol research design doc](../research/multi-lane-protocol-2026-04-25.md) — repo-side authority for the v0 7 rules (the protocol is also mirrored in user-memory `feedback_lane_protocol.md`, which is per-machine and not web-accessible)
- [v0.1 Delta companion](../lane-protocol-v0.1-deltas.md#delta-1--rule-11-probe-after-write-p1)
- [GitHub Releases API race condition (devactivity.com)](https://devactivity.com/insights/mastering-github-releases-avoiding-race-conditions-for-enhanced-engineering-productivity/)
- [GitHub community discussion #9252 — concurrency group bug](https://github.com/orgs/community/discussions/9252)
