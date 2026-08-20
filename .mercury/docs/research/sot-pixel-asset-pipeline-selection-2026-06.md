# SoT 像素素材管线选型研究报告（2026-06-30）

> **性质**：SoT（Ship of Theseus）游戏开发的**像素素材管线**选型研究。像素管线属 **Mercury 仓**域（工具/skill/adapter 落 Mercury），产出的**素材文件落 SoT 暂存区由用户导入**，本研究阶段**只研究 + 选型，不实现、不部署**。
>
> **硬约束**：
> - 不直接干预 SoT 仓（`D:\ShipOfTheseus\Ship_of_Theseus` / `SoT-fyc-space`）；勘察只读。
> - 强制研究协议：官方文档优先、记 source URL、无法验证标 UNVERIFIED、不编造定价/版本/API。
> - 外部 adapter ≤200 LOC；有官方 MCP 优先 MCP 免 adapter。
> - 上游基线：roadmap §2「痛点2 — 像素素材」（旧付费方案，本报告作对比基准重新权衡，非照搬）。

---

## 1. 任务背景

为 SoT 战棋 RPG（Godot 4.6.1，`AnimatedSprite2D` 单位）搭一条**能稳定产「引擎级一致性」素材的像素管线**。

用户倾向（第一轮决策）：① 预算 = gpt-image-2 已有 key 免费用 + 可接受 ~$30 一次性，担心质量管控 + 后处理麻烦；② 首批资产 = 角色多帧动画 + UI 图标；③ 质量门槛 = **引擎级一致性**（最高档）；④ **不走** AI 视频生成路线。

已有自产基座（Mercury 仓）：`adapters/gpt-image-2/`（MIT 挂 `wuyoscar/gpt_image_2_skill`，uvx 锁 SHA）→ `scripts/image_gen/`（character-bible 契约 + 参考链 + 验证）→ `animate-frames` skill（#351，帧数/尺寸/调色板量化/dHash 一致性/可选闭环 五验证门 + 重试）。Phase 3（LoRA/动作评分）未做。

---

## 2. SoT 现状勘察（只读，2026-06-30）

| 勘察项 | 结论 |
|---|---|
| **美术资源** | **完全为空** —— 无任何 png/svg/webp/ase 图片、无 `.tres`（SpriteFrames）、无 assets/art/sprites 美术目录。SoT 当前是**纯逻辑/无美术战棋**（占位在跑）。 |
| **唯一已有素材** | 主角剑圣红发剑士 sprite（rika/Gemini 产），在 `D:\ShipOfTheseus\resource` 暂存区，**未进 Godot 仓**。 |
| **棋子形态** | `scenes/tactical/Unit.tscn` 用 **`AnimatedSprite2D`** → 需 SpriteFrames 多帧资源（Aseprite Wizard 目标格式）。 |
| **棋盘视角** | `assets/prototype/viewport_angle/` 仍在 **`B1 Fake 3/4 方格` vs `C 等距 Isometric`** 之间对比**未定** → 影响棋子需几个朝向。 |
| **UI 图标尺寸** | skillbar 物品格 ~**38px**（技能/状态/物品图标）。 |

**定性**：不是「补几张图」，是**从零搭管线**，首批 = 角色多帧 + 棋子 + UI 图标。

---

## 3. 质量基线（已有产出参照）

| 产出 | 风格 | 关键观察 |
|---|---|---|
| `rika_9512f411` / `rika_fa8c464f`（rika） | **anime 高清多帧立绘** | 同一红发剑士跨帧/多姿势**一致性很好**（服装/配色/剑/红发稳定）。rika 强项。 |
| `Gemini_Generated_*`（通用模型） | **真低分辨率像素**单张 | 清晰像素网格 + 有限调色板 + 干净轮廓，**单张质量高**；但单张 ≠ 多帧序列一致。 |
| `perfect-pixel-scaled-x3` | anime→真像素后处理 | 像素化转换（3× 放大）的尝试产物。 |

**核心张力**：「引擎级一致性」是最高门槛，而通用模型 gpt-image-2 的**跨帧多帧一致性恰是公认弱项**（见 §4）。已有产出暴露**风格岔路**：anime 高清 sprite（rika）vs 真低分辨率像素（Gemini）—— 两条路工具完全不同。

---

## 4. 八候选研究汇总（web-verified + 对抗式第二来源核查）

> 方法：Workflow 8 候选并行研究（每候选官方文档事实采集）→ 每候选关键事实（定价/商用 license/集成/一致性宣称）对抗式第二来源核查（存疑默认 REFUTED/UNVERIFIABLE，防厂商自夸）。16 agents / ~899K token。**以下为核查后的准确版**。

### 4.1 速览表

