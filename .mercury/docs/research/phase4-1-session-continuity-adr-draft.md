# Phase 4-1 ADR — Session Continuity 技术方案选型

**Status**: DECIDED — 2026-04-13 (用户已拍板选定 Option D)
**Date**: 2026-04-12 (updated 2026-04-13)
**Issue**: #238 (Phase 4-1 实现), parent: #183 (Phase 4 Session Continuity)
**Decision authority**: User (已完成决策)
**Research artifact**: `.research/reports/RESEARCH-Session-Continuity-183-2026-04-11.md` (5 questions, 30+ sources)

---

## Context

Mercury 的 Phase 4 目标是实现 **Session Continuity**：当一个 Claude Code 会话因 context 压缩、预算耗尽或用户中断而结束时，下一个会话能够无缝恢复任务状态，不依赖人工手动传递背景。

### 现状问题

- 每个 Claude Code 会话是独立的 — 结束即失忆
- 当前缓解手段：用户手动 handoff（写文档粘贴到下个会话首条消息）
- Phase 3 Memory Layer 部分解决了 **知识积累**（daily log + compile → AgentKB），但不解决 **任务断点恢复**

### Phase 3 Memory Layer 已提供的基础设施

| 组件 | 状态 |
|------|------|
| AgentKB daily log (flush.py) | ✅ 运行中 |
| SessionEnd hook | ✅ 全局注册 |
| PreCompact hook | ✅ 已修复 (#232) |
| NAS rsync 备份 | ✅ 每小时 rc=0 |
| AgentKB compile pipeline | ✅ 运行中 |

Phase 4 在此基础上增加 **结构化 handoff 生成 + 自动续接** 能力。

---

## 方案对比

研究报告评估了四个方案，以下是完整对比：

### Option A — SDK-Based Orchestrator（最高控制度）

**原理**: 外部 Python 脚本通过 Agent SDK 包装 Claude Code 会话。
- 设置 `max_turns=30` 或 `max_budget_usd` 作为会话预算
- 预算耗尽时（`error_max_turns` / `error_max_budget_usd`）：自动从 transcript 生成 handoff doc
- 立即以 `resume=session_id` 启动新会话，handoff 注入为 prompt 前缀
- 任务完成时检查是否需要链接下一会话

**优点**: 完全自动续接，无需人工干预；可编程控制会话生命周期
**缺点**: 需要 Mercury 外部运行一个 orchestrator 进程；改变用户启动 Claude Code 的方式
**Mercury 适配度**: 高 — DIRECTION.md 描述的 Session Continuity 直接对应此模式

---

### Option B — Hook-Based Passive Continuity（最低复杂度）

**原理**: 4 个 hooks 组合，不改变用户启动方式。
1. `SessionStart` — 读取并注入 `.claude/handoff.md`
2. `PostToolUse` — 通过 transcript 长度启发式监控 context 用量
3. `PreCompact` — 提醒 Claude 在压缩前写 handoff
4. `Stop` — 拦截停止（exit code 2），强制 Claude 先完成 handoff

**优点**: 最轻量；与现有 hook 架构完全兼容；不改变用户工作流
**缺点**: 被动续接 — 仍需用户粘贴 handoff 到新会话；无法自动链接

**Mercury 适配度**: 中 — 解决"不丢 context"但不解决"自动续接"

---

### Option C — CLI Wrapper + Session Monitor（中等复杂度）

**原理**: Shell/Python 脚本监控 `claude` 进程。退出后读 transcript，生成 handoff，再以 `claude -c` 重启。

**优点**: 对用户透明（替换 `claude` 命令）；利用 `-c` 续接
**缺点**: DIRECTION.md 明确 Mercury 不是"CLI wrapper"；`-c` 依赖 session 文件本地存在
**Mercury 适配度**: 低-中

---

### Option D — Hybrid：Stop-hook handoff + SDK resume（研究报告推荐）

**原理**: 结合 B 和 A：
1. `Stop` hook（exit code 2）强制 Claude 在停止前写结构化 handoff 到 auto-memory
2. SDK orchestrator 脚本检测会话结束，立即以 `resume=session_id` + handoff prompt 启动新会话
3. `CLAUDE.md` 包含 compaction summary 指令，保留 task state 跨越压缩点

**优点**: 同时解决"不丢 context"和"自动续接"；复用 Phase 3 Memory Layer 存储
**缺点**: 需要实现两个组件（Stop hook + SDK orchestrator）；orchestrator 需持续运行
**Mercury 适配度**: 最高

---

## 方案对比矩阵

| 维度 | Option A | Option B | Option C | Option D |
|------|----------|----------|----------|----------|
| 自动续接（无需人工粘贴） | ✅ | ❌ | ✅ | ✅ |
| 不改变用户启动方式 | ❌ | ✅ | ❌ | 需讨论 |
| 实现复杂度 | 高 | 低 | 中 | 中-高 |
| 复用 Phase 3 infrastructure | 中 | 高 | 低 | 高 |
| 依赖外部进程 | ✅ orchestrator | ❌ | ✅ wrapper | ✅ orchestrator |
| 跨机器恢复支持 | 需 .jsonl 同步 | ✅ (handoff file) | 需 .jsonl | 混合 |
| 研究报告推荐 | — | — | — | ✅ |

---

## 关键技术事实（来自研究报告）

1. **Agent SDK session resume 已确认可用**: `resume=session_id` + `fork_session=True` 是 stable API
2. **SDK 无"context 快满"预警**: `compact_boundary` 在压缩后才触发，无 pre-compaction 回调；`max_turns` 可作为代理预算
3. **Stop hook exit code 2 可拦截**: 已被 claude-code-session-kit 在 92 次真实 session 验证
4. **`PostCompact` 无 `compact_summary` 字段**: DIRECTION.md 中的引用是 UNVERIFIED（研究确认）
5. **1M context window 已 GA (2026-03)**: 压缩频率大幅降低，session 寿命更长
6. **社区验证**: claude-code-session-kit、claude-auto-resume 等项目已证明这些模式可行

---

## 研究报告推荐意见

研究报告推荐 **Option D (Hybrid)**，理由：
- 利用现有 Phase 3 Memory Layer（AgentKB 存储 handoff）
- Stop hook 是 Mercury 已有的 hook 架构扩展，不引入新范式
- SDK orchestrator 可用现有 Python stack 实现
- 同时解决被动保护（Stop hook）和主动续接（SDK resume）

---

## 已决策事项（S47 用户拍板完成）

**选定方案**: Option D (Hybrid: Stop-hook handoff + SDK resume)

**已确认细节**：
1. **Orchestrator 形态**: 独立 Python 脚本（`handoff-orchestrator.py`），用户通过 `/handoff` skill 触发
2. **启动方式**: 保持 `claude` 直接启动，`/handoff` 在 session 内调用，orchestrator 负责续接
3. **Handoff 粒度**: 用户主动 `/handoff` 或 agent 自主调用；PreCompact 自动写 checkpoint 作为底稿

---

## 实施计划（已执行，S48 完成）

### Phase 4-1 子里程碑

| 里程碑 | 内容 | 状态 |
|--------|------|------|
| M0: PreCompact checkpoint | flush.py 检测 PreCompact 触发，写 session-checkpoint.md 到 auto-memory | 🔄 AgentKB PR#5 |
| M2: session_chain 表 | skill_stats.py 新增表 + session-end.py 自动记录 | 🔄 AgentKB PR#5 |
| M1: /handoff skill | 全局 skill + handoff-orchestrator.py (Agent SDK) | 🔄 AgentKB PR#5 |

---

## 关联 Issues

- **父 Issue**: #183 — Cross-Session State & Continuity (OPEN)
- **Phase 4-1 实现 Issue**: #238 (OPEN, 关联 #183)
- **Phase 3 已关闭**: #217 (NAS KB 架构) #232 (flush.py PreCompact fix, merged)
