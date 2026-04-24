# Phase 4-4 / Phase 5 Circular Dependency — Break-point Analysis

> Status: **Design proposal (awaiting main-agent decision)** | Author: designer sub-agent | Date: 2026-04-24
> Scope: Resolve the apparent deadlock between EXECUTION-PLAN.md Phase 4-4 "enhancement" and Phase 5 "Notify Hub"
> Out of scope: Implementation; EXECUTION-PLAN.md itself is not modified by this doc
> Related: `.mercury/docs/DIRECTION.md` §3 Module 3 (Notify Hub), §3 Module 4 (Quality Gate), §4 (mount strategy)
>          `.mercury/docs/EXECUTION-PLAN.md` Phase 4 (L241-333), Phase 5 (L337-362)
>          `.mercury/docs/research/phase4-3-compact-prevention-eval.md` §4-3 B.3 (already deferred to Phase 5)
>          Issue #226 (sliding-window loop detector — shipped), Issue #289 (Claude Code Routines research — open)

---

## 1. Problem Statement

EXECUTION-PLAN.md currently reads as if Phase 4-4 "enhancement" and Phase 5 "Notify Hub" form a closed
dependency loop:

```
Phase 4-4 enhancement: needs "通知用户" (notify user on stall) ─┐
                                                                 │
Phase 5 Notify Hub: 前置条件 "Phase 1-4 全部能力"  ─────────────┤
                                                                 │
Phase 4 complete: requires 4-4 enhancement done  ◄──────────────┘
```

Surface reading: each blocks the others, nothing ships.

The symptom matters because:

