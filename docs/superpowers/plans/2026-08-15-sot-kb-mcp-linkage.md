# SoT, KB, and MCP Linkage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore a single unambiguous SoT authority chain and prove read/write linkage among the design library, Godot project, KB, Godot MCP, and Obsidian MCP.

**Architecture:** The design database/API is the rule authority, SoT owns Godot and designated design data, and KB is a non-authoritative mirror/context layer. MCP tests exercise real read-only tools first; Obsidian then performs one UUID-scoped create/read/hash/delete probe with filesystem diff verification and a local-file fallback.

**Tech Stack:** Godot 4.6.1, `@satelliteoflove/godot-mcp@2.15.0`, Obsidian local MCP HTTP endpoint, Codex MCP configuration, Python/PowerShell, Git.

**Spec:** `.mercury/docs/design/2026-08-14-codex-native-import-design.md`

## Global Constraints

- Design rules live in `D:\ShipOfTheseus\SoT-fyc-space`; snapshots are generator-owned and never hand-edited.
- `power`, `cd`, and `range` all belong to the design library under the approved third-generation decision.
- `D:\ShipOfTheseus\Ship_of_Theseus` is SoT-owned; KB writes are SoT+user only.
- Obsidian configuration uses `bearer_token_env_var = "OBSIDIAN_API_KEY"`; no token value appears in TOML, logs, commits, or chat.
- The token pasted in chat must return 401/403 before any historical chat containing it is imported.
- Godot and Obsidian pass only after real tool calls; `enabled` and a listening TCP port are insufficient.
- All probe writes use a fresh UUID, a create-only path, a pre/post tree diff, and cleanup in `finally`.
- Ownership follows the canonical matrix exactly: design-library `app/tests/scripts` are Mercury-owned; live design data is user+SoT; Godot is SoT-owned; KB is SoT+user; cross-lane documentation is written by its initiating lane. Mercury may send a durable handoff and consume returned commit/hash evidence, but must not write SoT-owned Godot/KB/live-design targets.
- Before every repository mutation or stage, the owning lane runs `preflight_snapshot.py guard` with the exact planned paths and stops on branch, HEAD, dirty-byte/hash, or target-overlap drift.
- For the canonical-authority commit that must precede guard implementation, the SoT lane records the equivalent explicit branch/HEAD/merge-base/status/dirty-byte-hash/target-overlap checks in the protected migration root; this one bootstrap exception expires when Task 4 of the Import plan commits.
- After the guard/ledger tool is frozen, every Mercury/SoT/KB checkpoint uses `begin-approved` → `guard` → owning-lane edit/test/two reviews/explicit commit → `seal-approved` → chain verification → push. The canonical bootstrap evidence is imported as sealed rows; open, post-hoc, or cross-owner rows block later work.

---

### Task 1: Correct SoT field authority and lane documentation

**Files:**
- Modify: `D:\ShipOfTheseus\SoT-fyc-space\docs\mercury-sot-lane-management.md`
- Create: `D:\Mercury\Mercury\tests\codex\sot\test_lane_authority.py`
- Create: `D:\ShipOfTheseus\SoT-fyc-space\docs\migration\2026-08-15-codex-lane-acceptance.md`

**Interfaces:**
- Consumes: the 2026-08-14 inbox decision and current rule API/snapshot.
- Produces: one canonical, non-contradictory single-writer table.

- [ ] **Step 1: Write a failing authority test**

Mercury writes the read-only cross-root test in its own repository, then sends a durable native handoff naming the exact canonical document and acceptance contract to the SoT lane. The SoT lane changes only the initiating-lane documentation and returns its agent ID, repository HEAD, commit ID, test command, exit code, and report hash.

```python
def test_power_cd_range_have_one_authority():
    text = Path("docs/mercury-sot-lane-management.md").read_text(encoding="utf-8")
    for field in ("power", "cd", "range"):
        assert canonical_owner(text, field) == "design-library"
        assert conflicting_owners(text, field) == []
```

Also assert design app/tests/scripts owner is Mercury, live design data owner is user+SoT, snapshots are generator-only, Godot owner is SoT, KB owner is SoT+user, and cross-lane docs owner is the initiator.

