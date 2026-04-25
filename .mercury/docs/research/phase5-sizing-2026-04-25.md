# Phase 5 Notify Hub Sizing — Post-Routines Research

> Status: **Decision doc (main-agent output, S73)** | Date: 2026-04-25 | Lane: `main`
> Scope: Determine whether Claude Code Routines (2026-04-14 launch) shrinks Phase 5 MVP scope
> Inputs: Issue #289 body + `phase4-5-circular-dep-breakpoint-2026-04-24.md` §3.B + WebFetch of `code.claude.com/docs/en/routines` + `platform.claude.com/docs/en/api/claude-code/routines-fire`
> Out of scope: Claude Design (A-series scenarios); transport-vendor selection ADR (separate doc, needs user input)

---

## 1. Research Findings — Routines canonical reference

WebFetch 2026-04-25 against official Anthropic docs confirms the following facts. Per Mercury MANDATORY RESEARCH PROTOCOL, all claims below are cited to the URLs fetched unless marked UNVERIFIED.

### 1.1 Trigger surface

| Trigger | Minimum cadence | Where configurable | Constraints |
|---------|-----------------|--------------------|-|
| Scheduled | 1 hour | CLI `/schedule` + web | Custom cron via `/schedule update`; < 1h rejected |
| API (`/fire`) | No rate floor at request level | Web only (CLI cannot create/revoke tokens) | Per-routine bearer token `sk-ant-oat01-*`; fire-and-forget; no idempotency key |
| GitHub event | Subject to per-routine + per-account hourly caps during preview | Web only | Events = Pull Request + Release **only** (no Issues, no push, no workflow_run) |

**Combinable**: one routine can attach multiple triggers simultaneously.

### 1.2 API `/fire` endpoint — critical behavioral detail

```
POST https://api.anthropic.com/v1/claude_code/routines/{routine_id}/fire
Authorization: Bearer sk-ant-oat01-...
anthropic-beta: experimental-cc-routine-2026-04-01
anthropic-version: 2023-06-01
Content-Type: application/json
```

Body:
```json
{"text": "freeform ≤65536 chars, NOT parsed as JSON"}
```

Response (200):
```json
{
  "type": "routine_fire",
  "claude_code_session_id": "session_...",
  "claude_code_session_url": "https://claude.ai/code/session_..."
}
```

**Key constraints** (quoted from platform.claude.com):

- *"The request returns once the session is created. It does not stream session output or wait for the session to complete."* — **fire-and-forget**
- *"Each successful request creates a new session. There is no idempotency key."* — retries duplicate work
- `text` field is a literal string — JSON inside is not parsed, routine receives the raw characters
- Errors: 400 (bad beta header / >65k chars / paused routine), 401 (bad token), 404 (no routine), 429 (rate limit; honors `Retry-After`), 503 (overloaded)

### 1.3 GitHub trigger details

- Event categories: **Pull Request + Release only** (confirmed from docs table)
- PR filter fields: Author / Title / Body / Base branch / Head branch / Labels / Is draft / Is merged
- Filter operators: equals / contains / starts with / is one of / is not one of / matches regex (regex tests **whole field**, needs `.*x.*` for substring)
- *"Each matching GitHub event starts a new session. Session reuse across events is not available"*
- Claude GitHub App must be installed on the repo (separate from `/web-setup`)

### 1.4 Session runtime

- Runs as **full Claude Code cloud session** — *"no permission-mode picker and no approval prompts during a run"*
- Repositories cloned at start from **default branch** unless prompt says otherwise
- Can run shell commands, use **skills committed to the cloned repository** (*"use [skills](/en/skills) committed to the cloned repository"*), call connectors
- Identity is the account owner: *"commits and pull requests carry your GitHub user, and Slack messages, Linear tickets, or other connector actions use your linked accounts"*

### 1.5 Push / branch scope

- Default: push only to `claude/*` prefixed branches
- Toggle: **"Allow unrestricted branch pushes"** per-repository in the routine config
- Confirmed from docs: *"This prevents routines from accidentally modifying protected or long-lived branches. To remove this restriction for a specific repository, enable Allow unrestricted branch pushes for that repository."*

### 1.6 Versioning / breaking-change policy

