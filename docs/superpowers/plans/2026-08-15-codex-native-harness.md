# Codex-Native Mercury Harness Implementation Plan

> **状态（2026-08-15）：已被 Issue #576 当前决策取代，仅作历史执行记录。** 项目级
> Codex hooks 已退役；`.codex/hooks.json`、旧 hook probe 与测试脚本已删除，不得执行
> 下文 hook 重建步骤。当前 Codex 防护由 `.codex/rules` 强制，`scripts/codex/` 包装
> 脚本与指令层补充；`.claude/hooks/` 仍服务 Claude Code。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace imported Claude-era instructions, skills, agents, hooks, workflow control, and context handling with tested Codex-native equivalents while preserving Mercury's issue, review, and single-writer contracts.

**Architecture:** `AGENTS.md` remains the repository authority, project skills live under `.agents/skills`, subagents live under `.codex/agents`, and official hooks call small versioned adapters. A test harness feeds recorded official payload fixtures into every hook and runs native multi-agent message probes; imported artifacts stay disabled until parity is proven.

**Tech Stack:** Codex Hooks/Skills/Subagents, Python 3, PowerShell 7, Bash compatibility scripts, TOML/JSON, unittest.

**Spec:** `.mercury/docs/design/2026-08-14-codex-native-import-design.md`

## Global Constraints

- `AGENTS.md` is the active repository instruction source; `CLAUDE.md` is migration input only.
- `.agents/skills` is the final Mercury project-skill source; Superpowers 6.3.0 is the single methodology source for overlapping skills.
- `.codex/agents` contains only callable subagents; Claude's `main` role maps to the root agent and is not duplicated.
- Hook enablement occurs only after every configured source event class and every handler pass success, deny, timeout, malformed-input, and read-only fixtures.
- `SessionEnd` has a 3-second hard limit and a 2.5-second soft deadline; it snapshots at most 64 MiB and never invokes Git, an LLM, a daemon, or a background worker.
- Native agent concurrency, budgets, timeouts, cancellation, truncation, and non-convergence are explicit in every workflow entry.
- Existing dirty paths and user-level Codex state are not committed.
- Before every repository mutation or stage, run `preflight_snapshot.py guard` with the exact planned paths; compare branch, HEAD ancestry, status, dirty bytes/hashes, and target overlap. Stop on drift.
- Every checkpoint uses the shared mandatory transaction: `begin-approved` → `guard` → edit/test/two reviews/explicit commit → `seal-approved` → chain verification → push. An interrupted row must be byte-identically aborted or it blocks subsequent work.

---

### Task 1: Convert instruction authority and remove stale capability claims

**Files:**
- Modify: `AGENTS.md`
- Modify: `.codex/config.toml`
- Modify outside Git: `C:\Users\392fy\.codex\config.toml`
- Create: `tests/codex/harness/test_instruction_contract.py`

**Interfaces:**
- Consumes: imported instructions, approved design, and current Codex official capability set.
- Produces: a root instruction contract with no dependency on Claude role injection, tmux teams, external orchestrators, or nonexistent hook limitations.

- [ ] **Step 1: Write a failing instruction-contract test**

```python
FORBIDDEN = (
    "hooks docs 404",
    "Codex has no native workflow",
    "role injected by orchestrator",
    "tmux agent team required",
)

def test_agents_uses_native_codex_contract():
    text = Path("AGENTS.md").read_text(encoding="utf-8").lower()
    assert all(term not in text for term in FORBIDDEN)
    assert "scripts/codex/git-safe.ps1" in text
    assert "single-writer" in text
```

- [ ] **Step 2: Run the test and observe stale-claim failures**

Run: `python -m unittest tests.codex.harness.test_instruction_contract -v`

Expected: FAIL on at least one stale statement.

- [ ] **Step 3: Rewrite only the obsolete sections**

Preserve issue-first, dual review, Git-safe, tool-call XML lint, and single-writer rules. Replace external-orchestrator language with Codex `spawn_agent`, direct messages, follow-ups, plan/goal tracking, hooks, and official Import semantics. Add the four canonical roots and the design-library field-authority decision.

