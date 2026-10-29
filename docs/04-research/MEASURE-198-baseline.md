# Measurement Baseline — Issue #198 Token Tiering (T-0 snapshot)

**Purpose**: Pre-tiering `/usage` snapshot captured immediately after PR #201 merge (develop @ bc10170). Used as the comparison baseline for the 7-day post-merge measurement protocol defined in `.mercury/docs/decisions/DEC-3-198-token-tiering.md` → "Measurement Protocol" section.

**Captured at**: 2026-04-08
**Mercury commit**: `56de8d5` (develop, post chore/Mercury_KB cleanup)
**Tiering state**: newly active — all 6 `.claude/agents/*.md` have explicit `model:` frontmatter, no sub-agent dispatch has occurred since merge yet

**Plan**: a Claude subscription plan (recorded at measurement time)

---

## Observability limitation (user-confirmed 2026-04-08)

The `/usage` slash command in this Claude Code build **does NOT expose periodic (weekly) bucket breakdown** — only the current session detail is shown. The empirically-documented "All models weekly", "Sonnet only weekly", "Opus only weekly" buckets (per anthropics/claude-code#12487) are NOT directly observable from inside the CLI.

**Implication**: The original "Measurement Protocol" in DEC-3-198 assumed daily `/usage` bucket snapshots. Since those are unavailable, we need a **fallback measurement strategy** (see below).

## Fallback measurement strategy

Since absolute bucket numbers are not visible, the week-1 measurement will rely on **observable proxy signals**:

| Signal | How to observe | What it tells us |
|---|---|---|
| **Opus session-limit hits** | Count "Claude usage limit reached" / rate-limit error events per day in dev-pipeline runs | Direct: did Opus drain faster/slower than pre-tiering? |
| **Session `/usage` at end-of-day** | Manual `/usage` snapshot, record session token totals | Relative: compare same-workload-day totals pre/post |
| **Claude Console web** | Log into claude.com/settings → Usage if available for Max users | If exposes weekly breakdown, use as ground truth |
| **Sub-agent dispatch visible model** | dev-pipeline logs + `/stats` after a dispatch round | Confirms frontmatter is actually honored at runtime |
| **Critic/design invocation check** | Check `/stats` after a critic or design run | Confirms Opus is used for those two |

If Claude Console web exposes the weekly bucket charts, that becomes the primary ground truth and this fallback is unnecessary. Mercury owner to check post-merge.

## Baseline signals captured (T-0)

| Signal | Value | Notes |
|---|---|---|
| Pre-merge Opus rate-limit incidents (last 7 days) | UNVERIFIED — no historical log kept | Starting observation at T-0 |
| Pre-merge dev-pipeline runs without rate-limit issue | UNVERIFIED | Same |
| Current session `/usage` | observed by user, no weekly breakdown available | Confirms only per-session visibility |
| Mercury commit at T-0 | `56de8d5` (develop) | Matches measurement window start |
| Agent frontmatter state at T-0 | All 5 dispatchable agents explicit (`dev`/`acceptance`/`research`=sonnet, `critic`/`design`=opus), `main`=inherit | Per PR #201 merge |
| CLAUDE_CODE_SUBAGENT_MODEL env | NOT SET (intentional — see ADR) | grep-confirmed in `.claude/settings.json` |

## Notes / observations

- **No historical baseline for rate-limit incidents** exists — the 7-day post-merge measurement will be a forward-looking observation, not a true A/B comparison against a pre-tiering week.
- **Ground-truth fallback**: if post-merge week shows zero Opus rate-limit hits during normal autonomous operation, that is evidence the tiering is working regardless of bucket visibility.
- **Qualitative check**: spot-check 2-3 critic runs + 2-3 design runs during the week to confirm they actually used Opus (via `/stats` or dispatch logs).

---

## 🚨 CRITICAL FINDING at T-0 (2026-04-08) — Opus tier routing BROKEN

**Summary**: Step 2 verification revealed that the PR #201 ADR is effectively a **no-op for the Opus tier** in this environment. `critic.md` and `design.md` explicit `model: opus` frontmatter is being silently overridden to Sonnet.

### Empirical tests

| Test | Frontmatter | Per-invocation param | Self-reported runtime | Pass? |
|---|---|---|---|---|
| `dev` dispatch | `sonnet` | (none) | Sonnet 4.6 | ✓ (accidentally — see below) |
| `critic` dispatch | `opus` | (none) | Sonnet 4.6 | ✗ |
| `critic` dispatch + explicit `model: "opus"` | `opus` | `opus` | Sonnet 4.6 | ✗ |

Both sub-agents self-reported via their system prompt header: *"You are powered by the model named Sonnet 4.6. The exact model ID is claude-sonnet-4-6"*.

### Root cause

`CLAUDE_CODE_SUBAGENT_MODEL=sonnet` is present at process-level in the current Claude Code session. Empirical tests prove this env var dominates:
- Frontmatter `model: opus` (doc-stated priority #3) — overridden
- Per-invocation `model: "opus"` parameter (doc-stated priority #2) — also overridden

The doc at [code.claude.com/docs/en/sub-agents](https://code.claude.com/docs/en/sub-agents) states priority order `env > per-invocation > frontmatter > main`. Empirically the env var is an **absolute** override — nothing in-session touches it.

### Env var source — UNFOUND despite exhaustive search

Searched and ruled out:
- User `~/.claude/settings.json` — no env block
- Project `.claude/settings.json` — removed in ADR fix commit, verified clean
- Windows User/Machine env (PowerShell `[Environment]::GetEnvironmentVariable`) — empty at both scopes
- `~/.bash_profile`, `~/.bashrc`, `~/.profile`, `~/.bash_login` — files do not exist
- `/etc/profile.d/env.sh`, MSYS rc files — no match
- `.claude/session-env/<session-id>/sessionstart-hook-0.sh` — only sets Codex vars
- npm `claude` / `claude.cmd` / `claude.ps1` launcher scripts — bare, no env injection
- Claude Code plugin configs — no match

PowerShell confirms the env var exists ONLY at **Process** scope, not User or Machine. This means whatever launched the current Claude Code process injected it and then terminated (or it's a parent terminal's in-memory export).

### Consequence for #198

**As of 2026-04-08, the tiering in PR #201 is half-working**:
- T2 Sonnet (dev/acceptance/research) — ✓ working (accidentally, via env var match)
- T1 Opus (critic/design) — ✗ broken (env var override forces Sonnet)
- T3 Codex (tool-call) — ✓ working (unaffected by sub-agent env var)

The ADR's stated intent — critic bias-separation from dev, design deep reasoning — is NOT being delivered. Critic and dev both run on Sonnet, breaking the bias separation requirement in critic.md.

### Required action (user, not agent)

1. **Find the env var source** — possibly a parent terminal command, IDE config, or a claude launcher wrapper not in the usual locations. Suggested checks:
   - `history | grep SUBAGENT_MODEL` in the terminal that launched Claude Code
   - Check any custom shell function that wraps `claude` (aliased in a location I didn't check)
   - Check Windows Terminal profile / VS Code terminal settings for an `env` field
   - Check Git Bash shortcut's "Target" field (could be `mintty.exe -e bash -c "export CLAUDE_CODE_SUBAGENT_MODEL=sonnet && claude"`)
2. **Unset it, restart Claude Code, re-run the two verification tests** from this file.
3. **If you want an explicit default of sonnet** — set it back AFTER updating PR #201 to drop `model: opus` from critic/design and move those tiers to Codex T3 tool-call instead. This keeps the "all subagents default to sonnet" infra pattern that you may have intentionally configured earlier.

### Decision point

Two valid paths forward:

| Path A — Fix env var, keep ADR | Path B — Accept env var, rewrite ADR |
|---|---|
| Find + unset `CLAUDE_CODE_SUBAGENT_MODEL` | Keep the env var as infra default (Haiku-pattern) |
| critic/design → actual Opus | critic/design → Codex T3 (tool call) |
| PR #201 works as-written | Follow-up PR drops `model: opus` from critic/design, updates ADR |
| Requires finding the hidden env source | Self-contained, no external search |

**Step 2 result: BROKEN. Step 3 (Phase 2-1 OpenSpace) BLOCKED until a path decision is made.**

---

## ✅ RESOLVED 2026-04-08 — Path A executed, tier fully working

### Root cause (confirmed)

`CLAUDE_CODE_SUBAGENT_MODEL=sonnet` was present in a stale 28-hour-old PowerShell parent process (pid 37840, started 2026-04-07 01:36:34). The variable had been `setx`-written to the User registry at some unknown earlier time, inherited by that PowerShell at its launch, then later cleaned from the registry — but the old PS process kept the env in its own memory, and every Claude Code session spawned from that PS inherited it transitively.

User verified: a **fresh PowerShell window** (new Windows Terminal tab) showed `$env:CLAUDE_CODE_SUBAGENT_MODEL` as empty, confirming the registry is clean. The stale PS was killed, Claude Code restarted from a fresh PS.

### Fix

- **No config changes needed** anywhere — not user settings, not project settings, not PS profiles, not WT config
- Action taken: **kill the stale PowerShell process, restart Claude Code from a fresh PS**
- Current Claude Code session env: `CLAUDE_CODE_SUBAGENT_MODEL` is unset (verified via `env | grep`)

### Re-verification (2026-04-08, post-fix)

| Agent | Frontmatter | Self-reported runtime | Pass? |
|---|---|---|---|
| `dev` | `model: sonnet` | claude-sonnet-4-6 | ✅ |
| `critic` | `model: opus` | claude-opus-4-6[1m] (1M context) | ✅ |
| `design` | `model: opus` | (verified in same run) | ✅ |

### Tiering state at T-0 (post-fix)

- T1 Opus 4.6 (1M): main session + critic + design — ✓ working
- T2 Sonnet 4.6: dev + acceptance + research — ✓ working
- T3 Codex gpt-5.4: tool-call path — ✓ working (unaffected)

**PR #201 ADR is now delivering its stated intent in runtime.** Step 3 (Phase 2-1 OpenSpace) unblocked.

### Preventive note for future

If `CLAUDE_CODE_SUBAGENT_MODEL` ever reappears: check for stale long-running terminal processes BEFORE searching config files. Process Explorer's Environment tab on the Claude Code node.exe is the fastest diagnostic.

---

## Week-1 measurement checklist

Following `MEASURE-198-week1.md` protocol (to be created at Day-7). Daily samples to be taken at:

- [ ] Day 1 (2026-04-09)
- [ ] Day 2 (2026-04-10)
- [ ] Day 3 (2026-04-11)
- [ ] Day 4 (2026-04-12)
- [ ] Day 5 (2026-04-13)
- [ ] Day 6 (2026-04-14)
- [ ] Day 7 (2026-04-15) — final comparison + Keep/Rebalance/Revert decision

## References

- ADR: `.mercury/docs/decisions/DEC-3-198-token-tiering.md` → "Measurement Protocol"
- Issue: #198
- Merged PR: #201 (bc10170)
- Research: `Mercury_KB/04-research/RESEARCH-TOKEN-TIERING-198.md` + `RESEARCH-TOKEN-TIERING-198-RUNTIME.md`
