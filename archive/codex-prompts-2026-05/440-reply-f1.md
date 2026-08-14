**DISAGREE-cite (F1 CSP 放宽项 advisory)** — `style-src 'unsafe-inline'` 已是当前 Radix UI Dialog 依赖的最小必要范围。

PR body 与 Issue #439 显式记录了这条约束及其架构来源：

- Radix Dialog 通过 `react-remove-scroll` 在 SPA 运行时动态注入 inline styles（[radix-ui/primitives#2057](https://github.com/radix-ui/primitives/issues/2057)）。
- SPA 上下文无法预生成 per-render nonce（[radix-ui/primitives discussion#3130](https://github.com/radix-ui/primitives/discussions/3130) 原话："If your application is rendered on the client side... you're out of luck unless you're willing to weaken your CSP"）。
- Tauri 2 的 nonce 注入只覆盖 bundled assets，不覆盖运行时 DOM mutation（[v2.tauri.app/security/csp](https://v2.tauri.app/security/csp/) 验证）。

后续迁移路径已在 PR body **Out of scope** 段标记：

> Removing 'unsafe-inline' from style-src (blocked by Radix architecture; would require Base UI migration)
> Compile-time nonce/hash injection for Radix runtime DOM mutations

迁移到 Base UI 是架构级变更，将作为单独 Issue 在 #427 backlog follow-up 中跟踪。当前 Tier-2 step 范围已在 commit 前充分讨论 + 双 reviewer 验证（Claude code-reviewer PASS 0/0/0/3 Nit + Codex sync-audit PASS 0/0/1/0），无需进一步变更。