| 候选 | 定位 | 引擎级多帧一致性 | UI 图标 | 集成（进 Mercury 管线） | 预算契合 | 商用 license |
|---|---|---|---|---|---|---|
| **PixelLab.ai** | 生成式像素专用 SaaS | 中偏强（有机制） | 中 | ✅**官方 MCP 零适配 + 官方 SDK `pip install pixellab`** | 按量，$30 用很久 | ✅**最清晰**（拥版权可商用含 Steam，仅禁训模型） |
| **Retro Diffusion** | 生成式像素 SaaS（FLUX） | **强**（调色板锁 + rd_animation） | 有 | REST 自写 adapter <50 LOC；**Aseprite 插件 $20 本地买断** | $20 一次性 / 按量 | 插件输出明确可商用；API 端 UNVERIFIED |
| **gpt-image-2** | 通用图像模型（非像素专用） | ❌**弱（公认硬伤）** | 单张可 | ✅已集成 animate-frames | ✅**免费**（已有 key） | 可商用，但纯 AI 图美国**不可独占版权** |
| **rika-ai.com** | 混合像素动画工具 | 好（用户实测）；序列帧有「抽奖」隐患 | 有 | ✅**有 API**（用户确认）+ 试错容错 | 不透明（官网定价页 404） | 用户确认**无商用阻塞** |
| **Scenario.gg** | 生成式游戏素材（Flux/SDXL LoRA） | anime 高清，中 | ✅**强**（设计系统 LoRA 锁风格） | REST，无官方 MCP | ❌**月费循环 $30/月**（超「一次性」预算） | 付费档完整可商用 |
| **本地 ComfyUI/SDXL** | 纯本地开源生成 | 可，但每角色需训 LoRA | 可 | 本地 REST（8188） | 免费**但搭建数天–数周** | SDXL OpenRAIL 可商用；ComfyUI 本体 **GPL-3.0** |
| **frameronin.com** | video→精灵表处理器（非生成） | — | — | 本地 REST /jobs | — | — → **排除**（不走视频路线） |
| **Aseprite CLI**（后处理层） | 像素后处理工具 | —（后处理底座） | — | ✅本地 CLI，adapter ≤50 LOC | **$20 一次性 / 免费替代** | 资产可商用 |

### 4.2 逐候选关键事实 + 对抗核查纠正

**PixelLab.ai**（生成式像素专用，自研模型 Pixflux/Pixen/BitForge）
- 定价（CONFIRMED）：免费 40 次试用（无需信用卡）；API 按 call+尺寸 —— Pixflux 64×64 **$0.00793**/call、8 方向角色 64×64 **$0.0173**/call、Pro 256×256 **$0.095**/call；订阅 Tier1 $12/月（年付/忠诚折扣 $9）。
- 集成（CONFIRMED）：官方 MCP `pixellab-code/pixellab-mcp` **v1.1.0**（2025-06-17，HTTP transport，Bearer token，4 工具 create_character/animate_character/create_tileset/create_isometric_tile），Claude Code 直接调；官方 Python SDK `pip install pixellab`。
- license（CONFIRMED）：ToS 明确**用户拥有版权、可商用（含 Steam 付费游戏）**，唯一限制=不得用生成图训练 AI 模型；瑞典法 + Open RAIL-M；Valve 申报归 Pre-Generated。
- 一致性：style reference 风格锁 + create-character-state（同角色 outfit/pose）+ animate-character（已有角色追加帧）；32×32=16 帧 / 128×128=4 帧 / **16px 以下弱**；真像素（非滤镜）；调色板精确锁定无官方硬约束（需后处理）。
- 资产：角色多帧**强**（4/8 方向、≤16 帧、skeleton）、tileset 强、UI 图标中、VFX 弱。
- **核查纠正**：研究侧「$30≈1500 次基础 call」REFUTED（实际 ~1734 次 8 方向 / ~3783 次 Pixflux / 315 次 Pro）；官方 Python SDK 确实存在（研究原标 UNVERIFIED 过保守）。

**Retro Diffusion**（生成式像素，自训 FLUX = RD_FLUX，Astropulse）
- 定价（公式 CONFIRMED）：API 积分制，rd_fast = `max(0.015, ((w×h)+100000)/6000000)×n`；rd_pro 0.18/张；动画 **0.07（standard）–0.25（advanced）**/张；编辑工具 $0.01–$0.18（部分免费）；**Aseprite 插件 Full $65 / Lite $20 一次性买断、本地推理、无订阅**。
- 集成（CONFIRMED）：官方 REST `POST api.retrodiffusion.ai/v1/inferences`（X-RD-Token，adapter <50 LOC）+ Replicate 四模型（rd-fast/plus/tile/animation）。**无官方 MCP**（自写 adapter ~100-150 LOC）。
- license：**Aseprite 插件输出明确可商用（CONFIRMED）**；API/网页端商用条款 UNVERIFIED（官方文档 WIP）。
- 一致性（CONFIRMED）：FLUX prompt engineering（非 LoRA）+ color palette 输入（**严格调色板锁定**）+ seed 固定 + tile_x/y 无缝；rd_animation 专保帧间一致；自研聚类下采样 + 256×256 原生训练 = 真像素。
- **核查纠正**：动画下限 0.07 非 0.14（研究 REFUTED）；「注册赠 50 积分」UNVERIFIABLE（官方未披露）。

