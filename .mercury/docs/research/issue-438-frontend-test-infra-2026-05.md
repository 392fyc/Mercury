---
issue: 438
parent: 427
title: "mercury-gui 前端测试基建选型 — scoping + decision ADR (Vitest + jsdom + Testing Library + Tauri mockIPC)"
date: 2026-05-25
status: PROPOSAL
decision_authority: User
verdict: "推荐栈 = Vitest@4.1.7 + jsdom@29.1.1 + @testing-library/react@16.3.2 (+ jest-dom/user-event) + @vitest/coverage-v8@4.1.7 + Tauri 官方 mockIPC；技术默认明确，两项 policy 待 User 拍板：是否 gate PR on coverage、coverage 阈值数值 vs aspirational。采纳前两个 gate 必过：核实 CI/dev Node 精确版本 ≥20.19（否则 jsdom 降级或改 happy-dom）+ 核实 coverage.thresholds 字段签名。"
relation: "sibling of #427 (mercury-gui frontend backlog)；来源 PR #437 Argus J1 (无前端测试基建) + #436 (dashboardPrefs 落地无测试覆盖)。P3 quality-of-life。"
research_protocol: "所有外部技术事实对照官方文档核实 2026-05-25，带 source URL + 包名@版本；无法核实项标 UNVERIFIED。in-repo 现状基于 develop 分支 mercury-gui/ snapshot。"
---

# Issue #438 — mercury-gui 前端测试基建选型: scoping + decision ADR

> **本 doc 是设计提案 (PROPOSAL)，不实施任何 test 文件、不改 `mercury-gui/` 任何代码或配置、不 dispatch 其它 agent。** Issue #438 是 scoping issue，DoD = 决策并文档化（NON-implementation）。采纳后另开实施 Issue 或按 §7 backfill 顺序逐 feature 落地。

---

## Path conventions (read this first)

- 仓库相对路径一律写 `mercury-gui/...`（如 `mercury-gui/vite.config.ts`、`mercury-gui/src/lib/ghFilter.ts`）。
- 需要机器根的占位符用 `${MERCURY_ROOT}`，本 doc 不出现任何具体机器路径。
- 包版本以 2026-05-25 核实的 snapshot 为准；patch 版本易漂移，文中数字为核实当下的快照，采纳时以 `pnpm` 解析的实际版本为准。

---

## 1. Context

### 1.1 现状（grounding）

`mercury-gui/` 是 Phase 6 GUI 的 Tauri 2 桌面壳前端，技术栈 snapshot：

| 维度 | 现状 |
|------|------|
| 构建工具 | Vite **7.0.4**（`vite.config.ts` defineConfig async） |
| 框架 | React **19.1.0** + react-dom 19.1.0 |
| 语言 | TypeScript **~5.8.3** |
| React 插件 | `@vitejs/plugin-react@^4.6.0` |
| 样式 | tailwindcss 4 + `@tailwindcss/vite` |
| Tauri 桥 | `@tauri-apps/api@^2`（`@tauri-apps/api/core` 的 `invoke`、`@tauri-apps/api/event` 的 `listen`） |
| tsconfig | `moduleResolution: bundler`、`jsx: react-jsx`、`strict`、`noEmit`、alias `@/* → ./src/*`（`vite.config` `resolve.alias` + tsconfig `paths` 已对齐） |
| build 脚本 | `tsc && vite build` |
| Node | 项目 Node.js 20（见 project env）；**CI workflow `setup-node` 当前写 `node-version: "22"`**（见 `.github/workflows/auto-verify.yml`） |
| 测试基建 | **零**（无 runner、无 DOM 环境、无 Testing Library、无 coverage） |

前端规模约 **~2050 LOC**。关键模块：
- 纯逻辑 utils：`src/lib/ghFilter.ts`、`src/lib/filter.ts`、`src/lib/dashboardPrefs.ts`、`src/lib/redact.ts`、`src/lib/elapsed.ts`、`src/lib/state-mapping.ts`、`src/lib/safeOpenUrl.ts`
- hooks（含 Tauri IPC）：`src/hooks/useSnapshot.ts`、`src/hooks/useGitHubData.ts`
- 组件：`src/components/GitHubDashboard.tsx`、`LaneTable.tsx`、`FilterBar.tsx`、`SnapshotView.tsx` 等

### 1.2 为何需要

