# Mercury + SoT Import Preflight and Ledger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a secret-safe, reversible inventory of every Mercury + SoT import asset, perform the desktop-only Import, and reconcile every selected item into a machine-readable ledger.

**Architecture:** Small Python modules inventory assets, scan without emitting secret material, snapshot dirty trees and Codex state, and reconcile before/after observations. The only non-scriptable step is the official Desktop Settings > Import flow, which is surrounded by explicit user gates and immutable local evidence.

**Tech Stack:** Python 3 stdlib (`argparse`, `hashlib`, `json`, `pathlib`, `sqlite3`), PowerShell 7, Codex Desktop Import, Git.

**Spec:** `.mercury/docs/design/2026-08-14-codex-native-import-design.md`

## Global Constraints

- Output root is exactly `D:\Codex-Migration-Backup\2026-08-15-mercury-sot`; abort instead of overwriting an existing non-empty root.
- Before its first file is written, the output root must have inheritance disabled, ACL entries only for the current user SID and `SYSTEM`, and EFS encryption enabled and mechanically verified. If EFS is unavailable, stop without copying sensitive data.
- The committed SoT canonical-authority test and commit ID are hard prerequisites for Task 2.
- Inventory all 68 recent Claude chats, 378 memory documents, 3 auxiliary memory assets, all settings/hooks/commands/skills/agents/workflows, attachments, backups, and current dirty files.
- A manifest stores asset IDs, paths, sizes, content hashes, classifications, and secret finding categories, but never secret values or secret-derived digests.
- Imported history containing an exposed credential is permitted only after the credential is invalidated and the old credential's authenticated request returns 401/403.
- Cursor standard paths are temporary Import staging only; the recoverable source remains `D:\Codex-Cleanup-Quarantine\2026-08-15-cursor`.
- Desktop automatic updates remain off. The official flow leaves existing Codex setup unchanged, so post-import reconciliation is mandatory.
- Before every repository mutation or stage, run `preflight_snapshot.py guard` with the exact planned paths; compare branch, HEAD ancestry, status, dirty bytes/hashes, and target overlap.
- For the initial secure-root/inventory/scanner/snapshot-tool commits before `guard` exists, run and preserve the equivalent explicit branch/HEAD/merge-base/status/dirty-byte-hash/target-overlap checks; after Task 4 commits, manual substitution is forbidden.
- Every approved repository mutation uses a two-phase hash-chained ledger: `begin-approved` runs before mutation and records repository, owning lane/agent, issue, reviews, pre-HEAD, exact planned paths and before hashes; `seal-approved` runs immediately afterward and adds post-HEAD/hashes, commit, and timestamp. Incomplete, reordered, post-hoc, or unrecorded rows in protected `approved-mutations.json` fail final comparison.
- Accordingly, every later “review, commit, and push” checkpoint means: `begin-approved` → `guard` → edit/test/two reviews/explicit `git-safe` commit → `seal-approved` → chain verification → push. `abort-approved` is valid only if it proves byte-for-byte restoration to the begin state. The bootstrap tool commits made before this API exists use the time-stamped equivalent evidence and are imported as sealed bootstrap rows immediately after Task 4 freezes the tool.

---

### Task 1: Create and verify the protected migration root

**Files:**
- Create: `scripts/codex/import/secure_backup_root.ps1`
- Create: `tests/codex/import/secure-backup-root.Tests.ps1`
- Create outside Git: `D:\Codex-Migration-Backup\2026-08-15-mercury-sot`

**Interfaces:**
- Consumes: an absent or empty exact D-drive destination and the current Windows user SID.
- Produces: an NTFS/EFS directory whose only access principals are the current user and `SYSTEM`.

- [ ] **Step 1: Write failing security fixtures**

Use a temporary NTFS directory. Assert the script rejects a reparse point, nonempty root, inherited `Authenticated Users`/`BUILTIN\Users`, failed EFS enablement, and a path outside `D:\Codex-Migration-Backup`. Assert a successful root reports exact owner SID, DACL, encrypted attribute, and zero files.

- [ ] **Step 2: Run the fixtures and observe the missing-script failure**

Run: `Invoke-Pester -Script tests/codex/import/secure-backup-root.Tests.ps1 -PassThru -Show All`

