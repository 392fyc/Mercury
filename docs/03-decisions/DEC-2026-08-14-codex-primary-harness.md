# Codex CLI Becomes The Primary Harness

Date: 2026-08-14

## Scope

Claude 订阅暂时不可用，Mercury 的完整工作流切到 Codex CLI 0.147.0。本记录只写**决策与其代价**；
逐条实测记录、命令与判据在 Mercury 仓 `docs/04-research/mercury/issue-571-codex-migration-2026-08.md`，
执行台账在 [Mercury #571](https://github.com/392fyc/Mercury/issues/571)。

## 已定决策

- **认证走个人 ChatGPT 订阅**，不再使用 API key 方式；原 provider 配置保留但不被引用，
  回滚只需重新引用它。
- **不设 `model`**：用订阅侧默认模型，避免猜一个可能不存在的模型名。
- **不走 WSL**：Codex 的多进程协作走 JSON-RPC，原生 Windows 已实测跑通并行与过程中 steer。
- **OMC 不迁**（围绕 Claude Code 原语构建）；**superpowers 只当方法论 skill 包**，
  它不是 harness，补不上编排缺口。
- **dual-verify 的第二路 = 自发两个 subagent 盲审对比**（不同 reasoning effort），
  不依赖外部 harness。

## 强制层的真实构成（与迁移前的假设不同）

这是本次迁移**最需要记住的一条**：迁移前假设防护有四层，`hook` 是其中一层且是主力。
实测后**只有三层**，顺序为 **沙箱 → `.codex/rules/` → 指令层**，前面的先生效。

| 层 | 实测状态 |
|---|---|
| 沙箱 | 有效。仓外、`.git`/`.agents`/`.codex` 一律拒写，`workspace-write` 档位下也是 |
| `.codex/rules/` | **有效，且是唯一真正拦得住命令的层**。在 router 层 `declined in 0ms`，原样回显 justification |
| 指令层（`developer_instructions` / `AGENTS.md`） | 有效但靠自律，模型可以不听 |
| hook | **`PreToolUse`/`apply_patch` 实测不触发**（两条独立证据路径）。根因 UNVERIFIED |

**hosted tool 完全没有技术拦截**：`web_search` 这类工具既不触发 hook，
`.codex/rules/` 也够不着它（rules 匹配命令前缀）。所以「写代码前先查官方文档」这条规则
在 Codex 上**纯靠自律**，没有任何东西会拦。这一点必须让所有 agent 知道，
否则它们会以为有护栏兜底。

## 结构性边界（不是待办，是做不到）

- ~~**Codex 在沙箱下做不了任何 git 写操作**~~ —— **本条同日晚些时候被实测推翻，改为：**
  **git 写取决于沙箱档位。** `workspace-write` 下 `.git` 只读（`index.lock: Permission denied`），
  但 `danger-full-access` 下**可以正常提交**（干净测试仓验证）。原判断只测了一档就下了
  「任何 / 恒 / 不是配置能解的」这类全称结论，是从单点观测推全称。

  **升档不等于裸奔**：实测 `danger-full-access` 下 `.codex/rules/` 仍在 router 层
  `declined in 0ms` 拦截 `git push`。沙箱管**文件系统访问**，rules 管**哪些命令能被发出**，
  后者在命令执行之前生效、不受档位影响。

  **所以 dev-pipeline / pr-flow 能在 Codex 上跑完整链**（实证：commit `0a6c08c` 由 Codex
  自行暂存并提交），路径是 `danger-full-access` + rules 拦直接 git 命令 + 走 `git-safe.ps1`。
  建议日常仍用 `workspace-write`，**只在需要 git 写时临时升档**。
- **没有 Dynamic Workflow 的等价物**。Codex 的 subagent 由模型自主决定何时 spawn，
  不是脚本指定拓扑。确定性扇出只能由外层脚本起 N 个 `codex exec` 进程。
- **没有定时任务能力**，轮询类流程改用操作系统的计划任务。

## 尚未解决

**hook 为什么不触发，根因 UNVERIFIED。** 已排除：feature 未开、配置文件不存在、
事件名错、matcher 错、项目未受信任、配置路径不合法 —— 官方文档
（`https://learn.chatgpt.com/docs/hooks`）逐条对上，配置是正确的。
这是**文档行为与实测行为的真实分歧**。

下一步最经济的探针是跑一次**交互式**会话，用来区分「非交互模式本就不发出会话事件」
与「hook 引擎不工作」，而不是继续加进程采集手段。

## 方法论教训（比结论更耐用）

本次迁移反复出现同一类失败：**测量看起来给出了答案，实际不区分任何东西**。
共记录五种无效测量（详见 Mercury 仓主档附录三），其中最危险的一种是
「对照组是好的、但被测那次运行本身失败了」—— 很容易把它的空结果当成结论。

配套的三条：

- **「装好了」的判据必须是它在目标系统里生效**，不是磁盘上有文件。
  superpowers 一度被判定为已安装，实际市场源从未注册、插件完全惰性。
- **exit 0 不等于干了活。** 记忆层三个 hook 全部 exit 0，而 SessionEnd 什么都没记。
- **改一处必须横向查平行表述。** 同一事实往往写在两三个文件里，只改一处 = 静默分歧。
  本次由 dual-verify 盲审抓出两例。
