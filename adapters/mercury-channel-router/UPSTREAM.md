# UPSTREAM — mercury-channel-router

## Origin

Original implementation. No code cherry-picked from external projects.

## References (inspiration only, no code copied)

- **Anthropic Channels reference** — MCP notification protocol, reply tool schema, permission relay:
  `https://code.claude.com/docs/en/channels-reference`
- **Anthropic Channels overview** — session lifecycle, multi-session constraints:
  `https://code.claude.com/docs/en/channels`
- **openclaw telegram-claude-poc.py** (seedprod) — routing + session ownership pattern inspiration:
  `https://github.com/seedprod/openclaw-prompts-and-skills/blob/main/telegram-claude-poc.py`
  (no license — no code copied; design pattern only)
- **grammy** — npm package used for Telegram long-polling:
  `https://github.com/grammyjs/grammY` (MIT)
- **node-telegram-bot-api** — previous dependency, replaced by grammy in Issue #302:
  `https://github.com/yagop/node-telegram-bot-api` (MIT)

## Dependencies

- `grammy` ^1.43.0 (MIT) — Telegram Bot API client (Issue #302 — replaces `node-telegram-bot-api@0.67.0` to drop the deprecated `request@2.88.2` transitive stack)
- Node built-ins: `http`, `fs`, `os`, `path`

## License

MIT (Mercury project).
