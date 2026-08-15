# Mercury Legacy Workflow and GUI Retirement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove Codex-native replacements for every active Dynamic Workflow contract, then remove obsolete workflow/orchestrator/GUI/Claude control entry points while preserving auditable archives and rollback paths.

**Architecture:** A fixture matrix first evaluates native skill/subagent compositions against each legacy workflow's success and failure behavior. Retirement manifests freeze exact tracked paths and caller graphs; documentation and CI change before source deletion; each deletion is a separate reversible commit. Talent validation is frozen into an archive rather than reimplemented.

**Tech Stack:** Codex native skills/subagents/messages, Node fixture runners, Python/PowerShell verification, GitHub Actions YAML, Git-safe workflow.

**Spec:** `.mercury/docs/design/2026-08-14-codex-native-import-design.md`

## Global Constraints

- Seven workflow entries must end as `REPLACED-PASS` or `FROZEN-ARCHIVE-PASS` before their active files are removed.
- Every replacement declares concurrency at or below platform capacity, total and stage budgets, timeout, max rounds, cancellation cleanup, failure isolation, overflow reporting, and non-convergence residue.
- Talent optimization remains suspended. Preserve the unique validator, README, usage, dependencies, examples, and hashes in an inert archive.
- Generate deletion allowlists from exact tracked paths, freeze hashes, and abort if the current tree differs from the manifest.
- Update #571 and #427, callers, docs, gates, and CI before deleting GUI or orchestrator source.
- Keep `auto-verify.yml` and `upstream-drift.yml`; retarget `skill-drift.yml`; retire/redesign `external-intel.yml` only with a tested replacement.
- Do not remove Claude source/history until official Import reconciliation and local rollback rehearsal pass.
- Do not remove `opencode-ai`, Antigravity, Copilot, Gemini, QQ input data, or unrelated VS Code data without a separate user decision.
- Before every repository mutation or stage, run `preflight_snapshot.py guard` with the exact planned paths; compare branch, HEAD ancestry, status, dirty bytes/hashes, and target overlap. Stop on drift.
- Every checkpoint uses the shared mandatory transaction: `begin-approved` → `guard` → edit/test/two reviews/explicit commit → `seal-approved` → chain verification → push. Deletion and tag-helper checkpoints are separate rows; an interrupted row must be byte-identically aborted or it blocks all retirement work.

---

### Task 1: Build the workflow parity fixture matrix

**Files:**
- Create: `.agents/skills/mercury-codebase-audit/SKILL.md`
- Create: `.agents/skills/mercury-multi-source-research/SKILL.md`
- Create: `.agents/skills/mercury-adversarial-plan-review/SKILL.md`
- Create: `.agents/skills/mercury-large-migration/SKILL.md`
- Create: `.agents/skills/mercury-staleness-audit/SKILL.md`
- Create: `scripts/codex/workflows/fixtures/codebase-audit.json`
- Create: `scripts/codex/workflows/fixtures/multi-source-research.json`
- Create: `scripts/codex/workflows/fixtures/adversarial-plan-review.json`
- Create: `scripts/codex/workflows/fixtures/large-migration.json`
- Create: `scripts/codex/workflows/fixtures/staleness-audit.json`
- Create: `scripts/codex/workflows/fixtures/ecc-practice-scan.json`
- Create: `scripts/codex/workflows/fixtures/talent-validate.json`
- Create: `scripts/codex/workflows/verify-parity.py`
- Create: `tests/codex/workflows/test_parity_contract.py`
- Create: `.mercury/docs/migration/2026-08-15-workflow-replacement-precheck.json`

**Interfaces:**
- Consumes: seven `.claude/workflows` implementations and native harness commands.
- Produces: one row per workflow with success, over-limit, budget-exhausted, timeout, cancellation, worker-failure, convergence, and residue results.

- [ ] **Step 1: Write failing global-invariant tests**

```python
REQUIRED = {
    "max_concurrency", "total_budget", "stage_budget", "timeout_seconds",
    "max_rounds", "cancel_cleanup", "failure_isolation", "overflow_report",
    "non_convergence_result",
}

def test_every_replacement_declares_resource_invariants():
    for fixture in load_fixtures():
        assert REQUIRED <= fixture["native_contract"].keys()
```

Also require a fixed input and expected output/error category for every failure mode.

- [ ] **Step 2: Run tests and observe missing fixture failures**

Run: `python -m unittest tests.codex.workflows.test_parity_contract -v`

