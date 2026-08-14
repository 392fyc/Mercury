DISAGREE-cite: `mercury-gui/` 是 Mercury-internal tooling (Phase 6 Tauri 2 桌面 shell)，不是 external-project adapter。CLAUDE.md `## MUST` 在 200-LOC 规则后明确说明: "This rule does NOT apply to `mercury-gui/` — the Phase 6 GUI is Mercury-internal tooling (a Tauri 2 desktop shell, not an external-project adapter), so it has no LOC cap (size by need)." 仅 `adapters/<vendor-name>/` 下的 external-project mounts 受 200-LOC 限制。

S126 documented pattern: 前 3 个 mercury-gui PR (#421 / #424 / #425) 都收到过同样的 LOC nit class — 每次 DISAGREE-cited via CLAUDE.md line 70 exemption。S125 Argus iter 还专门 acknowledge 了此 carve-out。本 PR (#435) 继承同一治理 lineage。

Structural split 作为可选 v2 enhancement 已 tracked 在 #427 backlog 第 7 项 (`mercury-gui/ structural split exploration`)，明确 LOW confidence。本 PR 范围仅限 #434 gh auth preflight 实现。