- **来源 PR #437 Argus J1**：前端无测试基建，复杂逻辑（filter 解析、race-guard hooks、localStorage 持久化、redact 正则）全靠 `tsc` 类型检查与人工 review 兜底。
- **来源 #436**：`dashboardPrefs` 落地时含 `isValidInterval`/save-path 防御性校验等可单测逻辑，但无测试覆盖即合入。
- **redact 前端 defense**（`src/lib/redact.ts`，见 #427 backlog）是 PII 第二道防线（Rust 端 `sanitize_paths` 为第一道），正则正确性强依赖测试。
- 定位 **P3 quality-of-life**：非阻塞性需求，目标是为后续 feature 提供可单测的安全网，并为高 risk 的纯逻辑模块补回归测试。

---

## 2. 逐点决策（8 个 DoD 点）

### DoD-1 Runner: Vitest vs Jest

| 选项 | 评估 |
|------|------|
| **Vitest** ✅ 推荐 | Vite 项目天然选择：复用现有 `vite.config.ts`（含 `resolve.alias`、`@vitejs/plugin-react`、tailwind 插件），零额外 transform 配置。peer dep `vite: ^6 \|\| ^7 \|\| ^8` → **兼容 Vite 7**；无 React peer dep（React 19 无障碍）。snapshot `vitest@4.1.7`。 |
| Jest | 在 Vite/ESM/TS 栈需额外 `ts-jest`/`babel-jest` + 手工桥接 alias（`moduleNameMapper`）+ 重建 Vite 插件行为（tailwind/react transform），官方不推荐用于 Vite 项目。维护成本高，与 `bundler` moduleResolution 易错配。 |

**决策：Vitest@4.1.7。** rationale：配置复用 = 最小新增面，与现有 `tsc && vite build` 流程正交并存。Source: https://vitest.dev/guide/

### DoD-2 DOM 环境: jsdom vs happy-dom

| 选项 | 评估 |
|------|------|
| **jsdom@29.1.1** ✅ 推荐（默认） | 更 browser-faithful，API 缺口少；React Testing Library / Tauri mock 示例均以 jsdom 为基准。**⚠️ jsdom 29 要求 Node `^20.19.0 \|\| ^22.13.0 \|\| >=24`**（见 §5 gate）。 |
| happy-dom@20.9.0 | 更快，但 API 有缺口（部分 DOM/Web API 实现不全），对边缘场景（如某些 event/clipboard/range）可能 false negative。 |

**决策：jsdom@29.1.1（默认），但以 §5 Node 版本 gate 为前提。** 配置 `test.environment: "jsdom"`。rationale：~2050 LOC 规模下测试运行时间不是瓶颈，browser 保真度优先于速度。**条件分支**：若 §5 gate 发现 CI/dev Node < 20.19，则二选一 —— (a) 降 jsdom 至 25.x（兼容更老 Node），或 (b) 改用 happy-dom@20.9.0。Source: https://vitest.dev/guide/environment

### DoD-3 Testing-library bundle

React 19 已被 RTL 16.x 适配（**权威依据 = `@testing-library/react@16.3.2` 的 peerDeps 显式声明 `react: ^18.0.0 || ^19.0.0` + `react-dom: ^18.0.0 || ^19.0.0`**；RTL 16.x 已对齐 React 19 的 `act` 来源变化 —— `act` 改由 `react` 包直接导出，不再走 `react-dom/test-utils`）。推荐 bundle：

| 包 | 版本 | 角色 |
|----|------|------|
| `@testing-library/react` | 16.3.2 | React 组件渲染/查询（`render`/`screen`） |
| `@testing-library/dom` | 10.4.1 | RTL 的 peer dep（查询引擎） |
| `@testing-library/jest-dom` | 6.9.1 | DOM matchers（`toBeInTheDocument` 等）；用 `@testing-library/jest-dom/vitest` 入口挂载 |
| `@testing-library/user-event` | 14.6.1 | 真实用户交互模拟（优于 fireEvent） |

**决策：上述 4 包全装。** cleanup 策略：开 `test.globals` 时 Vitest 自动 cleanup，OR 在 setupFile 手动 `afterEach(cleanup)`（见 DoD-4 决策）。Source: https://testing-library.com/docs/react-testing-library/setup

### DoD-4 Type defs + tsconfig

