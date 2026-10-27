# Change log

## Unreleased

- Add an opt-in fixed-peer callback relay that preserves internal queue isolation and reuses public-address, pinned HTTPS and certificate validation.

- Report fixed subscription outcome categories without logging callback URLs, secrets, request identifiers or request bodies.
- Include modern completion and private cache fields so MCP 2026-07-28 clients can decode discovery and tool results.
- Separate request protocol metadata from event subscription arguments while retaining legacy response shapes and recipient authorization.

## 0.1.0 — 2026-10-04

- Add authenticated recipient queues, fixed task metadata, persistent deduplication and explicit consumption.
- Add MCP Events discovery, subscriptions, signed callback verification and bounded delivery retries.
- Keep cloud OAuth connection and actual agent wakeup as separate deployment acceptance requirements.
- Add a credential-file client that can use an existing SSH channel without transferring tokens to the local command line.
