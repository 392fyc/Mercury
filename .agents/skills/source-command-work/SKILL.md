---
name: "source-command-work"
description: "切换语音交互到工作模式（基于已掌握需求自主推进任务，遇 check-in 主动语音提示）"
---

# source-command-work

Use this skill when the user asks to run the migrated source command `work`.

## Command Template

切换到**工作模式**（Mercury #468 双向语音 agent）。

1. 调用 voice MCP server 的 `set_mode` 工具，参数 `mode="work"`，并把本次要推进的任务
   写入 `task_context`（取自秘书模式记录的需求/目标/细节/规范）。
2. 进入工作模式后，你**基于已掌握的需求自主推进具体任务**。先读取秘书模式的记录文件
   （`get_status` 返回的记录文件路径）确认需求全貌。
3. **双向交互**——遇到以下情形时，用 `announce` 工具主动语音提示用户回到交互：
   - **决策需求**：需要用户在多个方案间拍板。
   - **额外信息确认**：缺少推进所需的信息。
   - **完成**：任务阶段性完成，播报结果。
   提示后如需用户口头回应，紧接着调用 `listen` 拉取用户语音。
4. 不确定时优先 `announce` + `listen` 双向确认，而不是擅自假设。

如果 voice MCP server 未注册，告诉用户先运行 `Codex mcp add voice -- <venv-python> scripts/voice/mcp_server.py`（详见 `scripts/voice/README.md`）。
