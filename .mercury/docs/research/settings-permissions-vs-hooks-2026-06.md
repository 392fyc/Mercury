# settings.json permissions vs PreToolUse hooks — #486 决策 ADR

> 状态: **生效中** | 制定日期: 2026-06-21(JST) | 解析: [#486](https://github.com/392fyc/Mercury/issues/486) 核心决策([#478](https://github.com/392fyc/Mercury/issues/478) harness 现代化 **P3 / S8**)。本 ADR 交付「是否下沉」的决策(=不下沉);#486 原范围的**可选实现型 follow-up**(附加 defense-in-depth permissions / 新 hook 特性试点)见 §4.2,issue 由用户裁定关闭或留作 follow-up tracker。
> 来源核验: [code.claude.com/docs/en/permissions](https://code.claude.com/docs/en/permissions)(web-verified 2026-06-21)· [code.claude.com/docs/en/hooks](https://code.claude.com/docs/en/hooks)

---

## TL;DR

**Verdict: 不下沉 push-guard/pre-commit-guard 到 settings.json `permissions` —— 那是安全 regression。保留二者为硬化 PreToolUse hook(官方推荐的可靠命令约束路径);settings.json `permissions` 仅作正交的 *附加* defense-in-depth(robust 的 file-path / domain / tool-level deny),非替换。**

#486 原始提案是把部分 push-guard/pre-commit-guard 规则「下沉到 settings.json permissions 块声明式实现(`Bash(command:rm*)`、`Agent(model:opus)`)」,认为是「优化非新能力」。实证 + 官方文档核验后裁定:**对 push-guard 这个最安全敏感的门,下沉是 net regression**;且 #486 字面引用的 `Bash(command:rm*)` 语法**官方明确会被 ignore + 启动告警**。

---

## 1. 背景

### 1.1 #486 提案
- 用 `Tool(param:value)` 通配符(`Bash(command:rm*)`、`Agent(model:opus)`)把部分 push-guard / pre-commit-guard 规则从脚本下沉到 settings.json `permissions` 块(shared `.claude/settings.json` 当前无 `permissions` 块;`.claude/settings.local.json` 有一个**个人 allow-list + `bypassPermissions` 模式**块,非 deny-based 安全层、gitignored,与本 ADR 讨论的 deny-based 门禁正交)。
- 评估 PermissionRequest 事件替代 PreToolUse Bash matcher。
- 试点 `once:true` / `continueOnBlock:true` / `asyncRewake` 等新 hook 特性。
- 自评「属优化非新能力」。

### 1.2 push-guard 现状(实证)
`.claude/hooks/push-guard.sh` = **523 行**,其 fix history 记录它**专门关闭了 ≥6 个 bypass 洞**(dual-verify 多轮 + Codex audit 发现):
1. quoted protected-branch token(`git push origin "develop"`)
2. ≥6 层 env wrapper 链(`env env env … git push origin develop`)
3. subshell / group(`( git push origin develop )`、`{ …; }`)
4. quoted env values(`A="1 2" git push origin develop`)
5. quoted `-C` paths(`git -C "C:/repo with spaces" push origin develop`)
6. break-on-first(`git push origin lane/foo && git push origin develop`)
+ parser-failure fail-closed(jq/awk 缺失不再静默 exit 0)。

实现:quote-aware awk tokenizer + 每段 env-strip + 从 `push` token 起 bash token-array 切片 + jq-required fail-closed。**这 523 行不是过度工程 —— 是逐个堵 bypass 的产物。**

---

## 2. settings.json permissions 能做什么 / 不能做什么(web-verified)

来源: [permissions doc](https://code.claude.com/docs/en/permissions)。

### 2.1 能(robust)
- **deny/ask/allow** 三档,顺序 deny→ask→allow;managed > CLI > local > project > user 优先级;任一层 deny 不可被上层 allow。
- **file-path 规则**(`Read`/`Edit`)按 gitignore 语义 canonicalize,且**适用于 Bash 里的 cat/head/tail/sed**(非任意子进程)。`Read(.env)` 稳健挡 secret 读。
- **domain 规则**(`WebFetch(domain:...)`)按 hostname 匹配,稳健。
- **tool-level**(`Bash`、`Agent(Explore)`、`mcp__*`)整工具 deny,稳健。
- **shell-operator 感知**:`Bash(safe *)` **不**授权 `safe && other`(拆 `&&`/`||`/`;`/`\|`/`&`/newline 逐段匹配);strip 固定 wrapper(`timeout`/`time`/`nice`/`nohup`/`stdbuf`/裸 `xargs`)。

### 2.2 不能(官方 fragile 警告)
- **`Bash(command:value)` 参数匹配被 ignore + 告警**:官方原文「A rule like `Bash(command:rm *)` would be bypassable by a compound command, so Claude Code **ignores it and emits a startup warning**. Use `Bash(rm *)` … instead.」→ **#486 字面 `Bash(command:rm*)` 语法无效**(`command`/`file_path`/`path`/`url` 等 canonicalized 字段不可 param-match)。
- **参数约束模式 fragile**(官方 Warning 原文,以 `curl` 为例,push 同理):
  - options 前置(`git -X push …`)、变体、redirect、**变量间接**(`URL=… && curl $URL`)、**多余空格**(`curl  http://…`)均漏 —— 此为**官方明列**。
- **strip 集是固定且封闭的**(官方明列仅 `timeout`/`time`/`nice`/`nohup`/`stdbuf`/裸 `xargs`)。**Mercury 推断**(由 strip 集封闭 + 上述 fragile 警告推得,非官方逐条列举):env-var 前缀(`A=1 cmd`)、`env` wrapper、subshell `( )` 等任意非 strip-集 wrapper 不被规范化 → push-guard §1.2 堵的 bypass 洞 1-5(变量间接已官方确认 fragile;env-wrapper/subshell/quoted 由 strip 集封闭推断)**settings.json glob 无力覆盖**。

### 2.3 官方对「可靠命令约束」的推荐 = PreToolUse hook
原文:「For more reliable URL filtering, consider: … **Use PreToolUse hooks**: implement a hook that validates … and blocks」;「To run all Bash commands … except a few you want blocked, add `Bash` to allow + register a **PreToolUse hook** that rejects those specific commands」。→ **Mercury push-guard 正是官方推荐形态**。hooks 与 permissions **互补**(官方原文):**code-2 PreToolUse hook 在 permission 规则评估*之前*停止调用**(故 hook 阻断先于 allow 规则);而 **deny/ask 规则无论 PreToolUse hook 返回 allow/ask 都*仍被评估***(deny-first 优先级保持)。即二者各自独立生效,叠加成 defense-in-depth。

---

## 3. 决策矩阵

| 决策项 | 选项 | 裁定 |
|---|---|---|
| push-guard 下沉到 `Bash(...)` glob | ❌ No | glob 不覆盖 env-wrapper/subshell/quoted/变量间接(§2.2);下沉 = 重开 push-guard 已堵的 bypass 洞 = **net regression** |
| pre-commit-guard(commit 需 review flag)下沉 | ❌ No | 该门逻辑是「检查 `.mercury/state/review-passed` 存在 + bypass-mode 判定」,非命令模式匹配;permissions 无对应原语 |
| `Bash(command:rm*)` 字面语法 | ❌ 无效 | 官方 ignore + 告警(§2.2);若要用须写 `Bash(rm *)` glob,但仍 fragile |
| settings.json `permissions` 作附加 defense-in-depth | ✅ 推荐(scoped follow-up) | robust 的 `Read` secret-deny / `WebFetch(domain)` / tool-level deny 是真增量,**附加**于 hook 不替换 |
| PermissionRequest 事件替代 PreToolUse | ⏸ Defer | 收益不明 + 现有 PreToolUse 链稳定;无明确驱动场景 |
| `once:true`/`continueOnBlock:true`/`asyncRewake` 试点 | ⏸ Defer `[UNVERIFIED]` | 本 ADR 未 web-verify 其确切语义/可用版本;研究性,无紧迫需求,留待有场景再单独核验+试点 |

---

## 4. 推荐(立即生效 + follow-up)

### 4.1 立即生效(本 ADR)
1. 本文作 canonical 决策:**push-guard/pre-commit-guard 保持 PreToolUse hook**,不下沉。未来 session 勿因「声明式更简洁」naive 重试下沉(会重开 bypass 洞)。
2. #486 CLOSED,引用本 ADR。

### 4.2 Follow-up(scoped,非本 ADR;需各自评估 + dual-verify)
1. **附加 defense-in-depth permissions 块**(可选,低风险增量):在 `.claude/settings.json` 加 `permissions.deny`,挡 hook 未覆盖的面 —— 候选:`Read(.env)`/`Read(**/*secret*)` 类 secret-file 读(robust,canonicalized);egregious 模式。**须窄域**(过宽会挡合法操作),单独 PR + dual-verify。**不动现有 hook**。
2. **新 hook 特性试点**(`once:true` 等):有具体场景时先 web-verify 语义/版本,再单独试点。
3. PermissionRequest 事件:出现明确替代收益时再评估。

---

## 5. 来源(web-verified 2026-06-21)
- [Claude Code Docs — Configure permissions](https://code.claude.com/docs/en/permissions) —— `Bash(command:...)` ignore+告警、参数约束 fragile 警告、PreToolUse hook 推荐、deny/ask/allow 优先级、shell-operator 感知、wrapper strip、file-path/domain canonicalize。
- [Claude Code Docs — Hooks](https://code.claude.com/docs/en/hooks) —— PreToolUse exit-2 先于 permission、hook 与 permission 互补。
- Mercury 内部实证:`.claude/hooks/push-guard.sh`(523 行 fix history,6+ bypass 洞)、`.claude/hooks/pre-commit-guard.sh`(review-flag 门)。
- 关联:[#385 context ADR](./context-strategy-2026-05.md) · [harness 现代化调查](./harness-modernization-survey-2026-06.md) §第三梯队(S8 permissions 条目)。
