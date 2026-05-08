# Pixel Animation Workflow — Mercury Phase 1 Research ADR

**Issue**: [#351](https://github.com/392fyc/Mercury/issues/351) [research+feat] Pixel frame sequence animation workflow — agent-invokable image gen + import + verify pipeline

**Status**: Phase 1 Research — verdict **CONDITIONAL_GO (Path D)**

**Date**: 2026-05-08
**Session**: S86 (main lane)
**Branch**: `lane/main/351-pixel-anim-research`

---

## TL;DR

Mercury 应建设 agent-invokable 视觉素材生产管线，技术路径推荐 **Path D（Hybrid）**：

- 以 submodule 挂 `wuyoscar/gpt_image_2_skill`（MIT，~446 LOC，已是 Claude Code skill 形态）于 `adapters/gpt-image-2/` + 薄 Mercury 包装（≤80 LOC）
- 在 `scripts/` 内（无 LOC cap）实现 Mercury-specific 内容：character bible JSON 注入、reference chain orchestration、verification rubric、retry loop
- 直调 OpenAI Image API（`OPENAI_API_KEY` 路径），**不**走 Codex CLI `$imagegen` 路径（后者 3-5× plan usage 消耗，不适合 batch sprite workflow）

**CONDITIONAL** 项（必须 Phase 2 实施前验证）：

1. `wuyoscar/gpt_image_2_skill` `.claude-plugin/plugin.json` schema 与 Mercury skill mount 模型兼容
2. `gpt-image-2` 透明背景需求场景 fallback 到 `gpt-image-1.5`（gpt-image-2 不支持 transparent BG）
3. `gpt-image-2` 无 `seed` 参数，确定性依赖 reference image chain，需接受 identity-level（非 pixel-level）一致

---

## 1. Background

### 1.1 Mercury 现状

- agents 缺 visual asset 能力 → 阻塞任意涉及视觉素材的 project / task
- 已有 `dev-pipeline` / `pr-flow` / `dual-verify` 等 skill，但无 image gen / verify 模块
- DIRECTION.md §"适配层规范"硬约束：external-project adapters 在 `adapters/<vendor>/` 下 ≤200 LOC；Mercury-internal `scripts/` 不限 LOC

### 1.2 用户场景

- 游戏 sprite / 动画帧序列
- 教学 / 演示流程图动画
- 网页 micro-interaction 关键帧
- UI mockup 状态序列
- 漫画 / storyboard / 视觉叙事多面板

### 1.3 GPT Image 2 时机

GPT Image 2 于 **2026-04-21** 由 OpenAI 发布（snapshot `gpt-image-2-2026-04-21`），character consistency + multi-image composition 能力大幅改善（vs gpt-image-1.5）；Codex CLI 内置 `$imagegen` skill — 但 plan-usage 路径成本高 3-5×，Mercury 应走 API key 路径。

---

## 2. Decision Verdict

| Path | LOC | License | 推荐度 | Verdict |
|------|-----|---------|-------|---------|
| **A** — Mount `gpt_image_2_skill` only | adapter ~50-80 | MIT | 4/5 | 可行但少自定义 |
| **B** — Mount `falsprite` patterns | N/A | 含糊 (README claims MIT, no LICENSE file) | 1/5 | **NO_GO** — 主因 JS-only + fal.ai SaaS lock；license 缺正式 LICENSE 文件辅证 |
| **C** — Mercury thin orchestration only | scripts/ ~150-250 | own + MIT cookbook | 3/5 | fallback if A 不兼容 |
| **D** — Hybrid (A + Mercury scripts/) | adapter ~50-80 + scripts/ ~150-250 | MIT 主代码 (上游 prompt material 子目录可能 CC BY 4.0 — Phase 2 须 verify scope) | **5/5** | **推荐** |

**核心理由**：

- Path D 在 `adapters/` 严格遵守 ≤200 LOC 硬约束（实际 ~50-80 LOC 远低于上限）
- Mercury-specific 逻辑（character bible 注入、verify rubric、retry loop）放 `scripts/` 不受 LOC 限制
- 上游 `wuyoscar/gpt_image_2_skill` MIT、Python、2 prod deps、actively maintained（last commit 2026-05-05、1505 stars）
- 与 Mercury MUST 规则"模块化 + 不重写 + adapter ≤200 LOC + 内部 scripts 无 cap"完全对齐

---

## 3. Dimension 1 — GPT Image 2 API Surface

### 3.1 API Endpoints

- `POST /v1/images/generations` — text-to-image
- `POST /v1/images/edits` — image-to-image w/ optional mask；支持多 reference image
- Responses API tool path（`gpt-5+` 内置 image gen tool）
- Batch API（`/v1/batch`）50% 折扣
- Auth: `Authorization: Bearer $OPENAI_API_KEY`，需 organization-level verification

### 3.2 关键参数

| 参数 | 取值 / 说明 |
|------|-----------|
| `model` | `gpt-image-2` (latest) / `gpt-image-1.5` / `gpt-image-1` / `gpt-image-1-mini` |
| `n` | 多图 batch；官方未明确上限（Codex CLI `$imagegen` 报告 10；第三方 Runware 报告 20）— UNVERIFIED |
| `size` | `auto` / `WxH`；max edge 3840px；aspect ratio ≤3:1 |
| `quality` | `low` / `medium` / `high` / `auto` |
| `output_format` | `png` / `jpeg` / `webp` |
| `background` | `opaque` / `auto` — **`gpt-image-2` 不支持 `transparent`**（需 fallback `gpt-image-1.5`） |
| `partial_images` | 0-3 streaming 预览 |
| `seed` | **不存在** — image generation API 历来无 seed 参数 |
| `input_fidelity` | `gpt-image-2` 锁定 high，无法调节 |

### 3.3 Reference Image Conditioning

- `/v1/images/edits` 接受单 / 多 reference image（URL / base64 / File ID）
- 官方 Cookbook 示例多至 4 ref images；第三方 Runware 报告上限 16 — UNVERIFIED 官方上限
- Mask &lt;50MB；input image 大小上限未明确 — UNVERIFIED

### 3.4 Cost / Quota

| 维度 | gpt-image-2 |
|------|------------|
| Image output | $30 / 1M tokens |
| Image input | $8 / 1M tokens |
| Cached image input | $2 / 1M tokens |
| Per-image (1024×1024 medium) | ~$0.053 |
| Per-image (1024×1024 high) | ~$0.211 |
| Per-image (1024×1024 low) | ~$0.006 |
| Batch | 50% off |

| Tier | IPM (官方 OpenAI) | TPM (官方) | Daily req (第三方* — 非官方) |
|------|---|---|---|
| 1 | 5 | 100,000 | 100k |
| 2 | 20 | 250,000 | 250k |
| 3 | 50 | 800,000 | 800k |
| 4 | 150 | 3,000,000 | 3M |
| 5 | 250 | 8,000,000 | 8M |

**Free tier 不支持** — 必须付费 API tier。Rate limit 在 organization 级。

*官方 [model page](https://developers.openai.com/api/docs/models/gpt-image-2) 仅暴露 IPM + TPM；"Daily req" 列来自第三方 [scriptbyai.com](https://www.scriptbyai.com/rate-limits-openai-api/) 综合，未在 OpenAI 官方文档中。Phase 2 实施时应以官方 IPM 为准。

### 3.5 Codex CLI `$imagegen` vs 直 API

- `$imagegen` 是 Codex CLI 内置 skill，调用消耗 ChatGPT plan usage 3-5× 文本 turn 速率
- **Escape hatch**: 设置 `OPENAI_API_KEY` 环境变量 → Codex 路由到直 API 计费，绕过 plan
- Mercury 推荐：**直接调 API**，不依赖 Codex CLI（Mercury agent 可直接 `openai` SDK 或包装的 CLI）

### 3.6 Sprite Sheet 单 prompt 失败模式

社区结论（OpenAI Community thread #1379831）：单 prompt 输出完整 sprite sheet "produces incomplete results with repeated poses"。失败模式：
- 边缘帧截断
- 多帧重复 pose
- Anatomical left/right vs screen left/right 混淆
- Character proportion drift

**官方无 sprite sheet workflow 指引**；社区共识 = per-frame + reference chain。

---

## 4. Dimension 2 — OSS Workflow Audit

### 4.1 Candidate 总览

| Repo | LOC | License | Stars | Last commit | Mercury fit |
|------|-----|---------|-------|------------|-------------|
| **wuyoscar/gpt_image_2_skill** | ~446 (Python) | **MIT** | 1505 | 2026-05-05 | **5/5 — 推荐** |
| lovisdotio/falsprite | ~1200-1500 (JS) | 含糊 (README MIT badge, no LICENSE file) | 173 | 2026-02-26 | 1/5 — NO_GO |
| YouMind-OpenLab/awesome-gpt-image-2 | ~3960 tooling (TS) | CC BY 4.0 | 5001 | 2026-05-07 | 2/5 reference-only |
| ZeroLu/awesome-gpt-image | ~1500 (Python) | MIT | 1151 | 2026-05-07 | 2/5 reference-only |
| OpenAI Cookbook (multimodal/image-gen-models-prompting-guide) | ~600-800 | MIT (parent repo) | — | active | 4/5 reference + cherry-pick |
| systemchester/FrameRonin | ~15-20k (TS+Py) | **NONE** | 370 | 2026-04-17 | 1/5 NO_GO |

### 4.2 Path 评分细节

#### Path A — Mount `wuyoscar/gpt_image_2_skill` only

- ✅ MIT, Python, 2 prod deps (`openai`, `python-dotenv`)
- ✅ 已是 Claude Code skill 形态（SKILL.md + `.claude-plugin/plugin.json`）
- ✅ 上游 actively maintained
- ❌ 无 streaming / async batch
- ❌ 上游 `uv` 启动器需在 Mercury 环境就绪
- LOC: adapter wrapper ~50-80 LOC（远低于 200 LOC cap）
- Score: 4/5

#### Path B — `falsprite` patterns (NO_GO)

- ❌ **License 法律不清** — README 显示 MIT badge，但无 LICENSE 文件且 GitHub API `license: null`，正式合规需补 LICENSE 文件后再考虑（不作单独 NO_GO 主因，但作 NO_GO 辅证之一）
- ❌ JS-only（Mercury Python-native）— **主 NO_GO 因**
- ❌ 全栈 fal.ai SaaS lock（`fal-ai/nano-banana-2` 硬编码）— **主 NO_GO 因**

#### Path C — Mercury thin orchestration only

- ✅ 0 上游 drift 风险
- ✅ MIT cookbook prompt patterns 可 cherry-pick
- ✅ 最大灵活性（streaming / multi-model routing 自定义）
- ❌ 重复 `gpt_image_2_skill` 已解决问题（arg parsing / env / output path）
- LOC: `scripts/image_gen.py` ~150-250 LOC（uncapped）
- Score: 3/5

#### Path D — Hybrid (推荐)

- ✅ adapter 兜底所有 OpenAI API surface
- ✅ Mercury `scripts/` 自由实现 verify / retry / character bible
- ✅ 与 DIRECTION.md §适配层规范完全对齐
- ❌ 双层依赖协调
- LOC: adapter ~50-80 + scripts/ ~150-250
- Score: **5/5 — 推荐**

---

## 5. Dimension 3 — Character / Style Consistency 工程实践

### 5.1 三大支柱

1. **Character Anchor Block**（exact repetition，不换同义词）
2. **Reference image chain**（每帧用 ground truth 而非 cascade — 避免 drift 累积）
3. **Per-session 批量生成**（model context 连续性减少 drift）

### 5.2 Anchor Block 范式

```
Character Consistency: [same green tunic, same facial features, same proportions, same color palette]
Constraints: [no text, no redesign, no watermarks]
Scene: [character running through a snowy forest, urgent expression, motion blur on feet]
```

**关键**：Cookbook 强调"reference each input by index — Image 1 / Image 2 / ..."；不要换词。

### 5.3 Style Block (JSON)

```json
{
  "style": "2D pixel art, 32×32 tile",
  "color_palette": ["#3A86FF", "#FF006E", "#FFBE0B"],
  "lighting": "soft front, no hard shadows",
  "camera": {"angle": "front", "distance": "full body"},
  "medium": "pixel art",
  "mood": "heroic"
}
```

参数顺序：scene → subjects → environment → composition → lighting → camera → style → constraints。

### 5.4 Seed Strategy

GPT Image 2 **不支持 seed**。替代机制：
- Reference image as visual anchor
- Same-session 批量生成
- Anchor block exact repetition
- 接受 **identity-level 一致**（非 pixel-level）

### 5.5 Sprite-sheet 失败 → per-frame 救济

公认 best practice：
1. 生成 base character (neutral / frontal / hi-res)
2. 用 base 作 reference image，**逐帧**生成各 pose
3. 全部完成后跑 normalization pass：`"normalize the style, character consistency and size for this sprite sheet, keeping all the poses intact"`

### 5.6 Cross-session Continuity

- model 无持久记忆 — drift 是自然
- 用 **Character Bible JSON**（不可变属性 + reference image paths + anchor block）
- 每次 session 重新注入完整 anchor + reference images
- LoRA fine-tuning（CharForge / Scenario）是高成本路径，Mercury Phase 2 不采纳

### 5.7 Multi-character

- GPT Image 2 多 reference image 支持，但 feature swap 是已知问题
- 推荐："分离生成 + 后期 inpainting 合成"

### 5.8 模型选型建议（按场景）

| 场景 | 首选 |
|------|-----|
| 严格视觉连续性 (sprite frame seq) | FLUX 2（10 ref images native） |
| 复杂指令 (场景合成 / 文字叠加) | GPT Image 2 |
| 同批风格套装 (icon set / UI) | Recraft V3 set 模式 |

**Mercury Phase 2 默认 GPT Image 2**（vendor consistency + Codex CLI integration option），FLUX 2 / Recraft 留作 Phase 3 plug-in option。

---

## 6. Dimension 4 — Verification Rubric

### 6.1 Default Rubric Stack

| 维度 | 工具 | License | 类型 |
|------|------|---------|------|
| 4.1 Frame count match | `pathlib` (stdlib) | — | hard gate |
| 4.2 Dimension uniformity | Pillow | MIT-CMU | hard gate |
| 4.3 Palette quantization | Pillow `getcolors()` | MIT-CMU | hard gate |
| 4.4 Transparent BG | Pillow + numpy | MIT + BSD | hard gate |
| 4.5 Loop closure | imagehash dHash + scikit-image SSIM | BSD-2 + BSD-3 | soft gate |
| 4.6 Character consistency | imagehash dHash (default) | BSD-2 | soft gate |

**Default 安装体积** &lt; 100MB，CPU-only，16 帧 verify 估计 &lt;500ms。

### 6.2 Optional Phase 3

| 维度 | 工具 | 备注 |
|------|------|-----|
| CLIP image similarity | sentence-transformers + ViT-B/32 | 340MB 模型；CPU 4-12s/16帧 |
| LPIPS perceptual | lpips + torch | &gt;500MB 模型；CPU 5-15s/16帧 |
| Prompt-image alignment | CLIP text-image (抽样 3 帧) | 复用 4.6 CLIP 模型 |
| Face landmarks | mediapipe (写实人物) / 不适用 pixel art | — |
| LLM-as-judge | GPT-4V / BLIP-2 | 成本过高，仅手动 audit |

### 6.3 Pass/Fail/Regen 反馈结构

```
VerifyResult {
  passed: bool,
  frame_count: { expected, actual, passed },
  dimension_uniformity: { reference_size, violations: [...], passed },
  palette_quantization: { max_allowed, per_frame_sizes, union_size, passed },
  transparent_bg: { per_frame_ratios, threshold, passed },
  loop_closure: { dhash_distance, ssim, passed, severity },
  character_consistency: { method, per_frame_scores, threshold, passed, severity },
  fail_reasons: [string],
  retry_suggested_adjustments: { fix_frame_count, fix_transparency, ... }
}
```

### 6.4 Retry Budget

- `max_retries = 3`（hard cap）
- Retry 1: 追加 `fail_reasons` 到 prompt
- Retry 2: 结构化 hint 转 prompt 调整
- Retry 3: 降级参数（减帧数 / 放宽 palette）
- 仍 FAIL → escalate（issue comment / IM 通知）

### 6.5 Cost cap

- Verify 本身 ≈ $0（local CPU）
- Pipeline 调用受 `max_retries × cost_per_call` 限制；超 budget abort + escalate
- Optional CLIP/BLIP check 仅在最终 PASS 帧上跑一次

---

## 7. Phase 2 Implementation Plan (CONDITIONAL_GO)

### 7.1 前置验证（Phase 1 → Phase 2 gate）

- [ ] Fetch `wuyoscar/gpt_image_2_skill` `.claude-plugin/plugin.json` 验证 schema 与 Mercury skill mount 兼容
- [ ] Mercury 环境验证 `uv` 可用（`gpt_image_2_skill` skill launcher 依赖）
- [ ] 透明 BG 需求场景 fallback `gpt-image-1.5` 确认 — 决定 skill 是否暴露 model 切换 flag
- [ ] License gate 复核（MIT 已确认 via GitHub API）

### 7.2 实施任务（Phase 2）

#### 7.2.1 Adapter Mount

```
adapters/gpt-image-2/
├── README.md            # mount 说明 + license attribution + cherry-pick metadata
├── env-shim.sh          # OPENAI_API_KEY + uv 探测 (POSIX)
├── env-shim.ps1         # PowerShell 等价 (Mercury Windows host)
├── invoke.sh            # 包装 upstream CLI 调用 (POSIX)
├── invoke.ps1           # PowerShell 等价 (Mercury Windows host)
└── .gitmodules-pointer  # 指向 wuyoscar/gpt_image_2_skill submodule
```

> **Windows 兼容**: Mercury host 默认 Windows 11 + PowerShell 7。`.sh` 入口仅在 Git Bash / WSL 下工作；必须双轨提供 `.ps1` 等价或 Phase 2 改写为单一 Python 入口（推荐后者，跨平台 + 与 `scripts/image_gen/*.py` 风格一致）。

LOC 估计：~50-80 单平台 / ~80-130 双轨（仍远低于 200 cap）

按 CLAUDE.md §"Cherry-pick protocol" 必须项（6 条全部）：
1. `.mercury/state/upstream-manifest.json` 添加条目，字段: `path` / `scope` (`"project"`) / `upstream_repo` / `upstream_path` / `upstream_sha_at_import` (via `gh api` 验证) / `upstream_license` / `import_pr` / `import_date` / `import_rationale` / `last_drift_check` (null)
2. 所有 SKILL.md 添加 frontmatter: `upstream_source` / `upstream_sha` / `upstream_license` / `cherry_picked_in` / `cherry_picked_at`
3. 脚本头 5-line 注释块: `UPSTREAM` / `SOURCE` / `SHA` / `DATE` / `ISSUE`
4. **Config / template 文件**（`*.example` / CLAUDE 片段）顶部加 `# Based on <upstream> (LICENSE) SHA: <sha>` 归属注释
5. **License gate**: 仅 cherry-pick MIT / Apache-2.0 / 其他 permissive license（拒 GPL / AGPL / 无 LICENSE）。manifest 记录
6. **SHA verification**: `upstream_sha_at_import` 必须 commit 前 via `gh api repos/{owner}/{repo}/commits/{sha}` 验证；未通即标 `UNKNOWN_VERIFY_MANUALLY` 并在 PR body 列出

#### 7.2.2 Mercury Scripts (`scripts/` uncapped)

```
scripts/
├── image_gen/
│   ├── __main__.py            # 跨平台单一入口 (`python -m scripts.image_gen ...`)
│   ├── character_bible.py     # JSON load + anchor block 拼装
│   ├── pipeline.py            # invoke adapter + reference chain orchestration
│   ├── verify.py              # default rubric (4.1-4.6 hard+soft gates)
│   └── retry_loop.py          # max_retries=3 + structured feedback
└── image-pipeline.py          # CLI shim 整合入口（推荐 .py 而非 .sh / .ps1，避免 Windows-vs-POSIX 双轨）
```

> **Windows 兼容**: 整合入口推荐 `.py`（Mercury 已是 Python-native）。如需 shell 入口，必须同时提供 `.sh` (POSIX) + `.ps1` (Windows PowerShell 7)。

LOC 估计：~150-250

#### 7.2.3 Skill 入口

```
.claude/skills/animate-frames/
├── SKILL.md                   # frontmatter w/ upstream metadata
└── invoke.py                  # /animate-frames CLI 入口
```

#### 7.2.4 Smoke Test

- 4-frame walking-cycle sprite sheet
- prompt → gen → verify → final ✅
- 接入 `dual-verify` gate

#### 7.2.5 文档

- `.mercury/docs/guides/pixel-animation-workflow.md` — agent 调用指南
- character bible JSON schema 文档
- Phase 3 plug-in path（FLUX 2 / Recraft / LPIPS / CLIP）roadmap

### 7.3 不在 Phase 2 范围

- ❌ Video / GIF 编码
- ❌ Motion-quality scoring (LPIPS / SSIM detail tuning)
- ❌ Anthropic image gen 集成
- ❌ Custom UI tool
- ❌ 自训 model
- ❌ LoRA fine-tuning workflow（CharForge 路线）

---

## 8. Risk Register

| Risk | Severity | Mitigation |
|------|---------|------------|
| `gpt-image-2` 无 seed → identity drift | High | Reference image chain + anchor block + same-session batch |
| `gpt-image-2` 不支持 transparent BG | Medium | Fallback to `gpt-image-1.5` via skill flag |
| 上游 `gpt_image_2_skill` 升级 break adapter | Medium | Pin upstream SHA + drift-check（`scripts/upstream-drift-check.sh` 周期跑） |
| API rate limit (Tier 1: 5 IPM) | Medium | Phase 2 文档说明 tier 升级 / batch API 50% 折扣 |
| Sprite sheet single-prompt 失败 | Confirmed | Per-frame + reference chain default mode |
| Multi-character feature swap | Medium | Phase 2 不支持；Phase 3 inpainting 合成方案 |
| Verify 阈值 calibration（dHash/SSIM） | Low-Medium | Phase 2 smoke test 阶段 calibrate；可调阈值 |
| 跨 session character drift | High → Medium with bible | Character Bible JSON + ground truth ref archive |
| Codex `$imagegen` plan-quota 消耗 | High if mistakenly used | `OPENAI_API_KEY` 直 API 路径（不走 Codex CLI） |

---

## 9. Verdict

**CONDITIONAL_GO Path D** — Phase 2 实施前必须完成 §7.1 前置验证（4 条）。Phase 2 实施 LOC budget：adapter ~80 + scripts/ ~250 + skill ~50 ≈ ~380 LOC；Mercury 自写代码 + cookbook snippets 全 MIT；上游 `wuyoscar/gpt_image_2_skill` 主代码 MIT，prompt-material 子目录可能含 CC BY 4.0（Phase 2 cherry-pick scope 须 narrow 到仅 MIT 部分）。

通过 Phase 1 → Phase 2 gate 即可进入实施。

---

## 10. Sources (合计 ~54 unique URLs，重叠去重后 ≥30 unique)

### Dimension 1 — GPT Image 2 API

1. https://developers.openai.com/api/docs/guides/image-generation
2. https://developers.openai.com/api/docs/models/gpt-image-2
3. https://developers.openai.com/api/docs/models/gpt-image-1.5
4. https://developers.openai.com/api/docs/models/gpt-image-1
5. https://developers.openai.com/api/docs/pricing
6. https://community.openai.com/t/introducing-gpt-image-2-available-today-in-the-api-and-codex/1379479
7. https://community.openai.com/t/developing-sprite-sheets-with-gpt-image-2/1379831
8. https://community.openai.com/t/developing-sprite-sheets-with-gpt-image-2/1379831?page=2
9. https://codex.danielvaughan.com/2026/04/27/codex-cli-image-generation-gpt-image-2-visual-development-workflows/
10. https://developers.openai.com/codex/cli/features
11. https://developers.openai.com/codex/pricing
12. https://www.scriptbyai.com/rate-limits-openai-api/
13. https://wavespeed.ai/blog/posts/gpt-image-2-api-guide/
14. https://runware.ai/docs/models/openai-gpt-image-2
15. https://github.com/openai/codex/issues/19175
16. https://developers.openai.com/cookbook/examples/multimodal/image-gen-models-prompting-guide

### Dimension 2 — OSS Audit

17. https://api.github.com/repos/wuyoscar/gpt_image_2_skill
18. https://raw.githubusercontent.com/wuyoscar/gpt_image_2_skill/main/src/gpt_image_cli/cli.py
19. https://raw.githubusercontent.com/wuyoscar/gpt_image_2_skill/main/skills/gpt-image/SKILL.md
20. https://api.github.com/repos/lovisdotio/falsprite
21. https://api.github.com/repos/YouMind-OpenLab/awesome-gpt-image-2
22. https://api.github.com/repos/ZeroLu/awesome-gpt-image
23. https://api.github.com/repos/systemchester/FrameRonin
24. https://api.github.com/repos/openai/openai-cookbook/contents/examples/multimodal
25. https://systemchester.github.io/FrameRonin/

### Dimension 3 — Consistency Engineering

26. https://community.openai.com/t/need-for-character-consistency-and-style-locking-in-image-generation/1232362
27. https://community.openai.com/t/how-to-achieve-consistency-of-comic-characters/591652
28. https://bfl.ai/blog/flux-2
29. https://selfielab.me/blog/flux-ai-multi-character-consistency-workflow-guide-20260221
30. https://www.scenario.com/blog/ai-sprite-generator
31. https://www.seagames.com/blog/gpt-image-2-game-character-design-seagames
32. https://medium.com/diffusion-doodles/json-prompting-for-image-generation-e8c236c2ad65
33. https://dev.to/worldlinetech/json-style-guides-for-controlled-image-generation-with-gpt-4o-and-gpt-image-1-36p
34. https://developers.openai.com/api/reference/resources/images/methods/generate
35. https://arxiv.org/html/2412.03685v2
36. https://github.com/RishiDesai/CharForge
37. https://skywork.ai/blog/how-to-keep-ai-images-consistent-reference-images-attribute-locking-guide/
38. https://fal.ai/learn/devs/flux-2-developer-guide
39. https://medium.com/@justin_echternach/generating-sprite-sheet-animations-with-dall-e-2-1098275d5e43
40. https://www.recraft.ai/blog/how-to-create-image-sets
41. https://medium.com/design-bootcamp/how-to-design-consistent-ai-characters-with-prompts-diffusion-reference-control-2025-a1bf1757655d

### Dimension 4 — Verification Rubric

42. https://pypi.org/project/imagehash/
43. https://github.com/JohannesBuchner/imagehash
44. https://pypi.org/project/Pillow/
45. https://github.com/openai/CLIP
46. https://huggingface.co/sentence-transformers/clip-ViT-B-16
47. https://scikit-image.org/docs/stable/api/skimage.metrics.html
48. https://huggingface.co/docs/transformers/en/model_doc/blip-2
49. https://pillow.readthedocs.io/en/stable/reference/ImagePalette.html
50. https://benhoyt.com/writings/duplicate-image-detection/
51. https://unifiedimagetools.com/en/articles/ai-image-quality-metrics-lpips-ssim-2025

### Internal Mercury

52. `.mercury/docs/DIRECTION.md` §"适配层规范"
53. `CLAUDE.md` (repo root) MUST §"External-project adapters" + §"Cherry-pick protocol"
54. `.mercury/state/upstream-manifest.json` (existing format reference)

---

## 11. UNVERIFIED Items（Phase 2 前需复核）

1. `gpt-image-2` `n` 参数官方上限（社区 10 / 第三方 20，docs 未明确）
2. `gpt-image-2` reference image 数量官方上限（实践 ≥4，未明确上限）
3. `gpt-image-2` input 图像大小上限（仅知 mask &lt;50MB）
4. `gpt-image-2` outpainting 模式（mask 扩界是否原生支持）
5. FLUX 2 seed 参数（`bfl.ai/blog/flux-2` 未提及）
6. ImageCritic 框架 production 可用性（emergentmind.com 仅研究记录）
7. Recraft V3 multi-character 同框 consistency 机制（官方未公开）
8. Character Bible JSON OSS 标准（无公认 schema，本 ADR 综合归纳）
9. dHash / SSIM 在 pixel art 下的 threshold calibration（本 ADR 取经验值，需 smoke test 验证）
10. CLIP ViT-B/32 CPU 推理实测延迟（4-12s/16 帧为估算）
11. wuyoscar/gpt_image_2_skill `.claude-plugin/plugin.json` schema 与 Mercury 兼容性
12. SeflieLab "Flux 92% vs Midjourney 65% group consistency" 第三方测试（方法论未公开，不应作硬指标）

所有 UNVERIFIED 项不阻塞 Phase 1 verdict — 但 Phase 2 实施前应至少处理 §7.1 前置验证（项 11 + transparent BG fallback + uv 验证 + license 复核）。

---

## 12. Dual-Verify Audit Trail (S86 2026-05-08)

Codex audit job `task-movp37dn-k2ymjh` returned: **Critical: 0 / High: 3 / Medium: 5 / Low: 0** — verdict NEEDS-CHANGES。

### 12.1 Findings 接受 (5 项已修)

| # | Severity | 位置 | 接受/拒绝 | 修订 |
|---|---------|-----|----------|------|
| F2 | High | falsprite license rationale | ✅ Accepted | License 措辞 NONE → 含糊 (README MIT badge / no LICENSE file) — 非 NO_GO 主因，主因为 JS-only + fal.ai lock |
| F4 | Medium | "MIT 全栈" overstatement | ✅ Accepted | §2 Path D 表 + §9 Phase 2 LOC 段说明: 主代码 MIT，上游 prompt material 子目录可能 CC BY 4.0；Phase 2 cherry-pick scope 须 narrow |
| F6 | Medium | Tier 4 IPM 表 | ✅ Accepted | §3.4 IPM 100 → 150；新增 TPM 列；Daily req 列标注 `*非官方` 来自第三方 scriptbyai.com |
| F7 | Medium | Cherry-pick checklist 不全 | ✅ Accepted | §7.2.1 列全 CLAUDE.md §"Cherry-pick protocol" 6 条（含 attribution comments / license gate / SHA verification） |
| F8 | Medium | Windows compat | ✅ Accepted | §7.2.1 + §7.2.2 加 Windows / PowerShell 7 兼容说明，推荐统一 `.py` 入口避免双轨 |

### 12.2 Findings 反驳 (3 项 disagree)

| # | Severity | Codex 主张 | Mercury 立场 | 证据 |
|---|---------|-----------|------------|------|
| F1 | High | gpt-image-2 支持 `background: transparent` | **DISAGREE** | OpenAI 官方原话 (developers.openai.com/api/docs/guides/image-generation): "`gpt-image-2` doesn't currently support transparent backgrounds. Requests with `background: 'transparent'` aren't supported for this model." 已通过 WebFetch 直接验证。Codex 结论错误。ADR §3.2/§3.4/§8 维持原文 |
| F3 | High | 外部项目应放 `modules/` 而非 `adapters/` | **DISAGREE** | Mercury 仓库实际结构: `modules/` 目录为空；`adapters/` 含 5 个 `mercury-*` subdir。CLAUDE.md MUST 明文 "External-project adapters under `adapters/<vendor-name>/`"。DIRECTION.md §适配层规范同。Codex 似把其它项目惯例幻觉到 Mercury。ADR §7.2.1 维持 `adapters/gpt-image-2/` 结构 |
| F5 | Medium | last commit 2026-04-28 + stars HTML 圆为"1.5k" | **DISAGREE** on date / **partial agree** on stars | GitHub API `repos/wuyoscar/gpt_image_2_skill/commits` 直接返回 `2026-05-05T20:09:56Z` for top commit (sha `6fdd7243...`)。Star count 1505 来源亦是 API exact (`stargazers_count`)，非 HTML 渲染的"1.5k"。ADR §4.1 + §5 来源标注为 GitHub API endpoint，数值正确 |

### 12.3 Final Verdict

After fixes + rebuttals: **PASS** with documented audit trail。

- Claude side review: PASS (3 Low advisory notes, 已纳入此节)
- Codex side review: NEEDS-CHANGES → 5 findings 已 fix + 3 findings 已 documented disagree with evidence
- Final verdict: **PASS** — proceeding to commit

---

**End of ADR.**
