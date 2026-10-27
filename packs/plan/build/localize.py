# -*- coding: utf-8 -*-
"""html-plan 运行时中文化：对上游 htmlplan.js / htmlplan.css / pack.mjs 做精确字符串替换。

替换表在 localize.table，格式（每条一段，按文件顺序依次应用）：
    @@ <js|css|pack> <预期命中数>
    旧文本（可多行，逐字）
    @@ ->
    新文本
    @@ END
每条都断言命中次数等于预期；任何一条不符，localize() 抛 ValueError 并列出全部失效项。
上游：anthropics/claude-plugins-community，路径 html-plan/skills/html-plan/runtime/。Mercury Issue #655。
"""
from pathlib import Path

FILES = {"js": "htmlplan.js", "css": "htmlplan.css", "pack": "pack.mjs"}
UP_PATH = "html-plan/skills/html-plan/runtime/"


def parse_table(table):
    entries, lines, i = [], table.split("\n"), 0
    while i < len(lines):
        line = lines[i]
        if not line.startswith("@@ ") or line.startswith(("@@ ->", "@@ END")):
            if line.strip():
                raise ValueError(f"替换表格式错误，第 {i + 1} 行：{line!r}")
            i += 1
            continue
        _, key, count = line.split()
        old, new, cur = [], [], None
        cur = old
        i += 1
        while lines[i] != "@@ END":
            if lines[i] == "@@ ->":
                cur = new
            else:
                cur.append(lines[i])
            i += 1
        entries.append((key, int(count), "\n".join(old), "\n".join(new)))
        i += 1
    return entries


def localize(texts, sha, table_path=Path(__file__).with_name("localize.table")):
    """texts: {js, css, pack} 的 LF 文本。返回加好署名的中文版 {文件名: 文本}。"""
    # pack.mjs 里 ASD-STE100 英文检查所在的整段，逐字取自上游，作为一条替换的旧文本。
    pk = texts["pack"].split("\n")
    s = next((i for i, l in enumerate(pk) if l.strip().startswith("let total = 0, long = 0")), None)
    e = next((i for i, l in enumerate(pk) if "ASD-STE100: ${six} paragraph" in l), None)
    ste = "<<未找到 STE 段落>>"
    if s is not None and e is not None and s < e and pk[e].endswith(" }"):
        ste = "\n".join(pk[s:e + 1])[:-2]  # 末尾的 " }" 同时关闭外层代码块，保留
    entries = parse_table(table_path.read_text(encoding="utf-8").replace("@@STE_BLOCK@@", ste))
    texts, failures = dict(texts), []
    for key, expect, old, new in entries:
        got = texts[key].count(old)
        if got != expect:
            failures.append(f"[{FILES[key]}] 预期命中 {expect} 次，实际 {got} 次：{old[:90]!r}")
        else:
            texts[key] = texts[key].replace(old, new)
    shebang = "#!/usr/bin/env node\n"
    if not texts["pack"].startswith(shebang):
        failures.append("pack.mjs 不是以 shebang 开头")
    if failures:
        raise ValueError(f"{len(failures)} 条替换失效（上游可能已更新）：\n  " + "\n  ".join(failures))

    def hdr(name):
        return (f"// UPSTREAM: anthropics/claude-plugins-community (html-plan, MIT)\n// SOURCE: {UP_PATH}{name}\n"
                f"// SHA: {sha}\n// DATE: 2026-10-07\n// ISSUE: #655 (Mercury 中文版，已修改)\n")
    css = f"/* Based on anthropics/claude-plugins-community html-plan (MIT) SHA: {sha} — Mercury #655 中文版 */\n"
    return {"htmlplan.js": hdr("htmlplan.js") + texts["js"],
            "htmlplan.css": css + texts["css"],
            "pack.mjs": shebang + hdr("pack.mjs") + texts["pack"][len(shebang):]}
