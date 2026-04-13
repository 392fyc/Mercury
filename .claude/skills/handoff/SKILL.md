---
name: handoff
description: Generate a structured handoff document and optionally start a new session via Agent SDK to continue the task
argument-hint: "[optional additional instructions for the next session]"
level: 4
---

<Purpose>
Generate a structured handoff document capturing the current session's task state, key decisions, and pending work. Write it to auto-memory so the next session inherits full context. Optionally launch a continuation session via the Agent SDK handoff orchestrator.
</Purpose>

<Use_When>
- User says "/handoff", "handoff", "交接", "转手"
- Agent reaches a natural task boundary and needs to continue in a new session
- Context is getting large and a clean handoff would preserve quality
- User wants to pause work and resume later with full context
</Use_When>

<Do_Not_Use_When>
- Task is fully complete with nothing to continue
- User just wants a summary (use normal conversation instead)
</Do_Not_Use_When>

<Instructions>
When this skill is invoked, follow these steps exactly:

## Step 1: Gather State

Read the current session checkpoint if it exists:
```
~/.claude/projects/<encoded_cwd>/memory/session-checkpoint.md
```

Where encoded_cwd is the current working directory with `:` `\` `/` replaced by `-` and leading `-` stripped.

Also check:
- Current git branch: `git rev-parse --abbrev-ref HEAD`
- Recent commits: `git log --oneline -10`
- Any open tasks in the session

## Step 2: Generate Handoff Document

Write a structured handoff document to:
```
~/.claude/projects/<encoded_cwd>/memory/session-handoff.md
```

Use this format:

```markdown
---
name: session_handoff
description: "Session handoff — <one-line summary of current task>"
type: project
---
# Session Handoff — <date>

## Starting Prompt

<A ready-to-paste prompt for the next session. This is the PRIMARY artifact.
Include enough context that the next session can immediately resume work.>

## Task State
- **Issue**: #N [title]
- **Branch**: <branch name>
- **Completed**: <commit hashes and what they did>
- **In Progress**: <current step, blockers>
- **Pending**: <remaining items>

## Key Context (compact-loss protection)
- <Architecture decisions that must not be lost>
- <Gotchas or constraints discovered>
- <Important file paths and their roles>

## User Instructions
<If /handoff was called with arguments, place them here.
Otherwise write: "No additional instructions.">
```

## Step 3: Update session_chain (if DB exists)

Run this to update the session chain record:
```bash
python -c "
import os, sqlite3, sys
from pathlib import Path

agentkb_dir = os.environ.get('AGENTKB_DIR')
if not agentkb_dir:
    print('AGENTKB_DIR is not set', file=sys.stderr)
    sys.exit(1)

db = Path(agentkb_dir) / 'stats' / 'skill-usage.db'
if not db.exists():
    print('skill-usage.db not found, skip session_chain update')
    sys.exit(0)

try:
    with sqlite3.connect(str(db)) as c:
        c.execute('''UPDATE session_chain SET handoff_doc=?, status='handoff'
                     WHERE session_id=? AND status IN ('active','complete')''',
                  ('<path to session-handoff.md>', '<session_id>'))
        c.commit()
        print('session_chain updated')
except Exception as e:
    print(f'failed to update session_chain: {e}', file=sys.stderr)
    sys.exit(1)
"
```

## Step 4: Output the Starting Prompt

**CRITICAL**: Output the Starting Prompt section directly in the chat response so the user can copy-paste it as the first message of a new session. This is the primary deliverable — the file is the backup.

## Step 5: Offer Continuation (Optional)

Ask the user if they want to automatically start a new session:
- If yes, run: `uv run --directory "$AGENTKB_DIR" python "$AGENTKB_DIR/scripts/handoff-orchestrator.py" --handoff-doc "<absolute_handoff_path>"`
- If no, the user will manually paste the starting prompt into a new session

Do NOT auto-launch the orchestrator without user confirmation.
</Instructions>
