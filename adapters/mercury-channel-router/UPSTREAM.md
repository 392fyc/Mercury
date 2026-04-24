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
- **node-telegram-bot-api** — npm package used for Telegram long-polling:
  `https://github.com/yagop/node-telegram-bot-api` (MIT)

## Dependencies

- `node-telegram-bot-api` ^0.67.0 (MIT) — Telegram Bot API client
- Node built-ins: `http`, `fs`, `os`, `path`

## License

MIT (Mercury project).
