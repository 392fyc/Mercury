# Phase C — Surface Choice & Implementation Decision

**Issue**: [#324 Phase C — Auto-report + Director command surface (lane-aware)](https://github.com/392fyc/Mercury/issues/324)
**Author**: main lane S79 (2026-04-27)
**Status**: Design locked, ready for implementation
**Refs**: #318 (architecture reframe parent), #308 (subsumed cmd suite), PR #321 (research §Dim 1.3), PR #333 (Phase A merged), PR #334 (Phase B merged)

## Context recap

Issue #324 already fixes the Director surface as **Telegram via `mercury-channel-router`** — not an open `Telegram-only / Claude-Code-only / Hybrid` choice. Handoff doc framed it as open; #324 body removed that ambiguity.

So the research question is not *which channel*, but *how* to wire C1 (auto-report) and C2 (lane-aware routing) on top of the existing router that Phase 5 MVP already shipped.

## Existing surface inventory (read 2026-04-27)

`adapters/mercury-channel-router/router.cjs` — 221 LOC

- `MAX_SESS = 3` (line 16) — needs bump to 5 per #324 Acceptance + `feedback_lane_protocol.md` HARD-CAP
- `POST /notify` (line 186) — already accepts `{severity, title, body, label}` and forwards to Telegram. C1 backend = no change.
- `@<label-prefix> <text>` routing (line 144) — already lane-aware: finds session by `label.startsWith(prefix)` derived from branch (`feature/lane-XXX/...`).
- `/cancel @<label-prefix>` and `/continue @<label-prefix>` (line 117) — already lane-aware.
- `handleCmd` covers only `status / list / cancel / continue / help` — missing `/lanes`, `/dir`, `/model`, `/permission-mode`.

`adapters/mercury-channel-client/channel.cjs` — 231 LOC

- SSE inbox consumer relays `message / verdict / command` events to MCP. No change needed for C2 (router does the routing; client just relays).

**Conclusion**: ~80% of C2 already exists. Phase C is a thin enhancement, not a rewrite.

## Decision

### C1 — Side lane auto-report hook

**Mechanism**: cron-driven `scripts/lane-auto-report.sh` that diffs `lane-status.json` and posts `POST /notify` when state changes. No git hook (per-commit is too noisy; cron 5-min cadence matches Phase A statusline + Argus poll cadence per `feedback_cron_interval_argus.md`).

**Why cron over git post-commit hook**:
- Git hooks fire per commit — most commits aren't "milestones" worth pinging Director.
- Cron + state diff = only fires on actual state transitions (lane spawn / claim / close / commit-jump / soak-complete).
- Reuses Phase A `lane-status.sh` aggregator (already produces canonical JSON).
- Same trigger pattern as Phase A statusline refresh — one mental model.

**Backend**: `POST /notify` already exists. Zero router changes for C1.

**Script outline** (`scripts/lane-auto-report.sh`, est. ~120 LOC):
1. Run `lane-status.sh --json` → write to `.mercury/state/lane-status.current.json`
2. Diff against `.mercury/state/lane-status.previous.json` (created on first run; missing → diff = "all new")
3. For each detected transition (lane added / removed / claimed-issue-changed / commit-sha-jumped / lane-status-state-changed), build a notify payload `{severity:'info', title:'<lane> <transition>', body:'<details>', label:'<lane>'}`
4. POST to `http://127.0.0.1:${MERCURY_ROUTER_PORT:-8788}/notify` with router token from `~/.mercury/router.token`
5. Move current → previous for next diff

**Install**: `phase-c-install.md` documents cron registration (`durable: true`, 5-min interval `2-59/5` to avoid `:00` collision per `feedback_cron_interval_argus.md`).

**Tests** (`test-lane-auto-report.sh`, est. ~60 cases):
- Diff detects: lane added, lane removed, claimed-issue change, commit-sha jump, status-field change
- Diff ignores: cosmetic field changes (timestamps in non-state fields)
- Empty previous → emits "all new" notification
- Network failure → exit non-zero, leaves previous untouched (next run retries)
- Missing token → exit non-zero with diagnostic

### C2 — Router lane-aware routing enhancement

**Changes to `router.cjs`** (~50 LOC added, total ~270 → over 200-LOC adapter cap, see Follow-up below):

1. **`MAX_SESS = 3 → 5`** (1 line). Add `process.env.MERCURY_ROUTER_MAX_SESS` override for future tuning.
2. **`/lanes` command** — structured lane list (vs `/list` flat session list):
   - Output: `[label] active|idle | issue:#N | branch:feat/... | sse:N` per session, sorted by activity.
   - Subsumes the `lane-aware status` ask in #318.
3. **`/dir @<lane> <path>` command** — sends `{type:'command', cmd:'change_cwd', payload:{path}, from_chat:chatId}` to lane inbox; client relays as MCP notification (channel.cjs already handles `type:'command'` at line 186).
4. **`/model @<lane> <name>` command** — same pattern, `cmd:'switch_model'`.
5. **`/permission-mode @<lane> <mode>` command** — same pattern, `cmd:'set_permission_mode'`.
6. **Update `/help` text** to advertise new commands.

**Why no full router refactor**: the M6 split issue (#303) is the right place for that. Phase C is the *capability* delivery, not the *layout* delivery. Follow-up Issue will note that #324 lands at ~270 LOC (over 200-cap) and explicitly references #303 as the cleanup vehicle.

**Backwards compatibility**:
- `MAX_SESS` env override is additive. Default 5 (was 3) — sessions 4 and 5 now register where they previously got HTTP 429.
- New `/lanes` command does not collide with existing names.
- New `/dir`/`/model`/`/permission-mode` send `command` events; the channel client's existing `command` branch needed a small extension (~13 LOC) to forward the new payload keys (`path`/`model`/`mode`) as XML attributes on the channel notification — without that the verb arrived but the operand was dropped. Existing message/verdict paths unchanged.

**Tests** (smoke + targeted):
- Phase 5 MVP shipped without router unit tests; adding a full test harness is M6 work.
- Phase C adds: a small `test-lane-auto-report.sh` for C1 (shell), and a manual smoke checklist in `phase-c-install.md` §"Verification" for C2 (start router with `MAX_SESS=2` env, register 3rd → expect 429 fail; then `MAX_SESS=5` → expect 200 ok; send `/lanes` → expect structured output; send `/dir @lbl /tmp` → expect target session sees channel notification).
- A C2 unit test belongs in #303 M6 split (where router gets restructured into testable modules).

### What we are NOT doing in Phase C

- ❌ No router refactor / module split (defer to #303).
- ❌ No `/permission-mode` *enforcement* (just plumb the command; actual mode change happens in client / Claude Code).
- ❌ No Telegram bot reply formatter changes.
- ❌ No new hook events (cron only).
- ❌ No Phase D anything (deferred until Anthropic Agent Teams GA per S75 reframe).

## LOC estimate (rolled up)

| File | Before | Added | After |
|---|---|---|---|
| `adapters/mercury-channel-router/router.cjs` | 221 | +46 | 267 (over 200-cap; Follow-up referenced) |
| `adapters/mercury-channel-client/channel.cjs` | 231 | +13 | 244 (payload forward fix found in deep-review) |
| `scripts/lane-auto-report.sh` | (new) | 161 | 161 |
| `scripts/test-lane-auto-report.sh` | (new) | ~250 (14 cases) | 250 |
| `.mercury/docs/guides/phase-c-install.md` | (new) | ~120 | 120 |
| **Total Phase C delta** | | **~590 LOC** | |

Within the 1-2d effort estimate from #324.

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| Cron auto-report spams Telegram on noisy lanes | Diff-based emission: only fires on state transitions, not every poll. Optional `MERCURY_LANE_REPORT_QUIET=1` env to suppress entirely. |
| MAX_SESS bump breaks router lock contract (file lock is per-process, not per-slot — safe) | None needed (`acquireLock` is per-process pid, MAX_SESS only gates `/register` capacity). Verified by reading router.cjs lines 25-41 + 167-176. |
| `/dir` command security (arbitrary path) | Allowlist already enforced at routeMessage line 130 (`isAllowed`); only allowlisted Telegram users can send commands. No new attack surface. |
| Router LOC over 200-cap | Follow-up Issue notes #303 M6 split as cleanup vehicle. Phase C ships capability now; cleanup deferred per CLAUDE.md "modular design" — adapter becomes detachable later via #303, not now. |
| Test coverage gap on router additions | Smoke checklist in install guide; full unit tests = #303 scope. Acceptable risk per Phase 5 MVP precedent. |

## Follow-up Issues to file post-merge

1. **Adapter LOC cap** — file new Issue: "router.cjs grew to ~270 LOC during Phase C, exceeds 200-cap; M6 split (#303) is the cleanup vehicle". Reference both #324 and #303.
2. **C2 unit tests** — track under #303 (no new Issue needed; #303 already covers M6 split + testability).

## Open questions for user (none blocking)

None — design is locked from #324 spec + reading existing surface. Implementation proceeds.
