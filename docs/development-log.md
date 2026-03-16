# Mercury 开发日志

> 持续更新 — 记录每个阶段的功能实现、技术决策、遇到的问题及解决方案

---

## Phase 0 — PoC 可行性验证

**提交**: `136a716` → `12e68c0`

验证了 Claude Agent SDK、Codex CLI SDK、opencode HTTP 模式的可集成性。
详见 [poc-report.md](./poc-report.md)。

---

## Phase 1 — MVP GUI 外壳

**提交**: `12e68c0`

### 实现内容
- Tauri 2 + Vue 3 项目脚手架
- Node.js sidecar（JSON-RPC 2.0 over stdio）
- 基础 AgentPanel 聊天界面
- EventLog 事件流面板
- Sidecar 生命周期管理（spawn + shutdown）

---

## Phase 2-3 — 端到端 Agent 调度 + KB 集成 + Settings UI

**提交**: `e314752`

### 实现内容
- SDK 适配器修复（Claude/Codex/opencode）
- 可选 KB（Obsidian CLI）集成
- Settings 面板（Agents/Project/Display 三 Tab）
- 配置热重载（updateConfig RPC）

---

## Phase 4 — SoT 任务编排协议（类型层）

**提交**: `7bd20d2`

### 实现内容
- `TaskBundle` 完整接口（作用域控制、完成标准、验收交接、返工追踪）
- `ImplementationReceipt`、`AcceptanceBundle`、`IssueBundle` 类型
- `TaskStatus` 状态枚举（drafted → dispatched → in_progress → ... → closed）
- `TaskAssignee` 结构化元数据（agentId + model + sessionId）
- EventType 扩展（task.created, task.status_change, acceptance.*, issue.*）
- Rust Tauri 命令透传（create_task, get_task, list_tasks, record_receipt, 等 9 个）
- 前端 Bridge 类型镜像

### 技术决策
- **内存存储优先**：TaskBundle 存 Map，原型阶段不做持久化（Phase 6 再加）
- **Prompt 构建在 Orchestrator 端**：Agent 适配器不感知 TaskBundle，只收纯文本 prompt
- **向后兼容 dispatch_task**：有 taskId 走 bundle 流程，否则走旧的简单流程

---

## Phase 5 — Slash Command 支持

**提交**: `aead134` → `959537c`

### 实现内容
- `SlashCommand`、`SlashCommandArg` 类型定义
- `AgentAdapter.getSlashCommands()` 接口
- 各适配器声明命令列表（Claude 40+、Codex 20+、opencode 15+）
- `get_slash_commands` RPC 全链路（Orchestrator → Rust → Bridge）

### 遗留
- SlashCommandPalette.vue 弹出面板尚未实现（前端 UI）

---

## Phase 6 — Agents First + Task Persistence + Task Dashboard UI

**提交**: `414fead`

### 实现内容
- `AgentConfig.model` 字段（e.g. "claude-opus-4-6", "o3"）
- `MercuryEvent.modelId` 字段
- `TaskAssignee` 结构化元数据（agentId + model + sessionId）
- TaskManager 完整实现（~569 行）：状态机、CRUD、验收、Issue、会话绑定、prompt 构建
- TaskPersistenceKB（~89 行）：KB JSON 文件持久化（tasks/、acceptances/、issues/）
- Task Store（~95 行）：前端状态管理 + MercuryEvent 监听自动刷新
- TaskDashboard.vue（~481 行）：完整 UI — Summary Bar + Task List + Detail Panel + 操作按钮
- TitleBar Agents/Tasks 视图切换 Tab
- App.vue 视图切换逻辑 + Task Store 初始化

---

## Phase 7（当前）— Slash Command 拦截 + 图片传输 + 跨 Agent 上下文共享 + Settings UI 重构

**提交**: `39c3eb2` → `f3763d7`

### 7.1 Slash Command 适配器拦截

**目标**：SDK 模式下 `/xxx` 命令不会被 SDK 识别（产生 "Unknown skill" 错误），需在适配器层拦截处理。

