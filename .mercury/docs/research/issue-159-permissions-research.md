# Issue #159 — Permission Blocking in Unattended Mode: Research Report

**Date**: 2026-04-05
**Researcher**: Research Agent (Claude Opus 4.6)
**Status**: Complete

---

## Executive Summary

Claude Code's `bypassPermissions` mode skips **human permission prompts** but does NOT skip **custom hook execution**. Hooks always fire regardless of permission mode. However, there are critical behavioral bugs and design gaps that cause hooks to block unattended operation in undesirable ways. The `permission_mode` field IS available in hook input JSON, enabling hooks to conditionally adjust behavior based on mode.

---

## Q1: What does `bypassPermissions` control? Does it affect hook execution?

### Findings

`bypassPermissions` is one of five permission modes: `default`, `acceptEdits`, `plan`, `dontAsk`, `bypassPermissions`.

**What it controls:**
- Skips all human permission confirmation prompts for tool use
- Automatically approves all tool calls that reach the permission evaluation step
- Equivalent to `--dangerously-skip-permissions` CLI flag

**What it does NOT control:**
- Custom hooks (PreToolUse, PostToolUse, Stop, etc.) — these **always execute** regardless of mode
- Deny rules — if a `deny` rule matches a tool, it is blocked even in `bypassPermissions` mode
- Hook-based blocks — a hook returning `exit 2` or `{"decision":"block"}` blocks the tool call even in bypass mode

### Key Insight

Permission evaluation order: **hooks** -> **deny rules** -> **ask rules** -> **allow rules** -> **permission mode**. `bypassPermissions` only affects the final step. Hooks and deny rules take precedence.

