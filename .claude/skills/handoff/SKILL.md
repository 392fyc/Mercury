---
name: handoff
description: "Generate a structured handoff document + ready-to-paste starting prompt for the next session, preserving context across session boundaries. **Use `/handoff` whenever you reach context limits, need to hand off to a colleague, or want to continue in a fresh session.** Modes: `/handoff` (manual mode, output prompt), `/handoff auto` (auto-launch next session). Includes state summary, acceptance criteria, references — no manual reconstruction needed."
argument-hint: "[auto] [optional extra instructions for the next session]"
user-invocable: true
allowed-tools: Read, Write, Bash, Glob, Grep
---

# /handoff — Session Handoff & Continuation

You are executing the handoff skill. This is the **only** entry point for
handoff — nothing triggers automatically. Follow these steps precisely.

> ## ⚠️ #1 RECURRING FAILURE MODE — READ BEFORE ANYTHING ELSE
>
> In **auto mode** (`/handoff auto`, or any autorun/ralph/ultrawork run that the
> user told to auto-handoff at the end), the single most common bug is: the agent
> **writes the handoff doc + prints the Starting Prompt, then STOPS** — without
> ever invoking the launcher. **That is a FAILED handoff, not a completed one.**
>
> In auto mode, outputting the prompt text is necessary but NOT sufficient. The
> deliverable is a **spawned new session**. Auto mode is NOT complete until you
> have actually run `bash scripts/handoff-launch.sh ...` (Step 5 Auto mode; a
> bash script — on Windows it runs via Git Bash / the Bash tool) and seen it
> **exit 0 with its success report** (currently `spawned new tab`). Printing
> text and ending the turn = the bug the user keeps hitting. A mechanical safety
> net now exists (`.claude/hooks/auto-handoff-stop.sh`, Issue #469): if you ARM
> it first (Step 5-auto.0) and then stop while still armed, it runs the launcher
> for you. But the net only catches you if you armed BEFORE printing the prompt,
> and it does not replace your duty — the launcher call is still YOUR
> responsibility and MUST be the final substantive action of the turn (trivial
> state writes/cleanup may follow, but no further task work).
>
> Self-check before you end an auto-mode turn: "Did I run handoff-launch.sh and
> see it succeed?" If no → you are not done; run it now.

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

Two sources:

1. **Durable memory** — the auto-memory directory holds `MEMORY.md`,
   `LANES.md`, and checkpoints:
   ```
   ~/.claude/projects/<encoded_cwd>/memory/
   ```
   Where `<encoded_cwd>` is the cwd with `:` `\` `/` replaced by `-`, leading
   `-` stripped. Glob for `*.md`. Read checkpoints + project memories.
2. **Previous handoff doc** — resolve via Step 2.0 (`<workspace>/.handoff/` or
   `<kb_dir>/handoff/`) and read the prior `session-handoff*.md` there. Legacy
   sessions may still have it under the memory dir above — read whichever
   exists (prefer the Step 2.0 location).

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

  if [ -z "$OWNER" ]; then
    echo "INFO: could not resolve repo owner via gh — skipping Project query"
  elif [ -n "$PROJ_NUM" ]; then
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

### Step 2.0: Resolve handoff storage location (run once; reused by Step 5)

The handoff doc is **transient working state**, so it lives **with the
workspace/KB — never** under the global `~/.claude/projects/<encoded>/memory/`
dir (that path holds only durable memory: `MEMORY.md`, `LANES.md`, checkpoints).
Resolve the storage dir in this order and reuse `$HANDOFF_PATH` everywhere:

```bash
# Order:
#   1. <workspace>/.handoff-config (gitignored marker) with `kb_dir=<path>`
#      pointing at an existing dir  → HANDOFF_DIR=<kb_dir>/handoff
#   2. otherwise                    → HANDOFF_DIR=<workspace>/.handoff
# Both dirs + .handoff-config itself are gitignored. Never committed.
WORKSPACE="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
KB_DIR=""
if [ -f "$WORKSPACE/.handoff-config" ]; then
  KB_DIR=$(sed -n 's/^[[:space:]]*kb_dir[[:space:]]*=[[:space:]]*//p' \
            "$WORKSPACE/.handoff-config" | head -1)
  # Cleanup order matters: trailing whitespace FIRST, then quotes, then slashes
  # (a trailing space after a closing quote would otherwise defeat quote-strip).
  KB_DIR="${KB_DIR%"${KB_DIR##*[![:space:]]}"}"       # 1) strip trailing whitespace
  KB_DIR="${KB_DIR%\"}"; KB_DIR="${KB_DIR#\"}"        # 2) strip wrapping quotes
  KB_DIR=$(printf '%s' "$KB_DIR" | tr '\134' '/')     # 3) backslash → forward slash (octal \134; bash ${//\\} is unreliable)
fi
if [ -n "$KB_DIR" ] && [ -d "$KB_DIR" ]; then
  HANDOFF_DIR="$KB_DIR/handoff"
else
  HANDOFF_DIR="$WORKSPACE/.handoff"
fi
mkdir -p "$HANDOFF_DIR"

# Filename: main lane = session-handoff.md; named lane = session-handoff-<lane>.md
HANDOFF_PATH="$HANDOFF_DIR/session-handoff.md"   # multi-lane: append -<lane> before .md
```

`.handoff-config` format (one `kb_dir=` line; use **forward slashes** so Git
Bash resolves it; absolute path):
```
kb_dir=D:/ShipOfTheseus/ShipOfTheseus-KB
```
No marker file ⇒ no KB ⇒ handoff falls back to `<workspace>/.handoff/`.

### Step 2.1: Write the document

Write to `$HANDOFF_PATH` (resolved above):

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
mode), verify **all** in-flight work has finished or been explicitly
deferred. In **auto mode**, the handoff is a **terminal event** for the
old session — once the new session is spawned, the old session should
`/exit`. In **manual mode**, producing the handoff prompt is **not**
itself a terminal event; the output is a stable snapshot that the next
session (or the current session continuing) can pick up from. In either
mode, nothing carries over automatically to the next session unless it
goes through the written handoff document (read via the SHORT_PROMPT's
explicit `Read` directive in auto mode, or pasted by the user in manual mode).

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
2. **Save the full handoff document** to `$HANDOFF_PATH` (resolved in Step 2.0
   — `<workspace>/.handoff/` or `<kb_dir>/handoff/`). The next session loads it
   via the explicit `Read` directive in the SHORT_PROMPT (auto mode) or by the
   user pasting the prompt (manual mode) — NOT via auto-memory injection.

### Manual mode (`/handoff` default)

After Step 5.1 + 5.2, **stop**. Tell the user the old session stays alive;
they can copy the prompt to a new session manually or continue working in
this one. Do NOT spawn any new process.

Optional: offer to launch if the user later says so (Step 6).

### Auto mode (`/handoff auto`)

**MANDATORY**: auto mode is only complete once `scripts/handoff-launch.sh` has
actually run and succeeded — exit code 0 plus its success report (currently
`spawned new tab`). Do NOT end the turn after merely printing the prompt —
running the launcher is the whole point of auto mode (see the ⚠️ banner at the
top of this skill). This applies equally to an autorun/ralph/ultrawork run that
was told to auto-handoff on completion: the loop's final substantive act MUST
be the launcher call, not a printed prompt.

#### Step 5-auto.0: ARM the mechanical safety net FIRST (Mercury #469)

There is a Stop hook (`.claude/hooks/auto-handoff-stop.sh`) that mechanically
runs the launcher if you stop while a handoff is *armed*. It is a safety net for
exactly the recurring bug above — but it can only catch you if you **arm it
before you print the prompt**. So, the moment you enter auto mode (right after
Step 2 wrote the doc):

1. Resolve `LANE_NAME`, `WORKTREE_PATH`, `HANDOFF_PATH` — run the resolution
   block in the launch pattern below **now** (it needs no spawn).
2. Write the arm flag (key=value; consumed + deleted by the Stop hook). This
   snippet defines `REPO_ROOT` itself — do NOT rely on it being set by the later
   launch snippet, which runs after this point:

```bash
REPO_ROOT="$(git rev-parse --show-toplevel)"
mkdir -p "$REPO_ROOT/.mercury/state"
{
  printf 'lane=%s\n' "$LANE_NAME"
  printf 'handoff_doc=%s\n' "$HANDOFF_PATH"
  printf 'worktree=%s\n' "$WORKTREE_PATH"
} > "$REPO_ROOT/.mercury/state/auto-handoff-armed"
```

Now even if you stop early, the new session still spawns. The arm flag lives in
`.mercury/state/` (already gitignored) and is cleared on launch. This is the
permanent mechanical fix for Issue #469 — but it does NOT excuse you from
running the launcher yourself: arming is the net, the launcher call below is
still your job (and it disarms atomically on success).

After Step 5.1 + 5.2, and Pre-Termination Checklist passed:

**Required launch pattern — use a SHORT reference prompt, never inline the
full handoff content into the command line.** Inlining multi-line/multi-KB
content into `wt`/`tmux`/shell commands causes catastrophic failures on
Windows (multi-line expansion breaks argument parsing → error 0x80070002,
multiple ghost terminal windows; see `feedback_handoff_short_prompt_only.md`
— S3-side-multi-lane 2026-04-26 forensic record).

The SHORT_PROMPT directs the new session to `Read` the handoff doc
explicitly as its first action. A SessionStart hook (e.g. the
`claude-handoff` plugin's `hooks/session-start.py`) MAY additionally inject
the doc as `additionalContext`, but the prompt MUST NOT assume that
injection happened — plugin install scope may not cover the new session's
cwd (Mercury #359 / claude-handoff #12 forensic record), the plugin may
not be installed, or the runtime may have failed silently. The explicit
`Read` directive guarantees handoff visibility regardless of hook state.

**SHORT_PROMPT contract (Δ11 — Path C lane assertion)**:

The prompt MUST start with a `[LANE=<name>]` marker as its first
whitespace-delimited token. The new session's startup checks (via
`scripts/lane-assertion.sh`) verify three-way alignment between this marker,
the cwd-encoded project state dir, and the current git branch prefix. If
the marker is missing or any pair disagrees, the assertion fails fast and
guides recovery — preventing the share-cwd routing-bleed failure mode
(Issue #342, S13-side-multi-lane forensic record).

```bash
# Resolve the active lane:
#   - main lane handoff file    → lane=main
#   - lane-suffixed handoff file → lane=<suffix> (after first hyphen)
HANDOFF_BASENAME=$(basename "$HANDOFF_PATH" .md)
case "$HANDOFF_BASENAME" in
  session-handoff)             LANE_NAME="main" ;;
  session-handoff-*)           LANE_NAME="${HANDOFF_BASENAME#session-handoff-}" ;;
  *)                           echo "ERROR: unrecognised handoff filename: $HANDOFF_BASENAME" >&2; exit 1 ;;
