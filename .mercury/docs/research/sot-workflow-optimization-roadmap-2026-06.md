# SoT 游戏开发 Workflow 优化路线图（2026-06-22）

> **性质**:SoT（Ship of Theseus）游戏开发的**辅助 Workflow** 规划。非 Mercury 自身产品内容，故**不开 Mercury Issue**；待办在本文件追踪。工作区维持在 Mercury 目录。
>
> **硬约束(用户 2026-06-22 指令)**:
> - 对 SoT **仅做 Workflow 优化**,**不直接干预** SoT 仓库(`D:\ShipOfTheseus\Ship_of_Theseus` / `D:\ShipOfTheseus\SoT-fyc-space`)及其名义下的产出内容。
> - Workflow 模板宿主 = Mercury `.claude/workflows/`(随 repo 分发,可 `/<name>` 复用)。
> - side-lane 在做 harness/Mercury 产出优化;本 lane 专注 SoT。
> - 全程强制研究协议 web-verified;本文件事实均附来源。

---

## 0. SoT 现状基础设施地图(只读勘察 2026-06-22)

| 资产 | 状态 | 对优化的意义 |
|---|---|---|
| Godot **4.6.1** 工程(`Ship_of_Theseus`) | `project.godot` features=4.6;`data/skills/*.json` 数值技能 | 引擎数据层 |
| `addons/godot_mcp` **v2.15.0/mjs 2.16.0** | WS server + 完整命令面(scene/node/script/resource/input/screenshot/tilemap + game_bridge) | CC 可驱动编辑器/运行时(**握手有坑,见 §4**) |
| 自研 headless 测试 | `tests/test_swordsman_resources.gd`/`_integration.gd`(`extends SceneTree`,`godot --headless --script ...`,退出 0/1)覆盖真理源(unit/damage_calculator/pathfinding)+ JSON 层 | headless 回归**已通**;`剑圣手动测试清单.md` 仍部分手动 = 摩擦 |
| `tools/*.html` | damage-simulator / talent-tree-editor / map-designer / equipment-designer | 早于设计库;与设计库/平衡 sim **重叠**,宜演进非重复 |
| **SoT 设计库**(`SoT-fyc-space`,FastAPI+SQLite) | 部署 NAS `sot.fyc-space.uk`(待 CF token);7 实体 + 31 API + 8 页;`/api/import` 有,**无 Godot export** | 设计**叙述**层 SoT |
| 技能 schema 差异 | Godot=数值(`power/hit_bonus/range/area/cooldown/effects[]`);设计库 `Skill`=叙述(`effect` 散文 / `action_type` / `trigger`) | 设计库↔Godot 非 1:1,见 §5 |

**核心缺口**:设计库(设计)与 Godot(引擎)**断连**;"设计库当单一事实源驱动引擎"对引擎侧未实现。

---

## 1. 痛点1 — 天赋设计:`talent-validate` Workflow ⭐最高优先

### 1.1 诊断:为什么"agent 验证帮助小"(你的体感是对的)
纯 LLM 是**已知的弱平衡裁判**(web-verified):
- 系统性**宽容偏差**,非对抗框架下漏支配策略(arxiv 2512.07462 / 2601.19726)。
- **组合穷举失败**(漏交互对,除非显式枚举)、**算术漂移**(功率和不准)。
- LLM-only 管线无 schema 接地会产出结构非法/引擎不兼容输出(G-KMS, MDPI 2025)。
→ 单 agent "审一下这个天赋"必然得到表扬 + 表面建议。**必须换混合结构**。

### 1.2 解法:4-layer 混合管线(确定性层做量化,LLM 只做语义+对抗)
落点 `.claude/workflows/talent-validate.js`,入参 `{talent_id}` 或 `{talent_draft_json}`;**设计库 API 当语料适配器**(像 gpt-image-2 之于 animate-frames):harness 调 设计库 API 取候选天赋 + 该职业现有天赋 + tag 注册表 + 稀有度功率上限 + 规则表,**路径+任务**传 subagent(不 bulk 注入)。

| Layer | 做什么 | 谁做 | 关键 |
|---|---|---|---|
| **L1 结构验证**(<1s,零 LLM) | schema 校验;tag 合法性(mech/content 层一致);**确定性功率评分**(权重据现有平衡卡校准);规则冲突(SQL/JSON 查规则表) | JS 算术 + Haiku | **功率预算禁用 LLM**,纯评分函数,超稀有度上限即 flag |
| **L2 组合扫描** | **代码**枚举与候选共享 tag 的对/三元组(非 LLM 想象)→ 每个 flagged 对喂 Haiku triage,结构化 JSON `{interaction_type:loop/amplifier/neutral/anti-synergy, risk_level, example_sequence, mitigation}` | JS 枚举 + 并行 Haiku(5-20 对,≤16 并发) | 组合爆炸由 tag 结构控制 |
| **L3 对抗批判** | Agent A(Optimizer,得**全语料**+规则+候选)构造最高价值滥用序列 JSON `{sequence, expected_outcome, win_condition_turns}` → Agent B(Defender)指出中和它的规则 JSON `{neutralized, mechanism, residual_risk}`;`neutralized:false` → 升级为确认 exploit | 串行双 Sonnet | Red-Teaming Game(arxiv 2310.00322);~2000 token/天赋 |
| **L4 gap-fill 构思**(按需) | 设计师在 trigger×effect 覆盖矩阵选**空格子** → 单 Sonnet 生成**1 个**语料-schema-合法候选 JSON → 立即回灌 L1-L3 | 单 Sonnet | **禁批量 20 个**(triage 负担>收益);1 个或≤3 按 gap-fit 排序 |
| **Monte-Carlo sim** | 数值平衡(L3 只抓逻辑 exploit,定量平衡需模拟) | **Python 脚本**(harness shell 调,非 subagent) | 数值裁判;读同一份技能数据 |

**减负构思三招**(web-verified):① gap-fill(查语料缺失生态位 → 约束内生成 1 个);② 设计空间矩阵(trigger×effect 覆盖图,选格子,负担从"扫全空间"降到"选一格");③ embedding 相似度过滤(text-embedding-3-small,抑制 cos>0.85 冗余)。

**设计须防的失败模式**:LLM 数值漂移(所有数字从结构化字段**重算**,不信 LLM 叙述伤害);语料陈旧(扫描只与语料完整度等齐);tag 爆炸(≤60 tags,≤30 mech + ≤30 content,L1 强制归一)。

### 1.3 前置依赖(决策点交用户)
- **功率评分需数值** → 设计库 `Skill`/`Talent` 现为叙述字段,L1 评分需要数值化效果幅度。两条路(见 §5 同款):(a) 设计库补引擎数值字段;(b) 评分用一个手填的 `power_weights` 结构。**短期** L1 可先只做 schema+tag+规则冲突(不含功率评分),数值评分待 设计库补字段。
- 设计库需暴露稳定**只读 API**(已有 31 端点;确认 GET 列表/详情够用)。
- **Z3 可选增强**:`pip install z3-solver`(MIT)可把功率预算/tag 互斥/触发冲突编成 SAT/UNSAT + 反例(确定性可审计)。L1 的 SQL/评分够用则 Z3 是后续硬化项,非 MVP。

---

## 2. 痛点2 — 像素素材:`pixel-asset-pipeline` Workflow

### 2.1 工具裁决(web-verified,按素材类别专精)
**无单一工具覆盖全四类** → 三个 API 服务按强项分工 + Aseprite CLI 当统一后处理。

| 素材类 | 首选 | 自动化面 | 成本 |
|---|---|---|---|
| 角色/单位(多帧) | **PixelLab.ai** —— `POST /v2/create-character-with-4-directions` + 骨骼动画≤16 帧 + 参考图角色一致性;**官方 MCP `pixellab-code/pixellab-mcp`**(CC 直接用无需 adapter);**Godot 明列支持** | REST 40+ 端点 + Python SDK + MCP | $0.002-0.185/call,≤512² |
| 场景/tileset/地图 | PixelLab Wang tileset **或** Retro Diffusion **RD Tile**(Replicate,无缝纹理) | REST / Replicate | RD 新号 $0.50 免费额 |
| 技能 VFX | **Retro Diffusion RD Animation**(Replicate;"风格一致动画像素 sprite",引擎兼容布局) | Replicate API | 按 prediction |
| UI 图标 | **Scenario.gg** 自训 LoRA(5-100 参考图,**最强风格锁**,图标集内聚) | API-first + MCP + webhook | $10-50/月 |
| 兜底/省钱 | gpt-image-2(**Mercury animate-frames 已包**,单帧)/ ComfyUI 本地(高量省 API 费) | 已有 skill / 本地 HTTP | $0.04-0.17/img / 本地 GPU |