| 子项 | 决策 |
|------|------|
| globals types | 在 tsconfig 的 `compilerOptions.types` 加 `["vitest/globals", "@testing-library/jest-dom"]`，或在 setupFile 用三斜线 `/// <reference types="vitest/globals" />`。**推荐 `compilerOptions.types`**（集中、可发现）。 |
| `test.globals` | **推荐开启** `test: { globals: true }`，搭配 `vitest/globals` types —— 免每文件 import `describe/it/expect`，且触发 RTL 自动 cleanup。 |
| alias | **无需** `vite-tsconfig-paths`：Vitest 自动继承 vite `resolve.alias`（`@` 别名直接可用）。 |
| 独立 tsconfig.vitest.json | **非强制**。现有 tsconfig（`noEmit` + `strict`）可复用。**唯一需要独立 tsconfig 的场景**：`tsc && vite build` 的 `tsc` 当前 `include: ["src"]`，若 test 文件落在 `src/**/*.test.ts(x)` 内会被 production `tsc` 编译/检查 —— 此时需用 tsconfig `exclude` 排除 `**/*.test.*` / `**/*.spec.*`，或拆一个 `tsconfig.vitest.json` 给 Vitest 单独用。**推荐方案**：在主 tsconfig `exclude` 加测试 glob（最小改动），无需新建 tsconfig.vitest.json。 |

**决策：开 `test.globals` + `compilerOptions.types: ["vitest/globals", "@testing-library/jest-dom"]` + 主 tsconfig `exclude` 测试 glob；不新建 tsconfig.vitest.json。** Source: https://vitest.dev/config/

### DoD-5 CI wiring

现状：`.github/workflows/auto-verify.yml` 是 monorepo 风格（root pnpm，typecheck 当前指向 `packages/core`），`mercury-gui/` 是带自身 `package.json` 的子包。

| 子项 | 决策 |
|------|------|
| `pnpm test` 加入 workflow | **推荐加**：在 `auto-verify.yml` 新增一个 `frontend-test` job（或在现有 job 加 step），`cd mercury-gui && pnpm install --frozen-lockfile && pnpm test`。`test` 脚本 = `vitest run`（非 watch，见 DoD-8 / §CI）。 |
| 是否 gate PR on coverage | **policy 待 User 拍板**（见 §6）。技术上可在 CI 跑 `vitest run --coverage` 并让 `coverage.thresholds` 不达标即 fail。 |
| Node 版本对齐 | **采纳前 gate**：workflow 当前 `node-version: "22"`；若维持 jsdom 29，需确保 CI Node 满足 `^22.13.0`（22.0–22.12 不达标）。见 §5。 |

**决策：加 `frontend-test` job 跑 `vitest run`（typecheck 之外独立）；coverage gate 与否属 policy（§6）。** Source: https://vitest.dev/guide/

### DoD-6 Coverage policy

| 选项 | 评估 |
|------|------|
| 硬阈值 gate | `coverage.thresholds`（如 lines/functions/branches/statements 各设百分比），不达标 CI fail。强约束，但初期空覆盖率下会立刻红，需配合 backfill 节奏。 |
| aspirational（不 gate） | 跑 coverage 出报告但不 fail，作为参考指标，逐步爬升。 |

**决策：技术能力两者都支持；是否 gate + 具体阈值数值 = policy 待 User 拍板（§6）。** 设计推荐：**初期 aspirational**（先建基建 + backfill 高 risk 模块），待覆盖率爬到合理基线后再考虑对 `src/lib/**` 纯逻辑层设硬阈值（如 lines ≥ 80%），而非对全前端一刀切。Source: https://vitest.dev/guide/coverage

### DoD-7 Tauri-specific: 如何测 `@tauri-apps/api/core` invoke

被测的 `useSnapshot` / `useGitHubData` 直接调 `invoke(...)`，测试环境无真实 Tauri runtime。

| 选项 | 评估 |
|------|------|
| **官方 mockIPC** ✅ 推荐 | `@tauri-apps/api/mocks` 的 `mockIPC` + `clearMocks`，**随 `@tauri-apps/api@^2` 提供，无需额外装**（in-repo 核实：`mercury-gui/node_modules/@tauri-apps/api`@2.11.0 内含 `mocks.js` / `mocks.d.ts` / `mocks.cjs`，`@tauri-apps/api/mocks` subpath import 可解析；官方 mocking 文档亦以此 import 为示例）。拦截 IPC 层，按 `(cmd, args)` 返回桩数据。官方 Vitest 示例：jsdom 下 `beforeAll` polyfill `crypto.getRandomValues`，每个测试 `mockIPC(...)`，`afterEach(clearMocks())`。最贴近真实 invoke 路径。 |
| `vi.mock('@tauri-apps/api/core')` | 直接 mock 模块导出。更粗粒度（绕过 IPC 层），适合只想 stub `invoke` 返回值、不关心 event/IPC 细节的纯 hook 测试。备选方案。 |

