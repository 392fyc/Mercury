#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 packs/subagent-dev/skills/mercury-subagent-driven-development/ 的全部文件。

    python packs/subagent-dev/build/generate.py           写出（覆盖）
    python packs/subagent-dev/build/generate.py --check   只比对；任一文件不一致则退出 1 并列出

四个文件都是 Mercury 的改写或自写（见 UPSTREAM.md），全文放在 overlay/。生成脚本做三件事：
1. 确认子模块 sources/superpowers 检出在钉住的提交；
2. 确认 overlay 引用的上游原文（ANCHORS）在钉住的提交里仍然存在，防止悄悄换了出处；
3. 把 overlay/ 写出，或与现有文件比对（忽略 CRLF/LF 差异）。
"""
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PACK = HERE.parent
PIN = "d884ae04edebef577e82ff7c4e143debd0bbec99"
MOD = ROOT / "sources" / "superpowers"
UP = MOD / "skills" / "subagent-driven-development"
OUT = PACK / "skills" / "mercury-subagent-driven-development"
# 上游文件 -> 必须仍存在的原文片段（该片段也出现在 overlay 的对应文件里）
ANCHORS = {"implementer-prompt.md": ["It is always OK to stop and say", "Never silently produce work you're unsure about."]}
# 这两个文件取自更早的提交 917e5f5，钉住的提交里已被上游删除；只能整篇放 overlay，不做锚点检查
FROM_EARLIER = ("spec-reviewer-prompt.md", "code-quality-reviewer-prompt.md")


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
    files = {p.name: rd(p) for p in sorted((HERE / "overlay").iterdir()) if p.is_file()}
    bad = []
    for name, phrases in ANCHORS.items():
        bad += [f"上游缺少锚点：{name}：{x!r}" for x in phrases if x not in rd(UP / name)]
    bad += [f"overlay 缺少文件：{n}" for n in FROM_EARLIER if n not in files]
    if "--check" not in argv:
        if bad:
            print("\n".join(bad), file=sys.stderr)
            return 1
        OUT.mkdir(parents=True, exist_ok=True)
        for name, text in files.items():
            (OUT / name).write_text(text, encoding="utf-8", newline="\n")
        print(f"已写出 {len(files)} 个文件到 {OUT.relative_to(ROOT).as_posix()}")
        return 0
    bad += [f"{'不一致' if (OUT / n).is_file() else '缺失'}：{n}" for n, t in files.items()
            if not (OUT / n).is_file() or rd(OUT / n) != t]
    bad += [f"多余：{p.name}" for p in sorted(OUT.iterdir()) if p.is_file() and p.name not in files]
    print("\n".join(bad), file=sys.stderr) if bad else None
    print("superpowers 适配层：" + (f"{len(bad)} 处差异" if bad else f"{len(files)} 个文件与生成结果一致"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
