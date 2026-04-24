# Phase 5 MVP Telegram Notify Adapter — Transport ADR (v2 Bidirectional)

> Status: **Decision doc (S74 main lane)** | Date: 2026-04-25 | Lane: `main`
> Scope: 完整双向 Telegram 集成（outbound 推送 + inbound 远程操控）
> Supersedes: v1 outbound-only ADR (S74 上半段)
> Dependencies: `phase5-sizing-2026-04-25.md` / `phase5-bidirectional-feasibility-2026-04-25.md` / `router-llm-backend-2026-04-25.md`
> Related: Mercury Issue #293 / claude-handoff PR (待开)

---

## 1. Scope 变更说明（v1 → v2）

v1 ADR 假设 MVP 单向（Mercury → 手机推送），双向 defer Phase 5-2。

v2 反映以下用户决策（S74 中段）：
- **MVP 一步到位**：双向交互（手机 → 正在跑的 Claude Code session 当 user prompt）
- **多 session 同步运行支持**：硬限 3 session
- **任务标识自动推导 + 可 override**（方案 C）
- **0 LLM router MVP**：纯 deterministic 路由
- **库选 `node-telegram-bot-api`**（一步到位的双向需求让库的依赖链赚回来）

---

## 2. 架构总览

### 2.1 三 adapter 拆分

```
┌─────────────────────────────────────────────────────────────┐
│  Telegram (api.telegram.org)                                │
└─────────────────────────────────────────────────────────────┘
              ▲                                ▲
              │ getUpdates / sendMessage       │
              │ (node-telegram-bot-api lib)    │
              │                                │
┌─────────────▼────────────────────────────────┴─────────────┐
│  adapters/mercury-channel-router/  (long-running process)  │
│  - Telegram bot polling (single connection per token)      │
│  - IPC server (localhost HTTP)                             │
│  - Ownership election (which session is "active")          │
│  - Task label resolution (branch name → label)             │
│  - Command parser (/status /cancel /continue /list)        │
│  - Allowlist gate (sender ID check)                        │
│  - Session count limit (max 3)                             │
└────────────┬───────────────────────┬───────────────────────┘
             │ IPC                   │ IPC
             │ (HTTP localhost)      │ (HTTP localhost)
             │                       │
┌────────────▼──────────┐  ┌─────────▼──────────────┐
│ mercury-channel-      │  │ mercury-notify/        │
│ client/               │  │                        │
│ (one per Claude Code  │  │ (called from hooks /   │
│  session, MCP server) │  │  scripts that don't    │
│                       │  │  have a Claude session)│
│ - Spawned by          │  │                        │
│   Claude Code         │  │ - HTTP POST to router  │
│ - Notification        │  │   /notify endpoint     │
│   listener            │  │ - Used by loop         │
│ - Reply MCP tool      │  │   detector, post-      │
│ - Permission relay    │  │   commit hooks, etc.   │
└───────────────────────┘  └────────────────────────┘
        ▲                            ▲
        │ stdio                      │ exec
        │                            │
┌───────┴──────────────┐  ┌──────────┴─────────────┐
│ Claude Code session  │  │ Mercury hook scripts   │
│ (loaded via          │  │ (hook.cjs / *.sh)      │
│  --channels flag)    │  │                        │
└──────────────────────┘  └────────────────────────┘
```

### 2.2 数据流

**Inbound（手机 → Claude Code）**：
1. 用户在 Telegram 给 bot 发消息
2. Router `getUpdates` 拉到消息
3. Router 检查 sender 在白名单
4. Router 解析消息（前缀 `@<label>` / 命令 `/<cmd>` / 自由文本）
5. Router 决定路由目标 session
6. Router 通过 IPC 推消息给目标 session 的 channel-client
7. channel-client 通过 MCP `notifications/claude/channel` 推到 Claude
8. Claude 处理并通过 `reply` MCP tool 回复
9. channel-client 把 reply 通过 IPC 转给 router
10. Router 加 `[<label>]` 前缀后 sendMessage 回 Telegram

**Outbound from Claude session（Claude → 手机）**：
- 走 step 8-10 的 reply 路径

