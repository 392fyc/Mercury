# 让 Claude「说人话」:中文输出与术语清晰度的根因诊断与修复方案研究报告

> 编写日期:2026-06-30 · 范围:Claude Code 框架的语言/术语清晰度治理 · 受影响配置:用户级 `~/.claude/` + 项目级 `D:\Mercury\Mercury\.claude\`
>
> 关联 Issue:[#525](https://github.com/392fyc/Mercury/issues/525)(side-bug 路线) · 阶段:**仅研究,本会话未改任何配置**(GATE 在用户) · 产出方式:17 个 agent 的 Workflow(官方文档验证 → 根因诊断 → 方案对抗式核验 → 综合);三处承重事实(项目 `settings.json` 的 `language` 键、两处「仅里程碑用中文」矛盾)已由主控 agent 亲自本机复核通过

---

## 0. 实施记录与问题再定义(2026-07-01,用户确认后)

> 本节是定稿后补充。研究阶段我对问题的描述有偏差,用户当面纠正了两次,这里记录纠正后的准确定义和本会话实际落地的改动。下文第 1–8 节保留原始研究分析(含其诚实的限制说明),阅读时以本节的再定义为准。

### 问题再定义(用户原话归纳)

- 真正的毛病**不是**「该说中文却说了英文」,而是「不用正常中文表达,改用网络黑话 + 内部简写,再加过度精简,导致看不懂」。
- 「黑话」**同时包括英文简写**(nit-loop、escape-hatch、soak 等)**和中文圈内 / 网络说法**(做减法、止血、钉死、兜底、翻车、打空靶 等),两类都要换成平实正常的说法。
- **英文词汇本身不用刻意减少**——只要不是中文黑话,大部分英文用户都能理解;专有名词、特定术语用英文反而更清楚。
- 因此:本报告与旧 §Language 表里「把英文强制翻成中文」的框架是**错的**;正确目标是「用正常完整的中文句子 + 去黑话(把缩写展开成人话)+ 不过度精简」,而不是翻译。
- 对第 7 节可度量标准的相应修正:首要指标改为「网络黑话 / 内部简写出现次数(中英都算)+ 是否完整正常句子 + 是否过度精简」;「未翻译英文词数」降级,英文只在「本身构成黑话」时才计,正常的专名 / 术语英文不计。

### 本会话实际落地(用户批准「全做」,按 #259 治理)

1. **新建用户级 output-style**:`~/.claude/output-styles/clear-chinese.md`(`keep-coding-instructions: true`),并在 `~/.claude/settings.json` 设 `"outputStyle": "clear-chinese"`。正文 = 上面的「说人话」原则 + 「黑话 → 正常说法」对照 + 正反例 + 白名单。这是系统提示层、每轮生效。
2. **改写 `~/.claude/CLAUDE.md` §Language**:删掉量化交易翻译表与反例,换成「说人话」要点 + 「允许保留英文」白名单(文件 7984 → 6372 字节),并指向 output-style 避免重复。
3. **消除跨文件矛盾**:项目 `CLAUDE.md` 的「Chinese for milestones」改为「所有响应都用正常清楚的中文」;`CLAUDE.local.md`(简洁模式)的里程碑例外同步改为「所有回复正常清楚中文,简洁只去客套、不牺牲可读性」。
4. **写入门控**:做成 output-style 与 CLAUDE.md 里的「写记忆 / handoff 时先自检黑话与精简度」行为规则,**未新增常驻钩子**——因为用户正受工具调用 / 钩子类问题困扰(见 #527),此刻加钩子的风险与时机都不好。
5. **未做**(用户未选):清洗历史存储、每轮提醒钩子、韩日文 Stop 钩子。

### #259 治理记录

- **备份**:`~/.claude/settings.json.backup-pre-525`、`~/.claude/CLAUDE.md.backup-pre-525`、`D:/Mercury/Mercury/CLAUDE.local.md.backup-pre-525`。
- **验证**:settings.json 为合法 JSON 且 `outputStyle=clear-chinese`;无项目级 `outputStyle` 覆盖;output-style 文件 frontmatter 正确;`~/.claude/CLAUDE.md` 结构完好(User Terminology / USER:END 保留)。
- **生效条件**:output-style 与 CLAUDE.md 改动需 `/clear` 或开新会话才载入。
- **回滚**:删 `~/.claude/output-styles/clear-chinese.md` 或把 settings 的 `outputStyle` 改回 `Default`;其余 `mv` 回各自 `.backup-pre-525`;项目 `CLAUDE.md` 走 git 回退。
- **真实效果待验**:全部为软约束,须在新会话用第 7 节(经上面修正后的)标准实测,不能凭机制断言已生效。

---

## 1. 摘要

**问题**:尽管多份配置文件里都写了「始终用简体中文」的规则,Claude 的实际输出仍频繁出现两个毛病——一是把本可翻译的英文技术黑话直接夹在中文里(如 advisory、nit-loop、escape-hatch),二是过度简略到像电报。

**根因一句话**:语言规则是「提示层的软约束」(官方明示「不保证严格遵循」),它既被压在大量英文上下文里被稀释,翻译对照表又是错位的「量化交易领域词」,而每次会话自动载入的记忆/交接文档本身就满屏黑话,等于持续给模型灌一份「反面示范」让它照着模仿。

**推荐一句话**:采用分层纵深防御,但据本机实测重排优先级——**框架层的语言锁(`language` 键)已经部署且生效,无需再做**;真正的高杠杆是「清洗自动载入的记忆/交接文档」+「新建一个承载 harness 领域术语词表的用户级 output-style」+「消除『仅里程碑用中文』的跨文件矛盾」;所有改动 `~/.claude/` 的动作必须先经用户批准(GATE)、先备份再改。

---

## 2. 问题与现状(本地配置实况)

本节所有断言均经本机实测核验,不沿用旧观察。

### 2-1 框架层语言锁:已部署,但只锁住了「基础语言」

- 项目级 `D:\Mercury\Mercury\.claude\settings.json` 第 147 行**已有 `"language": "zh-CN"`**。
- **这是对前期调研材料的一处重要更正**:材料里多处断言「user/project 三处全未设 `language` 键」,实测不成立——项目级早已设上。
- 关键含义:既然框架层语言旋钮已开、基础中文响应也基本生效(用户抱怨的是「黑话混杂」而非「整段变英文」),这就**用实证说明了 `language` 键管不了术语翻译**。它只锁「用哪种语言回答」,锁不住「黑话要不要译成中文」。这条实测把整个方案的重心从「补语言锁」转向了「治术语翻译 + 治过度简略」。

### 2-2 系统提示层:output-style 完全未启用

- `~/.claude/output-styles/` 目录**不存在**;用户级 `~/.claude/settings.json` 里**没有 `outputStyle` 键**。
- 即:当前没有任何规则被提升到「系统提示(system prompt)」这一更高权重的注入层,语言规则全靠用户消息层的 CLAUDE.md 承载。

### 2-3 用户消息层:语言铁律被压在英文块下,翻译表是量化交易领域词

- `~/.claude/CLAUDE.md` 第 1–64 行是全英文的 OMC 编排块(`<!-- OMC:START -->`…`<!-- OMC:END -->`);第 67 行是 `<!-- USER:START -->` 用户可维护区起点(注释明示 `omc-setup --global --force` 可能覆盖此区)。
- §Language 标题在第 69 行,核心铁律「Always respond in **zh-CN**…」在第 71 行——它正好位于用户可维护区的最前一句(故所谓「再前移」空间极小)。
- 第 88 行起的「禁用→应译为」对照表与第 124–128 行的反例,**100% 是量化交易领域词**:baseline、watchlist、lookahead、spurious、regime、oracle、classifier、ensemble、residual…;反例也全是炒股句式(`P_a baseline`、`Utilities watchlist`、`REG_oai_side projection`)。
- 而用户实际抱怨的 harness/开发黑话(retire、stale、advisory、escape-hatch、nit-loop、cherry-pick、carve-out、fan-out)在这张表里**几乎零覆盖**——这是最高杠杆的「正例引导」却全部打在空靶上。该表显然是从旧的量化项目直接搬来的冻结产物,从未为 Mercury(一个 harness/开发仓)重做领域适配。

### 2-4 运行时层:无语言钩子

- 用户级 `UserPromptSubmit` 事件只挂了 `keyword-detector.mjs`(只检测技能关键词,不注入语言提醒);`Stop` 事件挂了 `persistent-mode.mjs` 和 `code-simplifier.mjs`,没有任何语言自检钩子。

### 2-5 跨文件矛盾与模式冲突

- 项目级 `CLAUDE.md` 第 90 行只把中文 carve(切)给「里程碑消息」(`Chinese for milestones`);`CLAUDE.local.md`(简洁模式 / caveman)第 23 行同样只 carve 里程碑。这两个文件加载时机比全局文件更晚,按官方「更具体者实际胜出」的拼接规则,可能被读成「只有里程碑用中文、其余可英文」。
- 简洁模式要求「丢弃填充词、尽量短」,而「先中文后括号附英文 + 把黑话展开成人话」本身就增加字数——两者存在直接张力,第 71 行的护栏只声明了「简洁模式不改语言」,却没豁免「翻译展开的字数成本」。

---

## 3. 根因诊断(三角度,按置信度排序)

> 三个角度互补,不是互斥。综合结论:这是一个「软约束 + 上下文污染 + 内容错位」叠加的系统性问题,单点修不动。

### 角度 A:运行时强制缺失 + 词表领域错配(置信度:高)

- **词表错配**(最高杠杆、最确定):翻译表与反例 100% 是量化交易词,与 Mercury 的 harness/开发工作零重叠。官方已验证「正例示范(few-shot)是最可靠的风格引导,且优于禁止清单」——但当前所有正例都落在用户已不工作的领域,等于把最强的引导火力打在空靶上。
- **运行时无人查**:语言规则纯靠 CLAUDE.md 静态文本(用户消息层,官方明示「无严格遵循保证」),四个框架级硬机制中,`language` 键已设(2-1),但 output-style、UserPromptSubmit 语言提醒、Stop 语言自检三者全部闲置。
- **简洁模式冲突**:简洁模式的「最短字数取胜」反向激励保留英文原词(`nit-loop` 7 个字符,展开成「挑刺循环(nit-loop)」更长),与翻译展开目标对撞。

### 角度 B:上下文风格镜像(置信度:中-高;「语言维度的镜像」官方标记为未证实,机制属外推)

- 每会话自动载入的持久记忆(`MEMORY.md`,启动时载入前 200 行或 25KB)本身就是满屏「中英黑话混杂 + 极度简略」的范本,等于给模型灌了一份巨量的「错误风格示范」。模型把实际读到的这些散文当作上下文里的风格分布去模仿,镜像力压过几十行孤立的 §Language 规则。
- 体量严重不对称:§Language 规则几十行,被同窗口里上千 token 的黑话散文淹没。官方已验证「上下文越长,召回越差(context rot)」「注意力预算有限,每个 token 都在摊薄注意力」——长上下文里规则被稀释,而占绝对体量的恰是反面示范。
- 官方已验证「提示/上下文的风格会影响响应风格」「模型向上下文里的常见分布收敛」。唯独「中英混用 / 语言切换」这一具体维度在官方文档里**没有专门条目(标记为未证实)**,故此角度的语言部分属于从通用「风格镜像」机制外推,须以实测为准。
- **自我强化闭环**:每个会话用黑话写交接/记忆 → 下个会话载入 → 模仿 → 写出更多黑话 → 回写记忆。漂移逐会话复利累积,任何一次性清洗都会被下一轮回写重新污染。

### 角度 C:规则位置与稀释(置信度:中)

- 语言铁律被压在 64 行英文 OMC 编排块之下;三份 CLAUDE 文件整体以英文为主导语域(OMC 全英、项目的 MUST/DO NOT 全英、简洁模式全英),语言铁律成了英文海洋里的中文孤岛,持续诱导模型模仿英文输出。
- 跨文件「窄化矛盾」:项目文件只把中文 carve 给里程碑(2-5),按官方拼接规则可能被读成「其余可英文」。这是本角度里**置信度最高**的一条(逻辑明确、可直接验证)。
- 总指令面过大(全局约 150 行 + 项目约 130 行 + 约 22KB 记忆),语言规则只是数十条之一,触发官方「文件越长遵循度越低」。
- **重要更正(本报告对材料的纠偏)**:把语言铁律「前移到文件最顶」并非有效手段——① 官方「重要指令放最前」只对 SKILL.md 背书,CLAUDE.md 侧无此明文;② 实测铁律已在用户可维护区第一句,可移动空间 ≤2–4 行,增益近乎为零。材料里引用「查询放末尾 +30%」作为「前移」依据是方向性误用(那条官方发现支持把高优先查询放**末尾**,不是开头)。

---

## 4. 可用机制核查(逐一,带官方来源)

> 每条标明「能做什么 / 不能做什么」,未证实项明确标出。

### 4-1 output-style(系统提示层)

- **能做**:直接修改 Claude Code 的系统提示,设定角色/语气/输出格式,**每轮响应都生效**并触发遵守提醒;自定义指令追加到系统提示末尾;设 `keep-coding-instructions: true` 可在保留内置编码能力的同时只改「怎么说」;compaction 后系统提示「保持不变、不重注入」,规则跨压缩稳定存活。来源:<https://code.claude.com/docs/en/output-styles>
- **不能做**:① 同一时刻只能启用一个,无法与内置 Explanatory/Learning 叠加;② 仍是提示级软约束,不保证严格遵循,**不能确定性禁绝韩日文**;③ 官方明示「output-style 不锁语言,只调语气/角色/格式」——语言锁应用 `language` 键。
- **未证实项**:与 CLAUDE.md 指令冲突时谁优先,官方**未文档化**;`/output-style:new` 子命令官方无说明(独立 `/output-style` 命令已于 v2.1.91 移除);中文文件名/取值能否被 `/config` 正确识别,官方未说,须实测。

### 4-2 hooks(运行时层)

- **能做**:① `UserPromptSubmit` 钩子可经 `hookSpecificOutput.additionalContext`(exit 0 打印 JSON)在模型生成**前**每轮注入一段可见提醒;② `Stop` 钩子可返回 `{"decision":"block","reason":...}` 阻止结束、逼模型重跑整轮。来源:<https://code.claude.com/docs/en/hooks>
- **不能做(关键)**:**没有任何钩子能读取或重写助手已生成的回复正文**。可改写内容的事件只有 PreToolUse/PermissionRequest 的工具入参、PostToolUse 的工具结果,全不碰助手文本;`MessageDisplay` 的 `displayContent` 只改屏幕显示,官方原文「the transcript and what Claude sees keep the original」——不改存档、不改 Claude 所见,用它「遮丑」会造成屏显与记忆/交接文档不一致。
- **限制**:连续 block 达 8 次后框架强制结束本轮(防死循环);Stop 钩子要读本轮正文须自行解析 `transcript_path` 指向的 .jsonl;退出码契约二选一(exit 0+JSON 或 exit 2,不可混用)。
- **未证实项**:多个 Stop 钩子同时 block 如何合并、8 次计数是全局还是按钩子,官方未文档化(本机 Stop 已挂 persistent-mode + code-simplifier,新增第三个 block 钩子的行为不可预测,落地前须实测)。

### 4-3 memory / CLAUDE.md(用户消息层)

- **能做**:四层级(管控/用户/项目/本地)拼接载入,全量进上下文;`@import`(最多 4 跳)、`.claude/rules/`(可按路径作用域化按需载入)、`claudeMdExcludes` 排除等。来源:<https://code.claude.com/docs/en/memory>
- **不能做**:CLAUDE.md 是「系统提示之后的一条用户消息」,官方明示「**no guarantee of strict compliance(不保证严格遵循)**」;要硬强制行为须用钩子,要系统提示级语言约束须用 output-style 或 `--append-system-prompt`。
- **要点**:四层是「拼接」非「覆盖」,矛盾时官方说会「任意选一条(pick one arbitrarily)」;`@import` **不省 context**(启动时全量展开);官方建议单文件 <200 行(越长遵循越差);「重要指令前置」只对 SKILL.md 成立,对 CLAUDE.md **无背书**。

### 4-4 settings(框架层)

- **能做**:`language` 键是官方语言锁——「Claude will respond in this language by default」,同时驱动文本响应/语音听写/会话标题语言;接受语言名或 BCP 47 代码。优先级:管控 > 命令行 > 本地 > 项目 > 用户。来源:<https://code.claude.com/docs/en/settings>、<https://code.claude.com/docs/en/voice-dictation>
- **不能做**:`outputStyle` 键不锁语言(只调语气/角色/格式);无独立 locale/i18n 体系,`language` 是唯一国际化控制点;它只锁基础语言,**管不了术语翻译**。
- **本机实况**:项目级已设 `"language": "zh-CN"`(2-1),基础中文已生效。
- **未证实项**:官方示例值仅 japanese/spanish/french,`chinese`/`zh` 取值未逐字示例(本机用的是 BCP 47 的 `zh-CN`,经实测基础中文已工作,可视为该取值有效的实证)。

### 4-5 提示工程机制(诊断依据,非配置项)

- 已验证:context rot(上下文越长召回越差)、注意力预算有限、查询放末尾 +30%、角色约束放系统提示「单句即有效」、正例优于否定例、风格镜像、向分布收敛、Opus 4.8「字面化、不自动外推作用域」、新模型对激进措辞(MUST/全大写)可能过度触发。来源:<https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/long-context-tips>、<https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-4-8>、<https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents>
- 未证实:「中英语言切换镜像」无官方专门条目(标记为未证实,属外推)。

---

## 5. 方案对比矩阵

| 方案 | 机制(注入层) | 做法 | 开销 | 可逆性 | 对黑话有效性 | 主要风险 | 对抗核验结论 |
|------|--------------|------|------|--------|-------------|---------|-------------|
| ① settings.json `language` 键(基线,**已部署**) | 框架层硬旋钮 | 项目 settings.json 第147行已有 `"language":"zh-CN"` | 零 token | 删键即回滚 | 仅锁基础语言,**管不了术语翻译** | 取值 `chinese`/`zh` 官方示例未列(`zh-CN` 属 BCP 47,经实测基础中文已生效) | 最省事的语言基线,但对「在说中文却满嘴黑话」无效——正是当前症结 |
| ② 自定义 output-style「清晰中文」 | 系统提示层 | 新建 `~/.claude/output-styles/`,`keep-coding-instructions:true`,正文放铁律+白名单+harness 术语词表+正例 | 低(约300–600 token,缓存摊薄;compaction 不重注入) | 完全可逆(删文件/切回 Default) | 中等且需实测;承载术语词表+格式是其不可替代价值 | 同时只能启用一个;仍是软约束;中文文件名兼容性需实测;keep-coding-instructions 漏设丢编码能力 | 机制成立但**主次勿颠倒**:不是语言锁头号手段(那是 language 键),价值在 language 做不到的术语翻译+格式 |
| ③ hook 运行时语言提醒 | 运行时层 | UserPromptSubmit 每轮注入一行提醒;可选 Stop 钩子按 Unicode 区间检测韩日文→block 重跑 | 事前几乎零;事后命中则整轮重跑(token 翻倍) | 极易(删 hook group) | 韩日文:接近硬强制(有8次封顶逃逸);黑话:测不准(易误伤白名单)只能软提醒 | **任何 hook 读不到也改不了已生成正文**;Stop 自检须扫全部 assistant 段+剥代码块否则自损;与现有 Stop 钩子 block 竞争未验证 | 机制声明诚实;作兜底层可行,须先修检测漏洞、先上 language+output-style |
| ④ CLAUDE.md §Language 段重构 | 用户消息层 | 词表从量化交易换成 harness 正例、消矛盾、显式作用域、精简 | 极低(净减 token) | 完全可逆(备份回滚;注意 --force 可能 clobber) | 低-中(单独使用) | 官方明示「无严格遵循保证」;不碰污染源会被很快稀释;是三层里最弱层 | 「重定位」近乎 no-op(铁律已在用户区最前);唯一真价值=术语词表领域重定向;query-at-end+30% 被误用作前移依据 |
| ⑤ 分层组合(**推荐**) | 四层纵深 | ②+④+可选③+清洗存储+消矛盾 | 低到中 | 逐层独立回滚 | 强(概率性降发生率,非确定性消除) | 全为软约束;MEMORY.md 非 git 删改不可逆须先备份;四层措辞须同向不矛盾 | 可行、机制基本对;**据实测重排**:消矛盾+清洗存储优先级应高于材料原排序,language 键已就位无需再做 |

---

## 6. 推荐方案:分层纵深防御(据实测重排优先级)

核心判断:任何**单点**方案都有天花板——output-style 治不了周边上下文持续镜像黑话的源头;CLAUDE.md 是软约束又被稀释;钩子改不了已生成文本;一次性清洗会被回写污染。所以采用四层正交分工(每层堵不同根因),但**与原始材料的排序不同**,本报告据本机实测做两处重排:

1. **框架层语言锁:跳过,已就位**。`language: zh-CN` 已在项目 settings 部署、基础中文已生效。材料把「先上 language 键」列为头号动作,实测已无需做;且它管不了术语翻译,把它当主力会误判形势。
2. **把「清洗自动载入存储」和「消跨文件矛盾」提到最高优先级**。理由:既然语言锁已开、模型确实在说中文,那么残余的黑话混杂主要由「上下文风格镜像」(角度 B)和「词表错配」(角度 A)驱动——前者的污染源就是自动载入的记忆/交接文档,后者的承载位就是 §Language 词表。这两处是当前最高、最确定的杠杆。

### 推荐执行顺序(均为 GATE 项,改 `~/.claude/` 前须用户批准)

**第 1 步(最高杠杆,最确定):消除跨文件矛盾。**
把项目 `CLAUDE.md` 第 90 行、`CLAUDE.local.md` 第 23 行的「仅里程碑用中文」改成完整重述「所有响应均用简体中文(里程碑亦然)」。这是成本最低、确定性最高的修复——因为这两个文件加载更晚、实际胜出,当前的「窄化」很可能是失效主因之一。

**第 2 步(最高杠杆,治本):清洗自动载入存储 + 加写入门控。**
备份后把 `MEMORY.md` / `LANES.md` / 当前活跃 handoff 文档改写成清楚中文,让「事实上的示范范例」从反面转正面;给「写记忆/写 handoff」这一步加写入期检查(可选 Stop 钩子做黑话密度自检),切断「黑话回写→下轮镜像→再写黑话」的闭环。注意:`MEMORY.md` 非 git 管理、删改不可逆,**必须先备份**。

**第 3 步(次高杠杆,承载术语词表):新建用户级 output-style「清晰中文」。**
新建 `~/.claude/output-styles/chinese-clear.md`(建议用 ASCII 文件名避免兼容问题,frontmatter `name` 写中文),`keep-coding-instructions: true`,正文放:① 铁律「始终用简体中文」;② 英文白名单(代码标识符/路径/命令/专名/ticker/数学符号/行业缩写);③ harness 领域术语小词表(escape-hatch→应急绕过通道、nit-loop→挑刺循环、advisory→仅建议-非阻断、stale→失效、retire→退役、cherry-pick→摘取、carve-out→豁免条款、fan-out→扇出);④「先中文后括号附英文(仅首次)」格式;⑤ 3–5 个正例对照;⑥ 显式作用域声明。这一层承载 `language` 键做不到的术语翻译 + 格式,是 output-style 的不可替代价值(**不是**当语言锁用)。

**第 4 步(同上,内容修复):重构 §Language 词表。**
把 `~/.claude/CLAUDE.md` 的量化交易词表/反例换成 harness 领域正例(与第 3 步词表保持同向),精简冗长禁止表,显式声明作用域。**不做「前移」**(已无空间且无官方背书)。注意 USER:START 区可能被 `--force` 覆盖,改前备份并在 auto-memory `feedback_terminology_handoff.md` 回引。

**第 5 步(可选兜底):运行时钩子。**
UserPromptSubmit 钩子(与现有 keyword-detector 并存)每轮注入一行清楚中文提醒;若要硬挡韩日文,加 Stop 钩子按 Unicode 区间(谚文/平假名/片假名)检测→block 重跑——但须先扫描本轮全部 assistant 文本段、先剥离代码块(否则会与「错误原文照引」规则自损式死循环),并先实测多 Stop 钩子的 block 合并行为。黑话**只做提醒不做 block**(易误伤白名单)。

**为什么是组合而非单点**:四个根因(词表错配 / 上下文镜像 / 跨文件矛盾 / 运行时缺失)被分别命中,且语言约束同时存在于系统提示、用户消息、运行时、存储四处,任一处被稀释时其余仍兜底。但必须诚实:全部为软约束,**无法确定性消除**黑话,净效果取决于「正确规则」与「被污染上下文」谁压过谁,因此**每步都须开新会话、用第 7 节指标实测裁定**,不能凭机制断言已生效。

---

## 7. 可度量的「说人话」标准

> 一份可检验的量化清单——既是「方案是否真生效」的裁定依据,也可部分写成脚本机械判定。

对一段输出(排除代码块、行内代码、错误原文引用、PR 正文)逐条核验:

| 维度 | 量化阈值 | 检测方式 |
|------|---------|---------|
| 韩文/日文字符 | 必须 = 0(硬性) | Unicode 区间:谚文 U+AC00–U+D7AF、平假名 U+3040–U+309F、片假名 U+30A0–U+30FF |
| 每 100 汉字内「未翻译且有中文对应的英文术语」 | ≤ 2 | 统计夹在中文里的拉丁词,扣除白名单 |
| 黑名单核心词出现 | = 0 | deploy/baseline/validate/scope/advisory/stale/retire/cherry-pick/carve-out/fan-out/escape-hatch/nit-loop 等 |
| 首次出现的非白名单英文术语 | 必须「先中文后括号附英文」 | 该术语左侧应有中文释义,后续只用中文 |
| 技术叙述单句长度 | 建议 6–60 字 | 避免电报式极简(简洁模式不豁免翻译展开) |
| 作用域声明 | 规则文本含「所有响应/所有领域/每段/含 caveman」 | 对治 Opus 4.8 不自动外推 |

**可钩子化程度**:前三行(韩日文字符、黑名单词、白名单外拉丁词密度)可写成一个读 `transcript_path` 的脚本机械判定;「先中文后附英文」「句长」属语义层,适合人工抽查或 Haiku 顾问式判定,**不宜做成阻断**(易误伤白名单标识符)。**验收方式**:对清洗前/后的 `MEMORY.md` 与 handoff 跑同一脚本,比较前三项数值——这既是验收门,也是「方案是否真生效」的唯一裁定依据。

---

## 8. 风险与回滚(对齐 #259 用户级变更治理)

> 所有改 `~/.claude/` 的动作属「仓库外变更」,必须按 #259 治理流程:开 Issue 记录命令清单 + diff 摘要 + 验证步骤,变更前备份。

**备份(每步改前必做)**:
- `CC="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"; cp "$CC/settings.json" "$CC/settings.json.backup-pre-lang"`
- `cp "$CC/CLAUDE.md" "$CC/CLAUDE.md.backup-pre-lang-refactor"`
- `MEMORY.md` 非 git、删改不可逆,改前务必单独备份其目录(`~/.claude/projects/D--Mercury-Mercury/memory/`)。

**命令清单(GATE 后执行)**:① `mkdir -p ~/.claude/output-styles` + 写 chinese-clear.md;② 编辑用户 CLAUDE.md §Language;③ git 改项目 CLAUDE.md / CLAUDE.local.md(走 PR);④ 备份后清洗 MEMORY.md/LANES.md/handoff;⑤ 可选:写钩子脚本 + 在 settings.json UserPromptSubmit/Stop 数组追加 group。

**验证清单(全部通过才算成功)**:
1. `python -c "import json,os; json.load(open(os.path.expandvars('$CC/settings.json')))"` JSON 合法;
2. 每个新钩子脚本在合成 stdin 下 exit 0、输出 JSON 合法;
3. 开新会话(改 output-style/CLAUDE.md 须 `/clear` 或新会话才生效)真实触发一轮观察无回归;
4. 按第 7 节指标对清洗前/后 MEMORY.md 与 handoff 做数值对比;
5. 故意写一句日文测 Stop 钩子是否 block(若启用)。

**回滚通道**:
- output-style:删 `~/.claude/output-styles/chinese-clear.md` 或把 settings 的 `outputStyle` 改回 Default,`/clear` 生效;
- settings/钩子:`mv` 回 `.backup-pre-lang`;
- 项目 CLAUDE.md/CLAUDE.local.md:`git revert`;
- 用户 CLAUDE.md:恢复 `.backup-pre-lang-refactor`(注意 `omc-setup --global --force` 可能 clobber USER 区,备份是唯一保险);
- MEMORY.md/LANES.md:从备份恢复(MEMORY.md 无 git 回滚通道)。

**残留风险**:① 全部为软约束,韩日文仍可能偶漏(Stop 钩子有 8 次封顶逃逸);② 不持续维持写入门控则清洗会被回写污染;③ 多 Stop 钩子 block 竞争未验证,启用前须实测;④ 简洁模式与翻译展开的张力靠声明缓解、未根除。

---

## 附:本报告对输入材料的主要纠偏(诚实记录)

1. **`language` 键已部署**:材料断言「user/project 三处全未设」,实测项目级第 147 行已有 `"language": "zh-CN"`——这把「先上 language 键」从头号动作降为「已完成、无需做」,并实证了「光锁语言治不了黑话」。
2. **「前移铁律」近乎无效**:铁律已在用户可维护区第一句;材料引「查询放末尾 +30%」作前移依据属方向性误用。
3. **跨文件「矛盾」性质**:更准确说是「项目文件把中文窄化给里程碑」+「环绕上下文以英文为主导致风格镜像」,而非纯优先级覆盖冲突;但消除该窄化仍是最高确定收益的一步。
4. **output-style 定位**:是术语词表/格式的承载层,不是语言锁——后者归 `language` 键。
5. **「显著压制黑话」属效果夸大**:官方对遵循度提升无量化,语言镜像维度未证实;诚实表述应为「概率性降发生率,须实测」。