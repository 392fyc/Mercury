# Lane Close Ceremony — `scripts/lane-close.sh`

Implements **Rule 3.2 tmp dir auto-prune on close** of the multi-lane protocol
(v0.1 Delta 3, Issue [#311](https://github.com/392fyc/Mercury/issues/311)).

## Why

When a lane finishes its scope, two cleanup steps must happen in lock-step:

1. The lane's `**Status**:` line in `LANES.md` flips from `active` to `closed`
   so future sessions know not to claim work against it.
2. The lane's `.tmp/lane-<lane>/` directory is removed so the repo's working
   tree doesn't accumulate orphan scratch dirs.

v0 had no script for either step, so operators did them by hand — and the
"flip the wrong line" failure mode was easy to hit (the registry has multiple
`**Status**:` lines, one per lane). This script makes the two-step ceremony
atomic and bounded to the owning lane's section.

## Usage

```bash
scripts/lane-close.sh <lane-name>
                      [--lanes-file PATH] [--memory-dir PATH]
                      [--tmp-dir PATH] [--repo-root PATH]
                      [--yes] [--force-cross-lane] [--dry-run]
```

| Flag / Env var | Effect |
|----------------|--------|
| `--yes` | Skip the interactive confirm prompt. Required in non-interactive contexts (CI, autorun). |
| `--force-cross-lane` | Suppress the warning emitted when the current branch does not match `feature/lane-<lane>/*`. Useful when closing from `develop` after a PR merge. |
| `--dry-run` | Print intended actions without modifying `LANES.md` or removing the tmp dir. |
| `--lanes-file PATH` | Override LANES.md location. Defaults to `<memory-dir>/LANES.md`. |
| `--memory-dir PATH` | Override memory dir. Defaults to `MERCURY_MEMORY_DIR` env, then `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/D--Mercury-Mercury/memory`. |
| `--tmp-dir PATH` | Override the tmp dir to remove. Defaults to `<repo-root>/.tmp/lane-<lane>`. |
| `--repo-root PATH` | Override repo root. Defaults to `git rev-parse --show-toplevel`. |
| `MERCURY_MEMORY_DIR` (env) | Same effect as `--memory-dir`. |

### Section-bounded edit (Rule 6 compliance)

The Status rewrite is bounded to the **target lane's section only**. The script
walks `LANES.md` line-by-line and:

1. Detects the lane heading (`### \`<lane>\``).
2. Sets an in-section flag.
3. Rewrites the **first** `- **Status**:` line encountered while the flag is on.
4. Resets the flag at the next `## ` or `### ` heading.

Other lanes' `Status` lines are **never** touched, even if they appear nearby
in the file. The test harness verifies this explicitly (a fixture with three
active lanes — `main`, `side-target`, `side-other` — confirms only the named
lane flips).

### Owning-lane heuristic

Per Rule 6, only the owning lane should close itself. The script can't
cryptographically prove ownership from inside a single repo checkout, so it
uses a heuristic: if the current branch starts with `feature/lane-<lane>/`,
the run is presumed in-scope and proceeds silently. Otherwise it emits a
`WARN` (not a hard error). Pass `--force-cross-lane` to silence the warning
when you legitimately need to close from another branch — e.g., after the
lane's last PR was merged and you switched back to `develop`.

### Tmp-dir safety guards

Before removing `.tmp/lane-<lane>/`, the script refuses to proceed if it
contains either:

- Any file matching `*.uncommitted` — the documented Mercury convention for
  "save me first" markers attached to scratch work.
- A `.git` directory — indicates a nested checkout that the operator likely
  doesn't want destroyed.

In either case the script exits `1` **without** flipping the Status line, so
the registry is never left in a half-closed state. Resolve the unsafe content
manually (move it to a real branch, delete after inspection, etc.) then
re-run.

### Exit codes

| Exit | Meaning |
|------|---------|
| `0` | Lane closed cleanly — Status flipped AND tmp dir removed (or did not exist). Also returned for successful `--dry-run`. |
| `1` | Validation failed: lane not in LANES.md / already closed / unsafe tmp content / user aborted at confirm prompt / non-interactive without `--yes`. |
| `2` | Invalid args, lanes-file not found, cannot resolve repo root. |

## Examples

```bash
# Standard close from the lane's own working branch
scripts/lane-close.sh side-multi-lane

# Close from develop (after PR merged) — silence cross-lane warning
git checkout develop
scripts/lane-close.sh side-multi-lane --yes --force-cross-lane

# Preview without mutating anything
scripts/lane-close.sh side-multi-lane --dry-run

# Custom paths for testing
scripts/lane-close.sh side-multi-lane --yes --force-cross-lane \
  --lanes-file ./test-fixtures/LANES.md \
  --memory-dir ./test-fixtures \
  --repo-root ./test-fixtures \
  --tmp-dir ./test-fixtures/.tmp/lane-side-multi-lane
```

## Tests

```bash
scripts/test-lane-close.sh
```

Builds a 4-lane synthetic LANES.md (3 active + 1 already-closed) and exercises:

- Argument validation (missing lane, invalid lane name, missing lanes-file)
- Happy path: target lane flipped, OTHER lanes' Status untouched, tmp dir removed
- Already-closed lane → exit 1
- Unknown lane → exit 1
- `*.uncommitted` file in tmp dir → exit 1, Status preserved (no partial state)
- `.git` artifact in tmp dir → exit 1
- `--dry-run` exits 0 without mutating LANES.md
- Non-interactive context without `--yes` → exit 1

Tests use synthetic fixtures only — no GitHub or live LANES.md interaction.

## Source references

- Issue [#311](https://github.com/392fyc/Mercury/issues/311) — acceptance criteria
- [v0.1 Delta companion](../lane-protocol-v0.1-deltas.md#delta-3--rule-32-tmp-dir-auto-prune-p2) — full rationale
- `feedback_lane_protocol.md` Rule 6 (LANES.md section ownership)
