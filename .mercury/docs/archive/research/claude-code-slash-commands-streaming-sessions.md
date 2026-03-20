# Research: Claude Code Slash Commands, Streaming Output, and Session Management

> Research date: 2026-03-20
> Scope: Claude Code CLI/SDK slash commands, streaming events, session lifecycle; Codex CLI comparison

---

## 1. Claude Code Slash Commands

### 1.1 Complete Built-in Command List

Source: [Built-in commands - Claude Code Docs](https://code.claude.com/docs/en/commands)

| Command | Purpose |
|---------|---------|
| `/add-dir <path>` | Add a new working directory to the current session |
| `/agents` | Manage agent configurations |
| `/btw <question>` | Ask a side question without adding to conversation |
| `/chrome` | Configure Claude in Chrome settings |
| `/clear` | Clear conversation history and free up context. **Aliases: `/reset`, `/new`** |
| `/color [color\|default]` | Set prompt bar color for current session |
| `/compact [instructions]` | Compact conversation with optional focus instructions |
| `/config` | Open settings interface. Alias: `/settings` |
| `/context` | Visualize current context usage as colored grid |
| `/copy [N]` | Copy last assistant response to clipboard |
| `/cost` | Show token usage statistics |
| `/desktop` | Continue session in Claude Code Desktop app. Alias: `/app` |
| `/diff` | Interactive diff viewer for uncommitted changes |
| `/doctor` | Diagnose installation and settings |
| `/effort [level]` | Set model effort level (low/medium/high/max/auto) |
| `/exit` | Exit CLI. Alias: `/quit` |
| `/export [filename]` | Export conversation as plain text |
| `/extra-usage` | Configure extra usage for rate limits |
| `/fast [on\|off]` | Toggle fast mode |
| `/feedback [report]` | Submit feedback. Alias: `/bug` |
| `/branch [name]` | Branch current conversation. Alias: `/fork` |
| `/help` | Show help and available commands |
| `/hooks` | View hook configurations |
| `/ide` | Manage IDE integrations |
| `/init` | Initialize project with CLAUDE.md |
| `/insights` | Generate session analysis report |
| `/install-github-app` | Set up Claude GitHub Actions |
| `/install-slack-app` | Install Claude Slack app |
| `/keybindings` | Open keybindings config |
| `/login` | Sign in to Anthropic account |
| `/logout` | Sign out |
| `/mcp` | Manage MCP server connections |
| `/memory` | Edit CLAUDE.md memory files |
| `/mobile` | QR code for mobile app. Aliases: `/ios`, `/android` |
| `/model [model]` | Select or change AI model |
| `/passes` | Share free week (eligible accounts only) |
| `/permissions` | View or update permissions. Alias: `/allowed-tools` |
| `/plan` | Enter plan mode |
| `/plugin` | Manage plugins |
| `/pr-comments [PR]` | Fetch GitHub PR comments |
| `/privacy-settings` | View privacy settings (Pro/Max only) |
| `/release-notes` | View changelog |
| `/reload-plugins` | Reload active plugins |
| `/remote-control` | Enable remote control from claude.ai. Alias: `/rc` |
| `/remote-env` | Configure remote environment |
| `/rename [name]` | Rename current session |
| `/resume [session]` | Resume conversation by ID/name. Alias: `/continue` |
| `/review` | Deprecated (use code-review plugin) |
| `/rewind` | Rewind conversation/code. Alias: `/checkpoint` |
| `/sandbox` | Toggle sandbox mode |
| `/security-review` | Analyze pending changes for security vulnerabilities |
| `/skills` | List available skills |
| `/stats` | Visualize daily usage, session history |
| `/status` | Show version, model, account info |
| `/statusline` | Configure status line |
| `/stickers` | Order Claude Code stickers |
| `/tasks` | List and manage background tasks |
| `/terminal-setup` | Configure terminal keybindings |
| `/theme` | Change color theme |
| `/upgrade` | Open upgrade page |
| `/usage` | Show plan usage limits |
| `/vim` | Toggle Vim/Normal editing mode |
| `/voice` | Toggle push-to-talk voice dictation |

### Bundled Skills (appear as slash commands)

| Skill | Purpose |
|-------|---------|
| `/batch <instruction>` | Orchestrate large-scale parallel codebase changes via git worktrees |
| `/claude-api` | Load Claude API reference for your language |
| `/debug [description]` | Troubleshoot current session via debug log |
| `/loop [interval] <prompt>` | Run prompt repeatedly on interval |
| `/simplify [focus]` | Review recently changed files for quality issues |

### 1.2 Key Command Behaviors

**`/new`**: This is an **alias for `/clear`**. It does NOT just start a new session — it clears the current conversation history and frees up context. The previous session data is preserved on disk and can be resumed later via `/resume`.

**`/compact [instructions]`**: Reduces conversation history by summarizing older messages. Accepts optional focus instructions, e.g., `/compact retain the error handling patterns`. Use when context exceeds ~80%.

**`/resume [session]`**: Opens a session picker, or resumes a specific session by ID/name. Only shows sessions from the current working directory.

**`/clear`**: Wipes conversation history from context window. Claude re-reads CLAUDE.md and directory files. Previous session is preserved for future resumption. **`/clear` and `/new` and `/reset` are all the same command.**

There is no standalone `/history` command built-in. History is accessed via `/resume` (session picker) or external tools reading `~/.claude/history.jsonl`.

### 1.3 Slash Commands in the SDK

Source: [Slash Commands in the SDK - Claude API Docs](https://platform.claude.com/docs/en/agent-sdk/slash-commands)

**Sending slash commands programmatically**: Yes. Send them as the `prompt` string in `query()`:

```typescript
// TypeScript
for await (const message of query({
  prompt: "/compact",
  options: { maxTurns: 1 }
})) {
  if (message.type === "result") {
    console.log("Command executed:", message.result);
  }
}
```

```python
# Python
async for message in query(prompt="/compact", options={"max_turns": 1}):
    if message.type == "result":
        print("Command executed:", message.result)
```

**Discovering available commands**: The init system message includes a `slash_commands` field listing all available commands (built-in + custom):

```typescript
if (message.type === "system" && message.subtype === "init") {
  console.log("Available:", message.slash_commands);
  // ["/compact", "/clear", "/help", "/refactor", ...]
}
```

**Intercepting**: Slash commands are intercepted by the SDK before reaching the model. They are NOT sent to Claude as prompts — they trigger internal SDK actions. The SDK returns appropriate message types:
- `/compact` returns `compact_boundary` system message with `compact_metadata.pre_tokens`
- `/clear` returns a new `init` system message with a fresh `session_id`

**Custom slash commands**: Create `.claude/skills/<name>/SKILL.md` (recommended) or `.claude/commands/<name>.md` (legacy). Both are automatically available in the SDK. Support `$ARGUMENTS`, `$0`/`$1`/`$N` placeholders, bash injection via `` !`command` ``, and file references via `@filename`.

---

## 2. Claude Code Streaming / Real-time Output

### 2.1 SDK Streaming Events

Source: [Stream responses in real-time - Claude API Docs](https://platform.claude.com/docs/en/agent-sdk/streaming-output)

**Enable streaming**: Set `includePartialMessages: true` (TS) or `include_partial_messages=True` (Python).

**StreamEvent types** (raw Claude API events wrapped in SDK container):

| Event Type | Description |
|:-----------|:------------|
| `message_start` | Start of a new message |
| `content_block_start` | Start of new content block (text or tool_use) |
| `content_block_delta` | Incremental update to content |
| `content_block_stop` | End of a content block |
| `message_delta` | Message-level updates (stop reason, usage) |
| `message_stop` | End of the message |

**StreamEvent container structure (TypeScript)**:
```typescript
type SDKPartialAssistantMessage = {
  type: "stream_event";
  event: RawMessageStreamEvent; // From Anthropic SDK
  parent_tool_use_id: string | null; // Identifies subagent messages
  uuid: UUID;
  session_id: string;
};
```

### 2.2 What You Can Stream

**Text deltas**: `content_block_delta` where `delta.type === "text_delta"` — gives character-by-character text output.

**Tool use**: Full real-time tool call monitoring:
- `content_block_start` with `content_block.type === "tool_use"` — tool name available
- `content_block_delta` with `delta.type === "input_json_delta"` — tool input JSON streaming
- `content_block_stop` — tool call complete

**Subagent tracking**: `parent_tool_use_id` field on StreamEvent identifies which messages come from which subagent.

### 2.3 Message Flow (with streaming enabled)

```
StreamEvent (message_start)
StreamEvent (content_block_start) - text block
StreamEvent (content_block_delta) - text chunks...
StreamEvent (content_block_stop)
StreamEvent (content_block_start) - tool_use block
StreamEvent (content_block_delta) - tool input chunks...
StreamEvent (content_block_stop)
StreamEvent (message_delta)
StreamEvent (message_stop)
AssistantMessage - complete message with all content
... tool executes ...
... more streaming events for next turn ...
ResultMessage - final result
```

**Without streaming**: You receive `SystemMessage`, `AssistantMessage`, `ResultMessage`, and `CompactBoundaryMessage` — but no `StreamEvent` messages.

### 2.4 Building a Streaming UI (SDK pattern)

```typescript
let inTool = false;

for await (const message of query({
  prompt: "Find all TODO comments",
  options: { includePartialMessages: true, allowedTools: ["Read", "Bash", "Grep"] }
})) {
  if (message.type === "stream_event") {
    const event = message.event;
    if (event.type === "content_block_start") {
      if (event.content_block.type === "tool_use") {
        process.stdout.write(`\n[Using ${event.content_block.name}...]`);
        inTool = true;
      }
    } else if (event.type === "content_block_delta") {
      if (event.delta.type === "text_delta" && !inTool) {
        process.stdout.write(event.delta.text);
      }
    } else if (event.type === "content_block_stop") {
      if (inTool) { console.log(" done"); inTool = false; }
    }
  } else if (message.type === "result") {
    console.log("\n--- Complete ---");
  }
}
```

### 2.5 Known Limitations

- **Extended thinking**: When `maxThinkingTokens` is explicitly set, `StreamEvent` messages are NOT emitted. Only complete messages after each turn. (Thinking is disabled by default, so streaming works unless you enable it.)
- **Structured output**: JSON result appears only in final `ResultMessage.structured_output`, not as streaming deltas.

### 2.6 CLI Real-time Display

The CLI shows real-time activity via:
- Spinner/status indicators during tool execution
- Streaming text as it generates
- Tool call names and progress indicators
- The SDK provides the same level of detail via `StreamEvent` — the CLI is essentially a consumer of these events.

---

## 3. Claude Code Session Lifecycle

### 3.1 `/new` vs `/clear` vs Ending a Session

Source: [Built-in commands - Claude Code Docs](https://code.claude.com/docs/en/commands), [Session management](https://platform.claude.com/docs/en/agent-sdk/sessions)

**`/new` IS `/clear`** — they are aliases for the same command (`/reset` is also an alias). All three:
1. Clear the current conversation history from the context window
2. Preserve the previous session data on disk for future `/resume`
3. Re-read CLAUDE.md and directory context
4. Start a fresh context window

**Ending a session** (closing CLI, `/exit`): Session is saved to disk at `~/.claude/projects/<encoded-cwd>/<session-id>.jsonl`. Can be resumed later.

### 3.2 Multiple Simultaneous Sessions

**Yes, you can have multiple sessions open simultaneously.** Each terminal gets its own session.

- If you `/resume` the SAME session in multiple terminals, both write to the same session file. Messages interleave (like two people writing in the same notebook). Nothing corrupts, but conversation becomes jumbled.
- For parallel work from the same starting point, use `--fork-session` (CLI) or `forkSession: true` (SDK) to give each terminal its own clean session.

### 3.3 Session Storage

Sessions are stored at:
```
~/.claude/projects/<encoded-cwd>/<session-id>.jsonl
```
Where `<encoded-cwd>` is the absolute path with non-alphanumeric characters replaced by `-`.

Global history log: `~/.claude/history.jsonl` — logs every prompt sent across all sessions.

### 3.4 SDK Session Management

Source: [Work with sessions - Claude API Docs](https://platform.claude.com/docs/en/agent-sdk/sessions)

| Approach | When to Use |
|----------|-------------|
| Single `query()` call | One-shot task, no follow-up |
| `continue: true` (TS) / `ClaudeSDKClient` (Python) | Multi-turn chat in one process |
| `continue_conversation=True` | Pick up most recent session after restart |
| `resume: sessionId` | Resume a specific past session |
| `forkSession: true` | Try alternative approach without losing original |
| `persistSession: false` (TS only) | Stateless, no disk writes |

**Capture session ID**:
```typescript
if (message.type === "result") {
  sessionId = message.session_id;
}
```

**Resume by ID**:
```typescript
for await (const message of query({
  prompt: "Continue the analysis",
  options: { resume: sessionId }
}))
```

**Fork**:
```typescript
for await (const message of query({
  prompt: "Try a different approach",
  options: { resume: sessionId, forkSession: true }
}))
```

**List/read sessions programmatically**:
- TypeScript: `listSessions()`, `getSessionMessages()`
- Python: `list_sessions()`, `get_session_messages()`

### 3.5 Session "Archive"

There is no explicit "archive" concept in Claude Code. Sessions are simply saved to disk and can be resumed later. The term "archive" in community usage typically means the session was saved when cleared or closed, and can be resumed via `/resume`.

---

## 4. Codex CLI Slash Commands and Streaming

### 4.1 Codex CLI Slash Commands

Source: [Slash commands in Codex CLI](https://developers.openai.com/codex/cli/slash-commands)

**Session Management:**
| Command | Purpose |
|---------|---------|
| `/clear` | Reset terminal and start fresh conversation |
| `/new` | Begin new chat within same CLI session |
| `/fork` | Branch current conversation into parallel thread |
| `/resume` | Reload saved session from history picker |

**Model & Performance:**
| Command | Purpose |
|---------|---------|
| `/model` | Switch between available models |
| `/fast` | Toggle fast mode (GPT-5.4) |
| `/personality` | Choose communication styles |

**File & Context:**
| Command | Purpose |
|---------|---------|
| `/mention` | Attach files/folders to conversation |
| `/diff` | Show git changes |
| `/review` | Request working tree analysis |
| `/compact` | Summarize conversation to preserve context tokens |
| `/copy` | Copy latest response to clipboard |

**Advanced:**
| Command | Purpose |
|---------|---------|
| `/plan` | Enter plan mode |
| `/agent` | Switch active subagent thread |
| `/apps` | Browse and insert connectors |
| `/mcp` | List MCP tools |
| `/experimental` | Toggle optional features |
| `/sandbox-add-read-dir` | Grant Windows sandbox directory access |

**System:**
| Command | Purpose |
|---------|---------|
| `/init` | Generate AGENTS.md scaffold |
| `/ps` | Monitor background terminals |
| `/feedback` | Submit logs/diagnostics |
| `/logout` | Clear credentials |
| `/quit` or `/exit` | Close CLI |
| `/permissions` | Adjust approval policies |
| `/status` | Display model, approval mode, token usage |
| `/debug-config` | Print config diagnostics |
| `/statusline` | Customize footer |

### 4.2 Codex SDK Streaming Events

Source: [Codex SDK](https://developers.openai.com/codex/sdk), [openai/codex GitHub](https://github.com/openai/codex/blob/main/sdk/typescript/README.md)

**SDK package**: `@openai/codex-sdk` (TypeScript, Node.js 18+)

**Basic usage**:
```typescript
import { Codex } from "@openai/codex-sdk";
const codex = new Codex();
const thread = codex.startThread();
const result = await thread.run("Your prompt here");
```

**Streaming with `runStreamed()`**:
```typescript
const { events } = await thread.runStreamed("Diagnose the test failure");

for await (const event of events) {
  switch (event.type) {
    case "item.completed":
      console.log("item", event.item);
      break;
    case "turn.completed":
      console.log("usage", event.usage);
      break;
  }
}
```

**Event types**:
| Event Type | Description |
|:-----------|:------------|
| `item.completed` | Individual item (tool call, response chunk) finished |
| `turn.completed` | Entire conversational turn finished, includes usage metrics |

**Session resume**:
```typescript
const thread = codex.resumeThread(threadId);
```

**Non-interactive execution**: `codex exec` (alias `codex e`) runs non-interactively, streaming results to stdout or JSONL.

### 4.3 Comparison: Claude Code vs Codex Streaming

| Feature | Claude Code SDK | Codex SDK |
|---------|----------------|-----------|
| Package | `@anthropic-ai/claude-agent-sdk` | `@openai/codex-sdk` |
| Streaming method | `includePartialMessages: true` on `query()` | `thread.runStreamed()` |
| Event granularity | Token-level (`content_block_delta`) | Item-level (`item.completed`) |
| Tool call streaming | Yes (input JSON streams character-by-character) | Yes (item.completed with tool call items) |
| Text streaming | Yes (text_delta events) | Yes (via item.completed) |
| Thinking/reasoning | Not streamable when extended thinking enabled | Not documented |
| Session resume | `resume: sessionId` | `codex.resumeThread(threadId)` |
| Session fork | `forkSession: true` | `/fork` in CLI, not documented in SDK |
| Subagent tracking | `parent_tool_use_id` field | `/agent` command in CLI |

---

## 5. Key Findings for GUI Integration

### 5.1 Streaming is Fully Supported

The Claude Agent SDK provides granular streaming via `StreamEvent`. A GUI can show:
- Real-time text generation (character-by-character)
- Tool call names as they start
- Tool input JSON as it streams
- Tool completion status
- Subagent activity via `parent_tool_use_id`

### 5.2 Slash Commands are SDK-native

Slash commands can be sent programmatically via `prompt: "/command"`. The SDK intercepts them before the model. A GUI can:
- Discover available commands from the init message's `slash_commands` field
- Send `/compact` when context is high (check via `/context`)
- Send `/clear` to reset (aliases: `/new`, `/reset`)
- Create custom commands via `.claude/skills/` or `.claude/commands/`

### 5.3 Session Management is Comprehensive

The SDK provides:
- `resume` for specific session resumption
- `continue` for most-recent session
- `forkSession` for branching
- `listSessions()` / `getSessionMessages()` for building session pickers
- `persistSession: false` for stateless operation (TS only)
- Session IDs available from `ResultMessage.session_id` and init `SystemMessage.session_id`

### 5.4 No "Thinking" Stream When Extended Thinking Enabled

Critical limitation: when `maxThinkingTokens` is set, StreamEvent messages are not emitted. The GUI must choose between extended thinking and real-time streaming.

---

## Sources

- [Built-in commands - Claude Code Docs](https://code.claude.com/docs/en/commands)
- [Skills / Slash Commands - Claude Code Docs](https://code.claude.com/docs/en/slash-commands)
- [Stream responses in real-time - Claude API Docs](https://platform.claude.com/docs/en/agent-sdk/streaming-output)
- [Agent SDK overview - Claude API Docs](https://platform.claude.com/docs/en/agent-sdk/overview)
- [Slash Commands in the SDK - Claude API Docs](https://platform.claude.com/docs/en/agent-sdk/slash-commands)
- [Work with sessions - Claude API Docs](https://platform.claude.com/docs/en/agent-sdk/sessions)
- [Codex CLI Slash Commands](https://developers.openai.com/codex/cli/slash-commands)
- [Codex SDK](https://developers.openai.com/codex/sdk)
- [Codex SDK TypeScript README](https://github.com/openai/codex/blob/main/sdk/typescript/README.md)
- [Claude Code Session Management - Steve Kinney](https://stevekinney.com/courses/ai-development/claude-code-session-management)
- [Claude Code Conversation History - kentgigger](https://kentgigger.com/posts/claude-code-conversation-history)
- [GitHub Issue #32871: /clear behavior](https://github.com/anthropics/claude-code/issues/32871)
