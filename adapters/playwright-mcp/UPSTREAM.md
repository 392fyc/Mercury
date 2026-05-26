# UPSTREAM — playwright-mcp

## Origin

Adapter mounts Microsoft's official
[`microsoft/playwright-mcp`](https://github.com/microsoft/playwright-mcp)
(`@playwright/mcp` on npm, Apache-2.0) as a Claude Code MCP server. No
upstream source is vendored — `launch.cjs` is original Mercury code that
resolves the cached `cli.js` at runtime and spawns it; provisioning is a
one-time manual step (see adapter README §Setup).

This is the **third mount mode** in Mercury's adapter policy — an
**npm-version-pinned MCP server (runtime-only)** — alongside (1) git submodule
to `modules/` and (2) `uvx git+<repo>@<SHA>` runtime-only (gpt-image-2). See
`adapters/README.md` §约束 + `.mercury/docs/DIRECTION.md` §四.

## Pin

| Field | Value |
|---|---|
| Repo | `microsoft/playwright-mcp` (Apache-2.0) |
| **Execution contract** | npm version **`@playwright/mcp@0.0.75`** |
| npm `dist-tags.latest` at pin | `0.0.75` (verified `registry.npmjs.org/@playwright/mcp/latest`) |
| `upstream_sha_at_import` | `8116437ffcfee1309cebc07dd30cee37720d2d19` |
| Pinned date | 2026-05-26 |
| bin | `{ "playwright-mcp": "cli.js" }` |
| License | Apache-2.0 |
| import_pr | 459 |

### npm-version ↔ git-tag mapping (important)

The **execution contract is the pinned npm version `0.0.75`** — npm resolves
the package tarball *by version* from the registry, not by git checkout. The
`upstream_sha_at_import`
(`8116437ffcfee1309cebc07dd30cee37720d2d19`) is the commit that git tag
`v0.0.75` points to, verified via
`gh api repos/microsoft/playwright-mcp/git/refs/tags/v0.0.75` on 2026-05-26.
That SHA is **audit metadata** (maps the pinned npm version back to an
upstream git tag for supply-chain / drift auditing) — it is **not** the
execution contract. `scripts/upstream-drift-check.sh` tracks
`upstream_path: package.json` to flag upstream version bumps that may require
re-pinning this adapter.

## Known incompatibilities / caveats

- **storage tools are opt-in** — cookie / localStorage / `browser_storage_state`
  tools require `--caps=storage`. Without it the auth-reuse flow does not
  exist (ADR §4.4). The adapter's recommended `.mcp.json` args include it.
- **CDP endpoint + storage-state is an open issue**
  ([microsoft/playwright-mcp#983](https://github.com/microsoft/playwright-mcp/issues/983))
  — when connecting via a CDP endpoint, storage-state config support is
  unresolved. Mercury avoids this entirely: the adapter hard-rejects all
  CDP / attach-class flags (ADR §4.2(b)) and only runs the MCP server's own
  self-launched isolated browser.
- **pre-1.0 API (0.0.x)** — CLI flags / tool names may change between minor
  versions. The flag allowlist in `launch.cjs` may need review on version
  bumps. Drift-check monitors `package.json`.
- **storageState scope** — `browser_storage_state` exports the *whole current
  context* (cookies + localStorage, **no** sessionStorage, **no** built-in
  domain filter). Single-domain scope comes from context isolation, not from
  the file format (ADR §4.1). Verified in Slice B (V6 PASS).
- **storageState export/load path asymmetry** — the server's output sandbox
  defaults to `<cwd>/.playwright-mcp/` (contains storageState exports with
  live cookies as well as console logs). This directory is gitignored
  (`.playwright-mcp/` in repo root `.gitignore`) to prevent credential
  leakage. The export location is controlled by the server's sandboxing and
  cannot be redirected via a CLI flag (`--output-dir` is removed from the
  adapter's flag allowlist). However, `launch.cjs` requires the `--storage-state`
  load path to be **outside the repo working tree** (ADR §5.2 path rule). This
  creates a one-step manual asymmetry: export lands in `<repo>/.playwright-mcp/`,
  must be moved to a repo-external path before the next session can load it.
  See adapter README §Slice B verification → Real-auth runbook for the exact
  move command. Tracked as a known operational note; `--output-dir` removal and
  the path asymmetry are not expected to be resolved without upstream API changes.

## Spawn-form note (Windows dev host)

On the dev host (Node 24 / npx 11, Node under `D:\Program Files`), npx's
generated bin-shim for `playwright-mcp` fails (`not recognized as an internal
or external command`) — reproduced under MSYS, cmd, and PowerShell. The
adapter therefore spawns `node cli.js` directly (shell-free, PATH-independent),
which returns `Version 0.0.75` reliably. No `node_modules` is vendored into
the repo.

The package is provisioned once via the one-time `npm install --no-save
--prefix <cacheDir>` command documented in adapter README §Setup. At runtime
the adapter only resolves the cached `cli.js` path (fail-closed if absent —
never auto-installs). See adapter README §"How it runs" for full rationale.

## Drift policy

`scripts/upstream-drift-check.sh` compares the upstream `package.json` blob
SHA at the pinned import SHA against the same file at upstream `HEAD`. The
manifest entry sets `upstream_path: "package.json"` because that file is the
canonical version/deps/bin contract — any change there signals the adapter's
pinned npm version may need review. The full upstream tree is not
file-by-file tracked (mounted as a runtime npm dependency, not by vendoring
source).

When drift-check reports `CHANGED`: review the upstream diff for license /
CLI-flag / tool-name changes before bumping the pinned npm version (in
`launch.cjs` `PINNED`, the `.mcp.json` args, the adapter README, and this
file) and the manifest `upstream_sha_at_import`.

## License

Upstream `@playwright/mcp` is Apache-2.0 (permissive — passes Mercury's
license gate). Mercury invokes it as a runtime dependency; no upstream source
is redistributed by this repository. `launch.cjs` is original Mercury code
(MIT, project license).

Cherry-pick / mount-provenance is recorded in the import commit message and
the `.mercury/state/upstream-manifest.json` entry.
