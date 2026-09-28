# Mercury #495 — Voice 对话模式升级 实现设计 + 对抗式评审

> 来源:ultracode verify+design+critique workflow(9 agents,2026-06-21)。
> 调研报告 `.research/reports/RESEARCH-voice-conversation-mode-2026-06-11.md`。
> **本 session 已落地 Slice 1(ring buffer);Slices 2-5 为 follow-up,合并门见 §8 对抗式发现。**

---

# Mercury #495 — Voice 对话模式升级实现设计（路径1 ring-buffer + 路径2 常驻 daemon / Stop-hook 队列推送 / 伪 barge-in）

工作树：`/d/Mercury/Mercury-468voice`（develop base，与 #468 同树）。所有路径以仓库根为基准。

---

## 1. Decisions（拍板两个 make-or-break 未知项）

### 决策 A — 路径2 Stop-hook 注入机制 + loop-guard

**定论：用 `{"decision":"block","reason":<出队 transcript>}` + exit 0，loop-guard 用「`stop_hook_active` 为真 OR 队列为空 → exit 0」双条件。不要用 `additionalContext`、不要用 `UserPromptSubmit`、不要用 `Notification`。**

依据（strand-1 "Stop hook voice transcript delivery"，VERIFIED）：

- `{decision:block, reason}` exit 0 是**唯一**能重新唤起模型的 hook 输出——`decision=block` 阻止本回合停止，`reason` 作为「下一条指令」交付给 Claude。这正是路径2「干活回合结束后把队列里攒的话喂回去继续」要的语义。
- `additionalContext`（Stop/SubagentStop/UserPromptSubmit）只能**给一个已经在跑的回合追加上下文**，回合已结束时它不重新唤起模型——无法用于「回合边界注入下一轮」。`Notification` 是无决策、仅 stderr-to-user，更不行。
- in-repo 双证据：`.claude/hooks/auto-handoff-stop.sh:129` 与 `stop-guard.sh` 都实跑 `{"decision":"block","reason":...}` exit 0 来把指令喂回 Claude。本设计直接复用这一同族模式。
- **loop-guard 必须双守卫**（strand-1 标注 DeepWiki 的 queue-state-vs-`stop_hook_active` 细节为 UNVERIFIED，故 BOTH guards）：
  1. `payload.stop_hook_active == true` → exit 0（重入保护，镜像 `stop_notify.py:77`）；
  2. 队列为空 → exit 0（只有队列非空才允许 block）。
- **reason 内的 transcript 必须 `json.dumps` 序列化，绝不字符串拼接**（Windows 路径含反斜杠、用户语音含引号都会破坏 JSON——`auto-handoff-stop.sh:124-129` 注释明确点了这个坑，它因此只用静态 reason 串）。

**与 strand-5（Mercury 现有 Stop hook 阵列）的协同结论**：这是一条 block，与 `stop-guard.sh` / `#469` 这两个唯一的 blocker 同处一个顺序数组。排在**最后**，且**自守卫**到 idle/空队列时静默 exit 0。详见 §4。

> 注意 strand-2（mcp-voice-hooks）的 hook 是「pull：拦截动作 → 强制 dequeue → block 该动作」。Mercury 这里用的是**回合边界**的 Stop block（turn-boundary），不是 mid-tool-call 真打断。它**关闭的是回合边界聋区，不是 mid-tool-call 聋区**。真正的「干活中间我说的话不丢」靠的是**队列不丢 + 下个回合边界强制喂入**（队列在 daemon 持续写、Stop hook 在每个回合末 drain），这与 strand-2 的三态队列思路一致但落点是 Stop 而非 PostToolUse（见 §5 为何本期不做 PostToolUse）。

### 决策 B — mic 所有权模型

**定论：模型 A —— daemon 独占 mic，`listen()` 退化为「读队列里最近一条 utterance」。不做单采集 fan-out（模型 B）。**

依据（strand-4 "精确代码地图" + strand-5，VERIFIED）：

- 两个 `sd.InputStream` 指向同一设备在 Windows MME/WASAPI + Realtek 驱动下不能可靠共存：`stt.py:230`（listen 自开流）与 `voice-zh-input.py:573`（daemon 持续流）都不带任何共享/独占协调，#495 正文已实测 `-9998`（paInvalidChannelCount，设备忙的典型表现）。`stt.py:182-192` 注释记载该驱动 forcing 16k 返回数字静音等多处非标准行为——不能用乐观共存假设。
- 模型 A 让 daemon（P2）成为**唯一 mic 所有者**，`listen()` 改读队列 → 一举消除争用，且顺带消除聋区（干活期间说的话也进了队列）。
- 模型 B（单采集进程 fan-out 给两个消费者）需新写一个 audio fan-out 总线（block 广播 + 两套 VAD 状态机），远比 A 复杂，且 `listen()` 本就该读 daemon 已转写的文本而非再跑一遍 VAD/STT。A 用最少新代码达成 #495「明确所有权」验收。

**所有权矩阵（必须写进 README，互斥）**：同一设备同一时刻只能有一个 mic 所有者。三者——旧 `voice-zh-input.py`（#465 纯输入 daemon）/ 新 `listen_daemon.py`（路径2）/ 旧 `listen()` 自开流——**不能同设备并发**。路径2 启用后，`listen()` 不再自开流（改读队列），旧 `voice-zh-input.py` 若要跑须换设备或停掉 daemon。

---

## 2. 路径1 设计 — `stt.py` `listen_once` 加 ring-buffer

**目标**：补回 VAD onset 前那 ~150ms+ 安静前导音（首字辅音/气口），消除句首截断。

**唯一改点**：`scripts/voice/stt.py` `SttEngine.listen_once`（194-266 行）。`_calibrate`（268-322）**完全不动** → #472 校准零回归。

**核心原理**：现有 `pending` 累加器只在 voiced 时攒、**任何 non-voiced block 即清空**（`stt.py:249`），所以静音→开口瞬间的前导音被丢。新加一个**独立于 `pending`、始终滚动**的定长 deque，每个 block（onset 前）都压入（不管 voiced），onset 触发时用它**整体 seed `seg`**（替换 `pending` 的 seed），避免双重计数。

**防双重计数关键**：deque 在 onset 时已经是 `pending` 的**超集**（含那 3 个触发 block + 前导静音），所以 `seg = list(deque)` 单独就完整正确；若写 `list(deque) + pending` 会把 3 个 onset block 重放两次（句首 ~150ms 口吃）。**SEED（替换）而非 PREPEND（追加）**——依据 strand-3 RealtimeSTT `frames = list(audio_buffer)` 的 seed-from-buffer 模式（`recording_buffers.py:112` / `lifecycle.py:205-208`）。

### Pseudo-diff（against `scripts/voice/stt.py`）

**改动1 — stdlib import（18-24 行那块）**

```python
 import queue
 import threading
+import collections   # #495: deque ring buffer for pre-record
+import math          # #495: ceil for ring maxlen
```

**改动2 — env 解析 + deque 初始化（在 `block_dur = blocksize / capture_sr` 即 211 行之后）**

```python
         capture_sr = self.capture_samplerate()
         blocksize = max(256, int(0.05 * capture_sr))  # ~50ms blocks
         block_dur = blocksize / capture_sr
+        # pre-record ring buffer (#495): keep the ~Ns BEFORE VAD onset so the leading
+        # syllable isn't clipped. Maintained on EVERY pre-onset block (voiced or not),
+        # unlike `pending` which resets on silence (line ~249). 0 disables. Validation
+        # mirrors #472 (_calibrate): a mistyped/non-finite/non-positive value must warn +
+        # fall back to default, never silently break listen().
+        pre_record_sec = 0.3
+        _env_pr = os.environ.get("VOICE_PRE_RECORD_SEC")
+        if _env_pr is not None and _env_pr != "":
+            try:
+                _v = float(_env_pr)
+                if _v == 0 or (np.isfinite(_v) and _v > 0):
+                    pre_record_sec = _v          # 0 = off; finite>0 = ring length (sec)
+                else:
+                    raise ValueError(_env_pr)
+            except ValueError:
+                print(f"[voice-stt] invalid VOICE_PRE_RECORD_SEC={_env_pr!r} "
+                      f"(need 0 or finite > 0), using default {pre_record_sec}",
+                      file=sys.stderr, flush=True)
+        # maxlen in BLOCKS = ceil(seconds / block_dur). ceil (not int) avoids rounding
+        # a sub-block duration down to a 0-length ring. 0 -> empty deque (feature off).
+        pre_blocks = math.ceil(pre_record_sec / block_dur) if pre_record_sec > 0 else 0
+        preroll = collections.deque(maxlen=pre_blocks)   # rolling pre-onset audio
         q = queue.Queue()
```

