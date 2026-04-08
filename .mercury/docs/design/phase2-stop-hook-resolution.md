# Phase 2 Stop Hook Resolution — Trade-off Design

**Status**: Proposal — awaiting user decision
**Date**: 2026-04-08
**Author role**: design agent
**Parent Issue**: Phase 2 #181
**Prior ADRs**:
- GSD REJECT — PR #193 (`.mercury/docs/research/phase2-1-get-shit-done-evaluation.md`)
- OMC DEFER — PR #195 (`.mercury/docs/research/phase2-1-omc-evaluation.md`)
- Superpowers REJECT — PR #197 (`.mercury/docs/research/phase2-1-superpowers-evaluation.md`)
- OpenSpace REJECT — PR #204 (`.mercury/docs/research/phase2-1-openspace-evaluation.md`)

**Decision authority**: Mercury owner (user). This document is the trade-off briefing; the design agent does not commit the decision.

---

## 1. Context

### 1.1 The acceptance criterion

Phase 2 of Mercury's execution plan (`.mercury/docs/EXECUTION-PLAN.md` §2-3 *"Stop Hook 实现"*) defines a single, mechanical acceptance criterion:

> **验收标准: Dev sub-agent 不能在 test 未通过时 stop**
> (Acceptance: dev sub-agent cannot stop while tests are failing)

The original reading of this criterion — reinforced throughout Phase 2-1 ADRs — is **harness-level, mechanical, exit-code-based**: if `npm test` (or equivalent) exits non-zero, the Claude Code Stop / SubagentStop hook must return blocking output, regardless of whether the LLM "thinks" the task is done.

### 1.2 How we got here

Phase 2-1 evaluated all 4 candidates named in `DIRECTION.md` as potential Quality Gate mounts. Outcome table:

| Candidate | Verdict | Decisive reason |
|---|---|---|
| GSD (`gsd-build/get-shit-done`) | REJECT | No Stop hook at all — all 9 hooks are PreToolUse/PostToolUse/SessionStart/statusLine |
| OMC (`Yeachan-Heo/oh-my-claudecode`) | DEFER | Has a Stop hook scaffold (`persistent-mode.cjs`), but the gate is LLM-level (Ralph loop iteration counter) — not mechanical exit-code |
| Superpowers (`obra/superpowers`) | REJECT | Ships exactly one hook (`SessionStart`); no Stop hook scaffold to interpose on — strictly weaker than OMC |
| OpenSpace (`HKUDS/OpenSpace`) | REJECT | Zero Claude Code hook infrastructure; self-evolving skill framework + MCP server, category mismatch; 15-day-old repo, pre-1.0, active stability churn |

**The exhaustion point**: four-for-four, no external project ships a ready-to-mount mechanical exit-code Stop gate that satisfies the criterion as originally written. OMC is the only candidate with *any* Stop hook scaffold.

### 1.3 The decision this document frames

