# Mercury + SoT Codex-Native Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Import the complete Mercury + SoT working domain through the official desktop flow, reconcile it into a pure Codex-native harness, restore both MCP links, and retire superseded Claude-era control layers without losing history.

**Architecture:** Four ordered plans form one migration transaction. Preflight and credential invalidation precede the GUI-only Import; native harness and MCP work then run against the imported result; destructive retirement runs only after parity evidence passes. Each plan produces its own committed evidence and rollback checkpoint.

**Tech Stack:** Codex Desktop/CLI 0.147.0, Python 3 stdlib, PowerShell 7, JSON/TOML, Codex Hooks/Skills/Subagents/MCP, Git through `scripts/codex/git-safe.ps1`.

**Spec:** `.mercury/docs/design/2026-08-14-codex-native-import-design.md`

## Global Constraints

- Canonical roots are `D:\Mercury\Mercury`, `D:\ShipOfTheseus\Ship_of_Theseus`, `D:\ShipOfTheseus\SoT-fyc-space`, and `D:\ShipOfTheseus\ShipOfTheseus-KB`.
- Preserve every pre-existing dirty path; every migration commit stages explicit files only.
- Use Codex Desktop Import, keep automatic updates off, and do not fall back to the 50-chat CLI flow.
- Never record a secret value or reversible secret digest in a manifest, log, commit, or chat.
- The Obsidian token pasted in chat is compromised and must remain invalid; its replacement is entered locally and never pasted.
- The Import click is blocked unless C free bytes are at least `20 * 2^30`; target is `22 * 2^30`.
- Talent optimization remains frozen; its unique validator is archived but not redesigned.
- Every repository mutation follows issue #571, two independent reviews, `git-safe.ps1`, and a dedicated push.
- Until the committed `preflight_snapshot.py guard` exists, the first tooling/canonical-authority mutations use the same read-only checks explicitly (`rev-parse` branch/HEAD, merge-base ancestry, porcelain-v2 status, SHA-256 of every dirty byte, and exact target-overlap rejection) and store their before/after evidence in the protected migration root. Once the guard exists, no manual substitute is allowed.
- After that tool is frozen, every checkpoint in every child plan expands to the mandatory transaction: `begin-approved` with exact repo/owner/issue/planned paths and before hashes → `guard` → edit/test/two reviews/explicit commit → `seal-approved` with commit, after hashes, and review evidence → chain verification → explicit push. On failure, `abort-approved` is allowed only when it proves the tree returned byte-for-byte to the begin state; an open or post-hoc row blocks all later mutations and final acceptance.

---

### Task 1: Execute the four plans with the canonical-authority front gate

**Files:**
- Read/execute: `docs/superpowers/plans/2026-08-15-import-preflight-and-ledger.md`
- Read/execute: `docs/superpowers/plans/2026-08-15-codex-native-harness.md`
- Read/execute: `docs/superpowers/plans/2026-08-15-sot-kb-mcp-linkage.md`
- Read/execute: `docs/superpowers/plans/2026-08-15-legacy-retirement.md`
- Create: `.mercury/docs/migration/2026-08-15-migration-transaction-acceptance.md`
- Create outside Git: `D:\Codex-Migration-Backup\2026-08-15-mercury-sot\terminal-receipt.json`

**Interfaces:**
- Consumes: approved design commit `9bcc036` and the cleanup evidence under `D:\Codex-Cleanup-Quarantine`.
- Produces: four independently reviewed evidence manifests and a final #571 acceptance record.

- [ ] **Step 1: Protect evidence, freeze dirty state, then correct SoT canonical authority**

Execute Task 1 of `2026-08-15-import-preflight-and-ledger.md` to create the protected migration root. Before its first repository write, hold the explicit bootstrap guard result in memory and persist it as soon as the protected root exists. Capture the four-root bootstrap branch/HEAD/status/dirty-byte hashes there. Then execute Task 1 of `2026-08-15-sot-kb-mcp-linkage.md` through its SoT-owned commit. Record the design-library commit ID and test evidence in the migration ledger. Do not start asset inventory while `power`, `cd`, or `range` has conflicting ownership text.

- [ ] **Step 2: Finish preflight and the official Import ledger**

Run Tasks 2–7 in `2026-08-15-import-preflight-and-ledger.md` (Task 1 is already complete). Do not begin the next plan until its reconciliation report has zero unclassified assets and zero unresolved Import failures.

- [ ] **Step 3: Build and verify the native harness**