- [ ] **Step 4: Configure long-context scope without overriding the model window**

Add exactly `model_auto_compact_token_limit_scope = "body_after_prefix"` to `C:\Users\392fy\.codex\config.toml`. Do not add `model_context_window` or a custom compaction threshold. Record the pre/post config hashes in the external migration ledger; do not commit the user config.

- [ ] **Step 5: Run tests and config parsing**

Run:

```powershell
python -m unittest tests.codex.harness.test_instruction_contract -v
python -c "import tomllib,pathlib; tomllib.loads(pathlib.Path('.codex/config.toml').read_text(encoding='utf-8')); print('TOML PASS')"
codex --version
```

Expected: test passes, TOML parses, Codex remains 0.147.0 or the same installed build used for the Import.

- [ ] **Step 6: Review, commit, and push the instruction checkpoint**

Stage only `AGENTS.md`, `.codex/config.toml`, and the test; obtain two reviews; commit `docs(TASK-571): adopt Codex-native harness contract`; push through `git-safe.ps1`.

### Task 2: Reconcile project skills without duplicate methodology

**Files:**
- Modify: `.agents/skills/animate-frames/SKILL.md`
- Modify: `.agents/skills/autoresearch/SKILL.md`
- Modify: `.agents/skills/caveman-toggle/SKILL.md`
- Modify: `.agents/skills/dev-pipeline/SKILL.md`
- Modify: `.agents/skills/dual-verify/SKILL.md`
- Modify: `.agents/skills/handoff/SKILL.md`
- Modify: `.agents/skills/mercury-subagent-driven-development/SKILL.md`
- Modify: `.agents/skills/pr-flow/SKILL.md`
- Modify: `.agents/skills/sot-pixel-pipeline/SKILL.md`
- Modify: `.agents/skills/web-research/SKILL.md`
- Create: `tests/codex/harness/test_skill_inventory.py`

**Interfaces:**
- Consumes: 45 imported user-level skills, 10 user-level agents, 1 command, 12 Mercury skills, and installed Superpowers 6.3.0 skills.
- Produces: ten reconciled Mercury-specific skills whose descriptions trigger distinctly and whose methodology dependencies name installed Superpowers instead of copying them.

- [ ] **Step 1: Write a failing inventory and duplicate-name test**

Build the exact ten-name core set from the already tracked tree above and the separately approved five-name workflow-replacement set from the legacy plan. Enumerate `.agents/skills/*/SKILL.md` and installed Superpowers names. In `--phase core`, require exactly the ten core entries; in `--phase final`, require the ten core plus the five hash-bound native workflow entries. In both phases, reject every other project skill, shadowing of `systematic-debugging`, `verification-before-completion`, or `subagent-driven-development`, frontmatter/directory mismatch, Claude-only tools, tmux teams, role injection, or the retired Dynamic Workflow runtime.

- [ ] **Step 2: Run the test and observe missing `.agents/skills` failures**

Run: `$env:MERCURY_SKILL_PHASE='core'; python -m unittest tests.codex.harness.test_skill_inventory -v; Remove-Item Env:MERCURY_SKILL_PHASE`

Expected: FAIL on stale Claude-era contracts or duplicated methodology in the existing tracked skill tree.

- [ ] **Step 3: Classify every imported global skill, agent, and command**

Write one ledger row for each of the 45 user-level skills, 10 agents, and 1 command. Final states are `active-native`, `shadowed-by-superpowers`, `project-scoped`, `frozen-archive`, or `exclude-domain`. Require an exact reason and destination for every non-active row; do not delete the imported source in this task.

- [ ] **Step 4: Port Mercury-specific contracts**

For each skill, preserve domain-specific safety and output contracts while replacing Claude tool names with available Codex tools. `dev-pipeline` must invoke installed Superpowers skills by exact name and must use native subagents/messages; `dual-verify` must require independent reviewers with distinct evidence; `handoff` must produce a durable file plus a direct native message.

