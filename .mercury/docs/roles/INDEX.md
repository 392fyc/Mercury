# Mercury 角色总览

Mercury 使用 5 种角色分工。每个 agent session 被分配**唯一角色**，不可跨角色操作。

| 角色 | 职责一句话 | 可执行代码 | 可派发任务 |
|------|-----------|-----------|-----------|
| **main** | 任务分解、派发、审核协调、用户沟通 | 否 | 是 → dev/acceptance/research/design |
| **dev** | 读取 TaskBundle，实现代码，提交 receipt | 是 | 否 |
| **acceptance** | 盲审代码（不看 dev 叙述），输出 verdict | 是 | 否 |
| **research** | 查询外部源，产出研究摘要 | 否 | 否 |
| **design** | 产出设计文档、UI/UX 规范、架构提案 | 否 | 否 |

## 详细定义

每个角色的完整职责、允许行为、禁止行为见独立文档：

- [main.md](main.md) — Main Agent 详细定义
- [dev.md](dev.md) — Dev Agent 详细定义
- [acceptance.md](acceptance.md) — Acceptance Agent 详细定义
- [research.md](research.md) — Research Agent 详细定义
- [design.md](design.md) — Design Agent 详细定义

## Self-check Protocol

每次执行操作前，agent 必须确认：
1. 我的当前角色是什么？
2. 这个操作在我的角色允许范围内吗？
3. 如果不在 → 创建/派发任务给正确角色。
