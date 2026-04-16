---
name: handoff
description: Generate a structured handoff document and ready-to-paste starting prompt for the next session. Use `/handoff` for manual mode (output only). Use `/handoff auto` to auto-launch the new session via `claude` CLI after the document is written.
argument-hint: "[auto] [optional extra instructions for the next session]"
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
| `/handoff` (no args) | **manual** | Write doc + output starting prompt in chat. Do NOT launch a new session. Old session stays alive by user choice. |
| `/handoff <instructions>` | **manual + extra** | Same as manual; put `<instructions>` into the "User Instructions" section of the handoff doc. |
| `/handoff auto` | **auto** | Write doc + output starting prompt + **auto-launch** new session via `claude` CLI after Pre-Termination Checklist passes. Old session should `/exit` after — auto mode treats the old session as a terminal event. |
| `/handoff auto <instructions>` | **auto + extra** | Same as auto, with extra instructions embedded. |

Default (no explicit `auto`): manual mode. Never auto-launch without an
explicit `auto` token as the first whitespace-delimited argument.

**Note on `/handoff:auto`**: Claude Code's colon syntax (`<x>:<y>`) is
reserved for plugin namespacing (e.g. `/plugin-name:skill-name`). A
project-level skill registered as `handoff` only resolves via `/handoff`,
and `/handoff:auto` is NOT a valid slash invocation for this skill — the
parser will treat it as an unknown command. Always use `/handoff auto`
(space-delimited).

**Strict parsing**: `auto` must be the **sole** argument or the first
whitespace-delimited token (i.e., `/handoff auto <extra>` is auto+extra,
`/handoff automatic` is **manual** with instructions "automatic"). This
prevents accidental auto-spawn from user instructions that happen to
start with "auto".

**Terminal-event semantics**: "handoff is a terminal event for the old
session" only applies to **auto mode** — the old session is expected to
`/exit` immediately after spawning the new one. In **manual mode** the
skill does NOT terminate the session; the user decides whether to `/exit`,
paste the prompt into a fresh session elsewhere, or keep working. Both are
valid; the skill itself writes the doc and outputs the prompt, nothing
else.

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

### Layer 5: GitHub Project / Issues (best-effort — skip cleanly if unavailable)

