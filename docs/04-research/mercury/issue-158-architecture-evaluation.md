# Mercury Architecture Direction Evaluation

## Related Documentation

- [Mercury Architecture Guide](../guides/architecture.md) — current architecture design
- [SoT Workflow Guide](../guides/sot-workflow.md) — task state machine reference

## Research Questions

- [x] Q1: Claude Code native agent capabilities (2025-2026)
- [x] Q2: Mercury component native coverage analysis
- [x] Q3: Path A (modular extraction) vs Path B (in-place evolution)
- [x] Q4: Codex adaptation layer continued value

---

## Q1: Claude Code Native Agent Capabilities (2025-2026)

### Sub-agents (Agent Tool)

Claude Code has a mature sub-agent system. Sub-agents run in their own context windows with custom system prompts, specific tool access, and independent permissions. They preserve main context by offloading exploration work. [Source: https://code.claude.com/docs/en/sub-agents]

Key capabilities:
- **Built-in sub-agents**: Explore, Plan, and general-purpose agents ship by default
- **Custom sub-agents**: Users define them via `.claude/agents/*.md` files with YAML frontmatter (name, description, tools allowlist, model selection)
- **Model routing**: Sub-agents can target different models (e.g., Haiku for cheaper tasks) via the `model` frontmatter field
- **Tool restrictions**: Each sub-agent can have an explicit `tools` allowlist, limiting what it can access
- **Scope levels**: Project-level (`.claude/agents/`), user-level (`~/.claude/agents/`), plugin-level, and CLI-defined (`--agents`)

Limitations:
- Sub-agents report results only to the caller -- no peer-to-peer communication
- No persistent state between invocations (each call is a fresh context)
- Results are summarized back into the caller's context window

### Agent Teams (Multi-Session Orchestration)

Agent Teams is an experimental feature (since Claude Code v2.1.32, February 2026) that coordinates 2-16 Claude Code sessions. [Source: https://code.claude.com/docs/en/agent-teams]

Key capabilities:
- **Team lead / teammate model**: One session acts as lead, spawning and coordinating teammates
- **Shared task list**: Tasks persist in `~/.claude/tasks/{team-name}/`, with pending/in_progress/completed states and dependency chains
- **Direct peer messaging**: Teammates communicate directly via a mailbox system -- no bottleneck through the lead
- **Self-claiming**: Teammates can autonomously claim unassigned, unblocked tasks
- **Plan approval**: Teammates can be required to plan before implementing; lead reviews and approves/rejects
- **Hook integration**: `TeammateIdle`, `TaskCreated`, `TaskCompleted` hooks for quality gates
- **Sub-agent definitions as teammate roles**: A sub-agent definition can be reused as a teammate type
- **Display modes**: In-process (single terminal) or split-pane (tmux/iTerm2)

Limitations (documented as experimental):
- **No session resumption** for in-process teammates (`/resume` does not restore them)
- **Task status can lag** -- teammates sometimes fail to mark tasks completed
- **One team per session**, no nested teams
- **Lead is fixed** -- cannot transfer leadership
- **3-7x more tokens** than single session
- **No Windows split-pane support** (tmux/iTerm2 only)
- Shutdown can be slow

### Native Task Management Tools

Claude Code 2.1 (January 2026) introduced four built-in task tools: [Source: https://github.com/Piebald-AI/claude-code-system-prompts/blob/main/system-prompts/tool-description-taskcreate.md]

- **TaskCreate**: Create tasks with subject, description, dependencies, metadata
- **TaskUpdate**: Update status, add blockedBy/blocks dependencies
- **TaskGet**: Retrieve full task details
- **TaskList**: List all tasks with current status

Tasks support dependency chains (blockedBy/blocks), multi-session coordination via shared task files, and real-time status updates. However, they are currently disabled in the VSCode extension due to a TTY check. [Source: https://github.com/anthropics/claude-code/issues/23874]

### Headless Mode / Agent SDK / API

Claude Code offers comprehensive programmatic access via CLI and SDK: [Source: https://code.claude.com/docs/en/headless] [Source: https://platform.claude.com/docs/en/agent-sdk/overview]

**CLI mode** (`claude -p`):
- Non-interactive execution with `--output-format` (text, json, stream-json)
- `--bare` mode skips all auto-discovery for deterministic CI/CD runs
- `--json-schema` for structured outputs
- `--continue` and `--resume <session_id>` for conversation continuity
- `--append-system-prompt`, `--system-prompt` for custom system prompts

**Agent SDK** (Python `claude-agent-sdk` v0.1.48, TypeScript `@anthropic-ai/claude-agent-sdk` v0.2.71 as of March 2026):
- Same tools, agent loop, and context management as Claude Code, as a library
- **Built-in tools**: Read, Write, Edit, Bash, Glob, Grep, WebSearch, WebFetch, AskUserQuestion
- **Hooks as callbacks**: PreToolUse, PostToolUse, Stop, SessionStart, SessionEnd -- programmatic, not just shell scripts
- **Sub-agents**: Define custom agents with specialized instructions, tool restrictions, and model routing programmatically
- **MCP integration**: Connect MCP servers programmatically via config objects
- **Session management**: Capture session IDs, resume sessions with full context, fork sessions
- **Permission modes**: Standard, acceptEdits, planning (read-only), bypassPermissions
- **Authentication**: Anthropic API key, Amazon Bedrock, Google Vertex AI, Microsoft Azure AI Foundry

This is highly relevant for Mercury's architecture because the Agent SDK could serve as Mercury's orchestrator runtime -- replacing the custom Node.js sidecar with SDK-based programmatic control of Claude Code sessions.

### Scheduled Tasks and Remote Control

Additional automation features (March 2026): [Source: https://claudefa.st/blog/guide/development/scheduled-tasks]

- **Scheduled Tasks**: Save a prompt to run on a recurring cadence (hourly, daily, weekly); each run fires a fresh session with full tool/MCP/skill access
- **Remote Control** (research preview, February 2026): Bridge local Claude Code terminal session with claude.ai/code, iOS/Android apps; session keeps running locally while controlled remotely [Source: https://simonwillison.net/2026/Feb/25/claude-code-remote-control/]
- **/loop**: Run a prompt or slash command on a recurring interval within a session

### Dispatch (Async Task Queue)

Launched March 17, 2026 as a research preview inside Claude Cowork: [Source: https://www.mindstudio.ai/blog/what-is-claude-code-dispatch]

- Asynchronous task execution -- submit a task, get results later
- Remote triggering from phone/browser via Cowork
- **Channels**: Persistent bidirectional communication pathways for ongoing interaction
- Shifts Claude Code from synchronous assistant to asynchronous task worker

### Skills System

Skills are folders of instructions, scripts, and resources that Claude loads dynamically: [Source: https://code.claude.com/docs/en/skills]

- Defined via `SKILL.md` files with YAML frontmatter
- Lazy-loaded: Claude only sees name/description until activated
- Can bundle scripts in any language
- Distributable via plugins or managed settings
- Trigger via `/` commands or automatic detection

### Channels (Bidirectional External Communication)

Launched March 20, 2026 as a research preview (requires v2.1.80+). [Source: https://code.claude.com/docs/en/channels-reference]

A Channel is an MCP server that pushes events into a Claude Code session:
- **One-way channels**: Forward alerts, webhooks, monitoring events for Claude to act on
- **Two-way channels**: Expose a reply tool so Claude can send messages back (chat bridges)
- **Permission relay**: Forward tool approval prompts to remote channels (approve/deny from phone)
- **Supported platforms**: Telegram, Discord, iMessage (official); custom channels via MCP SDK
- **Architecture**: Runs as subprocess communicating over stdio; can listen on local HTTP ports for webhooks
- **Plugin packaging**: Channels can be wrapped as plugins for distribution via marketplaces

This is significant for Mercury because Channels provide a native mechanism for bidirectional communication between Claude Code sessions and external systems -- a capability Mercury currently implements via its MCP orchestrator server.

### Plugin System

Claude Code's plugin system (public beta since v1.0.33) allows bundling skills, agents, MCP servers, hooks, and commands into distributable packages: [Source: https://www.morphllm.com/claude-code-plugins]

- **Component composition**: A single plugin can bundle slash commands, subagents, MCP servers, hooks, and LSP servers
- **Distribution**: Via plugin marketplaces; install with `/plugin install`
- **Ecosystem scale**: 9,000+ plugins as of early 2026 [Source: https://medium.com/@alexanderekb/claude-code-plugins-are-confusing-heres-a-quick-start-overview-of-what-s-actually-inside-bb0c2ad1e199]
- **No build step**: Simple directory-based architecture

This means Mercury's unique value could potentially be packaged as a Claude Code plugin for broader distribution.

### Hooks System

Claude Code has a comprehensive lifecycle hook system with 20+ event types: [Source: https://code.claude.com/docs/en/hooks-guide]

- **Event types**: SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, PermissionRequest, Stop, SubagentStart/Stop, TaskCreated/Completed, TeammateIdle, ConfigChange, CwdChanged, FileChanged, WorktreeCreate/Remove, PreCompact/PostCompact, and more
- **Handler types**: Command (shell), Prompt (LLM evaluation), Agent (multi-turn subagent verification), HTTP (webhook)
- **Decision control**: Hooks can allow, deny, or ask for tool calls; inject context; block edits; auto-approve permissions
- **Scope levels**: User-global, project, local, managed policy, plugin, skill/agent frontmatter
- **Matchers**: Regex patterns filtering by tool name, session source, error type, etc.
- **`if` field**: Permission-rule syntax for argument-level filtering (e.g., `Bash(git *)`)

---

## Q2: Mercury Component -- Native Coverage Analysis

> **Scoring methodology**: Coverage percentages are qualitative estimates based on feature parity — the proportion of Mercury's use cases that can be replicated using Claude Code native or ecosystem tools without custom code. They are not calculated from code metrics. "Native" means built into Claude Code; "ecosystem" includes community MCP servers, plugins, and third-party tools. When both are given, the higher figure represents the combined ceiling if ecosystem tools are adopted.

### 1. MCP Orchestrator Server (Session Management, Task Dispatch, Approval Flow)

**Mercury provides**: A Node.js JSON-RPC 2.0 orchestrator sidecar with: session lifecycle management (start/stop/resume/delete), task dispatch with role-based routing, approval flow (approve/deny requests), agent registry, callback queues, notification broadcasting, and skill injection.

**Claude Code native coverage**:
- **Session management**: Agent Teams + SDK provide multi-session coordination. Dispatch provides async task submission. `--resume` provides session continuity. However, there is no centralized session registry equivalent to Mercury's `list_sessions` / `delete_session` / `resume_session` API.
- **Task dispatch**: TaskCreate/TaskUpdate/TaskList/TaskGet provide task orchestration. Agent Teams provide role-based delegation. However, Mercury's state machine (drafted -> dispatched -> in_progress -> implementation_done -> main_review -> acceptance -> verified -> closed) is far more granular than Claude Code's simple pending/in_progress/completed model.
- **Approval flow**: Agent Teams have plan approval (lead reviews teammate plans). Hooks have `PermissionRequest` events. However, there is no equivalent to Mercury's structured `approve_request` / `deny_request` / `list_approval_requests` API.

**Coverage verdict**: ~40% covered. Claude Code provides building blocks (tasks, teams, hooks) but lacks Mercury's structured state machine, centralized session registry, and formal approval workflow.

### 2. Custom Hooks (Pre-commit, Web-research Gates, Push Guards)

**Mercury provides** (14 hook scripts in `.claude/hooks/`):
- `pre-commit-guard.sh` -- pre-commit quality gate
- `push-guard.sh` -- push safety
- `pr-create-guard.sh` / `pr-merge-guard.sh` -- PR lifecycle guards
- `web-research-gate.sh` / `web-research-extended-gate.sh` -- web research enforcement
- `scope-guard.sh` -- role boundary enforcement
- `stop-guard.sh` -- stop condition verification
- `session-init.sh` -- session initialization
- `post-commit-reset.sh` / `post-review-flag.sh` / `post-web-research-flag.sh` -- post-action state management
- `user-prompt-submit.sh` -- prompt preprocessing

**Claude Code native coverage**: Claude Code's hooks system (20+ event types, 4 handler types) is **more capable** than Mercury's hook infrastructure. Mercury's hooks run as shell scripts triggered by the orchestrator; Claude Code's hooks are natively integrated into the agent lifecycle with richer event types, structured JSON I/O, matcher/filter support, and prompt/agent handler types.

**Coverage verdict**: ~90% covered architecturally. The hook *infrastructure* is natively superior. The specific *business logic* in Mercury's hooks (SoT workflow guards, dual-verify gates, web-research enforcement) would need to be reimplemented as Claude Code hook scripts, but the mechanism is fully there.

### 3. Obsidian KB Integration (Persistent Knowledge Base)

**Mercury provides**: `knowledge-service.ts` with kb_read/kb_write/kb_append/kb_search/kb_list operations against the Obsidian vault via MCP.

**Claude Code native coverage**: Claude Code has no built-in Obsidian integration. However, the MCP ecosystem provides multiple Obsidian MCP servers (mcp-obsidian, mcpvault, obsidian-claude-code-mcp) that offer equivalent or better functionality. [Source: https://github.com/MarkusPfundstein/mcp-obsidian] These are community-maintained and can be configured in `.mcp.json`.

**Coverage verdict**: ~70% covered via ecosystem. The MCP servers provide raw vault access. Mercury's value-add is the *structured* KB interface (specific paths like `Mercury_KB/04-research/`, template-driven writes, handoff documents) -- this is domain logic, not infrastructure.

### 4. Codex Adaptation Layer (Cross-Model Dispatch)

**Mercury provides**: `codex-mcp-adapter.ts` + `codex-mcp-transport.ts` wrapping the Codex MCP server for cross-model task delegation and code review.

**Claude Code native coverage**: OpenAI released `codex-plugin-cc` (March 2026, 11.6k GitHub stars) -- an official first-party plugin that provides `/codex:review`, `/codex:adversarial-review`, `/codex:rescue` (task delegation), `/codex:status`, `/codex:result`, and `/codex:cancel` commands. [Source: https://github.com/openai/codex-plugin-cc] This is functionally equivalent to Mercury's Codex adapter but maintained by OpenAI themselves.

**Coverage verdict**: ~85% covered. The official plugin provides review and delegation. Mercury's adapter adds tight integration with the SoT workflow (dispatching tasks to Codex as a role-assigned agent), which the plugin does not replicate.

### 5. GUI (Tauri-based Session Viewer)

**Mercury provides**: A Tauri 2 + Vue 3 desktop application for session management, message viewing, task tracking, and agent configuration.

**Claude Code native coverage**: Claude Code has no GUI. It is a terminal-first tool. Agent Teams has in-process and split-pane display modes but no graphical interface. Claude Cowork (web-based) provides some session management but is Anthropic's hosted product, not self-hostable.

**Additional context**: Multiple third-party Tauri-based GUIs for Claude Code now exist:
- **opcode** (winfunc): Tauri 2 GUI for managing sessions, creating custom agents, tracking usage [Source: https://github.com/winfunc/opcode]
- **claude-code-gui** (5Gears0Chill): Tauri 2 + React + TypeScript with integrated terminal, project management, usage analytics, real-time session monitoring [Source: https://github.com/5Gears0Chill/claude-code-gui]
- **Claudia**: Commercial GUI managing sessions in `~/.claude/projects/` with smart search and metadata [Source: https://claudia.so/]
- **OpenCovibe**: Tauri 2 + Svelte 5, bidirectional stream-JSON protocol, per-run session actors [Source: https://github.com/AnyiWang/OpenCovibe]

**Claude Cowork** (Anthropic's own desktop product) provides session management, cross-device Dispatch, session filtering (Active/Archived, Local/Cloud), context management, and computer use. However, it is Anthropic's hosted product, not self-hostable. [Source: https://code.claude.com/docs/en/desktop]

**Coverage verdict**: ~10% covered natively (Cowork exists but is not self-hostable). ~40% covered if considering the third-party GUI ecosystem. Mercury's GUI differentiator is its tight integration with the SoT workflow, role-based dispatch, and approval flow -- features no third-party GUI replicates.

### 6. SoT Task Workflow (State Machine for Tasks)

**Mercury provides**: A 10-state task state machine (drafted -> dispatched -> in_progress -> implementation_done -> main_review -> acceptance -> verified -> closed + blocked + failed) with formal transitions, role-scoped dispatch prompts from templates, rework history tracking, implementation receipts, acceptance bundles, scope violation tracking, and KB persistence.

**Claude Code native coverage**: TaskCreate/TaskUpdate provide basic pending/in_progress/completed states with dependency chains. Agent Teams add coordination. But there is no equivalent to Mercury's formal state machine, review/acceptance stages, rework cycles, or structured receipt flow.

**Third-party workflow plugins**: The plugin ecosystem includes projects like `claude-code-workflows` (shinpr) with specialized agents running in phases (Analyze -> Design -> Plan -> Implement -> Verify) and multi-agent workflow orchestrators. [Source: https://github.com/shinpr/claude-code-workflows] These are closer to Mercury's SoT concept but are not standardized and lack Mercury's formal state machine, receipt flow, and KB persistence.

**Coverage verdict**: ~20% covered natively. ~35% if counting third-party workflow plugins. Claude Code's task tools are a simple checklist/kanban; Mercury's SoT is a formal workflow engine with review/acceptance/rework stages. This is one of Mercury's strongest unique values.

### 7. Role-Based Agent Dispatch (Dev/Research/Acceptance Agents)

**Mercury provides**: 5 roles (main, dev, acceptance, research, design) defined in YAML with structured metadata (canExecuteCode, canDelegateToRoles, inputBoundary, outputBoundary). `role-loader.ts` loads definitions at runtime. `role-prompt-builder.ts` generates role-scoped system prompts. The orchestrator enforces role boundaries via `scope-guard.sh`.

**Claude Code native coverage**: Sub-agent definitions can specify tools, model, and system prompts -- functionally similar to role definitions. Agent Teams can use sub-agent definitions as teammate types. However, there is no *enforcement* mechanism for role boundaries (what a role *cannot* do), no structured `canDelegateToRoles` / `inputBoundary` / `outputBoundary` metadata, and no formal role hierarchy.

**Coverage verdict**: ~45% covered. Sub-agents provide the dispatch mechanism; the role *policy* layer (boundaries, delegation rules, structured metadata) is Mercury-specific.

### Summary Table

| Component | Native Coverage | Mercury Unique Value |
|-----------|:-:|---|
| MCP Orchestrator | ~40% | State machine, session registry, approval API |
| Custom Hooks | ~90% | Business logic only (infra is native) |
| Obsidian KB | ~70% | Structured KB schema, domain templates |
| Codex Adapter | ~85% | SoT-integrated dispatch |
| GUI | ~10% | SoT-integrated session management |
| SoT Workflow | ~20% | Formal state machine, review/acceptance stages |
| Role-Based Dispatch | ~45% | Role policy enforcement, boundaries |

---

## Q3: Path A vs Path B Analysis

### Path A -- Modular Extraction

**Concept**: Extract Mercury's unique value into a standalone project that works WITH Claude Code rather than wrapping it. Mercury becomes a plugin/skill/hook collection + optional GUI, not an orchestration layer.

**What gets kept**:
1. **SoT Workflow Engine** -- extracted as a Claude Code plugin or skill that uses hooks (TaskCreated, TaskCompleted, Stop) to enforce the state machine
2. **Role Definitions** -- converted to Claude Code sub-agent definitions (`.claude/agents/`) with accompanying enforcement hooks
3. **GUI** -- standalone Tauri app that reads Claude Code's session/task state files (`~/.claude/tasks/`, `~/.claude/teams/`) instead of running its own orchestrator
4. **KB Integration** -- retained as MCP server configuration (pointing to Obsidian MCP servers) with Mercury-specific skill files for structured KB operations
5. **Workflow Hooks** -- migrated to Claude Code's native hook format in `.claude/settings.json`

**What gets deprecated**:
1. **MCP Orchestrator Server** -- replaced by Claude Code's native Agent Teams + SDK
2. **SDK Adapters** (claude-adapter.ts, gemini-adapter.ts, opencode-adapter.ts) -- no longer needed; Claude Code is the runtime
3. **Codex MCP Adapter** -- replaced by `codex-plugin-cc`
4. **RPC Transport** -- eliminated; Claude Code's native communication handles this
5. **Session Persistence** -- Claude Code manages its own sessions

**Migration effort**: HIGH (3-5 weeks estimated)
- Rewrite SoT state machine as hooks + skill (1-2 weeks)
- Convert role definitions to sub-agent format (2-3 days)
- Rewrite GUI to read Claude Code's native state files (1-2 weeks)
- Migrate all hook scripts to Claude Code hook format (2-3 days)
- Integration testing of the new composition (3-5 days)

**Risks**:
- Claude Code's internal file formats (`~/.claude/tasks/`, `~/.claude/teams/config.json`) are not documented as stable APIs -- the GUI would be fragile. **Mitigation**: use Agent SDK session management APIs (capture/resume/fork) instead of reading internal files directly; these are a supported public contract.
- Agent Teams is still experimental with known limitations (no session resume, task status lag)
- Loss of centralized control -- Mercury's orchestrator is a single point of coordination; in Path A, coordination is distributed across hooks/skills/teams

**Benefits**:
- Rides Claude Code's update cycle instead of fighting it
- Dramatically simpler codebase (no orchestrator, no adapters, no transport)
- Can adopt new Claude Code features (Dispatch, Channels) immediately
- Lower maintenance burden long-term
- **Agent SDK as orchestrator runtime**: The Python/TypeScript Agent SDK could replace Mercury's Node.js sidecar entirely -- using `query()` with hooks, sub-agents, MCP servers, and session management programmatically. The SoT state machine could be implemented as SDK hook callbacks rather than shell scripts. [Source: https://platform.claude.com/docs/en/agent-sdk/overview]

### Path B -- In-Place Evolution

**Concept**: Keep the current repo structure but progressively replace Mercury components with Claude Code native features where coverage is sufficient.

**Phase 1 (immediate)**:
- Replace Codex MCP adapter with `codex-plugin-cc` (drop `codex-mcp-adapter.ts` + `codex-mcp-transport.ts`)
- Migrate hook scripts to use Claude Code's native hook format (more event types, structured JSON I/O)
- Add Claude Code sub-agent definitions alongside Mercury role YAMLs for interop

**Phase 2 (when Agent Teams stabilizes)**:
- Optionally delegate multi-agent coordination to Agent Teams for parallelizable work
- Keep Mercury orchestrator as the "control plane" for the SoT state machine
- Mercury orchestrator becomes a thin layer: task state machine + approval flow + session registry, delegating execution to Claude Code's native tools

**Phase 3 (long-term)**:
- Evaluate GUI strategy: integrate with Dispatch/Channels for real-time session monitoring
- Assess whether SoT state machine can be implemented purely as Claude Code hooks
- Consider extracting Mercury's unique value as a Claude Code plugin for distribution

**What gets kept**: Everything, progressively thinned
**What gets deprecated**: Components as their native replacements mature

**Migration effort**: LOW per phase (1-2 weeks per phase, spread over months)

**Risks**:
- **Divergence**: Mercury's orchestrator may fall out of sync with Claude Code's evolving architecture, creating maintenance debt
- **Dual-path complexity**: Running both Mercury's orchestrator AND Claude Code's native features creates confusion about which system is authoritative
- **Stalled migration**: Without pressure, Phase 2/3 may never happen, leaving Mercury as permanent tech debt

**Benefits**:
- No big-bang migration risk
- Continuous delivery of value
- Time to evaluate which Claude Code features actually stabilize vs remain experimental
- Preserves all existing workflow investment

### Comparison Matrix

| Dimension | Path A (Extract) | Path B (Evolve) |
|-----------|:-:|:-:|
| Migration risk | Higher (big-bang) | Lower (incremental) |
| Long-term maintenance | Lower | Higher |
| Claude Code alignment | Strong | Moderate |
| Feature adoption speed | Fast | Slow |
| Unique value preservation | Moderate | Strong |
| Codebase complexity | Much simpler | Stays complex |
| Time to first value | 3-5 weeks | 1-2 weeks |
| Risk of stalling | Low (forced completion) | High |

### Ecosystem Context: Other Harness Projects

Several open-source projects face similar architectural decisions, providing useful reference points:

- **Claw Code**: Clean-room rewrite of Claude Code's harness architecture in Rust/Python; reimplements tool system, query engine, multi-agent orchestration [Source: https://claw-code.codes/]
- **OpenHarness (HKUDS)**: Agent loop with 43 tools, compatible with claude-code plugins [Source: https://github.com/HKUDS/OpenHarness]
- **Everything Claude Code (ECC)**: Performance optimization system with skills, instincts, memory across multiple agents [Source: https://github.com/affaan-m/everything-claude-code]
- **OpenClaw migration**: Article documenting migration from OpenClaw to native Claude Code features (Channels, Remote Control, Scheduled Tasks), noting that "Claude Code now offers a more reliable, secure, and cost-effective alternative" [Source: https://medium.com/@jiten.p.oswal/the-native-migration-why-claude-code-is-the-openclaw-replacement-youve-been-waiting-for-7d8076c318d2]

The trend across the ecosystem is clear: projects that wrapped Claude Code as a black box are migrating toward plugin/skill/hook composition patterns that work WITH Claude Code's native architecture rather than around it.

### Agent Teams Real-World Data

Anthropic's own engineering team used Agent Teams to build a C compiler: 16 agents produced a 100,000-line Rust-based C compiler capable of compiling the Linux kernel, over nearly 2,000 Claude Code sessions at $20,000 in API costs. [Source: https://www.anthropic.com/engineering/building-c-compiler]

For more typical workloads, real-world reports indicate:
- Content generation: 16 agents generated a full week of platform-specific social media content in 15 minutes for $7.80
- Daily development: Teams of 3-5 agents handle parallel document research, code writing, quality review, and deployment
- Token cost: 3-7x multiplier over single-session work, which is acceptable for parallelizable tasks but wasteful for sequential workflows

### Recommendation Framework

**Choose Path A if**:
- You want Mercury to be a lightweight enhancement layer, not a parallel orchestration system
- You are willing to accept that Agent Teams (experimental) may have rough edges for 3-6 months
- You want to minimize long-term maintenance burden
- The GUI can tolerate reading Claude Code's internal state files (or you are willing to build a small API adapter)

**Choose Path B if**:
- The SoT workflow (10-state machine, formal acceptance, rework tracking) is mission-critical and cannot tolerate any regression
- You need the GUI to remain fully functional during migration
- You want to de-risk by waiting for Agent Teams to exit experimental status
- You prefer gradual change with continuous delivery

**Hybrid approach (recommended consideration)**:
Start with Path B Phase 1 (replace Codex adapter + migrate hooks -- low-risk, high-value). Then evaluate Path A vs Path B Phase 2 based on Agent Teams stability at that point. This gives immediate value while preserving optionality.

### Migration Safeguards

**Preconditions** (before any migration begins):
- Snapshot current develop branch as a tagged baseline (`v0.x-pre-migration`)
- Assess impact on open issues (particularly #101 harness roadmap)
- Agent Teams minimum stability: session resume working, task status reliable

**Rollback plan**:
- Path B Phase 1: revert `codex-plugin-cc` → restore `codex-mcp-adapter.ts` (code is git-tracked, zero data loss)
- Path A: if Claude Code internal file formats break GUI, fall back to Mercury orchestrator as API layer
- Trigger: any regression in SoT workflow correctness or >2x token cost increase

**Acceptance criteria**:
- SoT 10-state machine: all transitions functional with equivalent or better latency
- Approval flow: approve/deny cycle completes without manual intervention
- Token cost: within 1.5x of current baseline for equivalent workloads
- Skills/hooks: all existing skills operational after migration

---

## Q4: Codex Adaptation Layer Assessment

### Current Mercury Implementation

Mercury provides two files for Codex integration:
- `codex-mcp-adapter.ts`: Wraps the Codex MCP server, providing task delegation and code review via the Codex CLI
- `codex-mcp-transport.ts`: Transport layer for MCP communication with Codex

These integrate Codex as a first-class agent within Mercury's SoT workflow -- tasks can be dispatched to Codex with role-scoped prompts, and results flow back through the orchestrator.

### OpenAI's Official Plugin (codex-plugin-cc)

OpenAI released `codex-plugin-cc` on March 31, 2026 (v1.0.2, 11.6k GitHub stars). [Source: https://github.com/openai/codex-plugin-cc]

It provides:
- **/codex:review**: Standard code review on uncommitted changes or branch comparisons
- **/codex:adversarial-review**: Adversarial review questioning implementation decisions
- **/codex:rescue**: Task delegation to Codex with model/effort parameters
- **/codex:status** / **/codex:result** / **/codex:cancel**: Background job management
- **/codex:setup**: Installation verification and optional review gate configuration

Installation: `plugin marketplace add openai/codex-plugin-cc` then `plugin install codex@openai-codex`

### Value Comparison

| Capability | Mercury Adapter | codex-plugin-cc |
|-----------|:-:|:-:|
| Code review | Yes | Yes (+ adversarial mode) |
| Task delegation | Yes (SoT-integrated) | Yes (/rescue) |
| Background execution | No | Yes (/status, /cancel) |
| SoT workflow integration | Yes (receipts, state machine) | No |
| Maintained by | Mercury project | OpenAI (official) |
| Update cadence | Manual | Plugin marketplace |

### What Would Be Lost

Removing Mercury's Codex adapter would lose:
1. **SoT-integrated dispatch**: Tasks dispatched to Codex currently flow through Mercury's state machine (drafted -> dispatched -> in_progress -> etc.). The plugin does not participate in this flow.
2. **Role-scoped prompts**: Mercury injects role definitions and dispatch templates when delegating to Codex. The plugin uses its own prompt structure.
3. **Centralized receipt flow**: Mercury expects implementation receipts from Codex that feed into the acceptance stage. The plugin returns results but not in Mercury's receipt format.

### Does Claude Code Have Native Cross-Model Dispatch?

No. Claude Code does not have built-in cross-model dispatch to non-Anthropic models. The sub-agent `model` field only selects among Anthropic models (Opus, Sonnet, Haiku). Cross-model dispatch is achieved through:
1. **MCP servers**: Any model accessible via MCP can be called as a tool (this is how Mercury's adapter works)
2. **Plugins**: The `codex-plugin-cc` is the primary example of cross-model dispatch via the plugin system
3. **Community tools**: `openclaude` enables Claude Code to use OpenAI, Gemini, DeepSeek, Ollama, and 200+ models via an OpenAI-compatible API shim [Source: https://github.com/mjohnnywest/openclaude]

### Recommendation

**Replace Mercury's Codex adapter with `codex-plugin-cc`** for review and ad-hoc delegation. The official plugin is better maintained, has more features (adversarial review, background jobs), and will track Codex API changes automatically.

**However**, if SoT-integrated Codex dispatch is needed (tasks that go through the full state machine), a thin adapter layer would still be required to bridge the plugin's output into Mercury's receipt format. This could be implemented as a Claude Code hook (PostToolUse on `mcp__codex__*` tools) rather than a full adapter.

### Codex Migration Plan

1. **Migration sequence**: Install `codex-plugin-cc` alongside existing adapter. Run both in parallel for 1-2 sessions to verify feature parity. Remove `codex-mcp-adapter.ts` and `codex-mcp-transport.ts` after validation.
2. **SoT bridge**: Implement a PostToolUse hook on `mcp__codex__*` that converts plugin output to Mercury receipt format. Hook handles format conversion only; state transitions remain in the orchestrator.
3. **Rollback trigger**: If `/codex:rescue` lacks `blockedBy`/`blocks` semantics needed for SoT dispatch, revert to Mercury adapter until plugin support is added.
4. **Removal criteria**: Mercury adapter files removed only after 3+ successful sessions using plugin exclusively.

---

## Sources

- [Claude Code Sub-agents Documentation](https://code.claude.com/docs/en/sub-agents)
- [Claude Code Agent Teams Documentation](https://code.claude.com/docs/en/agent-teams)
- [Claude Code Headless/SDK Documentation](https://code.claude.com/docs/en/headless)
- [Claude Code Hooks Guide](https://code.claude.com/docs/en/hooks-guide)
- [Claude Code Skills Documentation](https://code.claude.com/docs/en/skills)
- [Claude Code Q1 2026 Update Roundup](https://www.mindstudio.ai/blog/claude-code-q1-2026-update-roundup)
- [Claude Code Dispatch Explained](https://www.mindstudio.ai/blog/what-is-claude-code-dispatch)
- [OpenAI codex-plugin-cc](https://github.com/openai/codex-plugin-cc)
- [TaskCreate System Prompt](https://github.com/Piebald-AI/claude-code-system-prompts/blob/main/system-prompts/tool-description-taskcreate.md)
- [Claude Code Agent Teams Guide (claudefa.st)](https://claudefa.st/blog/guide/agents/agent-teams)
- [VSCode TaskCreate TTY Issue](https://github.com/anthropics/claude-code/issues/23874)
- [Obsidian MCP Server (mcp-obsidian)](https://github.com/MarkusPfundstein/mcp-obsidian)
- [openclaude Multi-Model Shim](https://github.com/mjohnnywest/openclaude)
- [Claude Code Releases (GitHub)](https://github.com/anthropics/claude-code/releases)
- [Claude Agent SDK Overview](https://platform.claude.com/docs/en/agent-sdk/overview)
- [Claude Agent SDK TypeScript](https://github.com/anthropics/claude-agent-sdk-typescript)
- [Claude Agent SDK Python](https://github.com/anthropics/claude-agent-sdk-python)
- [Claude Code Channels Reference](https://code.claude.com/docs/en/channels-reference)
- [Claude Code Scheduled Tasks Guide](https://claudefa.st/blog/guide/development/scheduled-tasks)
- [Claude Code Remote Control](https://simonwillison.net/2026/Feb/25/claude-code-remote-control/)
- [Claude Code Plugins Guide (Morph)](https://www.morphllm.com/claude-code-plugins)
- [Anthropic: Building a C Compiler with Agent Teams](https://www.anthropic.com/engineering/building-c-compiler)
- [Claw Code (Open-Source Harness)](https://claw-code.codes/)
- [opcode GUI (winfunc)](https://github.com/winfunc/opcode)
- [claude-code-gui (5Gears0Chill)](https://github.com/5Gears0Chill/claude-code-gui)
- [Claudia GUI](https://claudia.so/)
- [OpenCovibe GUI](https://github.com/AnyiWang/OpenCovibe)
- [Claude Code Desktop / Cowork](https://code.claude.com/docs/en/desktop)
- [OpenClaw to Claude Code Migration](https://medium.com/@jiten.p.oswal/the-native-migration-why-claude-code-is-the-openclaw-replacement-youve-been-waiting-for-7d8076c318d2)
