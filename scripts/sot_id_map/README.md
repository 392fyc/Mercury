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
| 2 | **起不来**：环境变量未设、`--engine-repo=` 传了空、**仓库根**目录不存在、文件不是 UTF-8、JSON 语法坏、JSON 嵌套深到解析器爆栈、目录没有读权限（ACL）、映射文件读不了或缺顶层键 |

**1 和 2 的判据是**：这是「两侧数据与表对不上」（→ finding，退 1），还是「我根本读不到数据」（→ 退 2）。

按这条判据，**来源目录被改名 / 快照被删 / 某侧出现重复 id 全部算退 1**——它们正是这个工具存在的头号理由，不是它跑不起来的理由。这类情况下该 (类型, 侧) 的 id 集退化成空、整轮继续跑完，好让一次运行把所有问题报全。**退化不会变成静默**：范围守卫会报出新出现的那个目录，映射表里仍指向消失来源的条目会撞出 `unknown_id`，两个方向同时响。

改名事件的报告长这样（`data/classes` → `data/classes_v2`），两半都在，读者一眼能看出是改名：

```
[undeclared_scope] engine path data/classes_v2 is not declared in id_map.json ...
[undeclared_scope] engine path data/classes is declared in id_map.json but no longer exists ...
[unknown_id] mappings[0] references engine class id 'myrmidon', which does not exist ...
[missing_source] engine class: data/classes is declared as a source directory but does not exist
                 — if it was renamed or moved, update this entity type's `engine.path`;
                   the undeclared_scope findings above name what is there now
```

**1 和 2 的分工是承重的**：1 的含义是「跑完了，发现了问题」，2 的含义是「根本没跑成」。所以任何异常都不许以栈回溯的形式冲到 `main()` 顶层——那样退出码会是 1，自动化会把一次崩溃读成一次正常的校验结果，而输出里一条 finding 都没有。相应地，`main()` 的异常漏斗是**显式列举**的（`SourceError` 与 `OSError`），**不是** `except Exception`：兜底式捕获会把这个包自己的 bug 也粉饰成「环境问题」。映射表自身结构坏了不走异常，走 `bad_map` finding（退 1）。

### 跑测试

```bash
python -m scripts.sot_id_map.test_smoke
```

测试主体跑在一个合成的微型双仓夹具上，不依赖真实仓库。每个负向用例都是两步：**先断言未破坏的夹具确实通过（退出 0），再破坏一处并断言被抓住**——只断言后者的话，夹具本身失效你也看不出来。最后一个用例在两个环境变量都指向真实目录时对真实仓库跑一遍，否则打印 SKIP 而不是假装覆盖过。

## 校验器会抓什么

| finding 类型 | 含义 |
|---|---|
| `uncovered_id` | 某侧存在、但映射表既没映射也没显式标 `unmapped` 的 id |
| `undeclared_scope` | 引擎 `data/` 下出现映射表没声明过的**目录或散装 `*.json` 文件**，或设计库 `snapshots/` **递归**范围内出现没声明过的文件（反过来，声明了但已消失也报） |
| `missing_entity_id` | 已声明的实体来源里有条目读不出 id（新增技能时忘了写 `id`、id 是空串、类型不对等）。两侧同一条规则，不会被无声跳过 |
| `missing_source` | 映射表声明的来源（引擎目录 / 设计库快照）在仓库里不存在了。**改名事件的另一半**，与 `undeclared_scope` 成对出现 |
| `duplicate_entity_id` | 同一侧有两个实体抢同一个 id，对应关系变得有歧义 |
| `unfollowed_link` | 实体目录树里出现目录软链接或 junction。**一律不跟随、一律报出**，详见下节 |
| `path_escape` | 目录树里的 `.json` 经软链接指到仓库根之外，未被读取 |
| `cross_side_contradiction` | 同一个 id 串两侧都存在，却在某一侧被标成 `unmapped`。见下方说明 |
| `unknown_id` | 映射表引用了对应侧不存在的 id |
| `duplicate_id` | 同一实体类型下同一个 id 被认领两次 |
| `missing_carrier` | 用了 `engine_implements_as_field`，但 `engine_carrier` 缺失、没有 `#字段` 部分、文件不存在，**或所指的字段在那个文件里根本不存在** |
| `bad_reason` | `unmapped` 的原因码不在受控枚举内 |
| `bad_cardinality` | 声明的基数与实际 id 个数不符 |
| `bad_map` | 映射文件自身的结构问题。含：路径逃出仓库根 / 绝对路径 / 盘符 / UNC / 含 `..`；`excluded_paths` 不是对象或理由为空；`mappings`·`unmapped` 里出现非对象（如 `null`）；`engine_ids`·`design_ids` 不是列表（裸字符串曾被当成单元素列表，单字符 id 能整条通过）；未知实体类型；mapping 缺 `basis`；`same_id_other_side` 埋在没有对面同名 id 的条目上 |

