# mercury-test-gate

Mechanical `SubagentStop` hook for Mercury. Blocks dev sub-agents from stopping while tests are failing.

## How it works

When a `dev` sub-agent tries to stop, Claude Code fires the `SubagentStop` event. This hook:

1. Checks `agent_type` — only acts on `dev` agents; all others pass through.
2. Handles `stop_hook_active` re-entry — blocks up to 3 consecutive re-attempts per session/agent window, then lets through with an audit log (prevents infinite loops while keeping real enforcement).
3. Resolves the test command — convention file first, then auto-detect.
4. Runs the test command with a configurable timeout.
5. If tests fail or time out — emits `{"decision":"block","reason":"..."}` on stdout + exit 0.
6. If tests pass — emits `{"hookSpecificOutput":{"hookEventName":"SubagentStop","additionalContext":"✓ Mercury test gate: `<cmd>` passed (exit 0)"}}` on stdout + exit 0, surfacing a positive signal to main (non-blocking; never combined with `decision`).
7. If no test command resolves (fail-open) — exits 0 with no output (spec-safe "no opinion"); strict mode (`MERCURY_TEST_GATE_STRICT=1`) blocks instead.

## Setup

The hook is registered automatically via `.claude/settings.json` under `SubagentStop` with matcher `dev`.

No additional installation is required beyond Node.js (already required by Claude Code).

## Convention file

Drop a `.mercury/config/test-gate.yaml` in your project root:

```yaml
test_command: npm run test:ci
```

This overrides auto-detection. The file format is intentionally minimal: one `test_command:` key.

## Auto-detect fallback order

When no convention file is present, the hook probes in this order:

1. `package.json` — `scripts.test` field (if not the default npm placeholder)
2. `pyproject.toml` — presence of `[tool.pytest`
3. `Makefile` — presence of a `test:` target
4. `Cargo.toml` — presence triggers `cargo test`
5. No match → fail-open (warning logged) unless strict mode is on

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `MERCURY_TEST_GATE_TIMEOUT_SEC` | `300` | Kill test command after N seconds |
| `MERCURY_TEST_GATE_STRICT` | unset | Set to `1` to block when no test command resolves |

## Opt-in strict mode

By default the hook **fails open** when no test command is found (warns on stderr, lets stop proceed). This avoids blocking docs-only or config-only projects.

To require a test command:

```
MERCURY_TEST_GATE_STRICT=1
```

Set this in your shell environment or in a project-level `.env` (loaded before Claude Code).

## Disable

Remove or comment out the `SubagentStop` entry in `.claude/settings.json`.

## 为何只 gate dev（设计判定）

`mercury-test-gate` 仅对 `dev` agent 生效，不对 `acceptance`、`critic`、`research` 等角色加阻塞门，原因如下：

- **acceptance / critic**：产出结构化 verdict（APPROVED / NEEDS-REVISION 等），由 Main 直接 dispatch 并以最终消息作为产出。对它们执行 `decision:block` 会阻止 Main 收到 verdict，属于错误行为。其停止行为本身即正确——无需外部检查可运行。
- **research**：产出 research summary，同样由 Main 消费。是否携带 source URL / `[UNVERIFIED]` 标签属于内容质量问题，不能用阻塞门强制（会造成 research agent 无法正常退出）。对应的提醒做成 opt-in nudge（见 `.claude/hooks/research-stop-nudge.sh`，`MERCURY_RESEARCH_STOP_NUDGE=1` 启用），默认关闭以避免噪音。
- **机械门的边界**：`SubagentStop` 阻塞门适合「有外部可执行检查（测试套件）且失败必须阻止停止」的场景。dev agent 满足此条件；其他角色不满足，故不加门。

## Layer model

This hook is orthogonal to OMC's `persistent-mode.cjs`. Both can be registered simultaneously. Claude Code runs all matching hooks; a stop is blocked if any hook returns `decision: "block"`. Mercury's adapter provides the mechanical exit-code check; OMC (if installed) provides the Ralph/UltraQA cycle-counting layer.

## Tests

```
node --test "adapters/mercury-test-gate/test/*.cjs"
```

Uses Node.js built-in `node:test` — no external dependencies.
