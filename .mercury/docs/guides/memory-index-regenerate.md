# Memory Index Regeneration — Phase F.A + F.B

Implements **Phase F.A** (Issue [#329](https://github.com/392fyc/Mercury/issues/329) — additive,
landed) AND **Phase F.B** (Issue [#330](https://github.com/392fyc/Mercury/issues/330) — in-place
cutover, BREAKING) of `feedback_lane_protocol.md` Rule 7 REPLACE (v0.1 Delta 5, parent epic
[#315](https://github.com/392fyc/Mercury/issues/315)).

## What Phase F.A is

Phase F.A is the **non-breaking** first phase of replacing the user-memory
append-only shared index (`MEMORY.md` + `SESSION_INDEX.md`) with per-session
files (`memory/sessions/S<N>(-<lane>)?.md`).

During F.A, `scripts/regenerate-memory-index.sh` reads the current memory dir
and emits a regenerated index document to a **separate file**
(`memory/INDEX.generated.md` by default) so canonical files stay byte-identical.
Operators inspect the generated file via `diff` against expected output across
multiple sessions to validate **determinism** before committing to Phase F.B
cutover.

| Phase | What changes | When |
|-------|--------------|------|
| **F.A (additive)** | Adds regenerate script; canonical files untouched | Issue #329 — landed |
| **F.B (cutover)** | Splits existing rows into per-session files; replaces canonical sections with generated content via `--in-place` | Issue #330 — landed |
| **F.C (lock-in)** | Claude Code PreToolUse + SessionEnd hooks prevent direct edits to canonical files | Issue #331 — staged in `scripts/hooks/`, deployment pending F.B 1-week soak |

## Why this exists

GitLab CHANGELOG conflict crisis is the canonical industry-broken pattern for
shared append-only index files at parallel-write scale. Mercury's append-only
`SESSION_INDEX.md` and `MEMORY.md` "Project (Session History)" subsection will
fail predictably at 5+ active lanes with concurrent commits. Already
empirically observed: side-multi-lane S1 and main lane S73 happened to not
conflict by luck (writes were minutes apart on non-adjacent lines).

Per-session files (`memory/sessions/S<N>-<lane>.md`) are append-only at session
granularity but **never co-edited** — the index is a derived artifact. This
removes the conflict surface entirely.

Source: [How we solved GitLab's CHANGELOG conflict crisis](https://about.gitlab.com/blog/2018/07/03/solving-gitlabs-changelog-conflict-crisis/).

## Usage

```bash
scripts/regenerate-memory-index.sh [--memory-dir PATH] [--output PATH]
                                   [--format text|diff]
```

| Flag | Effect |
|------|--------|
| `--memory-dir PATH` | Override memory dir. Defaults to `MERCURY_MEMORY_DIR` env, then `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/D--Mercury-Mercury/memory`. |
| `--output PATH` | Override output path. Defaults to `<memory-dir>/INDEX.generated.md`. Use `-` to write to stdout. |
| `--format text\|diff` | `text` (default) writes regenerated content. `diff` compares the fresh regenerate against the existing `<memory-dir>/INDEX.generated.md` snapshot from a prior text-mode run and reports drift (exit 0 = no drift, exit 1 = drift detected). Does **not** compare against canonical `MEMORY.md` / `SESSION_INDEX.md` — those are read-only inputs in Phase F.A. |
| `MERCURY_MEMORY_DIR` (env) | Same as `--memory-dir`. |
| `MERCURY_REGEN_TIMESTAMP` (env) | Override `generated_at` field. Tests + operator side-by-side determinism diffs set this for byte-identical comparison; production runs leave unset (live ISO timestamp). Note: `--format diff` strips the `generated_at` line before comparing, so operators do **not** need to set this when running drift checks against an existing `INDEX.generated.md`. |

Exit codes:
- `0` — clean regenerate written; in `--format diff` mode: no drift detected
- `1` — per-session frontmatter parse error OR drift detected in diff mode
- `2` — invalid args / memory dir missing / required source file missing

## Source precedence

For each session row, the script picks content from:

1. **`<memory-dir>/sessions/S<N>(-<lane>)?.md`** when present — frontmatter is
   authoritative
2. **`<memory-dir>/SESSION_INDEX.md`** existing table row — fallback when no
   per-session file exists

This precedence lets Phase F.A operate on **today's state** (no per-session
files yet exist) while gracefully accepting per-session files as Phase F.B
cutover writes them.

## Per-session file schema (target — populated during F.B)

```yaml
---
name: session_S<N>_<lane>
description: <one-liner — populates SESSION_INDEX 任务主题 column>
type: session
session_id: S<N>            # main lane: "S29" / side lane: "S3-side-multi-lane"
lane: main                  # or "side-multi-lane" / etc.
date: YYYY-MM-DD
origin_session_id: <UUID|—> # optional, defaults to "—"
outcome: <one-liner — populates SESSION_INDEX 关键产出 column>
---

# Session S<N>(-<lane>) — <theme>

(body — informational, not parsed by regenerate script)
```

Required fields: `session_id`, `date`, `description`, `outcome`. Missing any of
these → script exits 1 with WARN naming the offending file.

## Output structure (`INDEX.generated.md`)

```markdown
---
name: INDEX.generated
description: Auto-generated by scripts/regenerate-memory-index.sh — Phase F.A additive (Issue #329). DO NOT EDIT MANUALLY.
type: generated
generated_at: <ISO8601 UTC>
generated_from: <memory-dir absolute path>
---

# Memory Index — Auto-Generated

## SESSION_INDEX_GENERATED

> Replaces the table in `SESSION_INDEX.md` upon Phase F.B cutover.

| Session | 日期 | 任务主题 | 关键产出 | originSessionId |
|---------|------|----------|----------|-----------------|
| <sorted rows> |

## MEMORY_PROJECT_SESSION_HISTORY_GENERATED

> Replaces the "Project (Session History)" subsection of `MEMORY.md` upon F.B cutover.

- <sorted bullets>
```

Sort order: numeric prefix of session_id ascending; for same number, lane name
alphabetical with `main` first. Examples: `S1`, `S1-side-foo`, `S2`,
`S2-side-multi-lane`. Range rows like `S35–S46` sort by lower bound (35).

## Phase F.A operator workflow

**One-time setup**: nothing — script ships in `scripts/`, no install step.

**At end of each session** (during soak window, before Phase F.B cutover):

```bash
# Generate fresh index alongside canonical files
scripts/regenerate-memory-index.sh

# Inspect: should produce stable output across consecutive runs
MERCURY_REGEN_TIMESTAMP="2026-04-26T00:00:00Z" scripts/regenerate-memory-index.sh \
  --output /tmp/regen-1.md
MERCURY_REGEN_TIMESTAMP="2026-04-26T00:00:00Z" scripts/regenerate-memory-index.sh \
  --output /tmp/regen-2.md
diff /tmp/regen-1.md /tmp/regen-2.md  # should be empty (idempotency)

# Compare regenerated content against canonical SESSION_INDEX.md
# (during F.A: differences expected only in section wrapping/headers; row content
# should match exactly)
```

**Phase F.B readiness gate** (≥3-session soak):

- ✅ Run regenerate at end of each session
- ✅ `INDEX.generated.md` byte-identical (modulo timestamp) across consecutive runs within same session
- ✅ Row content matches canonical `SESSION_INDEX.md` rows verbatim
- ✅ MEMORY.md "Project (Session History)" bullets match canonical verbatim

If any check fails → file F.A bug Issue, fix, re-soak. Phase F.B does NOT
proceed until F.A is stable.

## Failure rollback (Phase F.A)

Phase F.A is non-breaking by construction:
- Script bug → delete `INDEX.generated.md`; canonical files unaffected; no-op
  rollback
- Soak reveals output instability → close PR not merged; iterate; defer Phase
  F.B until F.A stable
- No git or hook integration during F.A → no rollback infrastructure needed

## Phase F.B (`--in-place`) — landed

Phase F.B is the **BREAKING** cutover that mutates canonical `MEMORY.md` and
`SESSION_INDEX.md` in place. Use after the F.A soak window confirms output
stability across ≥3 sessions.

### Prerequisites

1. F.A soak window complete (≥3 consecutive `--format diff` clean runs).
2. Per-session files materialized for ALL existing rows. Use the one-shot
   migration helper:
   ```bash
   bash scripts/migrate-session-index-to-files.sh
   # Idempotent — skips files that already exist. --force overwrites.
   # --dry-run prints intended writes to stdout without touching disk.
   ```
   The helper bulk-creates `memory/sessions/S<N>(-<lane>)?.md` from
   SESSION_INDEX.md table rows. Special-case rows (en-dash range like
   `S35–S46`, gap rows like `S55`, see-handoff rows) require manual review
   and may need hand-rewriting before cutover.
3. Rows with embedded `|` in YAML scalar (corruption-recovery candidates) —
   helper emits WARN; verify reconstructed cells match intent before cutover.
4. Pre-cutover anchor tag exists on develop:
   ```bash
   git tag lane-protocol-v0.1-pre-cutover <commit-sha>
   git push origin lane-protocol-v0.1-pre-cutover
   ```

### Running cutover

```bash
bash scripts/regenerate-memory-index.sh --in-place
```

What happens:
- Auto-creates `<canonical>.pre-cutover.bak` for each canonical file (only on
  first invocation; subsequent runs preserve the original snapshot).
- Inserts HTML-comment marker block immediately after table header / heading:
  - `<!-- BEGIN: scripts/regenerate-memory-index.sh --in-place (Issue #330 Phase F.B). Regenerated content — DO NOT edit between markers. -->`
  - `<!-- END: scripts/regenerate-memory-index.sh --in-place -->`
- Replaces SESSION_INDEX.md table body with sorted rows from per-session files
  (ascending by session number, lane=main first within tie).
- Replaces MEMORY.md `## Project (Session History)` content with bullets
  sorted descending (latest first), each linking to the per-session file.
- Markdown table cells emit-escape `|` → `\|` so YAML scalars containing
  literal `|` survive cleanly.

### Idempotency

Running `--in-place` repeatedly produces byte-identical output. The script
pre-detects marker presence: if markers exist, marker-bounded replacement;
if not, first-run insertion after table-header / heading.

### Discipline gate (until Phase F.C lands)

Direct edits between markers will be lost on next regenerate. Until Phase F.C
(#331) deploys hooks at `~/.claude/hooks/` to mechanically enforce this,
operators MUST `bash scripts/regenerate-memory-index.sh --in-place` before
commit/handoff to refresh canonical files from per-session sources.

### Mutual exclusion

`--in-place` is mutually exclusive with `--output` and `--format diff`:
- `--in-place + --output` → exit 2 (operators uncertain about target file)
- `--in-place + --format diff` → exit 2 (cutover is not a diff operation)

## Phase F.C (`~/.claude/hooks/`) — staged

Phase F.C deploys two Claude Code hooks under
`${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks/` to mechanically enforce the F.B
discipline gate. Source-of-truth scripts live in this repo at
`scripts/hooks/`; deployment to the user-level hooks dir is operational, not
part of the in-repo PR.

| Hook | Event | Behavior |
|------|-------|----------|
| `mercury-memory-index-write-guard.py` | PreToolUse (matcher `Edit\|Write`) | Returns `permissionDecision: "deny"` when target is canonical `MEMORY.md` / `SESSION_INDEX.md` AND tool is `Write` (full overwrite always blocked) OR tool is `Edit` AND `old_string` falls inside the BEGIN/END marker region. Fail-open posture: no markers found OR file unreadable → allow (operator can re-run `--in-place` to restore markers). |
| `mercury-memory-index-validator.py` | SessionEnd (matcher `""`) | Runs `regenerate-memory-index.sh --format diff` against the configured memory dir; if drift detected (script exit 1), surfaces a stderr warning to the user. Surfaces a separate stderr notice on `subprocess.TimeoutExpired` (30s). Cannot block — SessionEnd hooks are observability-only per Claude Code contract. |

### Soft-disable env vars

| Env var | Effect |
|---------|--------|
| `MERCURY_INDEX_GUARD_DISABLED=1` | Short-circuits the PreToolUse hook (allows all edits). Use for emergency manual canonical edits. |
| `MERCURY_INDEX_REGENERATE=1` | Allows the PreToolUse hook (defense-in-depth marker — auto-set by `regenerate-memory-index.sh`). |
| `MERCURY_INDEX_VALIDATOR_DISABLED=1` | No-ops the SessionEnd validator. |
| `MERCURY_REPO_ROOT=<path>` | Mercury repo root used by validator. **Required for deployed hooks** (`~/.claude/hooks/`) — there is no machine-specific fallback. In-repo invocation auto-resolves via `__file__.parents[2]`. If unset and auto-resolution fails → silent no-op. |
| `MERCURY_MEMORY_DIR=<path>` | Override user-memory dir consumed by both hooks (defaults to `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/D--Mercury-Mercury/memory`). |

### Deployment (per #259 user-level governance pattern)

```bash
# 0. Pre-conditions: F.B 1-week soak passed; #331 PR merged.

# 1. Backup settings.json
CC="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
cp "$CC/settings.json" "$CC/settings.json.backup-pre-331"

# 2. Copy hook scripts
cp scripts/hooks/mercury-memory-index-write-guard.py "$CC/hooks/"
cp scripts/hooks/mercury-memory-index-validator.py   "$CC/hooks/"
chmod +x "$CC/hooks/mercury-memory-index-write-guard.py" \
         "$CC/hooks/mercury-memory-index-validator.py"

# 3. Set MERCURY_REPO_ROOT in settings.json env block (required when hooks
#    live outside the repo — no hardcoded fallback). Use placeholder syntax
#    appropriate to your environment:
#
#    "env": {
#      "MERCURY_REPO_ROOT": "<absolute path to your local Mercury repo>",
#      ...existing keys...
#    }
#
#    Examples (do NOT copy verbatim — substitute your own checkout path):
#      Windows:  "MERCURY_REPO_ROOT": "C:\\path\\to\\Mercury"
#      Unix:     "MERCURY_REPO_ROOT": "/path/to/Mercury"
#
# 4. Register in settings.json hooks block — add to PreToolUse and SessionEnd
#    arrays (preserve existing entries):
#
#    "PreToolUse": [
#      ...existing...
#      {
#        "matcher": "Edit|Write",
#        "hooks": [{
#          "type": "command",
#          "command": "\"${CLAUDE_CONFIG_DIR:-$HOME/.claude}/.venv/Scripts/pythonw.exe\" \"${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks/mercury-memory-index-write-guard.py\"",
#          "timeout": 5
#        }]
#      }
#    ],
#    "SessionEnd": [
#      ...existing...
#      {
#        "hooks": [{
#          "type": "command",
#          "command": "\"${CLAUDE_CONFIG_DIR:-$HOME/.claude}/.venv/Scripts/pythonw.exe\" \"${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks/mercury-memory-index-validator.py\"",
#          "timeout": 30
#        }]
#      }
#    ]

# 5. Verify settings.json JSON validity
python -c "import json,os; json.load(open(os.path.expandvars('${CLAUDE_CONFIG_DIR:-$HOME/.claude}/settings.json')))"

# 6. Smoke-test: synthetic stdin must produce expected decision
printf '{"tool_name":"Write","tool_input":{"file_path":"%s/MEMORY.md","content":"x"}}' \
  "$CC/projects/D--Mercury-Mercury/memory" \
  | "$CC/.venv/Scripts/pythonw.exe" "$CC/hooks/mercury-memory-index-write-guard.py"
# Expected: JSON with "permissionDecision": "deny"
```

### Rollback (Phase F.C)

Two channels:

1. **Soft-disable** (instant, no redeploy): set
   `MERCURY_INDEX_GUARD_DISABLED=1` and/or `MERCURY_INDEX_VALIDATOR_DISABLED=1`
   in `~/.claude/settings.json` `env` block. Hooks short-circuit on next
   invocation.
2. **Full removal**: restore `settings.json.backup-pre-331` and delete the two
   hook files. Reverts to F.B discipline-gate model.

### Verification (per #259)

Required before closing #331:

- [ ] `~/.claude/settings.json` JSON valid post-deployment
- [ ] PreToolUse synthetic stdin: `permissionDecision: deny` for canonical Write
- [ ] PreToolUse synthetic stdin: empty output for canonical Write with
      `MERCURY_INDEX_GUARD_DISABLED=1` set
- [ ] SessionEnd synthetic run: stderr drift warning when memory dir has drift
- [ ] Real session edits a per-session file → MEMORY.md regenerate refreshes
      index → no hook noise on clean state
- [ ] Repo-side test suites: `bash scripts/test-mercury-memory-index-write-guard.sh` and `bash scripts/test-mercury-memory-index-validator.sh` both exit 0
- [ ] Verification clean for ≥3 real sessions

## Rollback procedure (Phase F.B)

The cutover has **three rollback channels** for distinct failure modes — git-side (Channel 1), user-memory side (Channel 2), and combined (Channel 3).

### Channel 1: Git-side (script/doc regression)

If the cutover commits introduce script bugs or doc errors but the canonical
files in user-memory are still intact:

```bash
git reset --hard lane-protocol-v0.1-pre-cutover
git push --force-with-lease origin <branch>  # if branch already pushed
```

This reverts repo state to pre-cutover. User-memory files (NOT in git) are
untouched — operator restores those separately if needed via Channel 2.

### Channel 2: User-memory side (canonical file drift)

If the cutover leaves canonical `MEMORY.md` or `SESSION_INDEX.md` in unexpected
state (e.g. stray rows, missing markers, content drift):

```bash
MEM_DIR="${MERCURY_MEMORY_DIR:-${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/D--Mercury-Mercury/memory}"
cp "$MEM_DIR/SESSION_INDEX.md.pre-cutover.bak" "$MEM_DIR/SESSION_INDEX.md"
cp "$MEM_DIR/MEMORY.md.pre-cutover.bak"        "$MEM_DIR/MEMORY.md"
```

Backups are created automatically on first `--in-place` invocation and never
overwritten on subsequent runs (preserves the true pre-cutover snapshot).

### Channel 3: Combined (full revert)

If both repo AND user-memory need rollback:

1. Run Channel 2 first (restore canonical files from `.bak`).
2. Run Channel 1 second (revert repo to tag).
3. Verify regenerate works against pre-cutover state:
   ```bash
   bash scripts/regenerate-memory-index.sh --format diff
   # Should report "no drift" against the original INDEX.generated.md from F.A soak
   ```
4. If verification fails, file new Issue with reproduction; per-session files
   under `memory/sessions/` may need to be deleted to fully restore F.A baseline.

### Verifying rollback

After any rollback channel:
```bash
# Confirm canonical files no longer have marker blocks (pre-cutover state)
grep -c 'BEGIN: scripts/regenerate-memory-index.sh --in-place' \
  "$MEM_DIR/SESSION_INDEX.md" \
  "$MEM_DIR/MEMORY.md"
# Expected: both report 0
```

If the count is non-zero, Channel 2 was not invoked or the backup is itself
mutated. Check `.bak` file mtime against original cutover commit timestamp.

## Tests

```bash
scripts/test-regenerate-memory-index.sh           # F.A — 44 cases / 82 assertions
scripts/test-regenerate-memory-index-in-place.sh  # F.B — 14 cases / 54 assertions
scripts/test-mercury-memory-index-write-guard.sh  # F.C — 19 cases / 19 assertions
scripts/test-mercury-memory-index-validator.sh    # F.C — 19 cases / 19 assertions
```

F.A coverage: arg validation, sort ordering (lane suffix variants, range rows),
source precedence (per-session file overrides existing row), malformed-frontmatter
detection, missing-required-field detection, YAML block-scalar rejection,
idempotency (frozen timestamp), custom output paths, env-var resolution, diff
mode (no drift / drift / no existing snapshot / unfrozen timestamp ignored),
pipe-character corruption WARN, duplicate-row dedup, non-session-filename skip,
symlink skip (env-aware), I/O failure detection, frontmatter sanitization,
indented-verbatim preservation, ASCII-vs-em-dash separator preservation,
empty-line-in-section preservation, hostile content preservation, and empty
SESSION_INDEX.

F.C write-guard coverage: empty/malformed stdin → silent allow; non-Edit/Write
tools → silent allow; Edit on non-canonical paths → silent allow; Write on
canonical MEMORY.md / SESSION_INDEX.md → deny with reason; Edit with
`old_string` inside marker region → deny; Edit with `old_string` outside
marker region → silent allow; Edit on canonical file with no markers (pre-cutover
or post-rollback) → fail-open allow; Edit on missing/unreadable canonical file
→ fail-open allow; soft-disable via `MERCURY_INDEX_GUARD_DISABLED=1`
or `MERCURY_INDEX_REGENERATE=1` → silent allow even on canonical Write;
basename-matches-but-wrong-parent-dir → silent allow; mixed-form Windows paths
+ dot-segment paths → resolved consistently.

F.C validator coverage: regenerate exit 0 (clean) → no warning; exit 1 (drift)
→ stderr warning with session_id, regen exit code, stderr/stdout tails, fix
hint; `MERCURY_INDEX_VALIDATOR_DISABLED=1` → silent no-op even on drift;
malformed/empty stdin → silent no-op; missing repo root → silent no-op;
payload top-level not a dict → unknown placeholder, no crash.

Tests use synthetic memory dirs only — never touch real user-memory layer.

## Forward path

Status of subsequent migration phases:

- **#330 (Phase F.B)** — DONE (S9-side-multi-lane 2026-04-27). `--in-place`
  flag landed; `migrate-session-index-to-files.sh` helper landed; 75 per-session
  files created; canonical files cut over via marker-bounded splice; pre-cutover
  anchor `lane-protocol-v0.1-pre-cutover` tagged on develop.
- **#331 (Phase F.C)** — staged. Source-of-truth scripts live at
  `scripts/hooks/mercury-memory-index-write-guard.py` (PreToolUse) and
  `scripts/hooks/mercury-memory-index-validator.py` (SessionEnd). 38 test
  assertions across two harnesses pass. Deployment to
  `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks/` (per #259 user-level governance
  model) is operational and gated on F.B 1-week soak passing — see §Phase F.C
  for deployment commands and verification checklist.

## Source references

- Issue [#315](https://github.com/392fyc/Mercury/issues/315) — Δ5 epic (Rule 7 REPLACE)
- Issue [#329](https://github.com/392fyc/Mercury/issues/329) — Phase F.A acceptance criteria (this script)
- Issue [#330](https://github.com/392fyc/Mercury/issues/330) — Phase F.B cutover (precondition: F.A soak passes)
- Issue [#331](https://github.com/392fyc/Mercury/issues/331) — Phase F.C lock-in hooks (precondition: F.B stable)
- [v0.1 Delta companion §Δ5](../lane-protocol-v0.1-deltas.md#delta-5--rule-7-replacement-p0-breaking)
- [Multi-lane protocol v0 MVP validation report](../research/multi-lane-protocol-2026-04-25.md) §Delta 5
- [How we solved GitLab's CHANGELOG conflict crisis](https://about.gitlab.com/blog/2018/07/03/solving-gitlabs-changelog-conflict-crisis/)
- [git-merge-changelog driver](https://manpages.debian.org/testing/git-merge-changelog/git-merge-changelog.1.en.html)
