# 深度研究报告：Mercury Orchestrator Issue #101 Phase 2-3 实施策略

> **Report compiled**: 2026-03-31
> **Analyst**: Claude Code (main session, direct research)
> **Task**: TASK-5feb968d (supersedes TASK-3a3dafdf)
> **Status**: COMPLETE

---

## Phase 1 完成状态确认

**COMPLETED (PR#105 + PR#111)**

| 功能项 | 状态 | PR | 关键代码位置 |
|--------|------|----|----|
| 70% token checkpoint -> auto session handoff | Merged | #105 | orchestrator.ts:1757-1812 |
| Loop detection (EventBus listener) | Merged | #105 | orchestrator.ts:1818-1850 |
| Research agent output format (JSON) | Merged | #105 | task-manager.ts:1053-1118 |
| Configurable research loop cap (maxIterations) | Merged | #111 | task-manager.ts:1079-1082, types.ts:127 |

---

## Phase 2 实施方案（SHORT-TERM）

### 2.1 Verification-First Session Start

**目标**：Agent 启动时先执行 DoD pre-check

**涉及文件**：
- orchestrator.ts: prepareBundleTaskExecution() (~line 2617)
- task-manager.ts: buildDevPrompt() (~line 919)
- core/types.ts: TaskBundle interface

**类型扩展**：TaskBundle.verificationConfig — preSessionChecks (readGitLog, readTaskReceipt, readDoD, runTests), baselineTestCommands[]

**实施步骤**：
1. prepareBundleTaskExecution() 前插入 performVerificationFirstStart(task, session)
2. 读取 git log branch -20 --oneline 并注入 dispatch prompt
3. 若为 rework，附上前次 implementationReceipt 失败原因
4. DoD checklist 以 markdown checklist 格式嵌入 prompt
5. runBaselineTests 初版做 soft warning（log only），后续升级为 hard block

**风险**：低（只注入上下文）；git log 约 500-2000 tokens

---

### 2.2 Pre-Completion DoD Check

**目标**：Agent 完成前自动验证 DoD 条目

**涉及文件**：
- orchestrator.ts: handleDevTaskStreamComplete() (~line 1901) 之前
- core/types.ts: 新增 PreCompletionCheckResult

**类型**：PreCompletionCheckResult { dodItemsVerified[], passedChecks, failedChecks, verdict: PASS|FAIL|PARTIAL, summary }

**实施步骤**：
1. 在 handleDevTaskStreamComplete() 调用 parseImplementationReceipt() 后，新增 runPreCompletionChecks(task, receipt)
2. DoD 验证：用 Critic agent（buildCriticPrompt() 已存在）逐项判定
3. 可选：运行 npm test / linter，exit code 决定 verdict
4. 检查 docsMustUpdate 和 docsMustNotTouch 合规性
5. 结果写入 task.mainReview.preChecks[]，不阻塞，agent 自行决定是否修复

**风险**：中等（LLM judge 延迟 5-15s/item；timeout 超时视为 PARTIAL）

---

### 2.3 Token Budget Injection

**目标**：System prompt 注入 task-level token 预算指令

**涉及文件**：core/types.ts, orchestrator.ts (prepareBundleTaskExecution), task-manager.ts (buildDevPrompt/buildResearchPrompt)

**类型扩展**：
- ResearchConfig: maxOutputTokens? (Phase 2b, UNVERIFIED: needs claude CLI flag verification), cumulativeTokenBudget? (Phase 2c)
- TaskBundle.resourceBudget: tokenBudget?, warningThresholdPercent? (default 80)

**实施步骤**：
1. prepareBundleTaskExecution() 中，若有 resourceBudget.tokenBudget，在 rolePrompt 末尾追加预算提示块
2. streamMessages() 中累积 output tokens，超 80% 阈值时向 agent 发 wrap-up 提示
3. GUI SettingsPanel Research 标签页新增输入框
4. Phase 2b（CLI cap）需先 web 验证 claude CLI flags — UNVERIFIED

**风险**：低（informational only；与 TOKEN_CHECKPOINT_THRESHOLD 分层，不冲突）

---

## Phase 3 实施方案（STRATEGIC）

### 3.1 OpenTelemetry Integration

**新建文件**：
- packages/orchestrator/src/otel-tracer.ts — trace/span 生命周期
- packages/orchestrator/src/otel-exporter.ts — Jaeger/OTLP/Langfuse export

**配置**：MercuryConfig.otel?: OTelConfig { enabled, exporterType: jaeger|otel-collector|langfuse, jaegerEndpoint, otlpEndpoint, langfusePublicKey, langfuseSecretKey, sampleRate }

**集成点**：
- startRoleSession() -> otelTracer.startTrace(sessionId)
- streamMessages() 每条消息 -> otelTracer.recordEvent(sessionId, event)
- stopSession() -> otelTracer.endTrace(sessionId)

**风险**：低（enabled=false 时零开销；export 异步 fire-and-forget）

---

### 3.2 Eval Framework

**新建 package**：packages/eval/src/

```
eval-framework.ts          — loadDataset, runEval, computeMetrics
graders/code-grader.ts     — 测试 pass/fail, linter errors
graders/llm-grader.ts      — LLM-as-judge quality/completeness/correctness
datasets/representative-tasks.json  — 20 个基准任务
```

**核心指标**：passRate, avgTokensPerTask, avgDurationMs, byCategory, byDifficulty

**EvalTask**：{ taskId, title, context, definitionOfDone[], expectedOutcome, difficultyLevel: easy|medium|hard, category }

**风险**：中等（20 tasks x 5-10 min = 100-200 min；dataset 需手工策划）

---

### 3.3 Middleware Architecture Refactor

**新建目录**：packages/orchestrator/src/middleware/

**核心接口**：Middleware { name, enabled, priority, onSessionStart?, onSessionEnd?, onMessage?(ctx), onCompletion?(ctx) }

**内置 Middleware**：
- LoopDetectionMiddleware (priority 100): 检测重复 tool calls
- PreCompletionCheckMiddleware (priority 200): DoD 验证
- SecurityAnalysisMiddleware (priority 300): 扫描硬编码 secret、SQL injection

**集成**：streamMessages() 消息循环插入 middlewareStack.executeOnMessage(ctx)

**风险**：中等（streamMessages 是关键路径；try/catch 包装必须；每次 await 约 1-2ms 延迟）

---

### 3.4 Wave-Based Parallel Scheduling

**新建文件**：packages/orchestrator/src/task-scheduler.ts

**TaskBundle 扩展**：dependencies?: { blockedBy?: string[], mustRunAfter?: string[] }, scheduling?: { wave?, parallel? }

**算法**：拓扑排序 + wave 分配（含 cycle detection）-> Wave 0 串行 -> Wave 1+ Promise.all() 并行 -> Promise.allSettled() 等待 wave 完成

**风险**：中等（并行 branch push 竞争；wave 阻塞问题用 Promise.allSettled 缓解）

---

## Everything-Claude-Code 参考整合

| 领域 | Claude Code 模式 | Mercury 现状 | 改进建议 |
|------|-----------------|------------|---------|
| Hook chain | PreToolUse/PostToolUse/Stop（SDK 内部） | bash 脚本（进程隔离） | Phase 3 middleware 采纳内部 hook 模式 |
| MCP 工具注入 | tools 数组动态声明 | pre-fetch KB context 注入 prompt | 改为 lazy MCP invoke；prompt 列出 allowed tools |
| Session fork | fork: true 分支探索 | handoffSession 继承上下文 | Phase 3 暴露 fork API 给 Main Agent |
| 权限控制 | allowedTools 动态注入各角色 | 所有 dev 获相同工具集 | buildSystemRolePrompt() 显式声明禁止操作 |
| Resume token | 精确定位恢复点 | sessionId 已保存 | 结合 event sourcing 实现 deterministic replay |

---

## 冲突点与风险汇总

| 风险 | 影响 | 缓解策略 |
|------|------|---------|
| middleware 异常 crash streamMessages | 高 | try/catch 包装，失败 log-only，继续处理 |
| pre-completion check timeout | 中 | 超时 5-10s 视为 PARTIAL，不阻塞 |
| wave scheduling deadlock | 中 | computeWaves() 前 cycle detection |
| OTel export failure | 低 | 异步 fire-and-forget |
| loop detection false positive | 低 | warning-only |
| token budget vs checkTokenThreshold 混淆 | 低 | task-level (50k) vs session-level (70% of 200k) 明确分层 |

---

## 建议实施顺序

| 时间 | 任务 | 成本 | 关键 ROI |
|------|------|------|---------|
| Week 1 | Token budget injection (2.3) | 2-3h | token 节省 10-15% |
| Week 1 | Git log in dispatch prompt (2.1 初级) | 3-5h | rework 率降 20-30% |
| Week 2-3 | Pre-completion DoD check (2.2) | 8-10h | 接受度 +20% |
| Week 3-4 | Basic OTel traces (3.1) | 6-8h | 基础可观测性 |
| Week 5-8 | Middleware refactor (3.3) | 12-15h | 可维护性 +50% |
| Week 5-8 | Eval framework (3.2) | 10-12h | 持续量化 agent 效能 |
| Month 2+ | Wave-based parallel (3.4) | 8-10h | 并行 latency 降 40-60% |
| Month 2+ | Event sourcing checkpoint | 15-20h | crash recovery，token 节省 30-50% |

**MVP（1-2 周）**：Token budget injection + git log + Basic OTel，约 20h，agent 可靠性预期 +25%
**完整路线（3 个月）**：P0->P1->P2->P3，约 80-100h，agent 整体可靠性预期 +40%
