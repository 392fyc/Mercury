---
name: dual-verify
description: |
  Run Claude Code deep-review and Codex code-audit in parallel, then consolidate findings. **This is the mandatory pre-commit review step per Mercury CLAUDE.md** — use this instead of /code-review or /auto-verify. Trigger: 'dual verify', 'dual-verify', 'parallel review', 'run dual verify', '双路验证', '并行review', '代码审查', 'review before commit'. Use before any PR creation or direct commit to protected branches.
user-invocable: true
allowed-tools: Bash, Read, Grep, Glob, Agent
---

# Dual-Verify

Run Claude Code deep review and Codex code audit in parallel, then consolidate findings and mark review complete.

## When

- Before marking any PR as ready for merge.
- As a replacement for single-agent /code-review.
- Whenever CLAUDE.md requires code review before commit.

## Division of responsibility

| Responsibility | Owner |
|----------------|-------|
| TypeScript `tsc --noEmit` | Claude Code |
| Architecture / logic / integration correctness | Claude Code |
| Code style / edge cases / error handling | Codex (sync audit wrapper) |
| Metrics completeness (all 4 paths wired) | Codex (sync audit wrapper) |
| Memory leak (Map cleanup on all terminal paths) | Codex (sync audit wrapper) |
| Windows/PowerShell compat | Codex (sync audit wrapper) |