> maxlen 公式镜像 RealtimeSTT 的 `int((sample_rate//buffer_size)*duration)`（strand-3 `core/initialization.py:589-591`），用 `ceil(sec/block_dur)` 等价且对 sub-block 时长更稳。block 数随设备原生 `capture_sr` 自适应（不能硬编码 block 数）。

**改动3 — 每个 onset 前 block 都压 ring（while 体顶部，`voiced = rms > threshold` 即 237 行之后）**

```python
                 voiced = rms > threshold
+                if not in_speech and pre_blocks:
+                    preroll.append(block)   # ring auto-evicts oldest; superset of `pending`
                 if not in_speech:
```

> 只在 `not in_speech` 时压 → 镜像 RealtimeSTT 的 `if not self.is_recording` gate（strand-3 `recording.py:453`），speech 开始后 ring 冻结（utterance 中途不需要 pre-roll）。

**改动4 — onset 时从 ring SEED seg（替换 245 行 `pending` 的 seed）← 载重改动**

```python
                         if voiced_run >= onset_blocks:
                             in_speech = True
-                            seg, seg_samples = pending, pending_samples
+                            # Seed from the ring, NOT `pending`: the ring already holds the
+                            # onset_blocks voiced blocks PLUS the quiet lead-in. Using
+                            # `pending` would drop the lead-in (the #495 bug); using
+                            # ring+pending would DOUBLE-COUNT the onset blocks (in both).
+                            # The ring alone is correct and complete.
+                            if pre_blocks:
+                                seg = list(preroll)
+                                seg_samples = sum(len(b) for b in seg)
+                                preroll.clear()      # consumed; don't leak into next utterance
+                            else:
+                                seg, seg_samples = pending, pending_samples  # feature off
                             pending, pending_samples, voiced_run = [], 0, 0
                             silence_run = 0.0
```

**改动5（明确不做）**：non-voiced 重置路径（249 行 `pending,... = [], 0, 0`）**原样保留，绝不在此 clear `preroll`**——那个 reset 正是 `pending` 的原 bug，ring 的全部意义就是**挺过 non-voiced block**；deque 的 maxlen 自动管有界增长。

**不变量**：`voiced_run` 仍是唯一 onset 触发器（onset 行为不变）；`pending` 退化为「feature off 时的 fallback seed」；`silence_run` / `in_speech` / `cap` / `min_sec`（251-266 行）全不动；`_calibrate` 不动（#472 安全）。feature-off 路径（`VOICE_PRE_RECORD_SEC=0`）与今天**字节级等价**（改动3 skip、改动4 走 else 分支）。

**默认值 0.3**（vs RealtimeSTT 0.2）：Mercury `onset_blocks=3 × 50ms = 150ms` 触发延迟本身 ~150ms，加首个清音辅音想要 ~300ms look-back 才能完整复原首字。0.3s/0.05s ≈ 6 blocks（实际随 native `capture_sr` 缩放）。>0.5s 风险见 §7。

**daemon 同步**（路径2 依赖件）：`voice-zh-input.py` `ContinuousListener._run`（493-556 行，结构与 `stt.py` 同形）同样加 ring（改点同改动3/4，env 同 `VOICE_PRE_RECORD_SEC`），这样 daemon 转写也吃到 pre-roll。**本期路径1 只改 `stt.py`**；daemon 的 ring 随路径2 slice 落（见 §5）。

---

## 3. 路径2 设计 — 常驻 daemon / transcript 队列 / 队列-drain Stop hook / 伪 barge-in

### 3.1 组件总览（进程 / 文件 / 责任）

| 组件 | 路径 | 责任 | 关键 env |
|---|---|---|---|
| **P1 MCP-server** | `scripts/voice/mcp_server.py`（改） | own announce/TTS 播放（`tts._current` 在此进程）；`listen()` 改读队列 | 现有 `VOICE_*` |
| **P2 常驻 STT daemon** | `scripts/voice/listen_daemon.py`（**新**，复用 #465 `ContinuousListener`） | **独占 mic**、always-on、转写入队；检测 onset 触发伪 barge-in | `VOICE_ZH_*`、`VOICE_STATE_DIR`、`VOICE_PRE_RECORD_SEC` |
| **transcript 队列模块** | `scripts/voice/voice_queue.py`（**新**，注意不命名 `queue.py` 避免遮蔽 stdlib `queue`） | per-utterance JSONL 原子写/读/标 consumed | `VOICE_STATE_DIR` |
| 队列目录 | `.mercury/state/voice-queue/`（#495 指定） | 每条 utterance 一个 JSONL 行或一个时间戳文件 | — |
| **P3 队列-drain Stop hook** | `.claude/hooks/voice-queue-drain.sh` + `scripts/voice/queue_drain.py`（**新**） | 回合边界 drain 队列 → `{decision:block,reason}` 注入 | `VOICE_STATE_DIR`、`VOICE_QUEUE_MAX_ITEMS` |
| **barge-in stop-signal** | `.mercury/state/voice-tts.stop`（**新信号文件**） | daemon→播放进程的反向停播 channel | `VOICE_STATE_DIR` |
| TTS 播放（改造） | `scripts/voice/tts.py`（改 `_play_wav_bytes`） | 把阻塞 `sd.wait()` 换成可 poll stop-signal 的分块等待 | `VOICE_BARGEIN_POLL_MS` |

> **为何不复用 `state.record_note`**（strand-4 VERIFIED）：`record_note`（`state.py:116-150`）是 append-only markdown 笔记（写 `- **[时间] 记录**：text\n`），**无消费游标、无原子出队、读回要解析 markdown**——是「秘书笔记」语义。#495 要带消费指针的 FIFO。新建 `voice_queue.py` 复用 `state.py:_atomic_write`（50-63）的原子写 + `_state_dir()` 模式解析即可。

### 3.2 transcript 队列格式（`voice_queue.py`）

**目录**：`<VOICE_STATE_DIR>/voice-queue/`（默认 `.mercury/state/voice-queue/`）。

**格式**：**每条 utterance 一个文件**（不是单文件追加），文件名 `utt-<YYYYmmdd-HHMMSS-ffffff>.json`，内容：

```json
{"ts": "2026-06-21T14:03:11+08:00", "text": "你顺便把那个测试也跑一下", "consumed": false}
```

**为何一 utterance 一文件而非单 JSONL**（strand-5 风险「consume 的 read-modify-write 仍需 per-file rename 或锁」）：跨进程 P2 写 / P3 读+标 consumed 的并发下，**per-file rename 出队**比单文件 read-modify-write 简单且原子。出队 = `os.replace` 到 `consumed/` 子目录或直接 `os.remove`（rename 是 Windows 上的原子操作）。`_atomic_write` 只保证单次写原子，单文件方案的 consume 仍需额外锁——per-file 天然规避。

**API**（`voice_queue.py`）：

```python
def enqueue(text: str) -> str | None          # P2: 原子写一个 utt-*.json（复用 _atomic_write 风格）
def peek_all() -> list[dict]                   # P3: 按 ts 排序列出未 consumed 的
def drain(max_items: int) -> list[dict]        # P3: 取最多 max_items 条 + 原子 os.replace 到 consumed/ 子目录
def pop_latest() -> str | None                 # 模型A 的 listen(): 取最近一条 + 标 consumed
def is_empty() -> bool                         # drain hook 的载重自守卫
```

`drain` / `pop_latest` 的出队用 `os.replace(utt_path, consumed_dir/utt_name)`——拿到 rename 成功的才算「我消费的」，rename 失败（已被另一进程拿走）就跳过 → 防一条被消费两次。

### 3.3 listen() / MCP server 与 daemon 共存（模型 A 落地）

`mcp_server.py` `listen`（74-88 行）当前 `engine.listen_once(...)` 自开流。模型 A 下改成：

```python
@mcp.tool()
def listen(max_seconds: float = 20.0, silence_sec: float = 0.8) -> str:
    mode = _state.get_mode()
    if _daemon_active():                       # daemon 在跑 → 读队列，不碰 mic
        text = _voice_queue.pop_latest(wait_until=time.time() + max_seconds)
    else:                                      # 无 daemon → 退回自开流（向后兼容 #468）
        engine = _engine_for_mode(mode)
        text = engine.listen_once(max_seconds=max_seconds, silence_sec=silence_sec)
    if mode == "secretary" and text:
        _state.record_note(text, kind="note")
    return text
```

- `_daemon_active()`：检查 daemon 心跳文件（`.mercury/state/voice-daemon.pid` + `_pid_alive` 风格，复用 `tts.py:_pid_alive` 66-88）。daemon 在跑就走队列分支，**不再自开 mic** → 消除 `-9998`。
- daemon 不在跑时退回原 `listen_once` → **#468 向后兼容、可单独 detach**（路径2 是 opt-in：不起 daemon 就是今天的行为）。
- `_engine_for_mode`（47-71 行）保留——daemon **自身**转写复用 `SttEngine.transcribe`（`stt.py:117-180`），不是 MCP listen 工具 own mic。
- **超时语义重校准**（strand-4/5 风险）：模型 A 下 `listen()` 的 `max_seconds` 不再是「录音上限」，而是「等队列出现新 utterance 的超时」。`pop_latest(wait_until=...)` 轮询队列（参考 strand-2 mcp-voice-hooks `waitForUtteranceCore` 100ms 轮询 / active-flag 提前退出思路，**思路非代码**），到 `wait_until` 返回空串。工具超时预算（ADR §6）按此重标。

