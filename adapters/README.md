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

## 约束

- 适配层不超过 200 行。超过说明耦合过深，需重新评估挂载方式。
- 默认通过 git submodule 挂载到 `modules/` 目录（`.mercury/docs/DIRECTION.md` §4）。
- runtime-only 依赖可经 `uvx --from git+<repo>@<SHA>` 引用，作为 Phase 2 ADR `pixel-animation-workflow` §7.2.1 引入的有限例外（首例：`adapters/gpt-image-2/`）；引入时仍须满足 `CLAUDE.md` §"Cherry-pick protocol"（含 §6 SHA verification）。