**Outbound from hook（Mercury 内部进程 → 手机）**：
- hook script 调 `mercury-notify/notify(severity, title, body)`
- notify 通过 IPC POST 到 router `/notify`
- Router 加 `[<label>]` 前缀（从 PWD/branch 推导）后 sendMessage

### 2.3 IPC 通道选择

**localhost HTTP**（Bun.serve / Node http）：
- 跨平台（Windows/MSYS2/WSL/Native bash 全工作）
- 无 Unix socket 兼容性顾虑
- 端口：`MERCURY_ROUTER_PORT`（默认 8788，可 env var 覆盖）
- 仅监听 127.0.0.1，外部不可达

替代选项 rejected：
- Unix socket：Windows 10+ 才支持，MSYS2 还有边缘 case
- Named pipe：跨 Linux/Mac 不可移植
- TCP non-localhost：暴露面太大

---

## 3. 用户决策 + 研究依据汇总

### 3.1 Outbound transport（v1 沿用）

| Q | 决策 | 来源 |
|---|------|------|
| Q1 Privacy | Telegram bot（authenticated） | S73 用户 Q1 |
| Q2 Secret | `~/.claude/settings.json` env | S73 用户 Q2 |
| Q3 Payload | HTML parse_mode + 4000 char 截断 | S73 用户 Q3 + Q3 research |
| Library | `node-telegram-bot-api` v0.67.0 MIT | S74 用户翻库决定 |

### 3.2 Bidirectional 路径（v2 新增）

| Q | 决策 | 来源 |
|---|------|------|
| Q7 路径 | A2 自建 router + 自定义 channel | bidirectional research |
| Q8 任务标识 | 方案 C: branch name → `#<N>` 推导 + override | 用户 confirm |
| Q9 LLM 后端 | 0 LLM MVP（纯 regex） | router-llm research |
| Q10 限 session | 硬限 3 | 用户决策 |

---

## 4. `mercury-channel-router/` 详细设计

### 4.1 启动机制

**触发**：第一个带 `--channels` 的 Claude Code session 启动时，channel-client 的 MCP server 探测 router 端口；端口空 → spawn router 进程；端口占 → attach。

