---
issue: 446
parent: 427
sub_item: "7/8"
title: "mercury-gui 单包 vs workspace 拆分 — Phase 1 设计 ADR (Tauri 2 桌面应用结构评估)"
date: 2026-05-24
status: PROPOSAL
decision_authority: User
verdict: "NO-GO (推荐 Option 1 — 保持单包 + CLAUDE.md GUI-exemption)。拆分的唯一原始动机是消除 Argus LOC nit，但 exemption 已是正确且 working 的机制，DISAGREE-cite 化解已验证 3 次 (PRs #421/#424/#425)。拆分成本确定 (无官方迁移指南、tauri.conf.json + before*Command + PostCSS/shadcn aliases + lockfile + CI 路径全需人工改)，单 app 收益推断性 (共享 target/ + 单 Cargo.lock 对单 binary crate 无实益)。现状已是 thin-binary + lib crate + data/ 子模块 + 前端 components/lib/hooks/ui 分目录，目录边界已整齐。给出明确重评触发条件。"
relation: "build-on #427 sub-item 7 (LOW confidence — 'Argus findings were thin; investment may not pay off')；引用 CLAUDE.md GUI-exemption (Phase 6 GUI 豁免 200-LOC adapter cap)；引用 Cherry-pick 协议 Category A scaffolding carve-out (create-tauri-app)"
research_protocol: "所有外部技术事实对照官方文档核实 2026-05-24；未核实项标 UNVERIFIED + 来源 URL。in-repo LOC / path 引用基于 develop tip。"
---

# Issue #446 — mercury-gui 结构拆分: Phase 1 设计 ADR

> **本 doc 是设计提案 (design proposal / PROPOSAL)，不实装、不改 `mercury-gui/` 任何代码、不 dispatch 其它 agent。** 决策权在 User。本 ADR 给出独立 verdict + rationale + 重评触发条件；若 User 选择拆分，须另开实施 Issue。

---

## Path conventions (read this first)

- 本 doc **不写任何具体机器路径**。repo 内文件一律用 repo 相对形式 `mercury-gui/...` 表达。
- `${MERCURY_ROOT}` 是文档占位符 (非运行时已注入的 env var)，指 Mercury 安装父目录 (具体值由各 install 决定；Windows 团队按 CLAUDE.md "Install to D drive" policy)。本 doc 正文不需要引用机器根，仅在确需时用此占位符。
- in-repo LOC / 文件引用基于 develop tip (本 ADR 起草时 git status 记录的 develop HEAD)。

---

## 0. Executive Summary / 结论

**Verdict: NO-GO — 推荐 Option 1 (保持单包 + CLAUDE.md GUI-exemption)。**

一句话理由: 拆分的唯一原始动机是"永久消除 Argus LOC nit"，但 GUI-exemption (§1.2 定义) 已经是为此设计的正确机制、且经 PRs #421/#424/#425 三次 DISAGREE-cite 验证可化解；拆分成本确定 (无官方迁移指南、多处人工改造 + 回归面)，而单 app 场景的拆分收益是推断性的 (共享 target/ + 单一 Cargo.lock 对**单一** binary crate 无实际收益)。现状已是 Tauri 2 默认推荐的 thin-binary + lib crate 形态，且 `src-tauri/src/data/` 子模块与前端 `src/components/`/`src/lib/`/`src/hooks/`/`src/components/ui/` 分目录边界已整齐——可维护性痛点并不存在。

本 ADR 不否定 workspace 结构本身的价值，而是判断**当前规模 (单 app、~3930 LOC、目录已整齐) 下 ROI 为负**，并给出明确的"何时重评"触发条件 (见 §5)，把这个决策从"主观偏好"转为"条件触发"。

---

## 1. Context

### 1.1 现状 (in-repo evidence)

`mercury-gui/` 是 Phase 6 GUI——一个 Tauri 2 桌面 shell，由 `pnpm create tauri-app` 生成的**单包**结构 (根下单 `src-tauri/` binary crate + `src/` 前端 + `package.json` + `vite.config.ts`)。