Expected: FAIL because the fixture matrix is absent.

- [ ] **Step 3: Encode each legacy contract without executing talent work**

Implement the five named `.agents/skills` entries as the actual user-discoverable native replacements. Map codebase audit, multi-source research, adversarial plan review, and large migration to native multi-agent compositions. Map staleness audit to on-demand native composition. Map ECC scan to an inert archive/on-demand evidence reader. Mark talent validator as freeze/archive only; its fixture validates bytes/hash/help behavior without optimizing or changing talents. Each fixture binds the exact entry path, SHA-256, installed discovery name, and invocation contract.

- [ ] **Step 4: Implement the parity runner without activating frozen workflows**

For the five replacement fixtures, invoke the exact installed native skill entry with explicit caps and parse structured results. ECC and talent have no installed skill and must remain undiscoverable/unexecutable; before Task 2 creates their archives, report `ARCHIVE-PENDING` rather than fabricating a pass. The parity runner may supply fixtures and validate results but must not become a new outer orchestrator. Fail if a replacement's discovered path/hash differs, a frozen entry becomes discoverable, workers exceed concurrency, a timed-out worker remains live, truncated work is unreported, one worker failure aborts unrelated results, or max rounds return success without residue.

- [ ] **Step 5: Run the complete matrix**

Run: `python scripts/codex/workflows/verify-parity.py --fixtures scripts/codex/workflows/fixtures --allow-archive-pending --output .mercury/docs/migration/2026-08-15-workflow-replacement-precheck.json`

Expected: five replacement workflows report `REPLACED-PASS`; ECC and talent report exactly `ARCHIVE-PENDING` and remain undiscoverable/unexecutable; every applicable failure-path column passes. This is not deletion authority.

- [ ] **Step 6: Review, commit, and push the parity checkpoint**

Stage the five exact native workflow `SKILL.md` entries, fixtures, runner, tests, and replacement precheck; obtain two reviews; commit `test(TASK-571): prove native workflow replacements`; push through `git-safe.ps1`.

### Task 2: Freeze talent and generate immutable deletion manifests

**Files:**
- Create: `.mercury/archive/ecc-practice-scan/README.md`
- Create: `.mercury/archive/ecc-practice-scan/SHA256SUMS`
- Create: `.mercury/archive/ecc-practice-scan/mercury-ecc-practice-scan.js`
- Create: `.mercury/archive/talent-validator/README.md`
- Create: `.mercury/archive/talent-validator/SHA256SUMS`
- Create: `.mercury/archive/talent-validator/talent-validate.js`
- Create: `.mercury/docs/migration/2026-08-15-retirement-manifest.json`
- Create: `.mercury/docs/migration/2026-08-15-workflow-parity.json`
- Create outside Git: `D:\Codex-Migration-Backup\2026-08-15-mercury-sot\untracked-retirement-manifest.json`
- Create: `scripts/codex/retirement/build-manifest.py`
- Create: `tests/codex/retirement/test_manifest.py`

**Interfaces:**
- Consumes: current tracked workflow, orchestrator, GUI, caller, and CI trees.
- Produces: content-addressed archive and exact deletion groups whose live hashes must match at deletion time.

- [ ] **Step 1: Write manifest safety tests**

For the tracked manifest, require repo-relative regular files only; reject directories, globs, pathspec magic, drive roots, untracked paths, dirty overlaps, and a changed content hash. Require groups `dynamic-workflows`, `codex-orchestrator`, `mercury-gui-active`, and `mercury-gui-archive`. Separately require an untracked/quarantine manifest with resolved absolute path, entry type, byte size, SHA-256, destination, and recovery disposition; never mix its entries into Git deletion.

- [ ] **Step 2: Run tests and observe the missing builder failure**

Run: `python -m unittest tests.codex.retirement.test_manifest -v`

Expected: FAIL because the manifest builder does not exist.

- [ ] **Step 3: Create the inert ECC and talent archives**

Copy the exact tracked ECC scanner and talent validator bytes to their respective archives; record source path, usage text, dependency references, related examples, and SHA-256. Each archive README states that no skill, hook, workflow, package script, or CI job may execute it.

- [ ] **Step 4: Generate exact deletion groups**

Build groups from:

```text
.claude/workflows/README.md
.claude/workflows/mercury-adversarial-plan-review.js
.claude/workflows/mercury-codebase-audit.js
.claude/workflows/mercury-ecc-practice-scan.js
.claude/workflows/mercury-large-migration.js
.claude/workflows/mercury-multi-source-research.js
.claude/workflows/mercury-staleness-audit.js
.claude/workflows/talent-validate.js
packages/codex-orchestrator/** (tracked files only)
mercury-gui/** (tracked files only)
archive/packages/gui/** (tracked files only)
```

At planning time the two GUI roots contain 146 tracked files. Record the actual count and hashes; abort if count or hashes change before deletion.

Create a separate exact untracked manifest for generated/runtime residue. Include the resolved `mercury-gui\src-tauri\target` path if present, reject reparse points or paths outside the Mercury repository, record every contained file and hash, and choose a dated D-drive quarantine destination. Do not delete it in this task.

- [ ] **Step 5: Run dirty-overlap and hash verification**

Run:

```powershell
python scripts/codex/retirement/build-manifest.py verify --manifest .mercury/docs/migration/2026-08-15-retirement-manifest.json
python scripts/codex/workflows/verify-parity.py --fixtures scripts/codex/workflows/fixtures --require-archives --output .mercury/docs/migration/2026-08-15-workflow-parity.json
$env:MERCURY_SKILL_PHASE='final'; python -m unittest tests.codex.harness.test_skill_inventory -v; Remove-Item Env:MERCURY_SKILL_PHASE
```

Expected: exit 0; no deletion target intersects preflight dirty paths; five native entries are `REPLACED-PASS`; ECC and talent are `FROZEN-ARCHIVE-PASS` based only on exact archive file sets/SHA256SUMS, recovery instructions, and proof they are undiscoverable/unexecutable.

- [ ] **Step 6: Review, commit, and push archive/manifest**

Stage both archive trees, final parity output, manifest, builder, and tests; obtain two reviews; commit `chore(TASK-571): freeze legacy retirement allowlists`; push through `git-safe.ps1`.

### Task 3: Update active callers, documentation, issues, and CI first

**Files:**
- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `.mercury/docs/DIRECTION.md`
- Modify: `.mercury/docs/guides/fanout-and-skill-migration-guardrails.md`
- Modify: `.mercury/docs/guides/talent-validate-usage.md`
- Modify: `.mercury/docs/guides/upstream-drift-routine.md`
- Modify: `.mercury/docs/guides/workflow-migration-map.md`
- Modify: `.gitignore`
- Modify: `.gitattributes`
- Modify: `pnpm-lock.yaml`
- Modify: `.github/workflows/skill-drift.yml`
- Modify or remove after replacement proof: `.github/workflows/external-intel.yml`
- Preserve: `.github/workflows/auto-verify.yml`
- Preserve: `.github/workflows/upstream-drift.yml`
- Create: `scripts/codex/retirement/caller-scan.py`
- Create: `tests/codex/retirement/test_caller_scan.py`

**Interfaces:**
- Consumes: retirement manifest and native replacement names.
- Produces: an active tree with no caller pointing at a soon-to-be-deleted path.

- [ ] **Step 1: Write a failing semantic caller scan**

Scan tracked text while excluding the immutable archive, migration evidence, and Git history. Treat executable imports, package scripts, hook commands, CI commands, docs labeled active, and install instructions as active callers. Permit historical references only when the containing document is explicitly marked archive/migration evidence.

- [ ] **Step 2: Run the scan and record every active caller**

Run: `python scripts/codex/retirement/caller-scan.py --manifest .mercury/docs/migration/2026-08-15-retirement-manifest.json`

Expected: FAIL with a finite list of callers that require migration.

- [ ] **Step 3: Redirect active documentation and commands**

Point workflow usage to native skills/subagents and `run-acceptance.ps1`; remove claims that Mercury GUI or external orchestrator is required; keep rollback links to the archive/tag. Do not rewrite historical acceptance records as if the old system never existed.

- [ ] **Step 4: Retarget CI**

Make `skill-drift.yml` compare `.agents/skills` and active Codex plugin/skill manifests. Keep auto-verify/upstream-drift unchanged except for obsolete caller removal. Either replace `external-intel.yml` with a deterministic current-Codex evidence job and test it, or remove the workflow and document why no equivalent CI judgment is claimed.

- [ ] **Step 5: Record issue decisions**

Comment on #571 with native acceptance and rollback paths. Close or redirect #427 according to the approved GUI retirement. Align #478 and #496 with the final architecture; do not close an issue whose acceptance evidence is incomplete.

- [ ] **Step 6: Re-run caller scan and CI syntax tests**