**gpt-image-2**（OpenAI 通用图像模型）
- 定价（CONFIRMED）：token 计费，标准 图输入 $8/1M·输出 $30/1M（批量半价 $4/$15）；每图须用官方计算器估（gpt-image-1 基线 ~$0.02/$0.07/$0.19，gpt-image-2 实际单价 UNVERIFIED）。**用户已有 key 免费用**。
- 集成（CONFIRMED）：REST generations/edits/chat/responses + 官方 Python/Node SDK；需 Org Verification；**无官方 MCP**；Mercury animate-frames 已包。
- license（CONFIRMED）：可商用、用户拥有 Output；**关键：纯 AI 生成图在美国因缺人类作者性不可独占版权**（可用 ≠ 可独占）。
- 一致性（CONFIRMED 为弱项）：跨帧多帧一致性=**领域公认硬伤** —— 每次独立采样必漂移、`n` 只产随机变体非动作序列、无 palette-pin、**无真像素栅格意识（抗锯齿，非引擎可用真低分辨率像素）**；高清 anime sprite 比真 8/16/32px 更稳。需 pixel-snap（proper-pixel-art）+ 跨帧色彩量化（降 24 色消闪烁）+ 3D 人台参考缓解方向混淆。

**rika-ai.com**（混合像素动画工具）
- CONFIRMED（官网 FAQ 多源复现）：动画生成 **5 credits/次（失败自动退还）**；**pixel-perfect + 64×64/128×128 双档**分辨率可选。
- 用户提供（以实际用户为准）：**有 API + 允许一定试错费用**（推翻研究的「API UNVERIFIABLE」）；**无商用阻塞**（纯素材产出）；当前**剩 40 credits**（=8 个动画）可做 playground 验证。
- **隐患（用户观察）**：序列帧动画生成有「**抽奖**」行为 —— 不一定能按提示词较好生成**指定动作**。→ 战斗动画可控性待验证。
- UNVERIFIABLE（官网 SPA + /pricing /terms /docs 全 404）：¥200≈1000cr 套餐价、地图 2cr/角色 1cr 分项费率、跨帧一致性机制。

**Scenario.gg**（生成式，Flux/SDXL LoRA）
- 定价（CONFIRMED）：月费 —— Starter $15/月（年 $10，无自训）、**Pro $45/月（年 $30，含 LoRA 自训）**、Max $75/月（年 $50）；免费档 50 CU/日仅评估。→ **风格 LoRA 锁需 Pro 年付 $30/月循环，与「$30 一次性」性质不符**。
- 集成：REST + 公开 OpenAPI + Unity 插件开源；**无官方 MCP**。
- license（CONFIRMED）：付费档完整商用，Flux 代持商用授权；免费档不可商用。
- 一致性：Ideogram 单图角色一致 + Flux Kontext LoRA + 风格 LoRA（10-15 张参考图训练）；**UI 图标强（设计系统训练）**；产 anime 高清非真低分辨率像素（UNVERIFIABLE 但方向合理）。

**本地 ComfyUI/SDXL**（纯本地开源）
- 定价：ComfyUI **GPL-3.0**（核查纠正：非 MIT）免费；SDXL + nerijs/pixel-art-xl LoRA（CreativeML OpenRAIL，可商用）免费；运行成本=用户 GPU 电费。
- 集成：本地 REST `/prompt`（8188）；本地自托管**无官方 MCP**（云端 Comfy Cloud MCP beta）。
- 一致性：跨帧角色一致**非开箱即用** —— IP-Adapter FaceID + 角色 LoRA + ControlNet 三件套，**每角色需训 LoRA（数小时）**；8× nearest-neighbor 降采样得真像素；PixelArt-Detector 节点提供调色板约束（后处理）。
- **成本诚实评估**：搭建/学习曲线**数天–数周**，多帧 workflow 中高级用法。**优势**：ControlNet 可**精确控制动作姿态**（rika「抽奖」问题的潜在解）。

**Aseprite CLI + 后处理层**（处理器，非生成）
- 定价（CONFIRMED）：Aseprite **$19.99 一次性**（Steam）；**免费替代 = LibreSprite（GPL v2）/ Aseprite 自源码编译（EULA 允许个人编译 + 资产可商用）**；pixeldetector MIT 免费；ImageMagick 免费（核查纠正：是 ImageMagick License 非 Apache-2.0，仍宽松输出无限制）；PixelOver $19.20-30（GUI，无 CLI）。
- 集成（CONFIRMED）：`aseprite -b` 一条命令完成 **调色板量化 + 精灵表打包 + 帧 JSON + tag 分区**；**Godot Aseprite Wizard 插件**自动导入 SpriteFrames。无官方 MCP/REST（社区 MCP 包 CLI）。
- **意义**：**后处理可全自动化 → 直接解决用户「担心后处理麻烦」顾虑**。是任何生成路线的**统一后处理底座**。
- **缺口**：anime→真低分辨率像素的**风格化转换无「免费 + CLI 一键」工具**（pixeldetector 是「还原被放大像素」非「风格化」；PixelOver 效果好但 GUI 手动）。

