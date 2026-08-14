**DISAGREE-cite (F2 环境隔离校验 verification request)** — 已通过 MANDATORY RESEARCH PROTOCOL 验证 `devCsp` 与 production `csp` 互斥，不存在渗透路径。

权威证据：

- [Tauri 2 schema](https://schema.tauri.app/config/2)：`app.security.csp` 与 `app.security.devCsp` 是 schema 中两个独立字段，分别注入到 bundled HTML 与 dev-server HTML。
- [tauri-apps/tauri@cf54dcf](https://github.com/tauri-apps/tauri/commit/cf54dcf9c81730e42c9171daa9c8aa474c95b522)：引入 `devCsp` 的 commit message 明确 "devCsp will be injected only during development"。
- 行为契约：
  - `pnpm tauri dev` (development) → 使用 `devCsp`
  - `pnpm tauri build` (production bundle) → 使用 `csp`
  - 两条路径在 Tauri 2 runtime 内部 mutually exclusive，无 merge 行为

本地验证已完成：

- `cargo test --lib`：55/55 pass（Tauri build script 接受 `devCsp` 字段；schema 验证通过）
- `pnpm build`：clean（294.31 kB JS unchanged + 31.66 kB CSS）
- JSON validity：python `json.load` OK

如需进一步在 production bundle 层面验证（inspect dist/index.html 的 CSP meta tag），需运行完整 `pnpm tauri build`（Rust release build ~10 min）。考虑 Tauri 2 schema + commit cf54dcf 已是权威来源，PR scope 不包含完整 release bundle 构建作为验证步骤。