- [ ] **Step 5: Run discovery and trigger fixtures**

Run the inventory test in `core` phase and start a read-only Codex fixture that asks for each skill by exact name. Capture only the selected skill name and exit status in `.mercury/docs/migration/2026-08-15-skill-discovery.md`. The final acceptance runner switches the same test to `final` phase after legacy Task 1 adds the five approved workflow entries.

Expected: all ten project skills and 14 Superpowers skills are visible; no name resolves to two active sources.

- [ ] **Step 6: Review, commit, and push the skill checkpoint**

Stage exactly the ten target skill files, their required referenced assets, the inventory test, and the discovery report; obtain two reviews; commit `feat(TASK-571): port Mercury skills to Codex`; push through `git-safe.ps1`.

### Task 3: Reconcile native subagents and peer messaging

**Files:**
- Modify: `.codex/agents/acceptance.toml`
- Modify: `.codex/agents/critic.toml`
- Modify: `.codex/agents/design.toml`
- Modify: `.codex/agents/dev.toml`
- Modify: `.codex/agents/game-analyst.toml`
- Modify: `.codex/agents/game-critic.toml`
- Modify: `.codex/agents/game-researcher.toml`
- Modify: `.codex/agents/research.toml`
- Create: `scripts/codex/harness/agent-probe.ps1`
- Create: `tests/codex/harness/test_agent_configs.py`

**Interfaces:**
- Consumes: nine imported Claude role definitions, excluding `main` as a callable subagent.
- Produces: eight valid Codex agent configs and a probe report for root↔agent, agent↔agent, follow-up, cancellation, and different-effort dual review.

- [ ] **Step 1: Write failing schema and isolation tests**

Parse every TOML file; require nonempty description/developer instructions, bounded reasoning effort, no Claude-only tool names, and no writable path outside the assigned root. Assert the set is exactly the eight names listed above.

- [ ] **Step 2: Run tests and record current differences**

Run: `python -m unittest tests.codex.harness.test_agent_configs -v`

Expected: any imported mismatch fails with its exact file and key.

- [ ] **Step 3: Normalize each agent to its domain role**

Keep acceptance and critic read-only by default; scope game roles to SoT roots; scope dev to the assigned repository; require each role to return evidence and unresolved risks. Do not define a ninth `main.toml`.

- [ ] **Step 4: Implement the native messaging probe**

The PowerShell harness invokes a Codex task that spawns two read-only agents with `fork_turns="none"`, sends token `MERCURY_NATIVE_571`, requires a peer acknowledgement, sends a follow-up to one idle agent, cancels a bounded sleeper, and asks high/xhigh reviewers for independent verdicts. Persist and mechanically assert spawn provenance, `fork_turns="none"`, distinct agent IDs, and distinct `high`/`xhigh` reasoning-effort fields. Parse JSON output and fail on missing tokens, shared authorship, inherited conversation evidence, identical efforts, timeout, or uncancelled work.

- [ ] **Step 5: Run tests and probe**

Run:

```powershell
python -m unittest tests.codex.harness.test_agent_configs -v
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/codex/harness/agent-probe.ps1
```

Expected: schema passes; all five native behaviors pass within declared timeouts.

- [ ] **Step 6: Review, commit, and push the agent checkpoint**

Stage the eight TOMLs, probe, and test; obtain two reviews; commit `feat(TASK-571): verify native Mercury subagents`; push through `git-safe.ps1`.

### Task 4: Implement official-hook payload adapters（历史步骤，禁止执行）

**Files:**
- Create: `scripts/codex/hooks/common.py`
- Create: `scripts/codex/hooks/session_start.py`
- Create: `scripts/codex/hooks/session_end.py`
- Create: `scripts/codex/hooks/session_queue.py`
- Create: `scripts/codex/hooks/pre_compact.py`
- Create: `scripts/codex/hooks/user_prompt_submit.py`
- Create: `scripts/codex/hooks/pre_tool_use.py`
- Create: `scripts/codex/hooks/post_tool_use.py`
- Create: `scripts/codex/hooks/subagent_stop.py`
- Create: `scripts/codex/hooks/stop.py`
- Create: `tests/codex/hooks/test_event_matrix.py`
- Create: `tests/codex/hooks/fixtures/*.json`
- Modify: `.codex/hooks.json`
- Modify outside Git after tests: `C:\Users\392fy\.codex\hooks.json`

