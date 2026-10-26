# Adapters

外部项目适配层。每个 adapter 只做接口转换，不包含业务逻辑。

## 规范

```
adapters/
  {project-name}/
    README.md           # 挂载说明: 挂载了什么、为什么、适配了什么
    adapter.ts 或 .py   # 接口转换代码
    UPSTREAM.md         # 上游版本记录、已知不兼容项
```

## Adapters

| Adapter | Description |
|---------|-------------|
| `playwright-mcp/` | Config-gate wrapper mounting microsoft/playwright-mcp (Apache-2.0) as a pinned MCP server; enforces ADR §4.2 security red lines |
| `html-plan/` | Generates `harness/modules/plan/skills/html-plan-zh/` from the pinned submodule `modules/claude-plugins-community` (runtime localisation table, patch, overlays); `--check` compares content with line endings normalised (#668) |
| `superpowers/` | Generates `harness/modules/subagent-dev/skills/mercury-subagent-driven-development/` from overlays checked against the pinned submodule `modules/superpowers` (#668) |
| `caveman/` | Generates `harness/modules/caveman/` (`skills/caveman-toggle/SKILL.md`, `files/CLAUDE.local.md.example`) from overlays checked against the pinned submodule `modules/caveman` (#668) |

## 约束

- 适配层不超过 200 行。超过说明耦合过深，需重新评估挂载方式。
- 挂载方式分三类受治理的模式（详见 `.mercury/docs/DIRECTION.md` §四"挂载方式（三类并列）"）：
  1. **git submodule（默认）** — 挂载到 `modules/` 目录，钉住提交；Mercury 的改动只放在 `adapters/<上游>/`（补丁、`overlay/`、`generate.py`），下发文件由 `generate.py` 生成，`--check` 在 CI 中比对。
  2. **uvx git+SHA runtime-only** — 经 `uvx --from git+<repo>@<SHA>` 引用，Phase 2 ADR `pixel-animation-workflow` §7.2.1 引入的有限例外（首例为 `adapters/gpt-image-2/`，已随 #586 移除）；仍须满足 `CLAUDE.md` §"Cherry-pick protocol"（含 §6 SHA verification）。
  3. **npm-version-pinned MCP server（runtime-only）** — runtime-only 的 MCP server 经 npm 按确切版本挂载（**禁止 `@latest`**），首例 `adapters/playwright-mcp/`（playwright-mcp / Issue #154，ADR Mercury_KB: `04-research/mercury/issue-154-web-automation-2026-05.md` §5.1）。
- 模式 2、3 均须满足 license gate（仅 permissive）+ provenance（manifest + UPSTREAM.md）+ drift 监控（`scripts/upstream-drift-check.sh`）。
