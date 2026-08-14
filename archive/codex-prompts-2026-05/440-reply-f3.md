**DISAGREE-cite (F3 🔴 Critical security importance 6/10 — `sha256-<INLINE_STYLE_HASH>` suggestion)** — 建议的 `'sha256-<INLINE_STYLE_HASH>'` 方案对 Radix UI Dialog 架构不可行，接受 suggestion 将完全破坏 Dialog 渲染功能。

## 技术不可行性

**Radix Dialog 的运行时样式注入是动态计算的，无法预先哈希：**

- Radix Dialog 内置依赖 [`react-remove-scroll`](https://github.com/theKashey/react-remove-scroll)，在 Dialog 打开时根据**当前视口尺寸、滚动条宽度、当前 scroll position** 动态生成 inline `<style>` tag。
- 这些 CSS 字符串在不同客户端/设备/页面状态会有不同输出（例如 macOS 与 Windows 滚动条宽度不同 → 不同的 `padding-right` → 不同的 hash）。
- `'sha256-<hash>'` CSP 指令要求**精确**匹配字节序列；任何字符差异都会被阻塞。

**Suggestion 中的 `<INLINE_STYLE_HASH>` 是 placeholder，并非可填值**。Argus 自身也无法枚举所有可能的 hash，因为它们在浏览器运行时才计算。

## 已记录的架构来源（PR body + Issue #439 显式说明）

- [radix-ui/primitives#2057](https://github.com/radix-ui/primitives/issues/2057)：`react-remove-scroll injects styles at runtime`
- [radix-ui/primitives discussion#3130](https://github.com/radix-ui/primitives/discussions/3130) 原话："If your application is rendered on the client side or a certain component needs to be, you're out of luck unless you're willing to weaken your CSP"
- [vitejs/vite#11862](https://github.com/vitejs/vite/issues/11862)：Vite HMR 同样需要 `'unsafe-inline'`（影响 devCsp）
- [v2.tauri.app/security/csp](https://v2.tauri.app/security/csp/)：Tauri 2 的 nonce/hash 注入只覆盖 bundled assets，不覆盖运行时 DOM mutation

## Out of scope 已在 PR body 标记

PR body "Out of scope (Tier-3 / deferred)" 段已显式列出：

> Removing 'unsafe-inline' from style-src (blocked by Radix architecture; would require Base UI migration)
> Compile-time nonce/hash injection for Radix runtime DOM mutations (Tauri's nonce injection covers bundled assets, not runtime mutations)

Issue #439 "What this Tier-2 step does NOT do" 段同样显式记录此约束。

## 决定路径

接受 suggestion 会使 Radix Dialog 在 production CSP 下完全无法打开（CSP 阻塞 react-remove-scroll 的样式注入 → Dialog 进入但 portal/overlay 样式不应用 → 视觉破坏 + 滚动锁失效）。违反 Mercury CLAUDE.md "不实施会破坏现有功能的变更" 原则。

Tier-3 迁移到 Base UI（Radix 官方继任，原生 nonce 支持）是架构级变更，已在 #427 v2 backlog 后续 follow-up 中跟踪。当前 Tier-2 step 已实现可行范围内最大化 CSP 收紧（dev/prod 分离 + 4 个 defense-in-depth 指令），并在 commit 前通过双 reviewer 验证。

importance 6/10 已 considered — fix 本身不可行，因此这是 DISAGREE-cite 而非 nit-loop（S130 lesson 30）。
