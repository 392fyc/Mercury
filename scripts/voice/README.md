# voice — 双向中文语音 agent 交互 (Mercury #468)

在 Claude Code **会话内**用指令直接调用中文语音交互的一套可拆卸组件,建立在 #465
`scripts/voice-zh-input.py`(外围中文语音输入)之上。提供:

- **会话内调用**:本地 stdio MCP server,模型可在会话中调用 `listen` / `announce` 等工具。
- **秘书 / 工作双模式**:秘书模式快速记录+总结(不执行任务),工作模式自主推进任务并在
  决策/缺信息/完成时主动语音提示(双向)。
- **双向闭环**:模型主动 `announce`+`listen`(主),Stop hook 回合末播报回复(兜底)。

设计决策见 ADR:`.mercury/docs/research/issue-468-voice-agent-adr-2026-06.md`。
调研报告:`.research/reports/RESEARCH-468-voice-agent-2026-06-03.md`。

> **本地隐私**:STT(faster-whisper)与首选 TTS(Kokoro-FastAPI)全部本地离线运行,
> 音频不出本机。edge-tts 回退是在线服务,默认关闭。

---

## 架构

```
scripts/voice/
  stt.py        STT 层:faster-whisper 加载 + 转写 + 单句阻塞录音(复用 #465 原语)
  tts.py        TTS 层:Kokoro-FastAPI(OpenAI 兼容)客户端 + edge-tts 回退 + 播放
  state.py      双模式状态机 + 秘书结构化记录(.mercury/state/voice-mode.json)
  mcp_server.py stdio MCP server:listen / announce / set_mode / record_note / get_status
  stop_notify.py Stop-hook worker:回合末读 last_assistant_message → TTS 播报
.claude/hooks/voice-stop-notify.sh   Stop hook 包装(opt-in,默认不注册)
.claude/commands/secretary.md|work.md  /secretary、/work 模式切换 slash command
```

`scripts/voice/` 整体可拆卸:复制该目录 + 装依赖 + `claude mcp add` 即可在任意 repo 使用。

---

## 安装

### 1. 依赖(装进 #465 共享 venv)

```bash
uv pip install --python .venv-voice/Scripts/python.exe -r scripts/voice/requirements-voice-agent.txt
```

### 2. TTS 服务:Kokoro-FastAPI(首选,本地离线)

Kokoro 的 pip 包锁 Python `<3.13`,与本 venv 的 3.14 冲突,因此 TTS 作为**独立服务**运行
(自带 Python 环境,隔离版本锁)。两种部署(任选其一):

- **Docker(GPU,需 Docker Desktop + WSL2 后端做 GPU 直通)**:
  ```bash
  docker run --gpus all -p 8880:8880 ghcr.io/remsky/kokoro-fastapi-gpu:latest
  ```
- **本地 uv/uvicorn(原生 Windows)**:克隆 `github.com/remsky/Kokoro-FastAPI`,装 astral-uv +
  espeak-ng,运行其 GPU 启动脚本(见该 repo README;启动后监听 :8880)。

验证:`curl http://localhost:8880/v1/audio/voices` 应返回音色列表(含 `zf_xiaobei` 等普通话音色)。

> 来源:<https://github.com/remsky/Kokoro-FastAPI>(Apache-2.0)。OpenAI 兼容端点
> `POST /v1/audio/speech`,请求 `{"model":"kokoro","input":...,"voice":"zf_xiaobei","response_format":"wav"}`。

### 3. 注册 MCP server

```bash
# 从仓库根运行;$CLAUDE_PROJECT_DIR 在 Claude Code 内即仓库根(也可换成你的仓库绝对路径)
claude mcp add voice -- "$CLAUDE_PROJECT_DIR/.venv-voice/Scripts/python.exe" "$CLAUDE_PROJECT_DIR/scripts/voice/mcp_server.py"
```

(stdio 为默认传输;真实 PATH `python` 二进制在原生 Windows 上无需 `cmd /c` 包裹。`claude mcp add` 会把命令按当前工作目录解析,故在仓库根执行;若你的 venv 在别处,替换为对应绝对路径。)
注册后在会话内即可让模型调用 `listen` / `announce` / `set_mode` / `record_note` / `get_status`。

### 4. (可选)Stop hook 双向兜底

