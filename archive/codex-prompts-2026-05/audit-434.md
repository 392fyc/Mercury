# Codex sync-audit — #434 gh auth preflight (lane/main/434-gh-auth-preflight)

## Context

Mercury Phase 6 GUI MVP v2 sub-task: add `gh auth status` preflight check to the Tauri Issue/PR dashboard. Parent backlog #427 (5/8 v2 items remain post-S128).

## Pre-commit dual-verify rubric (S111 strict)

You are the **read-only Codex code-audit half** of the Mercury dual-verify gate. Produce a structured verdict: **PASS / NEEDS-CHANGES / FAIL** + Critical / Major / Minor / Nit findings. Mercury's policy is "no commit without dual-verify PASS"; this audit is consumed by the Main Agent before commit.

## Changes under review

Branch: `lane/main/434-gh-auth-preflight` (off `develop` @ `032e268`)

```
mercury-gui/src-tauri/capabilities/default.json |  11 ++
mercury-gui/src-tauri/src/gh_dashboard.rs       | 139 ++++++++++++++++++++++++
mercury-gui/src-tauri/src/lib.rs                |   1 +
mercury-gui/src/components/GitHubDashboard.tsx  |  44 +++++++-
mercury-gui/src/hooks/useGitHubData.ts          |  28 ++++-
mercury-gui/src/lib/ghTypes.ts                  |  10 ++
6 files changed, +226/-7 lines
```

Inspect via `git diff develop..HEAD -- mercury-gui/` from the repo root.

## What to verify

1. **Rust IPC `check_gh_auth`** in `mercury-gui/src-tauri/src/gh_dashboard.rs`:
   - Exit-code semantics match the official gh CLI contract (exit 0 = authenticated, exit 1 = auth issues; verified against cli.github.com/manual/gh_auth_status, gh v2.87.3 local).
   - `--hostname github.com` correctly constrains the check so an unrelated host's broken token doesn't false-flag.
   - Subprocess timeout reused from existing constant (30s) — no new hardcoded values.
   - `redact_home` applied to error message paths (no home-path PII leak).
   - `parse_gh_account` extracts the login via the `" account "` marker; first-match-wins on multi-host.
   - Both stdout and stderr combined for the account scan (handles gh's historical stdout vs stderr drift per cli/cli#7447).
   - Unit tests cover: success-path parse, failure-path None, multi-host first-match, empty-input None, serde roundtrip for both authenticated + unauthenticated `GhAuthStatus`.

2. **Capability config** `mercury-gui/src-tauri/capabilities/default.json`:
   - New `gh-auth-status` allow entry follows existing shape (`name`, `cmd: gh`, `sidecar: false`, `args`).
   - Hostname validator `^[\w.-]+$` is appropriately tight (host shape only, no shell metacharacters).
   - Order of args (`auth`, `status`, `--hostname`, <validated>) matches Tauri shell capability slot semantics.

3. **Frontend `useGitHubData.ts`**:
   - Preflight runs **before** the issue/PR fetch on first call.
   - `authPassedRef` sticky flag avoids unnecessary IPC calls after first OK.
   - Re-check while `authError` is set (so re-auth in another terminal recovers without GUI restart).
   - `reqIdRef` race guard intact (still applied to both the auth check and the fetch).
   - `authError` distinct from `error` state.

4. **Dashboard UI** `mercury-gui/src/components/GitHubDashboard.tsx`:
   - `authError` toast takes precedence over `error` toast (`error && !authError`).
   - Loading state also gated by `!authError` (no spinner trailing the actionable toast).
   - `redactHomePaths` applied to surfaced text.
   - `role="alert"` + `aria-live="polite"` on the auth toast for accessibility.
   - "Re-check authentication" button calls `refresh(true)` to force.

5. **Rust IPC registration** `mercury-gui/src-tauri/src/lib.rs`:
   - `check_gh_auth` added to `invoke_handler` macro.

## Specific things to look for

- **Capability shape correctness** — does the args list match Tauri 2 shell capability validator schema? Compare against existing `gh-issue-list` / `gh-pr-list` entries.
- **Race / staleness** — is the preflight IPC's late arrival on a previous request handled by reqIdRef? (It should be — both `invoke` calls share the same reqId scope.)
- **redact_home coverage** — is there any string path that could leak home paths to the frontend (e.g. stderr containing `C:\Users\...\.gh-config` in a misconfigured env)?
- **Account parser robustness** — does `parse_gh_account` handle the edge case where `" account "` appears literally inside an unrelated stderr line?
- **Tauri command signature** — `#[tauri::command] pub async fn check_gh_auth(app: tauri::AppHandle) -> Result<GhAuthStatus, String>` — does this match the existing `fetch_gh_dashboard` convention?
- **JSON serde — camelCase** — the existing types use `#[serde(rename = "camelCase")]`; should `GhAuthStatus` do the same? (It has no multi-word fields, so default snake_case is identical to camelCase here — verify.)

## Output format

Return:

```
=== VERDICT: PASS | NEEDS-CHANGES | FAIL ===

Critical findings (block commit):
  C1. ...

Major findings:
  M1. ...

Minor findings:
  Mn1. ...

Nits:
  N1. ...

Confidence: HIGH | MEDIUM | LOW
```

PASS = 0 Critical + 0 Major OK.
NEEDS-CHANGES = ≥1 Critical/Major.
FAIL = subprocess error / non-reviewable diff.

Mercury's tradition is short PR cycles; if you flag Minor / Nit only, the Main Agent will likely take the PASS path and defer them. So **be honest about severity**.
