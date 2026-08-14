# Consolidated design intent for Argus iter-3 — PR #440 (#439 CSP Tier-2)

All 3 inline review threads (F1 + F2 + F3) have been auto-resolved on iter-2 and there is no outstanding actionable feedback. This comment consolidates the rationale for the convergence so iter-3 has the full context.

## Convergence summary

| Gate | Status | Evidence |
|---|---|---|
| All 3 inline threads | RESOLVED (auto by iter-2) | GraphQL `reviewThreads { isResolved }` → 3/3 true |
| Status checks | SUCCESS | Type Check + Lint + Docstring Coverage = COMPLETED/SUCCESS |
| mergeable | MERGEABLE | `gh pr view --json mergeable` |
| Pre-commit dual-verify | PASS | Claude code-reviewer 0/0/0/3 Nit + Codex sync-audit 0/0/1/0 |
| Bundle size | UNCHANGED | 294.31 kB JS, 31.66 kB CSS — config-only change |

## Design intent (recap for iter-3)

### F1 — `style-src 'unsafe-inline'` 保留 (Minor advisory)

`'unsafe-inline'` 是 Radix UI Dialog 当前的最小必要范围，无法在 SPA + Tauri 2 上下文下消除：

- Radix Dialog → `react-remove-scroll` → 运行时注入 inline `<style>` ([radix-ui/primitives#2057](https://github.com/radix-ui/primitives/issues/2057))
- SPA 无法预生成 per-render nonce ([radix-ui/primitives discussion#3130](https://github.com/radix-ui/primitives/discussions/3130))
- Tauri 2 nonce 注入只覆盖 bundled assets，不覆盖运行时 DOM mutation ([v2.tauri.app/security/csp](https://v2.tauri.app/security/csp/))

迁移到 Base UI 是 Tier-3 架构变更，已在 PR body `Out of scope` 段标记，将作为单独 Issue 在 #427 backlog follow-up 跟踪。

### F2 — `devCsp` vs `csp` 环境隔离 (Minor verification)

Tauri 2 的 `csp` 与 `devCsp` 是 schema 级互斥字段，不存在 production 渗透路径：

- [schema.tauri.app/config/2](https://schema.tauri.app/config/2)：两个独立字段，分别注入不同 build target
- [tauri-apps/tauri@cf54dcf](https://github.com/tauri-apps/tauri/commit/cf54dcf9c81730e42c9171daa9c8aa474c95b522)：commit message 明确 "devCsp will be injected only during development"
- `pnpm tauri build` (prod bundle) 使用 `csp`；`pnpm tauri dev` 使用 `devCsp`
- 当前 `cargo test --lib` 55/55 + Tauri build script 接受 `devCsp` 字段已验证 schema 正确性

### F3 — 🔴 Critical security importance 6/10：建议 `'sha256-<INLINE_STYLE_HASH>'` (DISAGREE-cite)

Argus 的 suggestion 中 `<INLINE_STYLE_HASH>` 是 placeholder，**实际无法填值** — Radix `react-remove-scroll` 在运行时根据视口尺寸/滚动条宽度/scroll position 动态计算 CSS 字符串。不同设备/状态的 hash 不同，无法在构建时预先计算。

接受 suggestion 会使 Radix Dialog 在生产环境完全无法打开（CSP 阻塞 overlay 样式 → 视觉破坏 + 滚动锁失效）。已在 PR body "Out of scope" 段显式记录此约束。importance 6/10 已 considered；fix 本身不可行，因此 DISAGREE-cite 而非 nit-loop。

## 请求

3 个 inline threads 已全部 resolved，所有检查通过，DISAGREE-cite 理由完整 + 源引用充分。请 Argus iter-3 在确认无新 findings 后发出 APPROVED 决定，使 PR 进入 mergeable 状态。

/argus review