**伪代码（channel-client 内）**：
```js
async function ensureRouter() {
  const port = process.env.MERCURY_ROUTER_PORT || 8788;
  try {
    const res = await fetch(`http://127.0.0.1:${port}/health`, {
      signal: AbortSignal.timeout(500)
    });
    if (res.ok) return; // already running
  } catch {}
  // Spawn router
  spawn('node', [
    path.join(MERCURY_ADAPTERS, 'mercury-channel-router/router.cjs')
  ], {
    detached: true,
    stdio: 'ignore',
    windowsHide: true
  }).unref();
  // Wait for /health
  for (let i = 0; i < 20; i++) {
    await sleep(250);
    try {
      const res = await fetch(`http://127.0.0.1:${port}/health`);
      if (res.ok) return;
    } catch {}
  }
  throw new Error('router did not start within 5s');
}
```

**优点**：用户不需要单独启 router；零部署摩擦。

### 4.2 IPC 协议（HTTP endpoints）

| Method | Path | 调用方 | 作用 |
|--------|------|--------|------|
| GET | `/health` | channel-client / notify | 探活 |
| POST | `/register` | channel-client | 注册新 session（含 session_id, project_path, branch, pid） |
| DELETE | `/register/<session_id>` | channel-client | 注销 session（exit 前调） |
| POST | `/take-ownership/<session_id>` | channel-client | 主动声明"我是当前 active" |
| POST | `/notify` | notify.cjs / 任意 hook | 发出站消息（{severity, title, body, label?}） |
| POST | `/reply` | channel-client | Claude 通过 reply tool 后转发到 Telegram |
| GET | `/sessions` | channel-client (admin) | 返回当前注册的所有 session |
| WS | `/inbox/<session_id>` | channel-client | 长连接接收路由器推来的消息 |

**WebSocket** vs **SSE** vs **long polling**：
- WebSocket 双向，但 Bun WS 实现成熟
- SSE 单向（router → client），需 client 反向 POST
- long polling 兼容性最好，延迟最高
- **MVP 选 SSE**（router → client） + POST（client → router）：实现简单，跨平台稳定

### 4.3 Ownership 选举

**规则**：任何时刻只有一个 session 是 `active`，接收用户不加前缀的消息。

**初始 active**：第一个 register 的 session。

**变更触发**：
- 显式：channel-client 调 `/take-ownership` （比如用户在某个 tab 输入了内容触发 hook）
- 隐式：router 收到 sender 加 `@<label>` 时不变更 ownership（精确路由），不加时 ownership 不变；但 channel-client 在每次 `notifications/claude/channel` 处理后调 `/take-ownership` 让"刚响应的 session"成为下次默认

**MVP 简化**：让 channel-client 在收到任意 inbound 消息处理完毕后调 `/take-ownership`——即"最近响应的 session 自动成为 active"。

### 4.4 任务标识解析（方案 C）

**Router 收到 `/register` 时记录 session 元数据，包含 label 推导**：

```js
function deriveLabel({ project_path, branch }) {
  // 1. branch name 模式：feature/lane-<name>/TASK-<N>-<slug>
  const m1 = branch.match(/^feature\/(?:lane-[\w-]+\/)?TASK-(\d+)-([\w-]+)/);
  if (m1) return `#${m1[1]} ${m1[2]}`.slice(0, 30);
  // 2. branch name 模式：feature/<slug>
  const m2 = branch.match(/^feature\/([\w-]+)/);
  if (m2) return m2[1].slice(0, 30);
  // 3. cwd basename fallback
  return path.basename(project_path).slice(0, 30);
}
```

**Override**：channel-client 启动时读 `.mercury/state/session-label.txt` 如存在则覆盖 router 推导。Mercury 命令 `/label "<custom>"`（未来添加）写入此文件。

**Label 显示**：所有 router → Telegram 的消息都加 `[<label>]` 前缀。

### 4.5 限 session 数策略

**硬限 3**：
- `/register` 第 4 个 session 时返回 HTTP 429
- channel-client 收到 429 时 stderr 警告 + Claude Code 仍正常启动（仅无 Telegram 功能）
- router 已注册 session 退出（DELETE /register）后释放配额

### 4.6 Telegram polling

**库**：`node-telegram-bot-api` v0.67.0
- 长轮询模式 `bot.startPolling()`
- 单进程独占 token（多 router 实例会撞 offset）
- 使用 lock file `~/.mercury/router.lock` 防止误启多实例

**Bot 凭据**：从 env var `MERCURY_TELEGRAM_BOT_TOKEN` 读取（来自 `~/.claude/settings.json` 的 env block，因为 router 由 Claude Code 子进程 spawn，继承环境）。

### 4.7 消息路由规则（0 LLM）

**收到 Telegram message 后**：

```js
function route(msg, sessions, currentActive) {
  // 1. Allowlist gate
  if (!isAllowed(msg.from.id)) return null; // silent drop

  const text = msg.text.trim();

  // 2. Bot commands
  if (text.startsWith('/status')) return { type: 'cmd', cmd: 'status' };
  if (text.startsWith('/list'))   return { type: 'cmd', cmd: 'list' };
  if (text.startsWith('/cancel')) return { type: 'cmd', cmd: 'cancel', args: parseArgs(text) };

  // 3. Explicit prefix
  const prefixMatch = text.match(/^@([\w-]+)\s+(.+)$/s);
  if (prefixMatch) {
    const target = sessions.find(s => s.label.startsWith(prefixMatch[1]));
    if (target) return { type: 'route', session_id: target.id, content: prefixMatch[2] };
    return { type: 'error', text: `No session matching @${prefixMatch[1]}` };
  }

  // 4. Permission verdict (yes/no <id>)
  const verdictMatch = text.match(/^\s*(y|yes|n|no)\s+([a-km-z]{5})\s*$/i);
  if (verdictMatch) return { type: 'verdict', verdict: verdictMatch[1], request_id: verdictMatch[2] };

  // 5. Default: route to current active
  if (!currentActive) return { type: 'error', text: 'No active session' };
  return { type: 'route', session_id: currentActive, content: text };
}
```

### 4.8 命令处理

| 命令 | 行为 |
|------|------|
| `/status` | 返回 router 状态（version / uptime / active session label） |
| `/list` | 列所有注册 session：`[#293 telegram-notify] active\n[#292 multi-lane]\n` |
| `/cancel` | 不加前缀 → cancel current active；`/cancel @<label>` → 指定。具体怎么"cancel"传给 session 由 client 决定（注入 `<channel>` 标签让 Claude 看到指令） |
| `/continue` | 同 `/cancel`，传 continue 信号 |
| `/help` | 返回所有命令帮助 |

### 4.9 LOC 预算

| 模块 | LOC |
|------|-----|
| Telegram bot + lock file + auth gate | 30-40 |
| HTTP IPC server + endpoints | 50-60 |
| Ownership / register / session map | 30-40 |
| Label 解析 + override 读取 | 15-20 |
| 消息路由 + 命令处理 | 40-50 |
| **总计** | **165-210**（target 180 LOC，硬限 200） |

---

## 5. `mercury-channel-client/` 详细设计

### 5.1 角色

由 Claude Code 通过 `--channels plugin:mercury@<marketplace>` 加载（需 dev flag `--dangerously-load-development-channels` 在 research preview 阶段）。

### 5.2 实现核心

```js
import { Server } from '@modelcontextprotocol/sdk/server/index.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';

const mcp = new Server(
  { name: 'mercury-telegram', version: '0.1.0' },
  {
    capabilities: {
      experimental: {
        'claude/channel': {},
        'claude/channel/permission': {},
      },
      tools: {},
    },
    instructions: 'Telegram messages arrive as <channel source="mercury-telegram" label="..."> tags. Reply with the reply tool, passing the session_id from the tag.',
  }
);

// 1. ensureRouter() — spawn if not running
// 2. POST /register with {session_id, project_path, branch, pid}
// 3. Open SSE to /inbox/<session_id>
// 4. On SSE event: mcp.notification('notifications/claude/channel', {...})
// 5. Register reply tool: POST /reply to router
// 6. Register permission_request handler: POST /permission-request to router
// 7. On process exit (SIGTERM/SIGINT): DELETE /register/<session_id>
```

### 5.3 LOC 预算

| 模块 | LOC |
|------|-----|
| MCP server scaffolding | 20-25 |
| ensureRouter + register/deregister | 20-25 |
| SSE consumer + channel notification | 15-20 |
| Reply tool handler | 10-15 |
| Permission relay | 15-20 |
| **总计** | **80-105**（target 90 LOC，硬限 200） |

---

## 6. `mercury-notify/` 详细设计（重新评估）

### 6.1 v1 vs v2 对比

| | v1 设计 | v2 设计 |
|---|---|---|
| 直连 Telegram | ✅（raw fetch） | ❌ |
| 通过 router | N/A | ✅（HTTP POST localhost） |
| LOC | 60-80 | 25-35 |

v2 大幅简化：notify.cjs 不再持 Telegram 凭据/库，只发 HTTP 给 router。如果 router 没起，notify.cjs 静默失败 + stderr log（不 spawn router——避免 hook 路径副作用）。

### 6.2 接口

```js
// notify.cjs (~30 LOC)
async function notify(severity, title, body, options = {}) {
  if (process.env.MERCURY_NOTIFY_DISABLED) return { ok: true, skipped: true };
  const port = process.env.MERCURY_ROUTER_PORT || 8788;
  try {
    const res = await fetch(`http://127.0.0.1:${port}/notify`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ severity, title, body, ...options }),
      signal: AbortSignal.timeout(2000)
    });
    if (!res.ok) return { ok: false, error: `router_${res.status}` };
    return { ok: true };
  } catch (e) {
    process.stderr.write(`[mercury-notify] ${e.message}\n`);
    return { ok: false, error: 'transport' };
  }
}
module.exports = { notify };
```

### 6.3 Caller wire（不变）

`adapters/mercury-loop-detector/hook.cjs` 在 stall 触发后调 `notify('error', 'Mercury stall: <type>', stallReason)`，fire-and-forget。

---

## 7. 边界问题与解决方案

### 7.1 P0：handoff 切换的"无主时段"

**问题**：旧 session `/exit` 之前 spawn 新 session，启动间隔（~5-15s）内用户消息无人接。

**方案**：
- Router 维护 `orphan_buffer`（按 ownership 时间窗口缓冲，默认保留 30s 内消息）
- 新 session register 完成时，router 检查 `last_ownership_release_ts`，如在 30s 内 → 把 orphan messages 重放给新 session 的 inbox
- 超过 30s → 丢弃 + 通知用户："2 messages dropped during handoff window"

### 7.2 P0：新 session 启动失败

**问题**：旧 session exit 前新 session 没起来 → channel 永久断。

**方案**：
- handoff skill 在 spawn 新 session 后**等新 session register**（poll router `/sessions` 确认 N+1 个 session 存在）才允许旧 session exit
- 超时 30s 仍未 register → handoff 报错，**不允许旧 session 退出**
- 失败时 router 主动 sendMessage `[router] handoff failed, old session preserved`

### 7.3 P0：用户消息归属（已 confirm 解决方案）

- 加前缀 `@<label>` → 显式
- 命令 `/cmd` → router 内部
- 不加前缀 → current active
- Router reply 加 `[<label>]` 让用户看清楚谁在答

### 7.4 P1：router 进程生命周期

**启动**：第一个 channel-client 探测端口空时 spawn detached + unref。

**退出**：router 监听 channel-client `/register` 计数，所有 session DELETE 后启动 30s grace timer，倒计时内有新 register 则取消，否则 self-exit。

**Crash 恢复**：channel-client 的 SSE 连接断开时尝试重连 + 调 ensureRouter（探测端口空就 respawn）。

**Lock file**：router 启动时 `flock` 在 `~/.mercury/router.lock`，第二个实例尝试 lock 失败立即 exit。

### 7.5 P1：handoff plugin 集成

**问题**：handoff plugin 是通用的，不该 hardcode `--channels plugin:mercury@...`。

**方案**：
- 新 env var `MERCURY_CHANNELS_FLAGS`（默认未设）
- handoff skill 在 spawn 新 session 时检查该 env var，存在 → 注入到 launch 命令
- Mercury 项目用户在 `~/.claude/settings.json` env 段设：
  ```json
  "env": {
    "MERCURY_CHANNELS_FLAGS": "--channels plugin:mercury-telegram@local --dangerously-load-development-channels"
  }
  ```
- handoff plugin 需要小改动：在 `wt -- claude -- "$SHORT_PROMPT"` 前注入 `${MERCURY_CHANNELS_FLAGS:-}`
- 改动只 ~3 行，但需给 `392fyc/claude-handoff` 提 PR

### 7.6 P1：权限 request_id 跨 session 冲突

**问题**：5 letter ID 约 11M 组合，多 session 同时发起权限请求时 ID 可能撞。

**方案**：
- channel-client 在 forward 给 router 时把 ID 改写成 `<session_short>-<id>`（session_short = label 前 2 字符 / 哈希 4 字符）
- Router 转发到 Telegram 时用改写后的 ID
- 用户 reply `yes <session_short>-<id>` 时 router 解析 prefix 路由到正确 session
- 同时 router 反向解析回原 ID 给 Claude Code

### 7.7 P2：限 session 数怎么实施

参见 §4.5。MVP 用 HTTP 429 拒绝；channel-client stderr warning。

### 7.8 P2：idle session 占连接

只要 session 没 `/register` DELETE，router 就当它 active。MVP 不主动 timeout。

### 7.9 P3：prompt injection from compromised account

不属于 Mercury 防御范围；用户级安全。文档化提醒。

---

## 8. handoff plugin 集成具体方案

### 8.1 改动范围

`D:\Mercury\claude-handoff-plugin\skills\handoff\SKILL.md` Step 5 三处 launch 命令：

```bash
# Before
wt -w 0 nt --title "Handoff" -d "<cwd>" -- claude -- "$SHORT_PROMPT"
tmux new-window -n handoff "claude -- '$SHORT_PROMPT'"
tmux new-session -d -s handoff "claude -- '$SHORT_PROMPT'"

