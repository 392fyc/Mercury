# Mercury Project Rules

## MUST

- **Commit at every checkpoint**: Every major milestone MUST be committed and pushed to the remote repository to maintain clear progress history.
- **Code review before commit**: Each milestone MUST be code-reviewed BEFORE committing, not after. Quality gates are enforced pre-commit.
- **Research from live sources**: All research MUST be based on actual web queries and verified sources, not training data. The agent ecosystem evolves rapidly.
- **Main Agent is user-configurable**: The Main Agent MUST be user-configurable via UI/config. Any agent (Claude Code, Codex, opencode, Gemini CLI, etc.) can be assigned as Main Agent.
- **Install to D drive**: Install software to `D:\Program Files`, not C drive, when possible.
- **Obsidian CLI for framework management**: Obsidian CLI is enabled by default for project-level task/doc management. Each project gets a `{Project}_KB` vault alongside its source folder. Agents retain their own MCP/mem0/knowledge architecture independently — only Orchestrator/TaskManager uses KB.
- **Agents First**: Inter-agent communication uses JSON/YAML (machine-readable, minimal ambiguity). All agent interactions MUST include agentId, model, and sessionId. Task files persist as JSON in KB for dashboard visualization.
- **Use Chinese for milestone summaries**: Return phase/milestone completion messages in Chinese.

## DO NOT

- DO NOT hardcode any specific agent as the Main Agent.
- DO NOT make agent adapters depend on Obsidian/KB — agents keep their own MCP/SDK architecture. Only Orchestrator-level code uses KB.
- DO NOT commit without running code review first.
- DO NOT guess or assume SDK/CLI APIs from training data — verify via web search or actual source code.
- DO NOT install software to the C drive when D drive is available.
- DO NOT interfere with agent-level architecture, MCP connections, or mem0 configurations — Mercury is a CLI-to-GUI wrapper, not an API platform.

## Architecture

Mercury is a **CLI-to-GUI wrapper** for multi-agent collaboration:
- Tauri 2 (Rust) + Vue 3 frontend
- Node.js sidecar orchestrator (JSON-RPC 2.0 over stdio)
- SDK adapters wrap existing CLIs (Claude Code, Codex, opencode)
- SoT (Ship of Theseus) task orchestration pattern
- Flow: Vue → Tauri Rust → Node.js Orchestrator → SDK Adapters → Agent CLIs
