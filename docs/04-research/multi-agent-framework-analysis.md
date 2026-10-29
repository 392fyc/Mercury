# 多Agent框架吸收与任务流程优化研究

> 研究日期: 2026-03-20 | Session: session-6 | Handoff: R-multi-agent-research

---

## 研究对象概览

| 项目 | 核心特点 | Mercury可吸收价值 |
|------|----------|-------------------|
| [wshobson/agents](https://github.com/wshobson/agents) | 112 agents + 16 orchestrators, 三层渐进式加载 | Agent分类法、Skill复用、Model分层路由 |
| [ciscoittech/claude-agent-framework](https://github.com/ciscoittech/claude-agent-framework) | 97% context缩减, 自动生成配置 | 极简core + 按需加载、SYSTEM_GENERATOR |
| [oh-my-claudecode](https://github.com/Yeachan-Heo/oh-my-claudecode) | Teams流水线, 多Provider | Team阶段门控、Verify-Fix循环 |
| [open_claw](https://github.com/sscien/open_claw) | 94章教程, hooks/skills深度指南 | Hook四类型体系、Skill frontmatter规范 |
| [claude-squad](https://github.com/smtg-ai/claude-squad) | tmux多实例, worktree隔离 | Session隔离策略、Dashboard监控 |
| [ruflo](https://github.com/ruvnet/ruflo) | Swarm+259 MCP tools, Q-Learning路由 | 复杂度路由、Anti-drift、Token压缩 |

---

## 1. 多Agent框架架构模式

### 1.1 Agent分类与组织

**发现: 三种组织范式**

| 范式 | 代表项目 | 结构 | 适用规模 |
|------|----------|------|----------|
| **Plugin分类法** | wshobson/agents | 24类目 → 72 plugins → 112 agents | 大型，需按需安装 |
| **Core+Library** | claude-agent-framework | 3 core agents + 按需specialist | 中型，极简启动 |
| **Queen-Worker层级** | ruflo | Queen(策略/战术/自适应) + 8类Worker | 复杂任务，需协调 |

**wshobson/agents的Plugin粒度设计:**
- 每plugin平均3.4个组件 → 最小化token加载
- 安装命令: `/plugin install python-development` 只加载该领域agents
- 三层渐进式加载: Metadata(始终) → Instructions(激活时) → Resources(按需)
- 来源: https://github.com/wshobson/agents

**claude-agent-framework的极简启动:**
- `.claude/` 核心目录 <10KB auto-loaded
- `.claude-library/` 扩展能力按需加载
- REGISTRY.json 从1154行→421行 (64%缩减)
- 来源: https://github.com/ciscoittech/claude-agent-framework

> **Mercury可落地建议 #1**: 采用 Core+Library 模式。Mercury当前的 `.mercury/docs/` 和 `CLAUDE.md` 承载了所有指令，应拆分为:
> - `.claude/` (core): 角色定义、scope guard、基础workflow — 始终加载 (<10KB)
> - `.claude-library/` (extended): 详细模板、KB结构指南、架构文档 — 按需引用
> - 效果预期: 减少每次session启动的context消耗约60-70%

### 1.2 协调策略

**发现: 四种协调模型**

| 模型 | 项目 | 机制 | Mercury适用场景 |
|------|------|------|-----------------|
| **Sequential Pipeline** | wshobson/agents | backend→db→frontend→test→security→deploy | 全栈feature开发 |
| **Staged Pipeline + Gate** | oh-my-claudecode | plan→prd→exec→verify→fix(loop) | 所有TaskBundle |
| **Parallel Worktree** | claude-squad | git worktree隔离 + tmux session | 并行开发任务 |
| **Complexity Router** | ruflo | Q-Learning分析复杂度→路由到合适handler | 任务分发优化 |

**oh-my-claudecode的Verify-Fix循环:**
- `team-plan → team-prd → team-exec → team-verify → team-fix (loop)`
- verify阶段防止静默失败
- fix阶段持续重试直到完成
- 来源: https://github.com/Yeachan-Heo/oh-my-claudecode

**ruflo的复杂度路由:**
- Simple → Agent Booster (WASM, <1ms, 不需LLM)
- Medium → Haiku/Sonnet (~500ms)
- Complex → Opus + Swarm协调 (2-5s)
- Hook输出提供优化信号: `[AGENT_BOOSTER_AVAILABLE]`, `[TASK_MODEL_RECOMMENDATION]`
- 来源: https://github.com/ruvnet/ruflo

> **Mercury可落地建议 #2**: 在TaskBundle中增加 `complexity` 字段和 verify gate:
> ```json
> {
>   "complexity": "simple|medium|complex",
>   "modelRecommendation": "haiku|sonnet|opus",
>   "verifyGate": {
>     "enabled": true,
>     "checks": ["tests_pass", "scope_clean", "no_regressions"]
>   }
> }
> ```
> - simple任务 → Haiku agent处理，跳过acceptance
> - complex任务 → Opus处理，强制verify+acceptance
> - 效果预期: 减少simple任务的token消耗80%+

### 1.3 Model分层路由

**wshobson/agents的四层模型策略:**

| Tier | Model | Agent数 | 用途 |
|------|-------|---------|------|
| Tier 1 | Opus | 42 | 架构、安全、code review |
| Tier 2 | Inherit | 42 | 复杂任务，用户选择 |
| Tier 3 | Sonnet | 51 | 文档、测试、调试 |
| Tier 4 | Haiku | 18 | SEO、部署、简单文档 |

来源: https://github.com/wshobson/agents

> **Mercury可落地建议 #3**: TaskBundle的 `assignedTo.model` 应根据任务类型自动推荐:
> - 架构/安全审查 → Opus
> - 功能开发/bugfix → Sonnet (默认)
> - 文档/格式化/简单修复 → Haiku
> - 研究/分析 → Opus (需要深度推理)

---

## 2. 任务流程规范化

### 2.1 结构化Prompt使产出更准确

**发现: 三种有效的prompt结构化模式**

**模式A: Interview-before-Execute (open_claw)**
- 实现前先问澄清问题: scope、约束、成功标准
- 防止需求偏差
- 来源: https://github.com/sscien/open_claw

**模式B: Writer-Reviewer分离 (open_claw)**
- 一个agent实现，另一个review
- 分离关注点，无需人工干预即可发现错误
- 来源: https://github.com/sscien/open_claw

**模式C: Deep-Interview (oh-my-claudecode)**
- `/deep-interview` 使用苏格拉底式提问澄清模糊需求
- 跨加权维度测量需求清晰度
- 来源: https://github.com/Yeachan-Heo/oh-my-claudecode

> **Mercury可落地建议 #4**: 改进dispatch prompt模板，增加「前置确认」段:
> ```markdown
> ## Dispatch Prompt (Dev Agent)
>
> ### 任务
> {一句话描述}
>
> ### 前置确认 (Agent自检，不需回报)
> - [ ] TaskBundle已读完
> - [ ] readScope文档已全部读取
> - [ ] 理解allowedWriteScope边界
> - [ ] 确认definitionOfDone可验证
>
> ### 歧义升级
> 如遇以下情况，立即停止并上报:
> - definitionOfDone有主观判断项
> - codeScope与实际代码结构不匹配
> - 需要的API/SDK文档未在readScope中
> ```

### 2.2 Skill复用降低重复prompt

**wshobson/agents的146个Skill复用模式:**
- 三层架构: Metadata(始终加载) → Instructions(激活时) → Resources(按需)
- 自动激活: 用户说"Create FastAPI service" → 自动加载 async-python-patterns + testing-patterns + uv-package-manager
- 来源: https://github.com/wshobson/agents

**oh-my-claudecode的Skill提取:**
- `/learner` 命令从成功模式中提取skill
- YAML frontmatter: name, description, triggers
- 项目级skill覆盖用户级skill
- 来源: https://github.com/Yeachan-Heo/oh-my-claudecode

> **Mercury可落地建议 #5**: 创建 `.claude/skills/` 目录，将重复性workflow封装为skill:
> ```yaml
> # .claude/skills/mercury-pr-flow.md
> ---
> name: Mercury PR Flow
> description: Standard PR creation with CodeRabbit wait
> triggers: ["pr", "pull request", "merge"]
> ---
> 1. git push -u origin {branch}
> 2. gh pr create --title "{title}" --body "{body}" --base develop
> 3. 等待CodeRabbit review完成
> 4. 如有建议，修复后追加commit
> 5. 通知Main Agent审查
> ```
> 将 dev-standard-workflow、git-flow 等高频workflow转为skill，减少每次dispatch的prompt长度

### 2.3 TaskBundle模板改进

当前Mercury TaskBundle已经有良好的scope control，但缺少几个关键字段:

> **Mercury可落地建议 #6**: TaskBundle模板增强:
> ```json
> {
>   // 新增字段
>   "complexity": "simple|medium|complex",
>   "modelRecommendation": "haiku|sonnet|opus",
>   "estimatedTokenBudget": 50000,
>   "verifyGate": {
>     "autoChecks": ["lint", "type-check", "test"],
>     "manualChecks": ["code-review"]
>   },
>   "contextInjection": {
>     "skills": ["mercury-pr-flow"],
>     "rules": [".claude/rules/tauri-patterns.md"]
>   },
>   "escalationPolicy": {
>     "maxSelfRetry": 1,
>     "escalateAfter": "second_failure",
>     "escalateTo": "main"
>   }
> }
> ```

---

## 3. 高效任务发布

### 3.1 Context浪费的根因与对策

**发现: context浪费的4大来源**

| 来源 | 项目提供的解决方案 | Mercury现状 |
|------|-------------------|-------------|
| **启动时全量加载** | claude-agent-framework: <10KB core, 按需library | CLAUDE.md + .mercury/docs 全量注入 |
| **重复说明workflow** | wshobson: skill自动激活 | 每次dispatch重复写workflow |
| **大文档注入context** | ruflo: Token Optimizer -30~50% | handoff/task JSON全文读入 |
| **无效重试消耗** | ruflo: Anti-drift短周期+checkpoint | 无自动checkpoint机制 |

**claude-agent-framework的context极简策略:**
- Auto-loaded: agent-launcher.md (1KB max) + settings.json (0.5KB max)
- 按需loaded: REGISTRY.json (2KB max) + specialist agents
- 总生成量: simple项目 <5KB
- 来源: https://github.com/ciscoittech/claude-agent-framework

**ruflo的Token压缩:**
- ReasoningBank检索 (-32% tokens)
- Agent Booster直接编辑 (-15%)
- 缓存机制 (-10%)
- 最优batch size (-20%)
- 来源: https://github.com/ruvnet/ruflo

> **Mercury可落地建议 #7**: 实施三级context加载策略:
>
> **Level 0 (始终加载, <5KB)**:
> - 角色定义 (1段话)
> - Scope guard规则
> - Git flow要点 (3条)
> - 当前session状态 (1行)
>
> **Level 1 (TaskBundle触发加载)**:
> - 具体task的codeScope文档
> - 相关skill定义
> - 相关rules文件
>
> **Level 2 (按需引用, 不主动注入)**:
> - 架构文档
> - KB结构指南
> - 历史handoff
> - 模板文件
>
> **实现方式**: 精简CLAUDE.md为Level 0内容，将Level 1/2内容移至 `.claude-library/` 并通过TaskBundle的 `contextInjection` 字段指定

### 3.2 减少Prompt反复

**发现: prompt反复的根因是「缺少持久化的决策记录」**

**ruflo的3-scope memory:**
- Project scope: 项目级知识
- Local scope: 当前task上下文
- User scope: 用户偏好
- 跨agent可transfer
- 来源: https://github.com/ruvnet/ruflo

**claude-agent-framework的MEMORY.md:**
- 跨conversation状态追踪
- 持久化决策和发现
- 来源: https://github.com/ciscoittech/claude-agent-framework

> **Mercury可落地建议 #8**: 建立「决策日志」快速查找机制:
> - `Mercury_KB/03-decisions/` 已有决策记录，但缺少索引
> - 在 `Mercury_KB/00-index/` 增加 `decisions-index.md`，每条决策一行: `日期 | 决策标题 | 关键词`
> - TaskBundle的 `readScope.optionalDocs` 自动包含相关决策ID
> - 效果: agent不需要重新讨论已决定的问题

---

## 4. Agent自主推进

### 4.1 Hooks实现自动化

**open_claw的Hook四类型体系:**

| Hook类型 | 触发时机 | 自动化用途 |
|----------|----------|-----------|
| PreToolUse | 工具执行前 | Scope guard、lint检查、安全扫描 |
| PostToolUse | 工具执行后 | 自动测试、格式化、提交 |
| Notification | 长时操作中 | 进度通知、超时告警 |
| Stop | Session结束前 | 验证完整性、生成receipt |

来源: https://github.com/sscien/open_claw

**Mercury已有scope-guard hook** (PreToolUse)。可扩展:

> **Mercury可落地建议 #9**: 扩展Hook体系:
> ```
> .claude/hooks/
> ├── scope-guard.sh          # [已有] PreToolUse - 阻止越界写入
> ├── post-commit-verify.sh   # [新增] PostToolUse(Bash:git commit) - 自动运行lint+test
> ├── auto-push.sh            # [新增] PostToolUse(Bash:git commit) - commit后自动push
> ├── session-receipt.sh       # [新增] Stop - session结束时生成receipt到KB
> └── context-budget-warn.sh  # [新增] Notification - context接近限制时告警
> ```

### 4.2 Verification Gate自动化

**oh-my-claudecode的verify-fix循环:**
- team-exec完成后自动进入team-verify
- verify失败 → 自动team-fix → 重新verify
- 持续循环直到通过或达到maxReworks
- 来源: https://github.com/Yeachan-Heo/oh-my-claudecode

**ruflo的Anti-drift控制:**
- 频繁checkpoint via post-task hooks
- 共享memory命名空间
- 短任务周期 + 验证gate
- 协调者review所有输出
- 来源: https://github.com/ruvnet/ruflo

> **Mercury可落地建议 #10**: 在TaskBundle workflow中嵌入自动verify:
> ```
> 当前流程: dispatch → implement → receipt → main review → acceptance
> 改进流程: dispatch → implement → AUTO-VERIFY → receipt → main review → acceptance
>
> AUTO-VERIFY (agent自行执行，不需dispatch):
> 1. npm run lint (或项目lint命令)
> 2. npm run type-check
> 3. npm run test (affected files)
> 4. git diff --stat 确认只改了allowedWriteScope内的文件
> 5. 全部通过 → 填写receipt
> 6. 任一失败 → 自行修复 (maxSelfRetry=1) → 再次verify
> 7. 二次失败 → 升级到main
> ```

### 4.3 Session隔离与并行

**claude-squad的worktree隔离:**
- 每个session独立git worktree
- 独立分支，无同步开销
- TUI dashboard监控所有session状态
- 来源: https://github.com/smtg-ai/claude-squad

**claude-agent-framework的并行策略:**
- `isolation: "worktree"` + `run_in_background: true`
- 3-6x速度提升
- 来源: https://github.com/ciscoittech/claude-agent-framework

> **Mercury可落地建议 #11**: 为并行任务标准化worktree使用:
> - TaskBundle增加 `isolation: "worktree"|"branch"|"none"` 字段
> - 并行任务(互不依赖) → worktree隔离
> - 串行任务(有依赖) → 同branch顺序执行
> - Mercury UI可显示各worktree session的状态

### 4.4 自动错误恢复

**oh-my-claudecode的Ralph模式:**
- 持久化执行 + verify/fix循环
- 包含ultrawork模式(最大并行)
- 来源: https://github.com/Yeachan-Heo/oh-my-claudecode

**oh-my-claudecode的Rate-limit处理:**
- `omc wait --start` 启动auto-resume daemon
- session恢复后自动继续
- 来源: https://github.com/Yeachan-Heo/oh-my-claudecode

> **Mercury可落地建议 #12**: TaskBundle增加自动恢复策略:
> ```json
> {
>   "resilience": {
>     "maxSelfRetry": 1,
>     "onRateLimit": "wait_and_resume",
>     "onContextExhaustion": "checkpoint_and_handoff",
>     "checkpointInterval": "every_milestone"
>   }
> }
> ```

---

## 5. Mercury KB结构改进建议清单

基于以上研究，以下是对Mercury KB结构的具体改进建议:

### 5.1 目录结构优化

```
Mercury_KB/
├── 00-index/
│   ├── README.md                    # [保留]
│   ├── AI-Handoff-Guide.md          # [保留]
│   └── decisions-index.md           # [新增] 决策快速索引
├── 01-research/                     # [保留]
├── 02-context/
│   ├── current-session.md           # [保留] 精简为1-2行状态
│   ├── HANDOFF-*.json               # [保留] 但增加TTL字段
│   └── PLAN-*.md                    # [保留]
├── 03-decisions/                    # [保留]
├── 10-tasks/                        # [保留]
├── 20-acceptances/                  # [新增] 独立acceptance记录
├── 99-templates/                    # [保留]
└── archive/                         # [新增] 已完成task/handoff归档
```

### 5.2 Handoff文件增加TTL

当前handoff文件无过期机制，随时间累积增加KB噪音。

```json
{
  "expiresAt": "2026-04-01",
  "archivePolicy": "move_to_archive_after_expiry"
}
```

### 5.3 精简CLAUDE.md

当前CLAUDE.md + .mercury/docs 的全量注入是context浪费的主要来源。建议:

| 保留在CLAUDE.md (Level 0) | 移至.claude-library (Level 1/2) |
|---------------------------|--------------------------------|
| 角色识别 (3行) | 详细角色文档 (.mercury/docs/roles/) |
| MUST/DO NOT (精简版, 各5条) | 完整workflow文档 |
| Git基本规则 (3条) | git-flow详细文档 |
| KB路径表 (当前已有) | KB结构详细说明 |
| 当前session一句话状态 | 历史session上下文 |

**目标**: CLAUDE.md < 2KB，Level 1按需加载

---

## 6. TaskBundle / Prompt模板改进建议

### 6.1 TaskBundle模板 v2

在现有 `task-bundle.template.json` 基础上增加:

```json
{
  // === 新增: 任务元数据 ===
  "complexity": "simple|medium|complex",
  "modelRecommendation": "haiku|sonnet|opus",
  "estimatedTokenBudget": null,

  // === 新增: Context注入 ===
  "contextInjection": {
    "skills": [],
    "rules": [],
    "decisions": []
  },

  // === 新增: 自动验证 ===
  "verifyGate": {
    "autoChecks": ["lint", "type-check", "test"],
    "manualChecks": ["code-review"],
    "scopeCheck": true
  },

  // === 新增: 韧性策略 ===
  "resilience": {
    "maxSelfRetry": 1,
    "escalateAfter": "second_failure",
    "escalateTo": "main",
    "onContextExhaustion": "checkpoint_and_handoff"
  },

  // === 新增: 隔离策略 ===
  "isolation": "worktree|branch|none"
}
```

### 6.2 Dispatch Prompt模板 v2

```markdown
# Dispatch: {taskId}

{一句话任务目标}。

**TaskBundle**: `Mercury_KB/10-tasks/{taskId}.json`
**允许写入**: {allowedWriteScope概要}
**复杂度**: {complexity} → 建议model: {modelRecommendation}

## 执行协议
1. 读取TaskBundle + readScope全部文档
2. 实现 → 自动verify (lint + test + scope-check)
3. 通过 → 填写receipt并push
4. 失败 → 自修复1次 → 仍失败则升级

## 不需要做
- 不修改scope外文件
- 不猜测设计意图
- 不跳过verify
```

### 6.3 Handoff模板 v2

增加结构化的「下一步行动」和TTL:

```json
{
  "handoffId": "...",
  "expiresAt": "2026-04-01",
  "nextActions": [
    {
      "actionId": "A1",
      "description": "...",
      "taskBundleRef": "TASK-XXX-001",
      "priority": "Sev-2",
      "blockedBy": null
    }
  ],
  "contextSummary": "一句话当前状态",
  "keyDecisions": ["DEC-xxx引用"],
  "openQuestions": []
}
```

---

## 附录: 各项目关键URL索引

| 项目 | GitHub URL | 核心文档 |
|------|-----------|----------|
| wshobson/agents | https://github.com/wshobson/agents | README (plugin taxonomy, model tiers) |
| claude-agent-framework | https://github.com/ciscoittech/claude-agent-framework | SYSTEM_GENERATOR_PROMPT.md |
| oh-my-claudecode | https://github.com/Yeachan-Heo/oh-my-claudecode | README (Team pipeline, Ralph mode) |
| open_claw | https://github.com/sscien/open_claw | TUTORIAL.md (94 chapters) |
| claude-squad | https://github.com/smtg-ai/claude-squad | README (worktree isolation) |
| ruflo | https://github.com/ruvnet/ruflo | README (Q-Learning router, anti-drift) |
