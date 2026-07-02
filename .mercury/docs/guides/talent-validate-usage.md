# talent-validate 使用指南

> SoT 天赋平衡混合验证器(Mercury Dynamic Workflow)。确定性结构/tag/规则检查(纯 JS 零 LLM)+ Haiku 语义 advisory + 共享 tag 组合扫描 + 双 Sonnet 对抗(Optimizer-vs-Defender)。**只读 SoT,不改 SoT 仓**。设计/实跑验证见路线图 `.mercury/docs/research/sot-workflow-optimization-roadmap-2026-06.md` §1/§9。

## 何时用

设计一张新天赋、或打磨一张已录天赋时,在锁定前验证它:抓非法 schema/tag、悬空规则引用、史诗供给越界、危险 tag 组合循环、高价值滥用序列。**MVP 不含数值功率评分**(设计库 叙述层无 power 字段;待补,见路线图 §5)。

## 前置:起 设计库只读 API

talent-validate 需要 SoT 设计库 API 取 tag 注册表 / 规则表 / 同职业天赋。在单独终端起:

```bash
bash scripts/sot-codex-serve.sh    # http://127.0.0.1:8000, DB 隔离 Mercury tmp, 零写 SoT 仓
```

(NAS `sot.fyc-space.uk` 待 CF token,暂用本地。)

## 两种调用方式

### A. 验证 设计库 已录天赋 —— 传 `talent_id`

```
/talent-validate { "talent_id": "ss_jianqie", "class_id": "ss" }
```

### B. 验证新设计草稿 —— 传 `talent_draft_json`(产出新天赋主用法)

不必先录 设计库,直接喂草稿 JSON:

```
/talent-validate { "talent_draft_json": {
    "id": "ss_xxx", "class_id": "ss", "name": "天赋名",
    "damage_type": "物理", "rarity": "史诗", "status": "草稿",
    "trigger": "触发时机", "effect": "效果叙述",
    "rules": "必暴遵循 R1.1。", "tags": ["处决","攻击","剑气"]
  }, "class_id": "ss" }
```

> 也可用 Workflow 工具直接跑:`Workflow({ scriptPath: ".claude/workflows/talent-validate.js", args: {...} })`。后台跑,`/workflows` 看进度。单张约 20-22 agents / ~800K token / ~9 分钟。

## 草稿字段(L1 schema 必填)

`id` `class_id` `name` `damage_type` `rarity` `status` `trigger` `effect` `tags`(`rules` 强烈建议):

- `rarity` ∈ 普通 / 稀有 / 史诗 / 传奇
- `damage_type` ∈ 无 / 物理 / 魔法 / 圣 / 纯伤害 / 混合
- `status` ∈ 草稿 / 待优化 / 锁定 / 废弃
- `tags` 每个必须 ∈ 设计库 tag 注册表(12 个):**mech** 剑气/印记/处决/奥义/招架/反应 · **content** 区域/位移/攻击/自动/战棋/资源
- `rules` 里引用的规则码(R1.1 等)会被核查;引用不存在的码 → 悬空 warning

## verdict 解读

| verdict | 含义 |
|---|---|
| **pass** | 无硬问题 |
| **revise** | L1 warning(悬空引用 / 史诗池既有超限) 或 L2 危险组合(loop/amplifier@high\|medium) 或 stage 失败 |
| **reject** | L1 error(schema/枚举/tag 非法 / **新增史诗导致供给越界**) 或 L3 确认 exploit(对抗无法被现有锁定规则中和) |
| **blocked** | 数据/服务问题(非天赋本身);设计库 服务没起 / dataDir 缺数据 / 未传天赋 |

结果分层:
- **L1 deterministic** = 纯 JS 零 LLM,**可信**(schema/枚举/tag 合法/悬空引用/R6.6 史诗供给上限)。
- **L1 semantic** = Haiku,**仅 advisory 不驱动 verdict**(LLM 不做定量裁决)。
- **L2** = 与候选共享 tag 的同职业天赋组合 triage。
- **L3** = Optimizer 构滥用序列 → Defender 用现有**锁定**规则反驳;`neutralized:false` = 确认 exploit。

R6.6 史诗供给(每职业史诗 ≤6)归因区分:**已存史诗**池超限 → warning(非候选导致);**新增史诗**(草稿/库内非史诗升级)推高池越界 → error → reject(该牌导致)。

## 数据新鲜度

设计库 数据首次拉取后缓存在 `.mercury/tmp/codex-fixtures/`(gitignored 快照)。**在 设计库 改/加天赋后**,删该目录强制下次验证重拉最新(需服务跑着):

```bash
rm -rf .mercury/tmp/codex-fixtures    # 下次 talent-validate 自动重拉
```

或传 `args.dataDir` 指向一个新空目录强制全量重拉。

## 范围与护栏

- **不含数值功率评分**(待 设计库 补数值字段 + `/api/export/godot`,路线图 §5 决策点 1 已定走方案 a)。
- 只读 SoT(GET API + 不改仓);设计库 DB 隔离 Mercury tmp。
- #385 context 护栏:lean dispatch(传路径+任务) / fan-out cap + log / Haiku 注入 ≤50K。
