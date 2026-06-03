# ADR — #468 voice-zh-input 升级为双向语音 agent 交互系统

> 状态:ACCEPTED(2026-06-03,项目 owner 定方向:完整三组件一次做完 + Kokoro-FastAPI 独立服务)
> 上游调研:`.research/reports/RESEARCH-468-voice-agent-2026-06-03.md`(autoresearch 4 轮,gate PASS,verify 加权 5.0)
> 关联:#465(已交付 voice-zh-input 输入 daemon,PR #467 → `71aabe4`);本 Issue #468 升级

---

## 1. 背景与需求

#465 交付了外围中文语音**输入**(`scripts/voice-zh-input.py`:连续 VAD 听写 + 剪贴板+Ctrl+V 注入,faster-whisper large-v3 GPU)。#468 升级为**双向语音 agent 交互系统**,项目 owner 明确三点需求:

1. **从 Claude Code CLI 内用指令直接调用**中文语音(slash command / 会话内集成,而非只独立终端 daemon)。
2. **按问题深度分流 + 快响应**,≥2 模式:
   - **秘书模式**:高速,同步记录+总结用户发言(信息快速录入,**不执行任务**)。
   - **工作模式**:基于已掌握的需求/目标/细节/规范**自主推进任务**;遇决策/补充信息/完成时**主动提示用户**回到语音交互(双向)。
   - **显式声明切换**。

## 2. 决策

### 2.1 mount-vs-build:**架构 B(`scripts/` 自研粘合)延续,NOT mount VoiceMode**

