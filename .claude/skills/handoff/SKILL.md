---
name: handoff
description: Generate a structured handoff document and ready-to-paste starting prompt for the next session. Use `/handoff` for manual mode (output only). Use `/handoff:auto` to auto-launch the new session via `claude` CLI after the document is written.
argument-hint: "[:auto] [optional extra instructions for the next session]"
user-invocable: true
allowed-tools:
  - Read
  - Write
  - Bash
  - Glob
  - Grep
---

# /handoff — Session Handoff & Continuation

You are executing the handoff skill. This is the **only** entry point for
handoff — nothing triggers automatically. Follow these steps precisely.

## Invocation modes

Parse `$ARGUMENTS`:

| Trigger | Mode | Behavior |
|---|---|---|
| `/handoff` (no args) | **manual** | Write doc + output starting prompt in chat. Do NOT launch a new session. Old session stays alive. |
| `/handoff <instructions>` | **manual + extra** | Same as manual; put `<instructions>` into the "User Instructions" section of the handoff doc. |
| `/handoff:auto` | **auto** | Write doc + output starting prompt + **auto-launch** new session via `claude` CLI after Pre-Termination Checklist passes. Old session should `/exit` after. |
| `/handoff:auto <instructions>` | **auto + extra** | Same as auto, with extra instructions embedded. |
| `/handoff auto` (legacy) | **auto** | Same as `/handoff:auto`. Accept both syntaxes. |

Default (no explicit auto): manual mode. Never auto-launch without an
explicit `:auto` / `auto` token.

## Step 1: Gather Context

Layer these sources (each optional):

### Layer 1: Conversation context (always available)

You have the full conversation in context. Synthesize:
- What the user was working on
- Decisions made and their rationale
- Problems encountered and solutions found
- Incomplete work and known next steps

### Layer 2: Memory search (agentic)

Search the project's auto-memory directory:
```
~/.claude/projects/<encoded_cwd>/memory/
```
Where `<encoded_cwd>` is the cwd with `:` `\` `/` replaced by `-`, leading
`-` stripped.

Glob for `*.md`. Read previous handoffs, checkpoints, project memories.

### Layer 3: Project documentation (if present)

Skim `CLAUDE.md`, `AGENTS.md`, `README.md` at project root or parents.
Extract what's relevant to the handoff.

### Layer 4: Version control (optional)

If git is available:
```bash
git status --short 2>/dev/null
git log --oneline -5 2>/dev/null
git branch --show-current 2>/dev/null
```

### Layer 5: GitHub Project / Issues (MANDATORY for Mercury-class repos)

If the project uses GitHub Issues + a GitHub Project (v2), query for next-task selection:

```bash
# Adjust project number per repo. Mercury uses Project #3.
gh issue list --label "P0" --state open --json number,title,labels --limit 50 2>/dev/null
gh issue list --label "P1" --state open --json number,title,labels --limit 50 2>/dev/null

OWNER=$(gh repo view --json owner --jq '.owner.login' 2>/dev/null)
gh project item-list 3 --owner "$OWNER" --format json --limit 100 2>/dev/null | \
  python -c "
import json, sys
data = json.load(sys.stdin)
items = [i for i in data.get('items', []) if i.get('status') in ('Todo', 'In Progress')]
status_order = {'In Progress': 0, 'Todo': 1}
for i in sorted(items, key=lambda x: (status_order.get(x.get('status', ''), 9), x.get('priority', 'P9'))):
    num = i.get('content', {}).get('number', '?')
    print(f'#{num} [{i.get(\"priority\",\"?\")}] {i.get(\"title\",\"?\")} ({i.get(\"status\",\"?\")})')
"
```

Selection criteria (in order):
1. Actively blocked P1 bugs with known root cause
2. In-Progress items from the Project board
3. Highest-priority P0 Todo from Project board
4. Next Phase sub-item per `.mercury/docs/EXECUTION-PLAN.md` (or equivalent)

Pick **one** primary task + one secondary fallback. Never produce a menu.

## Step 2: Generate Handoff Document

Write to:
```
~/.claude/projects/<encoded_cwd>/memory/session-handoff.md
```

```markdown
---
name: session_handoff
description: "Session handoff — <one-line summary>"
type: project
---
# Session Handoff — <YYYY-MM-DD>

## Starting Prompt

这是 S{N+1}。<1-line context>。

### 当前状态
<repo / branch / commit / clean or dirty>

### S{N+1} 主任务：<Issue #N — specific title>

**背景**：<1-2 lines why this is highest priority, cite Issue/Project>

**执行步骤**：
1. <actionable step with file paths / commands>
2. <actionable step>
3. <verification>
4. <commit / PR>

**次要任务（主任务完成后）**：<Issue #N or Phase X-Y, one line>

### 参考文档
<only main-task-related docs>

## Task State
- **Primary Issue**: #N [title] (status)
- **Branch**: <branch>
- **Completed**: <commits + what they did>
- **In Progress**: <current step / blockers>
- **Pending**: <remaining items>

## Key Context (compact-loss protection)
- <architecture decisions not recoverable from code>
- <gotchas / constraints>
- <important file paths + roles>

## User Instructions
<If args passed in, embed here. Else "No additional instructions.">
```

**CRITICAL RULE for Starting Prompt**: one primary task with numbered
execution steps. Never a menu of options. The next session must be able to
start executing step 1 without asking for direction.

## Step 3: Session-chain update (best-effort, optional)

Session-chain tracking is provided by the `claude-handoff` plugin (see
`github.com/392fyc/claude-handoff`). If the plugin's session_chain DB
exists, record this handoff edge:

```bash
python -c "
import os, sys
from pathlib import Path

