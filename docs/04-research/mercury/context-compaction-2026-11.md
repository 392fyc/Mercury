# 长会话里规则失效的原因与 compact 设置（2026-11）

Issue：[#777](https://github.com/392fyc/Mercury/issues/777)。现行做法见 [context-continuity.md](../../02-guides/context-continuity.md)。相关测量：[Agent 查不查本地资料](agent-lookup-measurement-2026-11.md)。

## 结论

- **症状。** Ship 的长设计会话里，同一要求要反复强调，已经定掉的旧方案被再次拿出来问，知识库里的规则读过却没用上。
- **主要原因是 compact 之后规则正文回不来。** 5 个最大的会话共 26 次 compact，其中 22 次是空闲触发的。每次之后启动钩子都运行了，但只给出记忆文件的路径；用户偏好文件在一个四天的会话里只在开头读过一次。
- **次要原因可能是上下文过长。** 规则还在上下文里却没用上的 4 次失误，都发生在 60 万 token 以上。这只是一个会话里的对应关系，外部实测不支持“变长就会忘”。
- **采取的做法。** compact 窗口从默认的约 96.7 万调到 40 万；启动钩子在每次启动和 compact 之后注入项目简报的正文；lane 简报在 compact 和恢复会话之后注入，目前只用于 SoT 的目录布局。空闲 compact 保持默认。
- **没有做的。** 不做向量检索，不做每轮注入。
- **Codex。** 同一套启动钩子也装在 Codex 上，注入同一份简报；它的 compact 触发点不改，因为实测已经在窗口的 95% 左右触发（见下文“Codex 的测量”）。

## 术语

- **compact：** Claude Code 把对话历史换成一份摘要，腾出上下文。
- **写满触发：** 上下文达到 compact 窗口时自动 compact。
- **空闲触发：** 会话空闲约一小时、上下文超过 20 万 token 时自动 compact，默认开启。
- **启动钩子：** 用户级的 SessionStart 钩子，会话启动、恢复、清空和 compact 之后都会运行。
- **简报：** 项目为注入而维护的一页短文件 `brief.md`。

## 测量

数据来自本机的 Claude Code 会话记录，模型为 `claude-opus-5-5`，1M 上下文。

### compact 的类型

统计 Ship 的会话记录里按文件大小最大的 5 个（2026-11-10 统计），数其中的 `compact_boundary` 记录。有一条记录在文件里重复出现，只算一次，共 26 次：

| 类型 | 次数 | 特征 |
|---|---|---|
| 空闲触发 | 22 | 之前空闲 69 分钟到 30 小时，compact 前上下文 24 万到 81 万 token，记录里带 `precomputed` |
| 写满触发 | 3 | compact 前 96.6 万到 96.7 万 token，摘要耗时 51 到 111 秒 |
| 手动 | 1 | — |

26 次之后启动钩子都运行了（记录里有 `SessionStart:compact` 的成功条目），输出只有路径。

### 失误与上下文占用

取一个持续四天、77 条用户消息的设计会话，逐条看用户提出异议的回合：

| 失误 | 当时上下文 | 相关内容当时在哪 |
|---|---|---|
| 说某条规则“没有条文”，实际总表里有 | 64.6 万 | 开头读过全文，其间没有 compact |
| 违反一条已读过的用户偏好 | 59.9 万 | 同上 |
| 把用户 3 分钟前的话理解反了 | 94.0 万 | 就在对话里 |
| 引用更早的会话写下的“用户确认过”，实为代理推断 | 77.9 万 | 记录不区分用户原话和代理推断 |
| 用旧规则质疑用户刚定的裁决 | 34.4 万 | 裁决在两次空闲 compact 之前 |
| 把一条旧规则自行推广后登记成决策 | 26.6 万 | 空闲 compact 之后 |

前四行发生时内容都还在上下文里；后两行发生在空闲 compact 之后。

这个会话 compact 了四次，用户偏好文件一次都没有重读，规则总表只在要改它的时候重读过。

### 常驻内容的大小

项目根的 `CLAUDE.md` 和它导入的 `AGENTS.md` 合计约 47KB，compact 后会重新注入，但写的是 harness 规则，不含设计规则和用户偏好。规则总表 57KB，lane 的检查点文件 68KB。

## 官方机制（已核对）

- compact 后 Claude Code 只重读最近改过的最多 5 个文件，超过 5,000 token 的文件只给路径不给正文。项目根的 `CLAUDE.md` 从磁盘重新注入；来源为 `compact` 的 SessionStart 钩子会运行，输出加入上下文。见 [What survives compaction](https://code.claude.com/docs/en/context-window)。
- 钩子的 `additionalContext` 上限是 10,000 字符，超出后只留前 2,000 字符的预览和一个文件路径，Claude 不会被要求去读那个文件。每个钩子的输入都带 `session_id` 和 `transcript_path`。见 [Hooks reference](https://code.claude.com/docs/en/hooks)。
- 原生 1M 上下文的模型默认在约 96.7 万 token 时 compact；`autoCompactWindow` 可设为 10 万到 100 万。用 `/autocompact` 设的值按模型保存在 `modelSettings` 里，对该模型优先于同一文件里顶层的 `autoCompactWindow`。见 [Model configuration](https://code.claude.com/docs/en/model-config)。
- 空闲 compact 的条件和开关见 [`idleCompaction`](https://code.claude.com/docs/en/settings-reference)，最小规模默认 200,000 token。
- `CLAUDE.md` 建议每个文件不超过 200 行，更长会降低遵守程度。见 [How Claude remembers your project](https://code.claude.com/docs/en/memory)。

## Codex 的测量

数据来自本机的 Codex 会话记录。

| 模型窗口 | 实际触发 compact 的位置 |
|---|---|
| 258,400 token | 大多数在 23.7 万到 24.8 万 |
| 475,000 token | 大多数在 44.3 万到 45.2 万 |

两组各有少量离群值（例如 258,400 窗口下有 4 次在 22.5 万附近），不影响“约 95%”这个结论。

- 观察到的 compact 都是写满触发，没有空闲触发。其中一个会话 compact 了 16 次，都在 23.8 万到 24.7 万之间（14 次在 24 万到 24.5 万之间）。
- compact 之后 Codex 会重新给出 `AGENTS.md`，并保留用户消息的原文。
- 原先装在 Codex 上的启动钩子是旧脚本，在下游项目的目录里没有任何输出。
- 实测（Codex 0.167，手动 `/compact`）：钩子的消息在 compact 记录之后、下一条用户消息之前再次出现。回合进行中的自动 compact 没有测到；更早版本的记录里，3 次 compact 之后只有 1 次能看到钩子消息。
- Codex 钩子输出的上限按 token 计，默认约 2,500。一份 4,371 字符的中英混排文档估算约 2,500 token（模型估算，本机没有分词工具），所以把这个钩子的上限设为 10,000。

## 外部情况

- 同类反馈很多：[anthropics/claude-code#70555](https://github.com/anthropics/claude-code/issues/70555)（工作状态跨 compact 丢失）、[#44648](https://github.com/anthropics/claude-code/issues/44648)（规则随会话变长被稀释）、[#98747](https://github.com/anthropics/claude-code/issues/98747)（空闲 compact）。常见的补救是维护一页检查点文件，由 `compact` 来源的 SessionStart 钩子打印回去。
- 社区使用的 compact 窗口有 28.3 万、30 万、40 万、50 万，以及主会话 60 万。没有找到官方推荐的具体数值。
- 反面证据：[claude-code-context-rot-lab](https://github.com/thestackunderflow/claude-code-context-rot-lab) 在 79 万 token 的会话里埋 4 条事实和 1 条规则，全部答对，`/compact` 之后也全部保留。它只测了 Haiku 5.5，每组一次，测的是短事实的回忆。

## 数值怎么定的

- **compact 窗口 40 万。** 内容还在上下文里的失误都在 60 万以上，40 万把这一段切掉。没有取 30 万，是因为空闲 compact 已经在 20 万以上频繁发生，再压低会增加工作中途的 compact，而 26 万到 46 万之间的失误都跟在空闲 compact 之后，更像是 compact 丢内容造成的。30 万和 40 万之间没有证据分高下，这是判断。
- **简报合计 6,000 字符。** 上限是 10,000 字符，位置说明约占 700 字符，留出余量。超出的页不截断，钩子改为提示去读文件并告知用户。
- **空闲 compact 不关。** 关掉后每次回到会话都要重新处理几十万 token 的未缓存上下文。

## 未核实

- 上下文长度与失误的因果关系。
- 设置文件里的 `autoCompactWindow` 是否只在进程启动时读取。依据只有 [anthropics/claude-code#96467](https://github.com/anthropics/claude-code/issues/96467) 里用户的报告，官方页面没有写。
- 空闲 compact 从哪个版本开始默认开启。[#98747](https://github.com/anthropics/claude-code/issues/98747) 的报告者说是 2.1.286，官方页面没有写版本。
- 用户级的 `autoCompactWindow` 在桌面版预热进程启动的会话里是否生效（[#105316](https://github.com/anthropics/claude-code/issues/105316) 报告的是项目级设置不生效）。
- 注入一次之后，随着上下文再次变长，简报是否仍被遵守。

## 之后看什么

跑一两批设计任务后回看会话记录：compact 是否在 40 万附近触发，compact 之后是否还出现上表里的同类失误。仍然出现时，再考虑每条用户消息注入一小段，或调整窗口。