**决策：默认用官方 `mockIPC`（覆盖 invoke + event listen 场景），纯返回值断言可降级到 `vi.mock`。** 详见 §4 示例片段。Source: https://v2.tauri.app/develop/tests/mocking/

### DoD-8 Backfill priority

见 §7（独立章节，risk/value 排序）。**决策：纯逻辑 utils 优先 → hooks 次之 → 重 UI 组件最后。**

---

## 3. 推荐栈汇总表

| 包@版本 (snapshot) | 角色 | 安装位置 |
|---|---|---|
| `vitest@4.1.7` | test runner | devDependencies |
| `jsdom@29.1.1` | DOM 环境（默认，受 §5 Node gate 约束） | devDependencies |
| `@vitest/coverage-v8@4.1.7` | coverage provider（V8，v3.2.0+ 精度与 istanbul 持平，无需插桩） | devDependencies |
| `@testing-library/react@16.3.2` | React 组件渲染/查询 | devDependencies |
| `@testing-library/dom@10.4.1` | RTL peer（查询引擎） | devDependencies |
| `@testing-library/jest-dom@6.9.1` | DOM matchers（`/vitest` 入口） | devDependencies |
| `@testing-library/user-event@14.6.1` | 用户交互模拟 | devDependencies |
| `@tauri-apps/api/mocks`（随 `@tauri-apps/api@^2`；in-repo 核实 @2.11.0 含 `mocks.js`/`mocks.d.ts`） | Tauri IPC mock（`mockIPC`/`clearMocks`） | **无需额外装** |

> 版本均为 2026-05-25 核实快照；patch 易漂移，采纳时以 `pnpm` 实际解析为准。

**配置落点（采纳后实施，本 PROPOSAL 不写）**：
- `vite.config.ts` 或新建 `vitest.config.ts`：`test: { environment: "jsdom", globals: true, setupFiles: ["./src/test/setup.ts"], coverage: { provider: "v8", ... } }`。复用 vite 主配置（plugins/alias）。
- setupFile（如 `src/test/setup.ts`）：`import "@testing-library/jest-dom/vitest";` + Tauri `crypto` polyfill（若需）。
- `package.json` scripts：`"test": "vitest run"` + `"test:watch": "vitest"` + `"test:coverage": "vitest run --coverage"`。
- tsconfig：`compilerOptions.types` 加 globals；主 tsconfig `exclude` 测试 glob。

---

## 4. Tauri invoke mocking 方案（官方 mockIPC + crypto polyfill）

官方 Tauri 2 Vitest 模式（Source: https://v2.tauri.app/develop/tests/mocking/）。下方为**示例片段，仅作设计说明，本 PROPOSAL 不落盘为 test 文件**：

```ts
// 示例（采纳后落地，本 doc 不实施）
import { beforeAll, afterEach, expect, test } from "vitest";
import { mockIPC, clearMocks } from "@tauri-apps/api/mocks";
import { invoke } from "@tauri-apps/api/core";

beforeAll(() => {
  // jsdom 下 Tauri 内部依赖 crypto.getRandomValues，需 polyfill
  // configurable: true —— 较新 jsdom 可能已定义 window.crypto，缺此项会在二次 defineProperty 抛错
  Object.defineProperty(window, "crypto", {
    configurable: true,
    value: {
      getRandomValues: (buf: Uint8Array) => {
        for (let i = 0; i < buf.length; i++) buf[i] = Math.floor(Math.random() * 256);
        return buf;
      },
    },
  });
});

afterEach(() => {
  clearMocks();
});

test("read_lanes 返回桩数据", async () => {
  mockIPC((cmd, _args) => {
    if (cmd === "read_lanes") return [{ id: "main", label: "main" }];
    return undefined;
  });
  await expect(invoke("read_lanes")).resolves.toEqual([{ id: "main", label: "main" }]);
});
```