Expected: zero active callers to deletion targets; action YAML parses; local auto-verify and skill-drift tests pass.

- [ ] **Step 7: Review, commit, and push caller/CI migration**

Stage only active docs, caller scanner/tests, and intended workflow changes; obtain two reviews; commit `refactor(TASK-571): redirect callers to native Codex harness`; push through `git-safe.ps1`.

### Task 4: Remove Dynamic Workflow and external orchestrator source

**Files:**
- Delete from manifest group: `dynamic-workflows`
- Delete from manifest group: `codex-orchestrator`
- Create: `.mercury/docs/migration/2026-08-15-workflow-retirement.md`

**Interfaces:**
- Consumes: `REPLACED-PASS`/`FROZEN-ARCHIVE-PASS`, zero-caller scan, and exact hashes.
- Produces: no active `.claude/workflows` or `packages/codex-orchestrator`, with history/archive recovery intact.

- [ ] **Step 1: Verify the deletion manifest immediately before mutation**

Run the manifest verifier, dirty-overlap check, native harness acceptance, and caller scan. Expected: all exit 0.

- [ ] **Step 2: Delete only explicit manifest paths**

Feed each exact file path individually to the repository-approved removal flow. Do not use a recursive directory command, wildcard, `git clean`, broad staging, or an unresolved variable.

- [ ] **Step 3: Run negative scans and native acceptance**

Expected: no active caller or tracked file under either group; ECC and talent archive file sets/SHA256SUMS match and both remain undiscoverable/unexecutable; native harness acceptance remains exit 0.

- [ ] **Step 4: Review, commit, and push workflow retirement**

Stage explicit deleted paths plus the retirement report; obtain two reviews; commit `refactor(TASK-571): retire external workflow orchestrator`; push through `git-safe.ps1`.

### Task 5: Remove Mercury GUI source after recovery tag and issue gate

**Files:**
- Delete from manifest group: `mercury-gui-active`
- Delete from manifest group: `mercury-gui-archive`
- Modify: caller/gate/docs files identified in Task 3
- Create: `.mercury/docs/migration/2026-08-15-gui-retirement.md`
- Create: `scripts/codex/tag-safe.ps1`
- Create: `tests/codex/tag-safe.Tests.ps1`

**Interfaces:**
- Consumes: #571 decision, #427 disposition, clean caller graph, exported `.vsconfig`, and frozen Git recovery reference.
- Produces: no active or duplicated GUI source while preserving Git history/tag and toolchain reinstall instructions.

- [ ] **Step 1: Implement and test guarded tag creation/push**

Write Pester cases that reject the wrong branch, non-ancestor or unexpected HEAD, existing mismatched tag, lightweight tag, non-`recovery/TASK-571-` name, and remote mismatch. `tag-safe.ps1` must create an annotated tag only at the exact approved commit, push that one explicit ref, fetch/`ls-remote` it, and verify local/remote peeled commits equal the approved commit.

Run: `$result = Invoke-Pester -Script tests/codex/tag-safe.Tests.ps1 -PassThru -Show All; if ($result.FailedCount -ne 0) { throw "tag-safe tests failed" }`

Expected: Pester 3.4.0-compatible execution with `FailedCount = 0`; no real tag or remote ref is created by fixtures.

- [ ] **Step 2: Review, commit, and freeze the tag helper before remote mutation**

Stage only `scripts/codex/tag-safe.ps1` and `tests/codex/tag-safe.Tests.ps1`; obtain two reviews; commit `feat(TASK-571): add guarded recovery tag flow`; push through `git-safe.ps1`. Record the helper commit and SHA-256; any byte change invalidates approval.

- [ ] **Step 3: Create and verify the recovery reference**

Run the tested helper to create an annotated `recovery/TASK-571-<date>-pre-gui-retirement` tag at the exact pre-deletion commit and push only that ref. Record tag object ID, peeled commit, remote proof, and command in the retirement report. Verify `vs-buildtools-2022.vsconfig`, Rust inventory, and their SHA256SUMS under `D:\Codex-Cleanup-Quarantine\2026-08-15-toolchain-removal`.

- [ ] **Step 4: Verify the 146-file allowlist and no dirty overlap**

Re-run manifest verification. If the count or any hash differs, regenerate and re-review the manifest instead of deleting.

- [ ] **Step 5: Delete only explicit tracked GUI files and quarantine generated residue**