结构类 `bad_map` 在**读任何仓库数据之前**就判定，报告开头会打 `ABORTED: map structure is invalid; no repository data was read`。

### `cross_side_contradiction`：覆盖检查挡不住的那一种假话

覆盖检查只问「这个 id 被认领了吗」，不问「认领得对不对」。于是有一条路能一边通过一边说假话：一个**两侧都存在**的 id，被拆成两条互相矛盾的 `unmapped`——引擎侧写 `design_not_registered`（说设计库没登记），设计侧写 `engine_not_implemented`（说引擎没实装），而实际上两侧都有它。

所以：只要某个 id 串在同一实体类型的两侧 id 全集里**都**出现，针对它的 `unmapped` 条目必须在 `same_id_other_side` 字段里写明「为什么另一侧那个同名条目不是同一个东西」。这给真正的 id 撞名留了一扇写明理由的门。

**这扇门的边界要说清楚**：校验器只能机械验证这个字段**非空**，验不了理由是否成立——填一句敷衍话同样能通过。所以用这个字段是一个**需要被人复核的动作，不是免检**。为此有两条约束：

- **用了就会出现在报告里。** 汇总输出固定有一行 `same_id_other_side escape hatch: N in use`，N > 0 时逐条列出 id、原因码与所填理由。当前真实数据是 `0 in use`，一旦有人用了这扇门，下一个读报告的人立刻看得见，不必去翻数据文件。
- **不许预先埋伏。** 某条 `unmapped` 带了这个字段、但那个 id 在另一侧根本不存在（也就是它当前什么都没抑制），报 `bad_map`。否则可以提前把字段撒在一堆条目上，等哪天对面出现同名 id 时矛盾就被悄悄抑制掉了——那等于绕过了这道检查。

### 目录别名：一律不跟随、一律报出

实体目录树里如果出现**目录软链接或 Windows junction**，校验器不跟随它，并报 `unfollowed_link`。**被声明的来源目录本身**是别名时同样如此——路径上任意一级是别名都会报出来，不存在「子目录算、来源目录本身不算」的例外（`resolve_within` 看不见这种情况，因为 resolve 恰恰会把证据抹掉，所以另有一次 `alias_components` 检查在 resolve 之前跑）。两个原因：

- **不能静默跳过**：`os.walk` 默认不跟随 POSIX 软链接且一声不吭，链接后面的实体就会从 id 全集里凭空消失，而范围守卫只看得见父目录、发现不了——又是一次「绿着跑，数据没了」。
- **也不能盲目跟随**：链接可以指到仓库之外，跟随就等于绕过路径包含保证。

**跨平台行为被统一了**：实测 Windows 上 `os.walk` 会径直穿过 junction（POSIX 软链接则不会），也就是同一个仓库在不同机器上会被读成不同的样子，其中一种还会把未声明的内容经别名拉进来。所以这里不交给平台决定：别名目录一律剪掉、一律报出，请把它换成真目录，或者把它的目标声明成独立的实体来源。