**实现**：
- 三个适配器（Claude/Codex/opencode）均实现 `handleSlashCommand()` 异步生成器
- `sendPrompt()` 检测 `/` 前缀 → 调用拦截器 → 有输出则返回，无输出则透传给 SDK

**最终策略（经迭代优化）**：
- **只拦截能实际实现的命令**：`/help`（显示命令列表）、`/clear`/`/new`（结束 session）、`/status`（显示 session 状态）、`/exit`/`/quit`（结束 session）
- **其他所有命令透传给 SDK/模型**：模型自行处理或响应，不做无用拦截

**遇到的问题**：
1. 初版拦截了 40+ 命令，每个都返回 "不支持" 消息 → 用户反馈：不如直接用原生 CLI
2. 中间版本改为"诚实说明尚未支持" → 仍然是无用拦截，用户无法获得任何功能
3. **最终方案**：最小拦截原则——只拦截 4-5 个我们能真正实现功能的命令，其余全部透传

### 7.2 图片传输管道

**目标**：支持在 GUI 中粘贴/拖放图片，传递给 Agent 进行多模态分析。

**实现（7 层全链路）**：
```
Vue (paste/drag → base64)
  → tauri-bridge (ImageAttachment 类型)
    → Rust command (images: Option<serde_json::Value>)
      → JSON-RPC (send_prompt with images param)
        → Orchestrator (转发给适配器)
          → Adapter (构建 content blocks)
            → SDK query()
```

**类型定义**：
```typescript
export type ImageMediaType = "image/png" | "image/jpeg" | "image/gif" | "image/webp";
export interface ImageAttachment {
  data: string;         // base64 编码
  mediaType: ImageMediaType;
  filename?: string;
  width?: number;
  height?: number;
}
```

**AgentPanel 功能**：
- `handlePaste()` — 监听 Ctrl+V 粘贴图片
- `handleDrop()` — 拖放图片到聊天区
- 待发送图片预览（缩略图 + 删除按钮）
- 消息中内联图片显示

**遇到的问题**：
1. **图片在 RPC 通知中丢失**（Code Review 发现，Score 85）：`orchestrator.ts` 的 `streamMessages` 和 `messages.ts` 的 `onAgentMessage` 都遗漏了 `images` 字段 → 修复：添加 `images: message.images`
2. **"(image attached)" 占位文本**（Score 75）：纯图片发送时 AgentPanel 发送了字面 "(image attached)" 作为 prompt → 修复：传空字符串，适配器在需要时添加最小回退文本
3. **Claude SDK 多模态限制**：SDK `query({ prompt: string })` 只接受字符串，不支持原生 content array → 使用 `JSON.stringify(contentBlocks)` 作为 best-effort 方案，添加注释说明待 SDK 支持后升级
4. **`effectivePrompt as string` 不安全类型断言**（Score 75）→ 修复：正确类型化为 `string`，使用 `JSON.stringify`

### 7.3 跨 Agent 上下文共享

**目标**：KB 文件自动注入为共享上下文，使用系统 prompt 机制，不占用单 Agent 对话窗口。

**实现**：
- `AgentAdapter` 接口新增 `setSystemPrompt(prompt: string): void`
- **Claude 适配器**：使用 SDK 原生 `options.systemPrompt`（真正的系统级注入，不消耗对话上下文）
- **Codex/opencode 适配器**：无原生系统 prompt 支持 → 使用 prompt prepending（`[System Context]...\n[User Prompt]...`）
- `Orchestrator.buildAndInjectContext()` — 从 KB contextFiles 构建上下文 → 分发给所有 Agent 适配器
- RPC：`refresh_context`（手动重建）、`get_context_status`（查询状态）
- `updateConfig()` 热重载 — 检测 obsidian 配置变更自动重注入/清除
- Settings UI — Shared Context 状态徽章 + Refresh Context 按钮

