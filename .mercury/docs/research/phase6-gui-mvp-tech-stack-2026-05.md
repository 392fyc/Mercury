# Phase 6 GUI MVP — Tech stack research (5 candidates)

**Issue**: [#411](https://github.com/392fyc/Mercury/issues/411) (research-front, sub-task 4)

**Status**: Phase 1 — tech stack web-research record + Mode A decision (S120 → user-direction landed 2026-05-20)

**Session**: S120 (main lane)

**Companion ADR**: `phase6-gui-mvp-agentview-backend-schema-2026-05.md`

**Mandatory research protocol applied**: 5 WebSearches against vendor docs + npm registry (2026-05-19); 1 page-fetch attempt where required.

---

## TL;DR

5 candidate stacks 与 Mercury constraints (Windows D-drive install + cross-platform desirable + LOC budget 不强制 (internal tooling, NOT external adapter, ≤200 LOC cap 不适用) + read-only v1 + bundle size 重要 for desktop ship) 对照:

| Stack | Latest (2026-05) | Bundle | Lang | Cross-plat | Maturity | Mercury fit (read-only v1) |
|-------|------------------|--------|------|------------|----------|----------------------------|
| **Tauri** | v2.11.1 (2026-05-06) | ~10-30 MB | Rust + Web | Linux/macOS/Win + Android/iOS | **GA stable** | ★★★★★ (推荐 desktop primary) |
| **Electron** | v42.1.0 (3 days pre-S120, ~2026-05-16) | ~100-150 MB | JS + Web | Linux/macOS/Win | **GA, hugely mature** | ★★★★ (bundle 大但 zero-friction) |
| **Wails** | v3.0.0-alpha.93 (2026-05-17), v2 GA | ~20-50 MB | Go + Web | Linux/macOS/Win | **v3 alpha** (v2 GA OK) | ★★★ (Mercury 已 JS-heavy, Go 引入新 lang) |
| **Textual** | **UNVERIFIED** (v3+ inferred from pypi; search 未给出 2026 specific version) | n/a (TUI) | Python | Linux/macOS/Win | **GA, Textualize active** | ★★★ (TUI 适合 dev/SSH 场景, 但 GUI 形态弱) |
| **Pure Web (Next.js localhost)** | Next.js 15-16 **UNVERIFIED**, chokidar v5 (Nov 2025) | n/a (browser) | JS + Web | 任意 browser | **GA, hugely mature** | ★★★★ (但需用户开 browser, port 占用) |

**预初步预排** (S120 不决策, 仅供 user Mode A 决策时参考):

- **Top 2 primary**: **Tauri 2** (推荐 strong) | **Electron** (safe backup)
- **Top 1 alternative**: **Pure Web (Next.js localhost)** if user 接受 browser-based access
- **Secondary**: **Textual** for TUI-only ship (no GUI window)
- **Bottom**: **Wails v3** (alpha) / v2 (mature 但 Go 引入新 lang dep)

**所有 5 选项都不 block Mercury D-drive 安装 policy** — 详 §"Windows D-drive 安装 policy" 段。

---

## Tauri (v2.11.1, GA stable)

### Verified facts (2026-05-19)

- **Latest version**: **v2.11.1** (released 2026-05-06) per [Tauri Core Ecosystem Releases](https://v2.tauri.app/release/)
- **License**: MIT (per Mercury cherry-pick 允许 license list)
- **Languages**: Rust (backend) + Web (HTML/CSS/JS, framework-agnostic) (frontend)
- **Cross-platform**: Linux + macOS + Windows + Android + iOS (per [Tauri 2.0 docs](https://v2.tauri.app/))
- **Windows distribution**: Microsoft Installer (.msi) bundled via WiX, OR .exe via NSIS — per [Windows Installer | Tauri](https://v2.tauri.app/distribute/windows-installer/)
- **Windows prerequisites** (per [Prerequisites | Tauri](https://v2.tauri.app/start/prerequisites/)):
  - Microsoft Edge WebView2 runtime (preinstalled Win11; patched Win10)
  - Visual Studio 2022 Build Tools — "Desktop development with C++" workload
  - Windows 10/11 SDK
- **Bundle size**: ~10-30 MB typical (依赖 WebView2 系统 runtime, 不 bundle browser)

### Mercury fit assessment

| Dimension | Score | Note |
|-----------|-------|------|
| Bundle size | ★★★★★ | 10-30 MB vs Electron 100-150 MB — desktop ship 优势 |
| Performance | ★★★★★ | Rust backend + native WebView |
| Mercury existing JS stack reuse | ★★★★ | frontend 是 Web, 可复用 Mercury 已有 JS tooling (notify-hub, dispatch templates) |
| Windows D-drive 安装 | ★★★★ | .msi 安装器 standard Windows MSI 流程, 用户可选 dest (D-drive OK; 详见 §"Windows D-drive 安装 policy") |
| Cross-platform (Win/macOS/Linux ship v1) | ★★★★★ | 单 codebase, 三平台 build (Windows-only ship v1 也 OK, future-proof) |
| 新 lang dep (Rust) | ★★★ | Rust toolchain 引入新依赖; 但 Mercury 已 multi-lang (Node 20+ + Python 3.x), Rust 增量 acceptable |
| Maturity | ★★★★★ | v2 GA since late 2024, v2.11.x patch-stable |

### Caveats

- Rust 学习曲线: Mercury 暂无 Rust 代码; Tauri 大多数 UI 工作在 JS 侧 (Rust 仅 build / system API bridge / IPC commands), 但仍需 dev 写 Tauri commands in Rust
- Visual Studio 2022 Build Tools 安装到 D 盘 (per CLAUDE.md Windows policy) 需验证 — VS2022 安装器允许选 dest

### Recommended for Mercury

**★★★★★ Primary candidate** for Mercury Phase 6 GUI MVP — bundle size + cross-platform + Mercury JS reuse 三项都 strong, Rust 学习曲线是唯一 friction (但 v1 read-only 不需 heavy Rust 编程, IPC 主要 file-read).

---

## Electron (v42.1.0, GA hugely mature)

### Verified facts (2026-05-19)

- **Latest version**: **v42.1.0** (last published "3 days ago" — ~2026-05-16) per [Electron Releases](https://releases.electronjs.org/) + [electron - npm](https://www.npmjs.com/package/electron)
- **License**: MIT
- **Languages**: JavaScript + HTML/CSS + Web (bundled Chromium browser)
- **Cross-platform**: Linux + macOS + Windows (no mobile)
- **Windows binaries**: ia32 (x86), x64 (amd64), arm64
- **Packaging**: `electron-builder v26.8.2` standard (per [electron-builder Releases](https://releasealert.dev/npmjs/_/electron-builder))
- **Adoption**: 1689 npm packages depend on Electron (per npm)

### Mercury fit assessment

| Dimension | Score | Note |
|-----------|-------|------|
| Bundle size | ★★ | 100-150 MB typical (bundle Chromium) — heavy for "lightweight monitor" |
| Performance | ★★★ | Chromium overhead but rich features |
| Mercury existing JS stack reuse | ★★★★★ | 100% JS — Mercury Node 20+ 直接 reuse |
| Windows D-drive 安装 | ★★★★ | electron-builder NSIS/MSI 都支持 dest 选择 |
| Cross-platform (Win/macOS/Linux ship v1) | ★★★★★ | 标准 cross-platform desktop framework |
| 新 lang dep | ★★★★★ | 无新 lang (pure JS) |
| Maturity | ★★★★★ | 2013+ proven, VS Code / Slack / Discord 都用 |

### Caveats

- Bundle 大 — Mercury "lightweight monitor" 定位与 Electron 100MB+ ship 矛盾
- Chromium auto-update + security patch cadence 需 track (electron-updater 标准方案)
- 在内存占用 / 启动速度上 vs Tauri 劣势

### Recommended for Mercury

**★★★★ Safe backup** — 若 Mercury team 决策 prioritize "JS-only stack + max maturity over bundle size", Electron 是 safe pick. 但 Tauri 在所有客观维度上 (bundle + perf + cross-plat) 都 ≥ Electron, Rust 学习曲线是唯一 trade-off。

---

## Wails (v3.0.0-alpha.93 / v2 GA)

### Verified facts (2026-05-19)

- **v3 latest**: **v3.0.0-alpha.93** (2026-05-17, 2 days pre-S120) per [Wails Releases](https://github.com/wailsapp/wails/releases)
- **v3 status**: ALPHA — "API reasonably stable" per [v3.wails.io](https://v3.wails.io/), 应用 already in production but **docs + tooling still being refined**
- **v2 status**: **GA stable** — recommended for production
- **License**: MIT
- **Languages**: Go (backend) + Web (HTML/CSS/JS) (frontend)
- **Windows runtime**: Microsoft WebView2 (same as Tauri)
- **v3 highlights** (per Wails docs):
  - Improved binding generation
  - Multi-window support
  - More transparent build system
  - Heavy lifting tasks now CLI commands

### Mercury fit assessment

| Dimension | Score | Note |
|-----------|-------|------|
| Bundle size | ★★★★ | 20-50 MB typical (Go binary + WebView2 system runtime) |
| Performance | ★★★★ | Go backend native, 与 Tauri 相近 |
| Mercury existing JS stack reuse | ★★★★ | frontend Web, 可复用 |
| Windows D-drive 安装 | ★★★ | Wails 自带 installer 选项 less mature than Tauri/Electron; manual build + place 可 |
| Cross-platform | ★★★★ | Win/macOS/Linux (no mobile) |
| 新 lang dep (Go) | ★★ | Go 是 Mercury 暂无的新 lang; 引入 Go toolchain 增量 |
| Maturity | ★★★ | v3 alpha; v2 GA 稳但 v3 是未来 |

### Caveats

- v3 alpha 不适合 Mercury Phase 6 long-term decision (production-ready 推荐用 v2)
- Go lang 引入 — Mercury 暂未 Go 代码, 新 stack 引入
- Wails 用户社区比 Tauri / Electron 小

### Recommended for Mercury

**★★★ Secondary** — 若 Mercury team 有 Go 偏好 OR 想避开 Rust 学习曲线, Wails v2 是合理选择. 但相比 Tauri 在 maturity / community / cross-platform parity 上无 strict 优势, Go 引入新 lang dep 是 net cost.

---

## Textual (Python TUI)

### Verified facts (2026-05-19)

- **Latest version**: UNVERIFIED (search 未给出 2026 specific version; [Textualize GitHub](https://github.com/Textualize/textual) 持续 active dev, 当前推断 v3.x — 需 `pip show textual` 验证)
- **License**: MIT
- **Language**: Python (3.8+ typical)
- **Cross-platform**: macOS + Linux + Windows
- **Form factor**: TUI (terminal) + Web (textual-serve 可 browser deploy)
- **Inspired by**: Rich library (same team)
- **Widgets**: DataTable, TreeView, Input (mouse-interactive + animation)

### Mercury fit assessment

| Dimension | Score | Note |
|-----------|-------|------|
| Bundle size | n/a | TUI, no native bundle (Python interpreter dep) |
| Performance | ★★★★★ | 极轻量 — terminal output |
| Mercury existing JS stack reuse | ★ | Python lang, Mercury Python 仅在 `~/.claude/scripts/` (mem0/cost-tracker), 新 Python desktop app dep |
| Windows D-drive 安装 | ★★★ | Python 安装到 D 盘 OK (uv / pyenv 均支持); textual 是 pip 包 |
| Cross-platform | ★★★★★ | 任 Python-supported OS |
| 新 lang dep (Python) | ★★★ | Mercury 已有 Python 用法, 但用于 GUI 是 net cost (vs JS-side reuse) |
| Maturity | ★★★★ | Textualize 2026 active dev, GA |

### Caveats

- **Form factor 不匹配 GUI 期望** — TUI 在 SSH / headless 场景 OK, 但 user "Mercury 完整体验里程碑" 暗示有 visual GUI 需求
- Python lang 引入 Mercury frontend 是新方向

### Recommended for Mercury

**★★★ Niche** — Textual 适合 **SSH-only / headless monitoring** use case (e.g. NAS 远程监控), NOT primary GUI form factor. **可作为 v2+ companion** (e.g. `mercury-tui` 命令补充 GUI), 但 v1 不推荐。

---

## Pure Web (Next.js localhost) + chokidar

### Verified facts (2026-05-19)

- **Next.js**: 15-16 (**UNVERIFIED** — 具体 latest version 未 pin via `npm view next version`; [vercel/next.js](https://github.com/vercel/next.js) active dev. Mode A 已选 Tauri 2, Next.js 仅作 alternative-not-chosen 参考)
- **chokidar**: **v5** (Nov 2025), ESM-only, Node 20+ min — per [paulmillr/chokidar](https://github.com/paulmillr/chokidar) + [chokidar - npm](https://www.npmjs.com/package/chokidar)
- **License**: chokidar MIT, Next.js MIT
- **Languages**: JavaScript / TypeScript
- **Form factor**: Browser (localhost:port)
- **File watching**: chokidar v5 efficient native recursive on Windows

### Mercury fit assessment

| Dimension | Score | Note |
|-----------|-------|------|
| Bundle size | n/a | server-side Node + browser-side, no shipped binary |
| Performance | ★★★★ | Next.js server + browser; depends on user browser perf |
| Mercury existing JS stack reuse | ★★★★★ | 100% Mercury JS reuse, Node 20+ already deployed |
| Windows D-drive 安装 | ★★★★★ | Mercury repo 已 D-drive; localhost server run from D 盘 OK |
| Cross-platform | ★★★★★ | browser-based — works anywhere |
| 新 lang dep | ★★★★★ | 无新 lang |
| Maturity | ★★★★★ | Next.js + chokidar 两者都 hugely mature |

### Caveats

- **需 user 开 browser** — Mercury "桌面端" 体验弱化, 但 Telegram + Notify Hub 已 close 了 "离开键盘" UX, GUI 是 desktop 体验补充 — browser-based 是否够好需 user 决策
- **Port 占用** — localhost:3000 (Next.js default) 可能 conflict
- **No system tray / window** — 不能像 desktop app 一样 close-to-tray, 需用户主动 visit browser tab
- chokidar v5 ESM-only — Mercury 现 cjs 主导, 需 mixed support

### Recommended for Mercury

**★★★★ Strong alternative** — 若 user 接受 "open browser to monitor Mercury" UX, pure Web 是 lowest-friction (无新 lang, 无新 toolchain, no binary build). 但若 user 期望 desktop app feel (system tray, native window), 需 Tauri/Electron/Wails.

---

## Windows D-drive 安装 policy 通用分析

Mercury CLAUDE.md MUST: "Install to D drive, not C drive".

| Stack | D-drive 兼容性 | 实现路径 |
|-------|----------------|----------|
| Tauri | ✅ | .msi 安装器允许 user 选 dest folder (standard MSI 行为); 或直接 portable .exe 不安装 |
| Electron | ✅ | electron-builder NSIS/MSI 都允许 dest 选择 |
| Wails | ✅ | manual build → place 任意 (less mature installer) |
| Textual | ✅ | Python venv + pip install 任意位置; 或 PyInstaller bundle 到 D 盘 |
| Pure Web | ✅ | Mercury repo 已 D 盘, Next.js dev/build/run from D 盘 |

**所有 5 选项都满足 Mercury Windows D-drive policy**. 不构成 differentiator。

---

## Polling vs file-watcher (data flow Mode A blocker)

| Approach | Library | Pro | Con |
|----------|---------|-----|-----|
| **Polling** | n/a (setInterval / setTimer / time.sleep) | 简单, no extra dep | CPU/IO 浪费 (即使 nothing changed); latency 5-15s |
| **File-watcher (chokidar v5)** | `chokidar` (Node) | 0-latency 触发, low CPU when idle | ESM-only v5, node 20+ min; Windows native recursive support |
| **OS-native** | `fs.watch` (Node), `watchdog` (Python), `notify` (Rust crate) | 最低 overhead | 平台差异; Mercury 需 cross-plat → chokidar / watchdog 包装层 |

**推荐**: chokidar v5 (Node stack) OR watchdog (Python stack), depending on tech-stack 决策. 不需 Mercury 自建 daemon (chokidar / watchdog 已是 daemon 角色 in-process).

---

## Mode A Decision Record (user-direction landed 2026-05-20)

**Decision timeline** (audit-clarity per Argus iter-1 finding):

| Phase | Date (local) | Note |
|-------|--------------|------|
| Research-front 5 sub-tasks executed | 2026-05-19 | S120 session start, empirical schema inspect + 5 WebSearches |
| ADR initial drafts written | 2026-05-19 | Both schema + tech-stack ADRs as research-record (Mode A blockers section originally PENDING user) |
| User Mode A decision via AskUserQuestion | 2026-05-20 | 4-axis response received (form factor + stack + scope + cross-plat) |
| ADR Decision Record append (this section) | 2026-05-20 | tech-stack ADR section replacement; schema ADR v1/v2+ scope marker |
| Commit `6c3470e` + PR #412 opened | 2026-05-20 | branch `lane/main/411-phase6-gui-mvp-research` |

S120 完成 5 sub-tasks + surface 4 axes to user via AskUserQuestion. User 选择 (2026-05-20):

| Axis | User decision | Rationale |
|------|---------------|-----------|
| **Form factor** | **Local desktop GUI** ✅ | desktop daily-driver feel; system tray possible; native window (matches "Mercury 完整体验" 里程碑) |
| **Tech stack** | **Tauri 2** (v2.11.1) ✅ | bundle 10-30MB (light), cross-plat future-proof, Mercury JS frontend reuse, Rust IPC v1 read-only 简单 |
| **MVP scope** | **Scenarios 1 + 5** ✅ (NOT 4) | 比 ADR 预选 (1+4+5) 更精简 — cost-tracker trend chart (scenario 4) 推迟 v2+. v1 = 4-lane snapshot + Issue/PR dashboard |
| **Cross-platform** | **Windows-only v1** ✅ | Mercury daily-driver platform; fastest path; v2 可加 macOS/Linux future |
| **Data flow** | **chokidar file-watcher (default)** ✅ | 与 Tauri 兼容 (Node-side watcher 转发 IPC event to Rust side). Rust `notify` crate alt **DEFERRED to implementation kickoff** — 不在 Mode A 决策内; 由 #411-B 实现 session 测出 perf/complexity tradeoff 后定 |

### Implementation derivatives (decided)

- **Bundle target**: ~10-30 MB MSI installer (Tauri Windows MSI via WiX)
- **Frontend framework**: TBD (framework-agnostic per Tauri; 推荐 React 或 SolidJS for Mercury 已有 JS stack 一致性, 待 implementation 决策 — out of S120 scope)
- **Rust crates needed v1**: tauri, serde, serde_json, chrono (timestamps), regex (filter primitives)
- **Read-side data sources v1** (per companion schema ADR):
  - `~/.claude/jobs/<id>/state.json` (22 fields, schema-tolerant)
  - `~/.claude/daemon/roster.json` (4 fields)
  - LANES.md (Mercury lane registry → cwd group key)
  - gh CLI Issue/PR state (scenario 5)
- **Read-side data sources DEFERRED v2+** (scenarios 2/3/4):
  - `~/.claude/jobs/<id>/timeline.jsonl` (scenario 2 跨 session 时间轴)
  - `~/.claude/scripts/cost-tracker/<id>.jsonl` (scenario 4 cost trend chart)
- **System tray**: Tauri tray-icon plugin v1 desirable (close-to-tray UX)
- **Single-instance lock**: Tauri single-instance plugin v1 (avoid multi-launch)

### Implementation Issue chain (filed post-decision)

实施 work 由 dev-pipeline 跟进, 不在 S120 scope。建议 split 为:

- **#411-A** (P2, lane:main, enhancement): Tauri 2 project scaffold + Mercury Windows MSI build + system tray + single-instance — bare app skeleton, no scenarios yet
- **#411-B** (P2, lane:main, enhancement): Read-side data layer (state.json + roster.json + LANES.md parser; chokidar/notify file-watcher) — IPC commands defined
- **#411-C** (P2, lane:main, enhancement): UI scenario 1 — cross-lane snapshot (4 active lanes + active PR + 5h usage marker)
- **#411-D** (P2, lane:main, enhancement): UI scenario 5 — Issue/PR dashboard (gh CLI integration + label/lane/Phase filter)
- **#411 itself**: closes with this ADR PR (research + decision finalized)

或合并为单 Issue `#411 sequel` 描述完整 MVP, 走 dev-pipeline blind acceptance. 选择 split-or-single 由 implementation kickoff session 决定。

### Hold-the-line constraints (carry into implementation)

- **Mercury LOC budget**: internal tooling (NOT external adapter) — `≤200 LOC adapter cap` 不适用 per S119 CLAUDE.md authority chain reaffirm (specifically the MUST clause "External-project adapters under `adapters/<vendor-name>/` MUST stay under 200 lines" — scoped to `adapters/<vendor-name>/` only; this GUI is Mercury-internal tooling outside that scope). Size by need.
- **D-drive install**: Tauri MSI 用户安装到 D 盘 OK (standard MSI dest prompt). VS2022 Build Tools 自己也安到 D 盘 per CLAUDE.md policy
- **Read-only v1**: 严禁 write/delete to `~/.claude/jobs/` / `~/.claude/daemon/` (Anthropic owned per S119 governance)
- **Anthropic compat-tolerance**: schema-tolerant parsing per companion schema ADR — missing/added fields don't crash
- **No spawn of `claude agents` CLI**: 该 CLI TTY-required, 不可 batch parse (per schema ADR empirical)

---

## Open questions for Phase 2 (not S120 scope)

1. Tauri Rust IPC 写 file-read commands vs Tauri JS bridge 直接 fs.readFile — perf / complexity tradeoff
2. Electron bundle size 在 Mercury 实际场景 (read-only viewer) 能否 trim 到 < 80 MB
3. Wails v3 alpha → GA timeline (是否 Phase 2 等 v3 GA, 还是 v2 起步 future v3 migration)
4. Pure Web + chokidar 在 Windows OneDrive synced folder (e.g. `~/.claude/` 是否在 OneDrive) 上的 file-watch reliability
5. Textual web-serve mode (Textualize textual-serve 项目) 是否构成 "TUI + Web in 1 stack" 第 6 candidate

**这些 Phase 2 实现期间 verify**, 不阻塞 Mode A user decision。

---

## References

### Tauri
- [Tauri Core Ecosystem Releases](https://v2.tauri.app/release/) — v2.11.1 2026-05-06
- [Windows Installer | Tauri](https://v2.tauri.app/distribute/windows-installer/) — MSI/NSIS
- [Prerequisites | Tauri](https://v2.tauri.app/start/prerequisites/) — Win10/11 SDK + VS2022 + WebView2
- [Tauri 2.0 Stable Release | Tauri](https://v2.tauri.app/blog/tauri-20/)
- [tauri 2.11.1 — Docs.rs](https://docs.rs/crate/tauri/latest)

### Electron
- [Electron Releases](https://releases.electronjs.org/) — v42.1.0 latest
- [electron - npm](https://www.npmjs.com/package/electron) — 1689 dependents
- [Releases · electron-builder](https://releasealert.dev/npmjs/_/electron-builder) — v26.8.2

### Wails
- [Wails](https://wails.io/) — v2 GA
- [Wails v3](https://v3.wails.io/) — v3.0.0-alpha.93 2026-05-17
- [Releases · wailsapp/wails](https://github.com/wailsapp/wails/releases)

### Textual
- [Textual — Home](https://textual.textualize.io/)
- [Textualize/textual GitHub](https://github.com/Textualize/textual)
- [Python Textual: Build Beautiful UIs in the Terminal — Real Python](https://realpython.com/python-textual/)

### Pure Web
- [paulmillr/chokidar](https://github.com/paulmillr/chokidar) — v5 Nov 2025, ESM-only, node 20+
- [chokidar - npm](https://www.npmjs.com/package/chokidar)
- [vercel/next.js Discussion #10725: next dev watch all files?](https://github.com/vercel/next.js/discussions/10725)
