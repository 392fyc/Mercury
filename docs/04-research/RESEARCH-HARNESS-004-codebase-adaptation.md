Phase 2-3 KB write test
# Phase 2-3 Codebase Adaptation Research

> compiled: 2026-03-31 | analyst: Claude Code main session | COMPLETE

---

## 一、精确插入点

### Phase 2.1 Git Log — task-manager.ts buildDevPrompt() ~L994

插入位置：template substitution 完成后，kbContext append 前

上下文：
  if (kbContext) { result += kbContext section }  // 现有代码

修改：在此前调用 buildGitLogSection(task.branch, basePath)，
      结果作为 ## Git Implementation History 节追加到 result。

复用：TaskBundle.branch（已有字段）

---

### Phase 2.2 DoD Check — orchestrator.ts handleDevTaskStreamComplete() ~L1911

插入位置：parseImplementationReceipt() 调用后，recordReceiptAndTriggerReview() 调用前

修改：插入 await this.verifyDoDCompletion(task, receipt, agentId)

策略：non-blocking，失败时 emit 'orchestrator.dod.incomplete' 事件，不阻断流程

复用：buildCriticPrompt() L1436（DoD LLM judge，via executeOneShot 而非完整 Critic session）
      parseAcceptanceResult() L2199（pass/fail/partial 判定框架）

---

### Phase 2.3 Token Budget — orchestrator.ts buildSystemRolePrompt() L305

插入位置：buildRoleSystemPrompt() 调用参数中增加 tokenBudget 参数

修改：计算 floor((session.tokenLimit - session.tokenUsage) * 0.9) 作为安全预算
      传入 buildRoleSystemPrompt() 新增的可选参数

复用：SessionInfo.tokenUsage（已有）、SessionInfo.tokenLimit（已有）

---

### Phase 3.1 OTel — orchestrator.ts streamMessages() 三处

L1557 函数体开始：tracer?.startSpan('stream_messages', {agentId, sessionId})
L1637 stream 完成：tracer?.endSpan(span, {status:'success'})
L1649 catch 块：  tracer?.endSpan(span, {status:'error', error})

新增字段：Orchestrator.tracer?: OTelTracer（可选，基于 config.otel.enabled）

---

### Phase 3.3 Middleware — orchestrator.ts streamMessages() for-await 循环体

插入位置：bus.emit('agent.message.receive') 后，appendTranscriptMessage() 前

修改：for-of 遍历 this.messageMiddleware，每个 mw 用 try-catch 包裹
      mw 失败时 log-only，不中断 stream

新增字段：Orchestrator.messageMiddleware: MessageMiddleware[] = []

---

### Phase 3.4 Wave Scheduling — orchestrator.ts dispatchBundleTask() retry for-loop

修改：将现有 backoffDelay 替换为 Math.max(waveDelay, backoffDelay)
      wave delay 和 backoff 取最大值而非累加，避免双重延迟

新增字段：Orchestrator.scheduler?: WaveScheduler

---

## 二、可复用现有代码

detectToolLoop() orchestrator.ts L1818-1842
  -> 直接提取为 LoopDetection Middleware，6-item 窗口，3次连续 = 循环

checkTokenThreshold() orchestrator.ts L1721-1751
  -> Phase 2.3 在此函数内追加 task-level budget 检查，复用 tokenUsage/tokenLimit 比率模式

buildCriticPrompt() orchestrator.ts L1436
  -> Phase 2.2 DoD LLM judge，via one-shot 调用（非完整 Critic session，控制成本）

parseImplementationReceipt() orchestrator.ts L2131
  -> Phase 2.2 依赖此解析结果，无需修改

buildDevPrompt() append 模式 task-manager.ts L994
  -> Phase 2.1 git log 采用相同的 append 模式（与 kbContext 一致）

---

## 三、类型系统扩展

TaskBundle 新增字段（Phase 2-3）：
  tokenBudgetUsed?: number        // Phase 2.3: 本次 dispatch 前已消耗 token
  estimatedTokens?: number        // Phase 2.3: 估算 token 数（基于历史）
  dispatchWaveId?: string         // Phase 3.4: wave batch 标识
  scheduledDispatchAt?: number    // Phase 3.4: 实际 dispatch 时间戳

TaskBundle 已有相关字段（无需修改）：
  branch, definitionOfDone, implementationReceipt, mainReview,
  dispatchAttempts, priority, role

SessionInfo：已有字段完全满足 Phase 2-3 需求
  tokenUsage, tokenLimit, tokenCheckpointAt

MercuryConfig 新增：
  otel?: OTelConfig               // { enabled, exporterEndpoint, serviceName }
  waveScheduling?: WaveSchedulingConfig  // { enabled, maxConcurrentDispatch, rateLimitPerSecond }

新增类型定义：
  OTelConfig, WaveSchedulingConfig, MessageMiddleware, MiddlewareContext, WaveScheduler

---

## 四、角色 Prompt 现状与注入方案

buildDevPrompt() 当前结构：
  L978-987: template substitution (taskId, context, bundleJson, receiptTemplate)
  L994-996: kbContext 追加（可选）

Phase 2.1 git log：在 L994 前，以相同 append 模式插入
Phase 2.3 token budget：在 buildSystemRolePrompt() 中注入，追加到 Completion 节前：
  "Token Budget: ~{N}K tokens remaining. Plan implementation within this budget."

buildRoleSystemPrompt() 从 .mercury/roles/{role}.yaml 加载，
包含：Role, Responsibility, Allowed Actions, Forbidden Actions, Completion 节。

---

## 五、适配风险（基于实际代码）

Risk 1：streamMessages 复杂度（L1568-1648）
  现状：token 追踪 + loop detection + dev/research/design 多路分支
  缓解：middleware try-catch per mw；token count 在 middleware 前完成；loop detection 在 middleware 后运行

Risk 2：DoD check 成本
  现状：buildCriticPrompt() 启动完整 Critic session，成本高
  缓解：改用 executeOneShot（单轮评估）；超时 5s 视为 PARTIAL

Risk 3：Token budget 精度
  现状：streaming token count 有模型差异
  缓解：floor(remaining * 0.9) 保守系数

Risk 4：Wave delay 与 backoff 叠加
  现状：dispatchBundleTask() L2846 已有指数退避
  缓解：Math.max(waveDelay, backoffDelay) 而非累加

Risk 5：OTel 与 EventBus 重复
  现状：EventBus 已转发所有 orchestrator 事件为 RPC 通知
  缓解：OTel 限定 streamMessages critical path，enabled=false 时零开销

---

## 六、代码量估算

Phase 2.1:  新增 30-50 行，修改 3 行
Phase 2.2:  新增 80-120 行，修改 5 行
Phase 2.3:  新增 20-40 行，修改 8 行
Phase 3.1:  新增 40-60 行，修改 12 行
Phase 3.3:  新增 50-80 行，修改 8 行
Phase 3.4:  新增 100-150 行，修改 15 行
类型扩展:   新增 30-50 行，修改 10 行
总计:       新增 350-550 行，修改约 61 行

---

## 七、依赖关系图

Phase 2.1: 独立，无依赖
Phase 2.2: 依赖 parseImplementationReceipt (存在) + buildCriticPrompt (存在)
Phase 2.3: 依赖 SessionInfo.tokenUsage/tokenLimit (存在) + checkTokenThreshold (存在)
Phase 3.1: 独立，可与 Phase 2.x 并行开发
Phase 3.3: 依赖 streamMessages loop body (存在) + detectToolLoop 模式 (存在)
Phase 3.4: 依赖 dispatchBundleTask retry logic (存在) + TaskBundle.priority (存在)
