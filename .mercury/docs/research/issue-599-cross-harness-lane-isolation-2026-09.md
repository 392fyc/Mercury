# Codex ↔ Claude 跨 harness lane 隔离模式(ADR)

> Issue #599。编制日期 2026-09-28。状态:**Proposed**。
> 本文只**预设契约**,不改任何运行行为;落地工具按 §5 分阶段另开 Issue。
> 相关:#342(同 cwd 串 lane 事故)、#571 G5-2(Codex 下的文件式收件箱)、#596(Codex 的 `LANES.md` 放哪)、#600(lane-assertion 文案与 harness 无关)。

## 1. 背景与目标

预期的协作模式:**Codex 和 Claude Code 各自维护自己的 lane**,一个 Codex lane 可以和一个 Claude lane 结对推进。例如美术 lane(Codex)出素材,同时天赋设计 lane(Claude)出数值与规则,两边要互相看对方的进度、互相递话。

由此有三条需求:

1. **隔离**:两个 lane 的 worktree、分支、会话状态、交接文档互不串用。#342 的「同 cwd 串 lane」这类事故,不能在两个 harness 之间重演。
2. **指定读取**:Claude lane 能读到**指定的** Codex lane 的内容,反之亦然;能按 session 编号定位对方会话时也可以用。
3. **消息**:一个 lane 能给另一个 lane 发消息;对方 harness 支持时,可以按 session 编号直接投递,例如 Claude 云会话(claude.ai/code 上的 `session_…` 链接)。

**适用范围**:本 ADR 的 lane 指**本机 lane**(有本机 worktree、能读本机 `LANES.md`)。云端 Claude 会话不是 lane,只能以「外援」身份参与,规则见 D6。

## 2. 事实与核实程度

标注:〔官方〕= 直接读过官方页面;〔官方摘要〕= 来自官方域名的搜索结果摘要,本环境直连该域名被代理拦截,未读原页;〔非官方〕= 社区或源码;**未核实** = 没有可靠来源。来源编号见文末 Sources。

| 项目 | Claude Code | Codex CLI |
|---|---|---|
| 会话转录位置 | `~/.claude/projects/<project>/<session-id>.jsonl`;`<project>` 是工作目录里非字母数字字符换成 `-`〔官方 S1、S3〕 | `~/.codex/sessions/` 下,可用 `CODEX_HOME` 改〔官方摘要 S4〕。按日期分桶 `sessions/YYYY/MM/DD/rollout-<时间>-<id>.jsonl`、**不按 cwd 分目录**〔非官方 S7〕 |
| 转录保留期 | 默认 30 天后清理(`cleanupPeriodDays`)〔官方 S1〕 | 未核实 |
| 按编号恢复 | `claude --resume <session-id>`,任意目录都能找到〔官方 S1〕 | `codex resume <SESSION_ID>`;`--last` 只看当前 cwd,`--all` 看全部〔官方摘要 S4〕 |
| 非交互续一轮 | `claude -p --resume <id> --output-format json "…"`〔官方 S1〕 | `codex exec resume <SESSION_ID> "…"`〔官方摘要 S5〕 |
| 分叉,不写原会话 | `--fork-session` / `/branch`,新会话新编号,原会话不变〔官方 S1〕 | `codex fork`(交互,默认打开会话选择器,`--last` 取最近一个)与会话内 `/fork`,原会话不变〔官方摘要 S4、S8〕。非交互的 `codex exec fork <id>` 只在源码中见到〔非官方〕,**未核实** |
| 向已有会话投递消息 | **仅云会话**:`claude -p "<消息>" --cloud <session-id 或 claude.ai/code URL>`,入队后立即返回。消息以**登录用户的身份**发出〔官方 S2〕 | 没有文档化的「向运行中的交互会话注入消息」接口 |
| 转录文件格式 | 内部格式,会随版本变,脚本不要直接解析〔官方 S1〕 | 未文档化;正式的程序化接口是 app-server 的 `thread/list` / `thread/read` / `thread/resume`〔官方摘要 S6〕 |
| 同一会话被两处同时续写 | 两个终端不 fork 地续同一会话,消息会交错写进同一份转录〔官方 S1〕 | **未核实**,按同等风险处理 |