单个 `.json` 文件的软链接会被 `os.walk` 当普通文件列出，所以每个文件读取前还会再做一次「解析后是否仍在仓库根内」的检查，指到仓外的报 `path_escape` 且不读。

### `engine_carrier` 的字段部分会被真的解析

`engine_implements_as_field` 的全部说服力就在「引擎把它建成了哪个字段」，所以 `#` 之后那半截会被当作点号路径（`a.b.c`）在那个 JSON 里逐层下探，走不通就报 `missing_carrier`。只验文件存在等于没验。

## 数据文件怎么读

`id_map.json` 四段：

1. **`engine_scope` / `design_scope`** —— 范围守卫。引擎 `data/` 下每个子目录与散装 `.json`、设计库 `snapshots/` 递归范围内每个 `.json`，要么被某个 `entity_types` 条目认领，要么列进 `excluded_paths` 并写明理由。没被声明的一律报 `undeclared_scope`，所以将来两侧新增内容不会被无声漏掉。

   `excluded_paths` **必须是对象**（路径 → 理由），且理由非空字符串。写成数组会被拒（`bad_map`）：数组没有地方放理由，而一个不用写理由的排除通道可以一次吞掉整个实体类型——那正是这道守卫存在的意义的反面。
2. **`reason_codes`** —— 受控枚举，六个码，每个带说明。
3. **`entity_types`** —— 每个实体类型的两侧取数方式（引擎 = 目录递归、设计库 = 快照数组 + id 字段名），`coverage` 为 `required` 或 `excluded`，`note` 写明该类型的跨仓情况。

   **路径一律相对于仓库根，且必须留在根以内。** 绝对路径、盘符路径（`D:/...`）、UNC（`//host/...`）、含 `..` 的路径全部报 `bad_map`，且此时**一个字节的仓库数据都不会被读**（报告开头会打 `ABORTED`）。这不是洁癖：`root / "D:/x"` 在 Windows 上会把 root 整个丢掉，而 `../../..` 曾让校验器真的去遍历了 Mercury 自己的目录、把本仓的 `id_map.json` 与 `.omc/` 会话状态读了进来。拼接之后还会再用 `resolve()` 复查一次是否落在根内——这一层抓的是字符串检查看不见的软链接逃逸。

   **「必须写点什么」的字段一律做真类型校验**，不是 `str(x)` 强转后判非空。`str(None)` 是 `"None"`、`str(False)` 是 `"False"`、`str([])` 是 `"[]"`，三者都非空都为真——所以曾经**填一个 JSON `null` 就能满足「必须写理由」**，包括那个抑制 `cross_side_contradiction` 的 `same_id_other_side`。现在 `type` / `side` / `id` / `reason` / `cardinality` / `basis` / `engine_carrier` / `same_id_other_side` / `excluded_paths` 的值 / `reason_codes` 的描述，全部要求真字符串且 `strip()` 后非空，否则 `bad_map`。默认姿态是**未知即不安全**，不是未知即无害。

   **两侧的 id 规则是同一条**，不存在一侧更宽松：id 必须是非空字符串，或整数（会被转成字符串——这是为设计库某些表用数据库自增主键做的**显式**让步，不是 `str()` 随手兜底）。空串、null、布尔、浮点、对象一律报 `missing_entity_id`，既不静默收下也不静默丢弃。
4. **`mappings` / `unmapped`** —— 具体条目。`mappings` 每条必须有 `basis`（对应关系从哪儿溯源），`unmapped` 每条必须有受控原因码。

### 八个原因码