esac

# Resolve the worktree path from LANES.md (Rule 5.1, Issue #342).
# This is the cwd that wt/tmux must launch the new session at — its
# encoding determines ~/.claude/projects/<encoded>/ project state dir.
LANES_FILE="${MERCURY_MEMORY_DIR:-${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/D--Mercury-Mercury/memory}/LANES.md"
WORKTREE_PATH_RAW=$(awk -v lane="$LANE_NAME" '
BEGIN { in_section=0; in_fence=0 }
/^```/ { in_fence = !in_fence; next }
in_fence { next }
/^### `[^`]+`/ {
  match($0, /^### `[^`]+`/)
  hdr=substr($0, RSTART+5, RLENGTH-6)
  in_section=(hdr == lane) ? 1 : 0
  next
}
in_section && /\*\*Worktree path\*\*/ {
  s=$0
  i=index(s, "**Worktree path**"); s=substr(s, i+length("**Worktree path**"))
  if (substr(s, 1, 2) == " (") { e=index(s, ")"); if (e > 0) s=substr(s, e+1) }
  c=index(s, ":"); if (c == 0) next
  s=substr(s, c+1); sub(/^[[:space:]]+/, "", s)
  if (substr(s, 1, 1) == "`") {
    # Backtick-quoted (canonical) — preserves paths with spaces, but trims
    # trailing whitespace inside the quoted region to defang invisible typos.
    s=substr(s, 2); bt=index(s, "`")
    if (bt > 0) { out=substr(s, 1, bt-1); sub(/[[:space:]]+$/, "", out); if (out != "") print out }
  } else {
    sub(/[[:space:]]+$/, "", s); if (s != "") print s
  }
}
' "$LANES_FILE")
if [ -z "$WORKTREE_PATH_RAW" ]; then
  echo "ERROR: lane '$LANE_NAME' has no Worktree path field in $LANES_FILE" >&2
  echo "       Add it per feedback_lane_protocol.md Rule 5.1 before auto-handoff." >&2
  exit 1
fi
# Reject duplicate Worktree path bullets — first-wins would silently route
# to a stale value.
WORKTREE_COUNT=$(printf '%s\n' "$WORKTREE_PATH_RAW" | grep -c '^.')
if [ "$WORKTREE_COUNT" -gt 1 ]; then
  echo "ERROR: lane '$LANE_NAME' has $WORKTREE_COUNT Worktree path bullets in $LANES_FILE" >&2
  echo "       Edit the lane section to keep exactly one before auto-handoff." >&2
  exit 1
fi
WORKTREE_PATH="$WORKTREE_PATH_RAW"

SHORT_PROMPT="[LANE=${LANE_NAME}] Continue from session handoff. Read ${HANDOFF_PATH} as your first action."
```

`$HANDOFF_PATH` is the location resolved in **Step 2.0** (`<workspace>/.handoff/`
or `<kb_dir>/handoff/`, never the global memory dir). Shell variables do NOT
persist across separate Bash tool calls — if Step 5 runs in a fresh shell,
re-run the Step 2.0 resolution block first so `$HANDOFF_PATH` is set, or inline
the concrete resolved path into the `--handoff-doc` argument below.

**SHORT_PROMPT must remain free of `wt`/`tmux` metacharacters** —
`;` (command separator), `&` (background), `|` (pipe), `\` outside quotes,
`$()` (command substitution). The `[LANE=<name>]` marker only contains
`[a-z0-9-]+` per Rule 2.1 + the literal `[`/`]`/`=` brackets, none of which
are `wt`/`tmux` metacharacters. The lane name is read from LANES.md (which
the protocol governs), so injection via crafted lane names is bounded by
Rule 2.1 + Rule 6 (lane sections only edited by their owning lane).

**Pre-launch alignment smoke check (recommended)**:

If `scripts/lane-assertion.sh` is present in the repo, run it once with the
SHORT_PROMPT as input — it verifies the marker resolves and Worktree path
extraction works before spawning a process you may have to clean up:

```bash
if [ -x scripts/lane-assertion.sh ]; then
  if ! BOOTSTRAP_PROMPT="$SHORT_PROMPT" bash scripts/lane-assertion.sh \
       --cwd "$WORKTREE_PATH" --branch "$(git -C "$WORKTREE_PATH" branch --show-current 2>/dev/null || echo develop)"; then
    echo "ERROR: lane-assertion pre-flight failed — refusing to spawn" >&2
    exit 1
  fi
fi
```

(Soft-disable via `MERCURY_LANE_ASSERT_DISABLED=1` if break-glass.)

**Required**: Do NOT construct the wt/tmux command inline. Call the
canonical launcher:

```bash
REPO_ROOT="$(git rev-parse --show-toplevel)"
bash "$REPO_ROOT/scripts/handoff-launch.sh" \
  --lane "$LANE_NAME" \
  --worktree "$WORKTREE_PATH" \
  --handoff-doc "$HANDOFF_PATH" \
  && rm -f "$REPO_ROOT/.mercury/state/auto-handoff-armed"
```

The `&& rm -f ...auto-handoff-armed` disarms the Step 5-auto.0 safety net
**atomically** in the same command — on launcher success the flag is gone before
you can yield, so the Stop hook sees no flag and does not double-spawn. If the
launcher fails, the flag stays armed and the Stop hook handles the retry.

This script:
- Constructs SHORT_PROMPT canonically with `[LANE=<name>]` marker preserved
- Validates no wt/tmux metacharacters (`;` `&` `|` `$(` `` ` ``) in SHORT_PROMPT
- Invokes wt directly from bash (Windows) or tmux (macOS/Linux) without
  going through `Start-Process` or `ShellExecute` shim

**Do NOT** invoke wt via PowerShell `Start-Process -FilePath "<concatenated string>"` —
Windows Shell will treat the entire commandline string as the executable
path and produce `0x80070002 ERROR_FILE_NOT_FOUND`. Mercury Issue #377
forensic record:
```
[出现错误 2147942402 (0x80070002) (启动"S5 -d D:\Mercury\Mercury-side-bug
-- C:\Users\392fy\.local\bin\claude.exe -- LANE=side-bug Continue from
session handoff. Read ...
```

**ONLY entry point**: `scripts/handoff-launch.sh`. No exceptions. Do not call
`wt`, `tmux`, `Start-Process`, or any direct terminal-spawn primitive from
your agent code under any circumstances. The launcher is the only supported
mechanism for `/handoff auto` and is dual-verify-tested (Mercury Issue #377).

**SHORT_PROMPT metacharacter rationale** (kept here for human readers — the
launcher enforces this automatically): SHORT_PROMPT must remain free of
`;` (command separator), `&` (background), `|` (pipe), `$()` (command
substitution), and `` ` `` (backtick). The launcher does NOT reject `\`:
on Windows, the canonical handoff-doc path contains backslashes
(`C:\Users\...\session-handoff.md`), and bash double-quoting preserves
`\<char>` literally for non-special chars. The `[LANE=<name>]` marker
only contains `[a-z0-9-]+` per Rule 2.1 + the literal `[`/`]`/`=`
brackets, none of which are wt/tmux metacharacters.

The positional argument after `--` is the session's first user message —
documented at <https://code.claude.com/docs/en/cli-reference>. The `--`
sentinel ensures a prompt beginning with `-` is not parsed as a CLI option
(<https://github.com/anthropics/claude-code/issues/3844>) — and the
`[LANE=...]` marker starts with `[` so the sentinel is also defensive
against any future SHORT_PROMPT variants. The `-d "$WORKTREE_PATH"` flag
(wt) / `-c "$WORKTREE_PATH"` flag (tmux) sets the new tab's cwd to the
lane's worktree, so `~/.claude/projects/<encoded>/` resolves to the
lane-specific state dir per Rule 5.1.

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
- The chat-output prompt is required in BOTH modes — never skip it. Completion
  is mode-scoped: in **manual mode** the prompt IS the primary deliverable; in
  **auto mode** the prompt is necessary but completion additionally requires
  the launcher to have run.
- **Auto mode is NOT done until the launcher ran.** `scripts/handoff-launch.sh`
  MUST run and succeed (exit 0 + success report, currently `spawned new tab`)
  as the final substantive action. Ending the turn after only printing text is
  the #1 recurring auto-handoff bug (see top-of-skill banner).
- Do NOT add automatic hooks for SessionEnd or PreCompact — handoff is
  **explicit only**. The mechanical reliability fix is a *Stop* hook
  (`.claude/hooks/auto-handoff-stop.sh`, Issue #469) that fires ONLY when auto
  mode has explicitly armed it (Step 5-auto.0) — it never auto-handoffs an
  un-armed session, so "explicit only" is preserved.
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
- **Δ10/Δ11 (Issue #345)** — auto-mode SHORT_PROMPT MUST start with
  `[LANE=<name>]` marker, and `wt -d` / `tmux -c` MUST be set to the
  lane's `Worktree path` field from `LANES.md` (Rule 5.1). The new
  session's `scripts/lane-assertion.sh` validates three-way alignment
  (marker × cwd-encoded × branch prefix) at startup. Soft-disable via
  `MERCURY_LANE_ASSERT_DISABLED=1`. See
  `.mercury/docs/guides/lane-naming.md` §Lane workspace isolation
  Δ10/Δ11 sub-sections for the full contract.
