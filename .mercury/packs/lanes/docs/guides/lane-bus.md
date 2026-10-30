# Lane bus (prototype, Issue #712)

The lane bus lets the active Claude Code session and the active Codex session of one lane send each other messages
directly, so the user no longer copies reports between the two apps. It is a prototype: the Claude side is verified
end to end on Windows; waking an idle Codex desktop thread is not solved (see Limits).

## Model

- **Seat.** Each lane has one seat per harness (`claude`, `codex`). A session takes the seat with `claim`; the
  session it replaces is retired and can no longer read, send or reclaim. Messages are addressed to the seat, so
  whatever the old session left unread passes to its successor. This is the handoff rule: the session a handoff opens
  claims the seat, the old one retires.
- **Store.** One SQLite file, `<lane home>/lane-bus.sqlite`, where the lane home is `lane-paths.sh memory-dir`
  (ignored by Git, shared by every worktree). `--db` or `MERCURY_LANE_BUS_DB` overrides it.
- **Provenance.** Every delivered message starts with the lane provenance tag. It is a peer report, never user
  authorization (`mercury-lane-contract.md`, "Cross-lane work"; AGENTS.md "Cross-lane messages"). The body follows
  between `=== begin body <nonce> ===` and `=== end body <nonce> ===`, with a nonce drawn when the message is read, so
  a body cannot close its own block or forge the tag and header of another message. `send` refuses a body with
  characters a chat view does not show: controls, format and separator characters, a lone carriage return, and other
  default-ignorable code points such as variation selectors; a single emoji joiner or presentation selector after a
  character that is neither whitespace nor another joiner still passes (Issue #733).

## Commands

Run through `bash .mercury/packs/lanes/scripts/lane-bus.sh` (it picks a Python 3.11+ and passes the arguments on).

**Which lane.** `--lane`, else `$MERCURY_LANE`, else `main`. `main` is only a default: a session learns its lane from
its `[LANE=<id>]` marker or the user, never by inference (`mercury-lane-contract.md`, "Session identity"). Claiming
another lane's seat would retire that lane's session and take its messages. In a project with more than one lane, set
`MERCURY_LANE` for each session or pass `--lane`; when neither is set, the SessionStart hook says that `main` was only
assumed instead of simply suggesting a claim.

| Command | Use |
|---|---|
| `claim --harness H --session ID` | take the seat for this session |
| `send --harness H --session ID --body-file F` | send to the other harness's seat (or the body on stdin) |
| `read --harness H --session ID` | print unread messages and mark them read |
| `status --harness H` | show both seats and unread counts |
| `wait --harness H --session ID --timeout S` | block until a message arrives (default 1800 s, at most 86400): exit 0 message, 3 timeout, 4 not the seat holder |
| `watch --harness claude` | Claude Code plugin monitor (CLI only, see below); ends after `--max-hours` (default 24, at most 72) or when the seat passes on |
| `hook --harness H` | SessionStart / UserPromptSubmit / Stop hook |
| `serve --harness H` | MCP server on stdio (optional; its `lane_bus_wait` is capped at 55 s, below Codex's default 60 s tool timeout) |

Any command exits 2 on bad arguments or a store error, with the reason on stderr; `hook` always exits 0.

The session id is `CLAUDE_CODE_SESSION_ID` in Claude Code and `CODEX_THREAD_ID` in Codex.

## How each side learns of a message

- **Claude Code desktop.** The session runs `lane-bus.sh wait --timeout <seconds>` as a background command. When it
  exits the app wakes the idle session: exit 0 means read and act; exit 3 (timeout) also wakes it and means re-arm or
  stop, not a message. Verified: send to woken reply in about 16 s (Issue #712).
- **Claude Code CLI.** The plugin below also starts a monitor (`watch`) that reports new messages. The desktop app
  does not start plugin monitors.
- **Hooks.** The Claude Code plugin registers them. Codex is not wired up: registering `lane-bus.sh hook --harness
  codex` in Codex is untested, and whether Codex accepts the same output format is unverified. On SessionStart and UserPromptSubmit the hook adds a note with the unread count; on Stop it
  continues the turn once while messages are unread. It speaks only to the seat holder, never marks anything read,
  and lets the session go on after any failure. A SessionStart in a session that does not hold the seat says who
  does and how to claim it.
- **Codex.** The thread reads on its own turns (`read`, or the hook). An idle Codex desktop thread is not woken, so
  until Codex offers a way in, the user wakes it (Issue #731). A `send` to the codex seat, from the CLI or
  `lane_bus_send`, also prints a wake line: the lane marker when the lane was named (`--lane` or `MERCURY_LANE`), the
  message id and the `read` command. Claude shows that line to the user as its own code block, and the user pastes it
  into the Codex thread. The line never carries the body: whatever the user pastes reaches Codex as user input, while
  the body has to stay behind its provenance tag, which marks it as a peer report and not user authorization.

## Claude Code plugin

`packs/lanes/claude-plugin/` is a local marketplace (`mercury-lanes`) with the plugin `mercury-lane-bus` (hooks and
the CLI monitor). Its commands call the project's installed copy, `${CLAUDE_PROJECT_DIR}/.mercury/packs/lanes/
scripts/lane-bus.sh`, on purpose: the lane home is resolved by the project's `lane-paths.sh`, which a plugin copy
would not have. Install for one project and roll back:

```bash
claude plugin marketplace add ./.mercury/packs/lanes/claude-plugin
claude plugin install mercury-lane-bus@mercury-lanes --scope local
claude plugin uninstall mercury-lane-bus@mercury-lanes --scope local
claude plugin marketplace remove mercury-lanes
```

Installing a plugin changes Claude Code configuration; back it up first and record it on the task Issue.

## Windows

The scripts need Git Bash, not WSL. `bash` on PATH is often a WSL launcher (`C:\Windows\System32\bash.exe` or the
`%LOCALAPPDATA%\Microsoft\WindowsApps\bash.exe` alias). The bus uses, in order: `MERCURY_LANE_BUS_BASH`,
`CLAUDE_CODE_GIT_BASH_PATH`, the Git Bash next to `git.exe`, then PATH without those two locations.

## Limits

- Codex idle wake: the Codex desktop app runs its own embedded app-server; starting a turn from a second app-server
  would make two writers on one thread (openai/codex#54683, an open community report on codex-cli 0.154 to 0.162,
  says an outside `codex exec resume` or a second app-server is refused with "already has an active writer"), so
  the prototype does not do it; the wake line above is the stopgap.
- One active session per harness per lane; no group messages.
- The hidden-character check also refuses the ideographic variation selectors (U+E0100 onward, used for some name
  glyphs), the Mongolian free variation selectors (U+180B onward) and emoji flags built from tag characters; such
  text has to be described rather than sent as is. Private-use code points, and unassigned ones outside the
  default-ignorable ranges, pass, so that icon-font glyphs and emoji newer than the sender's Python still go through.
- Seats are not authenticated. Any process of the same user can claim a seat with any session id, or write the
  SQLite store directly; the seat rule keeps honest sessions from crossing, it does not stop a hostile local process.
  The MCP server does not tie `lane_bus_claim` to the host's session id either, because that id is read once when
  the server starts and may no longer match if the host begins a new session without restarting the server.
- Polling interval and wait deadlines follow AGENTS.md "Waiting on long-running work": every `wait` has a timeout.
