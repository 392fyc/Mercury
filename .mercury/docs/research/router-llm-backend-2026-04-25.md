# Mercury Router LLM 后端候选 — Codex vs Claude API vs No-LLM

> Status: research output | Date: 2026-04-25 | Lane: main
> Scope: 是否用 Codex CLI 替代 Claude API 作 Mercury Telegram router 的轻推理后端
> Author: research subagent (ac40257a) + main synthesis

---

## 1. Codex CLI 当前能力

### Q1. Headless / Programmatic 调用接口

**当前版本**：0.125.0（2026-04-24 发布，npm 包名 `@openai/codex`）

**Headless 调用**：`codex exec` 子命令

| Flag | 作用 |
|------|------|
| `codex exec "<prompt>"` | 非交互执行 |
| `--json` | JSONL 事件流 |
| `--output-last-message <file>` | 写最终消息 |
| `--output-schema <schema.json>` | 强制 JSON Schema 输出 |
| `--model <name>` | 指定模型 |
| `--ephemeral` | 不持久化 session |

**模型可选**：gpt-5.5 / gpt-5.4 / gpt-5.4-mini / gpt-5.3-codex

### Q2. 订阅 vs API Key 计费

**关键发现**：用户当前 `~/.codex/auth.json` 显示 `"auth_mode": "apikey"`，**不是 ChatGPT 订阅登录**。

| 模式 | 计费 | 限额 |
|------|------|------|
| ChatGPT 订阅登录 | 含在订阅 | Plus 15-80条/5h；Pro 5x 80-400条；Pro 20x 300-1600条 |
| API Key（当前） | 按 token 计费 | 无额度限制 |

切换需 `codex auth login` 重新认证。

### Q3. 调用延迟

**社区报告汇总**（无官方 benchmark）：

- 进程启动 ~500ms
- CLI 初始化（含后台插件发现）1-3s
- API roundtrip（gpt-5.4-mini）~500ms-1s
- **总计**：最乐观 2-4s，普通 5-10s

对比 Claude API direct（HTTP）：~0.5-1.5s。

### Q4. JSON 输出可靠性

`--output-schema` 官方支持 JSON Schema 强制结构化输出。但通过进程 stdout 解析有 encoding/partial output 脆弱点。实际可靠性 UNVERIFIED。

### Q5. Windows 兼容性

- Windows 10 1809+ 支持，11 推荐
- **MSYS2 bash 冲突已知问题**（Discussion #3580）
- **PowerShell 5.1 默认 ANSI encoding，中文乱码风险**（社区 confirmed）
- 修复：`[Console]::OutputEncoding = UTF-8` 或 PowerShell 7+

---

## 2. Mercury 现有 Codex 集成

| 文件 | 角色 |
|------|------|
| `D:\Mercury\Mercury\.codex\config.toml` | 全局 `developer_instructions` 14 条强制规则 |
| `scripts\codex\guard.ps1` | branch / commit / push 守卫 |
| `.claude/skills/dual-verify/SKILL.md` | Codex 通过 `subagent_type: codex:codex-rescue` 调用，不是 shell exec |

**用户 auth**：`~/.codex/auth.json` `auth_mode: apikey`，session 历史从 2026-02 起活跃。

---

## 3. 决策矩阵

| 维度 | Codex CLI (`codex exec`) | Claude API direct (Haiku 3.5) | OpenAI API direct (gpt-4o-mini) | No-LLM (deterministic) |
|------|--------------------------|-------------------------------|--------------------------------|------------------------|
| 计费 | API key per-token / 订阅 | per-token (~$0.00025/call) | per-token (~$0.0002/call) / 订阅 | $0 |
| 延迟 | 5-10s | 0.5-1.5s | 0.5-1s | <50ms |
| JSON 可靠性 | 中（schema 但 stdout 脆） | 高（tool_choice/JSON mode） | 高（response_format: json_schema） | N/A |
| Windows 兼容 | 已知坑（encoding/MSYS2） | 无（HTTP only） | 无（HTTP only） | 无 |
| Mercury 集成 | 中（要重构 codex 调用） | 低（@anthropic-ai/sdk 成熟） | 低（openai npm 成熟） | 零 |
| Per-call system prompt | 不支持 | 支持 | 支持 | N/A |