### 2.2 4-stage 管线(Workflow 设计)
1. **生成**(按类别 fan-out):上表工具;角色/tileset 走 PixelLab MCP,VFX 走 RD Animation,图标走 Scenario LoRA,兜底 gpt-image-2。输入契约 = animate-frames 的 character-bible JSON(两后端共享上游契约)。
2. **一致性+调色板门**:复用 **Mercury animate-frames 现有 dHash 一致性 + palette 量化校验**;低于阈值拒绝 + 重生成。
3. **后处理 = Aseprite CLI**(headless 通用后处理,~$20 一次性,无 per-call 费):`aseprite -b --color-mode indexed --palette master.gpl frames/*.png --sheet out.png --sheet-type packed --sheet-pack --data out.json --split-tags` —— 一次调用搞定**调色板量化 + sprite-sheet 打包 + 帧元数据 + tag 分区**。
4. **Godot 导入**:解析 Aseprite JSON → 生成 `.tres` SpriteFrames 资源(或 GDScript 导入助手)。**注意约束**:此步**产出物落 SoT 仓** → 按"不直接干预 SoT 仓"约束,Workflow 只产出文件到暂存区,**由用户/opencode 导入 SoT 仓**,或明确征得同意。

### 2.3 Frame Ronin 定位(你的参考)
Frame Ronin(`github.com/systemchester/FrameRonin`,开源自托管,627★)= **video/GIF → sprite-sheet 处理器**(U2Net 去背 + FFmpeg + RPGMaker 模板),**非生成式**;有 REST `/jobs` API(可自动化,无 CLI/MCP,需本地 Docker 起后端);**license 未确认(LICENSE 404,标 UNVERIFIED,采用前须核)**。
→ **定位**:仅当用 **AI 视频生成**(Seedance/Wan2.1/Kling)产动画片段时,Frame Ronin 是"视频→精灵表"桥;若纯 gpt-image-2/PixelLab 静帧路线,**Aseprite CLI 已够,Frame Ronin 价值有限**。与 animate-frames **互补非竞争**。

### 2.4 与 Mercury 现有的协同
animate-frames 的 character-bible JSON 契约 + dHash 门 + palette 校验是地基;加 (1) PixelLab MCP 角色生成 + (2) Aseprite CLI 后处理 = 端到端管线,**无需架构重做**。PixelLab adapter 若需自写 HTTP 适配,落 `adapters/pixellab/`(≤200 LOC);但有官方 MCP 时优先 MCP 免 adapter。

---

## 3. `godot-test` Workflow:包装现有 headless harness 成闭环