- [ ] **Step 2: Run the test and reproduce the contradictory lines**

Run from `D:\Mercury\Mercury`: `python -m unittest tests.codex.sot.test_lane_authority -v`

Expected: FAIL on the old statements around the formerly conflicting field rules.

- [ ] **Step 3: Edit the canonical document and preserve decision provenance**

Replace the contradictory assignments; cite the exact inbox decision path/date; state that KB `rules-catalog` is a mirror and design-table conflicts resolve to the design table. Do not change live rule values in this task.

- [ ] **Step 4: Verify rule API and snapshot parity without writing the design repository**

Fetch `http://192.168.0.254:8400/api/rules`, load `snapshots/rules.json`, compare 23 normalized records field-by-field, and record count/hash/result in the acceptance document.

Expected: HTTP 200, 23 records on both sides, zero normalized differences.

- [ ] **Step 5: Review and commit in each owning repository**

In the design-library repository, the SoT initiating lane stages only the canonical doc and acceptance record and commits one reversible documentation checkpoint. In Mercury, stage only the read-only test after it passes. Obtain Mercury+SoT review for both checkpoints; never stage the Mercury-owned test from the SoT lane.

### Task 2: Port the five SoT project skills to Codex contracts

**Files:**
- Modify: `D:\ShipOfTheseus\Ship_of_Theseus\.agents\skills\sot-designlib\SKILL.md`
- Modify: `D:\ShipOfTheseus\Ship_of_Theseus\.agents\skills\sot-kb-write\SKILL.md`
- Modify: `D:\ShipOfTheseus\Ship_of_Theseus\.agents\skills\sot-session-end\SKILL.md`
- Modify: `D:\ShipOfTheseus\Ship_of_Theseus\.agents\skills\sot-session-start\SKILL.md`
- Modify: `D:\ShipOfTheseus\Ship_of_Theseus\.agents\skills\sot-task-receipt\SKILL.md`
- Create: `D:\ShipOfTheseus\Ship_of_Theseus\tests\codex\test_sot_skill_contracts.py`

**Interfaces:**
- Consumes: the five imported `.claude/skills` and the corrected single-writer table.
- Produces: five discoverable Codex skills with exact local-filesystem/MCP fallbacks and no Claude-only tool names.

- [ ] **Step 1: Write failing discovery and ownership tests**

Enumerate the exact five already tracked target `SKILL.md` files; parse frontmatter; reject `Task`, Claude Agent Team, tmux, and unavailable MCP tool names. Require `sot-designlib` to enforce design authority, `sot-kb-write` to enforce KB ownership and UTF-8 verification, and both session skills to use Codex hook/session semantics.

- [ ] **Step 2: Run tests and observe stale-contract failures**

Run from `D:\ShipOfTheseus\Ship_of_Theseus`: `python -m unittest tests.codex.test_sot_skill_contracts -v`

Expected: FAIL on stale Claude-only contracts in the existing `.agents/skills` tree.

- [ ] **Step 3: Port each skill with an explicit tool contract**

The SoT lane performs the edits. Use available Codex filesystem, native messaging, Godot MCP, and Obsidian MCP names. Every mutating skill states its owning lane, allowed roots, pre-write baseline, write verification, and fallback when the optional MCP is unavailable. `sot-session-end` must enqueue durable context through the reviewed native hook rather than a background process.

- [ ] **Step 4: Run exact-name trigger fixtures**

Start five read-only Codex fixtures that mention each skill by name and require the selected skill plus intended root. Expected: all five resolve once, no imported `.claude/skills` copy wins discovery, and no fixture writes.

- [ ] **Step 5: Review and commit in the Godot repository**

Stage only the five skills, test, and a redacted trigger report; obtain SoT+Mercury review; commit one reversible native-skill checkpoint.

### Task 3: Restore the KB current-session entry and local filesystem fallback

**Files:**
- Create: `D:\ShipOfTheseus\ShipOfTheseus-KB\03-AI-Context\Active-Context\current-session.md`
- Create: `D:\ShipOfTheseus\ShipOfTheseus-KB\scripts\verify-current-session.ps1`
- Modify only if its target is stale: the KB protocol index that links to `current-session.md`

