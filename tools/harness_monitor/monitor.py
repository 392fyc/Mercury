#!/usr/bin/env python3
"""Report on what agents actually consult, to feed improvements back into the harness (Mercury #755).

One run: agentsview refreshes its SQLite copy of the Claude Code and Codex logs and exits; this program reads that copy
read-only, classifies the changed sessions with rules.py, keeps only counts in stats.db and writes report.html. It
leaves nothing running. See README.md next to this file.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from rules import analyse  # noqa: E402

AGENTSVIEW_VERSION = "0.46.1"
SYNC_SECONDS = 900  # the first import reads every log
CLASSES = {"M": "记忆读取", "B": "跨 lane 消息", "S": "skill 文档", "K": "知识库", "R": "规则文件", "W": "网络查询", "G": "GitHub"}
ALL = "ALL"  # the count of every validated call, whatever its class
HOSTS = ("claude", "codex")
SKILL_PATH = re.compile(r"[\\/]([A-Za-z0-9_.:-]+)[\\/]+SKILL\.md")
TOOLS = ("Bash", "PowerShell", "exec", "js", "Read", "Grep", "Glob", "WebFetch", "WebSearch", "Skill")
SOURCE_COLUMNS = {
    "sessions": {"id", "agent", "project", "cwd", "file_size", "file_mtime", "file_hash", "message_count", "deleted_at", "local_modified_at"},
    "tool_calls": {"session_id", "message_id", "tool_use_id", "tool_name", "input_json", "file_path"},
    "messages": {"id", "timestamp"},
}
SCHEMA = """
create table if not exists seen(session_id text primary key, fingerprint text not null);
create table if not exists counts(session_id text, day text, host text, project text, cell text, calls integer);
create index if not exists counts_session on counts(session_id);
create table if not exists tools(session_id text, host text, tool text, day text, calls integer);
create index if not exists tools_session on tools(session_id);
create table if not exists skills(session_id text, day text, host text, project text, skill text, calls integer);
create index if not exists skills_session on skills(session_id);
create table if not exists meta(key text primary key, value text);
"""


def normal_sha256(data):
    """Hash with LF line ends, so a Windows checkout and a Linux checkout agree."""
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def rules_sha256():
    return normal_sha256((HERE / "rules.py").read_bytes())


def code_sha256():
    """Counts depend on this program as well as on the rules, so a change to either recounts everything."""
    return normal_sha256((HERE / "rules.py").read_bytes() + Path(__file__).read_bytes())


def rules_valid(validation):
    """Both the rules and the set of tools they were checked on decide what a count means."""
    return rules_sha256() == validation["rules_sha256"] and sorted(TOOLS) == sorted(validation["tools"])


def key_of(session_id):
    """stats.db keys a session by this hash, so the file alone cannot be traced back to a transcript."""
    return hashlib.sha256(session_id.encode()).hexdigest()[:16]


def load_validation():
    return json.loads((HERE / "validation.json").read_text(encoding="utf-8"))


def load_config(state_dir):
    """The machine's own settings. They stay in the ignored state directory because they can name private projects."""
    path = Path(state_dir) / "config.json"
    cfg = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    return {
        "agentsview_bin": cfg.get("agentsview_bin") or os.environ.get("MERCURY_AGENTSVIEW_BIN") or "agentsview",
        "agentsview_data_dir": cfg.get("agentsview_data_dir") or str(Path(state_dir) / "agentsview"),
        "exclude_projects": list(cfg.get("exclude_projects") or []),
        "exclude_text": cfg.get("exclude_text") or "",
        "project_aliases": dict(cfg.get("project_aliases") or {}),
    }


def call_text(raw):
    text = raw or ""
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            text = str(data.get("command") or data.get("input") or text)
    except ValueError:
        pass
    return text


def classify(tool, raw, path):
    return analyse(tool, call_text(raw), path or "") & set(CLASSES)


def skill_names(tool, raw, path):
    """Which skills a call that counts as S names: the Skill tool's argument, or the folder of a SKILL.md."""
    if tool == "Skill":
        try:
            return {str(json.loads(raw or "{}").get("skill") or "unknown")}
        except (ValueError, AttributeError):
            return {"unknown"}
    return set(SKILL_PATH.findall(f"{path or ''} {call_text(raw)}"))


def open_source(path):
    con = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    for table, need in SOURCE_COLUMNS.items():
        have = {r[1] for r in con.execute(f"pragma table_info({table})")}
        if need - have:
            con.close()
            raise RuntimeError(f"agentsview table {table} lacks {sorted(need - have)}; the monitor expects version {AGENTSVIEW_VERSION}")
    return con


