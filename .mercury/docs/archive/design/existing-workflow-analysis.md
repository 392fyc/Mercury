# Ship of Theseus 多Agent协作模式分析

> Mercury 项目参考文档：从 SoT 实战经验提炼管理模式、痛点和优化方向

---

## 1. 现有架构

### 1.1 角色分层

```
Human Supervisor（决策权）
    ↕ 自然语言
Main Agent（Claude Code / Claude Opus 4.6）
    ↕ 人工中转（复制粘贴 prompt + 结果）
Sub Agents（Codex CLI / opencode / AntiGravity）
```

### 1.2 Main Agent 职责

| 职责 | 具体内容 |
|------|---------|
| **设计** | 游戏机制设计、规则裁定、ADR 撰写 |
| **调度** | 拆分任务、生成 Task Bundle、选择执行 Agent |
| **审计** | 审查 Sub Agent 交付物、反馈修正指令 |
| **KB 管理** | Session Ledger、Registry、Checkpoint 的读写同步 |
| **编排** | 多 Agent 并行任务分配、结果比较、择优合并 |

### 1.3 Sub Agent 职责

| Agent | 模型 | 专长 | 限制 |
|-------|------|------|------|
| Codex CLI | GPT-5.3/5.4 | 代码实现、批量 JSON | 无 KB 写入权 |
| opencode | Claude Opus 4.6 / GPT-5.4 | 并行代码、设计到代码转换 | 无 KB 写入权 |
| AntiGravity | Gemini 3.1 Pro | UI/UX 视觉、素材、大上下文分析 | 半活跃 |

### 1.4 信息流

```
Main Agent 生成 Task Bundle/Prompt
    → Human 复制到 Sub Agent 会话
        → Sub Agent 执行 + commit 到隔离分支
            → Human 将结果/分支信息复制回 Main Agent
                → Main Agent 审计 + 反馈
                    → 循环直到通过
                        → Main Agent 更新 KB Registry
```

---

## 2. 已验证有效的模式

### 2.1 并行竞争实现 (Parallel Competition)
- **剑士天赋树优化 v1**：3 个 Agent 同时优化同一 JSON，各自独立分支
- **效果**：不同 Agent 产出互补（Codex 结构最优，AG 文案最优，opencode 设计理解最稳）
- **择优合并**：Main Agent 比较后选最优方案为基线

### 2.2 多轮反馈迭代 (Iterative Refinement)
- 每轮 Sub Agent 提交 → Main Agent 审计 → 生成针对性反馈 prompt → 下一轮
- 典型需 2-3 轮收敛（Codex 2 轮，AG 3 轮，opencode 2 轮）

### 2.3 Task Bundle 规格化
- 标准化输入（read_scope, write_scope, definition_of_done）
- 消除歧义，Sub Agent 无需猜测设计意图
- 盲验收（Acceptance Agent ≠ 实现 Agent）保证质量

### 2.4 KB 作为 Single Source of Truth
- Session Ledger 记录当前状态
- Registry YAML 追踪所有任务/Issue 生命周期
- Checkpoint JSON 支持会话恢复

### 2.5 隔离分支 + Worktree
- 每个 Sub Agent 独立 worktree + 分支
- 避免冲突，支持并行开发
- 合并由 Main Agent/Human 控制

---

## 3. 痛点和效率瓶颈

### 3.1 🔴 人工中转成本（最大瓶颈）

| 操作 | 平均耗时 | 频率 |
|------|---------|------|
| 复制 prompt 到 Sub Agent | 1-3 分钟 | 每次任务派发 |
| 等待 Sub Agent 执行 | 5-30 分钟 | 每次执行 |
| 复制结果回 Main Agent | 2-5 分钟 | 每次反馈 |
| 切换窗口 + 上下文恢复 | 1-2 分钟 | 每次切换 |

**3 Agent × 3 轮 × 4 步 = 36 次人工中转**（剑士天赋树 v1 实际发生）