### 3.4 伪 barge-in 路径（daemon 检测 onset → 停 Kokoro 播放）

**IPC 障碍**（strand-4 VERIFIED）：announce 的实际播放（`tts.speak→_do→_play_wav_bytes`，297-308 行）跑在 **MCP-server 进程**，in-flight 句柄在该进程模块级 `_current` dict（`tts.py:56`）。daemon 是独立进程，看不到那块内存，**无法直接调 `_stop_current()`**（201-216 行只读同进程 `_current`）。现有 `voice-tts.lock` 协议（59-158 行）只防「同时播放」，没有「播放中被外部叫停」的反向 channel。

**方案：扩展 `voice-tts.lock` 协议 → 新增一个 stop-signal 文件 + 播放循环 poll**（strand-4 推荐，最小扩展，不引入 socket/命名管道）。

**信号文件**：`<VOICE_STATE_DIR>/voice-tts.stop`（与 `voice-tts.lock` 同目录，复用 `tts._lock_path()` 的 dir 解析）。

**daemon 侧**（`listen_daemon.py`）：检测到语音 onset（复用 `ContinuousListener` 的 `voiced_run >= ONSET_BLOCKS` 触发点，`voice-zh-input.py:523`）且 `voice-tts.lock` 存在（=有人在放）时，`touch` 写 `voice-tts.stop`（写当前时间戳 + 自己 pid，便于过期清理）。

**播放侧改造**（`tts.py` `_play_wav_bytes` 218-237 行）：把一次性阻塞 `sd.play; sd.wait()` 换成**分块 poll 等待**：

```python
def _play_wav_bytes(data):
    import numpy as np, sounddevice as sd
    ...  # decode 同现状
    _current["stream"] = True
    poll = float(os.environ.get("VOICE_BARGEIN_POLL_MS", "50") or "50") / 1000.0
    stop_signal = _lock_path().parent / "voice-tts.stop"
    try:
        sd.play(audio, sr)
        # 可中断等待：每 ~50ms poll 一次 stop-signal，存在则 sd.stop() 立即停（伪 barge-in）
        import time as _t
        while True:
            if not sd.get_stream().active:       # 正常放完
                break
            if stop_signal.exists():
                sd.stop()                        # barge-in：用户开口 → 停 TTS
                try: stop_signal.unlink()        # 消费信号
                except OSError: pass
                break
            _t.sleep(poll)
    finally:
        _current["stream"] = None
```

即把 `_stop_current` 的触发由「仅同进程内存」升级为「同进程内存 OR 外部信号文件」。`_play_mp3_file`（edge 回退路径，247-272 行 `time.sleep` 不可中断）**文档化为不支持 barge-in**（edge 是 opt-in 在线回退，非主路；strand-4 风险已点）。

stop-signal 的过期清理：drain 时若发现 `voice-tts.stop` 写者 pid 已死且 mtime 过期（复用 `_LOCK_STALE` / `_pid_alive` 风格），清掉——防陈旧信号误停下一次播放。

### 3.5 Stop hook 注入的精确 JSON（`queue_drain.py` 输出）

```python
# queue_drain.py main()，自守卫全过后：
items = voice_queue.drain(max_items=int(os.environ.get("VOICE_QUEUE_MAX_ITEMS", "5") or "5"))
if not items:
    return 0
transcript = "\n".join(f"- {it['text']}" for it in items)
reason = ("用户在你上一回合执行期间通过语音补充了以下内容（按时间顺序），"
          "请在继续前纳入考虑：\n" + transcript)
print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
return 0
```

- **`json.dumps` 序列化 reason**（含中文/引号/可能的反斜杠都安全），绝不拼接——遵 `auto-handoff-stop.sh` 的教训。
- `drain` 已把这几条 `os.replace` 到 `consumed/` → 即便 Claude 这回合又被 stop-guard 二次 block，下次重入 `stop_hook_active=true` 直接 exit 0，不会重放。
- `max_items` 上限（默认 5）配合 §4 的 hook timeout 预算，防 drain 超时被杀。

---

## 4. Hook 注册 — settings.json Stop 数组（opt-in + 自守卫 + 与现有 hook 组合）

**追加第三条 command hook 到现有 `hooks.Stop[0].hooks` 数组，排最后**（`stop-guard.sh` / `auto-handoff-stop.sh` 原样保留，绝不替换整个 Stop block）。依据 strand-5（README §4 + settings.json 108-123 实测结构）：

```json
"Stop": [
  {
    "hooks": [
      { "type": "command", "command": "bash \"$CLAUDE_PROJECT_DIR/.claude/hooks/stop-guard.sh\"", "timeout": 10 },
      { "type": "command", "command": "bash \"$CLAUDE_PROJECT_DIR/.claude/hooks/auto-handoff-stop.sh\"", "timeout": 30 },
      { "type": "command", "command": "bash \"$CLAUDE_PROJECT_DIR/.claude/hooks/voice-queue-drain.sh\"", "timeout": 15 }
    ]
  }
]
```

**opt-in（不提交进默认 settings.json）**：与 `voice-stop-notify.sh` 同政策——ship `voice-queue-drain.sh` 进 `.claude/hooks/`，注册步骤写进 `scripts/voice/README.md §4`，避免给全团队每回合 spawn Python 进程。**timeout=15**（>voice-stop-notify 的 10）：多条 drain + 阻塞 TTS 需更多 headroom；用 `VOICE_QUEUE_MAX_ITEMS` 封顶让总播放有界（否则 Claude Code 会 mid-playback 杀进程）。

**`voice-queue-drain.sh` 包装**（镜像 `voice-stop-notify.sh` 12-25 行，永远 exit 0）：

```bash
#!/usr/bin/env bash
set -euo pipefail
PROJ="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
PY="$PROJ/.venv-voice/Scripts/python.exe"
[ -x "$PY" ] || PY="$PROJ/.venv-voice/bin/python"
if [ ! -x "$PY" ]; then cat >/dev/null 2>&1 || true; exit 0; fi
"$PY" "$PROJ/scripts/voice/queue_drain.py" || true
exit 0
```

> 注意 bash 包装**不能**简单 `|| true` 吞掉 `queue_drain.py` 的 stdout——block JSON 必须原样透传给 Claude Code。`"$PY" ... || true` 会保留 stdout（`|| true` 只兜 exit code），✅。但若 Python 打了 block JSON 后非零退出，`|| true` 让 shell exit 0 而 stdout 已 flush，OK。

**`queue_drain.py` 自守卫顺序**（每条都静默 exit 0，**永不 emit block** 除了「队列非空」那条真注入；镜像 `stop_notify.py` 的失败即 exit 0）：

1. 读 stdin JSON。`payload.stop_hook_active == true` → exit 0（重入守卫，镜像 `stop_notify.py:77`；与 #469 不冲突——#469 用自己的 armed/retried marker，**不碰** `stop_hook_active`，strand-5 `auto-handoff-stop.sh:79-86` VERIFIED）。
2. voice venv 不存在（bash 层已挡）。
3. **voice mode active？** 用 `state.get_mode()`（**不要 bash 解析 JSON**——`voice-mode.json` 含 Windows 反斜杠路径，`state.py` 已处理 JSONDecodeError/OSError fail-to-idle）。`idle` 或读不到 → exit 0。
4. **队列非空？**（载重守卫）`voice_queue.is_empty()` → 空则 exit 0。
5. 任何异常（TTS down / 锁竞争 / 队列损坏）→ exit 0。

**与现有 hook 的冲突裁定（干净，无冲突）**——依据 strand-5 VERIFIED：

- **Claude Code 顺序跑 Stop hooks，任一 `{decision:block}` 即胜（回合不停）**。两个唯一 blocker 是 `stop-guard` / `#469`。drain hook 排最后，被它们的 block 抢占（用户想 commit 时 stop-guard 先 block，本回合不进入 drain 的注入——可接受，下一个干净 stop 再 drain，strand-5 风险已点）。
- **vs `stop-guard.sh`**：stop-guard 先跑，可能因暂存改动 block。drain 自身只在队列非空注入，且 drain 是 idempotent（`os.replace` 到 consumed），不会重放。
- **vs `#469 auto-handoff-stop.sh`**：状态不相交（#469 own `auto-handoff-armed/retried` + 不碰 voice state / `stop_hook_active`；drain own `voice-mode.json` + 队列 + `stop_hook_active`）；决策不相交（只有 #469/stop-guard 会 block，drain 的 block 只在「真有队列」时发，与它们语义正交）。谁赢 block：stop-guard/#469 先发的赢回合-停止决策。
- **vs `voice-stop-notify.sh`（若都注册）**：两者都读 `voice-tts.lock` → **播放串行化**（不重叠）。但可能**重复发声**（队列 announce + last-message notify）。**缓解（强烈建议）：合并成一个 Stop worker**——drain hook 顺带 own last-message notify，README §4 把单 worker 合并列为更干净选项。本设计的 `queue_drain.py` 预留：若检测到 `voice-stop-notify` 未单独注册，则在 drain 末尾追加 last-message 播报逻辑（复用 `stop_notify.py` 的 transcript 解析）。

