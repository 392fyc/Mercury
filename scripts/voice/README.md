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
  voice_queue.py  per-session transcript FIFO 队列(原子入队 + O_EXCL exactly-once 出队 + watermark 时间锚定;#495 Slice 2)
  listen_daemon.py 常驻 STT daemon(模型 A:独占 mic、转写入队;enqueue-only 半双工;#495 Slice 3)
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

验证:`curl http://localhost:8880/v1/audio/voices` 应返回音色列表(含 `zf_xiaoyi` 等普通话音色)。

> 来源:<https://github.com/remsky/Kokoro-FastAPI>(Apache-2.0)。OpenAI 兼容端点
> `POST /v1/audio/speech`,请求 `{"model":"kokoro","input":...,"voice":"zf_xiaoyi","response_format":"wav"}`。

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

## Path 2:常驻 STT daemon(模型 A,opt-in)

默认 `listen()` 每次自开麦克风录一句(#468 行为)。Path 2 引入**常驻 STT daemon**:由**单个进程独占麦克风**、always-on 转写、把每句话写进 per-session transcript 队列(`voice_queue`);此时 `listen()` 改为**读队列**而非自开第二条音频流——消除两条 `InputStream` 抢同一设备在 Windows 上的 `PortAudio -9998`(实测)。

**启动 daemon**(独立终端,用共享 venv):

```bash
# 单会话(默认 session):
.venv-voice/Scripts/python.exe scripts/voice/listen_daemon.py
# 多 lane:用 VOICE_QUEUE_SESSION 指定 session(daemon 与会话内 listen() 须用同一值):
VOICE_QUEUE_SESSION=lane-x .venv-voice/Scripts/python.exe scripts/voice/listen_daemon.py
```

session **只**来自 `VOICE_QUEUE_SESSION`(与 listen() 同一解析通道,故二者永不错位)。daemon 起来后 `listen()` 自动走队列;**不起 daemon 则一切如 #468**(完全 opt-in、向后兼容)。`Ctrl+C` / `SIGTERM` 干净退出(释放设备 + 删 pidfile)。

> ⚠️ **半双工(默认)/ 建议用耳机**:daemon 始终开麦,而 `announce` 的 TTS 由扬声器播放。**同房间扬声器+麦克风**会让 daemon 听到 Kokoro 自己的声音并转写成幽灵「用户说」。默认做**半双工门控**(持 `voice-tts.lock` 时 daemon 丢弃采集),所以**播报期间麦克风是聋的**(安全的轮流对讲)。
>
> **opt-in barge-in(#495 Slice 4,默认关)**:设 `VOICE_BARGEIN=1` 后 daemon **不再**在播报期间静音,而是检测到用户开口(onset)时写一个 generation-绑定的停播信号(`voice-tts.stop`,绑定当前播放进程 pid),`announce` 播放循环每 ~50ms poll、命中即 `sd.stop()` 提前停播 —— 实现「说话盖过 agent 即停」。**⚠️ 仅耳机/AEC 环境可靠**:扬声器下 daemon 会把 Kokoro 自己声音误当 onset → 几乎每次播报被假打断 + 回授幽灵句,故默认关;真正的扬声器 barge-in 需 AEC,超范围。停播信号 generation-绑定(target pid)+ 每次取锁清陈旧信号,故陈旧信号**绝不**截断下一句。
>
> ⚠️ **真 mic 验证**:daemon 的采集/转写/-9998 规避/声学回授等行为依赖真实音频硬件 + Kokoro 服务,headless 单测覆盖逻辑(队列读写、心跳/pid 自愈、半双工门控、enqueue-only 无 paste),但**端到端需在真麦克风环境验证**:① 起 daemon,说一句,确认队列出现 `utt-*.json`;② 会话内 `listen()` 返回该句;③ `announce` 播报期间说话**不**被转写入队(半双工);④ `taskkill` daemon 后 `listen()` 退回自开麦不卡死。

---

## 配置(env)

| 变量 | 默认 | 说明 |
|---|---|---|
| `VOICE_ZH_MODEL` | `large-v3` | 工作/idle 模式 faster-whisper 模型 |
| `VOICE_ZH_MODEL_SECRETARY` | (同上) | 秘书模式模型(可设 `medium` / `large-v3-turbo` 降延迟) |
| `VOICE_ZH_DEVICE` | `auto` | `auto` / `cuda` / `cpu` |
| `VOICE_ZH_DEVICE_INDEX` | 系统默认 | 显式输入设备序号(`voice-zh-input.py --list-devices` 查看) |
| `VOICE_TTS_BASE_URL` | `http://127.0.0.1:8880/v1` | Kokoro-FastAPI 基址 |
| `VOICE_TTS_VOICE` | `zf_xiaoyi` | 普通话音色(`zf_*` 女 / `zm_*` 男;`zf_xiaobei` 带北方口音,#472 实测弃用) |
| `VOICE_TTS_MODEL` | `kokoro` | Kokoro 模型 id |
| `VOICE_TTS_SPEED` | `1.0` | 语速 |
| `VOICE_TTS_FALLBACK` | (空=关) | 设 `edge` 启用 edge-tts 在线回退 |
| `VOICE_TTS_EDGE_VOICE` | `zh-CN-XiaoxiaoNeural` | edge-tts 音色 |
| `VOICE_TTS_TIMEOUT` | `30` | Kokoro HTTP 超时(秒) |
| `VOICE_TTS_LOCK_WAIT` | `8` | 跨进程播放锁等待秒数(超时则跳过本次播报) |
| `VOICE_TTS_MAX_CHARS` | `600` | `speak()` 播报文本截断长度(所有调用方) |
| `VOICE_LISTEN_ONSET_GRACE` | `10` | `listen` 等待用户开口的 onset 窗口(秒) |
| `VOICE_PRE_RECORD_SEC` | `0.3` | onset 前预录环形缓冲时长(秒),补回句首被 VAD 截断的清音/气口;`0`=关,有效范围 `(0, 10]`,超出/非法值告警并回退默认 0.3(永不崩 listen)。实际 maxlen=`max(ceil(秒/块时长), onset_blocks)`,保证≥onset 窗口故永不丢 onset 音;预录的静音前导不计入 `min_sec` 短句门;>0.5 有把室噪/键盘声 prepend 进首字的风险(#495) |
| `VOICE_VAD_FACTOR` | `2.5` | 能量 VAD 阈值=噪声地板×该倍率(低增益麦克风语音仅 ~3.5× 噪声,故默认 2.5;#472) |
| `VOICE_VAD_THRESH` | (空=自动校准) | 能量 VAD 绝对阈值覆盖(RMS,设置后跳过自动校准) |
| `VOICE_QUEUE_SESSION` | (空=`default`) | transcript 队列 + daemon pidfile 的 session 键(多 lane 隔离;**daemon 与 listen() 端须一致**,否则各读各的队列) |
| `VOICE_DAEMON_HEARTBEAT_SEC` | `5` | daemon 心跳(pidfile mtime)刷新基准秒;`daemon_active()` 判活的过期阈值=2× |
| `VOICE_DAEMON_SILENCE_SEC` | `0.8` | daemon 判一句结束的尾静音秒数 |
| `VOICE_DAEMON_MIN_SEC` | `0.4` | daemon 最短有效句长(秒),短于此丢弃 |
| `VOICE_DAEMON_ONSET_BLOCKS` | `3` | daemon 起话所需连续浊音块数(防单次噪声尖峰开句) |
| `VOICE_DAEMON_MAX_SEC` | `20` | daemon 单句最长秒数,超过即强制 finalize(防永不静音输入让缓冲无界增长) |
| `VOICE_DAEMON_QUEUE_MAX` | `200` | daemon 音频块队列上限(块,~50ms/块);backpressure 下丢最新块而非无界增长内存 |
| `VOICE_BARGEIN` | (空=关) | 设 `1` 启用 opt-in barge-in(#495 Slice 4):daemon 播报期间不静音,检测 onset 即写停播信号停 TTS。**仅耳机/AEC 环境可靠**(扬声器会假打断 + 回授幽灵句);默认关=半双工 |
| `VOICE_BARGEIN_POLL_MS` | `50` | `announce` 播放循环 poll 停播信号的间隔(毫秒);barge-in 响应延迟上界 |
| `VOICE_STATE_DIR` | `<repo>/.mercury/state` | 状态/笔记目录(也存跨进程播放锁) |
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
- **Path 2 daemon 半双工(默认,#495 Slice 3)**:daemon 持 `voice-tts.lock` 时丢采集 → 播报期间麦克风聋(安全轮流对讲)。
- **opt-in barge-in(#495 Slice 4,默认关)**:`VOICE_BARGEIN=1` 后 daemon 播报期间不静音,onset 即写 generation-绑定
  停播信号(`voice-tts.stop`)让 `announce` 提前停;**仅耳机/AEC 环境可靠**(扬声器会假打断 + 回授幽灵句),
  真扬声器 barge-in 需 AEC 超范围。可中断播放用 elapsed-time 边界而非 `sd.get_stream()`(§8:后者可能轮询错流)。
- **Path 2 daemon 崩溃自愈**:daemon 仅在 `InputStream` 起来后写 pidfile,退出(`finally`/`atexit`/`SIGTERM`)删之,
  每轮刷新 mtime 作心跳;`daemon_active()` 要求 **pid 活 AND 心跳新鲜**双门,故 daemon 崩溃(泄漏设备/pid 回收假活)时
  `listen()` 退回自开麦而非卡在永不填充的队列;自开麦若撞 `-9998`(残留进程占麦)返回清晰错误而非崩工具。
- **daemon 与 listen() 的 session 一致性**:跨 lane 共享 `.mercury/state`,队列与 pidfile 按 `VOICE_QUEUE_SESSION` 隔离;
  daemon 启动 session 须与会话内 `listen()` 的 `VOICE_QUEUE_SESSION` 相同,否则 `listen()` 读不到 daemon 写入的队列。
- **listen() 时间锚定**:model A 下 `listen()` 只返回**调用之后**说的话(watermark),提问前的积压由 Stop-hook drain(Slice 5)消费,
  不会被误当成回答。
