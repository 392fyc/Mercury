# mercury-gui

Mercury Phase 6 GUI — Tauri 2 desktop shell for Windows.

## Mode A Decisions (Issue #413, locked S122)

| # | Axis | Decision |
|---|------|----------|
| 1 | Frontend framework | React 19 + TypeScript |
| 2 | Rust toolchain | `~/.rustup` user-dir (exception to D-drive rule, accepted) |
| 3 | Bundler | Vite (Tauri default) |
| 4 | GUI directory | `mercury-gui/` at repo root (standalone, no monorepo workspace) |
| 5 | VS2022 Build Tools | `C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools` (C-drive exception, already installed) |

Reference: [Issue #413 comment 4493506591](https://github.com/392fyc/Mercury/issues/413#issuecomment-4493506591)

## Tech Stack

| Component | Version |
|-----------|---------|
| Tauri | 2 (framework stable since 2024-10) |
| `@tauri-apps/cli` | ^2.11.1 |
| `@tauri-apps/api` | ^2.11.0 |
| React | ^19.1.0 |
| Vite | ^7.x |
| TypeScript | ~5.8.3 |
| `tauri-plugin-single-instance` | 2.4.2 |

## Build Commands

```sh
# Install JS deps (run once)
pnpm install

# Development (hot-reload)
pnpm tauri dev

# Production build — produces .msi in src-tauri/target/release/bundle/msi/
pnpm tauri build
```

## Windows MSI Install (D-drive)

`pnpm tauri build` places the installer at:

```
src-tauri/target/release/bundle/msi/mercury-gui_0.1.0_x64_en-US.msi
```

To install to `D:\Program Files\Mercury` (per Mercury MUST rule), run the
installer from the command line:

```cmd
msiexec /i mercury-gui_0.1.0_x64_en-US.msi INSTALLDIR="D:\Program Files\Mercury" /qb
```

Run from an elevated cmd / PowerShell — `D:\Program Files\` is a protected
directory and the install will fail (or prompt UAC) otherwise.

Note: the graphical installer wizard defaults to `C:\Program Files`. Always use
the `msiexec` CLI form above to satisfy the project D-drive install requirement.
Verified by manual user step (automated msiexec test deferred — DoD criterion 4).

## System Tray

A system tray icon is registered in `src-tauri/src/lib.rs` using the built-in
`tray-icon` Tauri feature (no separate plugin required). The tray shows a
"Quit Mercury" menu entry. `show_menu_on_left_click = true` is set for MVP
simplicity. Future work: left-click to show/hide main window (#416).

## Single-Instance Lock

`tauri-plugin-single-instance` is wired as the **first** plugin in the builder
chain (required by Tauri — ordering matters). A second launch attempt is silently
rejected. Focus-on-relaunch behaviour is deferred to Issue #416 (Phase 2 UI).

## Directory Layout

```
mercury-gui/
  index.html          — Vite entry
  src/                — React frontend (TSX)
  src-tauri/
    src/lib.rs        — Tauri app init: single-instance + tray + greet command
    src/main.rs       — Desktop entry point
    tauri.conf.json   — App config (identifier: com.mercury.gui)
    Cargo.toml        — Rust deps
    icons/            — App icons (bundled into MSI)
    capabilities/     — Tauri permission model
  public/             — Static assets
  vite.config.ts
  tsconfig.json
  package.json
```

## LOC Cap Note

`mercury-gui/` is Mercury-internal tooling (not an external-project adapter).
It is exempt from the `adapters/<vendor-name>/` ≤200 LOC cap per CLAUDE.md MUST
rules. See CLAUDE.md "External-project adapters" bullet for the authority chain.