**决定性理由**:VoiceMode(mbailey/voicemode,MIT,最值得参考)官方**原生 Windows 不支持(仅 WSL2)**,其后端服务管理走 launchd/systemd(无 Windows),与 Mercury 原生 Windows 11 + Python 3.14 + RTX 4060 直接冲突 [https://pypi.org/project/voice-mode/]。直接 mount(submodule + 薄适配)代价过高且与目标平台冲突。

**但按层借鉴 VoiceMode 接口设计**:① `converse` 工具的 `wait_for_response` 语义(true=说+听阻塞返回转写;false=仅 TTS 播报);② STT/TTS 经 OpenAI 兼容 HTTP 端点解耦的思想。

### 2.2 TTS 选型:**Kokoro-FastAPI 独立服务**

本地隐私优先 → Kokoro-FastAPI(Apache-2.0、离线、4060 上 ~300ms 首音、8 普通话音色 zf_*/zm_*)作为独立 HTTP 服务运行,**用其自带 `.python-version` 隔离 Kokoro 的 `Requires-Python <3.13` 硬锁**(主 voice 模块留 Python 3.14)[https://github.com/remsky/Kokoro-FastAPI] [https://pypi.org/project/kokoro/]。`edge-tts`(在线、copyleft)仅作零配置应急回退,默认不启用。

> Kokoro-FastAPI 作为**运行时外部服务**(类似数据库),非代码 vendor —— 经其文档化 Docker/uv 部署,不入 git、不走 cherry-pick 协议;setup 步骤记 README。TTS 客户端只发 OpenAI 兼容 `/v1/audio/speech` HTTP 请求。

### 2.3 三组件架构(全 Mercury-internal `scripts/`,无 LOC 上限)

```
scripts/voice/                      # 新增可拆卸 package
  __init__.py
  stt.py        # STT 层:faster-whisper 加载 + 转写 + 麦克风/VAD(抽取自 #465,共享)
  tts.py        # TTS 层:Kokoro-FastAPI OpenAI 兼容客户端 + edge-tts 回退 + 播放
  state.py      # 双模式状态机 + 秘书结构化记录
  mcp_server.py # stdio MCP server:listen / announce / set_mode / record_note 工具
scripts/voice-zh-input.py           # #465 daemon:重构为 import scripts.voice.stt(不重复 STT)
.claude/hooks/voice-stop-notify.sh  # Stop hook:读 last_assistant_message → 信号 → TTS 播报
.claude/commands/secretary.md       # /secretary 切秘书模式
.claude/commands/work.md            # /work 切工作模式
```

**模块化合规**:`scripts/voice/` 整体可拆卸(在任何 repo 复制 package + `claude mcp add` 即用);不依赖 Mercury 其他模块。

## 3. CLI 内调用机制:自建 stdio MCP server

`claude mcp add voice -- python scripts/voice/mcp_server.py`(stdio 为本地默认,原生 Windows 可行,`python script.py` 真实 PATH 二进制无需 `cmd /c` 包裹)[https://code.claude.com/docs/en/mcp]。用 PyPI `mcp` / FastMCP 高层 API [https://github.com/modelcontextprotocol/python-sdk]。

暴露工具(借鉴 converse 语义,模型在会话内调用):
- `listen()` → 阻塞录音 + faster-whisper 转写,返回中文字符串(借鉴 `converse(wait_for_response=true)`)。
- `announce(text)` → 仅 Kokoro TTS 播报,不监听(借鉴 `wait_for_response=false`,= agent check-in 播报原语)。
- `set_mode(mode)` → secretary | work,切状态机。
- `record_note(text)` → 秘书模式结构化记录(写笔记文件)。

## 4. 双向闭环(keystone):两条互补机制

- **(A) 模型主动(主)**:工作模式遇决策/缺信息/完成时,模型自身调 `announce()` 播报 + 可选 `listen()` 拉用户语音回应。模型可控 check-in 时机。
- **(B) Stop hook 兜底**:`.claude/hooks/voice-stop-notify.sh` 在每回合结束自动读 `last_assistant_message`(兜底解析 `transcript_path` JSONL `.message.content[0].text`)→ 写信号文件 → voice daemon 监听 → Kokoro 播报。先例 `ktaletsk/claude-code-tts` 已用 Kokoro 同构实现 [https://github.com/ktaletsk/claude-code-tts]。

> **agent 无法回合间自发输出**——主动 ping 必经 harness 层(hook)或模型调工具,设计已遵循 [https://code.claude.com/docs/en/hooks]。

## 5. 双模式状态机

```
[IDLE] --/secretary--> [SECRETARY]
[SECRETARY]: 低延迟 STT(medium/large-v3-turbo) 连续转写 → record_note 结构化记录,不执行任务
[SECRETARY] --用户显式声明(/work)--> [WORK]
[WORK]: 模型基于秘书阶段掌握的需求/目标/规范自主推进任务
[WORK] --决策点/缺信息/完成--> announce(TTS check-in) --> listen(拉语音) --> 回 [WORK] 或 [SECRETARY]
```

- 秘书→工作切换:**显式**(用户声明 `/work` 或语音口令),非自动推断。
- 秘书模式可降模型尺寸(medium 2.9% WER / large-v3-turbo ~2.7x 快)降延迟。
- 状态持久化:状态文件 `.mercury/state/voice-mode.json`(mode + 当前任务上下文 + 笔记路径),hook 与 MCP server 共享读。

## 6. 风险登记(实现期实测项)

| 风险 | 缓解 |
|---|---|
| `last_assistant_message` 官方 hooks doc 未列(仅二手源+社区先例),version/condition-specific | 兜底解析 `transcript_path` JSONL;实测确认字段存在性 |
| Stop hook 用户中断/取消是否触发 = UNVERIFIED | 实现期实测;`stop_hook_active` 防死循环 |
| Kokoro `<3.13` 锁绕过运行行为无实测 | Kokoro-FastAPI 独立服务/venv 隔离(已成立) |
| `/clear` 损坏 transcript 破坏解析 | 优先用 `last_assistant_message` 字段规避 |
| 快速连续响应音频重叠 | TTS 播放前 kill 前一进程 |
| MCP `listen()` 阻塞与 Claude Code 工具超时 | 设合理 `listen_duration_max`;实测工具调用超时边界 |

## 7. 验收标准

1. `claude mcp add voice -- python scripts/voice/mcp_server.py` 后,会话内模型可调 `listen`/`announce`/`set_mode`/`record_note`。
2. `announce("测试")` 触发 Kokoro 中文 TTS 实际出声。
3. `listen()` 录音并返回中文转写。
4. 秘书模式:连续转写 + 结构化记录到笔记文件,不执行任务。
5. 工作模式:模型遇 check-in 点调 `announce` + `listen` 双向。
6. Stop hook 在回合结束播报 agent 回复(兜底机制)。
7. `scripts/voice/` 可独立拆出;#465 daemon 重构后无回归。
8. dual-verify 通过;所有外部 SDK/API web-verified。

## 8. 引用

- VoiceMode 原生 Windows 不支持:[https://pypi.org/project/voice-mode/]
- converse 签名(主源):[https://raw.githubusercontent.com/mbailey/voicemode/master/voice_mode/tools/converse.py]
- Kokoro 中文音色 + Apache-2.0:[https://huggingface.co/hexgrad/Kokoro-82M/raw/main/VOICES.md] [https://pypi.org/project/kokoro/]
- Kokoro-FastAPI 独立服务:[https://github.com/remsky/Kokoro-FastAPI]
- Claude Code MCP / hooks:[https://code.claude.com/docs/en/mcp] [https://code.claude.com/docs/en/hooks]
- Python MCP SDK:[https://github.com/modelcontextprotocol/python-sdk]
- Stop hook → Kokoro TTS 先例:[https://github.com/ktaletsk/claude-code-tts]
