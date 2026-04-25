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

| Flag | Effect |
|------|--------|
| `--dry-run` | Print the intended actions without calling `gh`. Useful for CI validation. |
| `--no-assignee` | Skip `--add-assignee @me`. Use when the wrapper runs in CI/bot context where `@me` resolves to the bot account. |
| `-h`, `--help` | Print usage from this script's header. |

### Exit codes

| Exit | Meaning |
|------|---------|
| `0` | Clean claim — exactly one `lane:*` label present after probe |
| `1` | Conflict — multiple `lane:*` labels OR zero `lane:*` labels post-write; conflict comment posted on the Issue |
| `2` | Invalid args, `gh` or `jq` missing, or `gh` API error |

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
> Latest claim: `lane:other` by `@user`.
>
> GitHub REST API non-atomic — concurrent claims both succeeded silently. Manual arbitration
> required:
>
> 1. Decide which lane owns Issue #N
> 2. Other lane(s): `gh issue edit N --remove-label lane:<other>`
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
- [`feedback_lane_protocol.md`](https://github.com/392fyc/Mercury/blob/develop/.mercury/docs/research/multi-lane-protocol-2026-04-25.md) (user-memory, repo mirror in research doc)
- [v0.1 Delta companion](../lane-protocol-v0.1-deltas.md#delta-1--rule-11-probe-after-write-p1)
- [GitHub Releases API race condition (devactivity.com)](https://devactivity.com/insights/mastering-github-releases-avoiding-race-conditions-for-enhanced-engineering-productivity/)
- [GitHub community discussion #9252 — concurrency group bug](https://github.com/orgs/community/discussions/9252)