# After (env var injection)
wt -w 0 nt --title "Handoff" -d "<cwd>" -- claude ${MERCURY_CHANNELS_FLAGS:-} -- "$SHORT_PROMPT"
tmux new-window -n handoff "claude ${MERCURY_CHANNELS_FLAGS:-} -- '$SHORT_PROMPT'"
tmux new-session -d -s handoff "claude ${MERCURY_CHANNELS_FLAGS:-} -- '$SHORT_PROMPT'"
```

### 8.2 PR 计划

- 给 `392fyc/claude-handoff` 开 PR
- Title: "feat: support MERCURY_CHANNELS_FLAGS env var for channel propagation in auto handoff"
- 通用化命名：env var 改为 `CLAUDE_HANDOFF_AUTO_LAUNCH_FLAGS`（避免 Mercury 特化）
- Mercury 这边 export `CLAUDE_HANDOFF_AUTO_LAUNCH_FLAGS="--channels plugin:mercury-telegram@local --dangerously-load-development-channels"`

---

## 9. 依赖与前置

### 9.1 Bun 运行时

- 用户必须装：`https://bun.sh/docs/installation` (Windows: `powershell -c "irm bun.sh/install.ps1 | iex"`)
- Mercury 文档化：`adapters/mercury-channel-client/README.md` 第一行写"requires Bun"
- 安装目录建议 `D:\Program Files\bun`（按 Mercury D 盘规则）