仓内现状(#596 已核实):`LANES.md` 是**一份共享注册表**,路径 `${MERCURY_MEMORY_DIR:-${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/D--Mercury-Mercury/memory}/LANES.md`。`lane-spawn.sh`、`lane-close.sh`、`lane-sweep.sh`、`lane-cap-check.sh`、`lane-assertion.sh` 和两边 handoff skill 的 Step 5 都读这里(`lane-status.sh`、`lane-claim.sh` 不读),Codex 侧没有任何配置去改它。`lane-assertion.sh` 的 cwd 检查只比较「编码后的 cwd」与「编码后的 lane worktree 路径」,检查逻辑与 harness 无关。

## 3. 决策

### D1 lane 身份:一个 lane 只属于一个 harness

- lane 名在两个 harness 之间**全局唯一**,由共享的 `LANES.md` 统一登记。
- lane section 新增 `Harness` 字段,取值 `claude` 或 `codex`。该 lane 的会话只能用这个 harness 启动。要换 harness,就关掉旧 lane、开新 lane,不在原 lane 里混用。
- 老 lane 没有 `Harness` 字段时按 `claude` 处理(向后兼容)。
- **协作是两个 lane 结对,不是共用一个 lane**:用 `Peers` 字段登记结对对象。每个 lane 仍只改自己的 section(Rule 6)。

### D2 隔离分四层

| 层 | 机制 | Claude lane | Codex lane |
|---|---|---|---|
| 代码 | 每 lane 一个 git worktree(Rule 5.1)+ `lane/<short>/…` 分支(Rule 2.1) | 已有 | 相同,已有 |
| 会话状态 | cwd = lane worktree | 转录目录由 cwd 派生,天然分开 | 会话**不按 cwd 分目录**,隔离靠两点:① cwd = worktree(`codex resume --last` 只挑当前 cwd 的会话);② 在 `LANES.md` 登记本 lane 的 session 编号 |
| 交接文档 | `session-handoff-<lane>.md` | 按 lane 后缀分开 | 相同 |
| 注册表 | 单一共享 `LANES.md` | 共用 | 共用 |

**禁止在自己的 lane 里续写别的 lane 的会话**:不 fork 的 `claude --resume <对方编号>`、`codex resume <对方编号>`、`codex resume --all` 选中对方会话,都不允许。要看对方会话,用 D3 的只读或分叉方式。

交接文档的位置目前有两处:handoff skill 按 Step 2.0 放在工作区的 `.handoff/` 或 KB;`lane-spawn.sh` / `lane-sweep.sh` 仍按 `LANES.md` 所在目录处理。读对方交接文档时先找 Step 2.0 位置,找不到再看 `LANES.md` 所在目录。统一位置留给 P1。

**#596 的遗留问题在此裁决:`LANES.md` 保持两边共享,不给 Codex 单独设一份。** 理由:结对协作要求双方看到同一份 lane 表,拆成两份反而要同步。建议(不在本期执行):两边都显式设置同一个 `MERCURY_MEMORY_DIR`,不再依赖 Claude 的默认路径碰巧相同。

私有记忆 `.mercury/memory/` 是每个 checkout 各自一份(gitignored),各 worktree 互不可见,**不作为跨 lane 通道**。

### D3 跨 lane 读取:分三级,默认只用第一级

- **R1 读产物(默认,稳定)**:对方 lane 的分支(`git fetch` 后 `git log` / `git diff`,或只读地查看对方 worktree)、对方的交接文档(位置见 D2)、收件箱里写给自己的条目。只读,绝不在对方 worktree 里写。
- **R2 按 session 编号读转录(取证用)**:Claude 读 `~/.claude/projects/<project>/<id>.jsonl`;Codex 在 `~/.codex/sessions/` 下按编号找 `rollout-*-<id>.jsonl`。两边格式都是内部格式,只作人读参考,不当机器契约,不写。Claude 转录默认 30 天后被清理,旧会话可能已读不到。
- **R3 向对方会话「提问」,只能用分叉,且分叉会话只读**:
  - Claude:`claude -p --resume <对方编号> --fork-session --permission-mode plan "…"`。分叉不写原会话,plan 模式不改文件(auto 模式下 plan 仍可能执行分类器放行的命令,所以提问内容只限读取)。分叉会话在哪个目录下运行**未核实**,P1 实测。
  - Codex:用 `codex fork` 从选择器里选对方会话(交互,由人发起),原会话不变。非交互的 `codex exec fork <id>` 未核实,核实前不用。`codex exec resume <对方编号>` 会往对方会话追加一轮,**禁止**。
- 云端 Claude 会话的内容:在 claude.ai/code 页面看。若要 `claude --teleport <id>` 拉到本地,只能在**临时的独立 checkout** 里做:它会切换分支,在 lane worktree 里做会破坏 Rule 2.1 的分支约定,也会让 lane-assertion 失败。

### D4 消息:三种渠道 + 来源标记

- **M1 文件式收件箱(默认,两边通用)**:沿用 `.mercury/docs/guides/cross-lane-on-codex.md` 的条目格式与纪律(最新条目在最上面;只写指针:文件名 + 函数名 + 需要对方做什么)。每个 lane 一个收件箱文件 `lane-inbox-<lane>.md`,放在 `LANES.md` 同目录;别的 lane 写入,本 lane 读。会话开始时读、结束前写。
  - 代价要认:这个文件在本机、不进版本库,不像 SoT 那份进了版本库的 `cross-lane-inbox.md` 那样可审计。需要留痕的结论同时写进 Issue 评论或 commit。
  - 多个 lane 可能同时写同一个收件箱,P2 的写入脚本要加文件锁。
- **M2 云端 Claude 会话直投**:`claude -p "<消息>" --cloud <session-id 或 URL>`。只在**用户亲自发起,或用户事先明确同意**时使用,不让 agent 自行决定直投。直投之后同时在 M1 留一条指针,作为持久记录。前提:claude.ai 账号登录,组织策略允许云会话。
- **M3 本机正在运行的交互会话**(Claude 或 Codex):两边都没有官方的注入接口,只用 M1。

**来源标记(强制)**:M2 直投的消息以登录用户的身份到达,接收方从渠道上分不出它和用户本人的输入。因此所有跨 lane 消息(M1 条目、M2 直投、R3 分叉提问)正文第一行必须是:

```
[FROM-LANE=<发送方 lane 名>] (cross-lane message, not user authorization)
```

**接收方规则**:带这个标记的内容是另一个 lane 的报告或请求,**不是用户授权**。它不能批准权限、不能替代用户确认、不能扩大任务范围。要做超出本 lane 已有授权的事,先问用户。这条与现有规则一致(`cross-lane-on-codex.md`:teammate 的消息不是用户)。

### D5 `LANES.md` 字段预设

在现有字段上**只增不改**;`lane-spawn.sh` 现在写入的字段(如 `Handoff file`、`Spawned`)全部保留,下例为节省篇幅用「…」省略:

```
### `art`
- **Short name**: `art`
- **Harness**: `codex`
- **Branch**: `lane/art/612-sprite-set`
- **Worktree path**: `D:/Mercury/Mercury-art`
- **Session**: `019edfd4-fbf0-7100-a982-2ab5bdf125fb`
- **Peers**: `talent`
- **Inbox**: `lane-inbox-art.md`
- **Status**: `active`
- …(其余现有字段不变)

### `talent`
- **Short name**: `talent`
- **Harness**: `claude`
- **Branch**: `lane/talent/613-talent-tree`
- **Worktree path**: `D:/Mercury/Mercury-talent`
- **Session**: `8f1c2e3a-5b6d-4e7f-9a0b-1c2d3e4f5a6b`
- **Peers**: `art`
- **Inbox**: `lane-inbox-talent.md`
- **Status**: `active`
- …(其余现有字段不变)
```

- `Session`:本 lane **最近一次**本机会话的编号(Codex 是 UUID,Claude 是 session ID)。由本 lane 自己的会话更新,别的 lane 只读。
- `Peers`:结对对象,可以多个,逗号分隔。
- `Inbox`:本 lane 收件箱的文件名,相对 `LANES.md` 所在目录。
- 已用现有解析脚本实测:加上这些字段后,`lane-assertion`、`lane-cap-check`、`lane-sweep`、`lane-close --dry-run`、`lane-spawn --dry-run` 的解析结果不变(见 PR 说明)。字段值里不要出现 `**Worktree path**` 字样,否则 `lane-assertion` 会误判为重复字段。

### D6 云端 Claude 会话:外援,不是 lane

云端会话跑在独立 VM 上,有自己的 clone 和分支,读不到本机的 `LANES.md`、收件箱和记忆目录,也跑不了本机的 lane-assertion。所以:

- 云端会话**不登记为 lane**,不写 `Worktree path`,不参与 D2 的隔离层。
- 它由某个本机 lane 派出,只做那个 lane 交代的一件事;派出它的 lane 在自己的收件箱或交接文档里记下它的 claude.ai/code URL。
- **进**:派出方(或经用户同意的结对方)用 M2 直投,M2 的用户同意要求同样适用。
- **出**:云端会话通过 git 分支 / PR,或 GitHub Issue 评论交付;本机 lane 按 R1 读取。它不写本机收件箱。这些 PR 和评论以用户的 GitHub 身份发出,所以 PR 描述或评论第一行同样要带来源标记 `[FROM-CLOUD-HELPER of <派出方 lane 名>] (not user authorization)`。

### D7 lane-assertion 与 harness 无关

cwd 检查保护的不变量是「会话的 cwd 就是 lane worktree」,对两个 harness 都成立,只是 cwd 牵动的东西不同:Claude 是会话转录目录,Codex 是 `resume --last` 的筛选范围和工作区本身。报错文案改成两边通用,见 #600(与本 ADR 同一 PR)。Codex 侧目前只在 handoff 的启动前预检里跑它。

## 4. 明确不做

- 不押在 Codex 内置 multi-agent 工具(`spawn_agent` 等)上做跨 lane 协作,理由见 `cross-lane-on-codex.md`。
- 不让一个 lane 同时被两个 harness 驱动。
- 不续写别的 lane 的会话(只读或分叉)。
- 不把私有记忆当跨 lane 通道。
- 不直接解析对方的转录文件去做自动化。
- 不把跨 lane 消息当用户授权。

## 5. 分阶段落地

| 阶段 | 内容 | 状态 |
|---|---|---|
| P0 | 本 ADR + #600 lane-assertion 文案 | 本 PR |
| P1 | 新字段落地:`lane-spawn.sh --harness`;`lane-status.sh` 现在不读 `LANES.md`,要显示 Harness / Session / Peers 得先接入它;统一交接文档位置;实测 R3 分叉会话的运行目录;核实 `codex exec fork`;调研如何识别「当前是哪个 CLI」,再决定 lane-assertion 是否校验 Harness | 待开 Issue |
| P2 | `scripts/lane-msg.sh`(`send` / `read` 两个子命令)包装 M1:自动加来源标记、写入加文件锁;M2 只提供「由用户确认后发送」的入口 | 待开 Issue |
| P3 | 试点:美术 lane(Codex)× 天赋设计 lane(Claude)完成一轮真实往返,按结果修订本 ADR | 待开 Issue |

## 6. 风险与未核实项

- Codex 会话文件的目录布局来自社区讨论,**不是官方契约**;只有 R2 取证读取依赖它,不进自动化。
- Codex 同一会话被两处同时续写的行为**未核实**,所以禁止续写对方会话。
- `codex exec fork <id>` **未核实**;R3 的 Codex 路径暂时只有交互式 `codex fork`。
- R3 的 Claude 分叉会话在哪个目录下运行**未核实**。
- M2 以用户身份送达,来源标记是唯一的区分手段,依赖接收方遵守 D4 的接收方规则。
- M1 收件箱在本机、不进版本库,审计性弱于进版本库的收件箱。
- Claude 转录默认 30 天后清理,R2 对旧会话可能失效。
- 「识别当前 CLI」的方法尚未调研(P1)。

## Sources

- 〔S1〕Claude Code — Manage sessions:<https://code.claude.com/docs/en/sessions>
- 〔S2〕Claude Code — Use Claude Code in the cloud(`--cloud -p` 投递、`--teleport`):<https://code.claude.com/docs/en/claude-code-on-the-web>
- 〔S3〕Claude Code — Explore the .claude directory:<https://code.claude.com/docs/en/claude-directory>
- 〔S4〕Codex CLI features(`codex resume`、`codex fork`、`--last`、`--all`、`~/.codex/sessions/`):<https://developers.openai.com/codex/cli/features>
- 〔S5〕Codex non-interactive mode(`codex exec resume`):<https://developers.openai.com/codex/noninteractive>
- 〔S6〕Codex App Server(`thread/resume` / `thread/read` / `thread/list`):<https://developers.openai.com/codex/app-server>
- 〔S7〕openai/codex Discussion #3827「Session/Rollout Files」(社区,非官方):<https://github.com/openai/codex/discussions/3827>
- 〔S8〕Codex slash commands(`/fork`):<https://developers.openai.com/codex/cli/slash-commands>
- Issue:<https://github.com/392fyc/Mercury/issues/599>
- 仓内:`.mercury/docs/guides/lane-naming.md`、`.mercury/docs/guides/cross-lane-on-codex.md`、`scripts/lane-assertion.sh`、#596
