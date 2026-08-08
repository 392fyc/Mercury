# `scripts/sot_id_map` — 引擎 ↔ 设计库 id 映射表 + 机械校验器

Ship of Theseus 由两个仓库分别描述，各有一套互不相同的 id 命名体系：

| 侧 | 仓库 | 数据位置 | id 样例 |
|---|---|---|---|
| 引擎 | `Ship_of_Theseus`（Godot 4） | `data/**/*.json`，一个实体一个文件 | `swordsman_zhanji` |
| 设计库 | `SoT-fyc-space` | `snapshots/*.json`，导出脚本产物，数组形式 | `myrmidon_zhanji` / `kensei_zhanji` |

跨组交接时这两套 id 一直靠人脑对照。本目录把这份对照关系变成**一个人工维护的数据文件**加**一个机械校验器**。

对应 Mercury Issue [#556](https://github.com/392fyc/Mercury/issues/556)；分工依据是设计库 `docs/mercury-sot-lane-management.md` §2 分工矩阵「引擎↔设计库 id 映射表」行。

## 界线：只出表，不写消费代码

映射表的**消费方归 SoT lane**。本包刻意不提供任何「按映射把 A 侧 id 翻成 B 侧 id」的运行时函数——Mercury 这边再写一份消费逻辑，映射层就会变成第二个双写真源，正是 lane 文档 §2.2 字段归属表要封杀的病。

本包也**从不写入**那两个外部仓：只读取，只报告。

## 文件

| 文件 | 作用 |
|---|---|
| `id_map.json` | **唯一真源**，人工维护。声明范围、受控原因码枚举、映射条目、显式未映射条目 |
| `sources.py` | 从两侧仓库只读提取 id 全集；解析环境变量 |
| `checker.py` | 校验逻辑，产出 finding 列表 |
| `__main__.py` | 命令行入口 |
| `test_smoke.py` | 冒烟测试，重点锁「该失败的时候真的会失败」 |

## 用法

### 环境变量

| 变量 | 含义 |
|---|---|
| `SOT_ENGINE_REPO` | 引擎仓 checkout 根目录 |
| `SOT_DESIGN_REPO` | 设计库仓 checkout 根目录 |

两者都**没有默认值、没有硬编码本机路径**。任一未设置或指向不存在的目录，校验器打印一行可操作的报错并以 **2** 退出（不是崩栈，也不是静默跳过）。

### 跑校验

```bash
export SOT_ENGINE_REPO=/path/to/Ship_of_Theseus
export SOT_DESIGN_REPO=/path/to/SoT-fyc-space
python -m scripts.sot_id_map            # 文本报告
python -m scripts.sot_id_map --json     # 机器可读报告
```

也可以不走环境变量：`--engine-repo` / `--design-repo` 覆盖之。`--map` 可以指到另一份映射文件（测试与演练用）。

> Windows / Git Bash 提示：给 Python 传路径请用 `D:/...` 形式。Git Bash 会把 `/d/...` 这类 POSIX 路径在传给原生 exe 时改写，症状会伪装成「程序坏了」。必要时 `MSYS2_ARG_CONV_EXCL='*'`。

### 退出码

| 码 | 含义 |
|---|---|
| 0 | 两侧 id 全集都被覆盖，且映射引用的 id 都真实存在 |
| 1 | 有 finding，逐条列出（见下表） |
| 2 | 环境或用法问题：环境变量未设、目录不存在、映射文件读不了或结构坏了 |

### 跑测试

```bash
python -m scripts.sot_id_map.test_smoke
```

测试主体跑在一个合成的微型双仓夹具上，不依赖真实仓库。每个负向用例都是两步：**先断言未破坏的夹具确实通过（退出 0），再破坏一处并断言被抓住**——只断言后者的话，夹具本身失效你也看不出来。最后一个用例在两个环境变量都指向真实目录时对真实仓库跑一遍，否则打印 SKIP 而不是假装覆盖过。

## 校验器会抓什么

| finding 类型 | 含义 |
|---|---|
| `uncovered_id` | 某侧存在、但映射表既没映射也没显式标 `unmapped` 的 id |
| `undeclared_scope` | 引擎 `data/` 下出现映射表没声明过的目录，或设计库 `snapshots/` 出现没声明过的文件（反过来，声明了但已消失也报） |
| `unknown_id` | 映射表引用了对应侧不存在的 id |
| `duplicate_id` | 同一实体类型下同一个 id 被认领两次 |
| `bad_reason` | `unmapped` 的原因码不在受控枚举内 |
| `missing_carrier` | 用了 `engine_implements_as_field` 却没给 `engine_carrier`，或指向的引擎文件不存在 |
| `bad_cardinality` | 声明的基数与实际 id 个数不符 |
| `bad_map` | 映射文件自身结构问题（未知实体类型、mapping 缺 `basis` 等） |

## 数据文件怎么读

`id_map.json` 四段：

1. **`engine_scope` / `design_scope`** —— 范围守卫。引擎 `data/` 下每个子目录、设计库 `snapshots/` 下每个 `.json`，要么被某个 `entity_types` 条目认领，要么列进 `excluded_paths` 并写明理由。没被声明的一律报 `undeclared_scope`，所以将来两侧新增内容不会被无声漏掉。
2. **`reason_codes`** —— 受控枚举，六个码，每个带说明。
3. **`entity_types`** —— 每个实体类型的两侧取数方式（引擎 = 目录递归、设计库 = 快照数组 + id 字段名），`coverage` 为 `required` 或 `excluded`，`note` 写明该类型的跨仓情况。
4. **`mappings` / `unmapped`** —— 具体条目。`mappings` 每条必须有 `basis`（对应关系从哪儿溯源），`unmapped` 每条必须有受控原因码。

### 六个原因码

| 码 | 什么时候用 |
|---|---|
| `engine_not_implemented` | 设计库有，引擎尚未实装同类实体（将来实装后应改成 `mappings` 条目） |
| `design_not_registered` | 引擎有，设计库存在同类实体表但未登记这一条 |
| `engine_implements_as_field` | **两侧都有这件事，但引擎建成了另一个实体的字段而非独立实体**。必须给 `engine_carrier` 指针，校验器核实文件存在 |
| `engine_no_entity_kind` | 引擎侧根本没有这类实体（设计库的规则条文、判定层） |
| `design_no_entity_kind` | 设计库侧根本没有这类实体（引擎的 buff、词条、地图、波次） |
| `no_traceable_correspondence` | 两侧有相近概念，但没有可溯源的 id 级对应。按 #556 界线，溯源不到就写明原因，不猜 |

`engine_implements_as_field` 是刻意与 `engine_not_implemented` 分开的。当前用到它的四条是〔心眼〕两条技能与剑气 / 剑意印记两条资源：设计库把它们建成技能 / 资源实体，引擎把它们实装进了职业档案的 `sword_qi_config` 字段组。把这类归成「引擎未实装」是**错误定性**。

## 维护方式

`id_map.json` 是人工维护的。典型改动场景：

- **引擎实装了一条设计库已有的东西** → 把对应的 `unmapped`（`engine_not_implemented`）条目删掉，在 `mappings` 里加一条并写 `basis`。
- **任一侧新增实体** → 校验器会以 `uncovered_id` 报出来。补一条 `mappings` 或 `unmapped`。
- **任一侧新增整个实体类型（新目录 / 新快照文件）** → 校验器以 `undeclared_scope` 报出来。加 `entity_types` 条目，或列进 `excluded_paths` 并写理由。
- **设计库那侧改了内容** → 设计库的交接界面是快照。对方重跑 `python scripts/export_snapshot.py` 提交之后，本校验器读到的就是新数据。

改完必须跑一次校验器并贴退出码；这与 lane 文档 §6.1「交接即验收」的口径一致。

`unmapped` 目前 200 条以上，其中绝大多数是成组的（例如引擎的地图 / 波次 / 敌人整类没有设计库对应物）。首次填表时是按类型分组决定原因码、再逐条展开写入的，组一级的判断理由写在对应 `entity_types` 条目的 `note` 字段里，不在每条上重复。

## 已知的判断点（复核时优先看这几处）

- **引擎 `data/weapons/`（13 条）标了 `no_traceable_correspondence`**：设计库 `equipment` 表有同名的 `weapon_might` / `weapon_hit` / `weapon_crit` 三字段，schema 同形；但 id 命名空间（`wpn_*` vs `eq_wpn_*`）与用途（职业 / 测试单位内建武器 vs 可拾取装备）都不同，没有一条 id 对得上。判定为「不猜」而非配对。
- **设计库 `tags`（12 条）标了 `no_traceable_correspondence`**：引擎技能的 `tags` 是自由英文字符串（当前 30 个不同取值），没有注册表。字面上确有近似（奥义 / `ougi`、反应 / `reaction`、被动 / `passive_like`、区域 / `area`），但「位移」与「机动」两条都可能落到 `mobility`，逐条判不了，故整组不猜。
- **`eq_wpn_iron_sword` 是两侧唯一的装备对应**：同 id 同名（铁剑），但两侧属性模型不同（引擎 `stats {STR:2}` + `tier` + `rarity`，设计库 `weapon_might 5` / `weapon_hit 90` / `weapon_crit 0`）。本表只做 id 对应，数值是否该对齐属于裁决问题，不在此处判断。