**SubagentStop 无关**（strand-5 VERIFIED）：drain 属于 Stop 事件（主 agent 回合末），不挂 SubagentStop（那是 subagent 完成门，matcher dev/research）。

**治理**（用户级/settings 变更，遵 #469/#259 纪律）：opt-in 注册变更要开 Issue 记录 + 验证（settings.json JSON 合法 / hook 合成 stdin 下 exit 0 / 一次真实触发观察无回归）。

---

## 5. 本 session 的 scope 切分（现实主义）

| Slice | 内容 | 本期？ | 理由 |
|---|---|---|---|
| **Slice 1（本期全做）** | 路径1 ring-buffer：`stt.py` 改动1-4 + `VOICE_PRE_RECORD_SEC` env + README env 表追加 + 单测 | ✅ **完整实现+测试+合并** | 小、高置信、单文件、零新依赖（collections/math stdlib）、零 #472 回归面（`_calibrate` 不动）。strand-3 已给出精确 pseudo-diff + 双重计数防护。可独立 detach。 |
| **Slice 2（建议本期，若时间够）** | `voice_queue.py`（队列模块）+ 单测（纯文件 IO，无 mic） | ⚠️ **可本期** | 队列契约是路径2 真正的活（strand-5「hook 是简单的一半，队列契约才是真活」）。纯文件读写可完整单测，无 mic 依赖。先落队列模块让后续 slice 有地基。 |
| **Slice 3（follow-up）** | `listen_daemon.py`（复用 #465 ContinuousListener）+ daemon ring + `listen()` 改读队列 + `_daemon_active()` 心跳 | ❌ **follow-up** | 需真 mic 集成测试、模型 A 重构触及 `listen()` 超时语义、daemon 生命周期。strand-4/5 多处风险（超时重校准、三 daemon 互斥）。不宜与 Slice 1 混在一个 PR。 |
| **Slice 4（follow-up）** | 伪 barge-in：`tts.py` `_play_wav_bytes` 改造 + `voice-tts.stop` 信号 + daemon onset→touch | ❌ **follow-up** | **触及 no-overlap 保证的核心路径**（`_CrossProcLock` + `_play_lock`，strand-4 风险），需 dual-verify 确认不引入播放截断/锁泄漏回归。高风险，必须独立 slice + 充分验证。 |
| **Slice 5（follow-up）** | `voice-queue-drain.sh` + `queue_drain.py` + Stop hook 注册（opt-in）+ README §4 | ❌ **follow-up** | 依赖 Slice 2 队列 + Slice 3 daemon 入队。**且依赖 §7 的 UNVERIFIED 复查**（Stop block 注入机制虽 strand-1 已 VERIFIED，但 DeepWiki queue-state 细节待核）。 |

**为何路径1 单独先合**：它是路径2 daemon 的**复用件**（daemon ring 同改点），本身又是 #495 最高确定性、最低风险的改动。先合 Slice 1 立即改善句首截断体验，且为后续 slice 验证 `VOICE_PRE_RECORD_SEC` 语义。

**明确不做（本期及近期）**：strand-2 的 PostToolUse 「mid-tool-call 强制 dequeue」真打断——Mercury 本期只做**回合边界**注入（Stop block）。PostToolUse 每工具拦截投递的 matcher 作用域是 strand-2 标注的 UNVERIFIED 项，且真打断需 stdin injection / MCP polling tool，远超本期。文档化「关闭回合边界聋区，不关 mid-tool-call 聋区」。

**本 session 建议交付**：**Slice 1 完整 + Slice 2 队列模块**（两者都纯单元可测、无 mic、可合并），其余 follow-up。所有 slice 走 `/dual-verify` + PR 到 develop。

---

## 6. 测试计划（behavior-level，无活 mic）

### 路径1（Slice 1）

`scripts/voice/test_stt_preroll.py`（或并入现有 voice 测试），**用合成 block 序列驱动 listen_once 的循环逻辑**（把 VAD 循环抽成可测纯函数，或注入 fake `sd.InputStream` 把预设 block 喂进 `q`）：

1. **deque splice 正确性**：构造「N 个静音 block + 3 个 voiced onset block + voiced 主体 + 静音收尾」，断言 `seg` 头部包含 onset 前那 `pre_blocks` 个静音 block（前导音被保留），且**长度 == ring 长度 + 主体**，不多出重放的 3 个 onset block（防双重计数）。
2. **double-count 回归锁**：断言 seed 后 `seg` 的前 3 个 block 各只出现一次（hash 比对），守住「SEED 非 PREPEND」。
3. **env 校验**（镜像 #472 风格）：`VOICE_PRE_RECORD_SEC` 取 `0` / `0.3` / `-1` / `nan` / `abc` / 空串，断言：`0`→`pre_blocks==0` 走 off 路径；`0.3`→`pre_blocks==ceil(0.3/block_dur)`；非法值→stderr 警告 + 回退默认 0.3、不抛。
4. **off-switch 字节等价**：`VOICE_PRE_RECORD_SEC=0` 时，对同一 block 序列，`seg` 与「未打 ring 补丁的旧逻辑」逐 block 相等（证明 #472 回归面零）。
5. **maxlen 随 capture_sr 自适应**：mock 两个 `capture_samplerate`（如 16000 / 48000），断言 `pre_blocks` 按 `ceil(sec/block_dur)` 各自算对，不硬编码。

### 路径2 组件（可单测部分）

`scripts/voice/test_voice_queue.py`：

6. **队列读写**：`enqueue("话A")` → `peek_all()` 含一条未 consumed；`enqueue` 三条 → `drain(max_items=2)` 返回最早 2 条且把它们 `os.replace` 到 `consumed/`，第三条仍在；`is_empty()` 在 drain 干净后为真。
7. **原子出队不重复消费**：两个并发 `drain`/`pop_latest`（线程模拟）对同一条 utterance，断言 `os.replace` 只有一个成功 → 该条只被消费一次（另一个拿到空/跳过）。
8. **`pop_latest` 取最近**：enqueue 三条不同 ts，`pop_latest()` 返回 ts 最大那条文本并标 consumed。

`scripts/voice/test_queue_drain.py`（合成 stdin + fake 队列，无 mic）：

9. **drain hook 自守卫**：合成 Stop stdin，分别构造 (a) `stop_hook_active:true`→exit 0 无输出；(b) `state.get_mode()==idle`→exit 0 无输出；(c) 队列空→exit 0 无输出；(d) 队列非空 + mode!=idle + 非重入→stdout 是合法 JSON 且 `decision==block`、`reason` 含队列文本。用 `json.loads(stdout)` 断言合法（防拼接破坏 JSON）。
10. **reason 序列化安全**：enqueue 含引号/反斜杠/中文/换行的文本，断言 `json.loads(stdout)` 不抛、`reason` 内容正确（守 `json.dumps` 而非拼接）。
11. **max_items 上限**：队列 10 条、`VOICE_QUEUE_MAX_ITEMS=3`，断言 drain 只取 3、reason 只含 3 条。

barge-in 信号（无 mic，纯信号逻辑）：

12. **stop-signal 触发停播**：mock `sounddevice`（`sd.get_stream().active` 先真后假 / `sd.stop` 记录调用），在 `_play_wav_bytes` 等待循环中途 `touch voice-tts.stop`，断言 `sd.stop()` 被调用且信号文件被 unlink。
13. **无信号正常放完**：不 touch 信号，`sd.get_stream().active` 走到假，断言 `sd.stop()` **未**因信号被调（只正常结束），无回归到 no-overlap。
14. **陈旧信号清理**：写一个 pid 已死 + mtime 过期的 `voice-tts.stop`，断言下次播放前被清，不误停。

**hook 合成触发冒烟**（治理要求）：`queue_drain.py` 在合成 stdin 下 exit 0；settings.json 加 hook 后 `python -c "import json; json.load(open('.claude/settings.json'))"` 合法；一次真实回合末观察 drain 行为（队列空时静默、非空时注入）。

---

## 7. Open risks / UNVERIFIED（合并前必复查）