def open_stats(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=30)
    con.execute("pragma secure_delete=on")  # a project excluded later must not linger in free pages
    con.executescript(SCHEMA)
    return con


def meta(con, key, default=""):
    row = con.execute("select value from meta where key=?", (key,)).fetchone()
    return row[0] if row else default


def set_meta(con, **values):
    con.executemany("insert or replace into meta values(?,?)", [(k, str(v)) for k, v in values.items()])


def forget(con, session_id):
    for table in ("seen", "counts", "tools", "skills"):
        con.execute(f"delete from {table} where session_id=?", (session_id,))


def count_session(src, session_id, key, host, project, private):
    """Return the session's rows for the three count tables. A call that matches the private text is left out."""
    counts, tools, skills, used = {}, {}, {}, set()
    query = (
        "select t.tool_use_id,t.tool_name,t.input_json,t.file_path,m.timestamp from tool_calls t "
        "left join messages m on m.id=t.message_id where t.session_id=? order by t.id"
    )
    for use_id, tool, raw, path, stamp in src.execute(query, (session_id,)):
        if use_id and use_id in used:
            continue
        used.add(use_id)
        if private and (private.search(call_text(raw)) or private.search(path or "")):
            continue
        day = (stamp or "")[:10] or "unknown"
        tools[(tool, day)] = tools.get((tool, day), 0) + 1
        if tool not in TOOLS:
            continue
        cells = classify(tool, raw, path)
        for cell in cells | {ALL}:
            counts[(day, cell)] = counts.get((day, cell), 0) + 1
        for skill in skill_names(tool, raw, path) if "S" in cells else ():
            skills[(day, skill)] = skills.get((day, skill), 0) + 1
    return (
        [(key, d, host, project, c, n) for (d, c), n in counts.items()],
        [(key, host, t, d, n) for (t, d), n in tools.items()],
        [(key, d, host, project, s, n) for (d, s), n in skills.items()],
    )


def update(source_db, stats_db, config):
    """One pass: recount the sessions whose log changed. A change of rules or settings recounts everything."""
    private = re.compile(config["exclude_text"], re.I) if config["exclude_text"] else None
    excluded = config["exclude_projects"]
    aliases = config.get("project_aliases") or {}
    scope = hashlib.sha256(json.dumps([code_sha256(), excluded, config["exclude_text"], aliases], sort_keys=True).encode()).hexdigest()
    src, con = open_source(source_db), open_stats(stats_db)
    try:
        if meta(con, "scope") != scope:
            for table in ("seen", "counts", "tools", "skills"):
                con.execute(f"delete from {table}")  # execute, not executescript: a failed pass must roll this back
        known = dict(con.execute("select session_id,fingerprint from seen"))
        sessions = src.execute(
            "select s.id,s.agent,s.project,s.cwd,s.file_size,s.file_mtime,s.file_hash,s.message_count,"
            "(select count(*) from tool_calls t where t.session_id=s.id) from sessions s where s.deleted_at is null"
        ).fetchall()
        live, changed = set(), 0
        for sid, host, project, cwd, *parts in sessions:
            if host not in HOSTS or any(x in (project or "") or x in (cwd or "") for x in excluded):
                continue
            key = key_of(sid)
            live.add(key)
            fingerprint = normal_sha256("|".join(str(p) for p in (project, *parts)).encode())
            if known.get(key) == fingerprint:
                continue
            forget(con, key)
            counts, tools, skills = count_session(src, sid, key, host, aliases.get(project, project or "unknown"), private)
            con.executemany("insert into counts values(?,?,?,?,?,?)", counts)
            con.executemany("insert into tools values(?,?,?,?,?)", tools)
            con.executemany("insert into skills values(?,?,?,?,?,?)", skills)
            con.execute("insert into seen values(?,?)", (key, fingerprint))
            changed += 1
        for key in set(known) - live:
            forget(con, key)
        latest = src.execute("select max(local_modified_at) from sessions").fetchone()[0] or ""
        now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        set_meta(con, scope=scope, updated_at=now, source_latest=latest)
        con.commit()
        return {"sessions": len(live), "recounted": changed}
    finally:
        src.close()
        con.close()


def week_of(day):
    try:
        date = datetime.date.fromisoformat(day)
    except ValueError:
        return "unknown"
    return (date - datetime.timedelta(days=date.weekday())).isoformat()


