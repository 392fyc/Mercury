# Session, Role, History, And Approval V2 Decisions

Date: 2026-03-17

## Scope

This decision record captures the confirmed design for Mercury V2 session recovery, history browsing, role invariants, prompt persistence, and approval semantics.

## Confirmed Invariants

- A Mercury session is bound to `role + agent + sessionId`.
- `role` is immutable for the lifetime of a session.
- `resume` means restoring the original session, not rebinding history into the current panel.
- Legacy sessions without frozen role metadata are audit-visible but not resumable.

## Frozen Prompt Model

### Claude

- Claude SDK resumes conversation history, but `systemPrompt` is still a per-call option.
- Therefore Mercury must freeze the full prompt text at session creation time.
- Every subsequent Claude request, including resumed sessions, must pass the same frozen prompt via `systemPrompt`.
- Prompt changes in agent settings do not migrate old sessions.
- If the current computed prompt differs from the frozen session prompt, the UI should show an informational legacy marker only.

### Codex

- Codex does not expose a Claude-equivalent per-session `systemPrompt`.
- Codex session role context is therefore a weak-equivalent semantic:
  - freeze role prompt text in session metadata
  - prepend it to every `turn/start` prompt
  - keep `AGENTS.md` or `model_instructions_file` for project-level baseline only
- This difference is a protocol fact, not a Mercury defect.
- The UI must state the distinction explicitly:
  - Claude: system-level prompt
  - Codex: turn-level prepend

## History And Resume Model

- `SessionPicker` is for resumable sessions only.
- `SessionPicker` lists only sessions matching the current `role + agent`.
- `HistoryPanel` is read-only and shows all roles for the current agent.
- `overflow` and `completed` sessions do not appear in resumable lists.
- `overflow` and legacy sessions remain visible in read-only history.

## API Semantics

### `resume_session`

- `resume_session` must no longer accept `role` as a setter.
- It may accept `expectedRole` only as a validation hint.
- Orchestrator always restores `frozenRole` from session metadata.
- If `expectedRole` is provided and does not match `frozenRole`, the request must fail before adapter resume.

### `list_sessions`

- Keep a single API and add filter arguments:
  - `agentId?`
  - `role?`
  - `includeTerminal?`
- `SessionPicker` uses `list_sessions(agentId, role, false)`.
- `HistoryPanel` uses `list_sessions(agentId, undefined, true)`.

## Mercury Transcript Source Of Truth

- Mercury currently has no backend-owned transcript source.
- V2 establishes `.mercury/transcripts/{sessionId}.jsonl` as the baseline message store.
- Messages are appended immediately, one JSONL line per message:
  - user prompt before adapter call
  - assistant or system messages as they arrive from the adapter
- Frontend localStorage becomes a cache only.
- V2 HistoryPanel uses Mercury transcript only.
- Native adapter history is deferred to later enhancement work.

## Approval Semantics

Two approval concepts are intentionally separated:

- `SoT Main Review`
  - owned by Mercury Orchestrator
  - post-hoc review of implementation receipts or task outputs
  - does not intercept tool execution

- `Tool-level Approval`
  - owned by the underlying agent runtime
  - intercepts concrete file changes, command execution, or similar runtime actions

## Codex Runtime Direction

- Current `@openai/codex-sdk` integration does not provide the required per-item approval flow.
- Codex V2 should therefore move to `codex app-server` over stdio.
- This is a transport and integration change, not a small wrapper tweak.
- Mercury remains responsible for approval routing and UI.
- App-server becomes the runtime protocol for Codex tool-level approvals and improved history/resume support.

## Deferred Work

- Native history detail rendering for Claude/Codex/opencode
- Research on `model_instructions_file`
- Any attempt to auto-migrate old sessions to new prompt contracts