默认**不注册**(避免给全团队每回合 spawn 进程)。要启用,**往现有 `hooks.Stop[0].hooks`
数组里追加一条 command hook**(与现有 `stop-guard.sh` 并列,不要替换整个 `Stop`,否则会
破坏现有 hook)。本仓库当前结构是:

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          { "type": "command", "command": "bash \"$CLAUDE_PROJECT_DIR/.claude/hooks/stop-guard.sh\"", "timeout": 10 },
          { "type": "command", "command": "bash \"$CLAUDE_PROJECT_DIR/.claude/hooks/voice-stop-notify.sh\"", "timeout": 10 }
        ]
      }
    ]
  }
}
```

(上面只新增了 `voice-stop-notify.sh` 那一行,`stop-guard.sh` 原样保留。)
worker 自守卫:仅当语音模式 active(mode ≠ idle)时才播报,非语音会话静默 no-op。

---

## 使用

1. `/secretary` — 进入秘书模式:模型用 `listen` 拉你的语音并自动 `record_note`,只记录总结
   不执行任务。说清某任务需求后,显式 `/work` 切换。
2. `/work` — 进入工作模式:模型基于秘书记录自主推进任务,遇决策/缺信息/完成时 `announce`
   主动语音提示,需要你回应时紧接 `listen`。
3. `voice-zh-input`(#465)本身仍可独立作纯输入用:`python scripts/voice-zh-input.py`。

---

## 配置(env)

| 变量 | 默认 | 说明 |
|---|---|---|
| `VOICE_ZH_MODEL` | `large-v3` | 工作/idle 模式 faster-whisper 模型 |
| `VOICE_ZH_MODEL_SECRETARY` | (同上) | 秘书模式模型(可设 `medium` / `large-v3-turbo` 降延迟) |
| `VOICE_ZH_DEVICE` | `auto` | `auto` / `cuda` / `cpu` |
| `VOICE_ZH_DEVICE_INDEX` | 系统默认 | 显式输入设备序号(`voice-zh-input.py --list-devices` 查看) |
| `VOICE_TTS_BASE_URL` | `http://127.0.0.1:8880/v1` | Kokoro-FastAPI 基址 |
| `VOICE_TTS_VOICE` | `zf_xiaobei` | 普通话音色(`zf_*` 女 / `zm_*` 男) |
| `VOICE_TTS_FALLBACK` | (空=关) | 设 `edge` 启用 edge-tts 在线回退 |
| `VOICE_TTS_EDGE_VOICE` | `zh-CN-XiaoxiaoNeural` | edge-tts 音色 |
| `VOICE_STATE_DIR` | `<repo>/.mercury/state` | 状态/笔记目录 |
| `VOICE_STOP_MAX_CHARS` | `400` | Stop hook 播报截断长度 |

---

## 已知限制(实测项,见 ADR §6)

- `last_assistant_message` 字段官方 hooks 文档未列(社区先例),stop_notify 兜底解析 transcript。
- **stop_notify 兜底 fail-open**:`last_assistant_message` 缺失且 transcript 被 `/clear` 损坏/截断时,
  解析可能读出较旧的回复(而非保持静默)。优先用 `last_assistant_message` 字段已规避主路径。
- **TTS 防重叠**:announce(MCP server 进程)与 stop_notify(Stop hook 进程)经状态目录下的
  `voice-tts.lock` 跨进程文件锁串行播放;抢不到锁(`VOICE_TTS_LOCK_WAIT` 秒内)则跳过本次播报。
- **状态写非全局加锁**:`set_mode`/`record_note` 为 read-modify-write,JSON 写本身原子(读者不会读到半包),
  但实际写者只有单进程单线程的 MCP server,跨进程并发写概率极低;如未来多写者需加锁。
- Kokoro-FastAPI GPU 在原生 Windows 经 Docker 需 WSL2 后端;纯原生走 uv/uvicorn 路线。
- 秘书模式"快响应"可按需把 `VOICE_ZH_MODEL_SECRETARY` 降到更小模型。
- MCP `listen` 阻塞:等待开口的 onset 窗口由 `VOICE_LISTEN_ONSET_GRACE`(默认 10s)控制,
  静默时最长阻塞 ≈ `max_seconds + onset_grace`,留意 Claude Code 工具调用超时预算。
