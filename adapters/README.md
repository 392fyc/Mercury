# Adapters

外部项目适配层。每个 adapter 只做接口转换，不包含业务逻辑。

## 规范

```
adapters/
  {project-name}/
    README.md           # 挂载说明: 挂载了什么、为什么、适配了什么
    adapter.ts 或 .py   # 接口转换代码
    UPSTREAM.md         # 上游版本记录、已知不兼容项
```

## Adapters

| Adapter | Description |
|---------|-------------|
| `mercury-loop-detector/` | PostToolUse hook detecting stall/loop patterns in Claude sessions |
| `mercury-test-gate/` | PreToolUse hook enforcing test passage before destructive writes |
| `mercury-channel-router/` | Telegram bot router (long-lived process per machine); IPC hub for all sessions |
| `mercury-channel-client/` | MCP channel server (one per Claude Code session); bridges session to router |
| `mercury-notify/` | Thin HTTP client for hook scripts to notify via router (fire-and-forget) |
| `gpt-image-2/` | OpenAI gpt-image-2 image generation; mounts wuyoscar/gpt_image_2_skill via uvx-pinned-SHA |
| `playwright-mcp/` | Config-gate wrapper mounting microsoft/playwright-mcp (Apache-2.0) as a pinned MCP server; enforces ADR §4.2 security red lines |

## 约束

- 适配层不超过 200 行。超过说明耦合过深，需重新评估挂载方式。
- 挂载方式分三类受治理的模式（详见 `.mercury/docs/DIRECTION.md` §四"挂载方式（三类并列）"）：
  1. **git submodule（默认）** — 挂载到 `modules/` 目录。
  2. **uvx git+SHA runtime-only** — 经 `uvx --from git+<repo>@<SHA>` 引用，Phase 2 ADR `pixel-animation-workflow` §7.2.1 引入的有限例外（首例：`adapters/gpt-image-2/`）；仍须满足 `CLAUDE.md` §"Cherry-pick protocol"（含 §6 SHA verification）。
  3. **npm-version-pinned MCP server（runtime-only）** — runtime-only 的 MCP server 经 npm 按确切版本挂载（**禁止 `@latest`**），首例 `adapters/playwright-mcp/`（playwright-mcp / Issue #154，ADR `research/issue-154-web-automation-2026-05.md` §5.1）。
- 模式 2、3 均须满足 license gate（仅 permissive）+ provenance（manifest + UPSTREAM.md）+ drift 监控（`scripts/upstream-drift-check.sh`）。
