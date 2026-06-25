# Mercury Articles

Mercury 方法论文章合集 — 工程实践沉淀为公开方法论资产。

## 目的

Mercury 不仅是一套 harness 框架，更是一组设计哲学与工程实证。本目录用于沉淀 Mercury 自身演化产生的方法论，供：

- 关注 long-running AI agent / agent harness 设计的开发者
- 参考 Mercury 工程模式（multi-lane / dev-pipeline / dual-verify / external adapter）的项目
- 作为 Mercury 自身演进的"为什么"档案（per `.mercury/docs/DIRECTION.md` P4: 方法论先于工具）

## 写作约定

| 项目 | 约定 |
|------|------|
| 语言 | 简体中文（zh-CN） |
| 长度 | 长文 8000-12000 字；ADR-style 4000-6000 字 |
| 受众 | 技术开发者；假定 Claude Code / GitHub / git workflow 基础认知 |
| 引用 | in-repo 路径优先（DIRECTION.md / .mercury/docs/）；外部 URL 限于官方文档或权威一手来源 |
| 实证 | 每 section 至少 1 个 Mercury 自身工程案例（PR # / Issue # / commit SHA） |
| Trade-off | 显性化设计权衡；不只讲"做了什么"，更讲"为什么不做替代方案" |

## Article 索引

| # | 标题 | 状态 | 主题 |
|---|------|------|------|
| 001 | [Mercury harness 设计：让 AI Agent 持续自主工作的轻量框架](001-mercury-harness-overview.md) | 草稿中 | 综述：Mercury 哲学 + multi-lane v1 + dev-pipeline + dual-verify + Argus + production-readiness gate |

## 后续候选主题（Issue #376 候选清单）

- 候选 2：Production-readiness gate 工程化方法（4-gap 框架 / cost-tracker / sub-agent return-size / loop-detector FP reduction）
- 候选 3：Argus + Codex 双路审查与 nit-loop escape-hatch 协议（13 cross-PR 实证 evidence base）
- 候选 4：Mercury multi-lane protocol v1 设计与实证（Rule 1-8 sub-rules + lane lifecycle + cwd routing）
- 候选 5：Dev-pipeline + acceptance subagent + blind review pattern（Main → Dev → Acceptance chain + dodChecklist 结构化 receipt）

各候选独立 Issue 决定开写时机；不预设节奏。

## 不做

- 不发布到外部渠道（blog / Substack / HN / Anthropic community）— 此目录仅 in-repo 沉淀；外部发布需另行决策（per Issue #376 边界条款）
- 不依赖未发布的 Mercury 模块；只引用已 merge 内容
- 不写 marketing 文案；技术内容为主