**Source**: [Configure permissions - Claude Code Docs](https://code.claude.com/docs/en/permissions)

---

## Q2: Does `bypassPermissions` suppress user confirmation when a hook returns `exit 2`?

### Findings

**No.** When a hook exits with code 2, the tool call is **blocked outright** — it never reaches permission evaluation. The `exit 2` block applies before permission rules are evaluated, so `bypassPermissions` has no effect.

The stderr output from the hook is fed back to Claude as an error message. However, there is a **known bug** (Issue #24327, opened 2026-02-09):

> When a PreToolUse hook exits with code 2, Claude often **stops responding** and waits for user input instead of acting on the error feedback. Expected behavior: Claude reads the error reason and adapts (e.g., runs `/dual-verify` then retries commit). Actual behavior: Claude goes idle, requiring manual "continue" to resume.

This is **intermittent** — sometimes Claude does act on the feedback, but increasingly it stops. This is the **primary cause of "permission blocking" in unattended mode** for Mercury's hooks.

**Sources**:
- [Issue #24327 - PreToolUse hook exit code 2 causes Claude to stop](https://github.com/anthropics/claude-code/issues/24327)
- [Hooks reference - Claude Code Docs](https://code.claude.com/docs/en/hooks)

### Additional Bug: Async Execution in Bypass Mode

Issue #20946 (opened 2026-01-26) reports that in `--dangerously-skip-permissions` mode, PreToolUse hooks may fire **asynchronously** — the command executes immediately while the hook runs in the background. By the time the hook returns `exit 2`, the command has already completed.

**Impact on Mercury**: The `pre-commit-guard.sh` might fail to block `git commit` if running in bypass mode with certain Claude Code versions.

**Source**: [Issue #20946 - PreToolUse hooks don't block in bypass mode](https://github.com/anthropics/claude-code/issues/20946)

---

## Q3: Can hooks auto-approve in bypass mode while blocking in normal mode?

### Findings

**Yes.** The `permission_mode` field is available in the hook input JSON. Hooks can read this field and conditionally decide to allow or block.

**Hook input JSON includes:**

```json
{
  "session_id": "abc123",
  "transcript_path": "/home/user/.claude/projects/.../transcript.jsonl",
  "cwd": "/home/user/my-project",
  "permission_mode": "bypassPermissions",
  "hook_event_name": "PreToolUse",
  "tool_name": "Bash",
  "tool_input": {
    "command": "git commit -m 'fix: something'"
  }
}
```

**Implementation pattern:**

```bash
INPUT=$(cat)
PERM_MODE=$(echo "$INPUT" | jq -r '.permission_mode // "default"')

# In bypass mode, allow the action (don't block unattended operation)
if [ "$PERM_MODE" = "bypassPermissions" ]; then
  exit 0
fi

# In normal mode, enforce the gate
# ... existing blocking logic ...
exit 2
```

**Note**: Issue #4719 (Feature Request) originally requested this capability. The `permission_mode` field was subsequently added to hook input JSON and is now available in current versions.

**Sources**:
- [Issue #4719 - Expose Active Permission Mode to PreToolUse Hook](https://github.com/anthropics/claude-code/issues/4719)
- [Hooks reference - Claude Code Docs](https://code.claude.com/docs/en/hooks)

---

## Q4: Hook JSON response format — can hooks return structured decisions?

### Findings

**Yes.** There are two response mechanisms:

### Mechanism 1: Exit codes (simple)

| Exit Code | Behavior |
|-----------|----------|
| `0` | Allow (or process JSON stdout) |
| `2` | Block the tool call; stderr is fed back to Claude |
| Other non-zero | Hook error (tool call proceeds) |

### Mechanism 2: JSON stdout with exit 0 (structured)

Exit with code 0 and print JSON to stdout for fine-grained control:

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "allow",
    "permissionDecisionReason": "Bypass mode - automated operation",
    "additionalContext": "This is an automated session."
  }
}
```

**Valid `permissionDecision` values:** `"allow"` | `"deny"` | `"ask"`

**Important rules:**
- `exit 2` ignores any JSON output — the two mechanisms are mutually exclusive
- When multiple hooks fire, Claude Code picks the **most restrictive** answer (`deny` > `ask` > `allow`)
- A hook returning `"allow"` does NOT bypass deny rules — deny rules still take precedence
- `"ask"` escalates to user confirmation (problematic in unattended mode)

### For Stop hooks

Stop hooks use a different format with top-level `decision`:

```json
{"decision": "block", "reason": "Staged uncommitted changes detected."}
```

**Source**: [Hooks reference - Claude Code Docs](https://code.claude.com/docs/en/hooks)

---

## Q5: Environment variables available inside hook scripts

### Findings

The following are available to hook scripts:

| Variable | Description |
|----------|-------------|
| `$CLAUDE_PROJECT_DIR` | Project root directory |
| Standard env vars | PATH, HOME, etc. from shell profile |

The **permission mode is NOT available as an environment variable** — it is only available in the **hook input JSON** via the `permission_mode` field (read from stdin).

There is no `CLAUDE_PERMISSION_MODE` environment variable. The feature request (Issue #6227) to expose it as an env var exists but the current implementation requires reading it from stdin JSON.

**Source**: [Issue #6227 - Expose Active Permission Mode to Hooks and Statusline](https://github.com/anthropics/claude-code/issues/6227)

---

## Mercury Hook Analysis and Recommendations

### Current Problem

Mercury's hooks use `exit 2` to block operations. In unattended/bypass mode:
1. Hooks still fire and block (by design)
2. Claude often stops instead of acting on the error (bug #24327)
3. In some versions, hooks may fire async and fail to block in bypass mode (bug #20946)

### Hook-by-Hook Recommendations

| Hook | Current Behavior | Recommendation | Rationale |
|------|-----------------|----------------|-----------|
| **pre-commit-guard.sh** | Blocks commit without review flag | **Add bypass-mode passthrough** | In automated pipelines, the orchestrator manages review flow. Blocking causes stalls. |
| **web-research-gate.sh** | Blocks Edit/Write without web-researched flag | **Add bypass-mode passthrough** | The 60s TTL flag already expires; in unattended mode this creates frequent stalls. |
| **web-research-extended-gate.sh** | Same as above (Layer 2) | **Add bypass-mode passthrough** | Same reasoning as Layer 1. |
| **push-guard.sh** | Blocks push to protected branches | **KEEP BLOCKING always** | Safety-critical. Push to develop/master must never be automated without PR. |
| **pr-create-guard.sh** | Blocks PR create without metadata | **Add bypass-mode passthrough** | Automated PR creation by orchestrator should not be blocked. |
| **pr-merge-guard.sh** | Blocks merge without CodeRabbit review | **KEEP BLOCKING always** | Safety-critical. Merging without review violates project rules. |
| **scope-guard.sh** | Blocks C drive installs | **KEEP BLOCKING always** | Safety-critical. C drive protection must always be enforced. |
| **stop-guard.sh** | Blocks stop with staged changes | **Add bypass-mode passthrough with warning** | In automated cleanup, staged changes may be intentional. Log warning instead. |

### Implementation Pattern

For hooks that should passthrough in bypass mode, add this near the top (after reading stdin):

```bash
# Read stdin (hook JSON input)
INPUT=$(cat)

# In bypass/unattended mode, allow the action
if command -v jq >/dev/null 2>&1; then
  PERM_MODE=$(echo "$INPUT" | jq -r '.permission_mode // "default"' 2>/dev/null)
else
  PERM_MODE=$(echo "$INPUT" | sed -n 's/.*"permission_mode"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)
fi

if [ "$PERM_MODE" = "bypassPermissions" ] || [ "$PERM_MODE" = "dontAsk" ]; then
  exit 0
fi
```

### Safety Boundary: MUST Always Block Regardless of Mode

These operations MUST require enforcement even in unattended mode:

1. **Direct push to develop/master/main** (push-guard.sh) — violates PR-only merge policy
2. **PR merge without review** (pr-merge-guard.sh) — violates CodeRabbit review requirement
3. **C drive installation** (scope-guard.sh) — violates environment policy

### Migration to JSON Responses (Future Improvement)

Consider migrating from `exit 2` to JSON `permissionDecision` responses. Benefits:
- `exit 2` causes Claude to sometimes stop (bug #24327)
- JSON `"deny"` with `additionalContext` provides clearer guidance to Claude
- JSON `"allow"` with `permissionDecisionReason` gives audit trail

Example migration for pre-commit-guard:

```bash
# Instead of: exit 2
# Use:
cat <<'DENY'
{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"Code review required. Run /dual-verify first.","additionalContext":"After running /dual-verify, retry the commit. The review-passed flag will be set automatically."}}
DENY
exit 0
```

**Caveat**: This migration needs testing. JSON deny may have the same "Claude stops" behavior. The bug may be in how Claude interprets blocked tool calls, not in the signaling mechanism.

---

## Phase 2 — .claude/ Protected Directory (added after user feedback)

### Root Cause

Claude Code hard-protects `.claude/` directory writes even in `bypassPermissions` mode ([#38806](https://github.com/anthropics/claude-code/issues/38806), [#37253](https://github.com/anthropics/claude-code/issues/37253)). This means `Edit`/`Write`/`Bash` tool calls targeting `.claude/hooks/state/` still trigger permission prompts.

### Solution Implemented

Migrated all hook state files from `.claude/hooks/state/` to `.mercury/state/`:
- `.mercury/state/` is outside the protected directory → no permission prompts
- All 10 hooks updated with fallback: `_PROJECT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"`
- Legacy tracked state files removed from git index
- Both dual-verify skill copies reference new path

### Protected Directory Scope (per official docs)

| Path | Protected | Notes |
|------|-----------|-------|
| `.git/` | Yes | Repository integrity |
| `.claude/` | Yes | Agent configuration |
| `.claude/commands/` | Exempt | Claude writes here routinely |
| `.claude/agents/` | Exempt | Claude writes here routinely |
| `.claude/skills/` | Exempt | Claude writes here routinely |
| `.vscode/` | Yes | Editor configuration |
| `.idea/` | Yes | Editor configuration |
| `.husky/` | Yes | Git hooks |

---

## Known Issues and Risks

| Issue | Status | Impact |
|-------|--------|--------|
| #24327 — exit 2 causes Claude to stop | Open (2026-02-09) | High — primary cause of unattended stalls |
| #20946 — hooks async in bypass mode | Open (2026-01-26) | Critical — safety hooks may not actually block |
| #4719 — expose permission_mode | Resolved — field now in JSON | Enables conditional behavior |
| #37420 — bypass mode resets after hook returns "ask" | Open | Medium — can cause mode drift |

---

## Sources

- [Configure permissions - Claude Code Docs](https://code.claude.com/docs/en/permissions)
- [Hooks reference - Claude Code Docs](https://code.claude.com/docs/en/hooks)
- [Automate workflows with hooks - Claude Code Docs](https://code.claude.com/docs/en/hooks-guide)
- [Issue #24327 - PreToolUse hook exit code 2 causes stop](https://github.com/anthropics/claude-code/issues/24327)
- [Issue #20946 - PreToolUse hooks don't block in bypass mode](https://github.com/anthropics/claude-code/issues/20946)
- [Issue #4719 - Expose Active Permission Mode to PreToolUse Hook](https://github.com/anthropics/claude-code/issues/4719)
- [Issue #6227 - Expose Active Permission Mode to Hooks and Statusline](https://github.com/anthropics/claude-code/issues/6227)
- [Issue #37420 - Bypass mode resets after hook returns "ask"](https://github.com/anthropics/claude-code/issues/37420)
- [Issue #38806 - Allow bypassPermissions to bypass .claude/ protections](https://github.com/anthropics/claude-code/issues/38806)
- [Claude Code Permission Hook: Skip Prompts Safely](https://claudefa.st/blog/tools/hooks/permission-hook-guide)