1. Phase 4-4 **core** (sliding-window loop detector, Issue #226) already shipped via PRs #229/#231
   and has fired in real sessions (three times in the S72-predecessor stretch, including during the
   preparation of this doc). The `no_progress` / `duplicate_call` / `same_error` / `read_write_ratio`
   signals work and reset correctly — see `adapters/mercury-loop-detector/hook.cjs` L139-149.
2. Phase 4-3 B.3 ("连续 block 3× 无响应 → Notify Hub 通知") was also explicitly deferred to Phase 5
   in `phase4-3-compact-prevention-eval.md` §4-3 B.3. So both 4-3 and 4-4 have a dangling
   "notify" tail that converges on the same Phase-5 dependency.
3. Mercury is otherwise ready to close Phase 4 and move on.

This document re-reads each dependency claim in EXECUTION-PLAN.md against the actual shipped code
and finds the loop is **largely declarative, not real**.

---

## 2. Dependency Graph Re-analysis

### 2.1 Parsing every Phase 4-4 / Phase 5 claim in EXECUTION-PLAN.md

| # | Claim (verbatim paraphrase) | Line | Classification |
|---|------------------------------|------|----------------|
| C1 | Phase 4-4: sliding window 循环检测已实现并交付 | 326 | **Shipped fact** — PRs #229/#231 merged, hook.cjs in tree. |
| C2 | Phase 4-4 增强: 多级超时 (soft → idle → hard) | 327 | **Unimplemented enhancement.** No external dependency; pure local state machine. |
| C3 | Phase 4-4 增强: 卡死后自动生成诊断报告 + 通知用户（依赖 Phase 5 Notify Hub） | 328 | **Composite** — diagnostic report ≠ notify. Report=write-to-file, Notify=push-to-human. |
| C4 | Phase 5 可用开发模式: 全部 Phase 1-4 能力 + Session Continuity | 341 | **Idealized ordering**, not a capability gate. "可用开发模式" is about using Mercury to build Phase 5, not about Phase 5 itself requiring 4-4. |
| C5 | Phase 5-3 与其他模块集成: Session Continuity 通知 / Quality Gate stop 被拦截通知 / Dev Pipeline 任务完成通知 | 355-357 | **Consumer list** — Phase 5 advertises itself to upstream modules. Implies loose coupling via event interface, not a build-order dependency. |
| C6 | Phase 4 完成后解锁: agent 可长时间自主工作 ← 核心里程碑 | 333 | Core milestone already satisfied by 4-2+4-3+4-4 core; C2/C3 are optional polish, not gate conditions. |

### 2.2 True dependency graph (cleaned)

```
                        ┌────────────────────────────────────────┐
                        │  Phase 4-4 CORE (shipped)              │
                        │  - sliding-window loop detector         │
                        │  - 4 signal types                       │
                        │  - independent counters                 │
                        │  - per-session reset                    │
                        └──────────────────┬─────────────────────┘
                                           │
                   ┌───────────────────────┼───────────────────────┐
                   │                       │                       │
         ┌─────────▼──────────┐  ┌─────────▼──────────┐  ┌────────▼────────────┐
         │ C2 Multi-level     │  │ C3.a Diagnostic    │  │ C3.b Notify user    │
         │ timeout            │  │ report → file      │  │ on stall            │
         │                    │  │                    │  │                     │
         │ Dep: state machine │  │ Dep: loop detector │  │ Dep: Notify channel │
         │      only          │  │      fire event    │  │      (Phase 5 MVP)  │
         │ STANDALONE         │  │ STANDALONE         │  │ NEEDS CHANNEL       │
         └────────────────────┘  └────────────────────┘  └────────┬────────────┘
                                                                   │
                                           ┌───────────────────────▼───────────┐
                                           │ Phase 5 Notify Hub                │
                                           │ (dep: ONE outbound channel,       │
                                           │  NOT "all Phase 1-4 能力")        │
                                           └───────────────────────────────────┘
```

### 2.3 Kind of dependency — key distinctions

| Kind | Example in our plan | Real blocker? |
|------|---------------------|---------------|
| **Capability dependency** (A needs B's code to exist) | C3.b → Phase 5 outbound channel | Yes, but only on ONE channel, not the whole Hub |
| **Temporal dependency declaration** ("Phase 5 前置条件 Phase 1-4 全部能力") | C4 | No — it's a planning ideal, not a build gate. Phase 5 MVP can run with just Phase 1 (pr-flow existed in Phase 1) for test harnessing |
| **Event consumer coupling** (A emits, B decides whether to listen) | C5 | No — publisher/subscriber pattern, each side ships independently |
| **IO coupling — write-to-file** (report artifact) | C3.a | No external dep at all; it's just `fs.writeFileSync` inside the hook |
| **IO coupling — push-to-human** (notification) | C3.b | Yes — needs transport. But transport can be mounted, not built |

Conclusion: the "cycle" exists only because EXECUTION-PLAN.md glued together three orthogonal things
in one bullet (L328: `卡死后自动生成诊断报告 + 通知用户（依赖 Phase 5 Notify Hub）`). Splitting them
dissolves the cycle.

---

## 3. Break-point Candidates

### Summary matrix

| # | Approach | EXEC-PLAN edits | First-session scope | Friction | Yield | DIRECTION alignment |
|---|----------|-----------------|---------------------|----------|-------|---------------------|
| A | Split 4-4 enhancement: ship non-notify half now, defer notify half | L325-328, L330-333 | 1 session | Low | Medium-High | Excellent — modular, no custom orchestrator |
| B | Mount external Notify channel, unblock C3.b in the same breath | L337-362, Issue #289 fold-in | 2 sessions (research + mount) | Medium (Mode D eval required) | High | Good — follows P1 mount-over-self-build; adapter must stay ≤200 LOC |
| C | Rewrite EXEC-PLAN: Phase 5 MVP first, 4-4 enhancement follows | L337-362, Phase ordering | 2 sessions | High — mental model disruption | High if executed cleanly | OK — just a reordering |
| D | Close Phase 4 as-is, reclassify 4-4 enhancement as Phase 5 scope | L325-333, L337-362 | 0.5 session (doc-only) | Very low | Low-Medium — unblocks future work but adds nothing today | Excellent — honest about current state |
| E | Hybrid: A + B in sequence (A first, B at next natural opening) | L325-333 now, L337-362 later | 1 session now | Low now, Medium later | High over 2-3 sessions | Excellent |

### 3.A — Split 4-4 enhancement along the notify boundary

**Core idea.** The L328 bullet mashes three things together:
`(i) multi-level timeout + (ii) diagnostic report + (iii) notify user`.
Carve (iii) out into a separate bullet that explicitly says "needs Phase 5 channel", and ship (i) and (ii)
now without waiting.

**What to edit in EXECUTION-PLAN.md (main-agent applies — designer does not edit).**

- L325-328 current body:
  ```
  ### 4-4. 卡死检测 (S37, #226) — 核心已交付，增强待定
  - ✅ sliding window 循环检测已实现并交付 (PR #229, #231, merged)
  - ⏳ 增强: 多级超时（soft → idle → hard）
  - ⏳ 增强: 卡死后自动生成诊断报告 + 通知用户（依赖 Phase 5 Notify Hub）
  ```
- Replace with:
  ```
  ### 4-4. 卡死检测 (S37, #226) — 核心已交付，增强分两批
  - ✅ sliding window 循环检测已实现并交付 (PR #229, #231, merged)
  - ⏳ 增强 A (Phase 4 内可做): 多级超时 (soft → idle → hard)
  - ⏳ 增强 B (Phase 4 内可做): 卡死后诊断报告 → 写文件 (.mercury/state/stall-reports/)
  - ⏳ 增强 C (Phase 5 依赖): 卡死后推送通知给用户（随 Phase 5 Notify Hub 落地）
  ```

**First-session scope (for main-agent dispatch plan):**

- Dispatch dev sub-agent to extend `adapters/mercury-loop-detector/hook.cjs` with:
  - Multi-level timeout counter fields in the state file (soft / idle / hard)
  - On stall fire: serialize `{session_id, stall_type, last_5_tool_calls, err_sig, timestamp}` to
    `.mercury/state/stall-reports/<session_id>-<ts>.json` before the existing reset
  - Keep current adapter ≤200 LOC (now ~200; if it crosses, split into `hook.cjs` + `report.cjs`)
- Add a pruning rule (keep last 50 reports, unlink older) so state dir stays bounded
- Dual-verify gate, PR to develop, pr-flow skill

**Friction.** Low. Pure local code, no mount, no new external dep.

**Yield.** Medium-High. User gets forensic data on every stall (huge for Phase 5 debug later), plus
multi-level timeout is a commonly-missed Mercury capability. Does not unblock `C3.b` notify.

**Risk.** Adapter crosses 200 LOC if both enhancements land together — plan to split file if so.

### 3.B — Mount external Notify channel (Phase 5 MVP via mount)

**Core idea.** DIRECTION.md §3 Module 3 explicitly lists Notify Hub as "self-build worthy" because
the **routing and unified outlet** are Mercury-specific, but **transport** is commodity. We can
mount one transport now (ntfy.sh HTTP-POST / Apprise / Telegram Bot) behind a thin adapter, unblocking
C3.b and every other deferred "notify user" bullet (including 4-3 B.3).

**Candidate transports (Mode D 五标准 must be evaluated by main-agent before committing):**

| Candidate | Transport model | License | Adapter size estimate | Risk |
|-----------|-----------------|---------|----------------------|------|
| ntfy.sh (public server or self-host) | HTTP POST to `https://ntfy.sh/<topic>` | Apache-2.0 | ~40-60 LOC (curl/fetch wrapper) | Public topic = eavesdropping unless ACL'd; self-host adds infra |
| Apprise (Python lib) | Library call dispatches to 110+ targets | BSD-2-Clause | ~80-120 LOC (Python subprocess wrapper from Node) | Adds Python runtime dep; Mercury already has uv for mem0 so acceptable |
| Telegram Bot API (direct) | HTTPS POST to `api.telegram.org` | N/A (API, not lib) | ~50-80 LOC | Requires bot token secret handling; 1 transport only |
| Claude Code Channels (native) | `channels_send()` primitive | N/A | ~20 LOC if Channels shipped | **Needs verification of 2026-04 API availability** |

> **Do not pre-commit to any candidate in this doc.** Main agent must run Mode D five-criteria eval
> (社区活跃度 / 接口稳定性 / 可剥离性 / 维护者信誉 / 替代成本) and record decision in an ADR
> before mount. Do not introduce AGPL/GPL libs. Record `upstream_sha_at_import` via `gh api` per
> Mercury cherry-pick protocol.

**First-session scope:**

1. Open Issue "Phase 5 MVP: single-channel Notify adapter"
2. Dispatch research sub-agent (Mode C, `/autoresearch`) to compare the 4 candidates; produce ADR
3. Main agent selects one based on ADR; user-confirm before mount
4. Dispatch dev sub-agent (Mode D): create `adapters/mercury-notify/` with `adapter.{cjs,py}`,
   README, UPSTREAM.md, manifest entry, attribution header
5. Minimal interface: `notify(severity, title, body, [action_url])` → returns transport-specific ack
6. Wire one caller site: 4-3 B.3 fallback (the simplest existing consumer)

**Friction.** Medium. Requires:
- User decision on which transport (the choice leaks PII/phone/secrets — user-level concern)
- Secret management for bot tokens (out-of-repo env var, never commit)
- Mode D eval cycle (~1 session of research before implementation)

**Yield.** High. One mount satisfies 4-3 B.3, 4-4 C3.b, and every future "notify" bullet in Phase 5-3.
Phase 5 becomes a series of small "add another channel" adapter increments instead of a monolith.

**Interaction with Issue #289 (Claude Code Routines).** Important — do not conflate:
- Routines = **scheduled background task spawner** (the "who triggers the notify") — analog to cron/GHA
- Notify Hub = **outbound push transport** (the "how the notify reaches the human")
- The two are orthogonal layers. #289 research output should be folded in as a **trigger backend**
  option for Phase 5-3 cases 1 and 3 (session-switch and task-complete notifications), not as
  a replacement for the outbound channel. Main agent should keep #289 as separate research,
  and Phase 5 MVP explicitly does not wait on it.

### 3.C — Reorder EXECUTION-PLAN.md so Phase 5 MVP precedes 4-4 enhancement

**Core idea.** Flip the sequence: declare Phase 4 complete at current state, rewrite Phase 5 to be
"MVP first, integrations later", and put the 4-4 enhancement bullet inside Phase 5 as a consumer.

**What to edit in EXECUTION-PLAN.md:**
- L241-333: add "Phase 4 Complete ✅" header block before 4-4 restatement; move the C2/C3 bullets out
- L337-362: rewrite Phase 5 into 5-0 (MVP mount, single channel), 5-1 (interface, renumbered), 5-2, 5-3
- Update L384-392 Phase-order table

**First-session scope.** Rewrite doc only; implementation starts session 2. Session 1 is pure
Mode A (requirements + structure reshuffle with user confirm).

**Friction.** High.
- Mental-model disruption — a lot of cross-references in memory/session-state docs assume Phase order
- User has to re-verify phase table + execution-plan nav links + handoff docs that cite phases
- Higher blast radius for minor gain

**Yield.** High if executed cleanly — gives a coherent narrative where Notify is a first-class
early module. But that's also what **Approach B's Mode D eval step naturally produces** — Approach C
is a superset of B with added doc churn.

**Verdict preview.** Approach B delivers C's yield without the doc-rewrite tax. Not recommended.

### 3.D — Close Phase 4, open fresh "Phase-Notify" milestone (no enhancement)

**Core idea.** Be honest: Phase 4 core is done; the enhancement was wishlist. Stop claiming Phase 4
is unfinished because of an optional enhancement. Create a Phase 4.5 / "Notify" milestone covering
what was C3.b + 4-3 B.3 + Phase 5-3 integrations, close the loop conceptually.

**What to edit in EXECUTION-PLAN.md:**
- L325-333: reduce 4-4 to just "✅ core shipped"; remove C2/C3 bullets (or move to a backlog doc)
- Add a new section "Phase 4.5: Notify-gated Enhancements" or just merge enhancements into Phase 5
  scope wholesale
- L337-362: unchanged, but noting that 4-4 enhancements are now Phase 5 consumers

**First-session scope:** doc-only, 0.5 session. Main agent + user confirm the scope shift.

**Friction.** Very low — it's a planning-doc correction.

**Yield.** Low-Medium. No new capability ships, just clearer boundaries. But clears the "Phase 4
not done" mental weight, which has real cost: every session-start doc reads "Phase 4 still has
unfinished enhancements".

**Use case.** Good as a *lead-in* to Approach B — do D first (doc hygiene), then B (build the channel).

### 3.E — Hybrid: A now + B at next natural opening (RECOMMENDED)

**Core idea.** Ship Approach A immediately (session 1 — pure local enhancement, no external dep,
no user decision required mid-task). Then at session 2-3 when the user has bandwidth for a Mode D
mount decision, run Approach B to unlock all deferred "notify" tails in one stroke. Do D's doc
cleanup opportunistically inside A's PR.

**Why this is not just "A+B sequential".** The claim is: **session 1 requires zero user decisions**
(fits `feedback_auto_run_mode` — pipeline work proceeds without asking), while session 2 is where
the architectural commitment (which transport) happens. This is the healthiest rhythm.

---

## 4. Recommendation

### Primary: **Approach E (Hybrid — A now, B next, D opportunistically)**

**Rationale.**
- A is mechanically unblocked today. No external dep, no user decision, fits auto-run mode.
- A's diagnostic-report artifact (stall JSON in `.mercury/state/stall-reports/`) is exactly the
  forensic input Phase 5-3 needs when deciding what to put in a notification body. We want that
  data collected BEFORE building the transport, not after.
- B is the right shape for Phase 5 MVP: one mount, one adapter, ≤200 LOC, no custom orchestrator.
  It satisfies DIRECTION.md §3 Module 3 exactly.
- D is essentially free if folded into A's PR body / CHANGELOG (`clarify Phase 4-4 enhancement
  scope split` is a one-paragraph plan edit).

**First-session scope (for main-agent dispatch):**

1. Open Issue "Phase 4-4 enhancement A+B: multi-level timeout + diagnostic report"
2. Dispatch dev sub-agent (Mode B) to extend `adapters/mercury-loop-detector/`:
   - Add soft/idle/hard timeout counters to state schema
   - On stall fire, write report JSON to `.mercury/state/stall-reports/` before reset
   - Keep current adapter's 200 LOC cap; split into `hook.cjs` + `report.cjs` if it crosses
3. Update EXECUTION-PLAN.md L325-328 per Approach A's text (main agent writes, or Dev sub-agent
   does if scope allows)
4. `/dual-verify` + `/pr-flow` as usual
5. Soak 1 session (natural use) to confirm no regression in existing loop detection

**Session-2 follow-up scope (do not dispatch in session 1):**

1. Open Issue "Phase 5 MVP: single-channel Notify adapter — candidate eval"
2. Dispatch research sub-agent (Mode C) for Notify transport eval — outputs an ADR
3. **Wait for user decision** on transport (this is a real decision point — secret handling,
   privacy posture, self-host vs public)
4. If approved, Mode D mount + adapter in session 3

### Human decision point (not auto-runnable — flag for main agent)

- **Transport choice (session 2).** The user must pick ntfy/Apprise/Telegram/Channels. This involves
  where messages land (phone / desktop / IM), who else can see them (public topic vs auth), and
  whether we accept a Python runtime dep. Not an architecture call — a personal-workflow call.
- **Secret handling (session 2).** Bot tokens must live outside the repo. User must confirm env-var
  placement (probably `$HOME/.claude/settings.json` env block, per existing `MERCURY_MEM0_DISABLED`
  precedent) and rotation plan.

### Explicitly **not recommended** (with reasons, so the team does not relitigate)

- **Approach C (full doc reorder).** High friction for doc churn that Approach E achieves cheaply.
  The only scenario where C is better: user decides Phase 5 is the next focus AND wants a narrative
  rewrite for publication. Not worth it for internal clarity alone.
- **"Build our own Notify Hub from scratch before mounting anything".** Violates DIRECTION.md P1
  (lightweight body + external mount) and CLAUDE.md `DO NOT build custom orchestrator layers`.
  Notify transport is a solved problem; do not reinvent.
- **Pure Approach D (close Phase 4, no enhancement).** Leaves users blind when loop detector fires
  (current state: block message only, no forensic trail). Throws away low-hanging fruit.
- **"Wait for #289 Routines research before any action".** #289 is about scheduled **trigger**
  backends, not about outbound **transport**. They are orthogonal. Gating Phase 4 closure on a P2
  research task is a category error.

---

## 5. Rollback / Exit Criteria

If Approach A's soak reveals problems in session 1:

| Symptom | Likely cause | Fallback |
|---------|--------------|----------|
| Adapter crosses 200 LOC unsplittable | Too many enhancements bundled | Split into `adapters/mercury-loop-detector/` (core) + `adapters/mercury-stall-reporter/` (diagnostic) — two adapters, each ≤200 LOC |
| New false-positive stall fires after soft/idle/hard timeouts added | Timer logic interacts with existing counters | Revert timeout portion; keep diagnostic-report portion (they are independent) |
| Diagnostic report disk usage grows unbounded | Pruning rule wrong | Tighten retention from 50 → 10 reports, or move storage out of repo (`$HOME/.claude/mercury/stall-reports/`) |
| User reports the feature feels intrusive | Cognitive noise > value | Feature-flag via `MERCURY_STALL_REPORT_DISABLED=1` env var (matches existing `MERCURY_MEM0_DISABLED` pattern) |

If Approach B's research (session 2) finds no transport meets DIRECTION five-criteria:

| Finding | Response |
|---------|----------|
| All candidates fail 可剥离性 or 维护者信誉 | Fall back to Claude Code Channels if its API shipped; else defer Phase 5 MVP another cycle and close the enhancement-C bullet as "Phase 5 未来任务" |
| Only ntfy.sh qualifies but user rejects public topic | Recommend self-hosted ntfy container on NAS (AgentVol01 already runs Container Station) — adds infra but keeps transport independence |
| All candidates fine but user cannot commit to transport choice | Use GitHub Issue comment as transport MVP (we already have the token) — lowest-privacy, zero-infra baseline to unblock Phase 5 skeleton; user can swap later |

If Approach E as a whole stalls after two sessions:

1. Formally adopt Approach D (doc-only close of Phase 4) and move on to Phase 6 evaluation
2. Log the Notify-related deferrals as a single "Phase 5 deferred cohort" Issue with explicit
   re-evaluation conditions (e.g. "unfreeze when user acquires consistent mobile workflow that
   makes notifications valuable")

---

## 6. Appendix — What this doc does NOT do

- Does not edit EXECUTION-PLAN.md (out of scope per task statement).
- Does not dispatch dev agents (designer role forbids).
- Does not pre-select the Phase 5 transport — that is Mode D eval work by main/research agent.
- Does not fold Issue #289 research into Phase 5 work — the two are orthogonal; main agent
  handles sequencing.
- Does not evaluate whether the loop-detector itself needs changes unrelated to stall reports
  (e.g. the known S37 `no_progress` false-positive on long cron-heavy sequences). That is its
  own backlog item.