### 9.2 claude.ai 订阅

- Channels API 仅支持订阅认证（不支持 API key）
- 用户已是 Opus 4.7 用户，满足

### 9.3 启动 Claude Code 的命令变更

- 以前：`claude`
- 之后：`claude --channels plugin:mercury-telegram@local --dangerously-load-development-channels`
- 通过 `CLAUDE_HANDOFF_AUTO_LAUNCH_FLAGS` env var 透传到 handoff 自动 spawn 的 session

### 9.4 Telegram bot 凭据

- `MERCURY_TELEGRAM_BOT_TOKEN` 在 `~/.claude/settings.json` env block
- `MERCURY_TELEGRAM_ALLOWED_USER_IDS` 逗号分隔 user IDs（白名单 sender）

---

## 10. LOC 预算总览

| Adapter | Target | Hard limit | Includes |
|---------|--------|-----------|----------|
| `mercury-channel-router/router.cjs` | 180 | 200 | Telegram bot + IPC + ownership + label + commands |
| `mercury-channel-client/channel.cjs` | 90 | 200 | MCP server + IPC client + reply + permission |
| `mercury-notify/notify.cjs` | 30 | 80 | Thin HTTP client to router |
| **小计 实现** | **300** | — | |
| `*/README.md` × 3 | 80 | — | docs only |
| `*/UPSTREAM.md` × 3 | 30 | — | reference notes (no cherry-picks) |
| handoff plugin PR diff | 6 | — | env var injection |
| **总计** | **~416 LOC** | | 三 adapter 各自合规 200 限 |