**frameronin.com** → **排除**：video/GIF→精灵表处理器（与 GitHub systemchester/FrameRonin 同源），非生成式；用户不走视频路线即无用。license UNVERIFIED。

---

## 5. 用户决策记录（2026-06-30）

1. **预算**：gpt-image-2 免费用 + 可接受 ~$30 一次性；担心质量 + 后处理麻烦。
2. **首批资产**：角色多帧动画 + UI 图标；视频路线**不走**。
3. **质量门槛**：引擎级一致性。
4. **风格分工**（用户细化，关键）：**不是单一管线，按资产类型分工** ——
   - 人物原画（静态）→ gpt-image-2 产更高质量 / rika 高清兜底；
   - 战斗动画（多帧）→ rika 高清可应对；
   - **棋子（小尺寸）→ 高清不适合，走真低分辨率像素**；
   - UI 图标 → 待定。
5. **棋子路线**：**A. PixelLab 真像素**（以高清立绘为参考生成真像素棋子）。
6. **rika 定位**：有 API + 试错容错 + **无商用阻塞**；剩 40 credits 可 playground 验证；**隐患 = 序列帧「抽奖」可控性待验**。

---

## 6. 最终选型方案

### 6.1 架构：统一契约 + 按资产分工 + 统一后处理

```
            ┌─ character-bible JSON（角色身份单一事实源，复用 animate-frames 契约）
            │   外观/调色板/风格/轮廓语言/参考图 —— 喂给所有生成工具当一致性锚
            ▼
┌───────────────── 按资产分工生成 ─────────────────┐
│ 人物原画(静态)   → gpt-image-2（免费，质量高）       │
│ 战斗动画(多帧)   → rika API（anime 高清，一致性好*）  │
│ 棋子(小尺寸真像素)→ PixelLab MCP（参考图保持一致）    │
│ UI 图标(~38px)   → gpt-image-2 单张 / PixelLab       │
└──────────────────────┬───────────────────────────┘
                       ▼
   Aseprite CLI 统一后处理（调色板量化 + 精灵表 + 帧 JSON + tag）
                       ▼
   Mercury animate-frames 验证门（帧数/尺寸/调色板/dHash 一致性）
                       ▼
   Godot Aseprite Wizard → SpriteFrames（落 SoT 暂存区，用户导入）
```

**黏合剂 = 三个统一底座**（把分工的碎片黏起来，降低「多工具」复杂度）：
1. **character-bible JSON 契约**（同一份角色定义喂所有工具）—— 跨工具/跨形态一致性锚。
2. **Aseprite CLI 后处理**（任何来源的帧都过同一后处理 → 统一调色板/精灵表/Godot 格式）。
3. **animate-frames 验证门**（任何来源的帧都过同一 dHash/调色板一致性校验）。

### 6.2 资产 × 工具映射表（最终）

| 资产类型 | 形态 | 风格 | 主工具 | 备选 | 成本 |
|---|---|---|---|---|---|
| 人物原画/立绘 | 静态大图 | anime 高清 | **gpt-image-2** | rika 高清 | 免费 |
| 战斗动画 | 多帧 sprite | anime 高清 | **rika API** | 见 §6.4 备选 | 5cr/次（失败退还） |
| 棋子/地图单位 | 64–128px AnimatedSprite2D | 真低分辨率像素 | **PixelLab MCP** | rika 64/128 档 | 按量，免费 40 次试用起 |
| UI 图标 | ~38px | 真像素/扁平 | **gpt-image-2 单张** | PixelLab | 免费 |
| 后处理（全部） | — | — | **Aseprite CLI + Godot Aseprite Wizard** | LibreSprite（免费） | $20 一次性 / 免费 |
| 验证门（全部） | — | — | **animate-frames（dHash/调色板）** | — | 免费（已有） |

### 6.3 棋子（PixelLab 真像素）路线细节

- **生成**：PixelLab 官方 MCP（`pixellab-code/pixellab-mcp` v1.1.0，Claude Code 直接调，**零 adapter**）；以人物原画高清立绘为 **style/character reference** 保持同角色身份；`create-character`（4/8 方向，契合视角 Fake-3/4 或 Isometric 未定）+ `animate-character`（≤16 帧）。
- **尺寸**：棋子 64–128px → PixelLab 64×64 一次出多帧、128×128 一次 4 帧（避开 16px 弱区）。
- **后处理**：Aseprite CLI 统一调色板 + 精灵表 + tag → Godot Aseprite Wizard 导入 SpriteFrames。
- **预算**：免费 40 次试用先验证；转正式按量充值（Pixflux 64×64 $0.0079/call，$30 ≈ 数千次）。
- **license**：用户拥版权可商用（含 Steam），最干净。
- **依赖项（SoT 设计层定）**：棋子朝向数 = 视角定案（Fake-3/4 vs Isometric）。

