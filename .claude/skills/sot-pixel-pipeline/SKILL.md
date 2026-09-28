---
name: sot-pixel-pipeline
description: 用户明确要求生成角色立绘、界面图标、战斗 cut-in、棋子精灵或其他棋盘单位像素素材时使用。运行前确认输出范围和当前费用。
user-invocable: true
---

# SoT 像素素材管线

这是 Mercury 按素材类型调用像素美术生成流程的技能。实际生成、后处理、验证和 Godot 导出由仓库内 scripts/sot_pixel/ 与 adapters/ 负责；当前候选中的 invoke.py 只是命令行封装。

| 素材 | 后端 | 处理 | 输出 |
|---|---|---|---|
| 立绘 | 项目 gpt-image-2 adapter | 无 | PNG |
| 图标 | 项目 gpt-image-2 adapter | 可选调色板量化 | PNG |
| Cut-in | 项目 gpt-image-2 adapter | 无界面与文字的提示约束 | PNG |
| 棋子 | PixelLab REST | Python 打包器，可选 Aseprite | 64×64 精灵图与 SpriteFrames .tres |

Claude Code 本身不提供上述图像后端；运行依赖当前 Mercury 仓库中的脚本、适配器、Python 包和各自服务的访问权限。不要把它们称为 Claude 原生图像能力。运行前确认输出目录、素材内容和当前费用，先用 --dry-run 检查计划和提示。不要读取、打印或写入凭据；真实生成所需的凭据由项目适配器处理。

完整输入格式、参数和样例见 .mercury/docs/guides/sot-pixel-pipeline.md。

生成角色资料模板：

~~~powershell
python .claude/skills/sot-pixel-pipeline/invoke.py --bible-template > bible.json
~~~

生成立绘：

~~~powershell
python .claude/skills/sot-pixel-pipeline/invoke.py --asset portrait --bible bible.json --out-dir staging/ --name aria
~~~

生成图标：

~~~powershell
python .claude/skills/sot-pixel-pipeline/invoke.py --asset icon --bible bible.json --out-dir staging/ --name slash_skill --scene "crossed swords, minimalist, icon style" --quantize
~~~

生成战斗 cut-in：

~~~powershell
python .claude/skills/sot-pixel-pipeline/invoke.py --asset cutin --bible bible.json --out-dir staging/ --name aria_cutin --scene "dramatic close-up, weapon raised, determined expression"
~~~

生成棋子：

~~~powershell
python .claude/skills/sot-pixel-pipeline/invoke.py --asset pawn --bible bible.json --out-dir staging/ --name aria --ref staging/aria_portrait.png --backend pixflux --animate-walk --size 64
~~~

默认 high top-down 视角让棋盘上的四个行走方向形成不同朝向。只有真正的横版角色才使用 side 视角。

## 验收与移交

- 棋子资源应包含 idle、walk_north、walk_south、walk_east、walk_west、hurt、death 动画；death 不循环，其余循环。
- 检查 JSON 报告中的 passed、verify.fail_reasons、尺寸和动画列表。
- 输出写入指定 staging 目录；本技能不直接修改游戏仓库。由用户按项目流程导入。
- 生成失败时记录报告原因，不要把 dry-run 当成真实生成成功。