| # | 项 | 状态 | 合并门 |
|---|---|---|---|
| R1 | **Stop block 注入机制**：`{decision:block,reason}` exit 0 重新唤起模型 + reason 作为下一指令 | strand-1 **VERIFIED**（官方 decision-control 表 + `auto-handoff-stop.sh`/`stop-guard.sh` 双 in-repo 证据）；但 strand-1 标注 **DeepWiki 的「queue-state vs `stop_hook_active`」细节未读源、UNVERIFIED** | Slice 5 实现前：WebFetch `code.claude.com/docs/en/hooks` 复核 Stop decision-control 表当前措辞 + 确认 `stop_hook_active` 语义未变（用 **BOTH guards** 兜底，strand-1 已建议）。不靠训练数据。 |
| R2 | strand-2 **PreToolUse matcher 作用域**（全工具 vs 仅 speak） | **UNVERIFIED** | 本期**不做** PostToolUse/PreToolUse 投递 → 此风险本期不触发。未来若做 mid-tool-call，落地前读其 hooks.json / 官方 hook 文档确认拦截点。 |
| R3 | **双重计数复发**：未来 edit 改回 `seg = list(preroll) + pending` | 设计已防（SEED 非 PREPEND） | 保留改动4 的 WHY 注释 + 测试2/4 作回归锁。 |
| R4 | **pre-roll 噪声 bleed**：`VOICE_PRE_RECORD_SEC` 过大把室噪/键盘声 prepend，Whisper 可能转出虚假首 token 或扰动 no_speech 门 | 已知（strand-3 风险） | 默认 0.3 保守；README 文档化 >0.5s 风险；测试覆盖默认值。 |
| R5 | **barge-in 改造触及 no-overlap 核心**（`_CrossProcLock` + `_play_lock`），可能引入播放截断/锁泄漏 | 高风险（strand-4） | Slice 4 独立 PR + 强制 `/dual-verify`（Claude deep-review + Codex audit）确认锁不泄漏、正常播放不被截断（测试13）；edge mp3 回退文档化不支持 barge-in。 |
| R6 | **三 daemon mic 互斥**：旧 `voice-zh-input.py` / `listen_daemon.py` / 旧 `listen()` 自开流同设备并发 → 复发 `-9998` | 已识别（strand-5） | README 明确所有权矩阵（§1 决策B）；`_daemon_active()` 心跳让 `listen()` 自动让路。 |
| R7 | **`listen()` 超时语义重校准**：模型 A 下 `max_seconds` 变成「等队列新 utterance 超时」，ADR §6 工具超时预算需重标 | 已识别（strand-4/5） | Slice 3 落地时重标 + 文档化；`pop_latest(wait_until)` 轮询参 strand-2 思路。 |
| R8 | **队列原子出队**：跨进程 P2 写 / P3 读+标 consumed，read-modify-write 需 per-file rename | 设计已防（一 utt 一文件 + `os.replace` 出队） | 测试7（并发 drain 只消费一次）作门。 |
| R9 | **路径修正**：任务原始描述把 `voice-stop-notify.sh` 写在 `scripts/voice/`，实际在 `.claude/hooks/`（strand-4/5 已纠） | 已纠 | PR 引用按实际位置 `.claude/hooks/voice-queue-drain.sh`。 |
| R10 | **模块命名**：新队列模块**不能**叫 `queue.py`（会遮蔽 `stt.py:22 import queue` 的 stdlib，因 `sys.path[0]==voice/`） | 设计已防 | 命名 `voice_queue.py`。 |

**Mercury 约束遵守确认**：模块化/可 detach（路径1 单文件、路径2 opt-in 不起 daemon=今天行为）；无 C 盘安装（venv 在 `.venv-voice/`，状态在 `.mercury/state/`）；env-var opt-in（`VOICE_PRE_RECORD_SEC` 默认 0.3 但 0=off；hook opt-in 不入默认 settings.json）；不破 #472 校准（`_calibrate` 不动 + off-switch 字节等价测试）；合并前 `/dual-verify` + PR 到 develop（所有 slice，尤其 Slice 4 barge-in）。

**关键文件路径汇总**（工作树 `/d/Mercury/Mercury-468voice`）：
- 改：`scripts/voice/stt.py`（路径1）、`scripts/voice/tts.py`（barge-in，Slice 4）、`scripts/voice/mcp_server.py`（listen 读队列，Slice 3）、`scripts/voice-zh-input.py`（daemon ring，随 Slice 3）、`scripts/voice/README.md`（env 表 + §4）、`.claude/settings.json`（Stop hook，opt-in 文档化）
- 新：`scripts/voice/voice_queue.py`、`scripts/voice/listen_daemon.py`、`scripts/voice/queue_drain.py`、`.claude/hooks/voice-queue-drain.sh`
- 新状态：`.mercury/state/voice-queue/`、`.mercury/state/voice-tts.stop`、`.mercury/state/voice-daemon.pid`

---

## 8. 对抗式评审发现(3 lens × adversarial,Path 2 follow-up 的硬合并门)

三个评审 lens 全部裁决 **NEEDS-CHANGES**。以下 blocking 项是 Slice 3/4/5 落地前必须解决的(每条附 critic 给的 fix 要点):

### NEEDS-CHANGES — LOOP/CORRECTNESS — adversarial review of the Stop-hook queue-drain injection mechanism, loop-termination guard, exactly-once consumption, and empty/off no-op behavior.

- **Utterances spoken DURING the drain-induced continuation turn are silently stranded — the loop-guard is correct but defeats the design's own premise. Verified mechanism: after drain emits {decision:block}, the NEXT Stop fire has stop_hook_active==true (official docs: 'Set to true when a Stop hook is currently executing to prevent recursive hook invocation' / community: 'fires a second time because of a prior block decision'). The design's guard #1 (stop_hook_active==true -> exit 0) therefore correctly STOPS the infinite loop within one turn. BUT it also means: any utterance the daemon enqueues while Claude is acting on the injected transcript (turn T1) is NOT delivered at T1's Stop boundary (guarded out), and is deferred until some later USER-initiated turn's Stop — which may never come in work-mode where Claude is expected to act autonomously. The design's core promise ('干活期间说的话不丢 / 关闭回合边界聋区') is only half-true: it closes the FIRST turn boundary but reopens a deaf window for the entire continuation turn it itself triggers.**
  - 为何会坏:This is the LOOP question's real answer: it does NOT loop forever (guard works), but the fix for not-looping creates a correctness hole the design claims it closes. In work-mode the stranded utterance can sit in the queue indefinitely with no delivery trigger, so the user's mid-work speech is effectively dropped — the exact failure #495 exists to prevent.
  - fix:Do NOT rely on stop_hook_active alone for the queue. Decouple loop-termination from queue-drain: (a) Track a per-turn drain marker the hook OWNS (mirror auto-handoff-stop.sh's RETRY-marker pattern at lines 79-86, which explicitly AVOIDS stop_hook_active precisely because stop-guard.sh sets it). Allow drain to re-block on a NEW non-empty queue even when stop_hook_active is true, but cap consecutive auto-continuations with an own counter (e.g. VOICE_QUEUE_MAX_CONTINUATIONS, default 3) so a daemon that keeps enqueueing (room noise / Claude's own TTS picked up by mic) cannot spin forever. (b) After the cap, exit 0 and let the deferred items wait — but document that explicitly and surface a TTS/notify nudge so the user knows speech is queued. (c) Add a test: enqueue during the continuation turn, assert it is delivered at the next boundary up to the cap, then assert termination at the cap (no infinite loop).
- **drain() exactly-once is NOT guaranteed across Mercury's multi-session model, and the shown drain() pseudocode lacks the per-file try/except its own prose promises. The queue dir is keyed only by VOICE_STATE_DIR (default .mercury/state/voice-queue/) with NO per-session scoping. Within ONE agent, listen()/pop_latest (mid-turn) and drain (turn-end) are temporally disjoint so they cannot race — but Mercury runs multi-lane / concurrent sessions sharing .mercury/state, so two sessions' drain hooks (or session A's drain vs session B's pop_latest) DO race on the same utt-*.json. The design's defense is os.replace ('rename success = I own it'), but: (1) the drain() pseudocode in 3.2/3.5 does listdir-then-replace with NO per-file FileNotFoundError/OSError catch — when the loser's source file has already vanished, os.replace raises and aborts the WHOLE batch (then the hook self-guard swallows to exit 0 => an entire batch of utterances silently dropped). Prose says 'rename failed just skip' but the code path shown does not.**
  - 为何会坏:This is the exactly-once question (Q3). Without per-session queue scoping the consume is not exactly-once under Mercury's real concurrency, and without per-file exception handling a lost race doesn't just skip one item — it drops the whole drain batch. Both are silent failures (hook always exits 0).
  - fix:(a) Scope the queue per session: key the dir on the Stop-hook's session_id from stdin payload (or VOICE_STATE_DIR + session suffix), so only the owning session's drain consumes its own utterances — eliminates cross-session race entirely and matches the daemon-feeds-one-session model. (b) Make drain() iterate per-file with try/except OSError around os.replace, treating FileNotFoundError as 'already consumed by someone else, skip this item, continue the batch' — never abort. (c) Add the concurrency test the plan lists as test 7 but make it cross-process (two real processes, not two threads) and assert each utterance is consumed exactly once AND no batch is dropped on a lost race.