def snapshot(stats_db, health=None):
    """What the report shows: counts and the names of tools, skills and projects. Sessions become plain numbers."""
    validation = load_validation()
    con = open_stats(stats_db)
    try:
        grouped, numbers, skills = {}, {}, {}
        for session, day, host, project, cell, calls in con.execute("select session_id,day,host,project,cell,calls from counts"):
            key = (week_of(day), host, project, cell, numbers.setdefault(session, len(numbers)))
            grouped[key] = grouped.get(key, 0) + calls
        for day, host, project, skill, calls in con.execute("select day,host,project,skill,sum(calls) from skills group by 1,2,3,4"):
            key = (week_of(day), host, project, skill)
            skills[key] = skills.get(key, 0) + calls
        since = validation["validated_on"]
        new_tools = con.execute(
            "select host,tool,sum(calls) from tools t where not exists "
            "(select 1 from tools o where o.host=t.host and o.tool=t.tool and o.day<=?) group by 1,2 order by 3 desc", (since,)
        ).fetchall()
        skipped = con.execute(
            f"select host,tool,sum(calls) from tools where tool not in ({','.join('?' * len(TOOLS))}) group by 1,2 order by 3 desc limit 12", TOOLS
        ).fetchall()
        return {
            "classes": CLASSES,
            "cells": validation["cells"],
            "validated_on": since,
            "sample_rows": validation["sample_rows"],
            "rows": [[k[0], k[1], k[2], k[3], v, k[4]] for k, v in sorted(grouped.items())],
            "skills": [[*k, v] for k, v in sorted(skills.items())],
            "new_tools": new_tools,
            "out_of_scope": skipped,
            "health": {
                "updated_at": meta(con, "updated_at"),
                "source_latest": meta(con, "source_latest"),
                "rules_ok": rules_valid(validation),
                "rules_sha256": rules_sha256()[:12],
                **(health or {}),
            },
        }
    finally:
        con.close()


def collector_env(config):
    env = dict(os.environ)
    env.update(
        AGENTSVIEW_DATA_DIR=config["agentsview_data_dir"], AGENTSVIEW_TELEMETRY_ENABLED="0", DO_NOT_TRACK="1",
        AGENTSVIEW_DISABLE_UPDATE_CHECK="1", AGENTSVIEW_NO_DAEMON="1",
    )
    return env


def sync(config):
    """Refresh agentsview's copy once, leaving no process behind. Return what went wrong, or an empty string."""
    binary = config["agentsview_bin"]
    try:
        version = subprocess.run([binary, "--version"], capture_output=True, timeout=30, stdin=subprocess.DEVNULL).stdout.decode(errors="replace")
        if f"v{AGENTSVIEW_VERSION} " not in version + " ":
            return f"expected agentsview {AGENTSVIEW_VERSION}, found: {version.strip()[:40]}"
        Path(config["agentsview_data_dir"]).mkdir(parents=True, exist_ok=True)
        done = subprocess.run([binary, "sync"], env=collector_env(config), capture_output=True, timeout=SYNC_SECONDS, stdin=subprocess.DEVNULL)
        return "" if done.returncode == 0 else f"agentsview sync exited with {done.returncode}"
    except (OSError, subprocess.TimeoutExpired) as exc:
        return str(exc)[:200]


def render(stats_db, target, sync_error=""):
    """Write the report as one self-contained file: no server, and nothing it loads from elsewhere."""
    data = json.dumps(snapshot(stats_db, {"sync_error": sync_error}), ensure_ascii=False).replace("<", "\\u003c")
    page = (HERE / "page.html").read_text(encoding="utf-8").replace("/*DATA*/null", data)
    partial = Path(target).with_suffix(".tmp")
    partial.write_text(page, encoding="utf-8")
    os.replace(partial, target)


def main(argv=None):
    root = HERE.parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", default=str(root / ".mercury" / "state" / "harness-monitor"))
    parser.add_argument("--no-sync", action="store_true", help="report on the data agentsview already has")
    args = parser.parse_args(argv)
    state = Path(args.state_dir)
    config = load_config(state)
    problem, summary = "" if args.no_sync else sync(config), {}
    try:
        summary = update(Path(config["agentsview_data_dir"]) / "sessions.db", state / "stats.db", config)
    except (sqlite3.Error, RuntimeError, OSError, re.error) as exc:
        problem = "; ".join(x for x in (problem, str(exc)[:200]) if x)
    render(state / "stats.db", state / "report.html", problem)
    print(json.dumps({**summary, "report": str(state / "report.html"), "sync_error": problem}, ensure_ascii=False))
    return 1 if problem else 0


if __name__ == "__main__":
    sys.exit(main())
