"""用 Codex 形态的 stdin 跑记忆层三个 hook（Mercury Issue #571 / G5-3）。

为什么要单独测：Codex 与 Claude 的 hook stdin 大部分字段一一对应，但有两处已知差异，
都可能让脚本静默失败而不是报错：

  1. SessionEnd 在 Codex 给的是 `reason`（值恒为 "other"），而脚本读的是 `source`
     —— 它会拿到 None。需要确认脚本容忍而不是抛异常。
  2. `transcript_path` 指向 Codex 的 rollout jsonl，与 Claude 的 transcript 格式不同。
     脚本会逐行 json.loads 去解析，格式不兼容时可能读出空结果 —— 空结果和"正常但没内容"
     长得一样，所以必须看脚本有没有崩，而不是只看退出码。

判据（项目治理规则）：每个脚本在合成 stdin 下 exit 0。
另外单独记录 stderr —— exit 0 但 stderr 有 traceback 属于"看起来通过实际坏了"。
"""
import json
import subprocess
import sys
import os

import glob

CC = os.path.expanduser(os.environ.get("CLAUDE_CONFIG_DIR", "~/.claude"))
PY = os.path.join(CC, ".venv", "Scripts", "python.exe")
if not os.path.exists(PY):  # 非 Windows 布局
    PY = os.path.join(CC, ".venv", "bin", "python")

# 用**真实**的 Codex rollout，不用手写夹具 —— 手写夹具的形状可能本身就不对，
# 那样测出来的「通过」或「失败」都不能说明真实行为。
_rollouts = sorted(
    glob.glob(os.path.expanduser("~/.codex/sessions/*/*/*/*.jsonl")),
    key=lambda p: os.path.getmtime(p),
    reverse=True,
)
_sized = [p for p in _rollouts if 50_000 < os.path.getsize(p) < 3_000_000]
ROLLOUT = (_sized or _rollouts or [""])[0]

# 用 json.dumps 构造，不手写 JSON 字符串 —— 手写的引号/转义错误会让夹具坏得像通过。
CASES = [
    ("SessionStart", "session-start.py", {
        "session_id": "g53-probe-start",
        "transcript_path": ROLLOUT,
        "cwd": r"D:\Mercury\Mercury",
        "hook_event_name": "SessionStart",
        "source": "startup",
    }),
    ("SessionEnd", "session-end.py", {
        "session_id": "g53-probe-end",
        "transcript_path": ROLLOUT,
        "cwd": r"D:\Mercury\Mercury",
        "hook_event_name": "SessionEnd",
        # 差异 1：Codex 给 reason 而非 source。故意不给 source。
        "reason": "other",
    }),
    ("PreCompact", "pre-compact.py", {
        "session_id": "g53-probe-compact",
        "transcript_path": ROLLOUT,
        "cwd": r"D:\Mercury\Mercury",
        "hook_event_name": "PreCompact",
        "trigger": "auto",
    }),
]

# 夹具健全性自检：解释器与三个脚本都必须存在，否则后面的 "exit 0" 毫无意义。
missing = [p for p in [PY, ROLLOUT] + [os.path.join(CC, "hooks", s) for _, s, _ in CASES]
           if not os.path.exists(p)]
if missing:
    print("夹具不完整，测试无效：")
    for m in missing:
        print("   缺失:", m)
    sys.exit(2)
print(f"夹具自检通过：解释器 + {len(CASES)} 个脚本 + rollout 夹具均存在\n")

failures = 0
for name, script, payload in CASES:
    path = os.path.join(CC, "hooks", script)
    try:
        proc = subprocess.run(
            [PY, path],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        print(f"[超时] {name:14s} {script} —— 60 秒未返回")
        failures += 1
        continue

    err = (proc.stderr or "").strip()
    # exit 0 但 stderr 里有 traceback，属于"看起来通过实际坏了"，单独判。
    broke = "Traceback" in err or "Error" in err
    status = "通过" if (proc.returncode == 0 and not broke) else "失败"
    if status == "失败":
        failures += 1
    print(f"[{status}] {name:14s} exit={proc.returncode}")
    if proc.stdout.strip():
        print(f"         stdout: {proc.stdout.strip()[:200]}")
    if err:
        print(f"         stderr: {err[:400]}")

print(f"\n{len(CASES) - failures}/{len(CASES)} 通过")
sys.exit(1 if failures else 0)