### 6.4 战斗动画（rika）可控性权衡 + 备选

- **首选 rika**：anime 高清多帧是其强项，用户实测一致性好，有 API 可自动化，无商用阻塞。
- **隐患**：序列帧「抽奖」—— 提示词→指定动作命中不稳定。
- **验证（Phase 0）**：用剩余 40 credits（=8 动画）在 playground 实测「指定动作」命中率（攻击/受击/待机各试），失败退还降低试错成本。
- **若抽奖太严重的备选**：
  - (a) **接受多抽挑选**（5cr 失败退还，成本可控，最省事）；
  - (b) **本地 ComfyUI + ControlNet 精确控姿**（动作可控性最强，但搭建数天–周、需 GPU、每角色训 LoRA —— 重投入，仅当 rika 抽奖不可接受时启用）；
  - (c) 战斗动画也降级走 PixelLab（但 PixelLab 是真像素，与「anime 高清战斗动画」风格诉求不符，仅当全局转真像素风时考虑）。

---

## 7. 落地步骤草案（实现 session 用，本 session 不实现）

> 起步现金 **$0–20**（远低于 $30 预算）：Phase 0 全用已有额度（rika 40cr + PixelLab 免费 40 次 + gpt-image-2 key）；Aseprite 可先用 LibreSprite 免费替代，确认采用后再 $20 买断。

**Phase 0 — 零/低成本 playground 验证（先验证再投入）**
- rika：用剩 40 credits 实测战斗动画「指定动作」命中率（§6.4），判定 rika 是否胜任战斗动画主力。
- PixelLab：用免费 40 次试用，以剑圣高清立绘为参考，产棋子真像素样本（4/8 方向 + 行走/待机），判定一致性 + 小尺寸清晰度。
- gpt-image-2：产 1-2 张人物原画 + 几个 UI 图标，确认质量。
- **产出**：三类样本对比 → 用户拍板最终采用哪些工具。

**Phase 1 — 棋子管线（核心，PixelLab MCP + Aseprite + animate-frames）**
- 配置 PixelLab 官方 MCP 到 Claude Code（API token）。
- character-bible JSON（剑圣身份，复用 animate-frames 契约）。
- Workflow/skill：PixelLab 生成 → Aseprite CLI 后处理 → animate-frames 验证门 → Godot Aseprite Wizard 导入草稿（落 SoT 暂存区）。

**Phase 2 — 战斗动画（rika API adapter，若 Phase 0 通过）**
- rika API adapter（≤200 LOC；先 web-verify rika API 签名/鉴权 —— 用户登录账号取文档/端点）；接入 character-bible 契约 + Aseprite 后处理 + 验证门。

**Phase 3 — 人物原画 + UI 图标（gpt-image-2，复用现有管线）**
- 直接用 animate-frames / adapter 单张模式；UI 图标走 gpt-image-2 单张 + Aseprite 调色板统一。

**贯穿**：所有实现走 Mercury develop + PR + dual-verify；素材文件落 SoT 暂存区**由用户导入**（不直接动 SoT/Godot 仓）。

---

## 8. 风险 + UNVERIFIED 清单

| 项 | 状态 | 影响/缓解 |
|---|---|---|
| rika 序列帧「抽奖」可控性 | 用户观察，待 Phase 0 验证 | 战斗动画主力存疑 → 40cr playground 实测；备选 §6.4 |
| rika API 签名/鉴权/端点 | UNVERIFIED（官网 /docs 404） | Phase 2 前用户登录账号取文档，强制 web-verify |
| rika 定价（¥200/1000cr 等） | UNVERIFIABLE | 不影响选型（按 credit 用，5cr/动画 CONFIRMED） |
| 跨形态角色一致性（高清原画 ↔ 真像素棋子） | 设计挑战 | PixelLab style/character reference + character-bible 锚；Phase 0 验证 |
| anime→真像素自动转换 | 无免费 CLI 一键工具 | 棋子改用 PixelLab 真像素直生（绕开转换）已规避 |
| 棋盘视角（朝向数） | SoT 设计层未定 | 棋子生成等视角定案；PixelLab 支持 4/8 方向 + 等距 |
| gpt-image-2 多帧一致性 | 公认弱项 | 仅用于静态单张（原画/图标），不当多帧主力 |
| 纯 AI 图美国不可独占版权 | 法律边界 | 可商用但难独占；如需版权保护需人类实质修改 |

---

## 9. 来源（web-verified，节选）