**遇到的问题**：
1. **Codex/opencode 每次消息都注入完整上下文**（Code Review 发现，High）：初版在每次 `sendPrompt` 时都 prepend 系统上下文 → 修复：使用 `systemPromptSentSessions: Set<string>` 追踪，仅在每个 session 的首条消息注入
2. **`setSystemPrompt()` 更新后旧 session 仍用旧上下文** → 修复：`setSystemPrompt()` 中 `clear()` sent-sessions set，下次消息自动重注入
3. **`buildAndInjectContext` 返回的 agentCount 可能偏高**（Low）：单个 adapter 的 `setSystemPrompt` 失败被 catch 吞掉，但 count 仍计入 → 已知问题，仅影响 UI 显示

### 7.4 TitleBar 增强

**实现**：
- 项目名称 + git 分支显示（从 `getProjectInfo` RPC 获取）
- Agents / Tasks 视图切换 Tab

**遇到的问题**：
1. **`window.focus` 事件过度触发**（Code Review 发现，Medium）：在 Tauri webview 中，`window.focus` 在每个元素获得焦点时触发（点击输入框等），导致每次都调用 `git rev-parse` → 修复：改用 `document.visibilitychange` 事件，仅在窗口重新可见时刷新
2. **`onUnmounted` 清理**：添加事件监听器移除，防止内存泄漏

### 7.5 Settings UI 重构

**目标**：降低 Agent 配置的自由度，避免用户配置不支持的 CLI 导致运行时错误。

**实现**：
- **CLI 下拉选择**：从自由文本改为预设下拉（Claude Code / Codex CLI / opencode / Gemini CLI）
- **自动填充**：选择 CLI 后自动设置 ID、integration、capabilities、restrictions、maxSessions
- **角色系统**：5 种角色（Main/Dev/Acceptance/Research/Design），各带职责描述
  - Main: Orchestrator — 用户直接对话，向其他 Agent 分派任务
  - Dev: Worker — 接收任务包，编码，返回实现回执
  - Acceptance: Reviewer — 对已完成任务执行盲验收测试
  - Research: Analyst — 收集信息、读文档、回答问题，不写代码
  - Design: Designer — 生成 UI/UX 设计稿、设计规格和视觉素材
- **Capabilities 只读**：由 preset 决定，不再支持手动编辑
- **卡片头部精简**：显示 `displayName` + `id / integration` 元数据

**设计决策**：
- 只支持有适配器的 CLI，未来新增 CLI 需同时更新 preset + 适配器
- `AgentRole` 类型全局更新（core types + bridge + AgentPanel props）

---

## 已知限制与待解决