- 对 `useSnapshot`：`mockIPC` 需覆盖 `read_jobs_by_lane` / `read_lanes` / `read_roster` 三个 cmd（hook 内 `Promise.all` 并发），并验证 reqId race-guard（连续 refresh 旧响应不覆盖新状态）。
- 对 `useGitHubData`：`mockIPC` 覆盖 `check_gh_auth` 预检 + `fetch_gh_dashboard`；验证 auth 失败短路 + authError/error 互斥清除逻辑。
- `useSnapshot` 还订阅 `@tauri-apps/api/event` 的 `listen("mercury:data-changed")` —— 若测事件驱动 refresh，需额外用 `mockIPC` 处理 event 注册/触发或 `vi.mock('@tauri-apps/api/event')` 提供可控的 listen/emit。
- 备选：`vi.mock('@tauri-apps/api/core', () => ({ invoke: vi.fn() }))` 用于只断言返回值的轻量 hook 测试。

---

## 5. 采纳前 gate（必过）

> 以下两点必须在采纳/实施前显式核实；任一不满足则触发对应降级分支。

### Gate-1 — 核实 CI/dev Node 精确版本 ≥ 20.19（jsdom 29 硬约束）

- **事实（核实，Source: https://vitest.dev/guide/environment + jsdom npm）**：jsdom 29 要求 Node `^20.19.0 || ^22.13.0 || >=24`。
- **风险点**：`.github/workflows/auto-verify.yml` 当前 `node-version: "22"` —— `setup-node` 的 `"22"` 会解析为最新 22.x，通常满足 `^22.13.0`，但**不保证**（若 runner pin 到 22.0–22.12 则失败）；本地 dev Node 20 必须是 ≥20.19。
- **in-repo 佐证**：`mercury-gui/package.json` 的 `@types/node` floor 是 `^22.10.5`（22.10 < 22.13），说明开发基线类型定义对应的 Node 系列**可能低于 jsdom 29 的 22.13 下限** —— 这是站内直接相关的信号，进一步支持下方"显式 pin Node 版本"的建议（类型 floor 与运行时 Node 应一并对齐到 ≥22.13 或 ≥20.19）。
- **决策分支**：
  - Node ≥ 20.19（且 CI 22.x ≥ 22.13）→ jsdom@29.1.1 直接采用。
  - Node < 20.19 / CI 22.0–22.12 → 二选一：(a) jsdom 降至 25.x（更宽 Node 兼容），或 (b) 改用 happy-dom@20.9.0。
- **建议**：把 workflow `node-version` 显式 pin 到 `"22.13"` 或 `"20.19"`（去除浮动），并在采纳 PR 里核实 dev 环境实际 Node。**标记 UNVERIFIED：jsdom 29 的精确 Node 要求 vs 实际 CI/dev Node —— 须采纳前核实。**

### Gate-2 — 核实 `coverage.thresholds` 字段签名

- 配 coverage 阈值前对照 https://vitest.dev/config/#coverage-thresholds 核实字段（`lines`/`functions`/`branches`/`statements`、`perFile`、`autoUpdate`、glob-level 阈值等）的完整签名，避免配错 key 静默失效。**标记 UNVERIFIED：`coverage.thresholds` 完整字段签名 —— 配置时须对照官方核实。**

---

## 6. Policy 待 User 拍板项

> 技术栈给明确推荐（§2/§3 已定）；以下两项是**政策选择**，设计给推荐但留 User 决策。

| Policy 项 | 技术默认/能力 | 设计推荐 | User 决策 |
|---|---|---|---|
| **是否 gate PR on coverage** | Vitest 支持 CI `vitest run --coverage` + `coverage.thresholds` 不达标即 fail | **初期不 gate**（aspirational），基建 + backfill 稳定后再对 `src/lib/**` 纯逻辑层启用 gate | ☐ 待拍板 |
| **coverage 阈值数值 vs aspirational** | `coverage.thresholds` 可设任意百分比；可全局或 per-glob | **aspirational 起步**；若设硬阈值，建议先只对 `src/lib/**`（纯逻辑）设 lines ≥ 80%，不对组件层一刀切 | ☐ 待拍板（含阈值数值） |

其余 7 个 DoD 点（runner/DOM/TL bundle/types/CI 是否加 test job/Tauri mock/backfill 顺序）均为**技术决策**，本 ADR 已给明确推荐，无需 User 政策介入（除 §5 两个核实 gate）。

---

## 7. Backfill priority（risk/value 排序）

基建落地后的回填顺序，原则：**纯逻辑确定性高、ROI 最大者先；含 IPC/异步/race 的 hooks 次之；重 UI 组件最后**（render 测试维护成本高、价值密度低）。

