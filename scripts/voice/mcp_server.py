# -*- coding: utf-8 -*-
"""mcp_server — in-session voice MCP server for Claude Code (Issue #468).

A local stdio MCP server (PyPI `mcp` / FastMCP, verified 1.27.2 on Python 3.14) that
lets the model drive Chinese voice interaction from inside a Claude Code session.
Register it once:

    claude mcp add voice -- <repo>/.venv-voice/Scripts/python.exe <repo>/scripts/voice/mcp_server.py

(stdio is the default transport; a real PATH `python` binary needs no `cmd /c`
wrapper on native Windows — verified at code.claude.com/docs/en/mcp.)

Tools exposed to the model (borrowing VoiceMode's converse semantics — ADR §3):
  listen()            — block, record one Chinese utterance, return the transcript
                        (≈ converse(wait_for_response=True))
  announce(text)      — speak text via local TTS, do NOT listen
                        (≈ converse(wait_for_response=False); the agent check-in primitive)
  set_mode(mode, ...) — switch secretary | work (explicit; ADR §5)
  record_note(text)   — append a structured entry to the secretary notes file
  get_status()        — current mode + TTS service health

The faster-whisper model is loaded lazily on the first listen() so a server used only
for announcements never pays the model-load cost. STT/TTS are fully local + private.

Config: STT via VOICE_ZH_MODEL / VOICE_ZH_DEVICE / VOICE_ZH_DEVICE_INDEX (shared with
#465's daemon); TTS via VOICE_TTS_* (see tts.py); state via VOICE_STATE_DIR (state.py).
Secretary mode can use a smaller/faster model via VOICE_ZH_MODEL_SECRETARY.
"""
import os
import sys

# this file is scripts/voice/mcp_server.py — import flat siblings (sys.path[0] == voice/)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server.fastmcp import FastMCP  # noqa: E402

import stt as _stt  # noqa: E402
import tts as _tts  # noqa: E402
import state as _state  # noqa: E402
import listen_daemon as _daemon  # noqa: E402  (light import; heavy deps lazy in the daemon)

mcp = FastMCP("voice")

# lazily-loaded warm engines, keyed by model name (secretary may use a smaller model)
_engines = {}


def _engine_for_mode(mode):
    """Return a warm SttEngine for the given mode, loading it on first use.

    Work/idle use VOICE_ZH_MODEL (default large-v3, best accuracy). Secretary uses
    VOICE_ZH_MODEL_SECRETARY if set (e.g. `medium` / `large-v3-turbo`) to cut latency.
    """
    default_model = os.environ.get("VOICE_ZH_MODEL", "large-v3")
    if mode == "secretary":
        model = os.environ.get("VOICE_ZH_MODEL_SECRETARY", default_model)
    else:
        model = default_model
    if model not in _engines:
        device = os.environ.get("VOICE_ZH_DEVICE", "auto")
        idx_raw = os.environ.get("VOICE_ZH_DEVICE_INDEX")
        try:
            device_index = int(idx_raw) if idx_raw not in (None, "") else None
        except (TypeError, ValueError):
            # an invalid index must not take the whole voice chain down — warn + fall back
            print(f"[voice-mcp] invalid VOICE_ZH_DEVICE_INDEX={idx_raw!r}, using default input device",
                  file=sys.stderr, flush=True)
            device_index = None
        engine = _stt.SttEngine(model=model, device=device, device_index=device_index)
        engine.load()
        _engines[model] = engine
    return _engines[model]