**Interfaces:**
- Consumes: official hook JSON on stdin and existing guard scripts.
- Produces: official JSON responses, translated guard calls, durable SessionEnd queue entries, and structured logs with no transcript content.

- [ ] **Step 1: Capture sanitized official fixtures**

Create one minimal fixture for `SessionStart`, `SessionEnd`, `PreCompact`, `PostCompact`, `UserPromptSubmit`, `PreToolUse`, `PermissionRequest`, `PostToolUse`, `SubagentStart`, `SubagentStop`, and `Stop`. Replace real paths and prompt text with fixture values; preserve field names and types.

- [ ] **Step 2: Write a failing handler matrix**

For every configured handler, test allow/success, deny/continue, malformed JSON, child nonzero, and 2-second timeout. Add explicit cases for Bash nonzero visible through `PostToolUse` and for tool failures that have no observable equivalent to Claude `PostToolUseFailure`.

- [ ] **Step 3: Implement pure adapters**

`common.py` parses one JSON object, validates event name, runs an exact allowlisted child, enforces timeout, and emits one JSON object. Never use `shell=True`. Translate Codex tool names to the existing guard's expected categories; record unsupported failures as capability gaps instead of silently passing. `session_queue.py` owns the complete synchronous producer/consumer state machine; no scheduler, daemon, or external worker may be required.

- [ ] **Step 4: Implement durable SessionEnd snapshot semantics**

Use `<CODEX_HOME>\state\session-end-queue`; reject reparse/network/cloud-sync paths; verify ACL user+SYSTEM. Stream at most 64 MiB to a same-volume snapshot `.tmp`, flush/fsync, compute SHA-256, atomically rename the snapshot; write and fsync a size/hash sidecar `.tmp`, atomically rename the sidecar; only then write/fsync/rename the queue envelope. Fail nonzero at the 2.5-second soft deadline. Before consumption, recheck sidecar/envelope agreement, declared size, and SHA-256. Write an idempotent result record before moving an item to `processed`; retain it for a verified grace period and delete it no later than 24 hours after successful consumption. Move checksum failures, malformed envelopes, and orphan payloads into a durable `quarantine` with a reason. Enforce a 1 GiB total queue cap. Each `SessionStart` consumes at most 4 items, 128 MiB, or 1.5 seconds—whichever comes first—and records exact residue count/bytes; larger work requires an explicitly registered Codex-native task with the same idempotency rules. If a quarantined item reaches 30 days, block further automatic cleanup and require a recorded user decision. Never silently delete unprocessed data.

Add fixtures for producer crash at every snapshot/sidecar/envelope rename boundary, duplicate consumption, checksum/size mismatch, per-start item/byte/time caps with residue, capacity exhaustion, 24-hour retention, 30-day quarantine, orphan recovery from sidecar, result persistence, and absence of any scheduler/daemon invocation. Sample sanitized real transcript sizes from both stores (459 Codex sessions, observed maximum about 5.83 MiB; 1,873 Claude sessions, observed maximum about 28.73 MiB; none above 64 MiB) and test the actual distribution plus the 64 MiB boundary.

- [ ] **Step 5: Run the complete hook matrix while hooks remain disabled**

Run:

```powershell
python -m unittest tests.codex.hooks.test_event_matrix -v
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/codex/hook-probe.ps1
```

Expected: every handler/event row and every queue lifecycle fixture passes; unsupported failure classes are empty or have explicit user-accepted degradation records; SessionEnd p99 fixture time is below 2.5 seconds; the producer-consumer E2E leaves a verified idempotent result.

- [ ] **Step 6: Install exact-hash user hooks and enable the feature**