- **Half-written-file read can silently drop the entire queue, and the glob is unpinned. The daemon (separate always-on process) writes utt-*.json continuously while drain reads. If enqueue() is not atomic, drain can json.load a half-written file -> JSONDecodeError -> the design's 'queue corrupted -> exit 0' guard drops EVERY queued utterance, not just the in-flight one. The design says enqueue '复用 _atomic_write 风格' but does not pin it as a hard requirement, and state.py._atomic_write writes its temp as .tmp-*.json IN THE SAME DIRECTORY. If drain globs '*.json' it will catch that .tmp-*.json mid-write; if it also globs the consumed/ subdir it will re-read already-consumed items.**
  - 为何会坏:This is the no-drop half of Q3 plus a corruptness-amplification bug: one partial read poisons the whole drain because the error handler is batch-level ('exit 0 on any exception'), not per-file. A single concurrent write can therefore drop an unbounded number of valid utterances.
  - fix:(a) Make atomic enqueue a HARD spec requirement: enqueue() MUST use mkstemp+os.replace (reuse state.py._atomic_write) so no reader ever sees a partial file. (b) Pin drain's glob to exactly 'utt-*.json' (NOT '*.json') so the .tmp-*.json temp is excluded by name, and ensure consumed/ is a subdir excluded from the listing. (c) Make json parsing PER-FILE with try/except: a single corrupt/partial file is skipped (left for next pass), never aborting the batch — replace the batch-level 'corrupted -> exit 0 drops all' with per-item resilience. (d) Add a test that writes a deliberately truncated utt-*.json alongside two valid ones and asserts the two valid ones are still delivered.
  - (minor) Q1 (injection real, not wishful): VERIFIED SOUND. Official docs + community confirm top-level {"decision":"block","reason":...} exit 0 'prevents stopping, continues the conversation' and 'the harness sends the reason string back to Claude as the next instruction.' In-repo proof: auto-handoff-stop.sh:129 and stop-guard.sh:21 both ship this exact form. Note: the block injects transcript TEXT for Claude to act on; it does not make Claude speak — the design's framing is correct but worth stating plainly in the PR.
  - (minor) Q4 (no-op when empty/off): SOUND. The self-guard order (stop_hook_active -> venv-missing -> state.get_mode()==idle -> queue empty -> any-exception) each exits 0, mirrors the proven stop_notify.py guards (lines 77,84) and voice-stop-notify.sh. Correctly uses state.get_mode() (Python, handles JSONDecodeError/OSError fail-to-idle) instead of bash-parsing voice-mode.json which contains Windows backslash paths. The separate is_empty() pre-check is redundant given 'if not items: return 0' after drain (TOCTOU-benign) but harmless.
  - (minor) The bash wrapper's stdout passthrough is correct: '"$PY" ... || true' preserves stdout (only swallows exit code), so the block JSON reaches Claude Code even if Python exits non-zero. Verified against the proven voice-stop-notify.sh which uses the identical pattern (stop_notify.py just happens to emit no stdout). One subtlety to document: set -euo pipefail must not let the PROJ=$(git ...) line abort before Python runs — the '|| ... || pwd' fallback covers it.
  - (minor) Hook ordering claim is correct: appending drain as the 3rd Stop hook after stop-guard.sh and auto-handoff-stop.sh matches the verified settings.json structure (2 hooks today, both top-level decision:block blockers). Drain排最后 means stop-guard/#469 win the block decision when they fire — acceptable, and the design correctly notes #469 uses its own RETRY marker not stop_hook_active (verified auto-handoff-stop.sh:79-86), so there is no cross-contamination of the stop_hook_active flag between #469 and drain.
  - (minor) R1 in the design's own risk table is appropriately flagged: re-confirm the Stop decision-control table wording before Slice 5. My fetch confirms it still holds as of this review (June 2026), but the design's instinct to re-verify rather than trust training data is correct. The DeepWiki 'queue-state vs stop_hook_active' detail is now RESOLVED by the official docs quote: stop_hook_active is set true on the recursive/second fire, which is exactly the guard semantics the design assumed.
  - (minor) Slice scoping is realistic: Slice 1 (ring-buffer, single file, _calibrate untouched) and Slice 2 (voice_queue.py pure file IO) are genuinely low-risk and unit-testable without a mic. The loop/correctness defects above all live in Slice 5 (the drain hook) and Slice 2's queue contract — neither blocks shipping Slice 1 first. Recommend Slice 2's voice_queue.py spec bake in atomic-enqueue + per-file-resilient-drain + per-session scoping BEFORE Slice 5 builds on it, since fixing the contract after the daemon depends on it is costlier.

### NEEDS-CHANGES — AUDIO/CONCURRENCY — adversarial. Grounded against actual code in /d/Mercury/Mercury-468voice: tts.py (_CrossProcLock + _play_wav_bytes + _stop_current), mcp_server.py (listen tool), voice-zh-input.py (ContinuousListener: _on_audio/_busy/paused gating, _finalize, close), stt.py (listen_once VAD loop), state.py, stop_notify.py, voice-stop-notify.sh.

- **Acoustic echo self-trigger loop — design omits full-duplex coupling entirely. Model A makes the daemon (continuous mic owner) and TTS playback PERMANENTLY concurrent. Today they are strictly serial (listen() blocks, THEN announce() plays), so the mic never hears the speakers. Under Model A the desktop mic hears Kokoro's own output through the speakers. Two failure cascades, both verified against code: (a) BARGE-IN FALSE FIRE — the daemon onset detector (voice-zh-input.py:519-523, `voiced_run >= ONSET_BLOCKS`) triggers on the TTS audio itself -> touches voice-tts.stop -> _play_wav_bytes (Slice 4 loop) calls sd.stop() -> the agent's own announce() is truncated mid-sentence on essentially every announce. (b) PHANTOM UTTERANCE — that same TTS audio is segmented + transcribed by ContinuousListener._finalize -> transcribe_segment -> voice_queue.enqueue() -> a fake 'user said' entry -> injected into the next turn via the Stop-hook drain. The hallucination gate (stt.py:92 HALLUCINATION_MARKERS) only catches silence-hallucinations, NOT real-but-self-spoken TTS text, so it does not save you. README has zero mention of headphones/half-duplex/push-to-talk (grep confirmed empty). This is the single biggest reason the chosen model is NOT free of contention — it trades device contention for acoustic contention.**
  - 为何会坏:Without acoustic-echo handling, every announce() in daemon-on mode either (a) cuts itself off via false barge-in or (b) feeds its own voice back as a user message into the next turn, corrupting the conversation. This is a designed-in infinite-feedback risk, not an edge case — it fires on normal use with built-in mic + speakers, which is the documented Mercury voice setup (Kokoro on :8880, no headphone assumption).
  - fix:Make the mic half-duplex against playback: when the cross-process playback lock (voice-tts.lock) is HELD, the daemon must mute its own VAD/enqueue path. Concretely — Slice 3/4: in ContinuousListener._on_audio (and listen_daemon), early-return (drop the block, like the existing `self.paused` gate at voice-zh-input.py:436) whenever voice-tts.lock exists AND its holder pid is alive. Reuse tts._lock_path()/_pid_alive. Add a small post-playback guard window (e.g. 200-300ms after lock release, env VOICE_HALF_DUPLEX_TAIL_MS) so speaker decay/reverb tail isn't transcribed. Document that true barge-in (talking OVER the agent) is explicitly out-of-scope without AEC; what you ship is half-duplex turn-taking (mic deaf while agent speaks). If real barge-in is wanted later, it requires an AEC layer (e.g. WASAPI loopback reference + webrtc-audio-processing) — call that out as a separate, large, UNVERIFIED follow-up, not Slice 4.