- **PixelLab**：pixellab.ai/pixellab-api、pixellab.ai/termsofservice、github.com/pixellab-code/pixellab-mcp、pypi.org/project/pixellab、jonathanyu.xyz PixelLab 评测（2025-12）。
- **Retro Diffusion**：github.com/Retro-Diffusion/api-examples、astropulse.itch.io/retrodiffusion、replicate.com/blog（RD on Replicate）、runware.ai/blog（RD at scale）。
- **gpt-image-2**：developers.openai.com/api/docs/models/gpt-image-2、developers.openai.com/api/docs/pricing、community.openai.com/t/developing-sprite-sheets-with-gpt-image-2/1379831、sarthakmishra.com/blog/building-animated-sprite-hero。
- **rika-ai**：rika-ai.com（官网 FAQ 搜索摘要；/pricing /terms /docs 均 404 = UNVERIFIABLE）；其余以用户（实际使用者）确认为准。
- **Scenario**：scenario.com/pricing、help.scenario.com FAQ、docs.scenario.com、github.com/scenario-labs/Scenario-Unity。
- **本地 ComfyUI/SDXL**：github.com/comfyanonymous/ComfyUI（LICENSE = GPL-3.0）、huggingface.co/nerijs/pixel-art-xl、huggingface.co/stabilityai/stable-diffusion-xl-base-1.0、github.com/dimtoneff/ComfyUI-PixelArt-Detector、docs.comfy.org。
- **Aseprite + 后处理**：aseprite.org/docs/cli、aseprite.org/faq、steamdb.info/app/431730、github.com/LibreSprite/LibreSprite、github.com/viniciusgerevini/godot-aseprite-wizard、github.com/Astropulse/pixeldetector（MIT）、imagemagick.org/license。
- **基线**：`.mercury/docs/research/sot-workflow-optimization-roadmap-2026-06.md` §2。

---

## 10. 决策状态

- ✅ 已拍板：风格分工、棋子=PixelLab 真像素、rika 无商用阻塞、后处理=Aseprite、验证门=animate-frames、视频路线排除。
- ⏳ 待 Phase 0 playground 验证：rika 战斗动画可控性、PixelLab 棋子一致性、gpt-image-2 原画/图标质量。
- ⏳ 待 SoT 设计层：棋盘视角（朝向数）。
- ⏳ 待用户：是否进实现 session（本研究产出经用户确认后进 Phase 0）。

---

## 11. Phase 0 playground 验证结果（2026-06-30，真实 API 产样本）