总规模 ~3930 LOC，构成:

| 层 | LOC | 关键文件分布 (路径相对 `mercury-gui/`) |
|----|-----|-------------|
| Rust `src-tauri/` | 1878 | `src-tauri/src/gh_dashboard.rs` 591 / `src-tauri/src/data/models.rs` 342 / `src-tauri/src/data/mod.rs` 332 / `src-tauri/src/data/commands.rs` 328 / `src-tauri/src/data/watcher.rs` 118 / `src-tauri/src/data/paths.rs` 82 / `src-tauri/src/lib.rs` 76 / `src-tauri/src/main.rs` 6 / `src-tauri/build.rs` 3 |
| 前端 `src/` | 2052¹ | `src/components/GitHubDashboard.tsx` 358 / `src/components/LaneTable.tsx` 140 / `src/lib/ghFilter.ts` 119 / `src/components/ui/dialog.tsx` 116 / `src/lib/filter.ts` 113 / `src/components/LaneRow.tsx` 111 / `src/hooks/useSnapshot.ts` 101 / ... |

> ¹ **计数口径**: 所有 LOC 为 develop tip 起草时 `wc -l` 快照 (统计换行符数)。无尾换行的文件其"内容行数"比 `wc -l` 多 1,故个别工具 (如按内容行计数的 reviewer) 可能报 +1 (例如 `gh_dashboard.rs` `wc -l` 591 / 内容 592),属口径差非错误;数字随 tip 增删漂移,**以量级与目录结构为准,非逐行精确承诺**。前端 2052 = 全部非 Rust 代码文件 (ts+tsx+js+jsx) 合计 = 总计 3930 − Rust 1878;若仅计 ts+tsx 约 2008 (差额为少量 js/jsx + 口径)。前端文件分布于 `src/components/`、`src/lib/`、`src/hooks/`、`src/components/ui/` 子目录——这正是 §1.1 事实 2 "目录边界已整齐" 的依据。

**两个关键 in-repo 事实 (起草时核实)，直接影响 verdict:**

1. **已是 thin-binary + lib crate 形态。** `src-tauri/src/main.rs` 仅 6 行——`fn main() { mercury_gui_lib::run() }`；`Cargo.toml` 已声明 `[lib] name = "mercury_gui_lib"` + `crate-type = ["staticlib", "cdylib", "rlib"]`。这正是 Tauri 2 官方默认模式 (见 §4.1)，**main→lib 的拆分早已完成**，不是 Option 2 才能带来的收益。
2. **模块边界已整齐。** Rust 侧已有 `data/` 子模块 (`mod.rs`/`models.rs`/`commands.rs`/`watcher.rs`/`paths.rs`)；前端侧已分 `components/`/`lib/`/`hooks/`/`ui/` 目录。"代码混在一起难维护"这个常见拆分动机在此**不成立**。

### 1.2 Motivation (为什么会有 #446)

#446 是 #427 的 sub-item 7/8。动机链:

- Phase 6 GUI chain (PRs #421/#424/#425) 中，Argus 在**连续 3 个 PR** 对 `mercury-gui/` 体量报 LOC nit。
- 三次均经 **CLAUDE.md 的 200-LOC MUST bullet 中 `mercury-gui/` carve-out** 化解 (本 ADR 以下简称该 carve-out 为 **GUI-exemption**;以规则名而非具体行号引用,避免文件增删后行号漂移失真)。原文 (该 MUST bullet `"External-project adapters under \`adapters/<vendor-name>/\` MUST stay under 200 lines"` 邻域): *"This rule also does NOT apply to `mercury-gui/` — the Phase 6 GUI is Mercury-internal tooling (a Tauri 2 desktop shell, not an external-project adapter), so it has no LOC cap (size by need)."* 化解方式是 reviewer thread 里 DISAGREE-cite 该 carve-out。
- 拆分的**唯一原始动机**就是"永久消除 Argus LOC nit"——把 GUI 拆成多个小 crate / package 使任何单一 crate 体量看起来更小，理论上让 Argus 不再触发。

### 1.3 #427 confidence 定位

#427 body 明确把 sub-item 7 标为 **LOW confidence**: *"Argus's findings were thin; investment may not pay off"*。换言之，**提案方自己就对 ROI 存疑**。这是本 ADR 的关键背景——评估不是从中性起点出发，而是从"提案方已标注 LOW confidence"出发，举证责任落在"证明拆分 pay off"一侧。

### 1.4 200-LOC cap 的适用边界 (避免误读)

CLAUDE.md 的 200-LOC 硬约束**只 scope 到 `adapters/<vendor-name>/` 外部项目适配层** (对齐 DIRECTION.md §适配层规范 line 240 + §8-2 line 385 的 `mercury-test-gate` adapter)。`scripts/` 内部工具与 `mercury-gui/` 均**不受** 200-LOC cap 约束。Argus 在 #421/#424/#425 报 LOC nit 正是把 adapter-scoped 规则误施于 `mercury-gui/`——这与历史上对 `scripts/codex-sync-audit.sh` (PR #338) / `scripts/lane-assertion.sh` (PR #346) 的 Argus nit-loop 是**同一类误判**。问题的根因是 reviewer 误施规则，不是 GUI 结构本身有缺陷。

---

## 2. Options

### Option 1 — status quo + CLAUDE.md GUI-exemption (保持单包)

保持 create-tauri-app 生成的单包结构不动。继续靠 GUI-exemption + reviewer thread DISAGREE-cite 化解 Argus 的 LOC nit。

- 不动 `Cargo.toml` / `tauri.conf.json` / `package.json` / `vite.config.ts` / CI。
- 维持现有 thin-binary + lib crate + `data/` 子模块 + 前端分目录形态。
- 化解机制: exemption 已落在 CLAUDE.md，被 OMC 全局 load；reviewer 触发 LOC nit 时 cite GUI-exemption 即 DISAGREE。

### Option 2 — workspace split (拆分为 workspace 结构)

含两个**可独立或组合**的子变体:

**Option 2a — Cargo workspace (Rust 侧拆分)**

把 `src-tauri/` 从单 binary crate 拆为 workspace + 多 member crate。形态示例:

- workspace root `Cargo.toml` 声明 `[workspace] members = [...]`。
- 把现有 `data/` 子模块抽成独立 lib crate (如 `mercury-gui-data`)，`gh_dashboard.rs` 可再抽一个 lib crate；保留 thin binary crate 依赖这些 lib。
- 收益 (官方定性): 共享 `target/` 目录 + 单一 `Cargo.lock`。
- 成本: 每个 crate 独立 `Cargo.toml` + 显式声明 crate 间依赖；Tauri 的 `tauri-build` / 入口约定需重新接线。

**Option 2b — pnpm workspace (前端 / monorepo 侧拆分)**

把前端从单 `package.json` 拆为 pnpm workspace。形态示例:

- root `pnpm-workspace.yaml` 声明 `packages:` glob。
- 拆为 `apps/desktop/` (Tauri app 主体) + `packages/lib/` (可复用前端逻辑，如 `lib/ghFilter.ts` / `lib/filter.ts` / hooks)。
- 收益 (社区定性): 多 app / 多 package 时共享依赖 + 清晰边界；Tauri 官方仓库自身用 `pnpm-workspace.yaml`。
- 成本: `tauri.conf.json` 的 `frontendDist` / `beforeDevCommand` / `beforeBuildCommand` 路径全要改；PostCSS / shadcn aliases / Vite resolve alias / `pnpm-lock.yaml` 位置 / CI 工作目录全要随之改。

**组合**: 2a + 2b 同时做 = 完整 monorepo 化。两者也可单独做。

---

## 3. 对比矩阵

维度评分: ++ 显著优 / + 略优 / 0 中性 / − 略差 / −− 显著差 (相对另一选项)。

| 维度 | Option 1 (单包 + exemption) | Option 2 (workspace 拆分) |
|------|------------------------------|----------------------------|
| **nit-silencing 收益 (原始动机)** | + GUI-exemption 已 working，3 次 DISAGREE-cite 验证可化解 | + 理论上小 crate 让 Argus 不触发，但**未验证** Argus 是否真按单 crate LOC 计数 (UNVERIFIED：可能仍按目录树总量报；该 UNVERIFIED 项**不影响 NO-GO 结论**,见本节末"稳健性"框) |
| **重构风险 / 一次性成本** | ++ 零改动 | −− 无官方迁移指南 (见 §4)；多处人工改 + 回归面大 |
| **对 cherry-pick Category A 影响** | ++ 现状即 create-tauri-app 一次性 scaffold，Category A provenance 干净 | − 拆分后偏离 scaffold 默认布局，未来对 create-tauri-app 升级 diff 的可比性下降 (非 blocker，但增审计摩擦) |
| **对 dual-verify `pnpm build` 影响** | ++ build 链路不变，已知 working | − `pnpm build` / `beforeBuildCommand` / `frontendDist` 路径全需重接 + 重新验证 dual-verify gate |
| **可维护性 (当前规模)** | + 已有 `data/` 子模块 + 前端分目录，边界整齐 | 0 拆 crate 边界更硬，但当前规模下子模块边界已够用，硬边界收益边际 |
| **未来扩展性 (多 app / 大共享 lib)** | − 若未来真要加第二 app，那时才补做拆分 | ++ 预铺 monorepo 结构，第二 app / 大共享 lib 时 ROI 显著 |
| **决策可逆性** | ++ 随时可拆 (拆分是加法，不丢信息) | − 拆回单包要重新合并 + 改回路径，逆向成本同样高 |
| **认知负担 / 上手成本** | + 单包，新人直接 `pnpm tauri dev` | − 多 crate / 多 package 需理解 workspace 拓扑 + 跨 crate 依赖 |

**矩阵读法**: Option 2 的优势集中在**未来扩展性**单一维度，且该维度的前提 (多 app / 大共享 lib) **当前不存在**。Option 1 在成本、风险、build 影响、可逆性上全面占优。nit-silencing 这个原始动机维度上，两者**打平甚至 Option 1 略优** (因为 Option 2 的 nit-silencing 效果本身 UNVERIFIED——Argus 是否真按单 crate 而非目录树总量计数，未经验证)。

> **关键: NO-GO verdict 对该 UNVERIFIED 前提稳健 (两种情形都指向不拆)。** 这个 UNVERIFIED 项 (Argus 计数口径) 只影响"若选择拆分，拆分能否兑现其唯一动机"，**不影响本 ADR 的 NO-GO 结论**:
> - **情形 A — Argus 按单 crate 计数**: 拆分*可能*消除 nit，但 §5.2 已证拆分成本确定、单 app 收益推断性 → 收益 < 成本，仍 NO-GO。
> - **情形 B — Argus 按目录树总量计数**: 拆分*连原始动机都无法兑现* (小 crate 不改变目录树总量) → 更明确 NO-GO。
>
> 因此**无需先做 spike 验证该前提即可下 NO-GO 结论**——spike 只在 User 推翻 verdict、决定走 Option 2 时才需要 (作为止损 gate，见 §6.4(a) + Appendix)。这消除了"关键前提 UNVERIFIED 影响 ADR 可执行性"的顾虑: 决策已可执行 (NO-GO)，验证负担被正确推迟到"假设要拆"的反事实分支。

---

## 4. Research findings (外部技术事实，带 source URL)

> 所有条目核实 2026-05-24。未核实项显式标 UNVERIFIED。

### 4.1 Tauri 2 默认单包，workspace 是 supported 但非推荐

Tauri 2 项目默认是单包结构 (root + `src-tauri/`)。官方承认 Rust workspace 是 supported scenario，但**不主动推荐**，且**无 pnpm workspace 专项迁移指南**。`main.rs` → `lib.rs` 的 `run()` thin-binary + lib 模式已是 create-tauri-app 默认生成模式 (本仓库 `main.rs` 6 行 + `Cargo.toml [lib]` 印证)。`tauri.conf.json` 的 `frontendDist` 接受相对路径。

- Source: <https://v2.tauri.app/start/project-structure/>
- Source: <https://tauri.app/reference/config/> (`frontendDist` 相对路径)

**推论**: Option 2 想从 main→lib 拆分中获得的"thin binary"收益**已经存在**——这部分不是拆分的增量价值。

### 4.2 Cargo workspace 无 LOC 阈值，收益对单 crate 不成立

Cargo workspace **无 LOC 触发阈值**。Rust Book 的定性建议是"as it grows larger"才考虑——即随**crate 数量**增长，而非单 crate 行数增长。workspace 收益 = 共享 `target/` + 单一 `Cargo.lock`；成本 = 每 crate 独立 `Cargo.toml` + 显式跨 crate 依赖声明。对**单一** ~1878 LOC binary crate，官方**无任何拆分要求**。

- Source: <https://doc.rust-lang.org/book/ch14-03-cargo-workspaces.html>
- Source: <https://doc.rust-lang.org/cargo/reference/workspaces.html>

**推论**: 共享 `target/` + 单 `Cargo.lock` 的收益**只在多 crate 时显现**。当前 `src-tauri/` 是单 crate，这两个收益值为 0。

### 4.3 pnpm workspace + Tauri idiomatic，但官方无专项指引，多 app 才显收益

pnpm workspace 与 Tauri 组合是 idiomatic 的 (Tauri 官方仓库自身用 `pnpm-workspace.yaml`；社区有 `apps/` + `packages/` 示例)，但官方**无专项迁移指引**，且收益**只在多 app 场景**显现。

- Source: <https://pnpm.io/workspaces>
- Source: <https://github.com/orgs/tauri-apps/discussions/13941>

### 4.4 迁移坑 (全部需人工处理，无官方迁移指南)

拆分迁移涉及多处人工改造，无官方迁移指南兜底:

- `tauri info` lockfile 检测 bug (Issue #4232；v2 修复状态 **UNVERIFIED**)。
- Tauri CLI 不支持 `--manifest-path`，workspace 下需 `cd` 到正确目录执行。
- `tauri.conf.json` 路径 (`frontendDist`) + `beforeDevCommand` / `beforeBuildCommand` 需随新布局改。
- PostCSS / shadcn aliases / Vite resolve alias 需随 `packages/` 拆分改。
- `pnpm-lock.yaml` 位置变化。
- CI 工作目录 / 路径全需人工改。

- Source: <https://github.com/tauri-apps/tauri/issues/4232>
- Source: <https://github.com/orgs/tauri-apps/discussions/13941>

### 4.5 版本与语法

- tauri 2.x — 精确 patch 版 **VERIFIED 为 2.11.2** (in-repo evidence: `mercury-gui/src-tauri/Cargo.lock` 锁定 `name = "tauri"` / `version = "2.11.2"`;`Cargo.toml` 声明的是 caret `tauri = { version = "2" }`,lockfile 给出实锁 patch 版)。
- `pnpm-workspace.yaml` 用 `packages:` glob 语法。
- Cargo workspace 用 `[workspace] members = [...]`。
- Source: crates.io (精确 patch 版 **UNVERIFIED**)

### 4.6 中立 evidence (规模判断)

在现规模下 (单 app、目录已整齐)，拆分的主要收益是"为未来扩展预铺结构"，**而非解决任何当前痛点**。成本是确定的；单 app 收益是推断性的。只有当未来同仓新增第二个 Tauri app、或出现需要跨 app 复用的大型共享 Rust lib 时，拆分 ROI 才显著上升。

---

## 5. Verdict + Rationale

**Verdict: NO-GO — 推荐 Option 1 (保持单包 + CLAUDE.md GUI-exemption)。**

### 5.1 exemption 已是正确机制 (而非临时绕道)

GUI-exemption **不是** workaround，而是对一个**本就该豁免**的对象的正式表述。200-LOC cap 在 CLAUDE.md 与 DIRECTION.md 中明确 scope 到 `adapters/<vendor-name>/` 外部项目适配层 (§1.4)；`mercury-gui/` 是 Mercury-internal tooling (Tauri 2 桌面 shell，非外部项目适配)，从规则的**立法本意**看就不在 cap 覆盖范围内。Argus 在 #421/#424/#425 的报告是**误施规则**——与历史上对 `scripts/` 内部工具的 nit-loop (PR #338 / #346) 是同一类误判，且 CLAUDE.md 已为后者写明 carve-out 与 authority chain。因此正确的处置是**让 reviewer 学会不误施规则**，而不是**改变被审对象的物理结构去迎合误判**。后者是"为了消除误报而改变正确的事物"，方向倒置。

### 5.2 拆分不 pay off (成本确定 vs 收益推断)

- **收益侧**: §4.2 / §4.3 表明 Cargo + pnpm workspace 的实质收益 (共享 target、单 Cargo.lock、跨 app 复用) **全部需要多 crate / 多 app 前提**，当前单 crate + 单 app 场景下这些收益值为 0 或边际。§4.1 表明想要的 thin-binary 收益**已经存在**。nit-silencing 收益本身 **UNVERIFIED** (§3：未验证 Argus 是否真按单 crate 而非目录树总量计数)。
- **成本侧**: §4.4 列出迁移坑全部需人工处理 + 无官方迁移指南；`pnpm build` / dual-verify gate 需重接 + 重新验证；`tauri.conf.json` + `before*Command` + PostCSS / shadcn aliases + lockfile + CI 路径全需改。这些成本**确定且非平凡**。
- **#427 自标 LOW confidence** (§1.3): 提案方自己已对 ROI 存疑，举证责任在"证明 pay off"一侧，而 evidence 无法支撑。

确定成本 vs 推断收益 + 单一受益维度 (未来扩展性) 的前提当前不存在 = NO-GO。

### 5.3 可维护性痛点不存在

§1.1 事实 2: `data/` 子模块 + 前端 `components/`/`lib/`/`hooks/`/`ui/` 分目录已提供清晰边界。把子模块升级为 crate / package 的硬边界收益在当前规模下是边际的，且引入跨 crate 依赖声明与 workspace 拓扑的认知负担。

### 5.4 决策可逆性偏向 Option 1

拆分是加法操作但逆向成本对称 (拆回要重新合并 + 改回路径)。保持单包**不丢任何信息**——未来任何时点真有需要都能拆；而过早拆分则要立即承担成本却推迟 (甚至可能永不) 兑现收益。"等到有第二个 app 再拆"是严格占优的等待期权 (option value)。

### 5.5 重评触发条件 (把决策从主观转为条件触发)

本 verdict 在以下**任一**条件成立时应重评 (建议届时重开 #446 或新 Issue)，避免因社会证明 / 主观偏好重复争论:

1. **同仓新增第二个 Tauri app** (或第二个需独立打包的前端 app)——此时 pnpm workspace (Option 2b) 的多 app 复用收益首次成立，ROI 转正。
2. **出现需跨 app / 跨 crate 复用的大型共享 Rust lib** (经验阈值建议: 当 `src-tauri/` 中可独立复用的非 Tauri 逻辑 ≥ 一个 crate 的合理规模，或单文件如 `gh_dashboard.rs` 增长到使 `data/` 子模块边界不再够用)——此时 Cargo workspace (Option 2a) 收益成立。
3. **Argus LOC nit 从 advisory 升级为 blocking** 且 GUI-exemption 的 DISAGREE-cite **不再被接受** (即化解机制实际失效，而非仅 reviewer 重复报)——此时才需要用结构拆分作为最后手段。注意: 优先处置应是修正 reviewer 规则适用 (如把 `mercury-gui/` carve-out 写得像 `scripts/` carve-out 一样显式)，结构拆分仍是 last resort。
4. **`src-tauri/` 单 binary crate 编译时间** 因体量增长成为开发瓶颈 (workspace 增量编译收益此时才显现)。

---

## 6. Recommendation (proposal only)

1. **采纳 Option 1**: 不拆分，保持单包。本 ADR 不触发任何实施 Issue。
2. **强化 exemption 的 reviewer 可见性 (低成本、可选)**: 既然根因是 Argus 误施 adapter-scoped 规则,建议 (proposal,非本 ADR 实施) 补强 CLAUDE.md 中 `mercury-gui/` 豁免的经验驱动证据。**核实现状**: CLAUDE.md 的 200-LOC MUST bullet (§1.2 定义的 GUI-exemption 所在 bullet) 已给 `mercury-gui/` 与 `scripts/` **同等结构待遇**——同一 MUST bullet、同一 DIRECTION.md §240/§385 authority chain、同一 "no LOC cap (size by need)" 措辞。真正的 delta 很小: 该 bullet 尾部 "Empirical drivers" 子句目前只列 `scripts/` 的 PR #338/#346,**未列 GUI 的 PRs #421/#424/#425**。建议仅把后者补进 empirical-drivers 清单,使未来 reviewer 一次性看到 GUI 也有经验驱动的 carve-out 而非每次触发 nit。**这比拆分代码便宜一个数量级,且直接命中根因。** 此项是改 CLAUDE.md 的轻量文档动作 (走用户/项目文档直写通道),与拆分无关,**且 pending User 认可** (CLAUDE.md 是项目治理文件)。**该治理动作已落地为可追踪项 #448** (状态 OPEN,本 session 创建;避免"结论已给出但治理动作未落地"),由 User 拍板后执行——本 ADR 不直接改 CLAUDE.md。
3. **记录重评触发条件** (§5.5)，使 #446 可在条件成立时被精准重开，而非凭主观重提。
4. **若 User 推翻本 verdict 选择拆分**: 须另开实施 Issue，并在该 Issue 中要求 (a) 先做 spike 验证 §3 中 "Argus 是否真按单 crate 计数" 的 UNVERIFIED 假设——若 Argus 仍按目录树总量报，则拆分连原始动机都无法兑现，应立即止损；(b) 验证 §4.4 全部迁移坑 + dual-verify `pnpm build` gate 在新布局下通过；(c) 记录 create-tauri-app scaffold 偏离 (Category A provenance 影响，见 §3)。

---

## 7. Sources

- Tauri 2 项目结构 (默认单包 / thin-binary+lib): <https://v2.tauri.app/start/project-structure/>
- Tauri config (`frontendDist` 相对路径): <https://tauri.app/reference/config/>
- Rust Book — Cargo workspaces (as it grows larger): <https://doc.rust-lang.org/book/ch14-03-cargo-workspaces.html>
- Cargo reference — workspaces (`[workspace] members`): <https://doc.rust-lang.org/cargo/reference/workspaces.html>
- pnpm workspaces (`packages:` glob): <https://pnpm.io/workspaces>
- Tauri + pnpm workspace 社区讨论 (apps/+packages/ 示例 + 迁移坑): <https://github.com/orgs/tauri-apps/discussions/13941>
- Tauri Issue #4232 (`tauri info` lockfile 检测 bug；v2 修复状态 UNVERIFIED): <https://github.com/tauri-apps/tauri/issues/4232>
- tauri 精确 patch 版 (2.11.2) — in-repo evidence `mercury-gui/src-tauri/Cargo.lock`;crates.io 包页: <https://crates.io/crates/tauri>

---

## Appendix — UNVERIFIED 清单 (供实施前 spike 核实)

| 项 | 状态 | 核实方式 |
|----|------|---------|
| tauri 精确 patch 版 (2.11.2) | ✅ VERIFIED (in-repo) | 证据来自 `mercury-gui/src-tauri/Cargo.lock` 锁定 `version = "2.11.2"` (不再需 crates.io 直抓) |
| Tauri Issue #4232 在 v2 的修复状态 | UNVERIFIED | 实施前查 issue 当前状态 + 在目标 v2 版本本地复现 `tauri info` |
| Argus 是否按单 crate / 单 package LOC 计数 (而非目录树总量) | UNVERIFIED — **决定 Option 2 nit-silencing 是否成立的关键** | 拆分实施前先做 spike：构造小 crate 看 Argus 是否仍报；若仍按总量报则拆分无法兑现原始动机 |
