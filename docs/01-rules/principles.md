# 现行原则

收编自 `90-archive/DIRECTION-2026-04.md`（原 `.mercury/docs/DIRECTION.md`，2026-04-06 制定）的仍然有效部分，2026-10-31（#736）。
这里只写“现在怎么做”；理由和讨论过程见“来源”一列。原文其余部分（模块职责、Phase 安排、Open Issues 处理、已删除组件的说明）
只作历史，不再作为依据。

## Mercury 是什么

| 编号 | 规则 | 来源 |
|---|---|---|
| M1 | Mercury 是一套让 Claude Code 与 Codex 持续、自主、高质量工作的轻量 harness：配置宿主、提供可装载的 pack，不自建编排层、CLI 包装或桌面应用。 | DIRECTION 一；#645、#666 |
| M2 | 多会话管理交给宿主原生能力或 Agent Orchestrator；角色使用原生子代理（`packs/core/roles/` 生成）。 | DIRECTION 三·模块 4；#645、#682 |
| M3 | 长期记忆是文件式的：项目私有记忆、会话索引和 handoff 文档；没有向量召回层。 | DIRECTION 三·模块 2；#518 |

## 架构原则

| 编号 | 规则 | 来源 |
|---|---|---|
| P1 | 本体只做宿主和外部项目做不到的事；能用原生能力或挂载外部项目的，不自研。 | DIRECTION 二 P1；`04-research/mercury/issue-158-architecture-evaluation.md` |
| P2 | 外部项目的差异由 `packs/<pack>/build/` 吸收；转换层只做转换，不含业务逻辑，外部集成不超过 200 行。 | DIRECTION 二 P2、四；AGENTS.md |
| P3 | 每个 pack 可以单独拆除；模块间的依赖必须在依赖方的文档中写明。 | DIRECTION 二 P3 |
| P4 | 设计必须在模型变强后依然成立；不做“假设模型做不到 X”的设计。 | DIRECTION 二 P5 |

## 做决定前

| 编号 | 规则 | 来源 |
|---|---|---|
| D1 | 动手前回答三个问题：这是 Mercury 本体该做的吗？能独立拆出来用吗？模型变强后还成立吗？任何一个答“否”，先改方案。 | DIRECTION 七 |

## 外部项目

| 编号 | 规则 | 来源 |
|---|---|---|
| E1 | 挂载方式默认是钉住提交的 Git submodule（`sources/`，原样保存）；运行时专用的 `uvx git+SHA` 和钉住确切版本的 npm MCP 包是受治理的例外，须有宽松许可证、`upstream-manifest.json` 登记、pack 内 `UPSTREAM.md` 和漂移监控，禁止浮动版本。 | DIRECTION 四；AGENTS.md |
| E2 | submodule 不自动追最新；升级在独立分支验证转换层兼容后再合入。 | DIRECTION 四 |
| E3 | 决定挂载前评估：社区活跃度、接口稳定性、能否只取所需部分、维护持续性、自研与挂载的成本对比。 | DIRECTION 四 |

## 质量门禁

| 编号 | 规则 | 来源 |
|---|---|---|
| Q1 | 门禁分三层：机械检查（退出码、测试、`kb_check`、`guard.ps1`）、代理自检、独立审查加用户在聊天中确认合并。新门禁优先放在机械层，不足时再上移。 | DIRECTION 八-4；`02-guides/git-flow.md` |
| Q2 | 审查深度按风险选择；重要行为改动、跨仓写入、安全或权限变更、代理指令或规则变更须独立审查。 | DIRECTION 七；AGENTS.md |

## 知识库

| 编号 | 规则 | 来源 |
|---|---|---|
| K1 | 知识库遵循 kb pack 的约定：入口、目录索引与状态、归档；改动规则、决策或进度的 PR 在同一 PR 里更新现行文档并标记被取代的内容，PR 描述写 `KB:` 行。 | #736 |