*"Breaking changes ship behind new dated beta header versions, and the two previous header versions continue to work so that callers have time to migrate."*

→ **2-version deprecation window**. Mercury can pin `experimental-cc-routine-2026-04-01` safely until the 3rd-forward version ships.

### 1.7 Usage and quota

- Pro 5 / Max 15 / Team-Enterprise 25 routine runs per account per day (each triggered run)
- **One-off schedules are exempt from the daily cap** (draw from subscription usage only)
- Each run draws standard subscription usage (same as interactive session)
- Organizations with "extra usage" billing can overflow on metered overage

### 1.8 Connectors / transport surface

- All MCP connectors connected to the claude.ai account are included by default
- Connectors available: Slack, Linear, Google Drive, etc. (MCP ecosystem)
- **No native Telegram / ntfy / LINE / SMS transport** — only what MCP ecosystem provides
- Environment variables supplied via the cloud environment config (separate primitive)

### 1.9 Cloud environment primitive

- Controls network access level, env vars (for secrets), setup script (cached)
- Must be configured **before** creating the routine that uses it
- Default environment provided

### 1.10 Resolution of Issue #289 UNVERIFIED questions

| # | Question | Resolution |
|---|----------|------------|
| Q1 | Claude Design automation API? | **UNVERIFIED still** — Routines docs do not cover Claude Design. Separate research needed when user pursues A-series scenarios. No blocker for Phase 5 sizing. |
| Q2 | Routines honors `.claude/agents/*.md` + `.claude/skills/*/SKILL.md`? | **Skills: CONFIRMED** (*"use skills committed to the cloned repository"*). **Agents: UNVERIFIED** — not explicitly mentioned, but cloud runs are full Claude Code sessions so native CLI agent-loading should apply. Flag for Phase 2 PoC validation. |
| Q3 | Research preview breaking-change rhythm? | **CONFIRMED stable** — 2-version deprecation window via dated beta headers. |
| Q4 | `claude/*` branch restriction vs Mercury `feature/TASK-*`? | **CONFIRMED manageable** — per-repo toggle `Allow unrestricted branch pushes`. Enables Mercury convention without removing other safeguards. Decision: if Phase 2 PoC uses Routines on Mercury repo, flip the toggle on that repo only. |

---

## 2. Phase 5 Sizing Decision

### 2.1 The sizing question (per S73 handoff step 4)

> 若 Routines + GHA 已覆盖 70%+ "何时通知" 场景 → Phase 5 MVP 只做 "消息 outbound transport" 一层
> 若 Routines 仅适 scheduled reminder，Phase 5 仍需 full MVP (trigger + transport + callback)

### 2.2 Answer — **Phase 5 DOES NOT shrink**

The premise hides a layer confusion. Restating the two orthogonal Phase 5 concerns:

| Phase 5 concern | What it is | What Routines offers |
|-----------------|-----------|----------------------|
| **Trigger backend** ("when to notify") | Decide when to dispatch a cloud-side task | ✅ Scheduled + GitHub events + API |
| **Outbound transport** ("how the notify reaches the human") | Push a message from Mercury's local process to user's IM/device | ❌ Only via MCP connectors; no Telegram/ntfy/LINE direct |
| **Callback from cloud trigger** ("notify when the triggered task finishes") | Async completion handoff back to user | ❌ `/fire` is fire-and-forget, no webhook-back |

#### Why Routines cannot collapse Phase 5 MVP

1. **Routines is cloud-initiated. Mercury's biggest notify use case is local-session-initiated.** The three canonical Phase 5-3 consumers per EXECUTION-PLAN.md L355-357 are:
   - Session Continuity session-switch notify → fires from **local Mercury session**
   - Quality Gate stop-intercepted notify → fires from **local hooks**
   - Dev Pipeline task-complete notify → fires from **local agent chain**

   None of these originate from a Routines trigger. They need a Mercury-local process to call an outbound channel. Routines does not reach into local sessions.

2. **`/fire` is fire-and-forget.** Mercury cannot "start Routines to do work AND have Routines notify user when done" without an outbound transport, because Routines provides no completion callback. The routine itself would have to push notification via connector → we're back to needing outbound transport inside the routine.