| 优先级 | 模块 | 类型 | risk/value rationale |
|---|---|---|---|
| **P0** | `src/lib/redact.ts`（`redactHomePaths`） | 纯逻辑 | **PII 安全第二道防线**，正则覆盖 Windows/POSIX/macOS 三类 home 路径 + 递归 object/array；正则边缘 case（如引号边界 `[^\\/"]`）错误 = 隐私泄露。最高 risk。 |
| **P0** | `src/lib/ghFilter.ts`（`parseGhFilter`/`matchesIssue`/`matchesPR`） | 纯逻辑 | 分支多（label/state/lane/text 前缀解析 + 空值丢弃 + AND 语义 + `state:draft` 特例），用户可见行为强依赖正确性；纯函数易测、ROI 高。 |
| **P1** | `src/lib/dashboardPrefs.ts`（`isValidInterval`/load/save） | 纯逻辑 | localStorage try/catch 降级 + interval 校验防御（#436 引入但无覆盖）；需 mock `localStorage`（jsdom 提供）。 |
| **P1** | `src/lib/filter.ts` | 纯逻辑 | 与 ghFilter 同类的 filter 解析/匹配，纯函数。 |
| **P1** | `src/lib/state-mapping.ts` / `src/lib/elapsed.ts` / `src/lib/safeOpenUrl.ts` | 纯逻辑 | 确定性映射 / 时间计算 / URL 安全校验，低成本高确定性；`safeOpenUrl` 带安全语义（防 `javascript:` 等）应优先于另两者。 |
| **P2** | `src/hooks/useGitHubData.ts` | hook + IPC | reqId race-guard + auth 预检短路 + authError/error 互斥清除逻辑复杂，bug 风险高；需 `mockIPC` + `renderHook`。 |
| **P2** | `src/hooks/useSnapshot.ts` | hook + IPC + event | reqId race-guard + `Promise.all` 并发 + file-watcher `listen` 订阅/cleanup（StrictMode 双调用 race）+ 60s tick；最复杂 hook，需 `mockIPC` + event mock + fake timers。 |
| **P3** | `src/components/GitHubDashboard.tsx` / `LaneTable.tsx` | 重 UI | 集成层，render 测试维护成本高；优先做关键交互（filter 输入 → 列表过滤、refresh 按钮 → loading 态）的窄 smoke，而非全量快照。 |
| **P3** | 其余组件（`FilterBar`/`SnapshotView`/`StateBadge`/`IssueRow` 等） | UI | 价值密度低，按需。 |

**建议实施波次**：波次 A = P0+P1 纯逻辑层（基建价值即时兑现，CI 可对此层先设 aspirational coverage）；波次 B = P2 hooks（验证 Tauri mockIPC 方案成立）；波次 C = P3 组件 smoke（按需）。

---

## 8. Proposal-only 声明

- 本 ADR **不实施任何 test 文件**、不创建 `vitest.config.ts` / setupFile、不改 `package.json` / tsconfig / `vite.config.ts` / CI workflow，不安装任何依赖。
- 采纳后路径：另开**实施 Issue**（建议 child of #438），按 §7 波次 A→B→C 逐步落地；或按单个 feature PR 顺带回填其触及模块的测试。
- 实施 Issue 第一步即跑 §5 两个 gate（Node 版本核实 + coverage.thresholds 字段核实），再装依赖。
- §6 两个 policy 项需 User 在采纳/实施前拍板（CI 是否 gate coverage + 阈值）。

---

## 9. Sources

- Vitest 指南（runner/CI `vitest run`）: https://vitest.dev/guide/
- Vitest test environment（jsdom vs happy-dom，`test.environment`）: https://vitest.dev/guide/environment
- Vitest coverage（`@vitest/coverage-v8`，V8 vs istanbul）: https://vitest.dev/guide/coverage
- Vitest config（globals types / alias 继承 / 配置字段）: https://vitest.dev/config/
- Vitest coverage thresholds 字段（采纳时核实，UNVERIFIED 完整签名）: https://vitest.dev/config/#coverage-thresholds
- React Testing Library setup（cleanup / jest-dom / React 19 适配）: https://testing-library.com/docs/react-testing-library/setup
- Tauri 2 mocking（`mockIPC` / `clearMocks` / crypto polyfill）: https://v2.tauri.app/develop/tests/mocking/

> **UNVERIFIED 汇总**：(1) jsdom 29 精确 Node 要求 vs 实际 CI/dev Node —— §5 Gate-1，须采纳前核实；(2) `coverage.thresholds` 完整字段签名 —— §5 Gate-2，配置时对照官方核实。
