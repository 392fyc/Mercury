# Phase 5 双向交互 MVP 可行性分析

> Status: research output | Date: 2026-04-25 | Lane: `main`
> Author: research subagent (af31ba4c) | Session: S74

---

## 1. 问题定义

**核心阻塞点**：Mercury bot 进程收到用户 Telegram 消息后，如何将该消息送入"用户当前正在运行的 Claude Code 会话"并让 Claude 将其当作 user prompt 处理？

MVP 用户决策已确定（Telegram / 长轮询 / `node-telegram-bot-api` / 单用户白名单），现有 ADR (`phase5-mvp-telegram-adr-2026-04-25.md`) 仅覆盖 outbound（Mercury → 手机）。本文研究 inbound（手机 → 正在运行的 Claude Code session）的技术可行性。

---

## 2. 六个研究问题各自的 Verdict

### Q1. 官方"外部消息注入"接口

**Verdict: 不存在直接注入接口，但存在官方 Channels API（研究预览）**

完整 CLI 参考（`https://code.claude.com/docs/en/cli`）确认：

- 无 `--message` / `--send` / `--inject` 等"向正在运行的 session 发消息"的 CLI flag
- 无任何 IPC socket / named pipe / REST 接口供外部进程注入
- `claude -r "<session>" "query"` 可以 resume 一个已有 session，但这会**开启新的交互进程**，不是注入现有进程
- `--resume` / `-r` + `-p` 组合：`claude -c -p "query"` 可以 continue 最近 session 并立即执行一个 prompt，但这同样是**启动新进程**而非注入

**关键发现**：`claude remote-control` 命令（v2.1.51+）和 `--channels` flag（v2.1.80+）是两个不同的官方机制，覆盖"外部驱动本地 session"这个需求。详见 Q3/Q2。

**引用 URL**：`https://code.claude.com/docs/en/cli`

---

### Q2. UserPromptSubmit hook 是否能"主动注入"

**Verdict: 不能。只有拦截/修改，无法在无用户输入时主动触发。**

UserPromptSubmit hook 是在用户已经按下 Enter 发送消息**之后**、Claude 处理之前触发的钩子。其 `additionalContext` 字段可以向 prompt 追加内容，但触发条件是"用户发消息"这个事件本身。

bot 无法在用户不操作时通过 hook 向 idle session 注入消息。这条路径是**拦截器**，不是**注入器**。

---

### Q3. Channels API — 官方 Telegram 双向注入机制

**Verdict: 可行，且 Anthropic 有官方 Telegram channel plugin。这是最接近"注入正在运行的 session"的官方路径。**

**关键事实**（来源：`https://code.claude.com/docs/en/channels` 和 `https://code.claude.com/docs/en/channels-reference`）：

- Channels 是 MCP server，由 Claude Code 作为子进程启动，通过 stdio 通信
- 启动命令：`claude --channels plugin:telegram@claude-plugins-official`
- 当 Telegram bot 收到消息时，channel MCP server 通过 `notifications/claude/channel` 协议将消息推入**当前运行的 Claude Code session**
- 消息以 `<channel source="telegram" ...>` XML 标签注入 Claude 上下文，Claude 读取并响应
- Claude 通过 `reply` MCP tool 回复，消息发回 Telegram chat
- **官方支持双向**：inbound + outbound + 权限审批 relay

