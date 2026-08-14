"""用桩程序验证 flush.py 的 harness 分流（Mercury #571 / G5-3）。

能验什么 / 不能验什么，先说清楚：
  能验 —— 分流逻辑、参数构造、输出取法、错误路径、claude 路径未回归。
  不能验 —— 真实模型产出的摘要质量（需要 G0-1 登录）。

桩程序是关键：它把收到的 argv 与 stdin 落盘，于是可以断言
「codex 确实拿到了 --skip-git-repo-check」这类**具体事实**，
而不是只看一个 exit code。
"""
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

CC = Path(os.path.expanduser("~/.claude"))
spec = importlib.util.spec_from_file_location("flushmod", CC / "scripts" / "flush.py")
flush = importlib.util.module_from_spec(spec)
sys.modules["flushmod"] = flush
spec.loader.exec_module(flush)

TMP = Path(tempfile.mkdtemp(prefix="flush-harness-"))
ARGV_LOG = TMP / "argv.log"
failures = []


def make_stub(name: str, *, write_output_file: bool, final_text: str = "STUB SUMMARY") -> Path:
    """造一个假 CLI：记录 argv+stdin；可选地按 -o 参数写出最终消息。"""
    d = TMP / f"stub-{name}"
    d.mkdir(exist_ok=True)
    script = d / f"{name}.py"
    script.write_text(
        "import sys, os\n"
        "from pathlib import Path\n"
        f"log = Path(r'{ARGV_LOG}')\n"
        "argv = sys.argv[1:]\n"
        "data = sys.stdin.read()\n"
        "log.write_text('ARGV: ' + repr(argv) + '\\nSTDIN_LEN: ' + str(len(data)) + '\\n', encoding='utf-8')\n"
        # codex 的事件流：故意往 stdout 打噪声，用来验证「不能拿 stdout 当摘要」
        "print('[EVENT] task_started')\n"
        "print('[EVENT] tool_call ...')\n"
        + (
            "if '-o' in argv:\n"
            f"    Path(argv[argv.index('-o') + 1]).write_text({final_text!r}, encoding='utf-8')\n"
            if write_output_file
            else "print(%r)\n" % final_text
        ),
        encoding="utf-8",
    )
    launcher = d / (f"{name}.cmd" if sys.platform == "win32" else name)
    if sys.platform == "win32":
        launcher.write_text(f'@echo off\r\n"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    else:
        launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
        launcher.chmod(0o755)
    return d


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"[通过] {name}")
    else:
        print(f"[失败] {name}  {detail}")
        failures.append(name)


# 夹具健全性自检：桩程序必须真的可执行，否则后面的断言全无意义。
codex_dir = make_stub("codex", write_output_file=True, final_text="CODEX SUMMARY OK")
probe = subprocess.run(
    [str(codex_dir / ("codex.cmd" if sys.platform == "win32" else "codex")), "--probe"],
    input="x", capture_output=True, text=True,
)
if probe.returncode != 0 or not ARGV_LOG.is_file():
    print("夹具无效：桩程序跑不起来，测试作废")
    print(probe.stderr[:400])
    sys.exit(2)
print(f"夹具自检通过：桩 CLI 可执行并记录了 argv\n")

orig_path = os.environ.get("PATH", "")

# ---------- 1. 默认仍走 claude ----------
# 注意查找顺序：_find_claude_exe() 先看 CLAUDE_CODE_EXECPATH，再看
# ~/.local/bin/claude.exe，最后才查 PATH。桩程序只放 PATH 里会被真的 claude.exe
# 抢先命中（第一次跑就踩了这个，返回了真实的 FLUSH_OK）——所以必须用排第一位的
# CLAUDE_CODE_EXECPATH 才能确保测到的是桩。
os.environ.pop("MERCURY_FLUSH_HARNESS", None)
claude_dir = make_stub("claude", write_output_file=False, final_text="CLAUDE SUMMARY OK")
os.environ["CLAUDE_CODE_EXECPATH"] = str(
    claude_dir / ("claude.cmd" if sys.platform == "win32" else "claude")
)
os.environ["PATH"] = str(claude_dir) + os.pathsep + orig_path
ARGV_LOG.unlink(missing_ok=True)
out = flush.run_flush("一些对话上下文")
log = ARGV_LOG.read_text(encoding="utf-8") if ARGV_LOG.is_file() else ""
check("默认 harness 走 claude 且取 stdout", "CLAUDE SUMMARY OK" in out, f"got={out[:120]!r}")
check("claude 收到 -p", "'-p'" in log, f"log={log[:160]!r}")

# ---------- 2. 显式切 codex ----------
os.environ["MERCURY_FLUSH_HARNESS"] = "codex"
os.environ["PATH"] = str(codex_dir) + os.pathsep + orig_path
ARGV_LOG.unlink(missing_ok=True)
out = flush.run_flush("一些对话上下文")
log = ARGV_LOG.read_text(encoding="utf-8") if ARGV_LOG.is_file() else ""

check("codex 路径取的是 -o 最终消息", out == "CODEX SUMMARY OK", f"got={out[:160]!r}")
check("事件流噪声没有混进摘要", "[EVENT]" not in out, f"got={out[:160]!r}")
check("codex 收到 exec 子命令", "'exec'" in log, f"log={log[:200]!r}")
check("codex 收到 --skip-git-repo-check（ROOT 非 git 仓库，缺了会拒绝运行）",
      "'--skip-git-repo-check'" in log, f"log={log[:200]!r}")
check("codex 收到只读沙箱", "'read-only'" in log, f"log={log[:200]!r}")
check("prompt 经 stdin 传入（非空）", "STDIN_LEN: 0" not in log, f"log={log[:200]!r}")

# ---------- 3. 临时文件不残留 ----------
leftovers = list((CC / "scripts").glob("_flush-codex-out-*.txt"))
check("临时输出文件已清理", not leftovers, f"残留={[p.name for p in leftovers]}")

# ---------- 4. 非法值 fail-closed ----------
os.environ["MERCURY_FLUSH_HARNESS"] = "gemini"
out = flush.run_flush("ctx")
check("非法 harness 被拒且不静默回退", out.startswith("FLUSH_ERROR"), f"got={out[:120]!r}")

# ---------- 5. codex 不存在时报错清晰 ----------
os.environ["MERCURY_FLUSH_HARNESS"] = "codex"
os.environ["PATH"] = str(TMP / "empty")
(TMP / "empty").mkdir(exist_ok=True)
out = flush.run_flush("ctx")
check("codex 缺失时给出明确错误", "codex executable not found" in out, f"got={out[:120]!r}")

os.environ["PATH"] = orig_path
os.environ.pop("MERCURY_FLUSH_HARNESS", None)
os.environ.pop("CLAUDE_CODE_EXECPATH", None)
shutil.rmtree(TMP, ignore_errors=True)

print()
if failures:
    print(f"{len(failures)} 项失败：", failures)
    sys.exit(1)
print("全部通过")
