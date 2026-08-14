---
name: dual-verify
description: |
  Mercury 强制的提交前审查门。在 Codex 上，两路 = 两个独立 spawn 的 subagent 以不同 model_reasoning_effort 盲审同一改动再比对结论；单一 agent 上下文内自审不算。用它，不要用 /code-review 或 /auto-verify。 Trigger: 'dual verify', 'dual-verify', 'parallel review', 'run dual verify', '双路验证', '并行review', '代码审查', 'review before commit'. Use before any PR creation or direct commit to protected branches.
user-invocable: true
allowed-tools: Bash, Read, Grep, Glob, Agent
---

# Dual-Verify

> **⚠️ 本文件下方的流程仍是 Claude Code 宿主时代的写法**（Claude 深审 + `scripts/codex-sync-audit.sh` 调 Codex），
> 在 Codex 作为唯一 harness 时不成立——那条路等于自己调自己。Codex 版的重写归 [#571](https://github.com/392fyc/Mercury/issues/571) 的 G4-2。
> 在重写落地之前，Codex 上执行本门的方式是：**spawn 两个独立 subagent，指定不同的 `model_reasoning_effort`，让它们互不可见地审查同一改动，然后比对两份结论**。
> 下方的检查清单（按关注点分工、结构化 finding、Critical/High 分级、fail-closed）仍然适用，只是执行者换成了那两个 subagent。

Run two independent blind reviews in parallel, then consolidate findings and mark review complete.

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
#    `mktemp -t` works on both GNU coreutils and BSD/macOS mktemp; bare `mktemp` is
#    not portable across the two (GNU defaults to /tmp/tmp.XXX, BSD requires a template).
PROMPT_FILE="$(mktemp -t dual-verify-codex.XXXXXX)"
cat > "$PROMPT_FILE" <<'EOF'
Audit branch <branch> vs <base>. Focus on: code style, edge cases,
error handling, metrics completeness, memory leak / cleanup on terminal paths,
Windows/PowerShell compat. TypeScript typecheck is not required (Claude side handles it).

SANDBOX NOTE: some harnesses permit only a narrow set of commands. These are
known to work and are enough to audit a diff — prefer them:
    git status --porcelain | git log -1 --oneline | git diff --stat
    git diff <base>...HEAD | git branch --show-current | cat <file> | ls <dir>
The following were refused on ONE specific harness (Windows app-server path).
That is an observation about that environment, not a general rule — they may
work fine on yours, so try them if you need them. Only if one is actually
refused, don't retry it:
    git rev-parse (any form) | git rev-list | git show-ref | git config --get
    git stash | git push | git commit | git checkout
    bash / bash -c (any argument, including ones with no git in them)
A refusal is not a reason to stop. Note which command was refused, continue with
whatever works, and report your findings. Do NOT conclude from a few refusals
that the sandbox blocks everything and abandon the audit.

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

### Why the prompt declares a command allowlist

Measured on Windows with codex-cli 0.129.0 going through the app-server path (Issue #554), across four probe rounds covering about twenty distinct commands. The ones named above were declined by the policy layer **before dispatch** — the job log shows `Command declined ... (exit -1)` one millisecond after `Running command`, versus 0.5–1.3s for commands that actually run. Refusal is deterministic for a given command: the same one issued twice was refused both times. That sample does not establish where the boundary sits in general, which is why the note above says "prefer these" rather than "only these exist".

The failure this prevents is not the refusal itself but what Codex concludes from it. Given a couple of refusals, Codex reports that the sandbox blocked everything and declines to audit at all — which is what made #554 look like "Codex cannot run any Bash in this repo". It can: in the one probe round that deliberately mixed both kinds, 7 of its 11 commands ran and returned real output, including the `git diff <base>...HEAD` this skill actually depends on. Telling it up front which commands work, and that a refusal is not a reason to stop, is what turns the audit from "unavailable" into "normal".

Upstream tracking: [openai/codex-plugin-cc#57](https://github.com/openai/codex-plugin-cc/issues/57) — as filed, the app-server's `thread/start` RPC does not accept `sandboxPermissions` and `config.toml` sandbox settings are ignored on that path, which is why this was not configurable away from Mercury's side.

**Don't take this section's word for any of it — re-measure instead.** It is cheap: ask Codex to issue one command from each list above and report the outcome verbatim — one command per tool call, so each outcome is attributable to a single command. A policy refusal is distinguishable from a real execution failure by its *shape*, not by the message: the job log (path is in the `result --json` payload under `.storedJob.logFile`) shows `Command declined … (exit -1)` about a millisecond after `Running command`, whereas anything that genuinely ran took 0.5–1.3s in the same log and reports a real exit code. If the refused list now runs, this note costs nothing and can be trimmed. If commands from the permitted list start failing, stop trusting the rest of this section and re-measure before acting on it.

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
