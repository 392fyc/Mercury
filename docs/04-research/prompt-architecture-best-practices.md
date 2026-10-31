# Agent Prompt Architecture Best Practices Research

> 研究日期: 2026-03-21 | Task: TASK-PROMPT-R-001 | 基于: [multi-agent-framework-analysis.md](https://github.com/392fyc/Mercury/blob/27a5468528c4119c1250e1da2d737d111205aad0/docs/04-research/multi-agent-framework-analysis.md) 扩展
> 本文不重复已有研究结论，聚焦 8 个具体问题的可落地回答。

---

## Q1: AGENTS 类指示文档如何精简？

### 发现

**核心原则: 指令数量与遵从质量成反比。**

1. **Anthropic 官方建议**: CLAUDE.md 应保持简洁，只包含 Claude 无法从代码推断的信息。每条规则自问 "删除后 Claude 是否会犯错？" 否则删除。膨胀的 CLAUDE.md 会导致 Claude 忽略你的实际指令。
   - 来源: https://code.claude.com/docs/en/best-practices

2. **指令容量上限**: 前沿 LLM 在 ~150-200 条指令范围内保持合理一致性。Claude Code 系统 prompt 自身占用约 50 条指令。因此用户可用额度约 100-150 条。
   - 来源: https://www.humanlayer.dev/blog/writing-a-good-claude-md

3. **CLAUDE.md 长度建议**: 目标 < 300 行；HumanLayer 自身保持 < 60 行。
   - 来源: https://www.humanlayer.dev/blog/writing-a-good-claude-md

4. **自动生成的 CLAUDE.md 降低成功率**: LLM 生成的 context files（/init 产出的文件）实际降低了任务成功率。手工精炼比自动生成更有效。
   - 来源: WebSearch "AGENTS.md vs CLAUDE.md" 2026 社区讨论

5. **AGENTS.md 与 CLAUDE.md 共存策略**: AGENTS.md 是跨工具通用指令（Codex, Cursor, Amp 均读取），CLAUDE.md 是 Claude 特定功能。推荐: AGENTS.md 放通用规则，CLAUDE.md 用 `@AGENTS.md` 导入后追加 Claude 专属配置。
   - 来源: https://github.com/anthropics/claude-code/issues/6235

### 推荐结构模板

```markdown
# {Project} — {Agent Name}

## Identity
Agent: {agent-cli-name}
Role: injected at session start. If absent, read dispatch prompt.

## Navigation (按需查阅，不预加载)
| Topic | Path |
|-------|------|
| Roles | .mercury/docs/roles/INDEX.md |
| Git   | .mercury/docs/git-flow.md |
| ...   | ... |

## MUST (≤8 条，每条一行)
- Commit at every checkpoint + push
- Web search before SDK/API code
- PR to develop (no direct push)
- ...

## DO NOT (≤5 条)
- Do not guess SDK APIs
- Do not modify files outside scope
- ...
```

**关键设计原则:**
- Identity: 3 行（agent名 + role来源 + fallback）
- Navigation: 只放路径表，不内联文档内容
- MUST/DO NOT: 合计不超过 13 条
- 无 workflow 详述（移入 skills）
- 无架构说明（移入 .claude-library/ 或 skills 按需加载）
- 目标: < 50 行 / < 2KB

### 对 Mercury 的适用性

Mercury 当前 CLAUDE.md 约 50 行（合理范围），但包含了 AGENTS.md 几乎相同的内容（两份文件 90% 重复）。

**推荐行动:**
1. 合并 CLAUDE.md 和 AGENTS.md 为 AGENTS.md（通用）+ CLAUDE.md（`@AGENTS.md` + Claude 专属）
2. 将 "Navigation" 表保留，将 workflow/模板/详细规则移入 `.claude/skills/`
3. MUST/DO NOT 精简到各 ≤8 条

---

## Q2: 角色注入策略

### 发现

**最佳实践: 一次注入 + 结构化强化 (非重复注入)。**

1. **Anthropic 官方 sub-agent 模型**: Sub-agent 只在启动时接收 system prompt（frontmatter 中的 markdown body），不接收完整 Claude Code system prompt。之后通过 task delegation message 传递具体任务。
   - 来源: https://code.claude.com/docs/en/sub-agents

2. **Mercury role-prompt-builder.ts 已采用正确模式**: `buildRoleSystemPrompt()` 在 session 开始时注入一次角色声明 + 约束 + task scope。这与 Anthropic 官方 sub-agent 模式一致。
   - 来源: Mercury 代码 `packages/orchestrator/src/role-prompt-builder.ts`

3. **oh-my-claudecode 的实践**: 32 个 agent 通过 prompt engineering 注入角色，而非反复在对话中重申。团队流水线 (plan→prd→exec→verify→fix) 每阶段是独立 session，各自有独立角色注入。
   - 来源: https://github.com/Yeachan-Heo/oh-my-claudecode

4. **Claude Code skills 的 description 机制**: Skill descriptions 始终加载到 context 中（使 Claude 知道有什么可用），但完整内容仅在调用时加载。这是一种 "lazy injection" — 比全量注入更高效。
   - 来源: https://code.claude.com/docs/en/skills

5. **Sub-agent `skills` preload 模式**: Sub-agent frontmatter 可通过 `skills: [skill1, skill2]` 在启动时预加载完整 skill 内容。这允许将角色知识以 skill 形式注入，而非写死在 system prompt 中。
   - 来源: https://code.claude.com/docs/en/sub-agents

### 对 Mercury 的适用性

Mercury 当前决策（session 开始注入一次）是正确的。验证结论:

- ✅ 一次注入 = Anthropic 官方 sub-agent 模式
- ✅ `buildRoleSystemPrompt()` 的结构（Role → Constraints → Task Scope）符合最佳实践
- ⚠️ 改进点: 当前 `ROLE_CARDS` 硬编码在 `@mercury/core` 中，应考虑迁移为 `.md` 文件以支持运行时编辑

**推荐行动:**
1. 保持一次注入策略
2. 将 `ROLE_CARDS` 从 TypeScript 常量迁移为 `.mercury/roles/{role}.yaml` 文件
3. `role-prompt-builder.ts` 改为读取 YAML 文件而非 import 常量
4. 角色详细行为规范以 skill preload 方式注入 sub-agent

---

## Q3: Skills 配置策略

### 发现

**核心矛盾: skills 过多 → agent 选择困难 vs skills 过少 → 流程脆弱。**

1. **Anthropic 官方 skill 加载机制**: Skill descriptions 占用 context 预算 = 2% of context window (fallback 16,000 chars)。超出时有 skills 被排除。可通过 `SLASH_COMMAND_TOOL_CHAR_BUDGET` 环境变量调整。
   - 来源: https://code.claude.com/docs/en/skills

2. **两个控制维度**:
   - `disable-model-invocation: true` — 只允许用户手动调用（deploy, commit 等有副作用的操作）
   - `user-invocable: false` — 只允许 Claude 自动调用（背景知识类）
   - 来源: https://code.claude.com/docs/en/skills

3. **Skill 内容分层**: SKILL.md 保持 < 500 行。详细参考材料放 supporting files（reference.md, examples.md），由 SKILL.md 引用。
   - 来源: https://code.claude.com/docs/en/skills

4. **wshobson/agents 三层加载**: Metadata(始终) → Instructions(激活时) → Resources(按需)。146 个 skill 通过此模式管理，无选择困难。
   - 来源: https://github.com/wshobson/agents (已有研究)

5. **oh-my-claudecode 的 skill scope**: Project skills (.omc/skills/) 优先级高于 User skills (~/.omc/skills/)，实现项目级覆盖。
   - 来源: https://github.com/Yeachan-Heo/oh-my-claudecode

### 推荐分组策略

| 分组 | 触发方式 | 示例 |
|------|----------|------|
| **Workflow Skills** | `disable-model-invocation: true` (手动) | `/mercury-pr-flow`, `/deploy`, `/commit` |
| **Convention Skills** | 自动 (description 匹配) | api-conventions, git-conventions |
| **Domain Knowledge** | `user-invocable: false` (仅 Claude 用) | legacy-system-context, architecture-overview |
| **Research Skills** | `context: fork` (隔离执行) | `/deep-research`, `/security-audit` |

### 对 Mercury 的适用性

Mercury 当前无 skills 系统，所有 workflow 规则写在 CLAUDE.md 和 .mercury/docs/ 中，每次 session 全量加载。

**推荐行动:**
1. 创建 `.claude/skills/` 目录，将以下高频 workflow 封装为 skill:
   - `mercury-pr-flow` (disable-model-invocation: true) — PR 创建 + CodeRabbit 等待 + 修复循环
   - `mercury-git-conventions` (自动) — 分支命名、commit 格式、禁止操作
   - `mercury-task-completion` (disable-model-invocation: true) — receipt → status → PR 序列
   - `mercury-research` (context: fork, agent: Explore) — 联网研究 + 验证
2. 从 CLAUDE.md 删除已封装为 skill 的内容
3. Skill description 保持 1-2 句话（控制 context budget）

---

## Q4: Agent 何时查 Index → 按索引查 docs → 拒绝猜测

### 发现

**关键机制: Navigation 表 + "Read before act" 指令 + Hooks 强制。**

1. **Anthropic 官方 "Explore first, then plan, then code"**: 官方推荐四阶段工作流 Explore → Plan → Implement → Commit。Plan Mode 阶段 Claude 只读文件和回答问题，不做修改。
   - 来源: https://code.claude.com/docs/en/best-practices

2. **Navigation 表模式** (Mercury 已采用): 在 CLAUDE.md 中提供路径表，让 agent 按需查找。这比内联文档更节省 token。关键是 agent 需要知道 "去哪里找"。
   - 来源: https://www.humanlayer.dev/blog/writing-a-good-claude-md (progressive disclosure strategy)

3. **PreToolUse Hook 可阻止未读即写**: 配置 PreToolUse hook 检测 Edit/Write 调用前是否已读过目标文件。如未读取则 exit 2 阻止操作。
   - 来源: https://code.claude.com/docs/en/skills (open_claw Hook 体系)

4. **Dispatch prompt 中的前置确认清单**: 在 dispatch prompt 中加入「前置确认」段，要求 agent 自检是否已读完所有 readScope 文档。
   - 来源: Mercury 已有研究建议 #4

5. **Sub-agent skills preload**: 通过 sub-agent `skills` 字段预加载必读的 convention skills，确保 agent 在开始工作前已获得关键知识。
   - 来源: https://code.claude.com/docs/en/sub-agents

### 具体机制设计

**Layer 1: System Prompt 指令 (软约束)**
```markdown
## 工作协议
1. 收到任务后，先读取 Navigation 表中相关文档
2. 读取 TaskBundle 中 readScope.requiredDocs 列出的所有文件
3. 未读完以上文档前，禁止 Edit/Write 操作
4. 遇到不确定的 API/SDK，必须 WebSearch 验证
```

**Layer 2: Dispatch Prompt 清单 (中约束)**
```markdown
## 前置确认 (自检)
- [ ] TaskBundle 已读完
- [ ] readScope 文档已全部读取
- [ ] 理解 allowedWriteScope 边界
- [ ] 确认 definitionOfDone 可验证
```

**Layer 3: PreToolUse Hook (硬约束)**
```json
{
  "hooks": {
    "PreToolUse": [{
      "matcher": "Edit|Write",
      "hooks": [{
        "type": "command",
        "command": ".claude/hooks/verify-read-before-write.sh"
      }]
    }]
  }
}
```

### 对 Mercury 的适用性

Mercury 已有 scope-guard hook (Layer 3)，已有 Navigation 表 (Layer 1)。缺失的是:
- Layer 2: dispatch prompt 中的前置确认清单
- Layer 3 增强: 不仅检查 scope，还检查是否已读过 readScope 文件

**推荐行动:**
1. dispatch prompt 模板增加前置确认段
2. 扩展 scope-guard hook 增加 "read-before-write" 验证
3. Dev role instructions 中强化 "不确定则 WebSearch" 规则

---

## Q5: 任务转交时的 dispatch 稳定性

### 发现

**关键: Dispatch = frozen system prompt + structured task message + file references。**

1. **Anthropic sub-agent 启动流程**:
   - Sub-agent 接收: frontmatter 中的 system prompt (冻结) + CLAUDE.md (自动加载) + delegation message (任务)
   - Sub-agent 不接收: 主对话的完整 system prompt
   - 来源: https://code.claude.com/docs/en/sub-agents

2. **Mercury 当前两种 dispatch 模式**:
   - `buildDevPrompt()`: 内联 TaskBundle JSON + context + completion instructions (内嵌模式)
   - `buildReferencePrompt()`: 只传文件路径，agent 自行读取 (引用模式)
   - 来源: Mercury `task-manager.ts` line 587-669

3. **oh-my-claudecode 的 dispatch**: 通过 `omc team N:codex "task"` 启动，每个 worker 接收独立任务描述，在独立 pane 中执行。
   - 来源: https://github.com/Yeachan-Heo/oh-my-claudecode

4. **Sub-agent skills preload 确保一致性**: 通过 `skills: [git-conventions, task-completion]` 预加载，所有 sub-agent 自动获得相同的 workflow 知识，无需在每次 dispatch 中重复。
   - 来源: https://code.claude.com/docs/en/sub-agents

5. **Claude Code `--agent` 模式**: 可用 `claude --agent dev-agent` 启动整个 session 以特定 agent 角色运行，system prompt 完全替换为该 agent 的 markdown body。
   - 来源: https://code.claude.com/docs/en/sub-agents

### Dispatch 流程设计建议

```
Main Agent 视角:
1. 创建 TaskBundle JSON → 写入 Mercury_KB/10-tasks/{taskId}.json
2. 创建分支: git checkout -b {agent}/{task-name} develop
3. 选择 dispatch 模式:
   a. Sub-agent 模式 (推荐): 定义 .claude/agents/dev-worker.md
      - skills: [mercury-git-conventions, mercury-task-completion]
      - tools: Read, Write, Edit, Bash, Grep, Glob
      - model: sonnet
   b. Reference prompt 模式 (现有): buildReferencePrompt() 发送文件路径
4. Dispatch:
   a. Sub-agent: Agent tool 调用 dev-worker, 传入任务描述
   b. Reference: 发送自然语言 prompt + TaskBundle 路径

Dev Agent 接收:
1. System prompt (frozen): dev-worker.md body + preloaded skills
2. CLAUDE.md/AGENTS.md: 自动加载
3. Task message: 任务描述 + TaskBundle 路径
4. 读取 TaskBundle → readScope → 开始工作
```

### 对 Mercury 的适用性

Mercury 当前通过 Codex API 发送 dispatch prompt。改进方向:

**推荐行动:**
1. 创建 `.claude/agents/dev-worker.md`，包含 dev role 指令 + skills preload
2. `buildReferencePrompt()` 保持精简 (只传路径 + 一句话任务 + 完成要求)
3. 将 git conventions、task completion 流程封装为 skills 而非写入 dispatch prompt
4. 效果: dispatch prompt 从 ~50 行降至 ~10 行，skill 负责一致性

---

## Q6: Sub-agent 流程规范

### 发现

综合 Anthropic 官方、open_claw、oh-my-claudecode 的最佳实践:

### Sub-agent 协议规范草案

#### 6.1 Git 规范

```yaml
branch_naming: "{agent-cli-name}/{task-id}"
commit_format: "{type}({task_id}): {summary}"
commit_types: [feat, fix, refactor, chore, docs, test]

forbidden_commands:
  - git switch / checkout <branch>
  - git branch -d / reset / stash / rebase / merge
  - git add -A / git add .
  - git push --force
  - Operate on master/develop directly

allowed_commands:
  - git add <specific-files>
  - git commit -m "..."
  - git push origin <current-branch>
  - git diff / status / log / branch --show-current (read-only)

anomaly_protocol: "Stop immediately, report to Main Agent"
```
来源: Mercury `.mercury/roles/dev.md` + Anthropic sub-agent patterns

#### 6.2 任务完成状态更新

```
1. 实现代码变更
2. 自验证 (lint + type-check + test + scope-check)
3. 自修复 (maxSelfRetry=1)
4. 填写 implementationReceipt
5. git add <changed-files> && git commit
6. git push origin <branch>
7. 返回结构化完成消息到 Main Agent
8. STOP. 不再执行任何操作。
```

#### 6.3 TaskBundle implementationReceipt 规范

```json
{
  "implementer": "{agentId}",
  "branch": "{branch-name}",
  "summary": "一句话中文总结",
  "changedFiles": ["path/to/file1.ts", "path/to/file2.ts"],
  "evidence": [
    "npm run lint: 0 errors",
    "npm run test -- --filter auth: 5/5 passed",
    "scope-check: all files within allowedWriteScope"
  ],
  "docsUpdated": [],
  "residualRisks": ["optional: known issues"],
  "scopeViolations": [],
  "completedAt": "2026-03-21T12:00:00Z"
}
```

#### 6.4 返回消息格式

```json
{
  "status": "completed | blocked | failed",
  "taskId": "TASK-XXX-001",
  "agentId": "codex-dev-1",
  "sessionId": "session-xxx",
  "summary": "一句话中文总结",
  "receiptPath": "Mercury_KB/10-tasks/TASK-XXX-001.json",
  "branch": "codex/TASK-XXX-001",
  "blockerReason": null
}
```

#### 6.5 PR + CodeRabbit 循环规范

```
Main Agent:
1. 接收 Dev 完成消息
2. 验证 receipt 完整性
3. gh pr create --base develop --head {branch}
4. 等待 CodeRabbit review (≥2 min)
5. 读取 review comments
6. IF issues:
   a. 创建 rework dispatch prompt (引用 review comments)
   b. 发送到 Dev Agent (resume 或新 session)
   c. Dev 修复 → commit → push → 重新等待 review
   d. 循环直到通过
7. gh pr merge
8. 更新 TaskBundle status → closed
```

### 对 Mercury 的适用性

Mercury 已有部分规范（git rules in dev.md, receipt format in task-manager.ts），但分散在多处。

**推荐行动:**
1. 将上述规范统一为 `.mercury/docs/sub-agent-protocol.md`
2. 将 git conventions 和 task completion 流程封装为 skills
3. 在 dev-worker.md agent 定义中 preload 这些 skills

---

## Q7: 任务完成的最终更新序列

### 发现

综合 oh-my-claudecode 的 verify-fix 循环、Anthropic 的 checkpoint 模式、Mercury 已有流程:

### End-of-Task 序列 Checklist

```
Phase D: Dev Agent 完成
  D1. ✅ 实现完成
  D2. ✅ AUTO-VERIFY: lint + type-check + test + scope-check
  D3. ✅ 自修复 (如 verify 失败, max 1 次)
  D4. ✅ 填写 implementationReceipt (JSON)
  D5. ✅ git add <specific-files>
  D6. ✅ git commit -m "{type}({task_id}): {summary}"
  D7. ✅ git push origin <branch>
  D8. ✅ 返回完成消息 (JSON) 到 Main Agent
  D9. ⛔ STOP. 不自行 PR/merge/切分支

Phase C: Main Agent 验收
  C1. ✅ 接收 Dev 完成消息
  C2. ✅ 读取 receipt + git diff 验证
  C3. ✅ Main Review (不通过 → rework dispatch 回 Dev, reworkCount++)
  C4. ✅ gh pr create --base develop --head {branch} --title --body
  C5. ✅ 等待 CodeRabbit review 完成 (不可跳过)
  C6. ✅ 读取 review comments
  C7. 🔄 IF review 有 issues → dispatch 修复到 Dev → Dev 修复 push → 重新等待 review
  C8. ✅ Review 通过 → gh pr merge
  C9. ✅ 更新 TaskBundle status: "completed" → "closed"
  C10. ✅ 更新 KB: Mercury_KB/10-tasks/{taskId}.json
  C11. ✅ [可选] 归档: move to archive/ if TTL expired
```

### 关键防护点

| 序号 | 防护 | 机制 |
|------|------|------|
| D2 | 不能跳过 verify | Hook (PostToolUse on git commit) |
| D8 | 必须返回结构化消息 | Dispatch prompt 中明确要求 |
| D9 | Dev 不可 PR | Role 禁止操作列表 |
| C5 | 不可跳过 CodeRabbit | Main Agent CLAUDE.md 强制规则 |
| C7 | 修复循环有上限 | maxReworks 字段 (默认 3) |

### 对 Mercury 的适用性

Mercury 当前流程大致覆盖 C1-C8，但:
- 缺少 D2 (auto-verify)
- 缺少 C5 的自动化等待机制
- C9-C11 (status update + KB update) 依赖人工

**推荐行动:**
1. 在 dev-worker skill 中加入 auto-verify 步骤
2. 创建 `mercury-pr-flow` skill 自动化 C4-C8
3. Task status update 通过 hook 或 skill 自动触发

---

## Q8: 减少人工干预的方法论

### 发现

| 排名 | 技术 | 效果 | 来源 | Mercury 适用性 |
|------|------|------|------|---------------|
| 1 | **自验证门控 (Auto-Verify Gate)** | 高 — 消除 "看起来对但实际错" 的问题 | Anthropic best practices, Loki Mode RARV cycle | ✅ 直接可用: PostToolUse hook + verify skill |
| 2 | **Skills 封装高频 workflow** | 高 — 消除重复指令，确保一致性 | Anthropic skills docs, wshobson/agents | ✅ 直接可用: 创建 mercury skills |
| 3 | **Sub-agent 隔离** | 中高 — 保持主对话 context 清洁 | Anthropic sub-agents docs | ✅ 直接可用: dev-worker agent |
| 4 | **Auto Mode (triage-by-blast-radius)** | 高 — 低风险操作自动审批 | Anthropic Auto Mode (2026-03 preview) | ⚠️ 需评估: 安全性 tradeoff |
| 5 | **Verify-Fix 循环 (maxRetry)** | 中 — 自动修复常见错误 | oh-my-claudecode Ralph mode | ✅ TaskBundle resilience 字段 |
| 6 | **Hook 自动化 (post-commit-verify, auto-push)** | 中 — 消除手动步骤 | open_claw Hook 四类型体系 | ✅ 直接可用: 扩展 .claude/hooks/ |
| 7 | **Rate-limit auto-resume** | 中 — 消除 "卡住等人来" | oh-my-claudecode `omc wait` | ⚠️ 需 Mercury daemon 支持 |
| 8 | **Context exhaustion checkpoint** | 中 — 长任务不丢失进度 | ruflo anti-drift, Anthropic auto-compaction | ⚠️ 部分可用: auto-compaction 已内建 |
| 9 | **Complexity-based model routing** | 低中 — 减少 token 浪费 | ruflo Q-Learning, wshobson 4-tier | ⚠️ 需 TaskBundle 改造 |
| 10 | **Structured escalation protocol** | 低 — 减少 "卡住不报" | Mercury dev.md escalation rules | ✅ 已有，需强化 |

### 三级人工干预缩减路线图

**Phase 1 (立即可做):**
- 创建 skills 封装高频 workflow (PR flow, git conventions, task completion)
- 在 dispatch prompt 加入 auto-verify 步骤
- 扩展 hooks: post-commit-verify, auto-format

**Phase 2 (中期):**
- 创建 `.claude/agents/dev-worker.md` 标准化 sub-agent
- 实现 verify-fix 循环 (maxSelfRetry in TaskBundle)
- Permission allowlist 减少审批中断

**Phase 3 (长期):**
- 评估 Auto Mode 适用性
- 实现 rate-limit resume daemon
- 复杂度路由 (TaskBundle complexity → model recommendation)

### 对 Mercury 的适用性

Mercury 当前人工干预主要发生在:
1. 每次 dispatch 需要手动构建 prompt (→ skills + 标准 agent 解决)
2. Dev 完成后需要手动 PR + 等待 review (→ mercury-pr-flow skill 解决)
3. CodeRabbit 反馈后需要手动 dispatch 修复 (→ verify-fix loop 解决)
4. 权限审批中断 (→ permission allowlist 解决)

**推荐行动:** 按上述三级路线图执行

---

## 附录 A: 发现与理想工作流阶段映射表

| 阶段 | 描述 | 关键发现 | 推荐改进 |
|------|------|----------|----------|
| **A1** | CLAUDE.md 静态注入 | Q1: < 50行, < 2KB, 删除可推断内容 | 精简 CLAUDE.md, 合并 AGENTS.md |
| **A2** | 读取角色 YAML | Q2: 一次注入足够, 不需反复强化 | ROLE_CARDS → YAML 文件 |
| **A3** | 读取 session 状态 | Q4: Navigation 表按需查阅 | 保持现有模式 |
| **A4** | 读取 handoff | Q4: Progressive disclosure | 保持引用模式 |
| **A5** | 研究规划设计 | Q1: Plan Mode 先探索后实现 | 使用 Plan Mode |
| **A6** | 注册任务 | Q6: Issue → TaskBundle | 保持现有流程 |
| **A7** | 创建任务包 | Q6: 增加 complexity, verifyGate 字段 | TaskBundle v2 |
| **A8** | 生成 dispatch prompt | Q5: 精简为 ~10行 + file ref + skills | 重写 dispatch 模板 |
| **A9** | 下发到 Dev agent | Q5: Sub-agent 模式 + skills preload | 创建 dev-worker.md |
| **B1** | AGENTS.md 注入 | Q1: @AGENTS.md 导入模式 | 合并重复内容 |
| **B2** | Role system prompt | Q2: frozen system prompt, 一次性 | 保持现有模式 |
| **B3** | 读取 dispatch prompt | Q5: 精简的引用模式 | buildReferencePrompt() |
| **B4** | 读取 TaskBundle | Q4: readScope 必须全部读完 | 前置确认清单 |
| **B5** | 联网查证 | Q4: WebSearch 验证, 不猜测 | 保持现有规则 |
| **B6** | 研究→开发 | Q4: Explore first, read-before-write hook | PreToolUse hook 扩展 |
| **B7** | Code review | Q8: Auto-verify gate (lint+test+scope) | 新增 auto-verify skill |
| **B8** | 填写 receipt | Q6: 标准化 JSON 格式 | 保持现有格式 |
| **B9** | 更新状态 | Q7: 结构化完成消息 | 标准化返回 JSON |
| **B10** | 返回汇报 | Q6: status + taskId + branch + summary | 标准化消息格式 |
| **C1** | 接收汇报 | Q7: 验证 receipt 完整性 | 自动检查 |
| **C2** | 查看信息 | Q7: git diff + receipt 验证 | 保持现有流程 |
| **C3** | Main Review | Q7: 不通过 → rework dispatch | maxReworks 限制 |
| **C4** | 提起 PR | Q7: gh pr create | mercury-pr-flow skill |
| **C5** | 等待 CodeRabbit | Q7: 不可跳过 | 自动等待机制 |
| **C6** | 反馈修复 | Q7: dispatch 循环 | 自动化 rework dispatch |
| **C7** | 合并 PR | Q7: gh pr merge | mercury-pr-flow skill |
| **C8** | 关闭任务 | Q7: status → closed | 自动更新 |

---

## 附录 B: 对 TASK-PROMPT-ARCH-001 的具体建议

### B.1 ROLE_CARDS 剥离方案

**现状**: `ROLE_CARDS` 定义在 `@mercury/core` TypeScript 常量中，编译时冻结。

**目标**: 运行时可编辑，支持用户自定义角色。

**方案:**
```
.mercury/roles/
├── main.yaml      # canExecuteCode, canDelegateToRoles, inputBoundary, outputBoundary
├── dev.yaml
├── research.yaml
├── design.yaml
├── acceptance.yaml
└── INDEX.md        # 角色总览 + 路径表
```

每个 YAML 文件结构:
```yaml
role: dev
description: "Implementation agent: reads TaskBundle, implements code, returns receipt."
canExecuteCode: true
canDelegateToRoles: []
inputBoundary: [TaskBundle, dispatch-prompt]
outputBoundary: [implementationReceipt, code-changes]
instructions: |
  # 直接内联 .mercury/roles/dev.md 的内容
  # 或引用: @.mercury/roles/dev.md
```

**迁移步骤:**
1. 将 `ROLE_CARDS` 常量导出为 YAML 文件
2. `role-prompt-builder.ts` 改为读取 YAML (使用 js-yaml)
3. `@mercury/core` 保留 TypeScript 类型定义，删除常量
4. 增加运行时 YAML schema 验证

### B.2 Roles 合并方案

**现状**: 角色信息分散在 4 处:
- `@mercury/core` ROLE_CARDS (TypeScript 常量)
- `.mercury/roles/dev.md` (实际加载的简版)
- `.mercury/docs/roles/dev.md` (完整但未被代码使用)
- `role-prompt-builder.ts` (构建逻辑)

**合并为:**
- `.mercury/roles/{role}.yaml` — 单一 SoT (包含 card 元数据 + instructions)
- `role-prompt-builder.ts` — 读取 YAML，构建 prompt (逻辑不变)
- `.mercury/docs/roles/` — 删除，内容合并到 YAML

---

## 附录 C: 对 TASK-PROMPT-IMPL-001 的具体建议

### C.1 Dispatch 模板迁移

**现状**: `buildDevPrompt()` 内嵌完整 TaskBundle JSON (~50行 prompt)。

**目标**: 精简 dispatch 为 ~10 行 + file references + skills preload。

**新 dispatch prompt 模板:**
```markdown
实现任务 {taskId}: {title}

任务详情: 读取 {taskFilePath}
项目上下文: 读取 {handoffFilePath}

## 执行协议
1. 读取 TaskBundle + readScope 全部文档
2. 实现 → 自动 verify (lint + test + scope-check)
3. 通过 → 填写 receipt 并 commit + push
4. 失败 → 自修复 1 次 → 仍失败则 STOP 并报告

允许修改: {allowedWriteScope概要}
禁止修改: {docsMustNotTouch概要}
```

**Skills 负责一致性** (不在 dispatch 中重复):
- `mercury-git-conventions` — 分支/commit/禁止操作
- `mercury-task-completion` — receipt 格式 + 完成序列
- `mercury-verify-gate` — auto-verify 步骤

### C.2 Prompt 构建改写方案

**`role-prompt-builder.ts` 改动:**
```typescript
// 现有: import { ROLE_CARDS } from "@mercury/core"
// 改为: 从 YAML 文件读取
import { loadRoleCard } from "./role-loader"; // 新模块

export function buildRoleSystemPrompt(role, task, ...) {
  const card = loadRoleCard(role); // YAML → object
  // 其余逻辑不变
}
```

**`task-manager.ts` 改动:**
- `buildDevPrompt()`: 保持 JSON 内嵌模式 (用于 Codex API 无文件访问场景)
- `buildReferencePrompt()`: 已是精简模式，增加 skills 引用字段
- 新增: `buildSubAgentPrompt()` — 用于 Claude Code native sub-agent 场景

### C.3 新增 `.claude/agents/dev-worker.md`

```yaml
---
name: dev-worker
description: "Mercury dev implementation agent. Use for all code implementation tasks."
tools: Read, Write, Edit, Bash, Grep, Glob, WebSearch, WebFetch
model: sonnet
skills:
  - mercury-git-conventions
  - mercury-task-completion
  - mercury-verify-gate
permissionMode: acceptEdits
memory: project
---

You are the Dev Agent for Mercury.

Your job: read the assigned TaskBundle, implement within allowed scope,
verify the result, and return an implementation receipt.

## Core Rules
- Read all required docs before changing code
- Stay within allowedWriteScope — never expand scope silently
- If ambiguous or blocked, STOP and report
- Prefer minimal, coherent edits compatible with existing architecture

## Git Safety
- NEVER switch branches, reset, rebase, or merge
- Commit only files within your allowedWriteScope
- Push only after self-verify passes
- Branch anomaly → STOP, report to Main Agent

## Completion
1. Auto-verify: lint + type-check + test + scope-check
2. Fill implementationReceipt (JSON)
3. git add <specific-files> && git commit
4. git push origin <current-branch>
5. Return structured completion message
6. STOP.
```

---

## 研究来源汇总

| 来源 | URL | 用于 |
|------|-----|------|
| Anthropic Best Practices | https://code.claude.com/docs/en/best-practices | Q1, Q2, Q4, Q8 |
| Anthropic Skills Docs | https://code.claude.com/docs/en/skills | Q2, Q3, Q4 |
| Anthropic Sub-agents Docs | https://code.claude.com/docs/en/sub-agents | Q2, Q5, Q6 |
| HumanLayer CLAUDE.md Guide | https://www.humanlayer.dev/blog/writing-a-good-claude-md | Q1, Q4 |
| AGENTS.md Issue #6235 | https://github.com/anthropics/claude-code/issues/6235 | Q1 |
| oh-my-claudecode | https://github.com/Yeachan-Heo/oh-my-claudecode | Q2, Q5, Q8 |
| open_claw | https://github.com/sscien/open_claw | Q3, Q4, Q6 |
| wshobson/agents | https://github.com/wshobson/agents | Q3 (已有研究) |
| claude-agent-framework | https://github.com/ciscoittech/claude-agent-framework | Q1 (已有研究) |
| ruflo | https://github.com/ruvnet/ruflo | Q8 (已有研究) |
| Loki Mode | https://github.com/asklokesh/loki-mode | Q8 |
| AGENTS.md vs CLAUDE.md 社区讨论 | WebSearch 2026 | Q1 |
| Token reduction strategies | https://dev.to/boucle2026 | Q1 |