Remove each tracked-manifest file individually. In a separate operation, re-resolve and verify every untracked-manifest source, reject drift/reparse/out-of-root targets, and move `src-tauri/target` to its exact dated D-drive quarantine. Verify the destination per-file manifest and source absence; do not stage it as a Git deletion.

- [ ] **Step 6: Run negative caller scan and full CI**

Expected: no active GUI path, package script, docs instruction, gate, or CI reference; active Mercury and SoT tests pass without Rust or Visual Studio installed.

- [ ] **Step 7: Review, commit, and push GUI retirement**

Stage explicit deletions, intended caller edits, and report; obtain two reviews; commit `refactor(TASK-571): retire Mercury GUI`; push through `git-safe.ps1`.

### Task 6: Retire Claude/OMC runtime only after Import rollback acceptance

**Files:**
- System packages: Claude binary/share and `oh-my-claude-sisyphus`
- Preserve in D backup: Claude Import source snapshot and hashes
- Create: `.mercury/docs/migration/2026-08-15-claude-runtime-retirement.md`

**Interfaces:**
- Consumes: Import reconciliation exit 0, native harness exit 0, state restore rehearsal, and source backup verification.
- Produces: active harness with no `claude.exe`, OMC, Anthropic key, Claude HUD/daemon/team, or Claude-hosted Codex wrapper dependency.

- [ ] **Step 1: Freeze a final read-only Claude source snapshot**

Copy the required Import source to the D migration backup, apply current-user-only ACL, encrypt assets whose secret decision requires encryption, and verify hashes/counts against `assets.jsonl`.

- [ ] **Step 2: Prove no active process/config/caller uses the runtime**

Scan processes, services, scheduled tasks, PATH, npm globals, hooks, package scripts, and active docs. Expected: no required caller; any historical reference is archive-only.

- [ ] **Step 3: Uninstall exact packages and move residual caches recoverably**

Use each product's registered uninstaller or package-manager uninstall. Move remaining Claude/OMC caches to a dated D quarantine with manifest instead of recursively deleting unknown AppData.

- [ ] **Step 4: Re-run Codex and four-root acceptance**

Expected: Codex runtime, native harness, Godot MCP, Obsidian MCP, KB fallback, rule parity, and four-root Git diff check all pass with Claude/OMC absent.

- [ ] **Step 5: Review, commit, and push runtime-retirement evidence**

Stage only the evidence report; obtain two reviews; commit `chore(TASK-571): retire Claude runtime dependencies`; push through `git-safe.ps1`.

### Task 7: Final negative dependency and rollback rehearsal

**Files:**
- Create: `scripts/codex/retirement/final-negative-scan.py`
- Create: `tests/codex/retirement/test_final_negative_scan.py`
- Create: `.mercury/docs/migration/2026-08-15-final-acceptance.md`

**Interfaces:**
- Consumes: all prior evidence and backups.
- Produces: final architecture verdict and tested local rollback instructions.

- [ ] **Step 1: Write and run negative-dependency tests**

Scan both tracked content and untracked filesystem entry points. Fail on active references to `.claude/workflows`, `packages/codex-orchestrator`, `mercury-gui`, `claude.exe`, OMC, tmux agent teams, Anthropic credentials, static Obsidian authorization headers, or executable residue omitted from the tracked/untracked retirement manifests. Allow only explicit migration/archive reports.

- [ ] **Step 2: Rehearse file/config rollback in an isolated copy**

Restore one repo/config checkpoint and one quarantined file into empty temporary targets, verify hashes, then discard the temporary targets. Do not overwrite live paths.

- [ ] **Step 3: Rehearse Codex-state rollback in an isolated copy**

Open every backed-up SQLite database, run integrity checks, load session/import indexes, and prove referenced attachments/memories exist. Do not replace live Codex state during the rehearsal.

- [ ] **Step 4: Record cloud rollback truthfully**

List imported cloud objects that the UI can delete and those without a demonstrated delete path. Never label local SQLite restoration as cloud rollback.

- [ ] **Step 5: Run final full acceptance**

Run native harness, SoT linkage, Import reconciliation, workflow negative scan, CI, XML lint, four-root dirty comparison, and C free-space check. Expected: all executable gates exit 0; C free is at least 20 GiB and target remains at least 22 GiB.

- [ ] **Step 6: Review, commit, and push final evidence**

Stage the scanner, test, and final acceptance report; obtain two reviews; commit `test(TASK-571): verify pure Codex Mercury SoT environment`; push through `git-safe.ps1`.
