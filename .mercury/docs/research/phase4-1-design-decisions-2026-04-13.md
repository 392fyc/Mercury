# Phase 4-1 Session Continuity — 设计决策记录
> Date: 2026-04-13 | 补充自 S47 ADR draft 讨论

---

## 确认决策

### 触发方式

| 触发源 | 行为 |
|--------|------|
| `/handoff [text]` | 用户主动触发；`text` 为追加到下 session 的指令 |
| Agent 自主调用 | 任务完成时 agent 可以直接调用，不需要特殊符号 |
| Stop hook（兜底） | 任务未完成时 block stop，提示用户显式 `/handoff` |

不使用特殊符号分隔（减少用户调用阻力）。

### 续接方式

**选定 Option B（Agent SDK 自动启动新 session）**：
- 生成 handoff doc → 通过 Agent SDK 自动开新 session → 注入 handoff 作为 prompt 前缀
- 目标：agent 在有足够背景信息的情况下可自主推进任务，减少人类反复干预
- 这是多 agent team 工作的前置条件

### 两种运行模式（优先级有序）

**Mode 1（Phase 4-1，优先实现）：compact-coexist**
- 让 session 自由运行，compaction 正常工作
- 任务完成后 `/handoff` 写最终文档
- PreCompact checkpoint（Phase 3 flush）作为底稿
- 好处：自动化潜力高（任务完成 → 队列取下一任务 → 自动续接）

**Mode 2（Phase 4-3，后续优化）：compact-prevention**
- 接近 context 上限时在任务节点主动 `/handoff`
- context 始终保持在合理范围
- 缺点：Phase 3 的 PreCompact/PostCompact hooks 需要重定位用途

### 范围限制

- 仅本地工作环境，不做跨机器/云端对应（依赖底层 Claude Code 自身升级）
- Sub-agent 不纳入机制（sub-agent 是一次性任务，无需持续 handoff）
- 多窗口：只在调用 `/handoff` 的 session 生效，其他窗口不受影响

### Handoff 文档格式

```markdown
# Session Handoff — {date}

## 任务状态
- Issue: #N [title]
- Branch: feat/N-xxx  
- 完成: [commit hash list]
- 进行中: [当前步骤，卡点]
- 待处理: [ ] item1 ...

## 关键上下文（compact 丢失防护）
- 架构决策 / 踩坑 / 约束发现

## 用户追加指令
{/handoff 后的 text，若有}

## 下一 Session 首要任务
1. ...
```

---

## 新增依赖问题（Phase 4-2/4-3 需解决）

### 1. Session 链管理（Phase 4-2）

扩展现有 `skill_stats.db`（SQLite），新增表：

```sql
CREATE TABLE session_chain (
    session_id      TEXT PRIMARY KEY,
    issue_ids       TEXT,          -- JSON: ["#238", "#183"]
    branch          TEXT,
    worktree_path   TEXT,          -- NULL if main workspace
    start_time      DATETIME,
    end_time        DATETIME,
    handoff_doc     TEXT,          -- file path
    next_session_id TEXT,          -- FK self
    status          TEXT           -- active / handoff / complete
);
```

`flush.py` SessionEnd 时顺手写 chain record（已知 session_id + branch）。

### 2. Worktree-per-task（Phase 4-2）

现状：多任务并发时共用主 workspace，分支混乱。
方向：强制 worktree-per-task，worktree CWD 不同 → handoff 自然隔离。
已有资源：OMC `project-session-manager` skill（评估可否直接集成，勿重新实现）。

---

## 前置研究任务（实施前必须完成）

### OpenClaw 研究（新 Issue 待创建）

OpenClaw 是本地完全自主运行的 agent（250K GitHub stars，60 天内超越 React）。
需研究其如何处理：
1. 长期记忆（long-term memory）管理
2. 群体记忆（collective memory，多 agent 共享）
3. Context 窗口管理（如何避免/处理 context overflow）

初步已知：
- 三层 memory 架构：daily/{date}.md + MEMORY.md（KB）+ session archive（语义搜索）
- 文件优先（Markdown on disk），vector DB（Milvus）做语义检索层
- 与 Mercury AgentKB 架构高度相似，但增加了 vector semantic search 层
- 集成：Mem0、Supermemory、NVIDIA NemoClaw（policy enforcement）

**研究目标**：评估 OpenClaw 的 session continuity 和 group memory 方案，提炼可借鉴的设计模式用于 Mercury Phase 4 实现。

---

## 任务队列自动化潜力（Phase 4-4 展望）

Mode 1 + session chain + Issue backlog → 完整自动化链路：

```
Issue backlog → 分配 Issue → 创建 worktree → 开 session
     ↓
  任务执行（可 compact）
     ↓
  /handoff → 写 chain record + handoff doc
     ↓
  从 backlog 取下一 Issue → 自动开新 session（注入 handoff）
```

这是 Mercury DIRECTION.md "autonomous continuation" 的具体实现形态。
Phase 4-1 不实现到这层，但 session_chain 数据模型必须兼容。
