# Codex-Rescue Async Semantics — Research & Decision

**Issue**: [#326](https://github.com/392fyc/Mercury/issues/326) — `feedback(codex-rescue): document async-forwarder semantics — main must poll for result`
**Lane**: `lane:main` (cross-lane impact, claimed via coordination Issue)
**Session**: S81 main lane, 2026-04-27
**Branch**: `feat/codex-rescue-async-doc`

## Problem

Mercury's `dual-verify` skill dispatches a Codex side audit via the `codex:codex-rescue` subagent. Empirical observations across S3/S4/S80 sessions:

| Session | Lane | Outcome |
|---------|------|---------|
| S3-side-multi-lane | side | 2 codex-rescue audits "hung > 8 min" → fell back to Claude-only |
| S4-side-multi-lane | side | 2 dispatches; 33,603 + 28,029 input tokens; **forwarder ack only, audit never returned** |
| S80 main | main | dispatch succeeded; subagent went silent for 2h+ at 13:15:04Z; output file mtime froze; no error/panic |

**Common pattern**: The `codex-rescue` subagent dispatches `codex-companion.mjs task ...` and returns the launch payload immediately. The actual audit runs asynchronously inside the codex-companion runtime. Mercury's dual-verify skill expected synchronous verdict-style output (matching the Claude-side critic), so it interpreted the missing verdict as a hang and fell back to single-reviewer mode.

**This is not a Codex stability issue.** It is a contract mismatch between:
- `codex-rescue` subagent — fire-and-forget forwarder, returns task id
- `dual-verify` skill — assumes sync verdict return (like the Claude-side critic)

S4 retrospective measured ~25k tokens of pure forwarder boot overhead per dispatch (loading three internal skills + system prompt before any real work). Compounded across 5+ commits per session, this becomes 125k+ tokens of forwarder cost with zero verdict delivery.

## Native CLI surface (verified 2026-04-27)

`codex-companion.mjs` (cached at `~/.claude/plugins/marketplaces/openai-codex/plugins/codex/scripts/codex-companion.mjs`) already exposes the synchronous primitives we need:

| Subcommand | Synchronous? | Use |
|------------|--------------|-----|
| `task` (foreground, default) | yes | runs codex inline; blocks until done |
| `task --background` | no | dispatches detached worker; returns `{jobId, ...}` payload |
| `status <jobId> --wait --timeout-ms N --poll-interval-ms M --json` | yes (with timeout) | polls until job in terminal state OR timeout; returns snapshot with `waitTimedOut` flag |
| `result <jobId> --json` | yes | fetches stored final payload (verdict, summary, findings, details, artifacts) for a finished job |

**Key insight**: `status --wait` is **already a built-in polling primitive** with native timeout support. We do not need to write a polling loop in shell — we just chain `task --background` + `status --wait` + `result`.

Verified by inspection of `codex-companion.mjs:293-309` (`waitForSingleJobSnapshot`):
```js
async function waitForSingleJobSnapshot(cwd, reference, options = {}) {
  const timeoutMs = Math.max(0, Number(options.timeoutMs) || DEFAULT_STATUS_WAIT_TIMEOUT_MS);
  const pollIntervalMs = Math.max(100, Number(options.pollIntervalMs) || DEFAULT_STATUS_POLL_INTERVAL_MS);
  const deadline = Date.now() + timeoutMs;
  let snapshot = buildSingleJobSnapshot(cwd, reference);
  while (isActiveJobStatus(snapshot.job.status) && Date.now() < deadline) {
    await sleep(Math.min(pollIntervalMs, Math.max(0, deadline - Date.now())));
    snapshot = buildSingleJobSnapshot(cwd, reference);
  }
  return { ...snapshot, waitTimedOut: isActiveJobStatus(snapshot.job.status), timeoutMs };
}
```

## Design space

User decision (Issue #326 Update 2, 2026-04-26): **retain Codex** in dual-verify because the different model architecture (Codex/GPT-5.x vs Claude) genuinely produces different findings. The OMC `code-reviewer`-replacement option is rejected; the fix must preserve Codex as the second reviewer while making its return contract synchronous from the orchestrator's perspective.

| Option | Approach | Verdict |
|--------|----------|---------|
| **A** | Document the async contract; main agent polls `/codex:status` manually after each codex-rescue dispatch | Insufficient — every dual-verify call still pays 25k token forwarder boot before the audit even starts; orchestrator burden remains |
| **B** | Replace `codex-rescue` with OMC `code-reviewer` agent | Rejected by user — loses cross-architecture perspective |
| **C** | Patch upstream `codex-rescue` agent to optionally block | Out-of-repo scope; would require oh-my-claudecode upstream PR; uncertain timeline |
| **D (chosen)** | Bypass subagent layer entirely; call `codex-companion.mjs` directly from a Mercury-local sync wrapper script | **chosen** |

## Decision: **Option D — direct CLI sync wrapper**

`scripts/codex-sync-audit.sh` chains the codex-companion native primitives:

```
task --background --json   [--write]   [--model …]   [--effort …]   --prompt-file <path>
  → captures jobId
status <jobId> --wait --timeout-ms (T*1000) --poll-interval-ms (P*1000) --json
  → blocks until terminal OR timeout
result <jobId> --json
  → returns final verdict payload (verdict text at .storedJob.result.rawOutput)
```

Read-only is the wrapper's default (it is named *audit*); `--write` is an explicit
opt-in passed through to `task`. Plugin discovery walks a precedence chain:
`CODEX_COMPANION_SCRIPT` → `CLAUDE_PLUGIN_ROOT` (only if set) → `$HOME` candidates
→ `$USERPROFILE` candidates (only if distinct from `$HOME`). On Git Bash on Windows,
`$HOME` and `$USERPROFILE` often resolve to different paths, so each is probed
independently rather than picking one.

### Why D over A

1. **Zero subagent overhead.** Direct CLI call skips the `codex-rescue` subagent boot (avoids ~25k token cost per audit, the dominant pain point per Issue Update 1).
2. **Native timeout discipline.** Codex-companion's `status --wait --timeout-ms` is the right tool — we use it instead of reimplementing in shell.
3. **Sync contract for the orchestrator.** dual-verify skill calls `bash scripts/codex-sync-audit.sh prompt.txt --timeout 600` and gets stdout containing the verdict (or a `TIMEOUT` marker with partial state). No polling discipline required from the orchestrator side.
4. **Preserves Codex/GPT-5.x as reviewer.** Per user override 2026-04-26, keeps cross-architecture diversity.
5. **Out-of-repo dependency is read-only.** We do not modify the codex plugin; we only call its public CLI.

### Why D over C

C requires upstream PR cycle (uncertain timeline, oh-my-claudecode acceptance), and the `codex-rescue` agent's "fire-and-forget forwarder" semantic is intentional per its design (it serves multiple use-cases beyond dual-verify, where async dispatch is correct). Forking that contract just for our use-case is wasteful when the underlying CLI already does what we need.

### Trade-offs accepted

- **Loses subagent context inheritance.** Direct CLI invocation does not see the subagent system prompt or skills (`codex-cli-runtime`, `gpt-5-4-prompting`). For dual-verify the prompt is fully constructed by the skill itself with explicit instructions, so this is not a problem.
- **Plugin path discovery.** The wrapper must locate `codex-companion.mjs` at runtime. Resolved via env-var precedence (`CODEX_COMPANION_SCRIPT` → `CLAUDE_PLUGIN_ROOT` → `${HOME}/.claude/plugins/marketplaces/openai-codex/plugins/codex/scripts/codex-companion.mjs`).
- **Less forgiving error path.** When the CLI is unavailable (Codex not set up), the wrapper exits nonzero with a clear "Codex unavailable, run /codex:setup" message; dual-verify skill falls back to Claude-only with this message recorded in PR body. Unchanged from current behavior.

## Implementation plan

### `scripts/codex-sync-audit.sh`

```
Usage: codex-sync-audit.sh <prompt-file> [options]

Options:
  --timeout SECONDS         total wait budget (default 600)
  --poll-interval SECONDS   polling interval (default 15)
  --model MODEL             codex model override (e.g. gpt-5.4-mini, spark)
  --effort EFFORT           reasoning effort (none|minimal|low|medium|high|xhigh)
  --read-only               omit --write (codex audit-only, no edits)
  --cwd PATH                workspace root (default: pwd)
  --dry-run                 print resolved commands; do not execute

Exit codes:
  0   succeeded — codex job reached terminal `completed`; verdict block on stdout
  1   codex job failed (non-timeout terminal failure) — failure JSON on stdout
  2   usage error (missing prompt file, bad arg)
  3   codex unavailable (CLI not found, jq missing, setup required, or JSON parse error)
  124 wait timed out — TIMEOUT marker + status snapshot on stdout (partial work preserved in codex job store)
  130 user interrupted (SIGINT/SIGTERM after dispatch — wrapper cancels codex job before exit)
```

Behaviour:
1. Resolve `codex-companion.mjs` path via env var precedence.
2. Validate prompt file exists, non-empty.
3. Dispatch `node codex-companion.mjs task --background --json --prompt-file <prompt>` with optional `--write`/`--model`/`--effort`.
4. Extract `jobId` from launch payload (jq).
5. Poll `node codex-companion.mjs status <jobId> --wait --timeout-ms <T> --poll-interval-ms <P> --json`.
6. Branch on `waitTimedOut` and `job.status` (codex-companion uses `completed` for terminal-success in `lib/codex.mjs`; `succeeded` is also accepted defensively):
   - `waitTimedOut === true` → emit `===CODEX-SYNC-AUDIT TIMEOUT===\n<status JSON>` on stdout, exit 124. The job continues running in the codex store; refetch later with `result --json`.
   - `job.status` in `{completed, succeeded}` → fetch `result --json`, validate `.storedJob.id` is present, emit `===CODEX-SYNC-AUDIT RESULT===\n<result JSON>` on stdout, exit 0.
   - `job.status` in `{failed, cancelled}` → emit `===CODEX-SYNC-AUDIT FAILED===\n<status JSON>` on stdout, exit 1.

An EXIT trap installed after `JOB_ID` is captured cancels the codex job on any
non-terminal exit path (jq parse failures, signals, unexpected status), so post-
dispatch failures do not leak orphan workers into the codex job store.

### `scripts/test-codex-sync-audit.sh`

Pure-shell tests (no real Codex needed):
1. Usage error: missing prompt file → exit 2.
2. Plugin discovery: env precedence honored.
3. Dispatch parsing: mock companion script returns canned launch JSON; wrapper extracts jobId correctly.
4. Wait branches: mock companion returns `completed` → exit 0; `failed` → exit 1; `waitTimedOut: true` → exit 124. Plus a malformed-result-JSON path → exit 3, and an EXIT-trap-cancels-orphan path validated by mock-side log inspection.
5. `--dry-run`: prints resolved commands; does not execute.

Mock approach: `CODEX_COMPANION_SCRIPT` env override pointed at a tiny `mock-companion.mjs` shipped alongside tests. Each subcommand returns deterministic canned JSON keyed by `--json` mode.

### `dual-verify` skill update

Replace the "Codex (rescue subagent)" section. New flow:
1. dual-verify skill writes the audit prompt to a temp file.
2. Calls `bash scripts/codex-sync-audit.sh <prompt-file> --timeout 600 --poll-interval 15 --read-only`.
3. Parses stdout. On exit 0, treat output as the Codex verdict. On exit 124, log timeout + fall back to Claude-only with PR-body disclosure. On exit 3, fall back same way with "Codex unavailable" disclosure.
4. PR body template adds an explicit "Dual-verify Codex side: PASS|TIMEOUT|UNAVAILABLE" line.

### `feedback_dual_verify_gate.md`

Add section: **Codex side returns synchronously via `scripts/codex-sync-audit.sh`** — sync wrapper handles polling with default 600s timeout. On timeout the tail is preserved in the codex job store; orchestrator may inspect via `node codex-companion.mjs result <jobId> --json` later for offline triage.

## Acceptance criteria mapping (per Issue #326 Update 2)

| Criterion | Implementation |
|-----------|----------------|
| `scripts/codex-sync-audit.sh` (or equivalent) exists | yes — `scripts/codex-sync-audit.sh` |
| dispatches codex-rescue + polls + sync verdict | yes — `task --background` + `status --wait` + `result --json` |
| Configurable timeout (default 600s) | yes — `--timeout SECONDS` arg, default 600 |
| On timeout, last available output + clear "TIMEOUT" marker | yes — exit 124 + `===CODEX-SYNC-AUDIT TIMEOUT===` marker + status JSON |
| dual-verify skill calls sync wrapper | yes — SKILL.md updated |
| PR body template documents new contract | yes — new "Dual-verify Codex side:" line |
| `feedback_dual_verify_gate.md` reflects new default | yes — memory updated |
| Token cost benchmark | will be recorded post-PR (this PR's own dual-verify is the benchmark) |

## Sources

- Issue #326 spec — <https://github.com/392fyc/Mercury/issues/326>
- `~/.claude/plugins/marketplaces/openai-codex/plugins/codex/scripts/codex-companion.mjs` — verified 2026-04-27 from `~/.claude/plugins/cache/openai-codex/codex/1.0.2/` (same content)
- S4-side-multi-lane handoff retrospective ("Codex CLI 不稳定" entry) — corrected by this analysis
- S80 main lane empirical: codex-rescue background process `bvcebb4wl` / agentId `aba2edb5a494364c0` — silent 2h+ post-13:15:04Z (per session-handoff.md Key Context)
- Mercury `feedback_dual_verify_gate.md` — current Codex-rescue contract documentation (will be updated)
- Codex agent definition — `~/.claude/plugins/marketplaces/openai-codex/plugins/codex/agents/codex-rescue.md` (forwarder-only contract, intentionally non-blocking)
