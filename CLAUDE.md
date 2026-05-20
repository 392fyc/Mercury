# Mercury — Claude Code

## Identity

Agent: Claude Code
Role definitions: `.claude/agents/{role}.md` (Phase 0 已完成迁移)
Archived: `.mercury/roles/*.yaml` → `archive/roles/`

## Navigation

Read these docs on demand when you need the corresponding information:

| Topic | Path |
|-------|------|
| **Project direction (最高准则)** | `.mercury/docs/DIRECTION.md` |
| **Execution plan** | `.mercury/docs/EXECUTION-PLAN.md` |
| Agent definitions | `.claude/agents/*.md` |
| Role definitions (archived) | `archive/roles/*.yaml` |
| Git branching rules | `.mercury/docs/guides/git-flow.md` |
| GitHub Issues workflow | `.mercury/docs/guides/issue-workflow.md` |
| SoT task workflow (legacy, for reference) | `.mercury/docs/guides/sot-workflow.md` |
| KB directory structure | `.mercury/docs/guides/kb-structure.md` |
| Dispatch prompt templates | `.mercury/templates/` |
| Architecture research (PR #162) | `.mercury/docs/research/issue-158-architecture-evaluation.md` |
| Agent view dispatch convention (multi-lane × bg sessions) | `.mercury/docs/guides/agent-view-dispatch.md` |

## Related Repositories

Mercury 的部分功能跨仓库运作。以下表格记录外部仓库与 Mercury 的关系。

| Repo | Location | Purpose | 关系 |
|------|----------|---------|------|
| **Memory layer (user-level)** | `~/.claude/hooks/` + `~/.claude/scripts/` | mem0 adapter + bridge + flush + session-start/end hooks + cost tracker (#361) | 运行时独立于任何 git 仓库；mem0 Qdrant 数据在 `~/.claude/scripts/mem0-state/`；cost-tracker per-session jsonl 在 `~/.claude/scripts/cost-tracker/` |
| **claude-handoff** | 插件仓库 <https://github.com/392fyc/claude-handoff> | Session handoff / 续接 + `session_chain` SQLite | 作为本地插件挂载在 `~/.claude/settings.json` marketplace |
| **AgentKB (archival-pending)** | `$AGENTKB_DIR` | 旧 Memory 层（Karpathy-style KB），Mercury #252 后被 mem0 取代 | 待归档；salvage 审计见 `.mercury/docs/research/agentkb-fork-salvage-audit-2026-04-17.md` |
| **Mercury_KB** | *(archived)* | 项目专属 Obsidian vault (archived) | 已归档，早于 AgentKB 被取代 |

**跨仓库开发注意事项：**
- `dev-pipeline` 等 skill 假设单仓库工作，跨仓库任务需直接实现
- 用户级 hooks / scripts 变更不走 Mercury PR 流程。相关路径里 `~/.claude` 等价于 `${CLAUDE_CONFIG_DIR:-$HOME/.claude}`；命令示例可任选一种书写，env 形式在多账户 / CI 下更可移植
- 新环境验证: 文件存在性 + 钩子注册 + 库可导入三层检查：
  1. `ls "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks/" "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/scripts/"` 看到 `pre-compact.py`/`session-end.py`/`flush.py`/`mem0_hooks.py`/`mem0_bridge.py`/`cost_tracker.py` 即为 #361 后状态
  2. `grep -c session-end.py "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/settings.json"` 应返回 ≥1（钩子在 SessionEnd matcher 注册）；`grep AGENTKB "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/settings.json"` 应返回 0 行
  3. `python -c "import sys; sys.path.insert(0, r'${CLAUDE_CONFIG_DIR:-$HOME/.claude}/scripts'); import cost_tracker; print(cost_tracker.session_log_path('verify-smoke'))"` 应打印 `cost-tracker/verify-smoke.jsonl` 路径（导入 + 路径解析 OK）
- 安装依赖: `cd "${CLAUDE_CONFIG_DIR:-$HOME/.claude}" && uv sync` 建立 `.venv/` 并装 mem0ai + qdrant-client
- 回滚通道: `MERCURY_MEM0_DISABLED=1` / `AGENTKB_MEM0_DISABLED=1` / `MERCURY_COST_TRACKER_DISABLED=1` / `uv remove mem0ai` 任一即可 no-op 对应路径
- Cost tracker (#361) env vars (canonical 实现见 `~/.claude/scripts/cost_tracker.py` `PRICING` / `_disabled()` / `ceiling_advisory()` / `detect_tier_misuse()`)：`MERCURY_SESSION_COST_CEILING_USD=NN.NN` 触发 statusline 颜色阶梯 (绿 <70% / 黄 70-89% / 红 ≥90%)；`MERCURY_TIER_MISUSE_THRESHOLD` (默认 2000) 控制 opus 小任务 advisory 阈值；`MERCURY_COST_TRACKER_DISABLED=1` 软关 `write_session_summary()` 写路径（statusline 段落另在 hook 内独立 gate）

**用户级变更治理（避免"仓库外漂移"）：**
- **变更记录位置**: 每次修改 `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks/`、`.../scripts/`、`.../settings.json` 时，在 Mercury 内开对应 Issue（类似 #259），在 Issue 下记录"命令清单 + 最终 diff 摘要 + 验证步骤"。Issue 关闭即成为该用户级变更的权威记录
- **验证清单（必须全部通过）**:
  1. `settings.json` JSON 合法（`python -c "import json,os; json.load(open(os.path.expandvars('${CLAUDE_CONFIG_DIR:-$HOME/.claude}/settings.json')))"`）
  2. 每个涉及的 hook 脚本在合成 stdin 下 exit 0（见 #259 PR body 的验证示例）
  3. 相关单测或 smoke test 通过（如 `mem0_bridge_test.py` 7/7）
  4. 一次真实 hook 触发观察无回归
- **回滚步骤**: 所有用户级变更前先 `CC="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"; cp "$CC/settings.json" "$CC/settings.json.backup-pre-<issue>"`；发现回归时 `mv` 回去即可；mem0 层额外可通过 env var 软关
- **环境依赖审计**: 定期跑 `grep -rE "AGENTKB_DIR|\$AGENTKB" "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/"` 确认未遗漏旧路径引用

## MUST

- **Direction first**: all development decisions must align with `.mercury/docs/DIRECTION.md`. When in doubt, consult the direction document.
- **Issue-first workflow**: every task must have a GitHub Issue before work begins. PRs must reference the Issue (`Closes #N` / `Fixes #N` / `Resolves #N` / `Refs #N`). Agent progress updates go on the Issue as comments.
- **Commit at every checkpoint**: every milestone must be committed and pushed.
- **Dual-verify before commit**: every milestone must pass `/dual-verify` (parallel Claude Code deep-review + Codex code-audit) before committing. Do not use `/auto-verify` alone as the pre-commit gate.
- **Web search before SDK/API code**: before writing ANY code that imports an external SDK, references an API signature, or claims a package version, you MUST use WebSearch/WebFetch to verify against the vendor's official documentation. GitHub source code alone is NOT sufficient. If verification is not possible, mark claims as UNVERIFIED.
- **Chinese for milestones**: return milestone completion messages in Chinese.
- **PR to develop**: all code merges into develop must go through a PR. Direct push to develop is forbidden.
- **Install to D drive**: install software to `D:\Program Files`, not C drive.
- **Modular design**: every new feature must be independently detachable. If it cannot be used outside Mercury, the coupling is too deep.
- **External-project adapters under `adapters/<vendor-name>/` MUST stay under 200 lines.** If an external integration needs more than that, rethink the mounting approach. This rule does NOT apply to Mercury-internal tooling under `scripts/` — internal tooling implements the protocol that lives in this repo and isn't trying to mount anything external, so it has no LOC cap (size by need). This rule also does NOT apply to `mercury-gui/` — the Phase 6 GUI is Mercury-internal tooling (a Tauri 2 desktop shell, not an external-project adapter), so it has no LOC cap (size by need). Aligns with `.mercury/docs/DIRECTION.md` §"适配层规范" line 240 (scopes the 200-line rule to the `adapters/{project-name}/` layer) and §8-2 line 385 (calls "adapter ≤200 LOC 是硬约束" specifically about the `mercury-test-gate` adapter); CLAUDE.md previously had a loose phrasing that Argus mis-applied to `scripts/`. Empirical drivers: PR #338 (`scripts/codex-sync-audit.sh` ~360 LOC) and PR #346 (`scripts/lane-assertion.sh` ~440 LOC) both hit Argus nit-loops on the adapter-size finding, requiring iter-3+ escape-hatch / disagree replies before APPROVED.
- **No self-research**: if an external project can solve the problem, mount it via submodule rather than reimplementing.

## DO NOT

- Do not build custom orchestrator layers — use Claude Code native sub-agents and skills.
- Do not guess SDK/CLI APIs from training data.
- Do not install software to C drive.
- Do not commit without running `/dual-verify`.
- Do not create PRs without an associated GitHub Issue.
- Do not build features that assume the model is weak — design for upward compatibility.
- Do not create **external-project adapters** under `adapters/<vendor-name>/` exceeding 200 lines — rethink the mounting approach if this happens. (Mercury-internal tooling under `scripts/` is exempt — see the MUST bullet "External-project adapters under `adapters/<vendor-name>/` MUST stay under 200 lines" above for the carve-out and authority chain.)

## Cherry-pick protocol

When cherry-picking any file from an external project into Mercury, the SAME commit must include:

> See §"Carve-out: CLI-generated scaffolding" below before applying rules 1-6 to files produced by `pnpm dlx shadcn@latest add`, `pnpm create tauri-app`, or `pnpm create vite`.


1. **Manifest entry**: add to `.mercury/state/upstream-manifest.json` — fields: `path`, `scope` (`"project"` for repo files, `"user"` for `~/.claude/` global files), `upstream_repo`, `upstream_path`, `upstream_sha_at_import` (verify via `gh api repos/{owner}/{repo}/commits/{sha}`), `upstream_license`, `import_pr`, `import_date`, `import_rationale`, `last_drift_check` (null).
2. **SKILL.md frontmatter**: add `upstream_source`, `upstream_sha`, `upstream_license`, `cherry_picked_in`, `cherry_picked_at` fields.
3. **Script header**: add 5-line comment block after shebang — `UPSTREAM`, `SOURCE`, `SHA`, `DATE`, `ISSUE`.
4. **Config/template files** (e.g. `*.example`, CLAUDE snippets): add `# Based on <upstream> (LICENSE) SHA: <sha>` attribution comment at top of file.
5. **License gate**: only cherry-pick MIT, Apache-2.0, or other permissive licenses. Record in manifest.
6. **SHA verification**: `upstream_sha_at_import` must be verified via `gh api` before committing. Never record from memory. Mark `UNKNOWN_VERIFY_MANUALLY` only if API is unreachable; list in PR body.

Drift monitoring: run `bash scripts/upstream-drift-check.sh` periodically to detect upstream changes.

### Carve-out: CLI-generated scaffolding

The cherry-pick protocol above applies to **files lifted from a specific upstream commit** (canonical upstream path + SHA + drift monitoring). It does NOT cleanly fit two adjacent cases that produce files via CLI invocation rather than direct upstream-path import. Split into 2 sub-categories:

#### Category A — Pure scaffolding (one-shot project init)

Generators that produce a one-time project skeleton from templated boilerplate. The templates ship with the CLI itself; no per-file upstream "source path" exists.

| Generator | Invocation | Output scope |
|---|---|---|
| **create-tauri-app** | `pnpm create tauri-app` | Tauri 2 project skeleton (Rust workspace + JS frontend templated config) |
| **create-vite** | `pnpm create vite` | Vite + framework skeleton (TS config + entry + index.html) |

**Required for Category A**:

1. **Provenance line in PR body**: PR creating the scaffold records the exact CLI invocation + version at invocation time (e.g., "Scaffold via `pnpm create tauri-app`, create-tauri-app vX.Y at 2026-MM-DD"). Use the actual version, not a placeholder. Note: `pnpm create` resolves the create-* starter package transiently and does NOT pin the generator into the produced app's `pnpm-lock.yaml` — the PR-body line is the only durable provenance record.
2. **License compatibility check**: confirm the scaffold output's license is MIT / Apache-2.0 / similarly permissive (Tauri 2 = MIT/Apache-2.0; Vite = MIT). Record in PR body.
3. **Customization allowed without attribution**: post-scaffold Mercury edits to the generated files do NOT require per-file `Based on` attribution.

**NOT required for Category A**:

- ❌ Manifest entry in `.mercury/state/upstream-manifest.json`
- ❌ SKILL.md frontmatter `upstream_sha` field
- ❌ Per-file `# Based on <upstream>` comment
- ❌ Drift monitoring via `scripts/upstream-drift-check.sh`

#### Category B — Registry-based item import (per-item upstream lift)

CLI tools that fetch named items from a versioned registry. Each `add` invocation imports a concrete registry item that has a canonical upstream identity. Closer to a per-file cherry-pick than to pure scaffolding. **Applies to ALL `shadcn add` invocations regardless of registry item type** — components, hooks, utilities, pages, fonts, themes, config files, rules, libraries, or any other resource a shadcn-compatible registry exposes (per [shadcn CLI docs](https://ui.shadcn.com/docs/cli) — `add` consumes registry items by name, URL, or local path).

| Generator | Invocation | Registry default | Output scope |
|---|---|---|---|
| **shadcn (any registry item)** | `pnpm dlx shadcn@latest add <name-or-url-or-path>` | <https://ui.shadcn.com/r> (official shadcn registry) | Any registry-backed resource: UI components, hooks, utilities, pages, themes, fonts, config files, rules, libraries, etc. Resource arg is a registry item name (default registry), a URL (any registry), or a local path. |

**Required for Category B**:

1. **Provenance line in PR body** (stricter than Category A): record (a) exact CLI invocation including the item-name / URL / path arg(s), (b) shadcn CLI version at invocation time, (c) **source identifier — always**, in one of three forms depending on the arg kind:
   - For a registry item name (default registry): `source = default registry (ui.shadcn.com)`
   - For a URL arg (custom registry or registry item URL): `source = custom registry URL: <url>`
   - For a local-path arg (file-system import, not registry-fetched): `source = local path: <relative-path>` (note that local-path adds bypass the registry layer entirely — the path IS the upstream identity)

   The source identifier determines license + upstream identity, so the arg kind must be unambiguous on the record. (d) registry item type if non-component (e.g., `registry:hook`, `registry:font`, `registry:lib`, `registry:page`, `registry:file`). Example: "Imported via `pnpm dlx shadcn@latest add tabs`, shadcn CLI vX.Y, source = default registry (ui.shadcn.com), item type = registry:component at 2026-MM-DD".
2. **License compatibility check**: confirm the license of the actual source you're importing from at invocation time, NOT a fixed assumption. The shadcn default registry is MIT (illustrative); custom registries may use different licenses, and local-path adds inherit the license of the source path's project. Verify per import + record the verified license in PR body.
3. **Customization is owned by Mercury after add**: shadcn's design philosophy is "copy-paste with full ownership" — once added, the file is Mercury-owned and editable without per-file upstream-tracking attribution.

**NOT required for Category B** (registry items are not pinned to upstream SHA; shadcn's contract is "you own the code"):

- ❌ Manifest entry in `.mercury/state/upstream-manifest.json` (registry items are not version-pinned to a specific upstream commit; shadcn's model deliberately decouples from upstream after add)
- ❌ Per-file `# Based on <upstream>` comment
- ❌ Drift monitoring via `scripts/upstream-drift-check.sh`

**Local-path guard — when local-path adds fall back to full cherry-pick protocol**

The local-path arg form is intended for Mercury-internal registry items (e.g., a path under `mercury-gui/` or a sibling Mercury repo path). It is NOT a back door for importing arbitrary external-project files via a local checkout.

A local-path add **falls back to the full cherry-pick protocol (rules 1-6)** when ANY of these conditions hold:

- The path resolves outside the current Mercury repo working tree
- The path resolves into a git submodule pointing to a third-party upstream repo
- The path resolves into a node_modules / vendored directory whose contents originate from an external package
- The path resolves into a temporary checkout of an external project staged for import

When in doubt, treat the local-path source as a file-lift cherry-pick (full protocol applies). The carve-out exists to formalize "shadcn-style registry add from a versioned source" — local-path is the narrowest case and the guard above keeps the supply-chain audit surface intact.

**Tighter than Category A** — Category B's PR body line must identify (a) the specific registry-item / URL / local-path arg, (b) the source identifier in the form appropriate to the arg kind (default registry / custom registry URL / local path), and (c) the item type if non-component, because together these determine license + upstream identity.

#### Adding new generators to either category

Extend the appropriate table via a separate PR. The PR must cite (a) generator's package source URL, (b) license, (c) whether it produces one-shot scaffolding (→ Category A) or per-item registry imports (→ Category B), (d) drift-tracking rationale. Any tool not listed should be treated as a regular cherry-pick (full protocol rules 1-6 apply) until categorized here.

#### Authority chain

This carve-out resolves the repeat-DISAGREE-cite pattern observed during Phase 6 GUI MVP chain (PRs #421/#424/#425) where Argus / Copilot review threads flagged CLI-generated files as missing attribution. The Category A / Category B split was added in response to the audit finding that shadcn `add` is materially closer to registry-import than to pure project scaffolding, while create-tauri-app / create-vite genuinely are pure scaffolding.