---

## 4. 关键反思 — Router MVP 是否真的需要 LLM

研究发起时假设 router 需要 LLM 做"自然语言意图解析"。重新审视用户已 confirm 的 P0#3 路由方案：

**用户 P0#3 设计**：
- 加前缀 `@<label>` → 显式路由
- 不加前缀 → 默认最近 active
- `/status` / `/list` 命令 → 返回 session 概览
- `/cancel` / `/continue` → 路由到目标 session

这些**全部是 deterministic 的**：
- 前缀解析：regex `^@([\w-]+)\s+(.+)$`
- 命令解析：regex `^/(\w+)`
- "最近 active"判定：router 自己维护 ownership 时间戳

**router MVP 不需要 LLM**。

LLM 推理才有用的场景（**非 MVP**）：
- 用户发口语化自由文本"把刚才那个任务取消" → 解析意图为 `/cancel main`
- 但这场景在 MVP 阶段可以让用户**自己加前缀**避免

---

## 5. 推荐

### MVP（Phase 5-2 第一版）

**纯 deterministic router，0 LLM 调用**。

- 前缀 + 命令解析全部 regex
- LOC 占用 ~80-120
- 零运行成本
- 零跨平台风险（不调任何外部 LLM CLI/API）

### 未来（Phase 5-3+）扩展自然语言意图解析时

**用 OpenAI API direct（gpt-4o-mini 或 gpt-5.4-mini）**，不要走 Codex CLI 壳：

- 绕过 5-10s 进程启动开销
- 当前已有 `OPENAI_API_KEY`，零配置
- 支持 `response_format: {type: "json_schema"}`
- 若想用订阅免 token：切到 ChatGPT 订阅登录后再调（但 OpenAI Messages API 不在订阅范围，仅 Codex CLI 在）

### Codex CLI 保留现有用途

- dual-verify code audit（subagent 模式，延迟不敏感）
- 不引入 router runtime 路径

---

## 6. Sources

**WebFetch 2026-04-25：**
- [Command line options — Codex CLI](https://developers.openai.com/codex/cli/reference)
- [Non-interactive mode — Codex](https://developers.openai.com/codex/noninteractive)
- [SDK — Codex](https://developers.openai.com/codex/sdk)
- [Models — Codex](https://developers.openai.com/codex/models)
- [Pricing — Codex](https://developers.openai.com/codex/pricing)
- [Windows — Codex](https://developers.openai.com/codex/windows)
- [MSYS2 Bash Discussion #3580](https://github.com/openai/codex/discussions/3580)

**WebSearch + 社区源：**
- [ChatGPT plan Codex usage](https://help.openai.com/en/articles/11369540-using-codex-with-your-chatgpt-plan)
- [Codex usage limits 2026](https://blog.laozhang.ai/en/posts/openai-codex-usage-limits)
- [Codex CLI lag #18663](https://github.com/openai/codex/issues/18663)
- [Codex CLI slow startup #9290](https://github.com/openai/codex/issues/9290)
- [PowerShell 5.1 ANSI encoding bug](https://community.openai.com/t/incorrect-cyrillic-rendering-in-codex-agent-on-windows-due-to-powershell-5-1-default-ansi-encoding/1356123)

**项目文件：**
- `D:\Mercury\Mercury\.codex\config.toml`
- `C:\Users\392fy\.codex\auth.json`
- `.claude/skills/dual-verify/SKILL.md`

---

## 7. 对 Issue #293 / ADR 的影响

- ADR 重写时 router 章节明确"MVP 0 LLM"
- LOC 估算：router ~80-120（无 LLM 调用栈）
- 未来扩展位置标注：`adapters/mercury-channel-router/intent-parser.cjs`（Phase 5-3 加）
- 用户的 OpenAI 订阅在 router 工作中**用不上**——保留给 dual-verify 这种延迟不敏感任务
