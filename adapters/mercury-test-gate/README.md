# mercury-test-gate

Mechanical `SubagentStop` hook for Mercury. Blocks dev sub-agents from stopping while tests are failing.

## How it works

When a `dev` sub-agent tries to stop, Claude Code fires the `SubagentStop` event. This hook:

1. Checks `agent_type` — only acts on `dev` agents; all others pass through.
2. Handles `stop_hook_active` re-entry — blocks up to 3 consecutive re-attempts per session/agent window, then lets through with an audit log (prevents infinite loops while keeping real enforcement).
3. Resolves the test command — convention file first, then auto-detect.
4. Runs the test command with a configurable timeout.
5. If tests fail or time out — emits `{"decision":"block","reason":"..."}` on stdout + exit 0.
6. If tests pass — exits 0 with no output (spec-safe "no opinion").

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

## Layer model

This hook is orthogonal to OMC's `persistent-mode.cjs`. Both can be registered simultaneously. Claude Code runs all matching hooks; a stop is blocked if any hook returns `decision: "block"`. Mercury's adapter provides the mechanical exit-code check; OMC (if installed) provides the Ralph/UltraQA cycle-counting layer.

## Tests

```
node --test "adapters/mercury-test-gate/test/*.cjs"
```

Uses Node.js built-in `node:test` — no external dependencies.
