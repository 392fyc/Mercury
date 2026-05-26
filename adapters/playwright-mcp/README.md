# playwright-mcp

Mercury adapter that mounts Microsoft's official
[`@playwright/mcp`](https://github.com/microsoft/playwright-mcp) (Apache-2.0)
as a Claude Code MCP server, pinned to **`@playwright/mcp@0.0.75`**. The
adapter is a **config-gate wrapper only** — it holds no browser logic. All
browser control lives in the upstream MCP server; Mercury only enforces
security defaults before the server starts.

Design authority:
[`.mercury/docs/research/issue-154-web-automation-2026-05.md`](../../.mercury/docs/research/issue-154-web-automation-2026-05.md)
(Phase 1 ADR). This adapter is Phase 2 **Slice A** (mount + governance gate +
config封装). See Issue #458 / #154.

## Why mounted

The ADR (§3) selects `@playwright/mcp` over lightpanda (AGPL-3.0, fails the
license gate), puppeteer (no official MCP → would need self-research), and
browser-use (agent-within-agent). `@playwright/mcp` is the official,
permissive-licensed, ready-made MCP server for stateful browser automation
with Cookie/Session reuse — matching #154's needs without building our own.

## What is configured

The recommended `.mcp.json` args (passed *through* this wrapper) are:

```
--isolated --caps=storage --storage-state=<repo-external per-user path>
```

- `--isolated` — fresh in-memory profile per session, nothing persisted to
  disk (ADR §4.3 least-privilege default). The wrapper **injects `--isolated`
  if the caller omits it**.
- `--caps=storage` — opt-in capability that enables the cookie / localStorage
  / `browser_storage_state` tools required for auth reuse (ADR §4.4). Without
  it those tools do not exist.
- `--storage-state=<path>` — storageState file for an isolated session, kept
  at a **repo-external per-user private path** supplied via
  `$MERCURY_PLAYWRIGHT_STORAGE_STATE` (see §Setup).

## Security red lines (enforced by `launch.cjs`)

All validation logic lives in `launch.cjs` (single file). The gate runs
**before** the upstream server is spawned.

### Minimum flag allowlist

Only these flags are permitted (default-deny — any unknown flag is rejected):

| Flag | Why kept |
|---|---|
| `--isolated` | Enforces fresh in-memory profile (injected if omitted) |
| `--caps` | Opt-in capabilities (e.g. `storage` for auth reuse) |
| `--storage-state` | StorageState file path (repo-external, validated) |
| `--allowed-origins` | Origin allowlist for network requests |
| `--blocked-origins` | Origin blocklist |
| `--headless` / `--headed` | Headless mode toggle |
| `--browser` | Playwright engine — value restricted to `chromium`/`firefox`/`webkit` |
| `--device` | Emulated device preset |
| `--viewport-size` | Viewport dimensions |

Removed from allowlist (vs. Slice A first draft) and why:

| Removed flag | Reason |
|---|---|
| `--config` | Config JSON accepts `cdpEndpoint`/`isolated:false` → backdoor past all three red lines |
| `--port` / `--host` | Switch stdio transport to SSE/HTTP → exposes unauthenticated network endpoint |
| `--help` / `--version` | Print plain text to stdout → contaminate JSON-RPC channel |
| `--save-trace` / `--save-session` / `--output-dir` | Artifact paths contain auth cookies; not needed for Slice A PoC |
| `--no-sandbox` | Weakens Chromium sandbox |
| `--timeout-action` / `--timeout-navigation` | Not required for Slice A |

### Enforcement rules

1. **Default-deny flag allowlist.** Any flag not on the allowlist is rejected —
   so a *new* attach-class flag introduced upstream automatically falls into
   default-deny.
2. **Whole-class rejection of attach / connect flags** (ADR §4.2(b)). Any flag
   whose name contains `cdp` / `endpoint` / `extension` / `connect` / `remote`
   is rejected — covers `--extension`, `--cdp-endpoint`, `--remote-endpoint`,
   `remoteEndpoint` and future siblings. Mercury never attaches to a
   pre-existing or externally-launched browser.
3. **`--browser` value whitelist.** Only `chromium`, `firefox`, `webkit`
   (Playwright engine names). Real-browser channel names (`chrome`, `msedge`,
   `chrome-beta`, `msedge-*`, `cdp`, …) are rejected — they attach to real
   installed browsers that hold the user's cookies.
4. **Flag value attach-token scan.** For all non-path, non-origin flags, the
   value is scanned for attach/connect tokens — prevents `--caps=connect` style
   value-smuggling. `--allowed-origins` / `--blocked-origins` are excluded from
   this scan: their values are origin lists that legitimately contain substrings
   like `remote` or `endpoint`; the real attach vector for those flags is
   flag-name-based and already covered by rule 2.
5. **Positional arguments rejected.** Any argument that does not start with `-`
   is rejected (exit 2). All flag values must use `--flag=value` equals form
   (not `--flag value` space form) except `--browser` and `--storage-state`
   which also accept space form internally.
6. **Path resolution (env-var → absolute, hard-fail).** Values of
   `--storage-state` are env-expanded (`$VAR` / `${VAR}`) and resolved to an
   absolute path. Unresolved tokens → launch rejected (not silently passed
   through).
7. **Reject repo-internal and real-profile paths.** The resolved path is
   canonicalized via `fs.realpathSync.native()` (resolves symlinks, Windows 8.3
   short names, junctions) before comparison. Rejected if inside the repo
   working tree (repo root resolved from `__dirname` — deterministic, no
   `git`/cwd dependency) or matching a real browser profile fragment
   (`Google/Chrome/User Data`, `Microsoft/Edge/User Data`, …; case- and
   slash-insensitive).
8. **Repo-root fail-closed.** If the adapter cannot determine the repo root
   (unusual deployment), path-type flags are rejected rather than silently
   allowed.

Any violation → stderr error + exit `2` (config gate reject) or `3`
(CLI cache absent — see §Setup). The MCP server never starts with an unsafe config.

> The `.mcp.json` entry uses `$MERCURY_PLAYWRIGHT_STORAGE_STATE` as the
> storage-state path. Export this env var to point at your repo-external private
> auth-state file before starting Slice B live sessions. If the variable is
> unset, the wrapper hard-fails at launch (fail-closed — never silently
> passes an unresolved path).
>
> **Value form requirement:** all flag values (except `--browser` and
> `--storage-state`) must use `--flag=value` equals form. Space-separated
> positional values are rejected by the config gate (exit 2).

## Setup (one-time provision)

Before first use, provision the pinned package into the per-user cache. This
is a **one-time step** — Slice A does not need it for gate tests, only for
actual browser spawning (Slice B).

Cross-platform form (matches the path `launch.cjs` resolves via `os.homedir()`):

```
npm install --no-save --no-fund --no-audit \
  --prefix "~/.cache/mercury/playwright-mcp/0.0.75" \
  @playwright/mcp@0.0.75
```

If the cache is absent at launch time, the adapter exits 3 and prints the
exact command to run (fail-closed — it never auto-installs at runtime).

Before Slice B live sessions, also export the storage-state env var pointing
at a **repo-external** private path:

```sh
# PowerShell (Windows)
$env:MERCURY_PLAYWRIGHT_STORAGE_STATE = "$env:LOCALAPPDATA\mercury\playwright-mcp\auth-state.json"

# POSIX shell
export MERCURY_PLAYWRIGHT_STORAGE_STATE="$HOME/.local/mercury/playwright-mcp/auth-state.json"
```

## How it runs (spawn form)

`launch.cjs` mounts the upstream server **runtime-only, with no vendored
`node_modules` in the repo**:

1. It resolves `cli.js` from the per-user cache
   (`~/.cache/mercury/playwright-mcp/0.0.75/node_modules/@playwright/mcp/cli.js`).
   If absent → exit 3 with the provision command above (fail-closed; no
   network access, no npm noise on the MCP stdio channel).
2. It spawns `node <cli.js> <safe-args>` with `stdio: 'inherit'` (transparent
   MCP stdio JSON-RPC) and propagates the exit code.

**Why not plain `npx @playwright/mcp@0.0.75`?** On the Windows dev host (Node
24 / npx 11, Node installed under `D:\Program Files` — a path with a space),
npx's generated bin-shim for `playwright-mcp` fails with
`'playwright-mcp' is not recognized as an internal or external command`. This
was reproduced under MSYS bash, `cmd.exe`, and PowerShell. By contrast
`node <cli.js>` (shell-free, PATH-independent, via `process.execPath`) works
reliably. Smoke-verified 2026-05-26: gate rejections exit 2; valid config
with provisioned cache reaches upstream `--version` → `Version 0.0.75`.

## Run

The server is registered in the repo-root `.mcp.json` under `playwright`:

```jsonc
{
  "playwright": {
    "type": "stdio",
    "command": "node",
    "args": [
      "./adapters/playwright-mcp/launch.cjs",
      "--isolated", "--caps=storage",
      "--storage-state=$MERCURY_PLAYWRIGHT_STORAGE_STATE"
    ]
  }
}
```

## Test

```
node --test "adapters/playwright-mcp/test/*.cjs"
```

Uses Node.js built-in `node:test` — no external deps. Tests cover the config
gate's pure validation functions (attach-class reject, default-deny unknown
flags, repo-internal / real-profile path reject, unresolved-env reject, legal
config pass + `--isolated` injection, slash/case robustness). Tests never
spawn a real browser.

## Out of scope (Slice B)

The following need an MCP client session + manual login to a target site and
are **not** part of Slice A: live navigate + screenshot (ADR V1), auth reuse
via storageState (V2), and storageState domain-scope / sessionStorage
identification (V6). They are deferred to Slice B (requires session restart +
human login).

## Provenance / license

`launch.cjs` is original Mercury code (MIT, project license). Upstream
`@playwright/mcp` is Apache-2.0, invoked as a runtime dependency (not
redistributed by this repo). See [`UPSTREAM.md`](UPSTREAM.md) for the
npm-version ↔ git-tag pin and drift policy; manifest entry in
`.mercury/state/upstream-manifest.json`.
