#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从子模块 modules/claude-plugins-community 生成 .agents/skills/html-plan-zh/ 全部文件。

    python adapters/html-plan/generate.py           写出（覆盖）
    python adapters/html-plan/generate.py --check   只比对；任一文件不一致则退出 1 并列出

来源分类见 UPSTREAM.md：runtime/* 由 localize.py 机器转换；references/blocks.md 用补丁；
LICENSE = license-header.txt + 上游仓库 LICENSE；其余取 overlay/。比对忽略 CRLF/LF 差异。
"""
import re
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import localize  # noqa: E402

PIN = "88003be1129c09381d27100b37a8fdc21b3214bf"
MOD = ROOT / "modules" / "claude-plugins-community"
SRC = MOD / "html-plan" / "skills" / "html-plan"
OUT = ROOT / ".agents" / "skills" / "html-plan-zh"


def rd(p):
    return p.read_bytes().decode("utf-8").replace("\r\n", "\n")


def apply_patch(text, patch):
    src, out, pos = text.split("\n"), [], 0
    parts = re.split(r"^@@ -(\d+)(?:,\d+)? \+\d+(?:,\d+)? @@.*\n", patch, flags=re.M)[1:]
    for start, body in zip(parts[::2], parts[1::2]):
        out += src[pos:int(start) - 1]
        pos = int(start) - 1
        for line in body.split("\n"):
            if line.startswith("+"):
                out.append(line[1:])
            elif line[:1] in (" ", "-"):
                if src[pos] != line[1:]:
                    raise ValueError(f"补丁与上游不符，第 {pos + 1} 行：{src[pos]!r}")
                if line[0] == " ":
                    out.append(src[pos])
                pos += 1
    return "\n".join(out + src[pos:])


def build():
    files = {}
    for p in sorted((HERE / "overlay").rglob("*")):
        if p.is_file():
            files[p.relative_to(HERE / "overlay").as_posix()] = rd(p)
    texts = {k: rd(SRC / "runtime" / n) for k, n in localize.FILES.items()}
    for name, text in localize.localize(texts, PIN).items():
        files["runtime/" + name] = text
    files["references/blocks.md"] = apply_patch(rd(SRC / "references" / "blocks.md"), rd(HERE / "blocks.md.patch"))
    files["LICENSE"] = rd(HERE / "license-header.txt") + rd(MOD / "LICENSE")
    return files


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
    try:
        files = build()
    except ValueError as err:
        print(err, file=sys.stderr)
        return 1
    if "--check" not in argv:
        for rel, text in files.items():
            (OUT / rel).parent.mkdir(parents=True, exist_ok=True)
            (OUT / rel).write_text(text, encoding="utf-8", newline="\n")
        print(f"已写出 {len(files)} 个文件到 {OUT.relative_to(ROOT).as_posix()}")
        return 0
    bad = [f"不一致：{r}" if (OUT / r).is_file() else f"缺失：{r}" for r, t in files.items()
           if not (OUT / r).is_file() or rd(OUT / r) != t]
    bad += [f"多余：{p.relative_to(OUT).as_posix()}" for p in sorted(OUT.rglob("*"))
            if p.is_file() and p.relative_to(OUT).as_posix() not in files]
    for line in bad:
        print(line, file=sys.stderr)
    print("html-plan 适配层：" + (f"{len(bad)} 处差异" if bad else f"{len(files)} 个文件与生成结果一致"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
