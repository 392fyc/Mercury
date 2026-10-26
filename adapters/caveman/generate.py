#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 harness/modules/caveman/ 下的 skills/caveman-toggle/SKILL.md 与 files/CLAUDE.local.md.example。

    python adapters/caveman/generate.py           写出（覆盖）
    python adapters/caveman/generate.py --check   只比对；任一文件不一致则退出 1 并列出

两个文件是 Mercury 自写的开关技能与模板（见 UPSTREAM.md），仅提示词措辞取自上游，
全文放在 overlay/，路径与 harness/modules/caveman/ 下的相对路径一致。生成脚本做三件事：
1. 确认子模块 modules/caveman 检出在钉住的提交；
2. 确认 overlay 引用的上游原文（ANCHORS）在钉住的提交里仍然存在；
3. 把 overlay/ 写出，或与现有文件比对（忽略 CRLF/LF 差异）。
"""
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PIN = "26c25e39b3aee469dbb017427ab55ee1d32de1a8"
MOD = ROOT / "modules" / "caveman"
OUT = ROOT / "harness" / "modules" / "caveman"
UP = MOD / "skills" / "caveman" / "SKILL.md"
ANCHORS = ["Respond terse like smart caveman.", "Code blocks unchanged. Errors quoted exact."]


def rd(p):
    return p.read_bytes().decode("utf-8").replace("\r\n", "\n")


def main(argv):
    for s in (sys.stdout, sys.stderr):
        s.reconfigure(encoding="utf-8")
    if set(argv[1:]) - {"--check"}:
        print(__doc__)
        return 2
    # An uninitialised submodule is an empty directory; git -C would then report the superproject's HEAD.
    head = ""
    if (MOD / ".git").exists():
        head = subprocess.run(["git", "-C", str(MOD), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    if head != PIN:
        print(f"子模块 {MOD.name} 未检出到钉住的提交 {PIN}（当前 {head or '未初始化'}）；先运行 git submodule update --init", file=sys.stderr)
        return 2
    files = {p.relative_to(HERE / "overlay").as_posix(): rd(p) for p in sorted((HERE / "overlay").rglob("*")) if p.is_file()}
    bad = [f"上游缺少锚点：{x!r}" for x in ANCHORS if x not in rd(UP)]
    if "--check" not in argv:
        if bad:
            print("\n".join(bad), file=sys.stderr)
            return 1
        for rel, text in files.items():
            (OUT / rel).parent.mkdir(parents=True, exist_ok=True)
            (OUT / rel).write_text(text, encoding="utf-8", newline="\n")
        print(f"已写出 {len(files)} 个文件")
        return 0
    bad += [f"{'不一致' if (OUT / r).is_file() else '缺失'}：{r}" for r, t in files.items()
            if not (OUT / r).is_file() or rd(OUT / r) != t]
    if bad:
        print("\n".join(bad), file=sys.stderr)
    print("caveman 适配层：" + (f"{len(bad)} 处差异" if bad else f"{len(files)} 个文件与生成结果一致"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