Expected: FAIL because `secure_backup_root.ps1` is absent.

- [ ] **Step 3: Implement create-deny-verify semantics**

Create the exact directory, disable inheritance, remove inherited principals, grant full control only to the current user SID and `S-1-5-18` (`SYSTEM`), set the owner to the current user, enable EFS with `cipher /E`, create one random probe, verify its encrypted attribute, then remove the probe. Abort and leave the root empty on any failed assertion.

- [ ] **Step 4: Review, commit, and freeze the root-creation tool**

Stage only the script and security fixtures; obtain two reviews; commit `feat(TASK-571): protect migration backup root`; push through `git-safe.ps1`.

- [ ] **Step 5: Create the real root before any inventory or backup output**

Run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/codex/import/secure_backup_root.ps1 -Path "D:\Codex-Migration-Backup\2026-08-15-mercury-sot"
```

Expected: root is empty, encrypted, not a reparse point, and its access check lists only current-user SID and `SYSTEM`. The script also exposes `verify-tree`, which recursively checks every later file/directory for EFS encryption, owner, and effective DACL; run it after each copy/move and immediately before Import.

### Task 2: Implement deterministic asset inventory

**Files:**
- Create: `scripts/codex/import/model.py`
- Create: `scripts/codex/import/inventory.py`
- Create: `tests/codex/import/test_inventory.py`

**Interfaces:**
- Consumes: absolute source roots, a frozen time window, controlled domain decisions, and the immutable inventory-contract SHA-256.
- Produces: sorted JSON Lines; every nonempty approved `.claude/skills/<name>` junction target member is a canonical real-`.agents` `AssetRecord`, while the alias itself remains a separate frozen relation and is not duplicated as an Import asset.

- [ ] **Step 1: Write failing normalization and classification tests**

Create fixtures that cover a Claude JSONL chat, Markdown memory, `.pyc` auxiliary file, skill, agent, command, hook, backup, attachment, dirty tracked file, and untracked file. Assert POSIX-normalized paths, stable SHA-256, and exactly one of `import`, `exclude-secret`, or `exclude-domain`.

```python
def test_inventory_is_stable_and_classified(tmp_path):
    chat = tmp_path / "projects" / "mercury" / "session.jsonl"
    chat.parent.mkdir(parents=True)
    chat.write_text('{"cwd":"D:/Mercury/Mercury","message":"SoT"}\n', encoding="utf-8")
    records = inventory_paths([chat], cutoff=None)
    assert records[0].kind == "chat"
    assert records[0].domain == "mercury-sot"
    assert records[0].asset_id == inventory_paths([chat], cutoff=None)[0].asset_id