- **缺口**:dev subagent 无法自动跑+读 Godot 测试;手动测试清单摩擦。
- **做法**:Mercury skill/workflow 跑 SoT 现有 headless 测试,带 verified 护栏:`--import --quit-after 2`(绕 race issue #77508)、`GODOT_DISABLE_LEAK_CHECKS=1`、`.godot/` 不入库、Godot 4 **不用 xvfb**(issue #43444);解析退出码/JUnit XML 回结构化 pass/fail。
- **可选升级**:自研 SceneTree → **GUT 9.6**(对 Godot 4.6,JUnit XML + 发现)或 **GdUnit4 v6.1.3**(runtime-free 纯逻辑测试更快);或保留轻量自研只做包装。把 `剑圣手动测试清单.md` 项转自动 SceneTree 断言。
- **gdtoolkit 门(快赢)**:`pip install "gdtoolkit==4.*"`(v4.5.0,纯 Python 无引擎)→ `gdformat`+`gdlint`+`gdparse` 提交前抓 GDScript 语法/风格错。
- **约束**:测试 GDScript 在 SoT 仓 → Workflow 只**跑+读**(只读执行),不改 SoT 仓测试文件;新断言由用户落仓或经同意。

---

## 4. godot_mcp 接入 + 安全连接协议 ⚠️

**握手坑(只读审计坐实)**:godot_mcp = 编辑器内 WebSocket server,bind LOCALHOST **端口 6550**,**单客户端**;新客户端**接管**(close code `4002` "Connection taken over by a newer client")**踢掉旧客户端**。SoT `.mcp.json` 已配 `godot` server(opencode + Claude 都可能用)。

**当前现状(2026-06-22 检查)**:`Godot_v4.6.1-stable_win64`(PID 17240)**编辑器活动** + 多 node 进程 → **极可能 opencode 正连着 6550**。**当前 CC 在 Mercury 工作区(未加载 SoT `.mcp.json`)→ CC 此刻未连**(安全)。

**安全连接协议(用户测试前必走)**:
1. 连 godot_mcp **前**确认 6550 无其他活动 MCP client(关 opencode 的 godot 连接 / 关多余编辑器实例),否则 CC 连接会接管并**踢掉 opencode**,扰乱活动 SoT 工作。
2. 本 session(编辑器活动)**已 defer live 连接测试** —— 符合用户"测试前先检查现状"要求。
3. 接入方式:把 `godot` MCP server 加入**这条 CC session 的 MCP 配置**(非改 SoT 仓 `.mcp.json`),或 Workflow 内按需连;用完断开释放 6550。
4. 版本注意:addon plugin.cfg 2.15.0 vs lazy_server.mjs 2.16.0 轻微不一致,接入时留意。

### 4.1 实测记录(2026-06-25,main lane / ultracode)— live 测试全绿

**接入方式**:
- **MCP 配置**(供重启会话后用 `mcp__godot__*` 工具层):`claude mcp add godot -s local -e GODOT_PROJECT_PATH=D:/ShipOfTheseus/Ship_of_Theseus -e GODOT_MCP_PACKAGE_VERSION=2.16.0 -- node D:/ShipOfTheseus/Ship_of_Theseus/addons/godot_mcp/godot_mcp_lazy_server.mjs`。落 `~/.claude.json` **本项目私有段**(`Scope: Local config` — 不碰 SoT 仓 `.mcp.json`、不污染 git);`claude mcp get godot` 报 ✔ Connected。**改后须重启会话**才能在 session 内拿到 `mcp__godot__*` 工具(本 session 未热加载)。
- **live 测试方式**:Node 24 内置 `WebSocket` **直连 6550**(绕 MCP stdio 桥直测 WS 命令面),本 session 即可验证、不必等会话重启。脚本 = scratchpad `godot-mcp-livetest.mjs`(纯只读命令,零 SoT 文件副作用)。

**预检(安全协议第①步)**:6550 接入前**并非空闲** — PID 22984(`@satelliteoflove/godot-mcp@2.16.0` cli.js,**2026/6/21 启动**,父链 `bash→npx→cmd→node`,opencode 泄漏僵尸 MCP server)持活动连接。另有 21 个空闲 `lazy_server.mjs`(lazy connect 未占 6550,无害)。用户授权**直接接管**(close 4002 踢 22984)。

**live 测试结果(9 只读命令全绿)**:
| 命令面 | 命令 | 结果 |
|---|---|---|
| system | `mcp_handshake` | addon **2.15.0** / godot **4.6.1-stable (official)** / proj ShipOfTheseus / path `D:/ShipOfTheseus/Ship_of_Theseus/` |
| project | `get_project_info` | main_scene `res://scenes/tactical/TacticalScene.tscn` |
| scene | `get_current_scene` | 实时编辑器打开 `res://scenes/tactical/bottom_dashboard.tscn`(BottomDashboard/Control) |
| scene | `get_scene_tree` | root=BottomDashboard/Control children=0 |
| script | `get_current_script` | `res://scripts/tactical/tactical_scene.gd` 17KB 真实源码 |
| node | `find_nodes`(type=Node2D) | count=0(当前 Control 树无 Node2D,命令正确执行) |
| screenshot | `capture_editor_screenshot` | **320×233 真实 PNG**(base64 head=`iVBORw0KGgoA`=PNG magic)= 驱动编辑器渲染铁证 |
| debug | `get_debug_output`(editor) | 编辑器日志含 "Taking over active client…Accepting replacement client" 自证 takeover 链 |
| game_bridge | `capture_game_screenshot` | NOT_RUNNING(编辑器态未跑游戏,命令路由+运行时门控正确) |

`disconnect_client` 返回 `__disconnect_after_response:true`(优雅断开协议生效)。

**两点坐实**:
1. **版本不一致**:addon plugin.cfg **2.15.0** vs server EXPECTED **2.16.0** — 握手照常工作,仅触发 console warning(`version_mismatch` 事件),不阻断命令面。
2. **22984 是 autoReconnect 僵尸**:disconnect 后 `WS_CLOSE code=4002`(被新 client **takeover**,非 4003 server-disconnect)+ AFTER 快照 6550 被**新端口 59086** 占 → 22984 被踢后立刻重连抢回。**对直连 live 测试无影响**(被踢前 9 命令已跑完),但会**持续抢占 6550**,干扰后续重启会话用 MCP 工具层的稳定性 → 已清理。

**用户裁决(2026-06-25)**:
- ✅ **已清理 22984 僵尸链**(用户授权):kill 5 进程(cli.js 22984 + npx-cli 19472 + cmd 25656 + bash 26908/28336),6550 回归 **Listen-only 干净**(仅编辑器 17240 监听,无 Established);21 个无害空闲 `lazy_server.mjs` 未碰。后续重启会话用 MCP 工具层时 lazy_server 连 6550 不再被僵尸争抢。
- ⏸ **MCP 工具层(`mcp__godot__*`)验证延后**:配置已就绪(local scope `claude mcp get godot` ✔),需重启会话生效;用户选"到此收尾"(底层 WS 命令面已直验,边际价值低)。
- ⏸ **运行时 game_bridge 真截图延后**:需 `run_project` 弹游戏窗口(可见副作用);用户选"到此收尾"。

---

## 5. 设计库↔Godot 数据桥(中期,需用户定 schema)

- **缺口**:设计库 `Skill` 叙述字段,Godot 要数值;设计库无 export → 设计不喂引擎。
- **两条路(决策点)**:**(a)** 设计库模型补引擎数值字段(power/hit_bonus/range/area/cooldown/action_cost/timing_constraint/结构化 effects[])+ `/api/export/godot` 输出 Godot 格式 `data/skills/*.json` + **jsonschema 4.26.0**(Draft 2020-12,min/max 边界 + `additionalProperties:false`)门控 —— **利于 §1 功率评分 + §6 平衡 sim**;**(b)** 设计库存 `godot_json` blob 字段(手填引擎块 verbatim 导出,省双建模少结构)。**推荐 (a)**,数值字段正是平衡所需。
- **约束**:此项**改 SoT 设计库仓** → 按"不直接干预 SoT 仓"约束,**本 lane 只出设计/方案,实施由用户或经明确授权**。Workflow 侧可先做"读 设计库 API → 校验 → 生成 Godot JSON 草稿到暂存区"的只读管线。

---

## 6. 优先级待办清单

| 优先 | 待办 | 落点 | effort | 阻塞/前置 |
|---|---|---|---|---|
| **P0 ✅DONE** | `talent-validate.js` Workflow(L1 结构+规则冲突 + L2 组合 + L3 对抗;不含数值评分) — **2026-06-22 落地+dual-verify+实跑见切 reject,详见 §9** | `.claude/workflows/talent-validate.js` | 中 | ~~设计库只读 API~~ ✓ |
| **P0 ✅DONE** | gdtoolkit lint 门 + `godot-test` 包装(护栏化现有 headless harness) — **2026-06-22 落地+dual-verify+6 用例回归,详见 §9.5** | `scripts/gdlint-gate.sh` + `scripts/godot-test.sh` | 小 | 无(只读跑) |
| P1 | `pixel-asset-pipeline.js` Workflow(PixelLab MCP + Aseprite CLI + animate-frames 门) | `.claude/workflows/` + 可选 `adapters/pixellab/` | 中 | 选定工具账号(PixelLab/RD/Scenario)+ Aseprite 购置 |
| P1 | godot_mcp 安全接入 + live 测试 | 本 session MCP 配置 | 小 | **用户确认 6550 空闲**(关 opencode 连接) |
| **P2 ✅DONE** | 设计库数值字段扩展 + `/api/export/godot`(§5) — **2026-06-25 落地+dual-verify+NAS 部署 smoke,详见 §11** | SoT 设计库仓 `553d21a` | 中 | ~~schema (a)/(b) + 授权~~ ✓ |
| **P2 ✅DONE(内置)** | Monte-Carlo 平衡 sim — **2026-06-25 做成设计库内置 `/api/sim` + `/sim` 网页(参数化近似模型),NAS 部署 smoke 通过,详见 §11** | SoT 设计库 `app/sim/` | 中 | ~~数值字段~~ ✓;Godot headless 防漂移冒烟仍待 |
| P3 | gap-fill 构思:trigger×effect 覆盖矩阵 + embedding 相似度过滤(并入 talent-validate L4) | `.claude/workflows/` | 小-中 | talent-validate P0 先落 |

## 7. 待用户拍板的决策点
1. ~~**设计库 schema**:数值字段扩展 (a) vs `godot_json` blob (b)?~~ → **已决 (2026-06-22):选 (a) 补引擎数值字段 + `/api/export/godot`**(中期方向,利于功率评分+平衡 sim;改 SoT 设计库仓需授权;MVP 不阻塞)。
2. **像素工具账号**:PixelLab(角色,有 MCP)/ Retro Diffusion(VFX)/ Scenario(图标,月费)各开哪个?Aseprite(~$20)购置?是否需要 AI 视频生成路线(才用 Frame Ronin)?
3. **改 SoT 仓授权边界**:§5 设计库扩展 + §2.2 Godot 导入产出物 —— 哪些允许 Workflow 落仓 vs 必须用户手动?
4. **godot_mcp 接入时机**:何时 6550 空闲可做 live 测试?
5. ~~talent-validate **首个验证对象**?~~ → **已决+已跑通 (2026-06-22):见切 `ss_jianqie` 跑通(verdict=reject,详见 §9);其余 3 张(破釜沉舟/先之先/心眼·彻)待扩。**

## 8. 来源(节选,全 web-verified)
- **天赋验证**:Z3 SMT(MIT,`pip install z3-solver`);Imaginarium ACM 2020(dl.acm.org/doi/fullHtml/10.1145/3402942.3409605);Red-Teaming Game arxiv 2310.00322;LLM 评估宽容偏差 arxiv 2512.07462 / 2601.19726;G-KMS MDPI 2025(mdpi.com/2079-8954/14/2/175);Automatic Game Design arxiv 1908.01420。
- **像素素材**:PixelLab API(pixellab.ai/pixellab-api)+ MCP(github.com/pixellab-code/pixellab-mcp);Retro Diffusion(replicate.com/retro-diffusion/rd-animation);Scenario(scenario.com);Aseprite CLI(aseprite.org/docs/cli);gpt-image-2(developers.openai.com/api/docs/models/gpt-image-2);Frame Ronin(github.com/systemchester/FrameRonin,license UNVERIFIED)。
- **Godot 工具**:GUT 9.6(github.com/bitwes/Gut);GdUnit4 v6.1.3(github.com/godot-gdunit-labs/gdUnit4);gdtoolkit 4.5.0(pypi.org/project/gdtoolkit);headless gotchas issue #77508 / #43444;jsonschema 4.26.0(pypi.org/project/jsonschema)。
- **godot_mcp 握手**:只读审计 `addons/godot_mcp/websocket_server.gd`(close code 4002 takeover)+ `.mcp.json`(port 6550)。

---

## 9. 进度记录:talent-validate Workflow(2026-06-22 落地)

> main lane / ultracode(Workflow + agents team)。Workflow 文件 `.claude/workflows/talent-validate.js`(可 `/talent-validate` 复用)。

### 9.1 已落地(P0 完成)
- **架构(不含数值评分 MVP)**:4 层管线。
  - **Phase0 Adapt**(agent+Bash):从 设计库只读 API(本地 `http://127.0.0.1:8000`;NAS `sot.fyc-space.uk` 待 CF token **不可达**)或 dataDir fixtures 取候选天赋+同职业天赋(轻量索引)+tag 注册表(12)+规则表(5)+rarityCounts+candidateStoredRarity;数据落 Mercury `.mercury/tmp/codex-fixtures/`(**零写 SoT 仓**)。
  - **L1 结构验证**(脚本内**纯 JS 零 LLM**):schema 必填/枚举合法/tag∈注册表/规则引用悬空(正则带边界)/R6.6 史诗供给上限(归因区分"新增越界=error→reject"vs"池既有超限=warning→revise");+ Haiku 语义初筛(**advisory-only,不驱动 verdict**,符合"LLM 不做定量裁决")。
  - **L2 组合扫描**:脚本 JS 枚举与候选共享 tag 的同职业天赋(PAIR_CAP=20,溢出 log)→ 并行 Haiku triage(interaction_type/risk_level);仅 loop/amplifier@high|medium 计入 flagged(防 loop+none 误报)。
  - **L3 对抗批判**(串行双 Sonnet):Optimizer(agentType design)构滥用序列 → Defender(agentType critic)用现有**锁定**规则反驳;neutralized:false=确认 exploit。
  - **fail-closed**:任何 L2/L3 agent 失败→stageFailures 追踪→verdict 至少 revise(覆盖不全不读作 pass)。
- **护栏(#385)**:lean dispatch(传路径+任务,peer body 不 bulk 注入)/fan-out cap+log/Haiku 注入<<50K/只读 SoT。worst-case ≤34 agent(1 Adapt+1 L1语义+20 L2+2 L3,远低 800 自限)。
- **dual-verify**(跨仓库手动:`oh-my-claudecode:code-reviewer` + `mcp__codex__codex`,因 dual-verify skill 假设单仓库):双审高度一致,**9 类问题已修**——① R6.6 供给归因 bug(`candidateStoredRarity` 区分新增/已存)、② L2 risk_level 门槛(loop+none 误报 revise)、③ L3 fail-open(defender 失败静默放行最危险情形)、④ stage-failure 追踪、⑤ ADAPT_SCHEMA 必填(candidateStoredRarity/ruleCodes.status)、⑥ 悬空正则边界(`[a-z]*`+lookaround)、⑦ L1 semantic advisory 化、⑧ inline draft 文件名一致、⑨ 早退返回形状带 verdict。

### 9.2 实跑验证:见切 ss_jianqie(2026-06-22)
**verdict=reject**(22 agents / 844K tokens / 522s / stageFailures=[]全覆盖)。三层各抓到真实缺陷,**完整验证路线图 §1 诊断(纯 LLM 弱裁判会漏的,混合管线抓到了)**:
- **L1 确定性**(纯 JS,纯 LLM 必漏):① `R3.13c` 悬空规则引用(见切 rules 字段引用了规则表不存在的 R3.13c,ZOC 增伤治理无锚点);② R6.6 史诗池供给 7>6(归因正确:"池子既有超限,非见切导致;需设计层腾位")。
- **L2 组合**(18 triaged,5 high-risk):见切 × 影缝/辻斬/先之先/明镜止水(loop high)+ 破釜沉舟(amplifier high)。**agent 真读了 dataDir 设计文档**——引用了"钉影近永久束缚"、"Fell Seal 控制链先例"、"PR #491 影缝候选删除"、"R3.12 三桶制"等真实语料(证明 lean dispatch 有效)。
- **L3 对抗**(确认 exploit):Optimizer 构造 **见切(招架必暴)→影缝(斩击附钉影 MOV=0+不可位移)→辻斬(钉影态降级夹击:必中+产气)→明镜止水(满气保招架必成功=见切必触发)** 自锁永久控制链(win 2 回合,每环节均来自已有天赋文本,无需假设未声明机制);Defender 扫全部锁定规则(R1.1/R3.14/R3.12/R6.6)**无一能封堵**(无续接冷却/控制时长/时序禁令规则)→ neutralized=false;residual_risk 给具体修法(影缝加每场限次/续接冷却/退化一次性 debuff)。Optimizer 诚实列 8 条可证伪引擎假设(非过度自信幻觉)。
- **判定语义**:reject ≠ 见切设计失败,而是"见切在当前语料(含影缝草稿)下触发了现有规则无法封堵的永久控制链,需设计层加门控"。这正是 talent-validate 该产出的高价值信号(**独立复现了 SoT 设计评审已知的影缝控制链争议**)。

**扩跑验证:其余 3 张已审天赋(2026-06-22,3 Workflow 并行,各 20-22 agents / 760-840K tok / 全覆盖 stageFailures=[])**

| 天赋 | 稀有度 | verdict | L1 确定性 | L2 flagged | L3 win | L3 exploit 核心 |
|---|---|---|---|---|---|---|
| 见切 ss_jianqie | 史诗 | reject | R3.13c 悬空 + R6.6 | 5 | 2 回合 | 见切→影缝→辻斬→明镜止水 永久控制链 |
| 破釜沉舟 ss_pofu | 史诗 | reject | R6.6(已存) | 11 | 3 回合 | 影缝钉死+破釜必暴+心眼倍率+辻斬产气 自持螺旋 |
| 先之先 ss_xianzhixian | 传奇 | reject | **空(无 R6.6)** | 8 | 4 回合 | 多敌围攻每攻击触发先制必暴"越被打越强"+背水三桶叠满 |
| 心眼·彻 ss_xinyance | 史诗 | reject | R6.6(已存) | 13 | 3 回合 | 影缝钉影+见切必暴+辻斬产气+心眼高暴击+速度先手 |

**横向验证达成**:
- **R6.6 稀有度分支正确**:3 史诗→warning(已存归因,非候选导致);**先之先(传奇)→L1 deterministic 空**(非史诗不触发供给检查)——验证稀有度分支。
- **悬空检测阴阳性全对**:见切 R3.13c→悬空 warning(阳性);心眼·彻 R3.14 / 破釜沉舟 R1.1→无悬空(阴性,不误报合法引用);先之先 rules 是程序注记无 R 码→正则不误匹配。
- **L3 对抗 4/4 确认 exploit**(neutralized=false,win 2-4 回合),Defender 均扫全部锁定规则确认无法中和。
- **L2 互引一致**:4 张互相标 high-risk(见切↔破釜沉舟↔先之先↔心眼·彻),agent 真读设计 notes(引用"高剑气进背水自强化螺旋须门控"/"影缝近永久束缚"/评审 D 阈值门控),证明 lean dispatch 有效。

**核心洞察 — 影缝是剑圣职业线系统性漏洞**:3/4 的 L3 exploit 核心依赖**影缝(草稿)无冷却钉影续接**(永久 CC),先之先依赖背水剑气螺旋。4 张全 reject 的共同根因 = 影缝 + 剑气经济螺旋无门控,几乎任何产必暴/标记/产气的天赋都能接入同一控制链。**这不是管线缺陷,而是反映真实设计状态**(评审已建议砍影缝 / 落地剑气阈值门控,但均未成锁定规则)。talent-validate 独立、反复地指向同一根因 —— 正是混合管线该有的价值。

**区分度后续测试建议**:移除/修复影缝(+把剑气阈值门控落成锁定规则)后重跑 4 张,看 verdict 是否分化(区分"真独立 exploit"vs"仅靠影缝接入")——可验证管线区分能力。需改 SoT 语料(不在本 lane 范围,待 SoT 设计层处理)。另:**R6.6"新增史诗→error→reject"路径**(候选 storedRarity≠史诗)这 4 张(已存史诗/传奇)未覆盖,可用合成史诗 draft 单测验证。

### 9.3 用户决策(2026-06-22 拍板)
- MVP 范围 = **不含数值功率评分版**(已落地)。
- 设计库 schema 中期方向 = **(a) 补引擎数值字段 + `/api/export/godot`**(决策点 1;MVP 不阻塞,改 SoT 设计库仓待授权)。
- 首验对象 = **见切先跑通**(已完成),其余 3 张待扩。

### 9.4 后续待办(talent-validate 增量)
- [ ] 扩跑其余 3 张已审天赋(破釜沉舟 `ss_pofu`/先之先 `ss_xianzhixian`/心眼·彻 `ss_xinyance`);心眼·彻作 draft 新增史诗应触发 R6.6 error→reject(验证"新增越界"归因路径,与"池既有超限"区分)。
- [ ] **数值功率评分**(L1 增量):待 设计库补数值字段(决策点 1=a 实施后),加确定性功率预算评分(权重据现有平衡卡校准)。
- [x] **L4 gap-fill 构思**(§1.2/§6 P3):trigger×effect 覆盖矩阵 + embedding 相似度过滤,选空格子生成 1 个语料-schema-合法候选回灌 L1-L3。**2026-07-02 落地**:gapfill 模式并入 `talent-validate.js`(args `{"gapfill": true}`;分类 agent + 纯 JS 矩阵/选格 + 单 Sonnet 生成 + text-embedding-3-small 冗余筛 cos>0.85,endpoint 经 args/env 无硬编码,筛重不可用时 fail-closed 标 UNVERIFIED 强制 ≥revise);用法见 usage guide §C。
- [ ] Monte-Carlo 平衡 sim(Python,§6 P2)做数值平衡(L3 只抓逻辑 exploit,定量平衡需模拟)。
- [ ] (可选硬化)把 fixtures 拉取从 Adapt agent 内联化为脚本前置;加 设计库 API 分页/规则表完整性校验(防合法引用被误报悬空);Z3 可选增强(§1.3)。

### 9.5 次要任务:gdtoolkit lint 门 + godot-test 包装(2026-06-22 落地,P0 完成)
- **落点**:`scripts/godot-test.sh` + `scripts/gdlint-gate.sh`(Mercury-internal tooling,无 LOC cap;**只读跑 SoT,不改 SoT 仓**)。
- **gdtoolkit 4.5.0**(web-verified PyPI,纯 Python 无引擎;装 D 盘 venv `.mercury/tools/gdtoolkit-venv`,Python 3.14.3 兼容):gdlint(风格+语法)+gdformat --check(格式,不改文件)+gdparse(语法)。**实测+web 确认 gdlint/gdformat 目录递归**(无需 find 展开;gdparse 才需)。
- **godot-test.sh**:包装 Godot 4.6.1 headless SceneTree 测试。web-verified 护栏:#88055(quit 退出码,4.6.1 实测可信但仍**退出码+stdout 双判**)、#77508(import race,`--import` 预热默认 off 避免与活动编辑器竞争)、`GODOT_DISABLE_LEAK_CHECKS=1`、#43444(Godot 4 无需 xvfb)。**三态 verdict**:pass/fail/**inconclusive**(exit 0 但无可解析总结 → 拒绝默认 pass,fail-closed)。
- **dual-verify**(code-reviewer + Codex 跨仓库手动):核心修 **Codex HIGH false-pass**(FAILN 原只认中文 `失败`,英文失败行+exit 0 quirk 会误判 pass)→ FAILN 中英文+全角解析 + inconclusive 兜底;另修 gdformat/gdparse 缺失 fail-closed exit 2(不静默跳过门禁)、工具启动失败映射 exit 2、find 空遍历 surface、header 诚实声明(`--script` 只读性取决于被调脚本)。
- **6 用例端到端回归全绿**:① 见切 pass/0 ② no_summary quit(0)→inconclusive/1 ③ 英文 "1 fail" quit(0)→fail/1(false-pass 修复) ④ gdlint-gate SoT FAIL/1 ⑤ clean PASS/0 ⑥ 工具缺失→exit 2。
- **实测抓到 SoT 真实问题**(只报告,不改 SoT 仓):`test_swordsman_resources.gd` 有 4 处 mixed-tabs-and-spaces + 需格式化。
- **用法**:`GODOT_BIN=<godot> bash scripts/godot-test.sh res://tests/test_swordsman_resources.gd <sot_project>` ; `bash scripts/gdlint-gate.sh <file_or_dir>`。

### 9.6 可用性收拢(2026-06-22,用户马上投入天赋产出)
为让 talent-validate 即用:
- **启动脚本** `scripts/sot-codex-serve.sh`:一键起 设计库只读 API(DB 隔离 Mercury tmp,reuse 已运行实例,零写 SoT 仓)。
- **使用指南** `.mercury/docs/guides/talent-validate-usage.md`:两种调用(talent_id / talent_draft_json)+ 草稿字段 + verdict 解读 + 数据刷新。
- **draft 模式 bug 发现+修复**(talent_draft_json = 产出新天赋主用法):合成草稿"残月·试作"首跑暴露 bug —— Adapt 在 draft 模式误 curl 不存在的候选 id(得 404 `{detail:天赋不存在}`)写进候选文件,L2/L3 读到 404 而非草稿(L1 不受影响,用脚本变量)。**修法**:候选内容从脚本变量**内联**进 L2/L3 prompt(候选单张非 bulk,符合 #385;peer 仍读文件),Adapt draft 分支强化"不 curl 候选"。**修复后重跑确认**:L2 从"flagged 空+没分析候选"→"6+ flagged 且每个引用残月真实机制(+2 剑气/kill)",证明 L2/L3 现正确读草稿。**talent_draft_json 模式完全可用**。
- **R6.6 新增 error 端到端确认**:残月·试作(史诗,id 不在库)→ L1 `error: 新增史诗供给达 8 > 上限 6` → reject(对照已存史诗的 warning),验证稀有度归因双分支。
- **工作流**:① `bash scripts/sot-codex-serve.sh` 起服务 → ② 喂 talent_draft_json 草稿 → ③ 读 verdict + L1(确定性可信)/L2(组合)/L3(对抗)。
- **待办**:talent-validate.js 的 draft 修复(4 处内联候选)**尚未 dual-verify + commit**(实证已通过重跑,静态 dual-verify 待补);产出仍在 working tree。

---

## 10. 方向转向:talent-validate 从 Mercury 本地 Workflow → SoT 设计库 网站内置交互式审阅(2026-06-23 用户提出)

> 用户诉求:本地 CLI 调度割裂麻烦;想在 https://sot.fyc-space.uk/ 网页内投入天赋 → 现场审核 1/多张 → 对话反馈 → 改 → 重审。**这是改 SoT-fyc-space 仓**(硬约束需用户授权;用户 2026-06-23 提出=倾向授权,待明确点头)。

### 10.1 可行性(web-verified 2026-06-23)
- **Claude Agent SDK**(`claude-agent-sdk-python` / `@anthropic-ai/claude-agent-sdk`,<https://docs.claude.com/en/api/agent-sdk/overview>):Python+TS,跑任何 Node/Python runtime,自带 subagents/持久 sessions/MCP/human-in-loop。**完全能嵌 SoT 设计库 FastAPI 后端**。
- **认证/成本(关键)**:
  - 订阅 OAuth token(Pro/Max)**仅限 Claude Code + claude.ai;第三方应用用=违反 Anthropic 消费者 ToS**(<https://support.claude.com/en/articles/9876003>)。
  - **但 2026-06-15 起新政**:Claude 订阅(Pro/Max/Team/Ent)有**月度 Agent SDK credit**($20-$200/计划),**覆盖第三方基于 Agent SDK 的应用**,按 API 费率计费、固定不滚存、用完另买(<https://support.claude.com/en/articles/15036540>)。→ **可合规用订阅 Agent SDK credit 供能**,额度有限;或用 Anthropic Console **API key**(按量无上限)。

### 10.2 架构(高层移植路径)
1. **L1 确定性层** → 移植成 SoT 设计库 Python 函数(FastAPI 内,纯代码零 LLM 零成本):schema/tag 合法/悬空引用/R6.6 供给。逻辑见 `talent-validate.js` `runL1Deterministic`,数据已在设计库(Talent/Tag/Rule)。**立即可做,零 API 成本**。
2. **L2/L3 LLM 层** → Claude Agent SDK(Python)在 FastAPI 后端编排(L2 Haiku triage / L3 双 Sonnet 对抗)。用 Agent SDK credit / API key。可**精简 agent 数**降成本(本地 Workflow 22 agents 为彻底,web 版可少)。
3. **交互前端** → 设计库加审阅页:投入天赋 → 审核 1/多张 → **Agent SDK session 持久对话**实现现场交流/反馈/迭代改/重审。
4. **成本估算待做**:单张本地 ~800K token;web 精简版应更低;按 API 费率算月用量 vs $20-$200 credit。

### 10.3 决策点(用户 2026-06-23 已全部拍板)
1. ✅ **授权 Mercury 直接改 `SoT-fyc-space` 仓** + 完成后**重新部署到 NAS**(sot.fyc-space.uk)。
2. ✅ **认证(双 provider fallback)**:主 = Claude 订阅自带 Agent SDK 月度 credit(沿用当前订阅),模型 `claude-opus-4-8`,effort **xhigh**;备用 = **OpenAI API key(复用 Argus 环境/配置里的 key,不在文档明文)**,模型 `gpt-5.4`,effort **xhigh**。后端实现"Claude 主→额度耗尽/失败时 fallback GPT"。
3. ✅ **范围(分阶段)**:先 L1 纯代码移植(零 AI 成本、立即可上),再 L2/L3 Agent SDK。
4. ✅ **谁落仓**:Mercury 直接改 `SoT-fyc-space` 仓 + NAS 重部署。
5. ✅ **命名**:弃用"Codex"称呼(易与 OpenAI Codex 混淆)→ 全改称 **"SoT 设计库"**(指 `SoT-fyc-space` 的 FastAPI 应用,部署 sot.fyc-space.uk)。

**实施注意(新 session 须 web-verify)**:Claude Agent SDK 原生用 Claude 模型;GPT-5.4 备用的集成方式(OpenAI SDK 直调 fallback,还是 Agent SDK 支持多 provider)+ `gpt-5.4`/`claude-opus-4-8` 模型 ID 可用性 + Agent SDK Python 包版本/API 签名,均须实施前 web-verify(强制研究协议)。

### 10.4 注意
- `talent-validate.js`(Mercury Workflow)仍是**原型/参考实现**,L1/L2/L3 逻辑 + 5 张实跑验证可直接复用。
- 这是**新工程**(超当前 talent-validate MVP),建议新 session 专做(本 session context 已满)。
- 强制研究协议:实施 Agent SDK 代码前须再 web-verify SDK API 签名/版本(本节为方向性 verify)。

### 10.5 实施进度(2026-06-23 session)

**Phase 1 — L1 确定性校验内置 ✅ DONE**(SoT-fyc-space `master` `54d008a`,本地仓无 remote):
- 三层模块化:`app/validation/l1.py`(零依赖纯逻辑,忠实移植原型 `runL1Deterministic`)← `app/validation/context.py`(DB 装配)← `app/api/validation.py` + `app/web/pages.py`(HTTP/HTMX)。
- 5 类检查:schema 必填 / rarity·damage_type·status 枚举合法 / tag∈注册表 / 悬空规则引用(正则带边界)/ R6.6 史诗供给(新增越界=error、池既有超限=warning、**跨职业迁移正确归因** —— Codex dual-verify 抓的 HIGH)。
- 3 端点:`POST /api/talents/validate`(草稿 dry-run)、`GET /api/talents/{id}/validate`、`POST /talents/validate`(网页「结构校验」按钮 HTMX 即时反馈)。
- **关键设计约束**:validate 输入用纯 str(非枚举类型),否则 FastAPI 422 拦截会吞掉「检测非法枚举」这个 L1 职责;web 校验路由注册早于 `{talent_id}` 参数路由。
- **24 pytest 全绿**(纯函数 14 + API/web 端到端 10)+ live uvicorn smoke 通过。**dual-verify 闭环**:code-reviewer APPROVE(7 约束全 PASS)+ Codex 初版 HIGH(跨职业归因)→ 修复(`candidate_stored_class_id`)→ re-verify 同意合并 + 采纳 reviewer MEDIUM(枚举 drift guard test、notes 三入口统一)。
- 测试基础设施:`requirements-dev.txt`(pytest+httpx)、`tests/conftest.py`(临时 DB,不碰 SoT 仓磁盘)。本地 `.venv` = CPython 3.14.3;Docker 部署 = python:3.12-slim,代码 3.12 兼容。

**Phase 2 方向重大转向(用户 2026-06-23 拍板,推翻原 handoff 假设)**:
- ❌ **不用 Claude / Anthropic / Agent SDK**;✅ **全用 OpenAI**(`gpt-5.4` + 纯 `openai` Python SDK)。架构从「Claude 主 + OpenAI 备双 provider」**简化为单 provider OpenAI**(无 fallback 层)。
- **认证驱动**:web 研究(7 agent / 308K token,全 web-verified)证实 **2026-06-15 订阅 Agent SDK credit 新政已于 06-16 取消**(来源 support.claude.com/en/articles/15036540 + digitalapplied.com/blog/anthropic-claude-credit-overhaul-june-15-2026),第三方应用合规上只能用 API key;用户选定全 OpenAI。

**Phase 2 — L2/L3 OpenAI 集成(✅ DONE,commit `a4e4027`,dual-verify 多轮闭环)** web-verified 事实(来源附下):
> Phase 2 完成:`app/validation/llm.py`(L2/L3 编排)+ `context.build_review_context` + `app/api/review.py`(async 端点 + `synthesize_verdict` + `require_token` 鉴权);**44 pytest 全绿**。dual-verify:Codex(CRITICAL `_call_json` 空内容降级 / HIGH optimizer 失败不 surface / 空洞 fail-open `sequence=[""]`、空白中和 / 幻觉规则码)+ code-reviewer(HIGH `/review` 缺 `require_token`)**全修闭环**;fail-closed 加固=空内容/拒答/非法JSON/缺键/空 rationale/无锁定规则引用的中和 一律 failed/undetermined。**真实 gpt-5.4 调用未本地验证**(本地无 OPENAI_API_KEY,Codex 走 ChatGPT 登录),留 NAS 部署 smoke。**follow-up(不阻断 MVP)**:连接池跨 LLM await、同步查询阻塞事件循环、模块级 env 常量 import 固化、L3 corpus 无上限截断、L3 规则码可加状态二次核验。

**🚀 NAS 部署完成(2026-06-23,真实 gpt-5.4 端到端验证成功)**:代码同步 NAS `sot-codex/`(NAS 无 git → tar over ssh)+ `OPENAI_API_KEY` 复用 argus `.secrets.toml`(写入 sot `.env`)+ docker compose rebuild(openai 2.43.0)+ restart(`sot-codex-app-1` `:8400` override,Cloudflare Tunnel → sot.fyc-space.uk;socket `/var/run/system-docker.sock`,docker compose v2.29.1-qnap2)。**真实 smoke**:L1 validate=revise(R3.13c+R6.6,与本地一致);**L2/L3 review 见切=reject + L3 confirmed_exploit=true + stage_failures=[]**——真实 gpt-5.4 独立复现原型「见切永久控制链」exploit 结论。容器内最小 gpt-5.4 调用确认 key/模型ID/reasoning_effort 扁平/response_format strict 全工作。**性能问题**:L3=high cap=5 单张 review **~247s**(接近超时);生产 `.env` 已加 `SOT_LLM_L3_EFFORT=high` / `SOT_LLM_L2_PAIR_CAP=5`。**⚠️ Phase 3 前端必须异步**(后台任务 + 轮询/SSE,不能同步等 ~4min HTTP)或进一步降 effort / 截断 L3 corpus(无上限,token 随职业天赋数膨胀)。回滚:`sot-codex/app.backup-pre-validate`(旧 app 备份)。
- 包:`openai` 2.43.0(2026-06-17,PyPI),`AsyncOpenAI`,Python≥3.9。env `OPENAI_API_KEY`。
- 模型:`gpt-5.4`(developers.openai.com/api/docs/models/gpt-5.4):ctx 1,050,000 / out 128K / **$2.50 input·$15 output per MTok**(缓存输入 $0.25)/ **>272K token 触发 2x 输入·1.5x 输出惩罚**。alias `gpt-5.4-2026-03-05`。
- **reasoning effort**:Chat Completions 用**扁平字符串** `reasoning_effort="high"`(合法 none/low/medium/high/xhigh);嵌套 `reasoning={"effort":...}` 是 Responses API 写法(gapCheck 关键纠错,来源 developers.openai.com/api/docs/guides/latest-model)。
- **结构化输出**:`response_format={"type":"json_schema","json_schema":{"name":...,"strict":True,"schema":{...,"additionalProperties":False}}}`,或 `.parse()`+Pydantic(developers.openai.com/api/docs/guides/structured-outputs)。
- 架构(移植原型 L2/L3):L2 = 代码枚举共享 tag 对 → 并行 `gpt-5.4` triage(JSON);L3 = 串行 Optimizer→Defender(JSON)。新端点 `POST /api/talents/{id}/review`(async,L1+L2+L3);`validate` 保留 L1-only(快、零成本)。成本控制:web 版 L2 pair cap 比本地 22-agent 小(~8-10);单张 review 估 ~$0.1。
- **key 路径决策**:代码从 env `OPENAI_API_KEY` 读(勿明文);本地无 key(Codex 走 ChatGPT 登录,Argus key 在 NAS)→ **mock 单元测试覆盖编排逻辑** + **真实 LLM smoke 放 NAS 部署阶段**(SoT 容器与 Argus 同 NAS 环境共享 key)。
- **部署**:用户选「等 L2/L3 完成一起部署 NAS」,Phase 1 暂不单独上线。

**Phase 3 — 交互审阅前端 ✅ DONE(2026-06-24,SoT master `619f167`,NAS 部署+真实 gpt-5.4 smoke 全绿)**:投入→异步审核→对话反馈→帮改→重审→显式落库全闭环。详细实现规格 + dual-verify 修复见 `sot-phase3-review-ui-plan-2026-06.md`(§6 七项对抗式批判修正 + §11 实施完成 + dual-verify 9 项修复)。
- **异步**:DB 持久 ReviewSession/ReviewJob + `asyncio.create_task` + HTMX 短轮询(每 2s)。**不用 SSE**(web-verified:CF Tunnel 对 SSE ~100s 超时+~100KB 缓冲对 247s 任务结构性失效);**不用 BackgroundTasks**(无 job 句柄)。持久会话 = **Chat Completions 自存 messages(存 DB),不用 Responses API**(30 天 TTL + 跨重启需 fallback 全量历史,既然都得自存就直接自存)。streaming 未用(轮询替代)。
- **对抗式设计 Workflow**(`wf_cf1d0c3a-8a2`,11 agent:4 研究+3 架构+1 评判+2 批判,冠军=草案3 UX优先嫁接草案2稳健+草案1复用) + main-loop web-verify 5 技术点(htmx 停轮询/FastAPI create_task/asyncio 弱引用 GC/CF Tunnel SSE/SQLite WAL connect listener,全附官方来源) + 跨仓库 dual-verify(code-reviewer + Codex MCP,双路确认 7 修正真实实现 + 9 项 findings 全修)。
- **测试**:71 pytest 全绿(44 现有零回归 + 27 新) + 本地 live uvicorn smoke + **NAS 真实 gpt-5.4 端到端 smoke 全绿**(见切 ss_jianqie 异步审阅 ~144s→驳回+L3 确认 exploit 复现 Phase 2;帮改对话真实提案;commit H3 门拦住;数据纪律 Talent updated_by=None 验证;停轮询 done 片段 0 trigger)。
- 生产 `.env` 沿用 `SOT_LLM_L3_EFFORT=high`/`SOT_LLM_L2_PAIR_CAP=5`,新 `SOT_REVIEW_*` 全安全默认(require_token 默认 ON,复用现有 API_TOKEN 作网页审阅令牌)。回滚 `sot-codex/app.backup-pre-phase3`(本次部署创建,= Phase1/2 状态)。
- **遗留 follow-up(非阻断)**:① 后台协程跨 session 可见性随 NAS smoke 间接验(WAL+busy_timeout);② LLM 输出入 HTML 属性 XSS 边界(autoescape 默认开 + 已改单引号属性,可后续用含 `<script>` reply 专测);③ `progress_detail` L2 细粒度进度未写(功能缺口非 bug);④ 派发端点 async 内同步 DB 查询轻微占事件循环(poll 是 def/线程池不受影响,低流量可接受)。

---

## 11. §5 数值字段桥 + §6 P2 平衡 sim + 去 Codex 命名（2026-06-25 落地）

**全部 DONE + dual-verify + NAS 部署 smoke 全绿**（SoT 设计库 master `553d21a` + 总结文档 `9e69929`）。详细产出总结（递交 SoT 组）= SoT 仓 `docs/engine-numerics-export-sim.md`。

**A. 数值字段 + `/api/export/godot`**：Skill 加 `engine_json`（完整 Godot 引擎块 JSON）+ jsonschema(Draft 2020-12, web-verified 4.26) 校验门（API create/patch + 网页 + 导入/seed 全写路径）+ `/api/export/godot` 三分区 fail-closed 导出 + dry-run 校验 + 技能页编辑器。**关键设计判断**（已在总结标 SoT 组裁决）：勘察 Godot `data/skills` 发现引擎层与设计层 taxonomy 不同（英文枚举）+ 职业专属字段（剑圣 qi/mark 经济）→ §5(a) 字面"显式列"务实改为"单 engine_json 块 + jsonschema 逐字段门控"。

**B. Monte-Carlo 平衡 sim**：做成设计库内置 `/api/sim/skill` `/api/sim/class` + `/sim` 网页（用户选内置非独立脚本）。参数化近似战斗模型（命中/暴击/防御/crit_damage_bonus/cooldown/effects dot/splash）→ 伤害分布/TTK/胜率 + 横向离群标记。**模型是近似非引擎复刻**（SoT KB = Obsidian `ShipOfTheseus-KB/` 有权威公式：格挡暴击互斥/pure 规则/无距离衰减/ZOC，可后续细化）。

**C. 去 Codex 命名**（用户特许改 SoT KB）：「SoT Codex」→「SoT 设计库」（app/模板/README/CSS + Godot dev_doc + Mercury roadmap/talent-validate-usage）。**消歧**：保留运营标识符（codex.db/docker 网络/sot-codex 目录）+ AI 工具引用（codex-cli/Codex dual-verify/GPT-5.3-Codex）。Obsidian KB 经查无设计库义 Codex（全 AI 工具，正确保留）。

**质量**：对抗式设计判断 + jsonschema 4.26 web-verified + **dual-verify 双路（code-reviewer + Codex MCP）闭环 8 修复**（splash 伤害归属 / **现有 codex.db 缺列迁移启动幂等 ALTER** / DOT chance=0 / median=0 离群抑制 / 导入校验 / PATCH null / n_runs=0 / sim.html XSS）+ **107 pytest 全绿**（71 现有零回归 + 36 新）+ **NAS smoke**（迁移补列验证 + export 三分区 + sim 真实往返 ss_zhanji win=1.0/dmg=741）。回滚：`sot-codex/db-backup-pre-engine/`（codex.db+wal+shm）+ `app.backup-pre-engine`。

**遗留**：① §5(a) 偏离待 SoT 组裁决；② sim 模型可据 KB 权威公式提保真（剑气印记/格挡暴击互斥）；③ Godot headless 防漂移冒烟（§6 P2 后半）仍待；④ 数值功率评分（§9.4 / L1 增量）现已解锁（engine_json 有数值）可做。

---

## 12. Skill 数值层增强包：§5a 显式列 + #4 功率评分（2026-06-25 实施）

> main lane / ultracode。用户裁决:§5(a)=**通用字段加显式列**;#4=**路径 X Skill 级功率评分**。两者紧耦合,统一实现为一个增强包,一次 dual-verify + 一次 NAS 部署。改 SoT 设计库仓(已授权)。

### 12.1 关键勘察发现(推翻 §11 遗留 ④ 乐观假设)
- `engine_json` 挂在 **Skill(基础技能)**,**不在 Talent(天赋卡)**。talent-validate L1 校验的是 **Talent**(candidate=talent dict,rarity/rules/effect 叙述,**无数值字段**)。
- 原型 `talent-validate.js` 自声明 "Numeric power-scoring intentionally OUT of MVP (narrative layer has no numeric power field)"。
- §1.2 原意"功率评分超**稀有度**上限"是 Talent 维度,但当前数值只在 Skill 层 → #4 只能先做 **Skill 级(路径 X)**;Talent 级(路径 Y)阻塞在天赋数值化(天赋多 buff/被动/skill_upgrade,难数值化),留后续。

### 12.2 Part A — §5(a) 通用字段加显式列
- **engine_json 仍单一事实源**(权威);通用标量字段镜像成 Skill `eng_*` **派生列**(便于 SQL 查询/排序/过滤 + 喂 #4 评分)。空/非法 engine_json → 列全 NULL。
- 列清单(GODOT_SKILL_SCHEMA 通用字段拍平):`eng_damage_type/eng_power/eng_hit_bonus/eng_cooldown/eng_action_cost/eng_timing_constraint/eng_range_type/eng_range_min/eng_range_max/eng_area_type/eng_area_size/eng_effect_count`。**effects/tags 数组 + 职业专属字段(qi/mark)不拍平**(仍留 engine_json,避免 20+ nullable 列脆弱)。
- 同步:`godot_export.derive_engine_columns(engine_json)->dict` 在每个写路径(skills.py create/patch、seed.py、import)解析填列。迁移:db.py `_ensure_columns` 幂等 ALTER 登记新列(仿 engine_json ALTER)。

### 12.3 Part B — #4 Skill 级确定性功率评分
- **`app/sim/power_score.py` = montecarlo 的确定性期望闭式版**:复用 `_def_for_type/_clamp/_num/_effects_expected_dot`(零漂移,单一战斗模型事实源),但算**期望值非 RNG 采样**(快+确定+可复现+未来可作校验门)。
- 评分公式(对默认 Target/CombatParams):`expected_primary = base_attack×power/100 × mitig × hit_prob × (1 + crit_prob×(crit_mult−1))`;`per_use = expected_primary + expected_dot×hit_prob + expected_splash`;**`power_score = per_use/(cooldown+1)`**(每回合期望功率,体现 CD 折扣=持续输出)。
- 端点 `/api/score/skill` + `/api/score/class`(仿 sim.py):横向按 power_score 排序 + 离群(ratio≥1.5 high/≤0.5 low,可按 action_cost 分组)。网页 `/score` 可选。
- **vs sim 区别**:sim=蒙特卡洛随机分布(p10/p90/胜率/TTK,全面但慢+RNG);功率评分=确定性期望标量(快速横向筛+可作门)。两者复用同一战斗模型不漂移。pure 暴击固定/格挡互斥/剑气经济仍属高保真域(遗留 ②),功率评分同 sim 不纳入。

### 12.4 实施进度（2026-06-25 实现+测试+dual-verify DONE，SoT 设计库 master `ec74ff1`；NAS 部署待执行）
- **Part A（§5a 显式列）**：models.py 加 12 个 eng_* 派生列;`godot_export.derive_engine_columns`;db.py 启动迁移幂等 `ALTER ADD COLUMN` + `CREATE INDEX IF NOT EXISTS` + 回填现有行;同步覆盖**全部写路径**(API skills create/patch、网页 pages 两 form、seed/import)。
- **Part B（#4 功率评分）**：`sim/power_score.py`(montecarlo 确定性期望闭式版,复用 `_def_for_type/_clamp/_num/_effects_expected_dot` 零漂移);`api/score.py` `/api/score/skill`+`/class` 横向离群;main 注册。
- **测试**：**139 pytest 全绿**(107 现有零回归 + 32 新)。
- **dual-verify（对抗式双路）**：Claude code-reviewer 3 维 Workflow(`wf_6da7924e-32f`) + Codex 跨仓库手动 → **9 findings → 8 修复闭环**(① parse_engine 非 str 守卫 ② backfill schema 非法守卫(脏库防御) ③ 升级表补建 index=True 派生列索引 **medium**(否则查询退化全表扫描,击穿"加列换性能"卖点) ④ score_skill cooldown 负值兜底 ⑤ eng_effect_count 只计 dict 项 ⑥ resolve_engine 提公开消除 score 对 sim 私有符号耦合 ⑦⑧ 注释纠正评分不读列)+ **1 DISAGREE-cite**(Codex round 精度 low,与既有 sim_class 离群模式一致,影响 <0.2%)。
- **关键发现（已记 §12.1）**：engine_json 在 **Skill** 不在 **Talent** → #4 只能 Skill 级(路径 X);Talent 级(原 §1.2"稀有度功率预算"意图)阻塞在天赋数值化(天赋多 buff/被动/skill_upgrade),留后续。
- **文档**：SoT 仓 `docs/engine-numerics-export-sim.md` §7(递交 SoT 组)。
- **NAS 部署 ✅ DONE(2026-06-25)**：tar over ssh 同步 `app/` → `docker compose build app` + `up -d`(rebuild,Dockerfile COPY app 进镜像故须 rebuild 非 restart)→ 启动自动迁移 smoke **全绿**：**12 个 eng_* 列 + 3 索引**(`ix_skill_eng_damage_type/power/action_cost` —— 验证 dual-verify 索引 medium 修复在**升级库**生效)+ **回填现有技能**(`ss_zhanji` eng_power=180/damage_type=physical);`/api/score/skill` 真实往返 **power_score=66.0**(与 pytest 逐位一致);app `startup complete` 无 traceback。回滚备份:`sot-codex/db-backup-pre-engine2/`(codex.db+wal+shm) + `app.backup-pre-engine2/`。
  - **NAS docker 调用补全(此前 KB 只记 socket+compose 版本,现补完整命令)**:QNAP Container Station docker = `/share/CACHEDEV1_DATA/.qpkg/container-station/usr/bin/docker -H unix:///var/run/system-docker.sock`;**必须 `export DOCKER_CONFIG=<可写目录>`**(否则 `.docker/config.json` permission denied 致 `docker compose` 子命令"not a docker command");392fyc 在 `administrators` 组(gid 0)可直接读写 system-docker.sock **免 sudo**。ssh 入口 `ssh.fyc-space.uk`(cloudflared access,token 需浏览器认证;**非交互 Bash 环境须 `dangerouslyDisableSandbox` 才能建隧道**)。

### 12.5 SoT 组回执(2026-06-25,用户转达)— 方向调整
1. **§2 偏离 ACCEPT**:engine_json+jsonschema 优于 20+ 显式列(引擎异质 + 职业专属,每加职业改库不可取)。**注**:本 session 已部署 = engine_json(单一事实源)+ 12 个【通用】派生列(非职业列堆叠,职业专属仍留 json) → **兼容此回执**(engine_json 为核心);12 派生列仅只读 SQL 镜像、#4 评分不依赖,**保留 vs 回退纯 engine_json 待用户定**(回退零评分影响)。
2. **Source-of-truth = Godot repo 权威**;设计库 engine_json 作【只读镜像】、定期从 repo 回填;**暂不启用 `/api/export/godot` 回灌 Godot 仓**(避免覆盖 repo 侧 QA)。→ 数据流反转(原「设计库→导出 Godot」作废);export 端点保留但暂不回灌;需后续 **Godot `data/skills`→设计库 engine_json 回填管线**。
3. **stale**:现有 5 张剑圣 engine_json 早于近期 QA(斩击产印记 / 拔刀溅射 50%+暴击 3.0x / 一闪 AoE),回填前视为过期;eng_* 忠实镜像故同步过期(repo 回填后自动刷新);卡片文本(斩击「转职后」/ 拔刀 ×1.5)待与现行引擎对齐。
4. **sim=离群初筛**;剑圣精校走 KB 公式 + Godot headless(= 遗留 #2/#3)。
5. **格挡保留方案不实装**(用户):sim/power_score 本就不含格挡,符合。
6. **天赋暂缓**(用户):Talent 级功率评分(路径 Y)+ talent-validate L2/L3 增量,等用户设计天赋后再做。

**pure 暴击固定 1.5x 数据源(用户问,已核实)**:**权威 = Godot 引擎 `scripts/core/damage_calculator.gd`**(`:54`/`:122` `enable_pure_crit` gate + `:81` 暴击固定 1.5x 不吃 crit_damage_bonus;**pure 暴击默认 disabled**,仅技能显式 `enable_pure_crit=true` 才有暴击且固定 1.5x),**2026-06-21 Godot headless 验证**(KB `swordsman-engine-verification-2026-06-21.md:48`);同步记录于 Godot `CLAUDE.md`/`AGENTS.md`/`.cursorrules` + KB `battle-calculation.md:203` + 设计库 `seed.json:536` 规则表。即 Godot repo(SoT 组②的 source-of-truth)实装事实,非臆造。正本 `rules.md`/`final-design.md` 未找到(Glob 无;引擎代码是更硬的 SoT)。**当前 sim/power_score 未实装 pure 规则**(仅列为模型边界),实装走遗留 #2 精校。

**数量门槛(用户问)**:① 单技能【绝对评分】(`score_skill`/`sim_skill` 给单张 power_score/TTK)现可用,不需基准;② 【横向离群筛查】(`score_class`/`sim_class` 比中位数标 high/low)需多张同职业数值化技能(经验 ≥5-8 张才稳),当前库仅 1 张(ss_zhanji,且 stale) → 横向暂无意义。**建议下一步 = Godot `data/skills/*.json`→设计库 engine_json 回填管线**(只读 Godot repo,一次性数值化全部剑圣技能 + 用新鲜 QA 覆盖 stale + 实现②只读镜像 + 解锁横向量化)。

### 12.6 SoT 设计库网页 UX/认证改进待办(2026-06-25 用户反馈,下个 session 考虑方案)
> 性质:设计库**软件功能**改进(属 Mercury 工具层,非游戏数据内容)。本 session 仅记录不执行。
> **核心主线 = 做网页认证用户库**(点 1/2/3 共同解):用 CF Access 登录态(`Cf-Access-Authenticated-User-Email` header)+ 用户库(当前仅「用户本人 CF 邮箱」+「Claude/agent」两个)替代手填 token / 手填 updated_by / 手选 author。

1. **X-API-Token 网页输入别扭**:现状 = `base.html` 右上角 🔑 录入框,给 Phase 3 深度审阅(L2/L3 LLM)端点用(`require_review_token`,公网防 gpt-5.4 烧钱),存 localStorage 后全站 htmx 自动注入 `X-API-Token`。**改进**:网页改用 CF Access 登录态鉴权 → token 只留纯 API/agent 调用,网页不再手填。
2. **updated_by 自动获取**:现状 = 网页手填文本框(`talent_new`/`talent_detail`),无自动;`review_ui` 写「审阅器」。**改进**:读 CF Access `Cf-Access-Authenticated-User-Email`(CF 限制当前仅用户本人唯一邮箱可登录)→ 用户网页写=登录邮箱;agent/API(带 token)=agent 标识;去掉手填框。
3. **comment author 标签 vs 用户库**:现状 = 自由文本下拉(「我」/「Claude」,`pages.py _COMMENT_AUTHORS`)。**改进**:统一到认证用户库(同点 2 来源),不再手选标签。方案下个 session。
4. **校验/审阅按钮位置**:现状 = 结构校验(L1)+ 深度审阅(L2/L3)按钮在「保存」旁(填表容器内)。**改进**:移到**右侧独立区**,点击跑对应 agent + **独立窗口交流**(脱离填表容器)。
5. **Rule code/version**:`code`=规则编号(R3.14,设计规则标识符,天赋 `rules` 引用它 + L1 查悬空),**非代码**——录新规则时填编号,纯浏览可空(但 Rule 行 code 空则无法被引用)。`version` **不该用户填**,应系统管理、**仅当规则修改且实际反应到游戏代码时更新**。**改进**:version 移除手填/自动化。
6. **Term.links 关联手填**:现状 = 术语卡片有「关联」(`Term.links`)手填。**改进**:移除手填(术语关联大量天赋/技能/规则,无尽头);改为**引用方反向对照**(天赋/技能引用术语时自动关联)。
7. **sim 页用途 + 呼出方式**:sim(`/sim` Monte-Carlo)定位 = **技能数值横向离群初筛**(非天赋设计直接工具;需多张数值化技能才有意义,现仅 1 张)。**改进**:① 不独立页(割裂工作流),改**任意页面可呼出浮层/侧边抽屉**;② 明确用途标注(技能数值平衡工具,非天赋设计)。
- **遗留状态更新**：§11 遗留 ①(§5a 裁决)→ 已裁决"通用字段加显式列"且落地;§11 遗留 ④(数值功率评分)→ Skill 级已做,Talent 级待天赋数值化。