# Plugin DB default location (claude-handoff plugin)
db_path = Path(os.environ.get('CLAUDE_HANDOFF_DB') or
                Path.home() / '.claude' / 'handoff' / 'session_chain.db')
if not db_path.exists():
    print('session_chain DB not found — skipping (plugin not installed or scaffold-only)')
    sys.exit(0)

# Defer actual writes to the plugin's session_chain package; do not duplicate
# schema logic here. If the package is importable, use it; else skip.
try:
    from session_chain import SessionChainDB
except ImportError:
    print('session_chain package not importable — skipping (scaffold not wired yet)')
    sys.exit(0)

db = SessionChainDB(db_path)
parent = os.environ.get('CLAUDE_SESSION_ID')
if not parent:
    print('CLAUDE_SESSION_ID not set — cannot record handoff edge')
    sys.exit(0)

db.record_handoff(
    chain_id=os.environ.get('CLAUDE_HANDOFF_CHAIN_ID') or parent,
    parent_session_id=parent,
    child_session_id=None,  # bound later by child session's SessionStart hook
    project_dir=os.getcwd(),
    task_ref=os.environ.get('CLAUDE_HANDOFF_TASK_REF'),
)
print('session_chain edge recorded (child pending)')
"
```

**IMPORTANT**: the AGENTKB-based orchestrator path (`$AGENTKB_DIR/scripts/handoff-orchestrator.py`)
is **deprecated**. Do not call it. The replacement is the `claude-handoff`
plugin's session_chain module (above), currently a scaffold — write side
may not yet be wired at session-start.py.

## Step 4: Pre-Termination Checklist

Before launching a new session (auto mode) OR outputting the prompt (manual
mode), verify **all** in-flight work has finished. A handoff is a
**terminal event** for the old session — nothing carries over automatically.

Confirm each:

- **No pending tool calls.** All Bash / file / tool operations returned.
- **No background processes.** `run_in_background` tasks, builds, spawned
  subprocesses have completed OR the user has explicitly accepted they
  continue after handoff.
- **No unsaved state.** Edits / commits / writes are actually on disk.
- **No pending user questions.** If the old session owes a reply, answer it.

If any item is incomplete, finish or defer explicitly. Surface status:
"All pending work done — ready to hand off?"

## Step 5: Output & Dispatch

Always do **both** of these — never skip either:

1. **Output the Starting Prompt section directly in chat** — PRIMARY
   artifact. User pastes it verbatim as the first message of a new session.
2. **Save the full handoff document** to the memory path above. The auto-
   memory system will load it at next session start.

### Manual mode (`/handoff` default)

After Step 5.1 + 5.2, **stop**. Tell the user the old session stays alive;
they can copy the prompt to a new session manually or continue working in
this one. Do NOT spawn any new process.

Optional: offer to launch if the user later says so (Step 6).

### Auto mode (`/handoff:auto`)

After Step 5.1 + 5.2, and Pre-Termination Checklist passed:

**Windows** (Windows Terminal with new tab):
```bash
wt -w 0 nt --title "Handoff" -- claude "<STARTING_PROMPT_VERBATIM>"
```

**macOS / Linux** (background new session in a new terminal):
```bash
# If terminal multiplexer is preferred:
tmux new-window -n handoff "claude \"<STARTING_PROMPT_VERBATIM>\""
# Or plain background spawn:
claude "<STARTING_PROMPT_VERBATIM>" &
```

The positional argument to `claude` is the session's first user message —
documented at <https://code.claude.com/docs/en/cli-reference>. The
SessionStart hook will inject the full handoff document as
`additionalContext`, so the new session has everything.

**Quoting considerations**:
- The starting prompt may contain double quotes, backticks, `$` signs, and
  newlines.
- Prefer writing the prompt to a temp file and using `claude "$(cat
  /tmp/handoff-prompt.txt)"` on POSIX, or using PowerShell's here-string on
  Windows, to avoid shell-escape hazards.
- If the prompt starts with `-`, it will be parsed as a CLI option (see
  <https://github.com/anthropics/claude-code/issues/3844>); prefix with
  `-- ` or wrap to avoid.

After spawning the new process, do NOT continue producing output in the old
session. The old session's job is done. Advise user to `/exit` (or close
tab) once they confirm the new session is running.

## Step 6: Post-Dispatch (manual mode only, optional)

If the user returns after manual mode and says "launch it now", re-enter
the auto path from Step 5 (auto mode).

## Rules

- Starting Prompt must be **self-contained** — zero context assumed in the
  new session.
- Include specific file paths, line numbers, commands.
- Never include secrets, API keys, credentials.
- The chat-output prompt is the PRIMARY deliverable — never skip it.
- Do NOT add automatic hooks for SessionEnd or PreCompact — handoff is
  **explicit only**.
- Handoff is a **terminal event** for the old session.
- Before terminating, verify all pending work has completed. Nothing
  carries over automatically.
- Manual mode MUST NOT spawn processes. Only `:auto` (or legacy `auto`) token
  triggers the `claude` CLI launch.
- The legacy `$AGENTKB_DIR/scripts/handoff-orchestrator.py` path is
  DEPRECATED. Do not invoke it. The `claude-handoff` plugin is the
  canonical session-continuity module
  (<https://github.com/392fyc/claude-handoff>).
