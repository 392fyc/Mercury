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

**MANDATORY: Query GitHub Project and Issues for next task determination**

Run the following to identify the highest-priority actionable next task:
```bash
# 1. Get P0/P1 open issues
gh issue list --label "P1" --state open --json number,title,labels --limit 10
# Add P0 label check too if applicable

# 2. Get Todo/In-Progress items from GitHub Project #3
gh project item-list 3 --owner <repo-owner> --format json --limit 30 | \
  python -c "import json,sys; data=json.load(sys.stdin); \
  items=[i for i in data['items'] if i.get('status') in ('Todo','In Progress')]; \
  [print(i.get('priority','?'), i.get('title','?'), i.get('status','?')) for i in sorted(items, key=lambda x: x.get('priority','P9'))]"
```

Use this data to determine: **what is the single most actionable next task?**
Selection criteria (in order):
1. Actively blocked P1 bugs with known root cause
2. In-Progress items from the Project board
3. Highest-priority P0 Todo from Project board
4. Next Phase sub-item per EXECUTION-PLAN.md

Do NOT produce a menu. Pick one primary task and one secondary task (fallback after primary completes).

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

这是 S{N+1}。以下是 S{N} 的完整交接。

### 当前状态
<repo/branch/commit状态，clean/dirty>

### S{N+1} 主任务：<Issue #N — 具体任务标题>

**背景**：<1-2句说明为什么这是最高优先级，来自Issue/Project数据>

**执行步骤**：
1. <具体可执行步骤，包含文件路径和命令>
2. <具体可执行步骤>
3. <验证方法>
4. <提交/PR步骤>

**次要任务（主任务完成后）**：<Issue #N 或 Phase X-Y，一句话描述>

### 参考文档
<仅列出主任务相关的文档>

## Task State
- **Issue**: #N [title] (status)
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

**CRITICAL RULE for Starting Prompt**: The prompt MUST contain a single primary task with numbered execution steps. It MUST NOT be a bulleted list of options. The next session agent should be able to start executing step 1 immediately without asking for direction.

## Step 3: Update session_chain (if DB exists)

Run this to update the session chain record:
```bash
python -c "
import sqlite3, json
from pathlib import Path
db = Path('$AGENTKB_DIR/stats/skill-usage.db')
if db.exists():
    with sqlite3.connect(str(db)) as c:
        c.execute('''UPDATE session_chain SET handoff_doc=?, status='handoff'
                     WHERE session_id=? AND status IN ('active','complete')''',
                  ('<path to session-handoff.md>', '<session_id>'))
        c.commit()
        print('session_chain updated')
"
```

## Step 4: Output the Starting Prompt

**CRITICAL**: Output the Starting Prompt section directly in the chat response so the user can copy-paste it as the first message of a new session. This is the primary deliverable — the file is the backup.

## Step 5: Offer Continuation (Optional)

Ask the user if they want to automatically start a new session:
- If yes, run: `uv run --directory $AGENTKB_DIR python $AGENTKB_DIR/scripts/handoff-orchestrator.py --handoff-doc <path>`
- If no, the user will manually paste the starting prompt into a new session

Do NOT auto-launch the orchestrator without user confirmation.
</Instructions>
