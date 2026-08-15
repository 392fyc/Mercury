# 已归档：orchestrator 原生 skill 引擎的技能库

原路径 `.mercury/skills/`，归档于 2026-08-14（Issue [#571](https://github.com/392fyc/Mercury/issues/571)）。

## 为什么归档

这 11 个 skill 由已退役的 orchestrator 的 BM25 skill 引擎（Issue #141）在**任务派发时注入提示**
——见同目录 `README.md`。该 orchestrator 随 #173/#174 于 **2026-04-06 归档**，实测确认彻底停用：

- 端口 7654 无监听，连接被主动拒绝
- 活代码零调用方
- `.mercury/state/` 无任何派发或回执记录
- 会话工具表里根本没有 `mcp__mercury-orchestrator__*`

**投递机制不存在了，所以这些文件不会被任何东西读到。**

## 归档前做过内容抢救（11 个逐个评估）

**关键数据：11 个 skill 的 `total_selections` / `total_applied` / `total_completions` 全部为 0。**
即便在 orchestrator 还运行的那段时间，BM25 引擎**一次都没有选中过它们中的任何一个**。
（计数器确实接线了 —— `recordSelection` 在 `archive/packages/orchestrator/src/orchestrator.ts:3148`
被调用，`recordApplied` / `recordCompletion` 在 2420/2421、2529/2530、3834，
所以「全 0」是真的从未被选中，不是计数器没写。）

逐个评估结论：

| 判定 | 数量 | skill |
|---|---|---|
| 内容已被现役制品覆盖 | 7 | `research-web-verify`、`issue-before-task`、`branch-safety-protocol`、`acceptance-receipt-check`、`autoresearch-protocol`、`rework-context-preservation`、`git-commit-heredoc-pattern` |
| 只对已归档 orchestrator 有意义 | 1 | `dispatch-scope-validation`（正文即在指示调用 `mcp__mercury-orchestrator__dispatch_task`） |
| 已过时（依赖物已退役） | 2 | `kb-obsidian-access`（obsidian MCP 随 #564/#565 退役，KB 改文件式访问）、`pr-review-flow`（**文件自己第 24 行就标注已过时**） |
| 有独有内容，已抢救 | 1 | `dual-verify` |

## 唯一被抢救的内容

`dual-verify/SKILL.md:138` 那一句：

> Cross-referencing catches false positives: an issue flagged by only one reviewer
> should be investigated, not auto-dismissed.

现役两份 dual-verify skill 的报告模板里只有「Claude-only: / Codex-only:」两个栏位，
**没有说明填完之后该怎么处置**。用 8 个中英关键词（`auto-dismiss` / `single reviewer` /
`false positive` / `误报` / `单侧` 等）横查两份现役 skill，全部零命中 —— 确认是独有内容。

已抄进 `.claude/skills/dual-verify/SKILL.md` 与 `.agents/skills/dual-verify/SKILL.md`
**两份**（只改一处会造成平行表述漂移），并补了当天的实例。

## 如果你在找某个具体规则

**不要从这里抄。** 上表第一行那 7 个的内容在现役制品里都有更新、更细的版本，
从这里抄会抄到旧版。现役落点：

- 强制规则 → `CLAUDE.md` / `AGENTS.md` 的 MUST / DO NOT
- 流程 → `.claude/skills/` 与 `.agents/skills/`
- 分支纪律 → `.mercury/docs/guides/git-flow.md` + `.claude/agents/dev.md`
- Issue 流程 → `.mercury/docs/guides/issue-workflow.md`

保留原文只是为了可追溯，不是为了复用。