Codex is invoked via `bash scripts/codex-sync-audit.sh` — direct CLI call into the codex-companion runtime. The wrapper dispatches the audit and **blocks until the verdict is ready or a timeout is hit**, returning structured stdout markers that this skill parses (per Issue #326). The legacy `codex:codex-rescue` subagent path is no longer used here: it returns asynchronously and previously caused dual-verify to silently fall back when the verdict failed to arrive in-thread.

## Step 1 — Launch parallel reviewers

The two reviewers can run in parallel: kick the Codex audit off via the Bash tool's `run_in_background: true` (or shell `&`), then perform the Claude-side deep review while it runs, then collect both verdicts.

**Claude Code deep review** (this session):

```bash
# Detect remote name. Most repos use `origin` but some use `upstream` or a custom name.
# Strategy: prefer `origin` if present (convention), else use the first configured remote.
REMOTE=""
if git remote get-url origin >/dev/null 2>&1; then
  REMOTE=origin
else
  REMOTE=$(git remote | head -n 1)
fi
if [ -z "$REMOTE" ]; then
  echo "ERROR: dual-verify could not detect a git remote. Configure one with 'git remote add origin <url>' and retry." >&2
  exit 1
fi

# Detect the base branch the current branch was cut from.
# Strategy: query the REMOTE (ls-remote) directly — no local refs, no gh context.
# Rationale for not using `gh repo view`: in fork / multi-remote setups, gh's current
# context may point at a different repo than $REMOTE, leading to a mismatched base
# branch. ls-remote is bound to the exact git remote we'll diff against, so there is
# no drift. The develop → main → master cascade covers effectively all real repos.
#
# Escape hatch: if you are in one of the rare repos with a custom default branch name
# (e.g. `trunk`, `stable`, `release`), set BASE_BRANCH_OVERRIDE in the env to skip the
# cascade entirely. This is the supported way to handle custom default branches
# without reintroducing the gh-vs-git-remote drift that iteration 4 removed.
BASE="${BASE_BRANCH_OVERRIDE:-}"
if [ -z "$BASE" ]; then
  REMOTE_REFS=$(git ls-remote --heads "$REMOTE" 2>/dev/null || true)
  if [ -z "$REMOTE_REFS" ]; then
    echo "ERROR: dual-verify failed to enumerate branches on remote '$REMOTE' — check network or credentials" >&2
    exit 1
  fi
  for candidate in develop main master; do
    if echo "$REMOTE_REFS" | grep -q "refs/heads/${candidate}\$"; then
      BASE="$candidate"; break
    fi
  done
fi
if [ -z "$BASE" ]; then
  echo "ERROR: dual-verify could not detect a base branch (tried develop/main/master on $REMOTE)." >&2
  echo "Set BASE_BRANCH_OVERRIDE=<your-base-branch> and retry." >&2
  exit 1
fi
# Fetch the base branch so $REMOTE/$BASE is populated even on shallow clones / minimal checkouts.
git fetch "$REMOTE" "$BASE" --quiet || {
  echo "ERROR: dual-verify failed to fetch ${REMOTE}/${BASE} — check network or branch name" >&2
  exit 1
}
git diff "${REMOTE}/${BASE}...HEAD" --stat
git diff "${REMOTE}/${BASE}...HEAD"
```

Check: language-appropriate correctness gates (e.g. `tsc --noEmit` for TypeScript, `pnpm lint`, `pytest --collect-only` for Python), logic correctness, integration points, schema compliance, missing branches in switch/if chains, resource leaks.

**Codex audit** (sync wrapper — synchronous CLI call, blocks until verdict ready):

```bash
# 1. Write the audit prompt to a temp file. Keep it concrete: list the diff scope,
#    explicit checks Codex should perform, and the expected output schema.
PROMPT_FILE="$(mktemp)"
cat > "$PROMPT_FILE" <<'EOF'
Audit branch <branch> vs <base>. Focus on: code style, edge cases,
error handling, metrics completeness, memory leak / cleanup on terminal paths,
Windows/PowerShell compat. TypeScript typecheck is not required (Claude side handles it).

Return:
  Critical: N  High: N  Medium: N  Low: N
  - <finding-1>
  - <finding-2>
  Overall: PASS | NEEDS-CHANGES
EOF

# 2. Dispatch synchronously. --read-only is the default (the wrapper is named *audit*).
#    Default --timeout 600 (10 min) and --poll-interval 15 are usually fine; tune for huge diffs.
bash scripts/codex-sync-audit.sh "$PROMPT_FILE" --timeout 600 --poll-interval 15
EXIT=$?
rm -f "$PROMPT_FILE"
```

Branch on the wrapper's exit code:

| Exit | Marker on stdout | Action |
|------|------------------|--------|
| `0` | `===CODEX-SYNC-AUDIT RESULT===` | Codex job ran to terminal `completed` (codex-companion's terminal-success status, per `lib/codex.mjs`). The verdict text is in the JSON payload at **`.storedJob.result.rawOutput`** (machine-readable) or `.storedJob.rendered` (display-formatted). Parse `rawOutput` for the `Critical: N  High: N ...` line + per-finding blocks + `Overall: PASS \| NEEDS-CHANGES`. Note: exit 0 means Codex *replied*, not that it approved — `NEEDS-CHANGES` also exits 0. |
| `1` | `===CODEX-SYNC-AUDIT FAILED===` | Codex job failed in a non-timeout way. Treat as `Codex: FAIL`; investigate stderr / re-fetch the result via the codex-companion CLI (see "Recovery commands" below) before merging. |
| `124` | `===CODEX-SYNC-AUDIT TIMEOUT===` | Wait budget exceeded. The job continues running in the codex job store; the orchestrator may re-fetch later via the codex-companion CLI (see "Recovery commands" below). The same `.storedJob.result.rawOutput` path applies once the job reaches `completed`. For this dual-verify pass, fall back to Claude-only and **disclose** "Dual-verify Codex side: TIMEOUT (jobId `<id>`)" in the PR body. |
| `130` | (cancellation message on stderr) | User interrupted (Ctrl+C). Wrapper attempted to cancel the codex job. Re-run when ready. |
| `3` | (none — message on stderr) | Codex CLI not installed / not authenticated, or codex-companion returned malformed JSON. Run `/codex:setup`. Fall back to Claude-only and disclose "Dual-verify Codex side: UNAVAILABLE" in PR body. |
| `2` | (usage error) | Bug in the dual-verify skill caller — fix the invocation, do not skip. |

> Note: `scripts/codex-sync-audit.sh` bypasses the `codex-rescue` subagent (saves ~25k tokens of forwarder boot per audit per Issue #326 Update 1). The codex-rescue subagent remains available for general "hand a long task to Codex" use cases — only dual-verify uses the direct sync path.

### Recovery commands

When the wrapper exits 1 or 124, the codex job is still in the codex-companion store and can be inspected by calling the companion CLI directly. The companion script lives at `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/marketplaces/openai-codex/plugins/codex/scripts/codex-companion.mjs` (or the equivalent `cache/openai-codex/codex/<version>/scripts/codex-companion.mjs` on a versioned install). Set a shell var to that path, then:

```bash
COMPANION="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/marketplaces/openai-codex/plugins/codex/scripts/codex-companion.mjs"
# Inspect the job snapshot:
node "$COMPANION" status <jobId> --json
# Once the job reaches `completed`, fetch the verdict payload:
node "$COMPANION" result <jobId> --json | jq '.storedJob.result.rawOutput'
```

If `$COMPANION` does not resolve, run `bash scripts/codex-sync-audit.sh --help` — the wrapper logs its discovered companion path on dispatch failure (exit 3) and accepts `CODEX_COMPANION_SCRIPT=<path>` as an env override.

## Step 2 — Collect results

Each reviewer produces:

```text
## <Reviewer> Review Results
Critical: N  High: N  Medium: N  Low: N
- <finding>
Overall: PASS | FAIL | NEEDS-CHANGES
```

## Step 3 — Cross-reference

Produce a consolidated report:

```text
## Dual-Verify Consolidated Report
Branch: <branch>
Claude: PASS | NEEDS-CHANGES
Codex:  PASS | NEEDS-CHANGES

Agreed Issues: <list or none>
Claude-only: <list or none>
Codex-only: <list or none>

Final Verdict: PASS | NEEDS-CHANGES
```

## Step 4 — Fix, verify, mark complete

1. Fix all Critical + High issues.
2. Run `auto-verify` (tsc --noEmit, scope, lint).
3. Set the review-passed flag:

```bash
mkdir -p .mercury/state && touch .mercury/state/review-passed
```

4. Commit and push.

## Evidence

```text
dual-verify: PASS (Claude: PASS, Codex: PASS, N issues fixed)
```

## Rules

- Both reviewers must return PASS before proceeding to merge.
- Fix before merge — do not proceed on a split verdict.
- Codex surfaces Windows-specific and platform concerns that may not be visible in Claude's review.

## Fallback

If `scripts/codex-sync-audit.sh` returns a non-success terminal state, fall back to Claude-only review and disclose the cause in the PR body. The PR body line MUST be one of:

- `Dual-verify Codex side: PASS` (exit 0)
- `Dual-verify Codex side: NEEDS-CHANGES` (exit 0 with findings; iterate)
- `Dual-verify Codex side: FAILED` (exit 1; investigate before retrying)
- `Dual-verify Codex side: TIMEOUT (jobId <id>)` (exit 124; the codex job continues — fetch later via `node "$COMPANION" result <jobId> --json`)
- `Dual-verify Codex side: UNAVAILABLE` (exit 3; Codex CLI not installed / not authenticated — run `/codex:setup`)

This fallback is acceptable for low-risk changes; high-risk PRs (orchestrator core, auth, schema changes) should re-run dual-verify once Codex is available rather than ship single-reviewer.