If the project uses GitHub Issues + a GitHub Project (v2), query for next-task selection. This layer is **best-effort**: if `gh` is missing, unauthenticated, or the repo has no Project, skip this layer and fall back to Layers 1–4 + `.mercury/docs/EXECUTION-PLAN.md` (or the repo's equivalent plan). Never let a Layer 5 failure block the handoff.

```bash
# Pre-flight: bail out gracefully if gh is unavailable or unauthenticated.
if ! command -v gh >/dev/null 2>&1 || ! gh auth status >/dev/null 2>&1; then
  echo "INFO: gh CLI unavailable — skipping Layer 5"
else
  gh issue list --label "P0" --state open --json number,title,labels --limit 50 2>/dev/null || true
  gh issue list --label "P1" --state open --json number,title,labels --limit 50 2>/dev/null || true

  # Project number: configurable via $HANDOFF_PROJECT_NUM. No per-repo
  # auto-fallback — the skill stays agnostic about which GitHub Project
  # belongs to which repo. Callers that want Project integration (e.g.
  # Mercury with Project #3) set the env var in their shell profile or
  # per-session before invoking /handoff.
  OWNER=$(gh repo view --json owner --jq '.owner.login' 2>/dev/null)
  PROJ_NUM="${HANDOFF_PROJECT_NUM:-}"

  if [ -n "$PROJ_NUM" ]; then
    gh project item-list "$PROJ_NUM" --owner "$OWNER" --format json --limit 100 2>/dev/null | \
      python -c "
import json, sys
try:
    data = json.loads(sys.stdin.read() or '{}')
except json.JSONDecodeError:
    sys.exit(0)  # gh returned empty/invalid — silently skip
items = [i for i in data.get('items', []) if i.get('status') in ('Todo', 'In Progress')]
status_order = {'In Progress': 0, 'Todo': 1}
for i in sorted(items, key=lambda x: (status_order.get(x.get('status', ''), 9), x.get('priority', 'P9'))):
    num = i.get('content', {}).get('number', '?')
    print(f'#{num} [{i.get(\"priority\",\"?\")}] {i.get(\"title\",\"?\")} ({i.get(\"status\",\"?\")})')
" 2>/dev/null || true
  else
    echo "INFO: HANDOFF_PROJECT_NUM not set — skipping Project query (set it to enable)"
  fi
fi
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

### Auto mode (`/handoff auto`)

After Step 5.1 + 5.2, and Pre-Termination Checklist passed:

**Required launch pattern — write prompt to a locked-down temp file, then
pass via `claude "$(cat ...)"` as a single positional argument.** This
matches the canonical `claude-handoff` plugin shape
(<https://github.com/392fyc/claude-handoff>) which has been validated
end-to-end on Windows (wt + PowerShell profile). Do NOT insert a `--`
sentinel between `claude` and the prompt — on Windows, `wt` spawns
`claude.cmd` (npm shim) directly via CreateProcess; cmd.exe's batch
re-parser can mis-handle the extra `--`, causing the new tab to open
with no running process. Handoff prompts always start with literal text
(e.g. `这是 S{N+1}`), never with `-`, so the `--` sentinel is unnecessary.

**Prerequisites**: auto-mode launch assumes a POSIX-like shell (`mktemp`,
`chmod`, `cat`, heredoc). On Windows this means **Git Bash / MSYS2 / WSL**
(which Mercury uses) — native `cmd.exe` and non-interactive PowerShell do
**not** satisfy this. If the host shell is not POSIX-compatible, fall back
to manual mode or use the PowerShell equivalent shown further below.

**Command-line length**: `claude` receives the prompt as a single positional
argument, which is subject to OS argv limits (Windows `CreateProcess` ≈
32 KB, Linux/macOS 128 KB+). Typical handoff prompts (~1–3 KB) are well
under any limit. For abnormally large prompts (> 30 KB on Windows), pipe
the file to `claude` via stdin or split the prompt into a separate
`--input-file`-style mechanism (check `claude --help` on the installed
version; stdin support is documented at
<https://code.claude.com/docs/en/cli-reference>).

```bash
# Step A (POSIX shells — Git Bash, WSL, macOS, Linux):
# write the verbatim prompt to a locked-down temp file.
TMP=$(mktemp) && chmod 600 "$TMP" && cat > "$TMP" <<'PROMPT_EOF'
<STARTING_PROMPT_VERBATIM>
PROMPT_EOF
```

```powershell
# Step A (PowerShell on Windows, if no Git Bash):
$TMP = [System.IO.Path]::GetTempFileName()
Set-Content -LiteralPath $TMP -Value @'
<STARTING_PROMPT_VERBATIM>
'@
# PowerShell $TMP permissions default to user-only on NTFS.
```

**Windows** (Windows Terminal, new tab — `wt` opens a real new tab):
```bash
wt -w 0 nt --title "Handoff" -- claude "$(cat "$TMP")"
```

**macOS / Linux with tmux** (real new window, detached from current TTY):
```bash
tmux new-window -n handoff "claude \"\$(cat $TMP)\""
```

**macOS / Linux without tmux** — there is no portable "new terminal"
primitive. `claude "..." &` only backgrounds the process in the **current
shell**; it will inherit the current TTY and die when the shell exits or
the tab is closed. If you need real isolation, use `tmux new-session -d`
or the terminal emulator's own CLI (e.g. `osascript -e 'tell app
"Terminal" to do script ...'` on macOS, `gnome-terminal --` on Linux).
Otherwise fall back to manual mode and let the user open the new session
themselves.

```bash
# Detached tmux session (survives current shell exit):
tmux new-session -d -s handoff "claude \"\$(cat $TMP)\""
# Then `tmux attach -t handoff` from the user's preferred terminal.
```

The positional argument to `claude` is the session's first user message —
documented at <https://code.claude.com/docs/en/cli-reference>. The
SessionStart hook will inject the full handoff document as
`additionalContext`, so the new session has everything.

**Cleanup (always run, regardless of branch taken)**:

```bash
# POSIX: run after launch succeeds; use trap for failure paths.
trap 'rm -f "$TMP"' EXIT
```

```powershell
# PowerShell: run after launch; use try/finally for failure paths.
try { <launch command> } finally { Remove-Item -LiteralPath $TMP -Force -ErrorAction SilentlyContinue }
```

This guarantees the handoff prompt (which may reference internal paths
or planning context) never persists on disk past the launch moment.

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
- **Mode-scoped termination**: auto mode treats handoff as a terminal
  event for the old session (spawn new → /exit old). Manual mode does
  NOT terminate; the user decides. Never apply auto-mode termination to
  a manual invocation.
- Before terminating (auto mode) verify all pending work has completed.
  Nothing carries over automatically.
- Manual mode MUST NOT spawn processes. Only the `auto` token (as the
  first whitespace-delimited argument to `/handoff`) triggers the `claude`
  CLI launch.
- The legacy `$AGENTKB_DIR/scripts/handoff-orchestrator.py` path is
  DEPRECATED. Do not invoke it. The `claude-handoff` plugin is the
  canonical session-continuity module
  (<https://github.com/392fyc/claude-handoff>).