```

- [ ] **Step 2: Run the test and observe the missing-module failure**

Run: `python -m unittest tests.codex.import.test_inventory -v`

Expected: FAIL because `scripts.codex.import.inventory` does not exist.

- [ ] **Step 3: Implement explicit source adapters**

Implement `inventory_paths()`, `inventory_claude_chats()`, `inventory_memories()`, and `inventory_repo_dirty()`. Sort records by canonical namespace/key order; compute stable single-handle hashes; read only the first user message for domain classification. Permit only a direct exact-case same-name Windows junction at `.claude/skills/<name>` whose target is the same user's canonical non-reparse `.agents/skills/<name>`. Never enumerate through the alias. Freeze the link lstat identity/raw target/tag, canonical target identity, and target-member descriptors/content hashes. Empty targets remain visible relations with zero members; nonempty target members are first-class secret-first `AssetRecord`s sourced from the real `.agents` path. All other reparse points fail closed, and contract/summary live rechecks reject link, target, membership, identity, or byte drift.

For every non-secret target member, emit one `AssetRecord` with `domain_reason=already-native-alias/no-import` and relation evidence; known secret containers retain provisional `exclude-secret`. Task 2 records bytes and provenance but does not implement an Import isolation policy or receipt protocol.

- [ ] **Step 4: Prove deterministic fixture output**

Run:

```powershell
python -m unittest tests.codex.import.test_inventory -v
```

Expected: deterministic inventory and Windows-only temp-junction inventory tests pass without reading target bytes through the alias.

- [ ] **Step 5: Review, commit, and push the inventory module**

Stage exactly `model.py`, `inventory.py`, and `test_inventory.py`; obtain two reviews; mark review; commit `feat(TASK-571): inventory Mercury SoT import assets`; push `feature/TASK-571` through `git-safe.ps1`.

- [ ] **Step 6: Generate the real inventory with the frozen tool**

Run:

```powershell
python scripts/codex/import/inventory.py freeze-contract --contract "D:\Codex-Migration-Backup\2026-08-15-mercury-sot\inventory-contract.json" --output "D:\Codex-Migration-Backup\2026-08-15-mercury-sot\assets.jsonl" --metadata "D:\Codex-Migration-Backup\2026-08-15-mercury-sot\assets.metadata.json" --domain-decisions "D:\Codex-Migration-Backup\2026-08-15-mercury-sot\domain-decisions.json" --claude-home "C:\Users\392fy\.claude" --mercury-root "D:\Mercury\Mercury" --godot-root "D:\ShipOfTheseus\Ship_of_Theseus" --design-root "D:\ShipOfTheseus\SoT-fyc-space" --kb-root "D:\ShipOfTheseus\ShipOfTheseus-KB"
python scripts/codex/import/inventory.py collect --contract "D:\Codex-Migration-Backup\2026-08-15-mercury-sot\inventory-contract.json" --contract-sha256 $contractSha --output "D:\Codex-Migration-Backup\2026-08-15-mercury-sot\assets.jsonl"
python scripts/codex/import/inventory.py summarize --contract "D:\Codex-Migration-Backup\2026-08-15-mercury-sot\inventory-contract.json" --contract-sha256 $contractSha --input "D:\Codex-Migration-Backup\2026-08-15-mercury-sot\assets.jsonl"
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/codex/import/secure_backup_root.ps1 -VerifyTree "D:\Codex-Migration-Backup\2026-08-15-mercury-sot"
```

Expected: 68 chats, 378 Markdown memory documents, 3 auxiliary memory assets, and no asset without a disposition. Bind the committed tool ID/hash to the manifest.

### Task 3: Implement value-free credential scanning

**Files:**
- Create: `scripts/codex/import/secret_scan.py`
- Create: `tests/codex/import/test_secret_scan.py`

**Interfaces:**
- Consumes: `assets.jsonl` and file bytes.
- Produces: `SecretFinding(asset_id, category, location, decision)`; no matched text and no hash of matched text.

- [ ] **Step 1: Write tests that fail if secret material leaks**

```python
def test_finding_never_serializes_secret(tmp_path):
    secret = "example_token_value_1234567890"
    finding = scan_bytes("asset-1", f"OBSIDIAN_API_KEY={secret}".encode())
    encoded = json.dumps([asdict(item) for item in finding])
    assert secret not in encoded
    assert "OBSIDIAN_API_KEY" in encoded