| 码 | 什么时候用 | 当前条数 |
|---|---|---:|
| `engine_not_implemented` | 设计库**在用**，引擎尚未实装同类实体（将来实装后应改成 `mappings` 条目） | 41 |
| `design_shelved_not_imported` | 设计库侧该条目的 `shelf_state` 不是「在用」（已归档 / 待删除），**不是等待实装的候选** | 19 |
| `design_not_registered` | 引擎有，设计库存在同类实体表但未登记这一条 | 37 |
| `engine_implements_as_field` | **两侧都有这件事，但引擎建成了另一个实体的字段而非独立实体**。必须给 `engine_carrier` 指针，校验器核实字段真实存在 | 4 |
| `engine_no_entity_kind` | 引擎侧根本没有这类实体（设计库的规则条文、判定层） | 27 |
| `design_no_entity_kind` | 设计库侧根本没有这类实体（引擎的 buff、词条、地图、波次、敌人、地形） | 37 |
| `engine_placeholder_no_design_entity` | 引擎侧占位 / 内部派生结构，**已核对确认**设计库没有对应实体 | 13 |
| `no_traceable_correspondence` | 两侧有相近概念，但**判不出** id 级对应。按 #556 界线，溯源不到就写明原因，不猜 | 12 |

三组容易混淆的边界，都是刻意分开的：

- **`engine_implements_as_field` ≠ `engine_not_implemented`。** 当前用到前者的四条是〔心眼〕两条技能与剑气 / 剑意印记两条资源：设计库把它们建成技能 / 资源实体，引擎把它们实装进了职业档案的 `sword_qi_config` 字段组。把这类归成「引擎未实装」是**错误定性**——它已经实装了，只是换了一种实体形态。
- **`engine_placeholder_no_design_entity` ≠ `no_traceable_correspondence`。** 前者是**查过两侧、确认没有**；后者是**查了但判不出**。把「已确证没有」和「判不了」塞进同一个码，就等于把两种该分开处置的状态混成一种：前者可以收工，后者要留着继续查。（此项区分应 SoT 请求加入，见设计库 `docs/sot-to-mercury-wave2-feedback-2026-08-09.md` §4 第 2 条。）
- **`design_shelved_not_imported` ≠ `engine_not_implemented`。** 前者说的是设计库自己把条目下架了，引擎不导入是**正确行为**；后者说的是设计库在用而引擎欠着。两者混用会让读者以为有 19 条实装欠账，实际只有 41 条。判据在数据里：`snapshots/*.json` 的 `shelf_state`（待删除的另带 `trashed_at` 时刻）。

## 维护方式

`id_map.json` 是人工维护的。典型改动场景：

- **引擎实装了一条设计库已有的东西** → 把对应的 `unmapped`（`engine_not_implemented`）条目删掉，在 `mappings` 里加一条并写 `basis`。**`basis` 要能从数据本身溯源**，最硬的证据是引擎侧文件自带的 `_mirror` 字段（自述镜像自设计库某个快照 commit）加上两侧字段值逐项相等，「看着像」不算。
- **设计库把某条下架（`shelf_state` 改成已归档 / 待删除）** → 该条的 `unmapped` 原因码从 `engine_not_implemented` 改成 `design_shelved_not_imported`；反之恢复在用则改回。这不影响覆盖，但影响读者判断「引擎到底欠了多少」。
- **任一侧新增实体** → 校验器会以 `uncovered_id` 报出来。补一条 `mappings` 或 `unmapped`。
- **任一侧新增整个实体类型（新目录 / 新快照文件）** → 校验器以 `undeclared_scope` 报出来。加 `entity_types` 条目，或列进 `excluded_paths` 并写理由。引擎侧直接落在 `data/` 下的散装 `*.json`、设计库 `snapshots/` 子目录里的快照，同样会被报出来。
- **任一侧把来源目录 / 快照改名或删掉** → 同时报 `missing_source`（老路径没了）与 `undeclared_scope`（新路径没申报），改 `entity_types` 里那个 `path` 即可。
- **引擎实装了一条设计库已有的同名东西** → 校验器以 `cross_side_contradiction` 报出来（前提是原先标了 `unmapped`）。补一条 `mappings`。
- **设计库那侧改了内容** → 设计库的交接界面是快照。对方重跑 `python scripts/export_snapshot.py` 提交之后，本校验器读到的就是新数据。