**约束**：
- 要求 claude.ai 账号认证（不支持 API key）
- 研究预览阶段（v2.1.80+），flag 语法可能变化
- 需要 [Bun](https://bun.sh) 运行时（官方 plugin 用 Bun 编写）
- session 必须保持运行（idle session 收不到消息）
- 官方 Telegram plugin 源码：`https://github.com/anthropics/claude-plugins-official/tree/main/external_plugins/telegram`
- 自定义 channel 在研究预览期需 `--dangerously-load-development-channels` flag

**这条路径是"真双向"**：消息进入正在运行的 session，Claude 在同一个 session 上下文里响应，回复发回 Telegram。

---

### Q4. Routines `/fire` 是否能注入已有 session

**Verdict: 不能。`/fire` 只能创建新 session，且是 fire-and-forget。**

来自 `phase5-sizing-2026-04-25.md` §1.2（2026-04-25 WebFetch 确认）：

> "Each successful request creates a new session. There is no idempotency key."
> "The request returns once the session is created. It does not stream session output or wait for the session to complete."

Routines 与注入现有 session 完全不相关。这条路径已在 S73 研究中排除。

**引用 URL**：`https://platform.claude.com/docs/en/api/claude-code/routines-fire`

---

### Q5. 同类项目双向实现 pattern

**openclaw `telegram-claude-poc.py`（seedprod）**：

来源：`https://raw.githubusercontent.com/seedprod/openclaw-prompts-and-skills/main/telegram-claude-poc.py`

Pattern：**开新 session，非注入已有 session**。

- 用 `subprocess.run()` 调用 `claude -p <message>` CLI
- 用 `--resume <session_id>` 保持多轮会话连续性
- session_id 持久化到 `~/.telegram-claude-sessions.json`
- 用户发 `/new` 清空 session_id，下次重建

这是"每条 Telegram 消息 spawn 一个新 claude 进程"的模式。优点：简单；缺点：每条消息等待新进程启动 + 无法共享现有 session 的上下文（内存、hook 状态等）。

**breverdbidder/claude-code-telegram-control**：

来源：`https://raw.githubusercontent.com/breverdbidder/claude-code-telegram-control/main/README.md`

Pattern：**MCP server 模式，Claude 主动调用 Telegram 工具**。

- 暴露 MCP 工具（`telegram_send`, `telegram_ask`, `telegram_notify`, `telegram_send_file`）
- Claude 在 autonomous session 中主动 call 这些工具
- 这是 outbound-only（Claude 主动通知），不是 inbound（用户发消息驱动 Claude）

**moazbuilds/claudeclaw**：repo 返回 404，不存在。

---

### Q6. Agent SDK 编程式持有 session

**Verdict: 可行，这是路径 B 的核心机制。**

Claude Agent SDK（原 Claude Code SDK，已改名）：

- TypeScript 包：`@anthropic-ai/claude-agent-sdk`（注：`@anthropic-ai/claude-code` 是老包名，已被替代）
- Python 包：`claude-agent-sdk`
- 文档：`https://code.claude.com/docs/en/agent-sdk/overview`

**关键能力**：

```typescript
import { query } from "@anthropic-ai/claude-agent-sdk";

// bot 进程自己持有 session，通过 resume 保持多轮连续
for await (const message of query({
  prompt: telegramUserMessage,
  options: { resume: sessionId, allowedTools: [...] }
})) { ... }
```

- SDK 在 bot 进程内部直接持有 session（非外部注入）
- `resume` 参数可以恢复已有 session ID，保持对话上下文
- SDK 捆绑了 Claude Code 原生二进制，不需要独立安装 `claude` CLI
- **需要 API key**（不支持 claude.ai 订阅账号，不同于 Channels）
- 这个模式下 bot 进程**就是** session owner，不存在"注入他人 session"的问题

**约束**：
- 需要 `ANTHROPIC_API_KEY`（计费方式与订阅不同，按 token 计费）
- 与用户手动开的 Claude Code terminal session 是**完全独立的两个 session**（不共享上下文、不共享 hook 状态）
- SDK session 没有 terminal UI，纯 headless

---

## 3. 决策矩阵 — 4 个候选实现路径

| 路径 | 方式 | 复杂度 | 是否真双向 | 阻塞/限制 |
|------|------|--------|-----------|-----------|
| **A. Channels API（官方 Telegram plugin）** | `claude --channels plugin:telegram@claude-plugins-official`；MCP server 推送消息进 session | 低-中（需 Bun；安装 plugin；配置 allowlist） | **是（真双向）**：消息进现有 session，Claude 同 session 响应，reply 工具回 Telegram | 研究预览（flag 语法不稳定）；需 claude.ai 订阅账号；session 必须保持运行；Bun 依赖 |
| **B. Agent SDK 持有独立 session** | bot 进程用 `@anthropic-ai/claude-agent-sdk` 自己起一个 headless session，`resume` 保持多轮 | 中（需 API key；独立 session 与用户 terminal session 无关） | **是（真双向）**：bot 拥有 session，任意发 prompt，任意接收响应 | 需要 API key（按 token 计费）；与用户手动开的 Claude Code session 完全隔离；无 terminal UI |
| **C. openclaw pattern：spawn 新 session per message** | `subprocess.run(['claude', '-p', msg, '--resume', sessionId])` | 低（纯 CLI 调用；无新 runtime 依赖） | **半双向**：每条消息启动新进程，resume 共享 conversation 历史，但每次进程启动有延迟（3-8秒）；无法共享 hook/内存 | 每消息冷启动延迟；openclaw 本身无 LICENSE 不可 cherry-pick；与 Mercury 本地 hook 状态隔离 |
| **D. tmux/screen send-keys** | bot 向 tmux pane 发字符序列模拟键盘输入 | 低（纯 shell）| **是（注入现有 session）**：真正向用户正在用的 terminal session 发消息 | 高度脆弱（依赖 terminal 状态/focus/滚动）；需要 tmux 且 session 在 tmux pane 内；prompt 注入风险；不跨平台 |

---

## 4. 推荐路径 + 理由

**推荐：路径 A（Channels API）作为主路径，路径 C 作为兼容降级**

**路径 A 理由**：

1. **官方支持**：Anthropic 有官方 Telegram channel plugin，源码开放（MIT 待确认）。不需要自己实现双向协议。
2. **真双向**：消息在同一个 Claude Code session 内处理，共享 session 上下文、技能、hooks、CLAUDE.md。
3. **权限 relay**：用户可以从 Telegram 批准/拒绝 Claude 的 tool use，这是 Mercury "远程确认"特性（DIRECTION.md §3 Module 3）的完整实现。
4. **与现有 MVP outbound 兼容**：outbound（`adapters/mercury-notify/notify.cjs`）和 inbound（Channels）是正交的；outbound 先落地，inbound 后加，无冲突。
5. **LOC 影响**：Mercury 只需写启动脚本和白名单配置（~30 LOC），核心 channel 逻辑由官方 plugin 承担。

**路径 A 的额外约束（需用户确认）**：
- 用户必须用 `claude --channels plugin:telegram@claude-plugins-official` 启动 Claude Code，而非普通 `claude`（或者 `/config` 设为全局启用）
- 需要安装 Bun（`bun --version` 检查）
- 研究预览阶段：flag 可能变化，需关注 changelog

**路径 C 作为降级**：若用户不想依赖研究预览 feature，或 Channels 在特定环境下不可用，openclaw pattern（`claude -p --resume <id>`）是零依赖的降级方案，但每条消息有 ~5s 冷启动延迟。

---

## 5. 不可行情况下的降级方案

若 Channels API 被封锁（企业策略 / 研究预览退出 / Bun 不可安装）：

**最接近双向的次优方案：路径 C（spawn + resume）**

- bot 收到 Telegram 消息 → `claude -p "<msg>" -r <session_id> --output-format json`
- 解析 JSON 输出取 Claude 回复 → 发回 Telegram
- session_id 缓存在本地文件
- **限制**：每消息 5-8s 延迟；与用户 terminal session 隔离；hook 状态不共享

这不是"注入现有 session"，而是"独立的 bot session"。从用户体验角度是可接受的双向交互，只是技术上两个 session。

---

## 6. 对 Issue #293 的影响

### ADR 是否需要重写

**需要新增一节**，原 ADR 只覆盖 outbound（Phase 5-1）。双向部分（Phase 5-2）现在有了可行的技术路径，建议在 Issue #293 comment 中记录本研究结论，并将"Path A: Channels API"作为 Phase 5-2 的首选方案。

原 ADR 的 outbound 决策（raw `fetch` + HTML + 4000 truncate）**不受影响**，仍然正确——但用户已选改为 `node-telegram-bot-api` lib 因为"一步到位"，这部分需要由 main agent 在 ADR 重写中调整。

### LOC 估算变化

| 部分 | 原估算 | 修订 |
|------|--------|------|
| outbound `notify.cjs` | 60-80 LOC | 不变 |
| inbound Channels（路径 A） | 未估算（Phase 5-2 defer） | ~30-50 LOC（启动脚本 + 白名单配置 + Mercury hook 集成）；核心由官方 plugin 承担 |
| 总 Phase 5 全双向 MVP | — | ~80-130 LOC（低于 200 LOC 硬限） |

**如果选路径 C（降级）**：bot.js 约 80-120 LOC（长轮询 + subprocess + session 缓存），无新 runtime 依赖。

### Caller wire 选择

Channels API 路径下，inbound 消息直接进 Claude session，不需要 Mercury 额外的 caller wire。Claude 自身处理 Telegram 消息，通过 reply tool 回复。这与 outbound notify（loop detector → `notify.cjs` → Telegram）是完全独立的两条数据流。

---

## 7. Sources

**WebFetch（2026-04-25，本 session 实际获取）：**
- [CLI reference](https://code.claude.com/docs/en/cli) — 完整 CLI flag 列表，确认无直接注入接口
- [Channels — push events into a running session](https://code.claude.com/docs/en/channels) — 官方 Telegram/Discord/iMessage channel，包括安装步骤和 session 启动方式
- [Channels reference — build your own channel](https://code.claude.com/docs/en/channels-reference) — MCP notification 协议、reply tool、permission relay 完整规范
- [Remote Control](https://code.claude.com/docs/en/remote-control) — `claude remote-control` 命令，确认这是"通过 claude.ai 驱动本地 session"而非编程 API
- [Agent SDK overview](https://code.claude.com/docs/en/agent-sdk/overview) — `@anthropic-ai/claude-agent-sdk` 包，programmatic session 持有

**WebFetch（2026-04-25，openclaw + breverdbidder）：**
- [openclaw telegram-claude-poc.py](https://raw.githubusercontent.com/seedprod/openclaw-prompts-and-skills/main/telegram-claude-poc.py) — spawn + resume pattern 确认
- [breverdbidder claude-code-telegram-control README](https://raw.githubusercontent.com/breverdbidder/claude-code-telegram-control/main/README.md) — MCP outbound tools pattern（非 inbound 注入）

**项目文档（本 session 读取）：**
- `.mercury/docs/research/phase5-sizing-2026-04-25.md` — Routines `/fire` fire-and-forget 确认
- `.mercury/docs/research/phase5-mvp-telegram-adr-2026-04-25.md` — 原 ADR 决策矩阵

**WebSearch（2026-04-25）：**
- [@anthropic-ai/claude-code npm](https://www.npmjs.com/package/@anthropic-ai/claude-code) — 旧包名（现已更名为 `@anthropic-ai/claude-agent-sdk`）
- [@anthropic-ai/claude-agent-sdk npm](https://www.npmjs.com/package/@anthropic-ai/claude-agent-sdk) — 当前 SDK 包名

**官方 plugin 源码（未 WebFetch，供后续读取）：**
- [Telegram channel plugin source](https://github.com/anthropics/claude-plugins-official/tree/main/external_plugins/telegram) — 官方实现参考（license 待 gh api 验证）