@mcp.tool()
def listen(max_seconds: float = 20.0, silence_sec: float = 0.8) -> str:
    """录制用户的一句中文语音并返回转写文本（阻塞直到一句话说完或超时）。

    用于在会话内主动拉取用户的语音输入。录音从检测到说话开始，到出现 `silence_sec`
    秒静音结束，最长 `max_seconds` 秒。返回识别出的中文（静音/超时返回空字符串）。
    遇决策点/需补充信息时在工作模式下调用它，与 announce 配合实现双向交互。
    """
    mode = _state.get_mode()
    if _daemon.daemon_active():
        # model A: a live STT daemon owns the mic -> READ the transcript queue instead of
        # opening a second InputStream (two streams on one device contend -> PortAudio
        # -9998). Time-anchored: only utterances spoken AFTER this call are returned, so a
        # pre-question backlog isn't mistaken for the answer (that backlog is the Stop-hook
        # drain's channel). silence_sec is irrelevant here — the daemon owns segmentation.
        text = _daemon.wait_for_utterance(max_seconds=max_seconds)
    else:
        # no daemon -> self-open the mic, exactly as #468. Path 2 is OPT-IN: don't start the
        # daemon and listen() behaves as before. A leaked mic device (a daemon that died
        # without releasing its stream) surfaces here as PortAudioError -9998; surface a
        # clear, actionable message rather than crashing the tool (§8).
        try:
            engine = _engine_for_mode(mode)
            text = engine.listen_once(max_seconds=max_seconds, silence_sec=silence_sec)
        except Exception as e:  # noqa: BLE001 — never crash listen(). Returns EARLY (before the
            # secretary record_note below) so a capture FAILURE is never written as a note. Only
            # a PortAudio error means the mic is contended (e.g. a leaked daemon device, §8);
            # other errors (engine load / decode) report their real type so the message isn't a
            # misleading "device occupied" claim.
            print(f"[voice-mcp] listen_once failed: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
            if "PortAudio" in type(e).__name__:
                return ("（语音捕获失败：麦克风可能被残留进程占用 [PortAudio -9998]。"
                        "请重启 voice daemon 或确认无其他进程占用麦克风后重试。）")
            return f"（语音捕获失败：{type(e).__name__}。请检查 STT 引擎 / 音频设备配置后重试。）"
    # in secretary mode, transcribed speech is auto-recorded as a note
    if mode == "secretary" and text:
        _state.record_note(text, kind="note")
    return text


@mcp.tool()
def announce(text: str) -> str:
    """用本地中文 TTS 朗读 `text`，仅播报不监听（agent 主动提示用户的原语）。

    工作模式遇到决策需求/需补充信息/任务完成时调用，主动语音提示用户回到交互。
    优先用本地 Kokoro-FastAPI（离线、隐私），不可达时按 VOICE_TTS_FALLBACK 回退。
    返回播报结果状态（不会因 TTS 失败而抛错）。
    """
    # length is bounded inside tts.speak() (VOICE_TTS_MAX_CHARS) for all callers
    result = _tts.speak(text, blocking=True)
    if result["ok"]:
        return f"已播报（{result['backend']}）"
    return f"播报失败：{result['error']}（请确认 Kokoro-FastAPI 服务在 {os.environ.get('VOICE_TTS_BASE_URL', 'http://127.0.0.1:8880/v1')} 运行）"


@mcp.tool()
def set_mode(mode: str, task_context: str = "") -> str:
    """显式切换语音交互模式：secretary（秘书：快速记录总结，不执行任务）或
    work（工作：基于已掌握需求自主推进任务，遇 check-in 点主动提示）。

    切换由用户显式声明驱动（如 /secretary、/work）。切到 work 时可带 task_context
    描述本次要推进的任务。返回切换后的状态描述。
    """
    try:
        st = _state.set_mode(mode, task_context=task_context or None)
    except ValueError as e:
        return f"切换失败：{e}"
    ctx = f"，任务：{st['task_context']}" if st.get("task_context") else ""
    notes = f"，记录文件：{st['notes_path']}" if st.get("notes_path") else ""
    return f"已切换到 {st['mode']} 模式{ctx}{notes}"


@mcp.tool()
def record_note(text: str, kind: str = "note") -> str:
    """在秘书模式记录文件中追加一条结构化、带时间戳的条目。

    `kind` 取 note（记录）/ requirement（需求）/ decision（决策）/ summary（总结）。
    用于秘书模式把用户发言结构化沉淀，供后续工作模式读取。返回记录文件路径。
    """
    path = _state.record_note(text, kind=kind)
    return f"已记录到 {path}" if path else "未记录（文本为空）"


@mcp.tool()
def get_status() -> str:
    """返回当前语音交互状态：模式 + 任务上下文 + 记录文件 + 本地 TTS 服务是否在线。"""
    st = _state.get_state()
    tts_ok = _tts.health()
    return (
        f"模式：{st.get('mode')}；任务：{st.get('task_context') or '—'}；"
        f"记录文件：{st.get('notes_path') or '—'}；"
        f"Kokoro-FastAPI：{'在线' if tts_ok else '离线（announce 将尝试回退或失败）'}"
    )


if __name__ == "__main__":
    mcp.run()  # stdio is the default transport