改完必须跑一次校验器并贴退出码；这与 lane 文档 §6.1「交接即验收」的口径一致。

`unmapped` 目前 200 条以上，其中绝大多数是成组的（例如引擎的地图 / 波次 / 敌人整类没有设计库对应物）。首次填表时是按类型分组决定原因码、再逐条展开写入的，组一级的判断理由写在对应 `entity_types` 条目的 `note` 字段里，不在每条上重复。

## 读表前必须知道的两件事

**一、`tag` / `rule` / `term` 三类的标识符不是 `id`。** 它们在设计库里的整型 `id` 是数据库自增主键，重建库时会被压实重排（设计库 `snapshots/README.md` §5.3 有实测记录），**不是跨仓标识符**。本表对这三类分别用 `key` / `code` / `term` 作为 id 字段，写在各自 `entity_types` 条目的 `design.id_field` 里。所以 `unmapped` 里 `tag` 那 12 条看到的中文（击杀 / 奥义 / 反应 …）是**标签的 `key`**，不是显示名；`rule` 那 23 条看到的 `R1.1` 是 `code`。

**二、两条 resource 的 `engine_carrier` 指向不同的职业档案，是有规则的。** `mark` 指 `data/classes/kensei.json`，`qi` 指 `data/classes/myrmidon.json`——规则是**指向设计库该条目 `class_ids` 所覆盖的职业**：`mark` 的 `class_ids` 只有 `kensei`；`qi` 的 `class_ids` 是剑士线三职共享，取这条线的起点职业 `myrmidon`（引擎 `data/classes/myrmidon.json` 的 `_note` 自述「剑士是这条线的起点（Lv1-4），转职在 Lv5」）。需要知道的引擎现状是：`sword_qi_config` 与两组键在**两个职业档案里都齐全**，所以指哪一个都能通过校验；不统一指向同一个文件是因为两条资源的 `class_ids` 本来就不同。两条 `unmapped` 条目的 `note` 里各自写了这段。

## 这个校验器防什么、不防什么

**防的是漂移与疏忽**：两侧数据变了而表没跟上、新增内容没人归类、字段写了一半没填值、目录被别名遮住、文件编码坏掉、嵌套深到解析器爆栈、路径写错走出了仓库、命令行参数传了空。这些都是日常会真实发生的事，上面每一条都有对应的 finding 和回归测试。

### 两条判据（要不要继续加闸门）

`id_map.json` 是 Mercury 自己仓库里的文件，改它要走 Issue + PR + review。所以往下再发现问题时，按这两条判断：

1. **要触发它，是不是必须有人往本仓提交一份刻意畸形的 map？** 是 → **不修**。那是 code review 的职责，再加闸门只会无限迭代。
2. **这个条件能不能在目标平台（本机 Windows + Git Bash）上实际构造出来？** 不能 → **不修**，登记进下面的「已知限制」，**连同「造不出来所以没验过」这句话本身一起登记**。

这两条是**事后**划的界线，不是给已知缺口开脱的说辞：`null`、空串、`--engine-repo=`、软链接 / junction、GBK 编码、深嵌套 JSON——这些都**不算**蓄意构造（是手写 JSON 与真实环境里的常见情形），所以全部修掉了。

### 已知限制（如实登记，不因为交付好看而抹掉）

