# Upstream dependency drift routine

> 立项: Issue [#508](https://github.com/392fyc/Mercury/issues/508)(side-bug lane)。
> 背景:`.mercury/state/upstream-manifest.json` 自导入(2026-04~05)起所有条目 `last_drift_check=null`,周期检查从未跑过;本机制把「上游依赖漂移检查」制度化,避免 ①组件过期产生局部问题 ②组件已失效却仍错误使用。

Mercury 挂载/借鉴了若干外部组件(cherry-pick 的 skill/agent 文件、version-pin 的 adapter、plugin 依赖、pinned 模型 ID)。这些上游会演进、会失效。本文档定义**两层周期检查机制 + 漂移裁决流程**。

## 两层机制

| | Tier 1 — 机械漂移 | Tier 2 — LLM staleness 审计 |
|---|---|---|
| 工具 | `scripts/upstream-drift-check.sh` | `/mercury-staleness-audit`(`.claude/workflows/mercury-staleness-audit.js`)|
| 触发 | `.github/workflows/upstream-drift.yml` 月度 cron(+ `workflow_dispatch`)| 人工周期例行(建议季度,或 Tier-1 告警后)|
| 判定 | 确定性:比较 cherry-pick artifact 的上游 blob SHA(import vs HEAD)→ `CLEAN/CHANGED/UPSTREAM_GONE/SKIP` | LLM + 对抗验证:判「落后多少」「组件是否已失效/disabled」「上游是否 archived」「破坏性变更 Mercury 是否真触达」→ `ACTIVE-RISK/ACTION-NEEDED/ACCEPTABLE-DRIFT/DORMANT-OK/NOT-STALE/UNVERIFIED` |
| 抓得到 | cherry-pick 文件内容漂移、上游文件被删 | 版本落后幅度、失效/禁用组件仍被引用、上游 archived、纯 transitive 依赖(blob 模型抓不到)|
| 成本 | 零 LLM,~秒级,gh api | 一次 run 数十 agent + web,~分钟级 |
| 产出 | 有漂移→ find-or-create 单一 tracking issue(label `upstream-drift`)贴报告 | 分类报告(不开 issue),喂给 operator 裁决 |

**为什么要两层**:Tier-1 只比对 blob SHA —— 它知道「caveman SKILL.md 上游变了」,但不知道「playwright 0.0.75 落后 latest 几个版本」「Fable5 已被禁用」「某 npm 依赖已 archived」。后者需要 web + 判断,是 Tier-2 的职责。Tier-1 抓 case ①(过期),Tier-2 兼抓 case ①②(过期 + 失效误用)。

## last_drift_check 语义(重要)

- `last_drift_check` = **「上次人工复核日期」**,由本地 `bash scripts/upstream-drift-check.sh --write-back` 回写、经 PR 提交。它**独立于**当次 CLEAN/CHANGED 状态(状态是瞬时的,重跑即得)。
- **GHA 只读、绝不回写**:GitHub Actions 不能 push develop(Mercury PR-only 规则)。月度 GHA 是「自动告警」(检测→开/更新 tracking issue),不动 manifest。manifest 的时间戳只在人工复核 + PR 时前进。
- 这样分工:`upstream-drift` tracking issue = 「机器说有漂移」;`last_drift_check` = 「人看过了,日期为证」。

## 漂移裁决流程(收到 CHANGED / Tier-2 ACTION-NEEDED 时)

逐条三选一:

1. **pull-update** —— 上游有实质改进/修复值得拉:按 cherry-pick 协议更新本地副本 + 更新 manifest `upstream_sha_at_import`,走 dual-verify + PR。
2. **re-pin** —— version-pin adapter 落后且值得升:更新 pin(launch 包/pyproject/Cargo)+ 重新 provision + 更新 `UPSTREAM.md` + manifest SHA,走 dual-verify + PR。
3. **accept-as-owned** —— 上游变更是 cosmetic、不可达、或 Mercury 已自有适配(cherry-pick 协议下本地副本是 Mercury-owned,无 live-sync 义务):无需改代码,仅 `--write-back` 刷新 `last_drift_check` 记录「已复核、接受现状」。

裁决后跑 `bash scripts/upstream-drift-check.sh --write-back` + 提交刷新的 manifest;tracking issue 在所有 CHANGED 裁决完成后关闭。

## 覆盖面与边界

- manifest(因而 Tier-1)覆盖 **cherry-pick 文件 + 显式 adapter version-pin**。**纯 transitive npm/cargo 依赖**(`pnpm-lock.yaml` / `Cargo.lock` 里的间接依赖)不进 manifest —— 由 lockfile 钉版本 + Tier-2 审计(其 discover agent 会扫 lockfile/pin)兜底。
- **用户级组件**(OMC plugin、`~/.claude/scripts/` 模型 ID 等)漂移走 [#259](https://github.com/392fyc/Mercury/issues/259) user-level governance,不在本 repo PR 流程内 —— Tier-2 会标出,由用户在其全局环境执行更新。
- **Argus 基座**(pr-agent 冻结)漂移归 [#498](https://github.com/392fyc/Mercury/issues/498) fork-rebase 专项,本机制只负责「标出落后」,不负责升级。

## 首跑基线(2026-06-24 UTC,#508)

> 时间戳用 UTC(`date -u`,与月度 GHA 一致);本次回写落 `last_drift_check=2026-06-24`。

- Tier-1:21 artifacts → `CLEAN=9 · CHANGED=9 · SKIP=3`。CHANGED = obra/superpowers(systematic-debugging ×2 + subagent-driven-development ×4)、JuliusBrussee/caveman ×2、microsoft/playwright-mcp。
- Tier-2:14 items → `0 ACTIVE-RISK · 2 ACTION-NEEDED · 6 ACCEPTABLE-DRIFT · 1 DORMANT-OK · 5 NOT-STALE`。详见 #508 + side-bug user-memory。
- 9 个 CHANGED 的裁决:superpowers 上游重构(ledger 恢复模式等)→ 单独 [#509](https://github.com/392fyc/Mercury/issues/509) 追踪 back-port(P3);caveman/agency-agents → accept-as-owned(Mercury 自有改写);playwright 0.0.76 / grammy 1.44 → 低优 re-pin(机制后续监控)。本 PR 仅回写 `last_drift_check` 建立基线,不在 PR 内 reconcile 各 CHANGED(漂移现状 = 机制监控对象)。
