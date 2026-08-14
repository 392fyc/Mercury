DISAGREE-cite: 同 F1 (comment 3282925652) — `mercury-gui/` 享有 CLAUDE.md line 70 mercury-gui exemption (Phase 6 internal tooling, no LOC cap)。S126 已建立 documented pattern (3 个 prior PRs DISAGREE-cited)。

将 `GhAuthStatus` / `parse_gh_account` / `check_gh_auth` 拆到 `gh_auth.rs` 单独 module 作为可选 follow-up 已记录在 #427 backlog item 7。本 PR 优先保持与现有 `fetch_gh_dashboard` / `GhSnapshot` 在同一文件的耦合度 (两个命令共享 `gh` CLI + `GH_SUBPROCESS_TIMEOUT_SECS` + `redact_home` + `GhCacheState` 上下文)，符合 cohesion-over-extraction 原则。

如果 structural split 工作启动 (#427 item 7)，gh_dashboard.rs 会一并被纳入拆分范围，作为系统级 refactor 而非单 PR 临时调整。
