# talent-validate 使用指南

> SoT 天赋平衡混合验证器(Mercury Dynamic Workflow)。确定性结构/tag/规则检查(纯 JS 零 LLM)+ Haiku 语义 advisory + 共享 tag 组合扫描 + 双 Sonnet 对抗(Optimizer-vs-Defender)。**只读 SoT,不改 SoT 仓**。设计/实跑验证见路线图 `.mercury/docs/research/sot-workflow-optimization-roadmap-2026-06.md` §1/§9。

## 何时用

设计一张新天赋、或打磨一张已录天赋时,在锁定前验证它:抓非法 schema/tag、悬空规则引用、史诗供给越界、危险 tag 组合循环、高价值滥用序列。**MVP 不含数值功率评分**(设计库 叙述层无 power 字段;待补,见路线图 §5)。

## 前置:起 设计库只读 API

talent-validate 需要 SoT 设计库 API 取 tag 注册表 / 规则表 / 同职业天赋。在单独终端起:

```bash
SOT_CODEX_DIR=/path/to/SoT-fyc-space bash scripts/sot-codex-serve.sh   # http://127.0.0.1:8000, DB 隔离 Mercury tmp, 零写 SoT 仓
```

(`SOT_CODEX_DIR` 必填,指向 SoT 设计库仓库检出位置;无默认值,防止换环境时静默指错目录。)

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
>
> 复跑注意:默认复用 `.mercury/tmp/codex-fixtures/` 里的既有数据快照;若设计库在两次运行之间有更新,args 里加 `"refresh": true` 强制重新抓取 API,避免基于陈旧快照裁决。

### C. 填空生成(L4 gap-fill)—— 传 `gapfill`

不给候选,让工具**主动提示设计空白并起草一张**:把已录语料分类到 trigger×effect 覆盖矩阵上,选出最优空格(行、列都已有天赋占据、只是组合未被探索的格子优先),单 Sonnet 生成 **1 张** schema 合法草稿(按 roadmap 明确**不做批量生成**,备选空格只列出不生成),经 embedding 冗余筛(与任一已录天赋 cos > 0.85 判冗余)后,原样回灌 L1-L3 验证:

```
/talent-validate { "gapfill": true, "class_id": "ss" }
```

- **embedding 配置**:默认模型 `text-embedding-3-small`。endpoint 按序取 args `embed_base_url` → env `AZURE_OPENAI_EMBED_BASE_URL` → env `OPENAI_BASE_URL`;key 由执行 agent 从 env `AZURE_OPENAI_API_KEY`(或 `OPENAI_API_KEY`)读取,不入 args。**配置缺失或调用失败 fail-closed**:冗余度标 UNVERIFIED 并强制 verdict ≥ revise,绝不静默当作已筛。
- **矩阵轴**:内置默认轴(见脚本 `DEFAULT_TRIGGER_AXES` / `DEFAULT_EFFECT_AXES`),可用 args `triggerAxes` / `effectAxes` 覆盖(每轴 ≤12 个唯一非空标签、单标签 ≤24 字符、不含 `|`;兜底桶「其他」缺失时自动补上);分类结果与语料 id 严格对账——语料外/重复/轴外条目丢弃并告警,覆盖有缺口时 verdict 下限 revise(防幻影空格)。
- **产出解读**:报告多出 `gapfill`(目标空格 + 前 3 空格 + 冗余度)与 `candidateDraft`(完整草稿 JSON,由你决定是否录入设计库);`verdict` 是对生成草稿的 L1-L3 判定,冗余候选的 verdict 下限为 revise。矩阵饱和(无空格可填)时返回 `blocked` 并说明。

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