```

Also test static bearer headers, common private-key headers, provider token prefixes, `.credentials.json`, environment assignments, and a benign long identifier that must not become a HIGH finding.

- [ ] **Step 2: Run the test and observe the missing scanner failure**

Run: `python -m unittest tests.codex.import.test_secret_scan -v`

Expected: FAIL because the scanner does not exist.

- [ ] **Step 3: Implement streaming scanning and redacted reports**

Read large JSONL files line-by-line; report only asset ID, detector category, byte/line location, severity, and required action. Reject any output object containing a captured value field. Support scanning Claude, quarantined Cursor, repo assets, final EFS-protected backups, attachments, and dirty-file backups. Bind findings to the scanned asset SHA-256 so any later byte change invalidates the decision.

- [ ] **Step 4: Run detector tests without making final credential decisions**

Run:

```powershell
python -m unittest tests.codex.import.test_secret_scan -v
```

Expected: tests pass. The authoritative scan waits until Task 4 has frozen the final backup bytes.

- [ ] **Step 5: Review, commit, and push the scanner**

Stage only the scanner and its tests; obtain two reviews; commit `feat(TASK-571): gate imports on redacted secret scan`; push through `git-safe.ps1`.

### Task 4: Implement offline dirty-tree and complete Codex-state snapshots

**Files:**
- Create: `scripts/codex/import/preflight_snapshot.py`
- Create: `scripts/codex/import/codex_state_backup.py`
- Create: `tests/codex/import/test_preflight_snapshot.py`
- Create: `tests/codex/import/test_codex_state_backup.py`

**Interfaces:**
- Consumes: four explicit roots, `C:\Users\392fy\.codex`, and an empty output root.
- Produces: `dirty-tree.json`, copied dirty files, SQLite backups created with `sqlite3.Connection.backup`, stable non-database copies, protected Claude/Cursor source copies, one unified immutable backup manifest, full reference verification, and a restore-rehearsal report.

- [ ] **Step 1: Write failing dirty-tree and WAL-consistency tests**

Create a temporary Git repo with one modified and one untracked file. Create a WAL-mode SQLite fixture with an uncheckpointed row. Assert the snapshot captures both files and the SQLite backup reads the row without copying live WAL/SHM blindly. Add guard cases for changed dirty bytes, branch drift, non-ancestor HEAD, new concurrent dirty paths, concurrent creation/modification of a planned target, and planned-target overlap. Add ledger cases that reject absent/duplicate/non-independent review evidence at seal time. Add a fake running Codex process fixture and require state backup to refuse while it is active.

- [ ] **Step 2: Run both tests and observe missing implementations**

Run: `python -m unittest tests.codex.import.test_preflight_snapshot tests.codex.import.test_codex_state_backup -v`

Expected: FAIL because both modules are absent.

- [ ] **Step 3: Implement exact-root snapshots and restore rehearsal**

Hard-code no roots in library code; require four repeated `--repo` arguments. Refuse `/`, a drive root, a reparse point, or an unprotected destination. Copy dirty file bytes and metadata; implement `guard` to re-read branch, HEAD, status, dirty bytes/hashes, and planned targets before every mutation. Implement two-phase `begin-approved`/`seal-approved` plus `final-compare` over append-only hash-chained `approved-mutations.json`; tests reject missing paths, unreviewed commits, chain breaks, unsealed rows, post-hoc timestamps, or deltas outside approved rows. Import the already time-stamped bootstrap canonical before/after evidence into its first sealed rows once the tool is frozen. Require Codex Desktop/CLI/app-server and Claude processes to be absent. Use SQLite backup for each `.sqlite`; copy sessions/rollouts/attachments/memories/vendor-import metadata and indexes while closed; copy every selected Claude source asset and all three Cursor quarantine trees into the protected EFS root; hash every file after copy. Produce one unified manifest listing every Codex-state, dirty-tree, Claude-source, and Cursor-source copy with source path/hash, copy path/hash, asset IDs, and owning tool commit/hash. Verification opens every database read-only, runs `PRAGMA integrity_check`, loads indexes, and proves every referenced local file exists in the isolated backup.

- [ ] **Step 4: Review, commit, and freeze snapshot tooling before real evidence**

Stage only the two modules and two test files; obtain two reviews; commit `feat(TASK-571): snapshot migration state safely`; push through `git-safe.ps1`. Record the commit ID and both script SHA-256 values in the protected ledger. Any later tool-byte change invalidates and requires regeneration of every snapshot/rehearsal below.

- [ ] **Step 5: Capture dirty trees, then close Codex for the full state snapshot**

Run:

```powershell
python scripts/codex/import/preflight_snapshot.py capture --repo "D:\Mercury\Mercury" --repo "D:\ShipOfTheseus\Ship_of_Theseus" --repo "D:\ShipOfTheseus\SoT-fyc-space" --repo "D:\ShipOfTheseus\ShipOfTheseus-KB" --output "D:\Codex-Migration-Backup\2026-08-15-mercury-sot\preflight"
```

Expected: all four roots recorded; the Mercury HEAD includes `9bcc036`; no existing dirty path is omitted. Then exit Codex completely and run from a separate local PowerShell window:

```powershell
python D:\Mercury\Mercury\scripts\codex\import\codex_state_backup.py backup --source "C:\Users\392fy\.codex" --output "D:\Codex-Migration-Backup\2026-08-15-mercury-sot\codex-state"
python D:\Mercury\Mercury\scripts\codex\import\preflight_snapshot.py copy-import-sources --assets "D:\Codex-Migration-Backup\2026-08-15-mercury-sot\assets.jsonl" --cursor-quarantine "D:\Codex-Cleanup-Quarantine\2026-08-15-cursor" --output "D:\Codex-Migration-Backup\2026-08-15-mercury-sot\import-sources"
python D:\Mercury\Mercury\scripts\codex\import\preflight_snapshot.py merge-manifests --codex-state "D:\Codex-Migration-Backup\2026-08-15-mercury-sot\codex-state\backup-manifest.json" --preflight "D:\Codex-Migration-Backup\2026-08-15-mercury-sot\preflight\manifest.json" --import-sources "D:\Codex-Migration-Backup\2026-08-15-mercury-sot\import-sources\manifest.json" --output "D:\Codex-Migration-Backup\2026-08-15-mercury-sot\backup-manifest.json"
powershell -NoProfile -ExecutionPolicy Bypass -File D:\Mercury\Mercury\scripts\codex\import\secure_backup_root.ps1 -VerifyTree "D:\Codex-Migration-Backup\2026-08-15-mercury-sot"
```

The scripts refuse while any Codex Desktop, CLI, app-server, writer-lock, state-writer, or Claude process exists. Reopen Codex only after the unified frozen manifest is complete and every copied entry passes EFS/ACL verification.

- [ ] **Step 6: Rehearse complete local restoration before secret decisions or Import**

Run:

```powershell
python scripts/codex/import/codex_state_backup.py verify --backup "D:\Codex-Migration-Backup\2026-08-15-mercury-sot\codex-state" --restore-copy "D:\Codex-Migration-Backup\2026-08-15-mercury-sot\restore-rehearsal"
python scripts/codex/import/preflight_snapshot.py bind-backups --assets "D:\Codex-Migration-Backup\2026-08-15-mercury-sot\assets.jsonl" --manifest "D:\Codex-Migration-Backup\2026-08-15-mercury-sot\backup-manifest.json" --preflight "D:\Codex-Migration-Backup\2026-08-15-mercury-sot\preflight"
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/codex/import/secure_backup_root.ps1 -VerifyTree "D:\Codex-Migration-Backup\2026-08-15-mercury-sot"
```

Expected: database integrity passes, all session/import indexes load, every referenced local attachment/memory/session exists in the isolated copy, and `backup-manifest.json` binds source/copied hashes plus the frozen tool commit/hash.

### Task 5: Close the credential and irreversible-cloud gates

**Files:**
- Modify outside Git: user environment variable `OBSIDIAN_API_KEY`
- Create outside Git: `D:\Codex-Migration-Backup\2026-08-15-mercury-sot\credential-decisions.json`
- Create outside Git: `D:\Codex-Migration-Backup\2026-08-15-mercury-sot\cloud-rollback-acceptance.json`

**Interfaces:**
- Consumes: redacted findings and user actions in each credential provider.
- Produces: evidence that exposed credentials are invalid, plus explicit acceptance that official Import has no documented batch undo.

- [ ] **Step 1: Run the authoritative scan against the frozen bytes**

Run:

```powershell
python scripts/codex/import/secret_scan.py scan --assets "D:\Codex-Migration-Backup\2026-08-15-mercury-sot\assets.jsonl" --backup-manifest "D:\Codex-Migration-Backup\2026-08-15-mercury-sot\backup-manifest.json" --output "D:\Codex-Migration-Backup\2026-08-15-mercury-sot\secret-findings.json"
```

Before and after scanning, verify every source and backup hash against the frozen manifests. Any changed byte invalidates all decisions and returns to Task 4.

- [ ] **Step 2: Rotate the compromised Obsidian token in Obsidian's local REST/MCP plugin UI**

Generate the replacement locally. Do not paste it into Codex, a shell command line, or a file. In a short-lived local interactive PowerShell process, use `Read-Host -AsSecureString`, `SecureStringToBSTR`, set the user environment variable directly from the BSTR conversion result, and call `ZeroFreeBSTR` in `finally`. A managed .NET `String` cannot be reliably zeroed, so do not claim that it was.

- [ ] **Step 3: Prove the pasted token is invalid**

Use a local request that supplies the old credential from an encrypted operator-only source; record only HTTP status and timestamp. Expected: 401 or 403. Never place the old credential in the ledger or command history.

- [ ] **Step 4: Resolve every remaining scanner finding**

For each finding, update `credential-decisions.json` with asset ID, frozen source hash, frozen backup-manifest hash, category, action, timestamp, and evidence status. Run `secret_scan.py verify-decisions`; expected exit 0. Any source, backup, or manifest hash change makes the decision file invalid.

- [ ] **Step 5: Record the cloud rollback limitation**

Record that local backups can restore local Codex state but cannot remove cloud-created chats, memories, or projects; note which object types the current UI exposes for manual deletion. Require the user to acknowledge this file before the Import button is pressed.

### Task 6: Stage Cursor sources and run official Desktop Import

**Files:**
- Create: `scripts/codex/import/destination_inventory.py`
- Create: `tests/codex/import/test_destination_inventory.py`
- Temporary staging: `C:\Users\392fy\.cursor`
- Temporary staging: `C:\Users\392fy\.cursor-sandbox`
- Temporary staging: `C:\Users\392fy\AppData\Roaming\Cursor`
- Create outside Git: `D:\Codex-Migration-Backup\2026-08-15-mercury-sot\import-ledger.json`
- Create outside Git: `D:\Codex-Migration-Backup\2026-08-15-mercury-sot\import-history.json`

**Interfaces:**
- Consumes: clean credential gate, verified backups, C free-space gate, Claude standard paths, quarantined Cursor sources, and the frozen Task 2 junction relations.
- Produces: official Import history plus a local run ID and per-item success/failure ledger.
- Handoff: before official Import, Task 6 must implement and review a reversible isolation tool that makes the two frozen direct same-name Claude skill junction aliases unavailable to Desktop without following their `.agents` targets, then restores and verifies the exact frozen relations after Desktop closes. The concrete tool, receipt schema, process/ACL checks, and recovery protocol belong to Task 6, not Task 2.

- [ ] **Step 1: Implement and test stable destination inventory before opening Import**

Write fixtures for observable chat, project, skill, agent, setting, MCP, hook, and memory destinations. Define each type's stable key without using an Import result as evidence. Run `python -m unittest tests.codex.import.test_destination_inventory -v`; first expect missing implementation, then implement `capture --phase before|after` and require the test to pass.

- [ ] **Step 2: Review, commit, and freeze destination inventory before the irreversible step**

Stage only `destination_inventory.py` and its test; obtain two reviews; commit `feat(TASK-571): inventory Codex import destinations`; push through `git-safe.ps1`. Record the commit ID and script SHA-256 in the protected ledger. Any later byte change invalidates both destination snapshots and blocks Import until they are regenerated.

- [ ] **Step 3: Recheck the hard gates and capture destination state immediately before staging**

Run: `(Get-PSDrive C).Free`

Expected: at least `23846768104` bytes before restoring exactly `224447976` bytes of Cursor staging, leaving at least `23622320128` bytes (22 GiB). Re-run snapshot verification, `secure_backup_root.ps1 -VerifyTree`, repository guards, and secret-decision verification; all must exit 0. Run `destination_inventory.py capture --phase before` to record every locally observable Codex destination object before opening Import.

- [ ] **Step 4: Copy Cursor sources back to their standard detection paths**

Generate a sorted manifest for all 889 source files containing relative path, entry type, byte size, and SHA-256. Copy the three exact quarantine trees without overwriting existing paths. Generate the same manifest at the destination and require byte-for-byte equality; the total must be exactly `224447976` bytes. Abort if a destination exists or any entry differs.

- [ ] **Step 5: Perform the GUI-only official flow**

Only after the Task 6 junction-isolation handoff gate is implemented and proves both frozen aliases unavailable, open Settings > Import, select Claude Code and Cursor, select Tools & setup, every surfaced relevant project (expected Mercury and the Godot game), and every reviewed Mercury + SoT chat. Do not invent project selections for the design library or KB when the UI does not surface them; record each as `not-applicable-not-surfaced` and cover it through instructions, skills, MCP, and KB linkage. Keep automatic updates off. Before selecting Continue, confirm the cloud rollback acceptance. After completion, open Import history and record its timestamp/status and every surfaced failure; close Desktop and use the Task 6 tool to restore and verify both frozen junction relations.

- [ ] **Step 6: Remove temporary Cursor staging recoverably**

Move the three staged C paths to `D:\Codex-Migration-Backup\2026-08-15-mercury-sot\cursor-post-import-staging`; verify every file against the already frozen Cursor source rows without changing `backup-manifest.json`, verify the original quarantine remains intact and C paths are absent, write a separate `cursor-post-import-manifest.json`, and run `secure_backup_root.ps1 -VerifyTree` before proceeding.

- [ ] **Step 7: Create the local Import ledger**

Use a generated UUID as `local_run_id`; record the Import history timestamp, source agent, asset ID, selected/not-selected status, result, destination type, and failure message. Export every locally/UI-observable Import-history row to `import-history.json` with capture method and screenshot/metadata hash. Do not claim an OpenAI batch ID unless the UI or local vendor-import metadata exposes one.

- [ ] **Step 8: Capture destination state immediately after Import**

Run `destination_inventory.py capture --phase after` against the same observable stores and key definitions used for the pre-Import inventory. Preserve Import-history evidence before enabling hooks, MCP, plugins, or migrated agents.

### Task 7: Reconcile Import results before enabling anything

**Files:**
- Create: `scripts/codex/import/reconcile.py`
- Create: `tests/codex/import/test_reconcile.py`
- Create: `.mercury/docs/migration/2026-08-15-import-reconciliation.md`

**Interfaces:**
- Consumes: `assets.jsonl`, `import-ledger.json`, before/after Codex inventory, and Import history.
- Produces: one final disposition per asset and a nonzero exit if any asset or Import failure is unresolved.

- [ ] **Step 1: Write failing one-to-one reconciliation tests**

Test duplicate destinations, missing asset IDs, unclassified failures, imported settings that overwrite nothing, excluded items with recorded reasons, ambiguous stable keys, UI-only objects that cannot be inspected, and a ledger claim with no before/after destination delta.

- [ ] **Step 2: Run the tests and observe the missing reconciler failure**

Run: `python -m unittest tests.codex.import.test_destination_inventory tests.codex.import.test_reconcile -v`

Expected: FAIL because `reconcile.py` does not yet exist; the already reviewed destination-inventory tests continue to pass.

- [ ] **Step 3: Implement strict reconciliation**

Define stable matching keys per destination type before examining results: source identity plus normalized title/project/path and content hash where observable. Require a one-to-one before/after delta or an independently inspectable destination for every `imported` item. Never infer success merely from Import history or ledger text. Require every source asset to end as `imported`, `native-replacement`, `frozen-archive`, `excluded-secret`, or `excluded-domain`. Treat warnings, ambiguous matches, and UI-unobservable destinations as unresolved until the user provides inspectable evidence; unresolved observability blocks acceptance.

- [ ] **Step 4: Run reconciliation and write the human report**

Run:

```powershell
python -m unittest tests.codex.import.test_destination_inventory tests.codex.import.test_reconcile -v
python scripts/codex/import/reconcile.py verify-import --assets "D:\Codex-Migration-Backup\2026-08-15-mercury-sot\assets.jsonl" --ledger "D:\Codex-Migration-Backup\2026-08-15-mercury-sot\import-ledger.json" --import-history "D:\Codex-Migration-Backup\2026-08-15-mercury-sot\import-history.json" --before "D:\Codex-Migration-Backup\2026-08-15-mercury-sot\destination-before.jsonl" --after "D:\Codex-Migration-Backup\2026-08-15-mercury-sot\destination-after.jsonl" --report .mercury/docs/migration/2026-08-15-import-reconciliation.md
```

Expected: exit 0; all 68 chats and 381 memory/auxiliary items have final dispositions; zero unresolved, ambiguous, or unobservable imported claims; automatic updates remain off.

- [ ] **Step 5: Review, commit, and push the reconciliation checkpoint**

Stage only `reconcile.py`, its test, and the reconciliation report; obtain two reviews; commit `docs(TASK-571): reconcile official Mercury SoT import`; push through `git-safe.ps1`.
