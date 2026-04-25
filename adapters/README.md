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

## 约束

- 适配层不超过 200 行。超过说明耦合过深，需重新评估挂载方式。
- 外部项目通过 git submodule 挂载到 `modules/` 目录。
- 详见 `.mercury/docs/DIRECTION.md` 第四章。