---

## 11. 开发分阶段

### Phase 5-1: notify.cjs + router skeleton（首 PR）

- `mercury-notify/notify.cjs` 30 LOC
- `mercury-channel-router/router.cjs` 仅 outbound 部分（接 `/notify` 转 Telegram，~80 LOC）
- Wire `mercury-loop-detector/hook.cjs` 卡死后调 notify
- 用户在 `~/.claude/settings.json` 设 `MERCURY_TELEGRAM_BOT_TOKEN`
- **Demo**：触发卡死 → 手机收到 `[#NNN] Mercury stall: no_progress`
- 关 #293 一半 scope

### Phase 5-2: router 完整 + channel-client（次 PR）

- `mercury-channel-router/router.cjs` 完成 inbound + ownership + commands
- `mercury-channel-client/channel.cjs` 完整实现
- handoff plugin PR（独立仓库）
- **Demo**：手机发"@main /status" → 收到 `[#NNN main lane] active 3h, 1 session`
- 关 Issue #295（待开）

### Phase 5-3: 自然语言意图解析（远期 defer）

- `mercury-channel-router/intent-parser.cjs` 加 LLM hook（OpenAI gpt-4o-mini direct）
- 让用户能发"取消刚才的"这种自由文本
- 不在本 ADR scope

---

## 12. 替代方案（rejected）