Run every checkbox in `2026-08-15-codex-native-harness.md`. Keep imported Claude handlers disabled until the Codex event matrix passes.

- [ ] **Step 4: Restore the remaining SoT, KB, Godot, and Obsidian linkage**

Run Tasks 2–6 in `2026-08-15-sot-kb-mcp-linkage.md`. A successful config parse is not an MCP pass; each server needs an actual harmless tool call.

- [ ] **Step 5: Retire legacy entry points**

Run every checkbox in `2026-08-15-legacy-retirement.md`. Delete only allowlisted paths whose callers and replacements both have recorded evidence.

- [ ] **Step 6: Run the reviewed final transaction runner**

Run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/codex/harness/run-final-transaction.ps1 -Mode PreReport -SkillPhase final -OutputRoot "D:\Codex-Migration-Backup\2026-08-15-mercury-sot\final-precheck"
```

The reviewed runner invokes strict Import reconciliation with assets/ledger/history/before/after, four-root `final-compare` with the sealed approved-mutation ledger, native harness `-SkillPhase final`, and XML lint. Expected: every redacted command envelope is complete and exit 0; the summary is PASS. A matching path list alone is insufficient.

- [ ] **Step 7: Materialize the transaction-level acceptance ledger**

Run `begin-approved` for the one planned report path and atomically store its row ID at protected `transaction-report-row-id.txt`. Immediately run `preflight_snapshot.py guard --repo "D:\Mercury\Mercury" --planned-path ".mercury/docs/migration/2026-08-15-migration-transaction-acceptance.md"` and fail if the path was concurrently created/changed or any dirty/HEAD invariant drifted. Then run `write-transaction-report` with the exact Step 6 envelopes, all four component report hashes, the pre-report sealed chain head, pending row ID, Import-history hash, and current C free bytes. Write the report; fail if any input is missing, stale, or non-PASS. Obtain two independent read-only reviews of the exact report bytes and store their agent IDs, verdicts, evidence hashes, report SHA-256, and timestamps in two protected review-evidence JSON files. The committed report deliberately records the parent chain head and pending row, not a self-referential terminal head.

- [ ] **Step 8: Commit and push the final acceptance ledger**

Run:

```powershell
$rowId = (Get-Content -LiteralPath "D:\Codex-Migration-Backup\2026-08-15-mercury-sot\transaction-report-row-id.txt" -Raw).Trim()
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/codex/git-safe.ps1 add .mercury/docs/migration/2026-08-15-migration-transaction-acceptance.md
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/codex/guard.ps1 mark-review
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/codex/git-safe.ps1 commit -Message "docs(TASK-571): accept Codex-native Mercury SoT harness"
python scripts/codex/import/preflight_snapshot.py seal-approved --row-id $rowId --repo "D:\Mercury\Mercury" --commit HEAD --review-evidence "D:\Codex-Migration-Backup\2026-08-15-mercury-sot\transaction-report-review-a.json" --review-evidence "D:\Codex-Migration-Backup\2026-08-15-mercury-sot\transaction-report-review-b.json" --ledger "D:\Codex-Migration-Backup\2026-08-15-mercury-sot\approved-mutations.json"
python scripts/codex/import/preflight_snapshot.py verify-chain --ledger "D:\Codex-Migration-Backup\2026-08-15-mercury-sot\approved-mutations.json"
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/codex/git-safe.ps1 push origin feature/TASK-571
```

Expected: the final commit contains only the new transaction-level acceptance ledger (distinct from Legacy Task 7's component report), the row seals and chain verifies, and the protected push succeeds.

- [ ] **Step 9: Seal the terminal state outside Git**

Run `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/codex/harness/run-final-transaction.ps1 -Mode Terminal -SkillPhase final -OutputRoot "D:\Codex-Migration-Backup\2026-08-15-mercury-sot\final-terminal" -TerminalReceipt "D:\Codex-Migration-Backup\2026-08-15-mercury-sot\terminal-receipt.json"`. It reruns strict reconciliation, four-root `final-compare` after the report commit/seal, native final-phase acceptance, and XML lint. The atomically written receipt contains the final Mercury commit, final approved-mutation chain head, transaction-report path/hash, every terminal envelope hash/exit code, Import-history hash, four-root final-compare hash, timestamp, and C free bytes. Run `secure_backup_root.ps1 -VerifyTree`. No repository or config mutation is permitted after this receipt; any such change invalidates it and requires a new terminal cycle.