- **Barge-in detection is structurally impossible with the reused ContinuousListener because its own _busy gate makes the daemon DEAF for seconds per utterance. voice-zh-input.py:436 drops all capture while `self._busy.is_set()`; _busy is SET at the start of every _finalize (line 445) and held through transcribe + the entire GRACE_SEC window + queue drain (cleared only at line 474). Slice 4 barge-in requires the daemon to detect a NEW onset while TTS plays — but during exactly the window after the user's prior utterance (transcribe latency on large-v3 can be 1-3s + GRACE_SEC=2s default), the daemon's onset path never runs. The design says 'reuse the ContinuousListener voiced_run>=ONSET_BLOCKS trigger point' but never reconciles that that trigger is bypassed whenever _busy is set. The two requirements (drain backlog via _busy to avoid stale audio for the #465 paste path, vs. stay-alert-for-barge-in for #495) are in direct conflict in the same class.**
  - 为何会坏:Barge-in that only works when the daemon happens to be idle is not barge-in. The most likely moment a user interrupts is right after they finished their last sentence and the agent started a long answer — which is precisely when _busy/grace is active and capture is dropped. Reusing ContinuousListener as-is silently disables the headline Slice 4 feature.
  - fix:Do NOT reuse ContinuousListener's _busy/grace state machine for the always-on barge-in listener. listen_daemon.py needs a dedicated, GRACE-free, AUTO_ENTER-free capture path whose ONLY job is enqueue + onset-signal — no paste, no Enter, no grace window, so there is no reason to gate capture off after each utterance. Drop the _busy gate from the #495 daemon path entirely (it exists in #465 only to avoid auto-paste/Enter races, which #495 doesn't have). Keep the half-duplex mute (issue 1) as the only legitimate capture-drop condition. This also removes the ambiguity of running #465's daemon and #495's daemon as 'the same' code — they have incompatible gating needs.
- **voice-tts.stop signal races the lock lifecycle and can truncate the NEXT playback to ~0ms (a no-overlap-protocol regression). The stop signal carries no binding to the specific playback it was meant to interrupt. Race: daemon writes voice-tts.stop at T while TTS-A is in its final ~50ms; TTS-A's poll loop finishes naturally (sd.get_stream().active goes false) and breaks BEFORE the next poll sees the signal, so the unlink-on-consume never runs; _CrossProcLock.__exit__ removes voice-tts.lock; TTS-B (next announce) acquires the lock, enters _play_wav_bytes, and on its FIRST poll sees the stale voice-tts.stop -> sd.stop() -> TTS-B truncated to one poll interval. The design's mitigation ('stale cleanup at drain time') runs in the Stop-hook process on turn-end cadence, NOT before each playback, so it does not close this window. The unlink is also best-effort (`except OSError: pass`) and another poll iteration can re-read it before unlink lands.**
  - 为何会坏:tts.py's entire reason-for-existing is the no-overlap guarantee (module docstring + _CrossProcLock). A stale stop-signal that silently truncates the next utterance is exactly the kind of playback-corruption regression CLAUDE.md flags Slice 4 as high-risk for. It will present as 'the agent's announcements randomly get cut to nothing,' very hard to diagnose.
  - fix:Bind the stop signal to the current playback generation, and clear it on lock ACQUIRE not just on consume. (a) Write the signal as a small JSON carrying the target pid + a monotonically increasing playback-id (or simply the lock-holder pid + lock mtime at signal time); _play_wav_bytes ignores any signal whose target doesn't match the CURRENT _CrossProcLock holder identity. (b) In _CrossProcLock.__enter__, immediately after winning the lock, unlink any pre-existing voice-tts.stop (clear stale signal at the start of every playback, same process, zero cross-process cadence dependency). (c) The daemon must only write the signal while the lock holder it observed is still alive AND still the lock holder (re-check), so a signal can't outlive its target. Add test 13/14 coverage for 'stale signal present at playback start is ignored/cleared, TTS-B plays full length'.