> 产物目录：`D:\ShipOfTheseus\resource\mercury-playground\`（用户暂存区，未碰 SoT/Godot 仓）。成本：gpt-image-2 ~$0.21（用户 key）；PixelLab ~8 次免费试用（剩 ~32 次）；rika 两次失败**自动退还**（不扣 credits）。

| 资产 | 工具 | 结果 | 产物 |
|---|---|---|---|
| 人物原画 | gpt-image-2 | ✅ anime 高清立绘，剑圣形象吻合，质量高 | gpt_portrait_01.png |
| UI 图标 | gpt-image-2 | ✅ 质量高 + 风格内聚，但「拟像素」非真低分辨率像素（档位待定） | gpt_icon_slash_01.png, gpt_icon_iai_01.png |
| 棋子 | PixelLab pixflux | ✅ 真像素优秀，64/128/等距清晰可辨 | pixellab_pixflux_64/128/64_iso.png |
| 棋子一致性 | PixelLab init_image/bitforge | ✅ 以剑圣立绘为参考保持同一角色，**init_image 最忠实**（立绘→真像素忠实转换） | pixellab_initimg_128.png, pixellab_bitforge_portrait/sprite_128.png |
| 战斗动画（真像素备选） | PixelLab animate-with-text | ✅ `action` 指定动作可控（挥剑斩击命中）+ 角色一致 + VFX 弧光；上限 64×64、本测 4 帧 | pixellab_anim_attack_00-03.png + _sheet + .gif |
| 战斗动画（anime 高清） | rika | ⚠️ **服务端故障**（后端 ComfyUI 127.0.0.1:8188 不可达），两次失败退还，anime 高清路线**未验证成** | — |

**关键技术发现**：
- **rika = ComfyUI 工作流的托管包装**（后端故障暴露 127.0.0.1:8188）→「抽奖」= 扩散采样随机；服务端稳定性依赖 rika 运维（本次就挂了）。
- **PixelLab SDK v1.0.5 有兼容 bug**：`Usage` model 写死 `Literal["usd"]`，当前 API 返 `usage.type='generations'` → SDK 解析抛 pydantic 错（**图已生成**）。**解法 = REST 直调绕过 SDK response 解析**（实现 adapter 时照此，不依赖 SDK 解析层；认证头可仍用 `client.headers()`）。
- PixelLab 约束：参考图（init_image/style_image/reference_image）尺寸**须 == 输出尺寸**；`no_background=True` 支持透明背景（gpt-image-2 不支持）；`animate-with-text` 上限 **64×64**；`isometric=True` 支持等距；端点 `POST api.pixellab.ai/v1/{generate-image-pixflux|generate-image-bitforge|animate-with-text}`。
- **PixelLab animate 比 rika 更可控**：`action` 字段指定动作命中（非抽奖）、同平台与棋子角色一致、服务稳定 → 真像素战斗动画的有力候选。

**最终决策（用户拍板 2026-06-30）**：

| 资产 | 工具 | 风格 | 状态 |
|---|---|---|---|
| 人物原画 | gpt-image-2 | anime 高清 | ✅ 锁定（Phase 0 验证） |
| UI 图标 | gpt-image-2 | **拟像素高清**（沿用 Phase 0 playground 两个样式，用户满意） | ✅ 锁定 |
| 棋子 | PixelLab | 真像素（小尺寸棋盘单位） | ✅ 锁定（Phase 0 验证） |
| 战斗动画 | **rika 高清路线** | 目标 = **NS 系火纹（风花雪月/Engage）清晰高质感 + 轻微像素化** | ⏸ 暂缓（rika 服务不可用） |

- **否决** PixelLab 真像素战斗动画（64×64 太复古，像上世纪 GBA 火纹）；PixelLab 只管棋子。combat 非 MVP 必要项。
- ⚠️ **rika 服务当前不可用**：重试 1 = 后端 ComfyUI（127.0.0.1:8188）不可达；重试 2 = **HTTP 403**（generate 阶段被拒）。非 apikey/调用问题（key 鉴权通过、请求格式正确），是 rika 服务端/账号侧。→ 战斗动画验证**暂缓**，待用户登录 rika 网页端确认服务/账号状态 + rika 恢复。
- **rika 可用性风险**：两类故障（后端 ComfyUI 不可达 + API 403），作为战斗动画主力有稳定性隐患。若持续不可用 → 研究 NS 火纹风 2D 动画替代（本地 ComfyUI 自建，rika 同源但自掌控；或其他 AI 动画工具），或战斗动画后置（非必要项）。

---

## 12. NS 火纹风战斗动画方案研究（2026-06-30，6 方案 + 对抗核查，12 agents）

> 触发：rika 服务故障 → 研究不依赖 rika 单点的、达「NS 系火纹（风花雪月/Engage）清晰高质感」战斗动画方案。用户要求：偏清晰、偏 rika 高清稍像素化、否决 GBA 低分辨率像素、combat 非 MVP 必要但要更具体。

**核心结论 1 — NS 火纹战斗演出 = 3D cel-shaded 模型**（官方一手确认）：Engage 用 Unity（ROM 拆包确认）+ 主角 6+ 轮建模 + 动态运镜 + 骨骼动画 + 为「3D 转化」设计的角色（Mika Pikazo）；Three Houses 过场由 **SANZIGEN**（专业卡通渲染 CGI 工作室）制作 cel-shaded 3D CGI + Koei Tecmo（Kou Shibusawa 工作室）引擎。**不是 2D sprite、不是真像素** —— 是专业工作室分工产物，非单人技术问题。

**核心结论 2 — 用户认可的「rika 高清稍像素化」= HD anime 2D 图 + 轻微像素化后处理**（非真像素画、非纯 3D）。和 NS 火纹「质感相似但技术不同」的 2D 实现。

**方案对比（核查后）**：

| 方案 | 达 NS 质感 | 动作可控 | 多帧一致 | 可靠性 | 门槛/代价 |
|---|---|---|---|---|---|
| **本地 ComfyUI + Illustrious XL**（= 自掌控的 rika，rika 后端就是 ComfyUI） | 单帧可达 rika 高清稍像素化；逐帧流畅战斗动画达不到 NS 级 | ✅精确（ControlNet OpenPose/DWPose + IPAdapter） | ⚠️**真实瓶颈**（≤16 帧勉强、>20 帧崩坏、需人工 QA/修帧） | ✅自掌控、无单点（ComfyUI 由 Comfy Org 公司化运营、活跃） | 需 24GB VRAM GPU（4090/3090 级）或云 GPU ~$0.58/hr；学习 1–2 周；NS 风格需自训 LoRA（现成 FE LoRA 是 GBA 像素风） |
| AI 3D（Meshy/Tripo/Rodin/Hunyuan3D-2.1） | ❌达不到主角级（脸/手是弱点，恰是火纹核心镜头） | ⚠️抽奖 + 手工绑骨/K帧 | ✅天然（同 mesh） | SaaS 单点 / Hunyuan3D Apache-2.0 本地最稳 | Blender 中级技能 + 数月学习；**不推荐单人** |
| 云 2D SaaS（Scenario/SpriteFlow/Ludo.ai） | ❌≤512px；Scenario 最接近但主观 | ❌抽奖（无骨骼/ControlNet 精确控制） | 中（角色模型训练 5-15 图） | ❌单点（同 rika 风险） | Scenario 有 MCP + API-first（$15/$45/$75/月）；无原生 Godot 直导（靠引擎切片） |
| 像素工具（PixelLab/Retro Diffusion） | ❌质性差距（原生像素方块非 HD） | ✅骨骼（PixelLab skeleton ≤256²，Tier1 $12/月） | ✅ | SaaS 单点 | 只适合**棋子**级像素，达不到 NS 质感 |
| gpt-image-2（已有 key、免费） | ✅单帧立绘质感高（Phase 0 已验证） | 单张 | 多帧弱（2026-04 多图角色连续性有改善） | 已集成 animate-frames | 适合**战斗立绘/cut-in**，非逐帧动画 |

**关键洞察（务实收敛）—— 战斗「清晰质感」分两层**：
- **战斗立绘/半身/cut-in**（NS 火纹战斗大量用角色特写 cut-in）→ **gpt-image-2/Illustrious XL 高清单帧质感可达**（Phase 0 人物原画已验证质量高）。
- **逐帧流畅战斗动作动画**（NS 火纹级）→ **AI 当前无法免人工达到**（多帧一致性是所有 2D AI 方案的共同瓶颈；NS 火纹本身是 3D 专业分工产物）。

**建议**：combat 非必要 → 务实路径 = 先用 gpt-image-2 做**高清战斗立绘/cut-in**（质感够、已验证、零额外门槛），**逐帧流畅战斗动画后置**（等愿投入本地 ComfyUI 重管线 + 人工修帧，或接受 NS 级以下质量）。rika 若恢复可作「快速出 HD anime 多帧」便利工具，但单点不稳、不作唯一依赖。

**核查纠正**：研究侧把 NS 火纹误述为「2D anime 插画/手绘」→ 实为 3D（已纠正）；Maya/ZBrush/Shuriken 具体工具链 UNVERIFIABLE（官方仅证实 4 点）；Rodin 模型数估算 REFUTED（按 credit 计）；Scenario 原生 Godot 直导 REFUTED；PixelLab 定价可查（$12/$24/$50）。

**用户已决（2026-07-01）**：① GPU = RTX 4060(8GB) + RTX 3070Ti(8GB) —— **单帧立绘/cut-in 够跑**（SDXL/Illustrious XL 单帧 8-12GB），逐帧 AnimateDiff(16-24GB) 吃紧；② 战斗画面 = **清晰战斗立绘/cut-in**（逐帧流畅动画后置）。硬件与所选方案完美匹配（8GB 跑不动的多帧动画恰是 defer 的部分）。

---

## 13. 最终选型全貌（2026-07-01，全部锁定 + Phase 0 验证通过）

| 资产 | 工具/方案 | 风格 | Phase 0 验证 |
|---|---|---|---|
| 人物原画 | gpt-image-2 | anime 高清 | ✅ gpt_portrait_01 |
| UI 图标 | gpt-image-2（沿用 playground 样式） | 拟像素高清 | ✅ gpt_icon_slash/iai |
| 棋子 | PixelLab（pixflux/bitforge，以立绘为参考保持一致） | 真低分辨率像素 | ✅ pixellab_pixflux/initimg/bitforge |
| 战斗画面 | **gpt-image-2 战斗立绘/cut-in**（主）+ 可选本地 Illustrious XL（8GB 卡可跑单帧） | NS 火纹 cut-in HD anime | ✅ gpt_battle_cutin_01（达 NS 质感 + 模型自动脑补 FE 战斗 UI） |
| 逐帧战斗动画 | **后置**（本地 ComfyUI+Illustrious XL 需 16-24GB+人工修帧；或 rika 恢复当便利工具） | — | rika 服务故障 → defer，非 MVP |
| 后处理 | Aseprite CLI + Godot Aseprite Wizard（免费替代 LibreSprite/自编译） | — | 待实现 |
| 验证门 | Mercury animate-frames（dHash/调色板一致性） | — | 复用现有 |

**统一架构**：character-bible JSON 契约（角色身份锚）→ 按资产分工生成 → Aseprite CLI 统一后处理 → animate-frames 验证门 → Godot Aseprite Wizard 导入（落 SoT 暂存区由用户导入，不碰 SoT/Godot 仓）。

**关键账目**：所有角色资产都有可靠方案；**rika 不再是关键依赖**（战斗立绘走 gpt-image-2，逐帧动画后置）。起步成本：gpt-image-2 按量（~$0.07/张，已验证）+ PixelLab 按量（免费 40 次起，$30 充值用很久）+ Aseprite $20 一次性或免费替代。**Phase 0 全部验证通过**（产物 `D:\ShipOfTheseus\resource\mercury-playground\`）。

**下一步 = 实现 session**：搭管线（PixelLab MCP/REST 直调 + Aseprite CLI 后处理 + animate-frames 验证门 + character-bible 契约 + 各资产产线脚本/skill），落 Mercury 仓走 develop + PR + dual-verify；素材落 SoT 暂存区由用户导入。**实现注意**：PixelLab SDK v1.0.5 有 Usage pydantic bug → REST 直调绕过（见 §11）。