### 3.2 🔴 上下文碎片化
- Main Agent 会话压缩导致历史丢失
- Sub Agent 每次新 session 需重新加载上下文
- 跨 Agent 的设计裁定需要重复传达

### 3.3 🟡 审计标准校准困难
- off-by-one 计数约定（BFS 边数 vs 节点数）需要多轮才发现
- Main Agent 审计依赖文本描述，无法直接可视化 Sub Agent 输出

### 3.4 🟡 状态同步延迟
- Sub Agent 完成后 KB 不会自动更新
- Registry 状态需要 Main Agent 手动同步
- 多 Agent 并行时状态冲突风险

### 3.5 🟡 图片/视觉传递受限
- CLI 中只能 @ 引用图片路径
- 无法直接在对话中查看截图、UI 设计、天赋树可视化
- 视觉审计需要人工截图中转

---

## 4. Mercury 应解决的核心需求

### P0 — 消除人工中转
1. Main Agent 可直接开启/恢复 Sub Agent session
2. Sub Agent 输出自动回传 Main Agent
3. 无需人工复制粘贴 prompt 和结果

### P1 — 统一会话管理
4. 所有 Agent 会话在同一 GUI 中可见
5. 会话历史持久化，支持恢复
6. 跨 Agent 上下文共享机制（共享文件引用、设计裁定等）

### P2 — 丰富交互能力
7. 支持直接发送/查看图片（不是 @ 路径）
8. 内嵌终端支持 CLI 命令、/ 命令
9. 支持各 Agent SDK 原生功能

### P3 — 状态自动化
10. 任务状态自动追踪（dispatched → in_progress → completed → verified）
11. KB/Registry 状态变更可触发通知
12. 多 Agent 并行进度面板

---

## 5. 从 SoT 经验提炼的设计原则

| # | 原则 | 来源经验 |
|---|------|---------|
| 1 | **Agent 隔离执行** | Sub Agent 在隔离分支/worktree 中工作，不相互干扰 |
| 2 | **Main Agent 保持编排权** | 设计决策和任务分配不下放给 Sub Agent |
| 3 | **结构化任务输入** | Task Bundle 格式消除歧义，比自由文本 prompt 更可靠 |
| 4 | **审计不可跳过** | 盲验收模式有效防止自我评价偏差 |
| 5 | **择优合并优于单线程** | 并行竞争 + 比较选优的产出质量高于单 Agent 迭代 |
| 6 | **状态集中管理** | Single Source of Truth 避免信息碎片化 |
| 7 | **会话可恢复** | Checkpoint 机制支持跨 session 连续性 |
| 8 | **工具无关性** | 管理框架不绑定特定 Agent 工具，新 Agent 可即插即用 |

---

## 6. 参考架构映射

### Anthropic Orchestrator-Workers 模式
```
SoT Main Agent = Orchestrator（动态任务分解 + 结果综合）
SoT Sub Agents = Workers（接收任务 + 独立执行 + 返回结果）
```

### OpenAI Agents-as-Tools 模式
```
Manager Agent 调用 specialist.as_tool() 获取结果
= SoT Main Agent 向 Sub Agent 派发 Task Bundle 获取交付物
```

### Golutra PTY-wrapping 模式
```
通过 PTY 包装现有 CLI 工具 = 工具无关性
= 与 SoT 的 Agent 可替换性原则一致
```

### Mercury 的定位
Mercury 应该是**以上三种模式的融合**：
- 使用 **SDK/API 模式**（而非 PTY）与支持 SDK 的 Agent 交互（Claude Agent SDK, Codex SDK, Gemini SDK）
- **PTY 模式作为降级方案**，用于不支持 SDK 的 Agent
- **Orchestrator 模式**用于 Main Agent 编排
- **统一 GUI** 提供所有 Agent 的会话视图和管理能力

---

*编写日期: 2026-03-15 | Main Agent (Claude Code) | Mercury 项目启动参考*
