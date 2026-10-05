# Changelog

## Unreleased

- Add a bounded, standard-library local dispatcher for explicitly registered `native-event-probe/1` events (Issue #638).
- Add protected installation policy, direct-human authorization record, body hash checks, SQLite at-most-once claims, completed-turn receipt validation, and consume recovery.
- Bind direct authorization expiry to the registry and body, with an absolute 24-hour lifetime from authorization.
- Require current remote `consumed` status before cached cursor advancement; retry stale `pending`/`sent` deliveries from the verified receipt without another native turn.
- Fail closed on JSONL event or item types outside the explicit controller allowlist.
- Give all receipt properties explicit API schema types, including constant boolean fields.
- Set the Code Mode, tool-only mode, prewarming and host feature flags false; model metadata can still select Code Mode declarations.
- Recognize only the exact pinned disabled-host startup diagnostic before the turn; retain its bytes and reject changed, repeated, late or other error items.
- Preserve the native exit code and JSONL stream when the last-message file is missing; missing or untrusted receipts still fail acceptance.
- Temporarily pin this synthetic receiver to the supported `gpt-6-sol` CLI model and suppress the documented unstable-feature startup warning. Global main-agent and subagent model settings are unchanged.
- Remove inherited Noise execution-server selectors before version checks and native inference, preserving the parent environment and existing authentication.
- Pin native execution to Codex CLI `0.156.1`, reject unreviewed user execution environments and system/project configuration by existence checks, pin project discovery to the worker root, and disable the reviewed shell, MCP/app, browser, image, multi-agent, hook, memory, sleep, artifact, goal, web-search, and experimental user-input features before inference.
- Add fake queue and fake native backend tests; no real model or SSH request is used by the test suite.