| Option | Why rejected |
|--------|--------------|
| 官方 Telegram channel plugin（路径 A1） | 不支持多 session；用户需求"3 session 同步"被屏蔽 |
| Agent SDK 持独立 session（路径 B） | bot 持有的 session 与用户 terminal 隔离，违背"接入正在用的会话"语义 |
| spawn + resume per message（路径 C） | 5-8s 冷启动延迟；不共享 hook 状态 |
| tmux send-keys（路径 D） | 平台脆弱、安全风险 |
| Codex CLI 当 router LLM 后端 | 5-10s 延迟、Windows encoding 风险、无 per-call system prompt |
| Webhook 模式（vs 长轮询） | 需要公网 IP / HTTPS 证书 / 反代；用户笔记本不在公网 |
| LLM intent parsing in MVP | 用户 P0#3 设计纯 deterministic 已够用；增加复杂度无收益 |

---

## 13. 风险登记

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| Channels API research preview 被 deprecated | 低 | 高 | Anthropic 文档承诺 2-version deprecation window |
| 用户笔记本休眠时 router 进程死 | 中 | 中 | channel-client 重连 + 唤醒后 sendMessage 提醒"router restarted, X messages dropped" |
| 多 session 同时跑 register 撞 ownership | 中 | 低 | 路由器侧加 mutex；MVP active 概念是软语义 |
| Bot token 泄露 | 低 | 高 | env var only、never log；用户级安全 |
| handoff 失败后旧 session 卡 exit 流程 | 中 | 中 | 30s timeout + 错误明示；让用户决定手动 exit |
| Bun 运行时升级断接口 | 低 | 中 | pin Bun major version；channel-client 用稳定 Node API 而非 Bun-specific |

---

## 14. Sources

**Phase 5 v2 ADR 综合以下 research：**
- `phase5-sizing-2026-04-25.md` — Routines 不替代 Phase 5
- `phase5-bidirectional-feasibility-2026-04-25.md` — Channels API 可行 + 多 session 阻塞
- `router-llm-backend-2026-04-25.md` — Codex 不适合，0 LLM MVP

**Anthropic 官方文档（WebFetch 2026-04-25）：**
- [Channels — push events into a running session](https://code.claude.com/docs/en/channels)
- [Channels reference — build your own channel](https://code.claude.com/docs/en/channels-reference)
- [CLI reference](https://code.claude.com/docs/en/cli)
- [Agent SDK overview](https://code.claude.com/docs/en/agent-sdk/overview)

**npm + Telegram：**
- `node-telegram-bot-api` v0.67.0 MIT (npm registry 2026-04-25)
- [Telegram Bot API 9.6](https://core.telegram.org/bots/api)
- [Bun installation](https://bun.sh/docs/installation)

**项目文档：**
- `.mercury/docs/DIRECTION.md` §3 Module 3
- `.mercury/docs/EXECUTION-PLAN.md` Phase 5 L337-362
- `.mercury/docs/research/phase4-5-circular-dep-breakpoint-2026-04-24.md`

**待开 PR：**
- `392fyc/claude-handoff` — env var support for auto-launch flags

---

## 15. 决策 checklist（实施前 user confirm）

- [x] Library: `node-telegram-bot-api` v0.67.0
- [x] Architecture: 自建 router + channel-client + notify 三 adapter
- [x] Routing: 0 LLM, regex + commands
- [x] Label: branch name 推导 (方案 C)
- [x] Session limit: 硬限 3
- [x] IPC: localhost HTTP / SSE
- [x] LLM 后端: 不用 (MVP) ；远期 OpenAI direct
- [ ] Bun 安装位置（建议 `D:\Program Files\bun`）
- [ ] handoff plugin PR 由 Mercury 维护者（你）提交还是我代笔
- [ ] 分阶段 ship（Phase 5-1 outbound 单独 PR / Phase 5-2 双向次 PR）

---

## 16. Next actions

1. **User confirm §15 三未定项**
2. 关闭 Issue #293 v1 ADR comment，重定向到本 v2 文件
3. 开 Issue #295 "Phase 5-2: bidirectional via custom Channels router"
4. Phase 5-1 dev dispatch（notify.cjs + router skeleton）
5. handoff plugin PR