**Interfaces:**
- Consumes: latest archived current-session file and the approved single-writer table.
- Produces: an active session entry with valid internal links and a read/write fallback independent of Obsidian MCP.

- [ ] **Step 1: Write a failing verifier**

The SoT lane writes the verifier in the KB repository. It requires the target file, UTF-8 without BOM, a session date, four canonical roots, owner/lane fields, and resolvable relative links. Its explicit mutating probe mode creates a temporary UUID file beside the session file, reads and hashes it, then removes it in `finally` and proves `git status` returned to baseline. Its default verification mode is strictly read-only.

- [ ] **Step 2: Run the verifier and reproduce the missing-entry failure**

Run: `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/verify-current-session.ps1`

Expected: FAIL because `current-session.md` is missing.

- [ ] **Step 3: Reconstruct the active entry from the latest archive**

Copy only still-valid context, update the date/roots/authority summary, link the Codex migration design and #571, and mark talent work frozen. Do not copy obsolete Claude-only runtime claims.

- [ ] **Step 4: Run the fallback verifier**

Expected: exit 0, temporary file removed, no unexpected Git diff, and exact path readable/writable by the current user.

- [ ] **Step 5: Review and commit in the KB repository**

Stage only the active entry, verifier, and an index change if required; obtain SoT review; commit one reversible KB checkpoint. The SoT lane returns commit/test/report hashes to Mercury; Mercury does not stage or write the KB.

### Task 4: Revalidate Godot MCP after cache cleanup

**Files:**
- Verify/modify only if needed: `C:\Users\392fy\.codex\config.toml`
- Verify: `D:\ShipOfTheseus\Ship_of_Theseus\addons\godot_mcp\plugin.cfg`
- Create: `D:\Mercury\Mercury\scripts\codex\mcp\verify-godot.ps1`
- Create: `D:\Mercury\Mercury\tests\codex\mcp\test_godot_config.py`

**Interfaces:**
- Consumes: Godot project, running add-on, Node >=20, and pinned package `@satelliteoflove/godot-mcp@2.15.0`.
- Produces: config-shape proof and an actual `project(action=get_info)` result.

- [ ] **Step 1: Write config tests**

Assert the only Godot MCP command is `npx`, args are exactly `-y @satelliteoflove/godot-mcp@2.15.0`, timeout is 60, environment contains only `GODOT_PROJECT_PATH`, and no active command references `godot_mcp_lazy_server.mjs` or version 2.16.0.

- [ ] **Step 2: Run config tests and version prerequisites**

Run:

```powershell
python -m unittest tests.codex.mcp.test_godot_config -v
node --version
codex mcp get godot
```

Expected: tests pass; Node is at least 20; CLI shows the exact package pin and 60-second timeout.

- [ ] **Step 3: Start the target Godot project with the add-on enabled**

Open `D:\ShipOfTheseus\Ship_of_Theseus\project.godot`. Confirm `addons/godot_mcp/plugin.cfg` is version 2.15.0 and the MCP add-on is active.

- [ ] **Step 4: Run the real tool probe**

The verifier runs a read-only Codex task and requires a completed `mcp_tool_call` for server `godot`, tool `project`, arguments `{action:get_info}`. Assert project name `ShipOfTheseus`, path `D:/ShipOfTheseus/Ship_of_Theseus/`, main scene `res://scenes/tactical/TacticalScene.tscn`, no error, and process exit 0.

- [ ] **Step 5: Review, commit, and push the Godot verifier**

The SoT lane owns any Godot-project change; the user owns user-level config. Stage only the verifier and test unless config actually required correction; obtain two reviews; commit `test(TASK-571): revalidate pinned Godot MCP`; push through `git-safe.ps1`. Return redacted tool-call evidence to Mercury.

### Task 5: Restore Obsidian MCP with environment-only authentication

**Files:**
- Modify: `C:\Users\392fy\.codex\config.toml`
- Create: `D:\Mercury\Mercury\scripts\codex\mcp\verify-obsidian.ps1`
- Create: `D:\Mercury\Mercury\tests\codex\mcp\test_obsidian_config.py`
- Create outside Git: redacted probe result under `D:\Codex-Migration-Backup\2026-08-15-mercury-sot`

