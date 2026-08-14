"""验证 session-end.py 的解析器改动（Mercury #571 / G5-3）。

两条都必须过，缺一不可：
  A. Codex 路径**现在能读出内容**（改动前是零条 → "SKIP: empty context"）
  B. Claude 路径**没有被打断**（这是脚本的现役用途，回归了就是把好的改坏）

用真实数据，不用手写夹具 —— 手写夹具的形状可能本身就不对，那样两边都测不出真相。
"""
import glob
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

CC = Path(os.path.expanduser("~/.claude"))
spec = importlib.util.spec_from_file_location("se", CC / "hooks" / "session-end.py")
se = importlib.util.module_from_spec(spec)
sys.modules["se"] = se
spec.loader.exec_module(se)
extract = se.extract_conversation_context

failures = []

# ---------- A. Codex 真实 rollout ----------
rollouts = sorted(
    glob.glob(os.path.expanduser("~/.codex/sessions/2026/*/*/*.jsonl")),
    key=os.path.getmtime,
    reverse=True,
)
sized = [(f, os.path.getsize(f)) for f in rollouts]
codex_targets = [f for f, s in sized if 50_000 < s < 3_000_000][:5]

if not codex_targets:
    failures.append("找不到可用的真实 Codex rollout，测试无效")
else:
    print(f"A. Codex 路径 —— 用 {len(codex_targets)} 个真实 rollout")
    got_any = False
    for t in codex_targets:
        ctx, turns = extract(Path(t))
        mark = "有内容" if ctx.strip() else "仍为空"
        print(f"   {os.path.basename(t)[:52]:52s} turns={turns:3d}  {mark}")
        if ctx.strip():
            got_any = True
    if got_any:
        print("   → 通过：至少一个真实 rollout 现在能读出对话内容")
    else:
        failures.append("Codex 路径仍然读不出任何内容 —— 改动无效")

# ---------- B. Claude 侧格式不能回归 ----------
print("\nB. Claude 路径 —— 用 Claude transcript 形状")
claude_lines = [
    {"message": {"role": "user", "content": "第一个问题"}},
    {"message": {"role": "assistant", "content": [{"type": "text", "text": "第一个回答"}]}},
    {"message": {"role": "user", "content": "第二个问题"}},
    {"type": "summary", "summary": "不该被算作对话"},
]
with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as fh:
    for obj in claude_lines:
        fh.write(json.dumps(obj, ensure_ascii=False) + "\n")
    claude_path = fh.name

ctx, turns = extract(Path(claude_path))
os.unlink(claude_path)

expect = ["第一个问题", "第一个回答", "第二个问题"]
missing = [e for e in expect if e not in ctx]
if turns != 3:
    failures.append(f"Claude 路径轮次错：期待 3，实得 {turns}")
if missing:
    failures.append(f"Claude 路径丢内容：{missing}")
if "不该被算作对话" in ctx:
    failures.append("Claude 路径把 summary 行误当成对话")

print(f"   turns={turns}（期待 3）")
print(f"   三条内容齐全: {not missing}")
print(f"   summary 行被正确忽略: {'不该被算作对话' not in ctx}")
if turns == 3 and not missing:
    print("   → 通过：现役 Claude 路径未受影响")

# ---------- C. 不能重复计数 ----------
print("\nC. 双计数检查 —— 同一轮用户输入不能被记两遍")
codex_dup = [
    {"timestamp": "t", "type": "event_msg",
     "payload": {"type": "user_message", "message": "唯一的一句"}},
    {"timestamp": "t", "type": "response_item",
     "payload": {"type": "message", "role": "user",
                 "content": [{"type": "input_text", "text": "唯一的一句"}]}},
]
with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as fh:
    for obj in codex_dup:
        fh.write(json.dumps(obj, ensure_ascii=False) + "\n")
    dup_path = fh.name
ctx2, turns2 = extract(Path(dup_path))
os.unlink(dup_path)
print(f"   turns={turns2}（期待 1，两条记录表示同一轮）")
if turns2 != 1:
    failures.append(f"双计数：期待 1 轮，实得 {turns2}")
else:
    print("   → 通过：没有重复计数")

print()
if failures:
    print("失败:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("三项全部通过")