Copy only reviewed user-level adapters to `C:\Users\392fy\.codex\hooks`; write their SHA-256 values into the migration ledger; update user and project `hooks.json`; set `features.hooks = true` only after `/hooks` displays the expected commands and hashes and the complete producer-consumer E2E passes. Keep all previous handler bytes/config recoverable and do not retire an active predecessor until that point; imported duplicate handlers remain disabled during testing.

- [ ] **Step 7: Review, commit, and push the hook checkpoint**

Stage hook sources, fixtures, tests, `.codex/hooks.json`, and no user-level file; obtain two reviews; commit `feat(TASK-571): migrate Mercury hooks to Codex events`; push through `git-safe.ps1`.

### Task 5: Verify rules independently of hooks

**Files:**
- Modify: `.codex/rules/default.rules`
- Create: `scripts/codex/harness/rules-probe.ps1`
- Create: `tests/codex/harness/test_rules_contract.py`

**Interfaces:**
- Consumes: destructive-command and sensitive-path policy from existing Claude/Codex rules.
- Produces: deny/ask/allow decisions that work with hooks disabled.

- [ ] **Step 1: Write rule contract tests**

Include fixtures for a harmless read, explicit-file `git-safe` staging, broad Git staging, protected-branch push, recursive deletion of a drive root, and direct access to credential files.

- [ ] **Step 2: Run tests and identify missing independent protections**

Run: `python -m unittest tests.codex.harness.test_rules_contract -v`

Expected: current missing or overbroad rules fail with their fixture name.

- [ ] **Step 3: Update rules with the smallest exact patterns**

Keep rules independent from hook state. Allow `scripts/codex/git-safe.ps1` exact-path operations; deny broad destructive targets; require approval for credential stores; avoid rules that block read-only inspection of project files.

- [ ] **Step 4: Run the CLI rule probe with hooks explicitly off**

Run: `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/codex/harness/rules-probe.ps1 -HooksOff`

Expected: every fixture returns the declared decision and no hook executable starts.

- [ ] **Step 5: Review, commit, and push the rules checkpoint**

Stage the rules, probe, and test; obtain two reviews; commit `feat(TASK-571): enforce Mercury safety with native rules`; push through `git-safe.ps1`.

### Task 6: Build one end-to-end harness acceptance command

**Files:**
- Create: `scripts/codex/harness/run-acceptance.ps1`
- Create: `scripts/codex/harness/run-final-transaction.ps1`
- Create: `tests/codex/harness/run-final-transaction.Tests.ps1`
- Create: `.mercury/docs/migration/2026-08-15-native-harness-acceptance.md`

**Interfaces:**
- Consumes: instruction, skill, agent, hook, rule, and context checkpoints.
- Produces: one exit code and a redacted acceptance report.

- [ ] **Step 1: Implement fail-fast orchestration**

Require a mandatory `-SkillPhase core|final` argument; never auto-detect or default it. Run instruction tests, the skill inventory with that explicit phase, agent tests/probe, hook matrix/probe, rules tests/probe, TOML/JSON parsing, and XML lint. Capture command, exit code, duration, and redacted summary; never capture full prompts or transcripts. `run-final-transaction.ps1` executes an exact allowlist of final commands and atomically writes one redacted JSON envelope per command containing command ID, reviewed tool SHA-256, start/end times, exit code, stdout/stderr hashes, and report hash; it never stores raw secret-bearing output.

Write Pester fixtures for success, nonzero exit, missing report, changed tool hash, stale output, partial envelope, and redaction. Require Pester 3.4.0 `FailedCount = 0` before review.

- [ ] **Step 2: Run acceptance from a fresh Codex task**

Run: `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/codex/harness/run-acceptance.ps1 -SkillPhase core`

Expected: exit 0, all sections PASS, and no dependency on `claude.exe`, OMC, tmux, or `packages/codex-orchestrator` at runtime.

- [ ] **Step 3: Review, commit, and push the acceptance report**

Stage both runners, the final-runner tests, and report; obtain two reviews; commit `test(TASK-571): accept Codex-native Mercury harness`; push through `git-safe.ps1`.
