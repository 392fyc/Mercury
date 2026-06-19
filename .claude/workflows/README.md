# `.claude/workflows/` — Mercury Dynamic Workflow 模板库

> 立项: Issue [#479](https://github.com/392fyc/Mercury/issues/479)(umbrella [#478](https://github.com/392fyc/Mercury/issues/478) harness 现代化 P0)
> 护栏: [`.mercury/docs/research/context-strategy-2026-05.md`](../../.mercury/docs/research/context-strategy-2026-05.md)(#385,生效中)
> 调查: [`.mercury/docs/research/harness-modernization-survey-2026-06.md`](../../.mercury/docs/research/harness-modernization-survey-2026-06.md)

Dynamic Workflow 是 Claude Code 原生的「**确定性多 agent 编排**」原语:一段 JS 脚本把扇出/流水线/裁决写进代码,runtime 在后台跑数十到数百个 subagent,中间结果留在脚本变量里,主会话只收最终结果。本目录是 Mercury 把这一能力**固化进 repo** 的宿主 —— 项目级 `.claude/workflows/` 随 repo 分发,clone 即得。

**环境要求**: Claude Code **v2.1.154+**(Dynamic Workflows + `args` 参数化);monorepo 多 `.claude/` 目录的「就近保存」行为另需 v2.1.178+。可用性按 `/config` 的 Dynamic workflows 开关;关闭后本目录模板与 `ultracode` 关键词均失效。来源: [code.claude.com/docs/en/workflows](https://code.claude.com/docs/en/workflows)。

---

## 何时用 Workflow(而非 subagent / skill / team)

| | Subagents | Skills | Agent Teams | **Workflows** |
|---|---|---|---|---|
| 谁持有计划 | Claude 逐轮决定 | Claude 跟着提示 | lead agent 逐轮 | **脚本本身** |
| 中间结果在哪 | Claude context | Claude context | 共享任务列表 | **脚本变量** |
| 可复用的是 | worker 定义 | 指令 | team 定义 | **编排本身** |
| 规模 | 每轮几个 | 同左 | 少量长跑 peer | **每次几十到几百 agent** |

**判据**:当任务「一个会话的 context 装不下」或「值得把编排沉淀成可重跑脚本」时升级到 Workflow。典型:repo 级 bug 扫描、500 文件迁移、需多源交叉核查的研究、需多角度起草再裁决的硬计划。详见 `.claude/agents/main.md` §四原语选型矩阵(#480)。

## 触发方式

1. **单任务 opt-in**:prompt 里含关键词 `ultracode`(v2.1.160 前是 `workflow`),或自然语言「use a workflow / 用 workflow 跑」。
2. **会话持久**:`/effort ultracode` = xhigh + 每个实质任务自动编排 Workflow。会话级,新会话重置;`/effort high` 退回。
3. **复用已存模板**:`/<name>`(本目录每个脚本的 `meta.name` 即命令名)。bundled 的 `/deep-research` 同理。

## 保存与复用约定

- **手写模板**(如本目录 4 个):文件名 = `meta.name`,放 `.claude/workflows/<name>.js` → 直接 `/<name>` 调用。
- **从运行保存**:跑出满意的一次后 `/workflows` → 选中 → 按 `s` → Tab 切换保存位置(`.claude/workflows/` 项目级随 repo / `~/.claude/workflows/` 个人级)→ Enter。
- **同名优先级**:项目级 > 个人级。
- **传参**:`/<name>` 后跟参数,脚本里读全局 `args`(运行时把字符串/数组/对象**原样**传入,无需解析)。**注意**:`args` 的具体**形状是每个模板自定义的**(见各脚本顶部常量),不是每个模板都接受三种形状 —— 例如 research/plan-review/migration 接受「字符串 或 `{字段…}` 对象」,audit 接受对象;传入不符合该模板契约的形状(如给只认字符串/对象的模板传顶层数组)会落入缺参分支并打印用法提示。每个模板都对缺省/不符做了优雅降级(返回 error + hint,不抛错)。
- **后台 + 监控**:Workflow 默认后台跑,会话不阻塞;`/workflows` 看每 phase 的 agent 数 / token / 耗时,可暂停(`p`)/停止(`x`)/重启 agent(`r`);同会话内可 resume(已完成 agent 返缓存结果),退出 Claude Code 则下次重跑。

---

## Mercury 模板清单

| 命令 | 模式 | 用途 |
|---|---|---|
| `/mercury-codebase-audit` | fan-out + adversarial-verify | repo 级多维度审计(security/correctness/resource),每条 finding 经对抗式核查存活才上报 |
| `/mercury-multi-source-research` | multi-modal sweep + cross-check | 多角度 web 研究 + 逐条 claim 独立交叉核查 + 引用合成,替代 autoresearch 串行单 context;UNVERIFIED 显式标注 |
| `/mercury-adversarial-plan-review` | judge-panel | N 个独立角度起草计划 → 对抗评审团打分 → 综合优胜方案 + 嫁接亚军亮点;只产计划,实现仍回 Main→dev |
| `/mercury-large-migration` | loop-until-done | 数十到数百文件机械迁移,按文件归属并行改造(每 agent 独占一文件,工作树原地编辑)+ 逐文件验证 + 循环至收敛;edits 不 commit,由 operator 合并提交 |

各模板的 `args` 入参见脚本顶部常量(如 `maxAngles` / `batchCap` / `contextPaths`)。

### 六大编排模式 ↔ 模板

fan-out(`codebase-audit`)· adversarial-verify(`codebase-audit` 的 Verify / `adversarial-plan-review` 的 Judge)· judge-panel/tournament(`adversarial-plan-review`)· generate-and-filter(`multi-source-research` 的 sweep→crosscheck)· classify-and-act(可组合)· loop-until-done(`large-migration`)。

---

## #385 护栏(所有模板必须遵守 — 改模板时核对)

大规模 fan-out **不等于**「1M ctx 随便塞」。每个模板内嵌以下硬约束(脚本顶部注释 + 实现):

1. **不 pre-inject 全量文档**:agent 拿到的是**路径 + 任务**,自己去 Read/Grep,绝不把整文件内容塞进 prompt。bulk injection 永久抢占 cache slot,即使物理装得下也浪费(#385 §4.5)。
2. **fan-out 设上限 + 不静默截断**:每个模板有显式 cap(`FAN_OUT_CAP`/`ANGLE_CAP`/`BATCH_CAP`/`MAX_ROUNDS`),被丢弃的工作量一律 `log()` 出来(静默截断会被误读成「全覆盖」)。
3. **Haiku 路径 50K 硬顶**:若某 stage 路由到 haiku-tier(`game-researcher` 等),注入切片必须 ≤50K token(Haiku 4.5 是 200K ctx 硬 cliff)。模板默认继承会话 model 且只传路径,远低于此线。
4. **Codex/GPT-5.5 路径 272K cliff**:越线一次整 session 计费翻倍 —— Workflow agent 跑 Claude 不直接受此约束,但若模板被改造去调 Codex 路径需重新评估。
5. runtime 硬上限:≤16 并发 / 1000 agent per run(runaway 兜底)。模板自身额外做**总量自限**:`codebase-audit` 与 `large-migration` 按 cap 组合算最坏 agent 数并钳制(`AGENT_BUDGET=800` 留余量),即使 operator 把 cap 调到上限也不会撞 1000 而被中途截断(migration 钳 `MAX_ROUNDS`、audit 钳 `MAX_FINDINGS`,触发时 `log()`)。

## Mercury 治理约束

- **dual-verify 仍是合并门**:Workflow 产出的代码改动,提交前照样跑 `/dual-verify` + 走 PR 到 develop(Workflow 不绕过任何 hook 回归)。
- **成本**:一次 run 可能比对话方式多用数倍 token。先在小切片上试(单目录 / 窄问题)估开销;`/workflows` 实时看 token。
- **model**:每个 agent 用会话 model,除非脚本 `opts.model` 显式路由。大 run 前先核对 `/model`。
- **不重写既有 skill**:autoresearch/dual-verify 等暂不全量改写为 Workflow(large effort);先用本模板库验证价值再渐进迁移(调查文档 §不建议吸收 #7)。
