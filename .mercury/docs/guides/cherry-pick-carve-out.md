---
title: Cherry-pick carve-out — CLI-generated scaffolding (Category A / B)
extends: CLAUDE.md §"Cherry-pick protocol"
issue: 517
---

# Cherry-pick carve-out: CLI-generated scaffolding

> 本指南是 CLAUDE.md §"Cherry-pick protocol"(rules 1-6)的扩展细则,从 CLAUDE.md 迁出以瘦身每会话 auto-load 预算(#517 批 D)。CLAUDE.md 保留操作摘要 + 指向本文件的指针。

The cherry-pick protocol in CLAUDE.md (§"Cherry-pick protocol") applies to **files lifted from a specific upstream commit** (canonical upstream path + SHA + drift monitoring). It does NOT cleanly fit two adjacent cases that produce files via CLI invocation rather than direct upstream-path import. Split into 2 sub-categories:

## Category A — Pure scaffolding (one-shot project init)

Generators that produce a one-time project skeleton from templated boilerplate. The templates ship with the CLI itself; no per-file upstream "source path" exists.

| Generator | Invocation | Output scope |
|---|---|---|
| **create-tauri-app** | `pnpm create tauri-app` | Tauri 2 project skeleton (Rust workspace + JS frontend templated config) |
| **create-vite** | `pnpm create vite` | Vite + framework skeleton (TS config + entry + index.html) |

**Required for Category A**:

1. **Provenance line in PR body**: PR creating the scaffold records the exact CLI invocation + version at invocation time (e.g., "Scaffold via `pnpm create tauri-app`, create-tauri-app vX.Y at 2026-MM-DD"). Use the actual version, not a placeholder. Note: `pnpm create` resolves the create-* starter package transiently and does NOT pin the generator into the produced app's `pnpm-lock.yaml` — the PR-body line is the only durable provenance record.
2. **License compatibility check**: confirm the scaffold output's license is MIT / Apache-2.0 / similarly permissive (Tauri 2 = MIT/Apache-2.0; Vite = MIT). Record in PR body.
3. **Customization allowed without attribution**: post-scaffold Mercury edits to the generated files do NOT require per-file `Based on` attribution.

**NOT required for Category A**:

- ❌ Manifest entry in `.mercury/state/upstream-manifest.json`
- ❌ SKILL.md frontmatter `upstream_sha` field
- ❌ Per-file `# Based on <upstream>` comment
- ❌ Drift monitoring via `scripts/upstream-drift-check.sh`

## Category B — Registry-based item import (per-item upstream lift)

CLI tools that fetch named items from a versioned registry. Each `add` invocation imports a concrete registry item that has a canonical upstream identity. Closer to a per-file cherry-pick than to pure scaffolding. **Applies to ALL `shadcn add` invocations regardless of registry item type** — components, hooks, utilities, pages, fonts, themes, config files, rules, libraries, or any other resource a shadcn-compatible registry exposes (per [shadcn CLI docs](https://ui.shadcn.com/docs/cli) — `add` consumes registry items by name, URL, or local path).

| Generator | Invocation | Registry default | Output scope |
|---|---|---|---|
| **shadcn (any registry item)** | `pnpm dlx shadcn@latest add <name-or-url-or-path>` | <https://ui.shadcn.com/r> (official shadcn registry) | Any registry-backed resource: UI components, hooks, utilities, pages, themes, fonts, config files, rules, libraries, etc. Resource arg is a registry item name (default registry), a URL (any registry), or a local path. |

**Required for Category B**:

1. **Provenance line in PR body** (stricter than Category A): record (a) exact CLI invocation including the item-name / URL / path arg(s), (b) shadcn CLI version at invocation time, (c) **source identifier — always**, in one of three forms depending on the arg kind:
   - For a registry item name (default registry): `source = default registry (https://ui.shadcn.com/r)`
   - For a URL arg (custom registry or registry item URL): `source = custom registry URL: <url>`
   - For a local-path arg (file-system import, not registry-fetched): `source = local path: <relative-path>` (note that local-path adds bypass the registry layer entirely — the path IS the upstream identity)

   The source identifier determines license + upstream identity, so the arg kind must be unambiguous on the record. (d) registry item type if non-component (e.g., `registry:hook`, `registry:font`, `registry:lib`, `registry:page`, `registry:file`). Example: "Imported via `pnpm dlx shadcn@latest add tabs`, shadcn CLI vX.Y, source = default registry (https://ui.shadcn.com/r), item type = registry:component at 2026-MM-DD".
2. **License compatibility check**: confirm the license of the actual source you're importing from at invocation time, NOT a fixed assumption. The shadcn default registry is MIT (illustrative); custom registries may use different licenses, and local-path adds inherit the license of the source path's project. Verify per import + record the verified license in PR body.
3. **Customization is owned by Mercury after add**: shadcn's design philosophy is "copy-paste with full ownership" — once added, the file is Mercury-owned and editable without per-file upstream-tracking attribution.

**NOT required for Category B** (registry items are not pinned to upstream SHA; shadcn's contract is "you own the code"):

- ❌ Manifest entry in `.mercury/state/upstream-manifest.json` (registry items are not version-pinned to a specific upstream commit; shadcn's model deliberately decouples from upstream after add)
- ❌ Per-file `# Based on <upstream>` comment
- ❌ Drift monitoring via `scripts/upstream-drift-check.sh`

**Local-path guard — when local-path adds fall back to full cherry-pick protocol**

The local-path arg form is intended for Mercury-internal registry items (e.g., a path under `mercury-gui/` or a sibling Mercury repo path). It is NOT a back door for importing arbitrary external-project files via a local checkout.

A local-path add **falls back to the full cherry-pick protocol (rules 1-6)** when ANY of these conditions hold. Resolve the source path with `git rev-parse --show-toplevel` for the Mercury repo root (or `realpath` on the path arg) before applying the test — symlinks, `..` traversal, and absolute paths are all normalized this way:

- The resolved path is **not** under the current Mercury repo working tree (i.e., does not have the `git rev-parse --show-toplevel` output as a prefix)
- The resolved path is under a git submodule whose upstream points to a third-party repo (check via `git submodule status`)
- The resolved path is under a `node_modules/`, `vendor/`, or other package-manager-staged directory whose contents originate from an external package
- The resolved path is under a temporary checkout of an external project staged for import (e.g., a `tmp/`, `scratch/`, or any cloned third-party repo directory)

When in doubt, treat the local-path source as a file-lift cherry-pick (full protocol applies). The carve-out exists to formalize "shadcn-style registry add from a versioned source" — local-path is the narrowest case and the guard above (plus the catch-all default-deny) keeps the supply-chain audit surface intact.

**Tighter than Category A** — Category B's PR body line must identify (a) the specific registry-item / URL / local-path arg, (b) the source identifier in the form appropriate to the arg kind (default registry / custom registry URL / local path), and (c) the item type if non-component, because together these determine license + upstream identity.

## Adding new generators to either category

Extend the appropriate table via a separate PR. The PR must cite (a) generator's package source URL, (b) license, (c) whether it produces one-shot scaffolding (→ Category A) or per-item registry imports (→ Category B), (d) drift-tracking rationale. Any tool not listed should be treated as a regular cherry-pick (full protocol rules 1-6 apply) until categorized here.

## Authority chain

This carve-out resolves the repeat-DISAGREE-cite pattern observed during Phase 6 GUI MVP chain (PRs #421/#424/#425) where Argus / Copilot review threads flagged CLI-generated files as missing attribution. The Category A / Category B split was added in response to the audit finding that shadcn `add` is materially closer to registry-import than to pure project scaffolding, while create-tauri-app / create-vite genuinely are pure scaffolding.
