# Phase 4-3 Compact-Prevention — Research & Design

> **Status**: research synthesis (Mercury S60)
> **Date**: 2026-04-19
> **Issue**: #266
> **Mode**: C (technical research, per EXECUTION-PLAN.md)
> **Upstream research protocol**: WebSearch + WebFetch vs official `code.claude.com/docs`, cross-checked with anthropics/claude-code issues + existing `~/.claude/hooks/pre-compact.py` runtime behavior

---

## Executive Summary

Claude Code auto-compacts conversation context around ~83.5% window usage (buffer 33K on 200K, third-party observation, UNVERIFIED against official telemetry — see §Sources / Verification notes). The existing Mercury setup uses `PreCompact` + `SessionEnd` hooks only for **flush-to-memory** (knowledge salvage); it does **not** proactively terminate the session. Phase 4-3 goal is to **trigger `/handoff` before compaction destroys working context**, so the next session resumes from structured state rather than a summarization residue.

**Verdict**: adopt **Option B — Threshold-driven main-loop advisory + PreCompact fail-safe** (hybrid).

- The authoritative early signal is **`CLAUDE_AUTOCOMPACT_PCT_OVERRIDE`-reduced compact trigger + `PreCompact` block-decision + SessionStart(source=compact) continuity marker**. PreCompact **can** block (exit 2 / `{"decision":"block","reason":...}`) — this is the only guaranteed fail-safe.
- External threshold estimation (transcript byte size, token-count) is **lossy but cheap**; only the main agent itself can accurately gauge remaining context (MRCR + tool overhead unaccounted-for by hooks).
- Proactive handoff cannot be done *inside* a hook because hooks run sub-process with no authority to run `/handoff` in the same session — the hook can only *advise* (statusline / notification) or *block-with-message* (telling the agent to handoff next turn).

**Recommended rollout (lowest risk → full)**: Option B.1 (advisory only, monitor) → B.2 (PreCompact block with handoff instruction) → optional B.3 (auto-launch next session via handoff auto). No part of Option A (fully autonomous proactive handoff) is recommended for MVP; main-agent-in-the-loop is safer than side-channel trigger.

---

## 1. PreCompact / PostCompact Hook Capability Matrix

Source: <https://code.claude.com/docs/en/hooks> (fetched 2026-04-19). Cross-check: `~/.claude/hooks/pre-compact.py` current runtime behavior; anthropics/claude-code issues #3537, #14258, #15923, #17237, #32026, #40492.