Per the OpenSpace ADR Consequences section (PR #204), the user must choose between two paths:

- **Path α** — relax the acceptance criterion and accept OMC's LLM-level gate as sufficient.
- **Path β** — mount OMC AND write a thin Mercury-side adapter (~50–150 LOC) that interposes a mechanical exit-code check.

A possible **Path γ** (belt-and-suspenders) and a **Path δ** (self-research from scratch, violating mount-first) are briefly considered in §6.

### 1.4 Stop hook mechanism reference (verified 2026-04-08)

From `https://code.claude.com/docs/en/hooks` (verified by design agent via WebFetch this session):

**Two equivalent ways to block a Stop or SubagentStop event**:

1. **Exit code 2** with a stderr message — stderr text is fed back to Claude as an error message.
2. **Exit code 0** with JSON on stdout:
   ```json
   {"decision": "block", "reason": "message for Claude"}
   ```

For Stop: *"Prevents Claude from stopping, continues the conversation."*
For SubagentStop: *"Prevents the subagent from stopping."*

The `reason` field is shown to **Claude**, not to the user. Both approaches are supported; they are mutually exclusive within a single hook invocation.

**SubagentStop input includes** (relevant fields):
- `session_id`, `cwd`, `hook_event_name: "SubagentStop"`
- `agent_id`, `agent_type`
- `last_assistant_message`
- `transcript_path`, `agent_transcript_path`
- `stop_hook_active` (re-entry guard flag)

The `stop_hook_active` flag is critical: if a Stop hook is already running (i.e., the agent has already been blocked once and re-entered Stop), this flag is `true`. Hooks must check this to avoid infinite loops.

---

## 2. Path α — Relax the acceptance criterion

### 2.1 What Path α concretely entails

| Action | File / target | Effort |
|---|---|---|
| Mount OMC via Claude Code plugin install | `/plugin install oh-my-claudecode` (or npm `oh-my-claude-sisyphus`) | 1 command |
| Document the plugin dependency | `modules/README.md` or `adapters/omc/README.md` (new file ~20 lines) | 10 min |
| Re-word `.mercury/docs/EXECUTION-PLAN.md` §2-3 acceptance criterion | Change "Dev sub-agent 不能在 test 未通过时 stop" wording to explicitly allow LLM-level enforcement via OMC UltraQA mode | 5 min |
| Add a dev-pipeline directive | `.claude/agents/dev.md` — require dev sub-agents to invoke `/oh-my-claudecode:ultraqa --tests` mode at task start | 30 min |
| Update Phase 2 completion ADR | `.mercury/docs/research/phase2-completion.md` (new file) documenting the relax decision | 30 min |

**Total effort**: ~1–2 hours end-to-end, plus dual-verify.
**Total new code**: 0 LOC.
**Total new Mercury-maintained infrastructure**: 0.

### 2.2 The actual semantic gap

Quote from OMC ADR (PR #195, §1) — verbatim Stop hook output:

```json
{"decision": "block", "reason": "[RALPH LOOP - ITERATION N/MAX] Work is NOT done. Continue working."}
```

This is **the correct Claude Code blocking mechanism** — `decision: "block"` is the official API and `reason` is fed back to Claude. But note what the `reason` string actually says: it is a Ralph-loop iteration counter, not a test-state assertion. The blocking happens because the iteration counter has not reached max, not because tests fail.

OMC's **UltraQA** mode is closer to Mercury's need. Quote from OMC ADR §2:
- Runs project test command and checks output each cycle
- Blocks Stop events while `active && !all_passing` (Priority 7 in `persistent-mode.cjs`)
- Exit condition: goal met (tests pass) OR max cycles reached

The critical line is *"runs project test command and checks output"* — but the checking is performed by an **LLM step** (architect/critic agent examining test output text), not by a harness-level exit-code read. The OMC ADR §3 states this plainly:

> Mercury's criterion is **mechanical**: "if `npm test` exit code != 0, block Stop."
> UltraQA's gate is **LLM-level**: the agent runs tests, observes output, and decides whether to mark the cycle complete.

### 2.3 Risk analysis — scenarios where a dev sub-agent CAN still stop with failing tests under Path α

| # | Scenario | Likelihood | Mercury blast radius |
|---|---|---|---|
| R1 | Dev agent forgets to invoke `/ultraqa --tests` at task start | Medium | Full — no gate runs at all |
| R2 | Dev agent invokes UltraQA but hallucinates "tests pass" from partial output | Low-medium | Full — UltraQA exits "goal met" |
| R3 | Tests actually run, fail, LLM misreads output (e.g., "0 failures" in an unrelated log line) | Low | Full |
| R4 | UltraQA max-cycles exceeded while tests still failing — UltraQA exits naturally, dev agent stops | Medium-high | Full (by design) |
| R5 | Test runner crashes before emitting output; LLM judges "no output = probably OK" | Low | Full |
| R6 | Dev agent runs tests in wrong directory / wrong command; LLM sees green output from unrelated tests | Low | Full |

**R4 is the one that matters most.** UltraQA's max-cycles exit is intentional — the scaffold assumes a human-in-the-loop or higher-level controller will handle the "we tried 5 times and failed" case. Mercury's dev sub-agents are expected to run unattended, so R4 means: *any sufficiently hard bug will eventually let the agent stop with tests red, just by exhausting cycles.*

**R1 is structurally worrying.** Unlike a Stop hook (which fires regardless of agent intent), invoking UltraQA is an **opt-in action by the agent**. A forgetful or "creative" dev agent can skip it entirely. This contradicts the whole point of harness-level enforcement — the original criterion exists precisely because the agent is not trusted.

### 2.4 Effort summary — Path α

| Dimension | Value |
|---|---|
| LOC (new Mercury code) | 0 |
| New docs | ~2 short files (plugin README + Phase 2 completion ADR) |
| Hours to "Phase 2 done" | 1–2 hours (plus dual-verify) |
| Ongoing maintenance | ~0 — OMC upstream owns the scaffold; Mercury only maintains the dev.md directive |
| Dependencies added | 1 (OMC plugin) |
| Mount-first adherence | Full |
| Semantic guarantee strength | **Weak** (LLM-level, opt-in, defeats R1/R4) |

---

## 3. Path β — OMC mount + thin Mercury adapter

### 3.1 Architecture sketch

```
Claude Code harness
  │
  ├── SubagentStop event fires
  │     │
  │     ├── [OMC plugin] persistent-mode.cjs hook
  │     │       ├── if Ralph/UltraQA active: emit {"decision":"block", ...}
  │     │       └── else: exit 0 (no opinion)
  │     │
  │     └── [Mercury adapter] mercury-test-gate hook  ← NEW
  │             ├── read stdin (SubagentStop JSON)
  │             ├── resolve test command for current task
  │             ├── run test command, capture exit code
  │             ├── if exit != 0: emit {"decision":"block", "reason":"..."}
  │             └── if exit == 0: exit 0 (no opinion)
  │
  └── result: Stop is blocked if EITHER hook blocks
```

**Location in repo**:
```
adapters/mercury-test-gate/
├── README.md
├── hook.sh              (or hook.cmd on Windows — polyglot wrapper)
├── lib/
│   ├── resolve-command.<ext>    (task → test command lookup)
│   └── run-and-check.<ext>      (execute + exit-code capture)
└── register.md          (instructions for wiring into .claude/settings.json)
```

**Hook registration** (in project `.claude/settings.json`, NOT shipped inside OMC):
- Event: `SubagentStop`
- Matcher: dev sub-agent type(s) (e.g., `dev`, `acceptance`)
- Command: absolute path to `adapters/mercury-test-gate/hook.sh`
- Async: false (must complete before Claude sees the decision)

### 3.2 Pseudocode — Stop hook handler logic shape

```
# Pseudocode — NOT implementation
# Stdin: SubagentStop event JSON per Claude Code hook spec

INPUT ← read_stdin_json()

# 1. Re-entry guard — avoid infinite block loop
IF INPUT.stop_hook_active == true:
    EMIT {"decision": null}    # see Q15 — `null` is NOT in the official Stop hook spec; placeholder for "no opinion" pending Q15 resolution. See Q14 for the bypass-vulnerability concern this re-entry policy creates.
    EXIT 0

# 2. Scope check — only enforce for tracked dev-style agents
IF INPUT.agent_type NOT IN mercury_gated_agent_types:
    EXIT 0    # not our concern; other hooks may still speak

# 3. Resolve the test command for the current task context
test_cmd ← resolve_test_command(cwd = INPUT.cwd, agent = INPUT.agent_type)

IF test_cmd == NULL:
    # No test command known for this task — fail open or fail closed?
    # Design choice: FAIL OPEN with a log warning. Rationale in §3.4.
    # NOTE: see Q2 — Argus PR #205 review flagged that this fail-open default
    #       conflicts with the mechanical-acceptance semantics; Q2 is now the
    #       binding question, not a settled answer.
    LOG "no test command resolved; skipping test gate"
    EXIT 0

# 4. Run the command with a timeout
result ← run_with_timeout(test_cmd, timeout = MERCURY_TEST_GATE_TIMEOUT_SEC)

# 5. Interpret the result
IF result.timed_out:
    EMIT {"decision": "block",
          "reason": "Mercury test gate: test command timed out after Ns. "
                    "Cannot stop. Investigate the hang before continuing."}
    EXIT 0

IF result.exit_code != 0:
    EMIT {"decision": "block",
          "reason": f"Mercury test gate: {test_cmd} exited {result.exit_code}. "
                    f"Last 20 lines of output:\n{tail(result.output, 20)}\n"
                    "Cannot stop while tests are failing. Fix and retry."}
    EXIT 0

# 6. Tests pass — let Stop proceed
EXIT 0
```

### 3.3 How does the Mercury adapter interact with OMC's existing Stop hook?

Three possible interaction models:

| Model | Description | Recommendation |
|---|---|---|
| **Override** | Mercury adapter runs instead of OMC's hook (by removing OMC from `.claude/settings.json`) | Not recommended — defeats the point of mounting OMC |
| **Layer (parallel)** | Both hooks registered for `SubagentStop`; Claude Code runs both; Stop is blocked if EITHER returns `decision: "block"` | **Recommended** — clean separation of concerns |
| **Chain (sequential)** | Mercury adapter wraps and invokes OMC's hook internally | Not recommended — brittle, couples Mercury to OMC internals |

The Layer model is natural because Claude Code's hook system runs all matching hooks. The OR-semantics of blocking (any hook can block) is exactly what we want: OMC handles its Ralph/UltraQA cycle-counting use case; Mercury handles the mechanical test-exit-code use case. Neither knows about the other.

**Concretely**: the adapter does NOT modify any file under `modules/omc/`. It only adds a new entry to Mercury's own `.claude/settings.json` pointing to `adapters/mercury-test-gate/hook.sh`. This preserves the mount-first principle — OMC stays pristine, Mercury adds orthogonal enforcement.

### 3.4 Test-run primitive — how does the adapter know WHAT to run?

This is the **biggest open design question** for Path β. Four options, evaluated:

| Option | How it works | Pros | Cons |
|---|---|---|---|
| **O1. TaskBundle field** | Each task carries a `test_command` field in its bundle manifest; adapter reads from a known location (e.g., `.mercury/tasks/current.json`) | Explicit, per-task, clean | Requires a TaskBundle spec that does not yet fully exist in Phase 1 output — adds Phase 2→Phase 1 coupling |
| **O2. Convention file** | Each project drops a `.mercury/test-command` file or `mercury.test.cmd` script; adapter reads/executes it | No task-level awareness needed; works at project scope | One command per project, no per-task differentiation; may run the entire test suite every Stop |
| **O3. Env var** | Main agent exports `MERCURY_TEST_COMMAND` before dispatching dev sub-agent; adapter reads from env | Simple, per-dispatch | Env var must propagate through Claude Code's sub-agent spawn; unclear if SubagentStop hook inherits dispatcher env |
| **O4. Auto-detect** | Adapter probes for `package.json` scripts.test → `npm test`; `pytest.ini` → `pytest`; `Cargo.toml` → `cargo test`; etc. | Zero configuration | Guesses wrong in polyglot repos; no signal when test command doesn't exist |

**Design recommendation**: **O2 + O4 fallback**. Ship with auto-detect (O4) so the adapter is useful out of the box; allow projects to override via a `.mercury/test-command` file (O2) when auto-detect is wrong. O1 can be added later if/when TaskBundle is formalized. O3 is too fragile.

**Fail-open vs. fail-closed** when no command is resolved: the pseudocode in §3.2 fails **open** (let Stop proceed with a log warning). Rationale: a fail-closed default would make the gate impossible to dismiss for projects that intentionally have no tests (e.g., docs-only repos), creating a brittle UX where every new project hits the gate on day one. Users who want fail-closed behavior can set a project-level `MERCURY_TEST_GATE_STRICT=1` env var.

### 3.5 Windows compatibility

Mercury runs on Windows 11 (per CLAUDE.md). Windows concerns for Path β:

| Concern | Mitigation |
|---|---|
| Shell — `hook.sh` vs. `hook.cmd` | Ship a polyglot wrapper (pattern used by `obra/superpowers/hooks/run-hook.cmd`: batch header + bash body with `:` no-op). On Windows, cmd.exe runs the batch header which locates Git for Windows' `bash.exe`. On Unix, the `:` no-op passes through. Alternatively, ship a Node.js `hook.cjs` — Node is a hard dependency Claude Code users already have. |
| Test command timeouts | Use Node.js `child_process.spawn` with `timeout` option rather than POSIX `timeout`/GNU coreutils, which is not reliably on Windows PATH. |
| Path handling | Use forward slashes in emitted paths; trust Node.js path module. |
| Line endings | Ensure `hook.cjs` has LF endings in repo; `.gitattributes` entry for `*.cjs text eol=lf`. |
| MSYS2 PATH leakage | Test runner invocation should use `shell: false` + explicit argv array to avoid MSYS2 path mangling (known issue: slash commands getting rewritten). |

**Recommended implementation language**: Node.js (`.cjs`). Rationale:
- Claude Code is Node.js — runtime is guaranteed available
- Consistent behavior across Win32, MSYS2, Git Bash, Linux, macOS
- Native `child_process.spawn` handles timeout, stdin, stdout cleanly
- JSON I/O is first-class (no jq dependency)
- OMC itself uses `.cjs` for `persistent-mode.cjs` — same runtime ecosystem

### 3.6 Effort summary — Path β

| Dimension | Estimate |
|---|---|
| New Mercury code | 80–150 LOC (fits <200 cap) — breakdown: hook.cjs ~60, resolve-command.cjs ~30, run-and-check.cjs ~40, README ~20 |
| New tests | ~100 LOC of adapter unit tests (exit-code paths, timeout path, re-entry guard, no-command-resolved path) |
| New docs | `adapters/mercury-test-gate/README.md` + `PHILOSOPHY.md` (why mechanical beats LLM-level) |
| Hours to Phase 2 done | 6–10 hours (dev + test + dual-verify + Argus review cycle) |
| Ongoing maintenance | Low — isolated adapter, stable Claude Code hook API surface, no OMC-internal coupling |
| Dependencies added | 1 (OMC plugin — same as Path α) + Node.js (already a hard dep) |
| Mount-first adherence | Full — OMC is mounted and untouched; Mercury adapter is orthogonal |
| Semantic guarantee strength | **Strong** — harness-level, mandatory, exit-code-based, cannot be skipped by the agent |

---

## 4. Comparison matrix

| Dimension | Path α (relax) | Path β (mount + adapter) |
|---|---|---|
| LOC delta | 0 | 80–150 |
| Semantic guarantee | LLM-level, opt-in | Mechanical, harness-level, mandatory |
| Mount-first principle | Full | Full |
| Time to Phase 2 done | 1–2 hours | 6–10 hours |
| Ongoing maintenance | ~0 | Low (isolated adapter) |
| Windows compatibility | Same as OMC (patched, tested) | Adapter must ship polyglot wrapper — manageable |
| Failure mode R1 (agent skips gate) | **Full blast radius** (no gate runs) | Impossible (hook fires regardless of agent intent) |
| Failure mode R4 (UltraQA max-cycles exit with red tests) | **Full blast radius** (exit "tried enough") | Impossible |
| Failure mode: hook script crashes | N/A | Tests pass (fail-open) — same risk as any hook |
| Failure mode: test command wrong/missing | N/A | Fail-open with log warning (design choice; §3.4) |
| Reversibility | High — just re-tighten the EXECUTION-PLAN wording | High — disable the hook in settings.json |
| Dependency surface | OMC plugin | OMC plugin + Node.js (already required) |
| Ships mechanical exit-code check | No | Yes |
| Enforces on ALL dev sub-agent stops | No (only when UltraQA is invoked) | Yes (every SubagentStop event matching scope) |
| Creates Mercury-branded Quality Gate artifact | No (delegates entirely to OMC) | Yes (publishable as standalone adapter in future) |
| Enables methodology writeup | Weak — "we accepted a gap" | Strong — "mechanical test gating as a ~100 LOC adapter on top of a mounted scaffold" |

---

## 5. Hidden third paths

### 5.1 Path γ — Belt and suspenders (relax criterion AND write adapter)

**What it is**: implement Path β exactly as described, but ALSO re-word the EXECUTION-PLAN.md criterion to acknowledge that the gate is now a Mercury-owned adapter on top of OMC, not a pure mount.

**Why consider it**: the re-wording is honest. Path β does not, strictly, mount a ready-made Quality Gate from an external project — it mounts a Stop-hook-capable scaffold (OMC) and adds Mercury's own enforcement layer on top. The original Phase 2 text ("实现 agent stop 拦截") and acceptance criterion can both remain mechanical AND the Consequences section of this ADR can record that the enforcement layer is Mercury-owned.

**Evaluation**: this is not really a separate path — it is Path β + honest documentation. Recommendation: **fold this into Path β**. When executing Path β, the EXECUTION-PLAN.md update should explicitly note "enforcement is Mercury-owned adapter at `adapters/mercury-test-gate/`, mounted on top of OMC scaffold". No separate evaluation needed.

### 5.2 Path δ — Self-research (Mercury-native Stop hook, no OMC mount)

**What it is**: skip OMC entirely. Write `adapters/mercury-test-gate/` as a standalone Claude Code hook with no external dependency.

**Pros**:
- Zero external dependency
- Slightly simpler (no need to reason about OMC's hook also being present)
- Fewer failure surfaces (no upstream breakage risk)

**Cons**:
- Violates the mount-first principle spirit: OMC ships a usable scaffold; reinventing Stop-hook registration/wiring from scratch is the exact self-research outcome CLAUDE.md forbids
- Forgoes OMC's architect/critic/executor agent library, UltraQA mode, and Ralph loop — all of which are still valuable even if Mercury has its own test gate
- Mercury owner's mount-first principle is explicitly "if an external project can solve the problem, mount it" — OMC solves the Stop-hook-registration problem even if it doesn't solve the exit-code-check problem
- The actual Mercury adapter in Path β is ~100 LOC regardless of whether OMC is present; removing OMC does not meaningfully reduce adapter complexity

**Evaluation**: **dismiss unless Path β execution reveals an OMC-specific blocker** (e.g., OMC plugin install breaks on Windows, OMC's hook conflicts with Mercury's at the `.claude/settings.json` level, licensing incompatibility). In that contingency, Path δ becomes the fallback — the adapter code itself is unchanged, only the `modules/omc/` mount step is skipped.

### 5.3 Paths considered and not pursued

- **Patch OMC upstream** to add an exit-code-check mode: high coordination cost, unpredictable timeline, does not solve Phase 2 this session.
- **Fork OMC** and maintain a Mercury branch: maintenance burden, diverges from upstream, exactly the anti-pattern mount-first exists to prevent.
- **Mount a completely different project not in DIRECTION.md**: would require a new Phase 2-1 research round. Given 4 candidates have been exhausted, there is low confidence a 5th candidate with the exact needed feature exists.

---

## 6. Recommendation

### 6.1 Design agent's opinion: **Path β (mount + thin adapter)**

**Primary reasoning**:

1. **Path α's R1 and R4 failure modes defeat the whole point of Phase 2.** The criterion exists because the agent is not trusted to know when tests pass. An opt-in, LLM-judged gate that exits "tried enough" on hard bugs is structurally the same thing as no gate — it just has more ceremony around the moment the agent decides to give up. Relaxing to LLM-level enforcement does not just weaken the guarantee; it **eliminates the mechanism** that Phase 2 was created to provide.

2. **Path β is cheap.** 80–150 LOC, 6–10 hours. This is less effort than the Phase 2-1 research rounds have already consumed on a single candidate. The cost/benefit math is heavily in favor of "just write it".

3. **Path β honors mount-first where it matters.** OMC is mounted. Mercury does not re-implement Stop hook scaffolding, plugin loading, or agent wiring — those are provided by OMC and Claude Code. Mercury only adds the one mechanical primitive (run command, check exit code) that no candidate ships. This is the correct reading of the mount-first principle: *reuse what exists, write only the load-bearing gap*.

4. **The <200 LOC adapter cap in CLAUDE.md exists for exactly this case.** The rule says adapters OVER 200 lines indicate coupling is too deep. The converse is: adapters UNDER 200 lines are the expected, sanctioned way to bridge an external project to Mercury's needs. Path β is designed to land at 80–150 LOC specifically because this cap signals "this is the right size for a bridge".

5. **Path β is the methodology-writeup-friendly path.** Mercury's DIRECTION.md positions the project as a modular harness with publishable methodology. "We wrote a 100-line Stop hook adapter that mechanically gates dev agents on test exit codes" is a crisp, publishable artifact. "We accepted an LLM-level gate because no external project shipped the mechanism" is not.

6. **Reversibility is symmetric.** Both paths are reversible. But Path β leaves Mercury with a real capability even if it is later retired; Path α leaves nothing behind.

### 6.2 When to pick Path α instead

Path α becomes the correct choice under any of these conditions:

- **Time pressure**: the user needs Phase 2 marked done this session, not next session. Path α is 1–2 hours; Path β is 6–10 hours.
- **Phase 2 is a stepping stone, not a destination**: if Phase 3 / Phase 4 will supersede or rewrite the Quality Gate anyway (e.g., Session Continuity might introduce a fundamentally different completion model), investing 10 hours in a mechanical adapter is waste.
- **User does not want new Mercury-owned infrastructure**: if the project philosophy is to own zero hook adapter code regardless of cost, Path α is the only path that honors that preference.
- **Willingness to revisit**: if the user is willing to file a follow-up Issue like "Issue #X — upgrade Phase 2 gate to mechanical enforcement" and leave the weak gate in production until then.

### 6.3 What the design agent cannot decide

This is a user decision. The design agent's job is to make the trade-off legible — not to commit to it. The recommendation above is based on technical cost/benefit and adherence to stated Mercury principles. The user may legitimately override based on factors outside the design agent's visibility (total project timeline, appetite for adapter maintenance, methodology writeup priorities, whether Phase 2 is load-bearing for Phase 3/4).

---

## 7. Open questions for user (must answer before work starts)

### 7.1 Questions gating Path β

| # | Question | Why it matters | Design agent's suggested default |
|---|---|---|---|
| Q1 | What is the source of truth for the test command? TaskBundle field (O1), convention file (O2), env var (O3), auto-detect (O4), or hybrid? | Drives `resolve-command.cjs` architecture | O2 (convention file) + O4 (auto-detect fallback) |
| Q2 | Fail-open or fail-closed when no test command resolves? | Determines UX on new / docs-only projects. **Argus PR #205 review (2026-04-08) flagged that the suggested fail-open default conflicts with the mechanical-acceptance semantics in EXECUTION-PLAN §2-3 — the recommended default is now contested and treated as an open question, NOT a settled answer.** | ~~Fail-open with warning; opt-in strict mode via env var~~ — **CONTESTED**, see PR #205 review thread |
| Q3 | Which agent types are in scope? Only `dev`? Also `acceptance`? Also `critic`? | Drives the agent-type matcher in the hook | Start with `dev` only; extend if needed |
| Q4 | Where does the adapter live — `adapters/mercury-test-gate/` or `.claude/hooks/mercury-test-gate/`? | Affects packaging and future standalone extraction | `adapters/mercury-test-gate/` (consistent with DIRECTION.md modular principle — adapter is independently detachable) |
| Q5 | Hook language — Node.js `.cjs`, bash polyglot, or Python? | Drives Windows compat story | Node.js `.cjs` (see §3.5) |
| Q6 | Default test-run timeout? | Drives `MERCURY_TEST_GATE_TIMEOUT_SEC` default | 300s (5 min), env-overridable |
| Q7 | Should the adapter also run under the plain `Stop` event (main agent), or only `SubagentStop` (dev sub-agent)? | Determines scope — original criterion says "dev sub-agent" specifically | Only `SubagentStop` — matches criterion wording exactly |
| Q14 | What is the policy when `INPUT.stop_hook_active == true` (re-entry after a prior block)? Current pseudocode lets Stop proceed to avoid infinite loops. **Argus PR #205 review (2026-04-08) flagged that this creates a "block once then bypass" path — the agent gets blocked once with red tests, then re-attempts Stop and succeeds.** Options: (i) current "block-once" semantics (vulnerable), (ii) bounded retry (e.g. block up to N consecutive Stop attempts within a session window), (iii) configurable per-agent strict mode that blocks unconditionally and relies on a separate timeout to escape infinite-loop scenarios. | Determines whether the mechanical guarantee is real or only single-shot | UNDECIDED — design subagent's original "let through on re-entry" is now treated as a placeholder |
| Q15 | The pseudocode emits `{"decision": null}` to express "no opinion, let Stop proceed". **Argus PR #205 review (2026-04-08) noted that `null` is NOT in the [Claude Code Stop hook spec](https://code.claude.com/docs/en/hooks)** — only `decision: "block"` (with `reason`) or no JSON output (= no opinion) or exit code 2 are defined. Replace with: (i) empty JSON `{}` on stdout, (ii) no stdout output at all + exit 0, or (iii) verify `decision: null` against the latest Anthropic docs in case the spec has been extended. | Determines whether the implementation produces undefined-behavior output that may be interpreted differently across Claude Code versions | Replace with **no stdout output + exit 0** as the spec-safe "no opinion" path |

### 7.2 Questions gating Path α

| # | Question | Why it matters |
|---|---|---|
| Q8 | Is the user explicitly willing to accept that a dev sub-agent *can* stop with failing tests in the R1/R4 scenarios? | This is the load-bearing trade-off; no implementation can proceed without explicit user acknowledgment |
| Q9 | What is the EXECUTION-PLAN.md §2-3 re-worded acceptance criterion text? | The design agent proposes: *"Dev sub-agent in a running UltraQA cycle cannot mark completion while tests are failing, per OMC's LLM-level gate. Hard exit on UltraQA max-cycles is accepted and surfaces as a user-visible incident."* User must confirm or revise. |
| Q10 | Is the user willing to enforce "dev sub-agent MUST invoke UltraQA mode" in `.claude/agents/dev.md`, and accept that the enforcement is also LLM-level (prompt discipline)? | R1 is only partially mitigated if the agent simply forgets to invoke UltraQA |

### 7.3 Questions common to both paths

| # | Question | Why it matters |
|---|---|---|
| Q11 | Is OMC mounted as a git submodule under `modules/omc/`, or purely via `/plugin install oh-my-claudecode`? OMC ADR §4 notes *"only the Claude Code Plugin method is supported"* — submodule path is undocumented. | Affects whether Mercury has source-level visibility into OMC, which matters for Path β Layer-model debugging |
| Q12 | Does the user want a Phase 2 completion ADR file, or should the decision live only in this design doc + EXECUTION-PLAN.md update? | Affects documentation layout |
| Q13 | Should this design doc itself be committed before work starts, or only committed as part of the implementation PR? | Affects reviewability and audit trail |

---

## 8. Appendix — raw Claude Code Stop hook API reference (verified 2026-04-08)

Source: `https://code.claude.com/docs/en/hooks` — fetched by design agent this session via WebFetch.

**SubagentStop input JSON** (fields relevant to Mercury adapter):

```json
{
  "session_id": "abc123",
  "transcript_path": "~/.claude/projects/.../transcript.jsonl",
  "cwd": "/absolute/path/to/project",
  "permission_mode": "default",
  "hook_event_name": "SubagentStop",
  "stop_hook_active": false,
  "agent_id": "def456",
  "agent_type": "dev",
  "agent_transcript_path": "~/.claude/projects/.../abc123/subagents/agent-def456.jsonl",
  "last_assistant_message": "..."
}
```

**Two ways to block**:

1. **Exit code 2** — stderr fed back to Claude as error message.
2. **Exit code 0 + JSON on stdout**:
   ```json
   {"decision": "block", "reason": "message shown to Claude, not to user"}
   ```

**Critical re-entry flag**: `stop_hook_active: true` means the Stop hook is running as a result of a prior block. Hooks must handle this to avoid infinite loops. Mercury adapter should emit no decision (let Stop proceed) when `stop_hook_active == true`, logging a warning if tests still fail — this is the "we already told Claude once, we are not going to spin forever" semantics.

**Official decision effects**:

| Event | Blockable | Effect of `decision: "block"` |
|---|---|---|
| `Stop` | Yes | Prevents Claude from stopping, continues the conversation |
| `SubagentStop` | Yes | Prevents the subagent from stopping |

---

## 9. Summary for the reader in a hurry

- All 4 DIRECTION.md Quality Gate candidates have been evaluated; only OMC ships any Stop hook scaffold, and its gate is LLM-level, not mechanical exit-code.
- Two real options:
  - **Path α**: accept OMC's LLM-level gate, update EXECUTION-PLAN wording, 0 LOC, 1–2 hours. Weakens the guarantee — R1 (agent skips gate) and R4 (UltraQA max-cycles exit with red tests) are full-blast-radius failure modes.
  - **Path β**: mount OMC AND write a thin Mercury adapter (`adapters/mercury-test-gate/`, ~80–150 LOC Node.js hook). 6–10 hours. Keeps the guarantee strong and mechanical. Layer model — adapter is orthogonal to OMC's own hook.
- Design agent recommends **Path β** on technical grounds. Path α is legitimate if time pressure or Phase 2 throwaway-ness dominates.
- 15 open questions listed in §7 for user to answer before implementation begins — most critical are Q1 (test command source), Q2 (fail mode default — **CONTESTED post-Argus-review**), Q3 (agent type scope), Q5 (hook language), Q14 (`stop_hook_active` re-entry bypass — **CONTESTED post-Argus-review**), Q15 (`{"decision": null}` not in spec — **CONTESTED post-Argus-review**), and (for Path α) Q8 (explicit acknowledgment of R1/R4 risk).
- A Path γ (belt-and-suspenders) folds into Path β. A Path δ (self-research, skip OMC) is dismissed as a mount-first violation unless OMC proves unmountable.
- This is a **user decision**. The design agent frames the trade-off; the owner commits.

---

*End of design document. Status remains: Proposal — awaiting user decision.*
