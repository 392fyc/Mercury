# UPSTREAM — mercury-channel-client

## Origin

Original implementation. No code cherry-picked from external projects.

## References (inspiration only, no code copied)

- **Anthropic Channels reference** — MCP notification protocol, reply tool schema, permission relay,
  `notifications/claude/channel` format:
  `https://code.claude.com/docs/en/channels-reference`
- **Anthropic Channels walkthrough** — session lifecycle, `--dangerously-load-development-channels` flag:
  `https://code.claude.com/docs/en/channels`
- **@modelcontextprotocol/sdk** — MCP server scaffolding:
  `https://github.com/modelcontextprotocol/typescript-sdk` (MIT)

## Dependencies

- `@modelcontextprotocol/sdk` ^1.29.0 (MIT) — MCP server + stdio transport
- Node built-ins: `child_process`, `path`

## License

MIT (Mercury project).
