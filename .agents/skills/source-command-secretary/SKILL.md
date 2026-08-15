---
name: "source-command-secretary"
description: "切换语音交互到秘书模式（快速记录+总结，不执行任务）"
---

# source-command-secretary

Use this skill when the user asks to run the migrated source command `secretary`.

## Command Template

切换到**秘书模式**（Mercury #468 双向语音 agent）。

1. 调用 voice MCP server 的 `set_mode` 工具，参数 `mode="secretary"`。
2. 进入秘书模式后，你的职责是**快速记录和总结用户发言，不执行任何任务**：
   - 用 `listen` 工具拉取用户的中文语音（每次一句）。秘书模式下 `listen` 会自动把转写
     记入笔记文件。
   - 对关键信息用 `record_note` 工具结构化沉淀：`kind="requirement"`（需求）/
     `decision`（决策）/ `summary`（总结）。
   - **不要主动推进或执行任务**——只做信息录入渠道。
3. 当用户表示已说清某任务的需求/目标/细节/规范，提示用户：说「切工作模式」或用 `/work`
   显式切换后你才开始推进。

如果 voice MCP server 未注册，告诉用户先运行 `Codex mcp add voice -- <venv-python> scripts/voice/mcp_server.py`（详见 `scripts/voice/README.md`）。