**Interfaces:**
- Consumes: listening `http://127.0.0.1:27123/mcp/`, user environment variable `OBSIDIAN_API_KEY`, and KB root.
- Produces: initialized MCP session, tool list, read result, UUID write/read/hash/delete result, and unchanged KB baseline.

- [ ] **Step 1: Write a config test that cannot reveal the token**

Parse TOML and assert:

```python
server = config["mcp_servers"]["obsidian"]
assert server["url"] == "http://127.0.0.1:27123/mcp/"
assert server["bearer_token_env_var"] == "OBSIDIAN_API_KEY"
assert set(server) <= {"url", "bearer_token_env_var", "required", "enabled", "startup_timeout_sec", "tool_timeout_sec", "default_tools_approval_mode"}
assert server["default_tools_approval_mode"] == "writes"
assert "headers" not in server
assert "authorization" not in server
assert "bearer_token" not in server
```

Also assert the environment variable exists and has a nonzero length without printing it.

- [ ] **Step 2: Add the disabled-by-default server entry**

Add the URL and `bearer_token_env_var`; set `required = false`; keep mutating tools approval-gated. Do not place a static Authorization header in any file.

- [ ] **Step 3: Verify initialize and tools/list before writes**

Run `codex mcp list` and a read-only Codex task that lists Obsidian tools and reads a known KB note. Expected: initialize and tool listing complete with no authentication or transport error.

- [ ] **Step 4: Run the guarded write probe**

Only the SoT/user lane may run this mutation. Capture KB `git status` and file inventory. Generate a UUID path under a dedicated migration-probes folder; require nonexistence; create one note through MCP; read through MCP; compare SHA-256 with local filesystem bytes. Immediately before deletion, re-read the exact UUID path through MCP and local filesystem and require both hashes to equal the originally created content; on mismatch, do not delete and emit an alert. Otherwise delete through MCP in `finally`; assert the probe path is absent and Git/file inventory matches baseline. Persist only a redacted, signed/hash-bound result for Mercury to consume.

- [ ] **Step 5: Prove filesystem fallback still works with MCP disabled**

The SoT/user lane temporarily disables only the Obsidian server, runs the KB verifier's explicit probe mode from Task 3, and restores the entry. Expected: local read/write passes; therefore an Obsidian outage degrades metadata/sync behavior but does not block controlled KB file access.

- [ ] **Step 6: Review, commit, and push verifier code**

Stage only the verifier and config test; user-level TOML stays outside Git. Obtain two reviews; commit `test(TASK-571): verify Obsidian MCP and KB fallback`; push through `git-safe.ps1`.

### Task 6: Run the four-root linkage acceptance

**Files:**
- Create: `D:\Mercury\Mercury\scripts\codex\mcp\verify-sot-linkage.ps1`
- Create: `D:\Mercury\Mercury\.mercury\docs\migration\2026-08-15-sot-linkage-acceptance.md`

**Interfaces:**
- Consumes: canonical lane test, rule API/snapshot parity, KB fallback, Godot MCP, and Obsidian MCP evidence.
- Produces: one redacted linkage verdict and exact remaining risks without writing any SoT-owned root.

- [ ] **Step 1: Implement fail-fast composition**

Read and validate SoT-produced evidence for the design-library authority test, rule parity check, KB fallback/write probe, Godot verifier, and Obsidian verifier. Check owning agent/lane, commit or config hash, command, exit code, artifact hash, and timestamp. The Mercury composition script is read-only and must never invoke a KB/design/Godot write probe itself.

- [ ] **Step 2: Run acceptance with both MCP servers available**

Run: `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/codex/mcp/verify-sot-linkage.ps1 -EvidenceRoot "D:\Codex-Migration-Backup\2026-08-15-mercury-sot\sot-evidence" -ReadOnly`

Expected: all five sections pass; probe notes are absent; API and snapshot contain 23 identical rules; both actual MCP tool calls complete.

- [ ] **Step 3: Review, commit, and push the linkage record**

Stage only the composed verifier and acceptance report; obtain Mercury+SoT reviews; commit `test(TASK-571): accept SoT KB MCP linkage`; push through `git-safe.ps1`.
