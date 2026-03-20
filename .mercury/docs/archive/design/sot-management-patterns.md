# SoT 管理模式提炼 — Mercury 可复用框架

> 从 Ship of Theseus 多 Agent 协作实践中提炼的模式，供 Mercury 项目参考和优化

---

## 1. 任务生命周期模型

### SoT 现有流程
```
drafted → dispatched → in_progress → implementation_done → acceptance → verified/closed
```

### 参与角色
```
Main Agent: drafted → dispatched → acceptance → verified/closed
Sub Agent:                  in_progress → implementation_done
Acceptance Agent:                              acceptance
```

### Mercury 优化方向
- 状态流转应**自动触发**（Sub Agent commit → 自动通知 Main Agent → 自动启动验收）
- 当前全部依赖人工推进，Mercury 应实现事件驱动

---

## 2. Task Bundle 规格（可直接复用）

### 结构
```yaml
task_id: "TASK-{phase}-{domain}-{seq}"
title: "人类可读标题"
priority: "Sev-0~3"
assigned_to: "{agent}/{model}"
branch: "{agent}/{task-name}"

input:
  read_scope: [文档列表]
  design_refs: [设计文档路径]
  context: "任务上下文描述"

constraints:
  allowed_write_scope: [允许写入的路径]
  docs_must_not_touch: [禁止触碰的文档]
  code_scope: "代码边界"

definition_of_done:
  - "验收条件1"
  - "验收条件2"

implementation_receipt:
  status: null  # Sub Agent 填写
  commits: []
  notes: ""
```

### Mercury 应如何使用
- GUI 中提供 Task Bundle 创建表单
- Main Agent 可通过结构化接口创建 Bundle
- Bundle 自动关联到对应 Agent 会话
- Sub Agent 完成时 receipt 自动回传

---

## 3. Registry 模型（可借鉴简化）

### SoT 三层 Registry
```
global-index.yaml      ← 全局跨阶段索引
phase-N-registry.yaml  ← 阶段级里程碑和任务
current-session.md     ← 当前会话状态
```

### Mercury 简化版
```
project-state.json     ← 项目状态（活跃任务、Agent 状态）
task-log.json          ← 任务历史（自动追加）
```

- Mercury 不需要手工维护 Registry — 状态由系统自动追踪
- 但仍需 Single Source of Truth 原则

---

## 4. 多 Agent 编排模式

### 模式 A: 并行竞争（Parallel Competition）
```
Main Agent → 同一任务 → Agent 1 + Agent 2 + Agent 3
                         ↓           ↓           ↓
                     方案 1       方案 2       方案 3
                         ↘           ↓           ↙
                         Main Agent 择优合并
```
- 适用于：设计类任务、需要多角度方案的场景
- Mercury 需要支持：同时派发、结果对比视图

### 模式 B: 并行分工（Parallel Division）
```
Main Agent → 任务 A → Agent 1
           → 任务 B → Agent 2
           → 任务 C → Agent 3
                ↓
         各自独立完成，无冲突
```
- 适用于：独立的代码实现任务
- Mercury 需要支持：任务依赖关系、自动排序

### 模式 C: 串行流水线（Sequential Pipeline）
```
Design Agent → Implementation Agent → Acceptance Agent
   设计规格  →      代码实现        →     盲验收
```
- 适用于：严格阶段性任务
- Mercury 需要支持：自动触发下一阶段

### 模式 D: 迭代精炼（Iterative Refinement）
```
Main Agent → Sub Agent → 审计反馈 → Sub Agent → 审计反馈 → 通过
```
- 适用于：质量敏感任务
- Mercury 需要支持：反馈 prompt 自动注入上一轮上下文

---

## 5. Agent 能力矩阵（Mercury 应支持的 Agent 属性）

每个注册到 Mercury 的 Agent 应声明：

```json
{
  "id": "codex-cli",
  "display_name": "Codex CLI",
  "models": ["gpt-5.4", "gpt-5.3-codex"],
  "default_model": "gpt-5.4",
  "integration": {
    "type": "sdk",
    "sdk_package": "@openai/codex-sdk",
    "fallback": "pty"
  },
  "capabilities": ["code", "batch_json", "godot_mcp"],
  "restrictions": ["no_kb_write", "isolated_branch_only"],
  "status": "active",
  "max_concurrent_sessions": 3
}
```

---

## 6. 会话恢复协议（SoT Checkpoint 模式）

### SoT 做法
```json
{
  "schema": "sot-checkpoint-v1",
  "run_id": "session-id",
  "task_id": "TASK-XXX",
  "agent": "codex-cli",
  "status": "in_progress",
  "resume_context": "自然语言描述当前进度和下一步",
  "handoff": { "from": "Agent A", "to": "Agent B" }
}
```

### Mercury 优化
- Checkpoint 由系统自动捕获（Agent SDK 提供 session_id）
- 会话恢复不需要人工提供 resume_context — 直接恢复 SDK session
- 跨 Agent handoff 通过 Mercury 内部路由，无需人工中转

---

## 7. 审计和验收模式

### SoT 盲验收
- Implementation Agent ≠ Acceptance Agent
- Acceptance Agent 只看 Task Bundle input + 代码 + 运行环境
- 不看 Implementation Agent 的 reasoning

### Mercury 可实现
- 自动创建 Acceptance Session：指定不同 Agent 执行验收
- 验收结果自动写入任务记录
- 支持 Main Agent 审计（不盲）+ 独立 Agent 盲验收两种模式

---

## 8. 信息隔离规则

### SoT 规则（应保留）
| 角色 | 可读 | 可写 | 禁止 |
|------|------|------|------|
| Main Agent | 全部 | Registry, Session, Bundle | 项目代码 |
| Dev Agent | Task Bundle + 指定文档 | 代码 + 自己的 Receipt | Registry, Session |
| Acceptance Agent | Bundle + 代码 + 运行环境 | 自己的 Acceptance Bundle | 其他所有 |

### Mercury 实现
- 通过 SDK 的 `allowedTools` / `allowed_write_scope` 强制执行
- GUI 中可视化各 Agent 的权限边界
- 违规尝试被记录并告警

---

*编写日期: 2026-03-15 | Main Agent (Claude Code) | SoT 模式提炼*