- **「文件软链接指到仓外 → `path_escape`」这段代码路径没有被真跑验证过。** 本机创建文件软链接需要特权（开发者模式或管理员），junction 只能做目录、做不了文件，所以造不出这个场景。对应测试会打 SKIP 而不是假装跑过。**这段代码是照逻辑写的，没有实证。**
- **`_entry_kind()` 里 `"unknown"` 那条分支从未被观察到执行。** 它防的是「`Path.is_dir()` 吞掉 OSError 返回 False，导致一个 stat 不了的条目从范围守卫里消失」。实测用 `icacls /deny` 拒绝目录读取时 `is_dir()` 仍返回 True，该前提没有发生。这是**防御性添加，不是在修一个已证实的漏洞**，代码注释里也是这么写的。
- **`resolve()` 处捕获 `RuntimeError` 同样是防御性的。** 它防的是软链接环，但本机建不出软链接、junction 又形不成环，**无法验证**；而且 `resolve()` 对环抛 `RuntimeError` 还是 `OSError` 是版本相关的。同样在注释里标明未复现。
- **跨平台行为只在 Windows 上实测过。** 「POSIX 软链接会被 `os.walk` 跳过」是文档知识而非实测；`_is_reparse_dir()` 在非 Windows 上恒为 False。别名剪枝逻辑本身与平台无关，但 Linux 上的实际表现没验过。
- **`_check_carrier` 里那次 `alias_components()` 检查是纵深防御，不是在修已证实的漏洞。** 实测构造（`engine_carrier` 指向经 junction 的路径）已经被范围守卫报出 `unfollowed_link`，并没有静默通过；这里再查一次只是让 carrier 路径与来源路径服从同一条规则，不必依赖「恰好另一处检查覆盖到了」。代码注释里也是这么写的。
- **`same_id_other_side` 只能被机械验证「是一个非空字符串」，验不了理由是否成立。** 填一句敷衍话仍然能通过（会被报告点名，但不会被拦）。这扇门的最终防线是人，不是工具。

## 已知的判断点（复核时优先看这几处）

- **引擎 `data/weapons/`（13 条）已从 `no_traceable_correspondence` 改判为 `engine_placeholder_no_design_entity`**（#558）。原先存疑的点是：设计库 `equipment` 表有同名的 `weapon_might` / `weapon_hit` / `weapon_crit` 三字段、schema 同形，判不出它是不是对应物。**Wave 2 把这个疑问用数据回答了**——设计库 `equipment` 的 22 条在用条目 1:1 落到了引擎 `data/equipment/`（引擎侧带 `_mirror` 锚点），与 `data/weapons/` 无关；而 13 条 `weapons` 的 `_note` 自述是 R1.7 结构迁移从旧 `class`/`enemy` json 内联字段搬来的引擎内部产物。所以现在是「已确证没有对应实体」，不再是「判不出」。
- **设计库 `tags`（12 条，标识符是 `key`）仍是 `no_traceable_correspondence`**，而且现在是**唯一**用这个码的一组：引擎技能的 `tags` 是自由英文字符串（当前 30 个不同取值），没有注册表。字面上确有近似（奥义 / `ougi`、反应 / `reaction`、被动 / `passive_like`、区域 / `area`），但「位移」与「机动」两条都可能落到 `mobility`，逐条判不了，故整组不猜。**这一组保留原样，正是为了让「判不出」这个状态还有地方表达。**
- **设计库装备现在有 22 条对应，不再是只有铁剑一条**。曾经记在这里的「两侧属性模型不同（引擎 `stats {STR:2}` + `tier`，设计库 `weapon_might`/`hit`/`crit`）」在 Wave 2 之后**已不成立**：引擎改用了设计库的三参数，22 条逐项相等。引擎侧仍多两个设计库没有的字段（`tier` 由 rarity 派生供掉落分桶、`engine_effects` 特效指令），按 lane 文档 §2.2 属引擎独有列、不回流。
- **`eq_wpn_lancereaver` 是唯一一条设计库有而引擎刻意没导入的装备**：它的 `shelf_state` 是「待删除」（`trashed_at` = 2026-07-09）。判据是导入行为与状态完全对齐——22 条在用全导、1 条非在用不导，所以这是按状态刻意跳过，不是实装欠账。**若把它标成 `engine_not_implemented` 就是错误定性**，会让读者以为引擎欠了一条。
