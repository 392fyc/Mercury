# Mercury 架构演进计划

> 状态: 活跃 | 创建: 2026-03-21
> 依据: prompt-architecture-best-practices 研究 + OpenSpec/Ruflo 对标 + 2026 多Agent最佳实践

## 概述

本文档是 Mercury 架构演进的总路线图。基于 Gap 分析，按优先级分为三个 Phase。

## Phase 1: 质量验证加强（本分支完成）

| # | Gap | 措施 | 状态 |
|---|-----|------|------|
| G4 | 无 auto-verify 门控 | 创建 `/auto-verify` skill（Claude + Codex 双版本） | ✅ 完成 |
| G6 | TaskBundle 创建无验证 | `createTask()` 增加 5 项输入校验 | ✅ 完成 |
| G5 | DoD 非结构化 | TaskBundle 模板增加可选 `specs` 字段 | 推迟（不改 TS 类型） |

## Phase 2: 自动化构架（下一任务）

| # | Gap | 措施 | 依赖 |
|---|-----|------|------|
| G8 | PR flow 全手动 | 创建 `/pr-flow` skill 自动化: pr create, poll checks, read comments, dispatch fix, merge | 无 |
| G1 | 无 context 耗尽处理 | orchestrator 监控 adapter streaming token count，70% 阈值 checkpoint | adapter 改造 |
| G2 | 无崩溃恢复 | session 非正常结束时 orchestrator 自动 re-dispatch | G1 |
| G3 | 无重试语义 | adapter 层 exponential backoff retry，TaskBundle 增加 `dispatchAttempts` | 无 |
| G9 | 无自动分诊 | orchestrator 读取 `modelRecommendation` 自动路由 | 无 |

## Phase 3: 架构级演进（设计讨论后启动）

| # | Gap | 措施 | 依赖 |
|---|-----|------|------|
| G11 | Agent 是 CLI session 非服务 | Codex adapter 迁移到 MCP (方案 B) | MCP SDK 集成 |
| G12 | MCP 仅做数据层 | Mercury orchestrator 暴露为 MCP server (方案 C) | G11 验证 |
| G7 | 单层 code review | 增加 critic agent（独立于 CodeRabbit） | G12 |
| G13 | Skill 无漂移检测 | CI 脚本校验 SKILL.md 引用路径、RPC 方法名 | 无 |
| G10 | Handoff 是独立流程 | TaskBundle + git 分支状态 = 天然 handoff，Handoff JSON 降级 | 无 |

## 关键架构决策记录

### AD-001: Claude 为唯一 Main Agent（当前约束）

- 系统配置层面禁止将其他 agent 设为 main
- Codex 提升 Main Agent 的路线图见 `.mercury/docs/codex-main-agent-roadmap.md`
- 需要完成: MCP Server 暴露、Approval 桥接、配置解锁

### AD-002: Codex Adapter 迁移方向

- 当前: `CodexAdapter` 通过 `codex app-server` 私有 JSON-RPC 协议
- 目标: `CodexMCPAdapter` 通过 `codex mcp-server` 标准 MCP 协议
- 优势: 标准化、减少自定义代码、内置 session 管理
- 详见 `.mercury/docs/codex-main-agent-roadmap.md` 方案 B

### AD-003: Skill 双栈同步

- `.claude/skills/` (Claude Code) 和 `.agents/skills/` (Codex) 手动对齐
- 未来可在 `.mercury/skills/` 维护通用源，构建时同步
- 当前 5 个 skill 已完成双栈: dispatch-task, acceptance-review, web-research, sot-workflow, auto-verify

### AD-004: Web-Search 强制执行三层机制

- 第一层: 指令文件 MUST 规则（CLAUDE.md / AGENTS.md / GEMINI.md / OPENCODE.md）
- 第二层: Hook 门控（web-research-gate + post-web-research-flag + user-prompt-submit）
- 第三层: Skill 引导（web-research skill 提供研究流程）
- 未来考虑: critic agent 作为第四层结构化执行

## 对标项目参考

### OpenSpec（可借鉴模式）

| 模式 | 说明 | Mercury 适用性 |
|------|------|---------------|
| Delta Specs | 变更产生 ADDED/MODIFIED/REMOVED delta，archive 时合并 | 可应用于 KB 变更管理 |
| Tasks.md Checkboxes | 文件即进度，任何 session 可续接 | TaskBundle 已覆盖此模式 |
| Verify 三维验证 | Completeness + Correctness + Coherence | 可增强 acceptance agent |
| Skill-as-Folder | 纯 markdown 描述 + 资源，无运行时依赖 | 已采用此模式 |

### Ruflo（可借鉴模式）

| 模式 | 说明 | Mercury 适用性 |
|------|------|---------------|
| 13 GitHub Agents | PR 生命周期全自动化 | `/pr-flow` skill 方向 |
| MCP 优先架构 | 所有集成通过 MCP | 方案 B/C 方向 |
| 规则引擎 | 组织级行为规范 | YAML roles 已覆盖 |

## 研究成果索引

| 文档 | 位置 | 内容 |
|------|------|------|
| Prompt 架构最佳实践 | `Mercury_KB/04-research/prompt-architecture-best-practices.md` | Q1-Q8 研究 |
| 多 Agent 研究成果 | `Mercury_KB/04-research/` | 行业对标 |
| Codex Main Agent 路线图 | `.mercury/docs/codex-main-agent-roadmap.md` | 迁移方案 A/B/C |
| 本文档 | `.mercury/docs/architecture-evolution-plan.md` | 总路线图 |
