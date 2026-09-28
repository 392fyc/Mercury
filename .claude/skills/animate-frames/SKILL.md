---
name: animate-frames
description: 用户明确要求生成行走循环、角色帧序列、精灵图或像素动画素材时使用。运行前确认输出范围和当前费用。
user-invocable: true
---

# 像素动画帧生成

这是 Mercury 逐帧精灵生成流程的入口。实际生成、验证和有限重试由仓库 scripts/image_gen/ 负责；当前候选中的 invoke.py 仅封装命令行。

- 输入包括角色设定 JSON 与场景 JSON。
- 验证包括帧数、尺寸一致性、调色板、dHash 一致性及可选的循环闭合。
- 本流程依赖项目图像适配器和对应外部服务。Claude Code 本身不提供该后端；不要声称这是 Claude 原生图像能力。
- 运行前确认输出目录、生成范围和当前费用；先用 --dry-run。不要读取、打印或写入凭据。
- 生成失败时检查结构化 JSON 报告中的原因。跳过验证门槛可能产生误报，应明确说明。

生成场景模板：

~~~powershell
python .claude/skills/animate-frames/invoke.py --example walking-cycle > scenes.json
~~~

模板包括 walking-cycle、idle 和 attack-arc。按项目指南填写角色设定，然后运行：

~~~powershell
python .claude/skills/animate-frames/invoke.py --bible knight.json --scenes scenes.json --out-dir frames/
~~~

所有后续参数传递给 scripts.image_gen；详见 --help 和 .mercury/docs/guides/pixel-animation-workflow.md。

技能本身是轻量包装，不复制项目图像生成、验证或重试逻辑。单张图、视频编码和 GIF 不属于该流程。检查 JSON 报告的 passed、final_fail_reasons、attempts 和 verify.gates；passed 为 false 时不要报告成功。