| Capability | PreCompact | PostCompact | SessionStart(source=compact) | SessionEnd | InstructionsLoaded(load_reason=compact) |
|---|---|---|---|---|---|
| Fires when | Before compaction | After compaction completes, before Claude responds | When session resumes after compaction | On session termination | Instruction files reload after compaction |
| Matcher | `auto`\|`manual` | `auto`\|`manual` | `source: "compact"` | — | `load_reason: "compact"` |
| Payload | `session_id`, `transcript_path`, `cwd`, `permission_mode`, `hook_event_name`, `trigger: auto\|manual` | same as PreCompact | `source: "compact"`, plus session_id / transcript_path | session_id / source / transcript_path | `file_path`, `memory_type`, `load_reason: "compact"` |
| Can block compaction | **Yes** — exit 2 (stderr shown) or `{"decision":"block","reason":...}` | **No** — cannot prevent, advisory only | n/a | n/a | n/a |
| Can inject context into new session | No (compaction not yet run) | No (structured output supported but not guaranteed re-injection into compacted transcript; see issue #40492) | **Yes** — instruction-load path can emit `additionalContext` into the post-compact session | — | Yes (SessionStart-style re-injection during compact) |
| Runs in same process as main agent | No (subprocess, stdin JSON) | No | No | No | No |
| Can run `/handoff` | No — hooks are subprocess with no slash-command access | No | No | No | No |
| Observed runtime (Mercury today) | `pre-compact.py` extracts last 30 turns → `flush.py` async spawn → mem0 write | not registered | not registered | `session-end.py` does the same + session_chain DB write | not registered |

**Key implication**: the only way to *use the compact boundary as a gate* is PreCompact-block (exit 2 / decision=block). PostCompact only observes aftermath. SessionStart(compact) can inject recovery instructions *but* into an already-compacted context — strictly weaker.

**Cross-verified with local code**: `pre-compact.py:95-167` reads stdin JSON, extracts turns, forks `flush.py` with `CREATE_NO_WINDOW` on Windows. Never returns exit 2 → compaction always proceeds. This is by design (Phase 3 intent was salvage, not prevention).

### Sources

- <https://code.claude.com/docs/en/hooks>
- <https://github.com/anthropics/claude-code/issues/15923> (PreCompact hook request → shipped)
- <https://github.com/anthropics/claude-code/issues/40492> (PostCompact verification limits)
- <https://github.com/anthropics/claude-code/issues/17237> (consolidated PreCompact/PostCompact request)

---

## 2. Threshold Signal Sources — Evaluation

Goal: detect "approaching context limit" *before* compaction begins, from a position where Mercury can act (i.e. influence main agent or surface statusline).

### S1. `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` + PreCompact-block

**Mechanism**: lower `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` to e.g. `60` so auto-compact triggers at 60% usage (default ~83.5%). PreCompact(auto) then blocks with `decision: block`, reason = "Mercury: please run /handoff auto to preserve working context". Main agent sees stderr / block message next turn.

| Dimension | Eval |
|---|---|
| Accuracy | **Authoritative** — uses Anthropic's own compact trigger telemetry; no estimation error |
| Cost | Near-zero (env var + exit 2) |
| Risk | Med — lowering threshold wastes 20%+ budget if main agent ignores block; also blocks legitimate auto-compact permanently if agent never handoffs → session becomes unusable |
| Reversibility | High (env var + hook config both toggleable) |
| Implementation complexity | Low (~30 LOC hook edit + env var) |
| MRCR impact on Opus 4.7 | Handoff triggered earlier → longer effective working memory (MRCR −46 at 1M means early-session facts are already unreliable) |

**Verdict**: best signal for *fail-safe gating*; bad for *first-warning UX* (only fires once, right at threshold).

### S2. Transcript-file byte size on disk

**Mechanism**: poll `transcript_path` bytes on a statusline hook or cron; trigger advisory when > N MB.

| Dimension | Eval |
|---|---|
| Accuracy | **Low** — JSONL includes tool calls, tool results, thinking blocks; tool results bloat byte count without contributing to model context proportionally. Empirically transcript at compact-time ranges 2–15 MB (unverified sample) |
| Cost | Near-zero (stat syscall) |
| Risk | High false-positive rate → alert fatigue → ignored |
| Reversibility | High |
| Implementation complexity | Low (5 LOC statusline hook) |

**Verdict**: reject as primary signal; usable as trailing sanity check.

### S3. Token-count estimation (tokenizer on transcript)

**Mechanism**: statusline hook runs `tiktoken`-equivalent on recent transcript content → estimate tokens; alert when > 80% window.

| Dimension | Eval |
|---|---|
| Accuracy | **Medium** — no public Claude tokenizer; Anthropic recommends the count-tokens API endpoint. Local approximation (e.g. `tiktoken` cl100k_base) drifts ~10–20% from actual Claude tokenization |
| Cost | Medium — tokenizing 15MB on every statusline repaint is prohibitive; must batch or sample |
| Risk | Drift between estimate and real — compact may fire before warning |
| Reversibility | High |
| Implementation complexity | Med (tokenizer dependency; batching logic) |

**Verdict**: better than S2 but still an estimate; inferior to S1 which uses Anthropic's own trigger.

### S4. `remaining_percentage` statusline field

**Mechanism**: per search results, Claude Code exposes a `remaining_percentage` field (likely to statusline API). Hook/statusline reads it directly.

| Dimension | Eval |
|---|---|
| Accuracy | **Authoritative** (if accessible in hook payload) |
| Cost | Near-zero |
| Risk | **Unverified** — field documented in third-party blog ([claudefa.st](https://claudefa.st/blog/guide/mechanics/context-buffer-management)), **not** confirmed in official hook payload spec retrieved 2026-04-19. May be statusline-only. UNVERIFIED. |
| Reversibility | High |
| Implementation complexity | Low if field is in hook payload; high if we need to parse statusline JSON or scrape UI |

**Verdict**: **requires empirical verification before use**. If present in statusline JSON, preferred for advisory UX. Note S1 remains the fail-safe regardless.

### Signal ranking

1. **S1 (CLAUDE_AUTOCOMPACT_PCT_OVERRIDE + PreCompact block)** — authoritative gate, fires exactly at threshold
2. **S4 (`remaining_percentage`)** — authoritative advisory if exposed to hooks *(UNVERIFIED)*
3. **S3 (token estimate)** — approximate advisory, higher cost
4. **S2 (transcript bytes)** — sanity only

---

## 3. Proactive Handoff Trigger Strategies — 3 Candidates

### Option A — Fully autonomous proactive handoff (pure hook-driven)

**Design**: PreCompact hook blocks compaction AND spawns detached `claude -p "/handoff auto"` subprocess that runs `claude-handoff` skill against the current session (reads transcript_path, writes handoff doc, launches next `claude` session). Main agent never decides; the hook forces it.

| Dimension | Eval |
|---|---|
| Cost | **High** — full handoff skill in hook (adds ~30s latency at compact boundary); ongoing: second Claude instance spawn |
| Risk | **High** — hooks can't directly call slash commands; must emulate handoff doc generation externally. Loses per-session state that main agent held in-context. Race with compact trigger if blocking logic fails |
| Reversibility | **Medium** — hook toggleable but failed handoffs leave orphaned processes, split-brain session state |
| UX | User loses control over handoff content; no chance for last-turn decisions |

**Verdict**: rejected for MVP. Pure hook-side emulation is fragile and re-implements what the main agent does in-context.

### Option B — Threshold-driven main-loop advisory + PreCompact fail-safe (recommended)

**Design**: three sub-levels, enable incrementally.

- **B.1** — statusline hook reads S3 or S4 signal, prints `"CTX 78% — consider /handoff"` at ~75%. Main agent decides whether/when to run handoff. Informational only. *No blocking*.
- **B.2** — in addition, `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=75`, and PreCompact(auto) returns `{"decision":"block","reason":"Mercury compact-prevention: run /handoff:auto to persist working context before compaction"}`. Main agent sees block message, runs handoff next turn, then compaction either naturally triggers in next session or is no longer needed.
- **B.3** (optional later) — if user absent / agent ignores block 3× in a row, spawn a notification-only subprocess (no session spawn) via Notify Hub (Phase 5 dependency).

| Dimension | Eval |
|---|---|
| Cost | **Low** — B.1 alone is ~20 LOC statusline; B.2 adds ~15 LOC hook edit + 1 env var |
| Risk | **Low** — block reason is advisory; main agent retains authority; failure mode = fallback to normal compact (B.1 path) or repeated blocks until agent handoffs (B.2) |
| Reversibility | **High** — all pieces env-var / config-toggleable; graceful degradation |
| UX | Main agent + statusline collaboration feels native; user sees threshold in HUD |

**Verdict**: **recommended**. Stages risk; each layer independently useful.

### Option C — External watchdog process

**Design**: separate long-running Python daemon polls `~/.claude/projects/*/latest_session.jsonl`; when byte size exceeds N, writes a sentinel file that the SessionStart hook of next turn reads to inject `"run /handoff now"` via `additionalContext`.

| Dimension | Eval |
|---|---|
| Cost | **Medium** — separate daemon to maintain, auto-start via task-scheduler, Windows/Linux parity burden |
| Risk | **Medium** — indirection via sentinel file; race conditions between daemon poll and hook firing; SessionStart additionalContext path is actually SessionStart only, not useful mid-session |
| Reversibility | High |
| UX | Decoupled from main agent loop; can also monitor other signals (CPU, idle time) |

**Verdict**: over-engineered for Phase 4-3 core goal. Defer unless Phase 5 Notify Hub needs a watchdog anyway — then consolidate.

---

## 4. Recommendation

**Adopt Option B, rollout B.1 → B.2. Defer B.3 and Option C until Phase 5.**

Concrete rollout plan (to be split into implementation Issues):

### Issue #<next>: B.1 statusline advisory (MVP)

- Add `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks/statusline-context.py` that prints context-pressure indicator using **S4 if verified accessible**, else **S3** token-estimate with batching.
- Dry-run only; collect telemetry over 2 sessions.
- AcceptanceCriteria: indicator visible in statusline; no regression on existing statusline output.
- Effort: ~40 LOC + 1 session of validation.

### Issue #<next>: B.2 PreCompact block + lowered override

- Set `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=75` (configurable, user can disable).
- Extend `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks/pre-compact.py` to emit `{"decision":"block","reason":...}` *on first auto-trigger*, record block in `flush.log` with session_id + turn_count.
- Add escape-hatch: if block count for same session_id ≥ 2, let compact proceed (agent may be ignoring block intentionally).
- Record in user-level-changes Issue per CLAUDE.md governance.
- AcceptanceCriteria: (1) session near threshold shows block reason to main agent; (2) after one `/handoff:auto`, next session starts clean; (3) rollback = `unset CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` OR revert hook edit.
- Effort: ~30 LOC + 1 real-session verify.

### Issue #<next>: verify S4 (`remaining_percentage`) availability (prerequisite — hard gate)

Any implementation Issue (B.1 / B.2) that depends on an `UNVERIFIED` signal MUST NOT start before this prerequisite Issue closes. "Verify-before-depend" is a hardened acceptance criterion on B.1/B.2, not just a nice-to-have.

- **Empirical probe (redacted)**: write temp hook that records **only the top-level keys** of the PreCompact / SessionStart / statusline payload (not values). If a value must be sampled for type inference, redact it (hash or prefix-only) before writing. Store output in a permission-restricted temp file (mode `0600` or Windows equivalent), inspect, then delete on inspection completion. Do **not** commit payload samples — only the discovered key-list goes in the Issue.
  - Rationale: `session_id`, `transcript_path`, `cwd` are session-identifying metadata; `transcript_path` further points at conversation content. These must not land in a world-readable file or git history.
- **Decision gate**: if S4 present → use in B.1; else fall back to S3 estimator (implementation effort +15 LOC, may drop to S2 if tokenizer install is prohibitive).
- **Effort**: ~1 hour.
- **Acceptance on B.1/B.2 tickets (hard gate)**: "Prerequisite Issue (S4 verification) is CLOSED with a key-list of the signal field and verdict PRESENT/ABSENT" — listed as *blocking acceptance* in the implementation TaskBundle, not as a non-functional suggestion.

### TaskBundle skeleton (for S61+ if approved)

Path convention: every user-level path uses `${CLAUDE_CONFIG_DIR:-$HOME/.claude}`, matching the form used throughout Mercury `CLAUDE.md` "Related Repositories" section and the existing Mercury hook codebase (`pre-compact.py`, `session-end.py`). This is portable across POSIX and Windows (via `CLAUDE_CONFIG_DIR` explicit override), multi-user, and keeps TaskBundle scopes stable under CI / container execution. As a Mercury Phase 4-3 convention, **avoid hardcoding `~/.claude/...`** in TaskBundle scopes — the executor shells may not expand `~` correctly on Windows PowerShell, and hardcoding breaks CI and container scenarios. (Mercury `CLAUDE.md` does not state this prohibition verbatim, but uses the env-var form consistently and records it as the canonical shape for cross-platform user-level paths.)

```json
{
  "taskId": "phase-4-3-b1-statusline-advisory",
  "issue": "mercury#<B.1-issue>",
  "title": "Phase 4-3 B.1 — statusline context-pressure indicator",
  "context": "MVP advisory for compact-prevention; reads S4 (if verified via prerequisite Issue) else S3 tokenizer estimate; displays threshold warning in statusline. Non-blocking.",
  "definitionOfDone": [
    "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks/statusline-context.py present and registered in settings.json",
    "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/settings.json JSON valid; hook exits 0 on synthetic stdin",
    "indicator visible in at least one live session",
    "Issue body records 2 sessions of telemetry",
    "prerequisite S4-verification Issue is CLOSED before merge"
  ],
  "allowedWriteScope": [
    "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks/statusline-context.py",
    "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/settings.json"
  ],
  "readScope": [
    "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks/pre-compact.py",
    "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks/session-end.py",
    "docs/user-level-changes Issue for B.1"
  ],
  "acceptanceCriteria": [
    "no regression on existing statusline output",
    "sensitivity to S4 vs S3 documented in PR body",
    "blocking: prerequisite S4-verification Issue CLOSED with signal verdict (PRESENT|ABSENT)"
  ],
  "verifyCommands": [
    "python -c \"import json, os; base = os.environ.get('CLAUDE_CONFIG_DIR') or os.path.join(os.path.expanduser('~'), '.claude'); p = os.environ.get('CLAUDE_SETTINGS_PATH') or os.path.join(base, 'settings.json'); json.load(open(p, encoding='utf-8'))\"",
    "python -c \"import os; base = os.environ.get('CLAUDE_CONFIG_DIR') or os.path.join(os.path.expanduser('~'), '.claude'); p = os.path.join(base, 'hooks/statusline-context.py'); assert os.path.isfile(p), p\""
  ]
}
```

(B.2 TaskBundle drafted analogously; content deferred to post-approval of B.1. Same `${CLAUDE_CONFIG_DIR:-$HOME/.claude}` convention applies.)

---

## 5. Decision Trade-off Table

| Axis | Option A (auto handoff) | **Option B (hybrid advisory+block)** | Option C (external watchdog) |
|---|---|---|---|
| Implementation cost | High (skill in hook) | **Low → Medium (staged)** | Medium (daemon) |
| Risk of broken session | High | **Low** | Medium |
| Reversibility | Medium | **High** | High |
| MRCR-aware (early handoff) | Yes (aggressive) | **Yes (configurable threshold)** | Yes (aggressive) |
| Main agent retains authority | No | **Yes** | Partial |
| Fails safely if feature disabled | No | **Yes** | Yes |
| Depends on unverified API | No | Partial (S4 verification gate) | No |
| Recommended for MVP | ✗ | **✓** | Defer |

---

## 6. Open Questions / Follow-ups

1. **S4 verification** — does `remaining_percentage` appear in hook payload, statusline payload, or neither? Needs empirical probe (Issue prerequisite to B.1).
2. **Handoff trigger inside hook** — confirm that `{"decision":"block","reason":...}` reason text reaches main agent's *next turn context* (not just stderr log). If not, B.2 degrades to log-only; UX suffers. Suggest empirical test on a throwaway session: set override=50, make agent use context until block fires, check if reason text appears in main loop.
3. **Multi-session interaction** — Phase 4-2 worktree-per-task spawns multiple parallel sessions. Does each get its own threshold / block? Confirm `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` is per-process (env var inheritance says yes).
4. **SessionStart(source=compact) recovery path** — even with B.2 block, user may `/compact manual` and ignore block → post-compact session needs minimal recovery instructions. Register a SessionStart(source=compact) handler that loads handoff doc if one exists from current branch.
5. **Timing ladder** — empirical measure: at `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=75`, how many turns of runway between block fire and hard compact? Needed to size agent's "finish current thought then handoff" window.

---

## Sources

Primary (web-verified 2026-04-19):

- [Claude Code Hooks reference — code.claude.com/docs/en/hooks](https://code.claude.com/docs/en/hooks) — authoritative PreCompact/PostCompact/SessionStart/InstructionsLoaded spec
- [anthropics/claude-code issue #15923](https://github.com/anthropics/claude-code/issues/15923) — PreCompact hook origin
- [anthropics/claude-code issue #17237](https://github.com/anthropics/claude-code/issues/17237) — consolidated PreCompact/PostCompact request thread
- [anthropics/claude-code issue #40492](https://github.com/anthropics/claude-code/issues/40492) — PostCompact verification limits
- [anthropics/claude-code issue #3537](https://github.com/anthropics/claude-code/issues/3537) — legacy no-postCompact bug (now resolved)

Secondary (informative, cross-checked):

- [claudefa.st context-buffer-management](https://claudefa.st/blog/guide/mechanics/context-buffer-management) — source for 83.5% trigger / 33K buffer claim; `remaining_percentage` field mention (S4 — UNVERIFIED against official spec)
- [anthropics/claude-code issue #11819](https://github.com/anthropics/claude-code/issues/11819) — configurable threshold request
- [anthropics/claude-code issue #15719](https://github.com/anthropics/claude-code/issues/15719) — `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` env var
- [Yuanchang blog — Claude Code auto-memory and PreCompact](https://yuanchang.org/en/posts/claude-code-auto-memory-and-hooks/) — community usage pattern

Local source (Mercury baseline):

- `~/.claude/hooks/pre-compact.py` (runtime behavior: flush-to-memory only, no block)
- `~/.claude/hooks/session-end.py` (session_chain DB write, flush spawn)
- `.mercury/docs/EXECUTION-PLAN.md` §4-3 (Phase 4-3 scope)
- `.mercury/docs/research/phase4-1-session-continuity-adr-draft.md` (Phase 4-1 ADR, structural reference)
- `.mercury/docs/research/phase4-2-worktree-mount-eval.md` (structural reference)

### Verification notes

- `trigger: "auto" | "manual"` field — confirmed in `code.claude.com/docs/en/hooks`.
- `PreCompact` `{"decision": "block", "reason": ...}` — confirmed in `code.claude.com/docs/en/hooks`.
- `SessionStart` `source: "compact"` matcher — confirmed in `code.claude.com/docs/en/hooks`.
- `InstructionsLoaded` `load_reason: "compact"` — confirmed in `code.claude.com/docs/en/hooks`.
- Auto-compact `~83.5%` trigger / `33K` buffer — **third-party source only**, marked UNVERIFIED against official telemetry.
- `remaining_percentage` field — **third-party source only**, UNVERIFIED; B.1 implementation must probe first.
- `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` env var — GitHub issue thread confirms; **UNVERIFIED** against official env-var reference page (not yet located).