- **Daemon mic-stream leak on non-graceful exit reintroduces -9998 — the exact failure the design claims to eliminate. ContinuousListener.close() (voice-zh-input.py:583-600) closes sd.InputStream ONLY via the `keyboard.wait(QUIT)` finally in main(). On taskkill / OOM / parent-shell close / unhandled fatal, the InputStream is never closed and PortAudio keeps the WASAPI capture device open until the OS reaps the process — and the pidfile the design proposes (voice-daemon.pid) may linger. _daemon_active() then either (a) sees a live-but-zombie pid and routes listen() to the queue forever (queue never fills because the dead daemon's stream is gone), or (b) sees a stale pid, lets listen() fall back to self-opening a stream, which collides with the still-OS-held device -> -9998. The design specifies pidfile + _pid_alive but no device-free verification and no stale-pidfile steal/handshake on daemon startup.**
  - 为何会坏:Mercury's own MEMORY.md records a prior incident class (shared-worktree branch switch) where 'files vanish' from stale process state; a leaked audio device is the audio analogue. The design markets Model A as eliminating -9998, but a crash path silently reintroduces it AND adds a new failure (listen() stuck reading a queue a dead daemon will never fill). 'opt-in, just don't start the daemon = today's behavior' does NOT cover the case where the daemon WAS started and then died.
  - fix:Make daemon startup self-heal and make liveness mean device-ownership, not just pid-alive. (a) On listen_daemon startup: if voice-daemon.pid exists, _pid_alive(old)==False -> remove it; ==True -> refuse to start (another owner) OR signal it to quit, then proceed. (b) Write the pidfile AFTER sd.InputStream.start() succeeds and remove it in a finally/atexit AND a signal handler (SIGTERM/CTRL_CLOSE_EVENT on Windows via win32api.SetConsoleCtrlHandler) so a clean kill still releases it. (c) _daemon_active() must treat 'pidfile present but a probe shows the device is openable / queue heartbeat mtime is stale > N sec' as NOT-active, so listen() never blocks forever on a zombie daemon — add a queue/daemon heartbeat file the daemon touches each loop, and gate _daemon_active() on heartbeat freshness, not just pid. (d) listen()'s self-open fallback must catch PortAudioError(-9998) and surface a clear 'daemon device leak — restart daemon' message rather than an opaque crash.
- **Model A breaks listen()'s request/response semantics: pop_latest() returns the most-recent queue entry regardless of whether it post-dates the agent's question, so listen() can return speech the user said BEFORE the agent asked. With the daemon always enqueuing, by the time the agent calls announce('问题?') then listen(), the queue may already hold an utterance from 30s ago (or the user's answer to the PREVIOUS question), and pop_latest returns that stale text as if it were the answer. The design's pop_latest(wait_until) waits for the queue to be non-empty, but the queue is essentially never empty in an active session.**
  - 为何会坏:This silently corrupts the work-mode interaction loop (announce a decision point -> listen for the user's answer): the agent acts on a stale or unrelated utterance. It is a correctness bug in the core bidirectional primitive, not a perf nit, and it is invisible (returns plausible text).
  - fix:Make listen() time-anchored, not 'latest'. Record a watermark timestamp at listen() entry (or have announce() stamp the queue) and have pop_latest only return utterances with ts > watermark; if none arrive before wait_until, return ''. This requires the queue entries' ts to be the capture-onset time (already present in the proposed schema) and listen() to ignore everything enqueued before it started waiting. Document that pre-question backlog is intentionally NOT consumed by listen() (it is consumed only by the Stop-hook drain, which is the right channel for 'things said during the previous turn').
  - (minor) voice_queue per-file os.replace atomicity is sound on Windows (rename is atomic), and the 'one utterance per file' choice correctly sidesteps single-file read-modify-write — this part of the design is good. Keep test 7 (concurrent drain consumes once) as the gate.
  - (minor) Naming the queue module voice_queue.py (not queue.py) to avoid shadowing stdlib `queue` (imported at stt.py:22 with sys.path[0]==voice/) is correctly identified — R10 is real and the fix is right.
  - (minor) Module-load import in queue_drain.py of `state`/`voice_queue` from the voice venv is fine, but ensure the bash wrapper does NOT swallow stdout: `"$PY" queue_drain.py || true` preserves stdout (|| only catches exit code) — design notes this correctly, just add a test asserting block JSON reaches stdout through the wrapper.
  - (minor) Edge/mp3 fallback documented as 'no barge-in' is acceptable since _play_mp3_file uses an uninterruptible time.sleep (tts.py:270); but also ensure the daemon does NOT write voice-tts.stop while an mp3 (mci) playback holds the lock, or the signal will leak to the following wav playback (ties into blocking issue 3's generation-binding fix).
  - (minor) Consider whether the Stop-hook drain should run at all while the daemon is enqueuing the user's CURRENT in-progress utterance — a half-spoken sentence captured at the turn boundary could be injected truncated. Low priority vs. the blocking items but worth a min-utterance-age filter in drain().

### NEEDS-CHANGES — INTEGRATION / REGRESSION — does #495 break existing Stop hooks, the pull-based listen()/secretary flow, #472 calibration, Windows compat, Mercury modularity, and is the scope-split safe to merge now?

- **Barge-in playback loop (Slice 4) relies on `sd.get_stream().active` + a `_current["stream"]=True` bool sentinel, but the REAL `_play_wav_bytes` (tts.py:218-237) uses the module-level `sd.play(audio, sr)` convenience and stores `_current["stream"]=True` (a bool, NOT a stream handle). `sd.get_stream()` returns the MOST-RECENTLY-CREATED stream of the process — which inside the MCP-server process may be an INPUT stream (the old listen() self-open path, or `_calibrate`'s `sd.rec`) rather than the play() output stream. The proposed `while True: if not sd.get_stream().active` poll is therefore (a) unverified against sounddevice's real API, (b) liable to poll the wrong stream, and (c) the design never reconciles it with the existing `_current["stream"]=True` sentinel that `_stop_current()` (tts.py:201-216) already reads. This is the exact 'looks-correct-but-wrong' integration trap: it replaces a working blocking `sd.play();sd.wait()` with an unproven poll on the no-overlap core path.**
  - 为何会坏:Slice 4 is flagged R5 as 'touches no-overlap core' but the design treats the sounddevice mechanism as settled. If `sd.get_stream()` returns an input stream or raises when no stream exists, the loop either never exits (playback hangs holding `_CrossProcLock` until `_LOCK_STALE`=90s, blocking ALL announce/stop_notify) or exits instantly (silent truncation). Either is a hard regression to the verified #468 playback path.
  - fix:Before Slice 4, WebFetch the sounddevice docs to confirm get_stream/play/stop semantics, then DO NOT use `sd.get_stream()`. Instead create an explicit `stream = sd.OutputStream(...)` (or use `sd.play` + track completion via a callback/`threading.Event` set in `finished_callback=`), store the REAL stream object in `_current["stream"]`, and poll `stream.active` on that handle. Add test 13 (no-signal normal playback completes, no spurious sd.stop) and a hang test (signal-file-never-appears → playback still completes and releases the lock) as merge gates.
- **Slice 3 proposes `listen_daemon.py` by 'reusing #465 ContinuousListener' but `ContinuousListener._finalize` (voice-zh-input.py:442-470) DELIVERS each finalized utterance by pasting into the focused window (`deliver_text(text)`) and pressing Enter (`keyboard.press_and_release("enter")`). Reused as-is for path 2, every side-utterance the user speaks during a work turn would be typed into whatever window has focus AND enqueued — double-delivery plus keystroke injection into the active app. The design's enqueue-only daemon requires overriding/replacing `_finalize`, which the design never states.**
  - 为何会坏:This is the central reuse claim of path 2 and it silently inherits the #465 daemon's keystroke-paste side effect, which is wrong for a transcript-queue daemon and actively harmful (random Enter presses / text injection into the editor while Claude works).
  - fix:In Slice 3, subclass ContinuousListener and override `_finalize` to call `voice_queue.enqueue(text)` with NO `deliver_text`/`keyboard` call (and skip AUTO_ENTER/GRACE entirely). Document explicitly that listen_daemon must not import/trigger the paste path. Gate with a test asserting enqueue is called and `keyboard`/`deliver_text` are NOT.
- **The barge-in stop-signal is a single shared file `.mercury/state/voice-tts.stop` with no owner/epoch binding to a specific playback. The daemon `touch`es it on ANY onset while `voice-tts.lock` exists; the player unlinks it on consume. But onset detection fires on the user's OWN voice AND on the TTS audio bleeding back through the mic (the daemon owns the mic and TTS plays on speakers in the same room). With an open mic during playback, Kokoro's own output can cross the VAD threshold → self-triggered barge-in that stops every announce mid-sentence (acoustic echo). The design's stale-pid cleanup does not address this live-feedback race.**
  - 为何会坏:This breaks the primary announce path for the common laptop-speaker-plus-mic setup: the agent's check-in TTS would routinely cut itself off. It is a classic open-mic/playback feedback loop the design never gates.
  - fix:Gate daemon-side barge-in: (a) pause/ignore the daemon's onset→touch while `voice-tts.lock` is held by the CURRENT TTS playback unless onset energy clearly exceeds the expected echo floor, OR (b) require a configurable guard (default: barge-in OFF / opt-in via env, mirroring edge-fallback being opt-in), OR (c) bind the stop-signal to the playback's pid+epoch so a signal can only cancel the playback it was raised against. Add a feedback-loop test (synthetic onset during active playback with echo-level RMS must NOT stop playback by default).
- **Model A makes `listen()` read the queue when `_daemon_active()` is true, but `_daemon_active()` keys on a heartbeat/pid file (`voice-daemon.pid`). If the daemon CRASHED but left a stale pid file whose pid was recycled to a live unrelated process, `_pid_alive` returns true → `listen()` permanently reads an empty/stale queue and NEVER falls back to self-open mic. For a user who DID enable the daemon then it died, every `listen()` silently returns '' (the exact #472-class silent-drop failure). The design cites `_pid_alive` reuse but pid-recycle false-positives on Windows are real.**
  - 为何会坏:Breaks the pull-based listen() flow for daemon users on daemon crash — and silently, which is the worst regression class for this subsystem (the whole #472 lesson is 'never silently drop speech').
  - fix:Make `_daemon_active()` require BOTH a live pid AND a fresh heartbeat mtime (e.g. daemon rewrites the pid file every N seconds; treat mtime older than 2N as dead regardless of pid). On 'daemon present but heartbeat stale', fall back to self-open `listen_once` (degrade to #468 behavior) rather than reading a dead queue. Test: stale-heartbeat pid file → listen() self-opens mic.
  - (minor) Scope split (Slice 1 stt.py ring-buffer + Slice 2 voice_queue.py) is realistic and safe to merge now: both are mic-free, single-purpose, fully unit-testable, touch NO Stop hook and NO settings.json, and are inert for non-daemon users. Path 2 being opt-in (no daemon = today's #468 behavior) satisfies the silent-no-op requirement. This part of the design is sound.
  - (minor) #472 calibration is genuinely safe in Slice 1: `_calibrate` (stt.py:268-322) is untouched, the ring buffer is independent of `pending`, and the VOICE_PRE_RECORD_SEC=0 off-switch yields byte-equivalent behavior (change 3 skips, change 4 takes the else branch). The SEED-not-PREPEND double-count guard is the correct call and the test plan locks it. Recommend keeping the off-switch byte-equivalence test (test 4) as a hard gate.
  - (minor) The Stop-hook injection mechanism `{decision:block,reason}` + exit 0 is correctly grounded against TWO in-repo precedents (stop-guard.sh:21, auto-handoff-stop.sh:129) and the dual loop-guard (stop_hook_active OR empty-queue) is the right belt-and-suspenders. The bash wrapper's stdout-passthrough-with-`|| true` was verified to actually pass the block JSON to the caller while exiting 0.
  - (minor) Hook ordering vs existing Stop array: drain排最后 is correct and there is NO double-block conflict — stop-guard and auto-handoff are the only blockers, their state is disjoint (auto-handoff explicitly avoids stop_hook_active; drain uses it). The design also correctly notes drain gets preempted when stop-guard blocks (acceptable: next clean stop drains). research-stop-nudge.sh (the one hook the design never enumerates) is SubagentStop/matcher=research scoped, so it cannot collide with the new Stop hook — but the design should add it to its hook-inventory for completeness.
  - (minor) Timeout budget: drain timeout=15 plus VOICE_QUEUE_MAX_ITEMS cap is reasonable, but note the existing Stop array already sums stop-guard(10)+auto-handoff(30); adding 15 is fine since hooks run sequentially and each has its own timeout. The real timeout risk is BLOCKING TTS inside drain — keep drain injection text-only (no synchronous playback inside queue_drain.py) so a slow Kokoro synth can't blow the 15s budget and get the hook killed mid-block-emit.
  - (minor) voice-stop-notify + voice-queue-drain double-voice risk is real (both read the same lock so playback serializes, but the user hears both the queue-injection AND the last-message notify). The design's 'merge into one Stop worker' suggestion is the right resolution — make it the DEFAULT in README §4 rather than an optional note, since registering both is the obvious user mistake.
  - (minor) Windows compat is mostly handled (os.replace atomic rename, _pid_alive ctypes path, json.dumps for backslash-safe reason). One gap: per-utterance file enqueue under `.mercury/state/voice-queue/` with microsecond-stamped names + os.replace-to-consumed is sound, but ensure the consumed/ dir and the drain's os.replace handle the Windows case where the source was already moved by a concurrent drain (FileNotFoundError must be caught and treated as 'already consumed', not an error) — test 7 covers the happy concurrent path; add the FileNotFoundError branch explicitly.
  - (minor) Mercury modularity: path 2 is independently detachable in principle (opt-in, separate files), but it introduces a NEW cross-process IPC contract (voice-tts.stop signal file) that couples tts.py playback to the daemon. To keep tts.py detachable, the stop-signal poll in _play_wav_bytes must degrade to a plain no-op when the signal file/dir is absent (which the design's `stop_signal.exists()` check does) — preserve that so tts.py works standalone without the daemon.

**结论**:Path 2 的核心架构风险(声学回授半双工、跨会话队列竞争、Stop-hook 续接回合聋区、barge-in 触碰 no-overlap 核心、daemon mic 泄漏 -9998 复发)已被对抗式评审显性化。Slice 3/4/5 须按上述 fix 实现并各自 `/dual-verify`,不可一次性合并。