3. **Connector coverage is narrow.** Routines' "transport" is the MCP connector set (Slack/Linear/GDrive). Mercury user's stated preference in prior sessions lean toward Telegram / self-hosted ntfy / LINE — none of which are first-class MCP connectors. Going Routines-only would force either:
   - Accept Slack as the canonical Mercury channel (workflow change)
   - Build an MCP connector for the preferred transport (a Phase 5 Notify Hub renamed)

4. **#226 loop detector fires in `adapters/mercury-loop-detector/hook.cjs` (local process).** Phase 4-4 enhancement C3.b ("notify user when stall fires") happens on laptop, not in cloud. Routines is irrelevant to this specific caller.

5. **Coverage math fails the 70% gate.** Of the canonical 3 Phase 5-3 consumers + 1 Phase 4-4 enhancement C3.b, Routines covers:
   - 0/4 by replacing the outbound layer
   - ~1/4 as trigger substitute (Dev Pipeline task-complete could theoretically be driven by a GitHub `pull_request.closed` trigger, but Mercury's current pr-flow skill already handles this case locally)

#### What Routines IS good for (Phase 5 complementary scope)

- **B3 nightly Issue triage** (recommended by #289 body) — pure cloud-cron scenario, no transport needed (result is a GitHub comment, not a push notification)
- **B1 Argus parallel review** — GitHub trigger on `pull_request.opened`, output is PR comments (uses Claude GitHub App identity, not Mercury)
- **B2 weekly KB lint** — scheduled, output is a GitHub Issue
- Consuming `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` env var → irrelevant (runs in cloud, not local)

**Pattern**: Routines fits when the "output" is a repo-surface artifact (Issue / PR / commit). It does not fit when the output is a push notification to the human.

### 2.3 Revised Phase 5 MVP scope (updates `phase4-5-circular-dep-breakpoint-2026-04-24.md` §3.B)

Keep Approach B (mount external Notify channel) as recommended, with clarifications:

**IN scope (Phase 5 MVP):**
- Outbound transport adapter (`adapters/mercury-notify/`) — ≤200 LOC
- Interface: `notify(severity, title, body, [action_url])` → transport-specific ack
- One transport choice from the candidate eval (ntfy / Apprise / Telegram / Channels-if-shipped)
- Wire **one** caller: 4-3 B.3 fallback OR 4-4 enhancement C3.b (whichever is the easier existing consumer)
- Secret management: env-var in `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/settings.json` per existing `MERCURY_MEM0_DISABLED` precedent

**OUT of scope (Phase 5 MVP, defer to Phase 5-2 / 5-3):**
- Second+ transport channel
- Two-way confirm/reply mechanism (user replies to notify → agent reacts) — docs call this "远程确认" in DIRECTION.md §3 Module 3 — needs inbound webhook; not MVP
- Routines-based trigger backend integration (separate follow-up)

**Routines integration lane (separate issue, separate phase):**
- A new sub-module "Trigger Backend Adapters" can consume Routines as one of its backends alongside NAS cron + GHA + in-process hooks
- Not in Phase 5 MVP; folded into Phase 5-3 or a Phase 5.5 follow-up
- Recommended PoC start: **B3 nightly Issue triage** per #289 body — low risk, cloud-only, does not need Phase 5 transport

### 2.4 Circular-dependency status

Per `phase4-5-circular-dep-breakpoint-2026-04-24.md` — the cycle was declarative only. S72 shipped Approach A (Phase 4-4 enhancements A+B) via PR #291. Phase 4 core is now complete per EXECUTION-PLAN.md L334. Phase 5 MVP can proceed independently.

With S73's sizing decision: Phase 5 MVP remains Approach B (mount external transport). No EXECUTION-PLAN structural change needed — only wording clarification in L342 (`可用开发模式: 全部 Phase 1-4 能力`) is already satisfied by Phase 4 completion.

---

## 3. Transport Candidate ADR Skeleton (3 open questions for user)

The `phase4-5-circular-dep-breakpoint-2026-04-24.md` §3.B candidate list holds. Decision deferred pending user input on three questions:

### Q1. Privacy posture — public vs authenticated transport?

| Option | Example | Pro | Con |
|--------|---------|-----|-----|
| Public topic | ntfy.sh `/topic-xxxxx` | Zero secret; URL-share = subscribe | Anyone with URL can read |
| Authenticated | Telegram bot + chat_id | Private by design | Bot token + recipient id must be stored |
| Self-host | ntfy on AgentVol01 | Full control | Infra overhead; NAS must be reachable from local |

**TBD**: user's stated privacy bar for stall reports + handoff notifications.

### Q2. Secret storage location?

| Option | Precedent | Risk |
|--------|-----------|------|
| `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/settings.json` env block | `MERCURY_MEM0_DISABLED` | Readable by any local process under user account |
| OS keychain (Windows Credential Manager / macOS Keychain) | — | Extra platform code |
| Separate `.env` file in repo root (gitignored) | — | Easy to leak if gitignore forgotten |

**TBD**: user preference between convenience and isolation.

### Q3. Diagnostic payload — where does the stall-report JSON go in the notify?

Options:
1. **Inline in notify body** — full JSON in message (can be long; may truncate)
2. **Link to local file path** — notify carries `file:///.../.mercury/state/stall-reports/<id>.json`; user reads on same machine
3. **Upload to gist / ntfy attachment / Telegram document** — accessible off-device; adds transport complexity
4. **Summary only in notify + full JSON retained locally** — compromise

**TBD**: user workflow — mobile-first (needs #3 or #4) vs same-machine (#2 is enough).

---

## 4. Recommended Next Actions

### S73 closure (this session)

1. ✅ Write this doc
2. ⏳ Post summary comment to Issue #289 with Phase 5 sizing verdict + UNVERIFIED resolutions
3. ⏳ **No EXECUTION-PLAN structural edit** — current L337-362 Phase 5 structure holds; the three open questions become transport-ADR prerequisites
4. ⏳ **Keep #289 OPEN** as watch tracker (original stance); update next-check triggers to reflect what we learned
5. ⏳ Handoff to S74 with Phase 5 MVP kickoff gated on user answering the 3 open questions

### S74+ (Phase 5 MVP kickoff, user decision gate)

1. User answers Q1-Q3 above (privacy / secret / payload)
2. Main agent dispatches research sub-agent (Mode C) to eval 4 transport candidates against 5-criteria rubric
3. Research outputs ADR; user confirms choice
4. Dev sub-agent (Mode D) implements `adapters/mercury-notify/` + wire one caller
5. Dual-verify + pr-flow

### S75+ (Routines integration, separate track)

- File new Issue "PoC: Routines nightly Issue triage" (B3 per #289)
- Requires no Phase 5 MVP — independent track
- Validates Q2 (agents support) + Q4 (branch restriction) at real cost

---

## 5. Appendix — What this doc does NOT do

- Does not edit `.mercury/docs/EXECUTION-PLAN.md` — no structural change needed per §2.4
- Does not pre-select transport candidate — waits on 3 user open questions
- Does not cover Claude Design (A-series scenarios) — separate research when user pursues automation API
- Does not create branches / PRs — design doc only; S73 handoff step 6 conditional on Phase 5 shrinking, which we determined does NOT happen

---

## 6. Sources

**Primary (fetched 2026-04-25 via WebFetch):**
- [Automate work with routines](https://code.claude.com/docs/en/routines) — full capability reference
- [Trigger a routine via API](https://platform.claude.com/docs/en/api/claude-code/routines-fire) — endpoint + errors + idempotency

**Project context:**
- Issue #289 body (<https://github.com/392fyc/Mercury/issues/289>)
- `.mercury/docs/research/phase4-5-circular-dep-breakpoint-2026-04-24.md` §3.B (S72 design)
- `.mercury/docs/EXECUTION-PLAN.md` L337-362 (Phase 5 structure)
- `.mercury/docs/DIRECTION.md` §3 Module 3 (Notify Hub rationale)

**Related (not fetched this session, for reader follow-up):**
- [Claude Code on the web — cloud environment](https://code.claude.com/docs/en/claude-code-on-the-web)
- [MCP connectors](https://code.claude.com/docs/en/mcp)
- [Beta headers policy](https://docs.claude.com/en/api/beta-headers)