| 问题 | 严重度 | 状态 | 备注（联网查证 2026-03-16） |
|------|--------|------|------|
| Claude SDK 多模态 | Medium | **已修复** | 改用 `AsyncIterable<SDKUserMessage>` 原生传递 content array（ImageBlockParam + TextBlockParam）。[Ref](https://platform.claude.com/docs/en/agent-sdk/typescript) |
| Codex 系统 prompt | Low | 已知（正确） | SDK 无运行时 systemPrompt API。仅 AGENTS.md 文件方式。prompt prepending 是唯一选项。[Ref](https://developers.openai.com/codex/sdk/) |
| Codex 图片 | Low | **已修复** | 改用 SDK 原生 `{ type: "local_image", path }` — base64 写入临时文件后传路径。[Ref](https://developers.openai.com/codex/sdk/) |
| opencode 系统 prompt | Low | **已修复** | HTTP 模式下使用 SDK 原生 `system` 字段（SessionPromptData.body.system）。CLI 回退仍用 prepending。[Ref](https://opencode.ai/docs/sdk/) |
| opencode 图片 | Low | **已修复** | HTTP 模式下使用 `FilePartInput` + `data:` URI 传递图片。[Ref](https://github.com/sst/opencode) |
| `buildAndInjectContext` agentCount 可能偏高 | Low | 已知 | 仅 UI 显示问题 |
| Gemini CLI 适配器尚未实现 | Medium | 待开发 | preset 已标记 disabled |
| Cargo（Rust 侧）未在 CI 中验证 | Low | 待配置 | 本地开发环境缺 cargo |

---

## 已完成功能总览

| 功能 | Phase | 状态 |
|------|-------|------|
| Tauri 2 + Vue 3 项目脚手架 | 1 | ✅ |
| Node.js sidecar（JSON-RPC 2.0 over stdio） | 1 | ✅ |
| AgentPanel 聊天界面 | 1 | ✅ |
| EventLog 事件流面板 | 1 | ✅ |
| SDK 适配器（Claude/Codex/opencode） | 2-3 | ✅ |
| KB（Obsidian CLI）集成 | 2-3 | ✅ |
| Settings 面板（CLI 预设 + 角色系统） | 2-3 → 7 | ✅ |
| TaskBundle 完整类型系统 | 4 | ✅ |
| TaskManager 状态机 + CRUD + prompt 构建 | 4 | ✅ |
| Task 持久化（KB JSON 文件） | 6 | ✅ |
| Slash Command 适配器声明 + RPC | 5 | ✅ |
| Slash Command 拦截（最小原则 + 透传） | 7 | ✅ |
| SlashCommandPalette.vue 弹出面板 | 7 | ✅ |
| 图片传输管道（7 层全链路） | 7 | ✅ |
| 跨 Agent 上下文共享（系统 prompt 注入） | 7 | ✅ |
| TitleBar 增强（项目名 + git 分支 + 视图 Tab） | 7 | ✅ |
| Settings UI 重构（CLI 下拉 + 5 角色） | 7 | ✅ |
| Agents First 元数据（agentId + model + sessionId） | 6 | ✅ |
| TaskDashboard.vue 完整 UI（列表 + 详情 + 操作） | 6 | ✅ |
| Task Store（前端状态管理 + 事件监听） | 6 | ✅ |

---

## 架构概览

```
┌──────────────────────────────────────────────────────────────┐
│  Vue 3 Frontend (Tauri WebView)                              │
│  ┌──────────┐ ┌──────────┐ ┌────────────┐ ┌──────────────┐ │
│  │AgentPanel│ │TitleBar  │ │SettingsPanel│ │TaskDashboard │ │
│  │(chat+img)│ │(info+tab)│ │(preset+ctx) │ │(list+detail) │ │
│  └────┬─────┘ └──────────┘ └─────┬──────┘ └──────┬───────┘ │
│       │                           │                │         │
│  ┌────┴───┐ ┌──────────┐ ┌───────┴──────┐ ┌──────┴───────┐ │
│  │SlashCmd│ │ EventLog │ │ agents store │ │ tasks store  │ │
│  │Palette │ │ (events) │ │ messages st. │ │ events store │ │
│  └────────┘ └──────────┘ └──────────────┘ └──────────────┘ │
│       │ tauri-bridge (invoke + listen)                       │
├───────┼──────────────────────────────────────────────────────┤
│  Rust │ Tauri Commands (thin passthrough)                    │
│       │ commands.rs → JSON-RPC over stdio                    │
├───────┼──────────────────────────────────────────────────────┤
│  Node.js Sidecar (Orchestrator)                              │
│  ┌──────────┐ ┌──────────────┐ ┌──────────┐ ┌───────────┐  │
│  │Orchestr. │ │TaskManager   │ │KBService │ │AgentReg.  │  │
│  │(RPC hub) │ │(state+prompt)│ │(Obsidian)│ │(adapters) │  │
│  └────┬─────┘ └──────┬───────┘ └──────────┘ └─────┬─────┘  │
│       │        ┌──────┴───────┐                    │         │
│       │        │Persistence KB│                    │         │
│       │        │(JSON files)  │                    │         │
│       │        └──────────────┘                    │         │
│  ┌────┴────────────────────────────────────────────┴────┐   │
│  │  SDK Adapters (Claude / Codex / opencode)            │   │
│  │  - sendPrompt (+ images, slash cmd intercept)        │   │
│  │  - setSystemPrompt (context injection)               │   │
│  │  - getSlashCommands (command list)                   │   │
│  └──────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────┘
```
