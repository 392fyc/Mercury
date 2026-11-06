"""Tests for tools/harness_monitor (Mercury #755). Every log row here is made up."""
import contextlib
import io
import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "harness_monitor"))
import monitor  # noqa: E402

SCHEMA = """
create table sessions(id text, agent text, project text, cwd text, relationship_type text, file_size int, file_mtime int, file_hash text,
  message_count int, deleted_at text, local_modified_at text);
create table messages(id integer primary key, timestamp text);
create table tool_calls(id integer primary key, session_id text, message_id int, tool_use_id text, tool_name text,
  input_json text, file_path text);
"""
SECRET = "SECRET_MARKER_TEXT"


def config(**extra):
    return {"exclude_projects": [], "exclude_text": "", **extra}


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.source = self.dir / "sessions.db"
        self.stats = self.dir / "stats.db"
        self.con = sqlite3.connect(self.source)
        self.addCleanup(self.con.close)
        self.con.executescript(SCHEMA)
        self.con.execute("insert into messages values(1,'2026-11-02T10:00:00Z')")
        self.con.execute("insert into messages values(2,'2026-11-09T10:00:00Z')")

    def session(self, sid="s1", agent="claude", project="alpha", cwd="", size=1, deleted=None, relationship=""):
        self.con.execute("delete from sessions where id=?", (sid,))
        self.con.execute("insert into sessions values(?,?,?,?,?,?,1,'h',1,?,'2026-11-05T00:00:00Z')", (sid, agent, project, cwd, relationship, size, deleted))
        self.con.commit()

    def call(self, sid, use_id, tool, command=None, path="", message=1):
        raw = json.dumps({"command": command}) if command is not None else "{}"
        self.con.execute("insert into tool_calls(session_id,message_id,tool_use_id,tool_name,input_json,file_path) values(?,?,?,?,?,?)", (sid, message, use_id, tool, raw, path))
        self.con.commit()

    def cells(self, host="claude"):
        rows = monitor.snapshot(self.stats)["rows"]
        out = {}
        for _, h, _, cell, n, _, _ in rows:
            if h == host:
                out[cell] = out.get(cell, 0) + n
        return out


class Validation(unittest.TestCase):
    def test_hash_ignores_line_endings(self):
        self.assertEqual(monitor.normal_sha256(b"a\r\nb\r\n"), monitor.normal_sha256(b"a\nb\n"))

    def test_changing_the_tool_list_voids_the_validation(self):
        validation = monitor.load_validation()
        self.assertTrue(monitor.rules_valid(validation))
        validation["tools"] = validation["tools"][:-1]
        self.assertFalse(monitor.rules_valid(validation))

    def test_rules_are_the_validated_rules(self):
        """Changing rules.py invalidates the audit: re-validate, then update validation.json."""
        self.assertEqual(monitor.rules_sha256(), monitor.load_validation()["rules_sha256"])

    def test_every_cell_is_declared(self):
        cells = monitor.load_validation()["cells"]
        for host in monitor.HOSTS:
            self.assertEqual(set(cells[host]), set(monitor.CLASSES))

    def test_rule_examples(self):
        self.assertEqual(monitor.classify("Read", "{}", ".mercury/memory/index.jsonl"), {"M"})
        self.assertEqual(monitor.classify("WebSearch", '{"query":"x"}', ""), {"W"})
        self.assertEqual(monitor.classify("Skill", '{"skill":"x"}', ""), {"S"})
        self.assertEqual(monitor.classify("Bash", json.dumps({"command": "gh issue list"}), ""), {"G"})
        self.assertEqual(monitor.classify("Bash", json.dumps({"command": "bash lane-bus.sh read --lane main"}), ""), {"B"})

    def test_memory_is_split_by_source(self):
        read = lambda path: monitor.classify("Read", "{}", path)  # noqa: E731
        self.assertEqual(read(".sot/memory/projects/x/MEMORY.md"), {"M"})
        self.assertEqual(read("C:/Users/u/.codex/memories/MEMORY.md"), {"N"})
        self.assertEqual(read("C:/Users/u/.codex/memories/rollout_summaries/a.md"), {"N"})

    def test_two_sources_in_one_command_both_count(self):
        command = "cat .mercury/memory/index.jsonl ~/.codex/memories/MEMORY.md"
        self.assertEqual(monitor.classify("Bash", json.dumps({"command": command}), ""), {"M", "N"})

    def test_evidence_files_are_not_memory(self):
        self.assertEqual(monitor.classify("Read", "{}", ".sot/memory/projects/x/lanes/ui/evidence/run.json"), set())
        command = r"$p='D:\\p\\.sot\\memory\\lanes\\ui\\evidence\\a.json'; Get-Content $p"
        self.assertEqual(monitor.classify("PowerShell", json.dumps({"command": command}), ""), set())

    def test_a_memory_edit_is_not_recall(self):
        command = "python -c \"p='.sot/memory/x/MEMORY.md'; t=open(p).read(); open(p,'w').write(t)\""
        self.assertEqual(monitor.classify("Bash", json.dumps({"command": command}), ""), set())


class CodexForms(unittest.TestCase):
    def test_an_inbox_file_is_a_message_not_memory(self):
        labels = monitor.analyse("exec", "Get-Content .mercury/memory/lanes/inbox-dev.md -Tail 5")
        self.assertIn("B", labels)
        self.assertNotIn("M", labels)

    def test_a_verify_list_is_not_recall(self):
        text = "$paths=@('lanes/a/session-checkpoint.md');\nforeach($p in $paths){ & './scripts/codex/sot-memory.ps1' -Action Verify -Path $p };\nGet-Content x"
        self.assertNotIn("M", monitor.analyse("exec", text))

    def test_comparing_bytes_under_a_hash_is_not_recall(self):
        text = "import hashlib\nb=(root/'.sot/memory/x/session-checkpoint.md').read_bytes()\nprint(hashlib.sha256(b).hexdigest())"
        self.assertNotIn("M", monitor.analyse("exec", text))

    def test_reading_text_next_to_a_hash_stays_recall(self):
        text = "import hashlib\nb=(root/'.sot/memory/x/session-checkpoint.md').read_text()\nprint(hashlib.sha256(b.encode()).hexdigest())"
        self.assertIn("M", monitor.analyse("exec", text))

    def test_a_browser_tab_on_an_outside_page_is_web(self):
        for code in ("await tab.goto('https://example.org/a')", "await cua.createBrowserTab('iab', 'https://example.org/')", "await cua.getTab({url:'https://example.org/'})"):
            self.assertIn("W", monitor.analyse("js", code), code)

    def test_a_browser_tab_on_a_local_page_is_not_web(self):
        self.assertNotIn("W", monitor.analyse("js", "await tab.goto('http://localhost:5173/')"))

    def test_a_url_in_text_is_not_web(self):
        self.assertNotIn("W", monitor.analyse("exec", "echo 'see https://example.org/a'"))

    def test_a_request_assigned_to_a_variable_is_web(self):
        self.assertIn("W", monitor.analyse("exec", "$r=Invoke-WebRequest -Uri 'https://example.org/a'"))


class CodexWrapping(unittest.TestCase):
    def test_a_command_inside_a_js_bash_helper_is_read(self):
        code = 'nodeRepl.write((await bash(base+"sed -n 1,9p .mercury/memory/lanes/LANES.md")).stdout)'
        self.assertIn("M", monitor.analyse("exec", code))
        self.assertIn("M", monitor.analyse("js", code))

    def test_printing_a_result_is_not_a_memory_write(self):
        code = "var s=fs.readFileSync('.sot/memory/x/session-checkpoint.md','utf8');nodeRepl.write(s)"
        self.assertIn("M", monitor.analyse("exec", code))

    def test_an_arrow_function_is_not_a_redirect(self):
        code = "await fs.readFile(root+'/.mercury/memory/lanes/LANES.md','utf8').catch(e=>({error:e.code}))"
        self.assertIn("M", monitor.analyse("exec", code))

    def test_a_reassigned_variable_uses_its_last_value(self):
        text = "$p='a/task.md'; Get-Content $p; $p='.sot/memory/x/session-checkpoint.md'; Get-Content $p"
        self.assertIn("M", monitor.analyse("exec", text))

    def test_a_command_inside_an_if_block_is_read(self):
        text = "$p='.sot/memory/x/LANES.md'; if(Test-Path -LiteralPath $p){rg -n gameplay $p}"
        self.assertIn("M", monitor.analyse("exec", text))

    def test_a_byte_read_through_the_memory_base_variable_is_not_recall(self):
        self.assertNotIn("M", monitor.analyse("exec", "let b=await fs.readFile(path.join(memoryBase,r.rel));sha(b)"))

    def test_a_message_about_memory_beside_the_host_store_is_not_project_memory(self):
        text = "$mem='C:/Users/u/.codex/memories/MEMORY.md'; if (Test-Path $mem) { rg -n x $mem } else { 'MEMORY.md not found' }"
        labels = monitor.analyse("exec", text)
        self.assertNotIn("M", labels)

    def test_a_lane_inbox_file_is_not_memory(self):
        self.assertNotIn("M", monitor.analyse("exec", "Get-Content .sot/memory/projects/x/lanes/ui/inbox.md -Tail 5"))

    def test_reading_back_to_compare_a_hash_is_not_recall(self):
        code = "var t=fs.readFileSync('.sot/memory/x/session-checkpoint.md','utf8');if(sha256(t)!==want)throw Error('changed')"
        self.assertNotIn("M", monitor.analyse("exec", code))

    def test_a_direct_read_beside_a_hash_check_stays_recall(self):
        text = "Get-Content .sot/memory/x/session-checkpoint.md; python -c 'assert sha256(a)==b'"
        self.assertIn("M", monitor.analyse("exec", text))

    def test_the_unwrapping_stays_out_of_claude_commands(self):
        command = "$p='a/task.md'; Get-Content $p; $p='.sot/memory/x/session-checkpoint.md'; Get-Content $p"
        for tool in ("Bash", "PowerShell"):
            self.assertNotIn("M", monitor.analyse(tool, command), tool)
        self.assertIn("M", monitor.analyse("js", command))

    def test_decoding_a_byte_read_next_to_a_hash_is_read(self):
        code = "import hashlib;b=(root/'.sot/memory/x/session-checkpoint.md').read_bytes();t=b.decode();print(hashlib.sha256(b))"
        self.assertIn("M", monitor.analyse("exec", code))

    def test_both_stores_in_one_call_keep_project_memory(self):
        text = "$a='C:/Users/u/.codex/memories/MEMORY.md'; if(Test-Path $a){rg x $a}; $b='.sot/memory/x/LANES.md'; if(Test-Path $b){rg x $b}"
        self.assertIn("M", monitor.analyse("exec", text))

    def test_each_way_of_comparing_a_hash_counts_as_a_check(self):
        read = "import hashlib;t=open('.sot/memory/x/session-checkpoint.md').read();"
        for check in ("assert h==w", "raise E", "if ($h -ne $w) { }", "if(h!==w){}", "throw e"):
            self.assertNotIn("M", monitor.analyse("exec", read + check), check)


class Update(Base):
    def test_counts_classes_and_total(self):
        self.session()
        self.call("s1", "a", "Read", path=".mercury/memory/index.jsonl")
        self.call("s1", "b", "WebSearch")
        self.call("s1", "c", "Bash", "ls")
        monitor.update(self.source, self.stats, config())
        self.assertEqual(self.cells(), {"ALL": 3, "M": 1, "W": 1})

    def test_main_sessions_and_subagents_stay_apart(self):
        self.session("s1")
        self.session("s2", relationship="subagent")
        self.session("s3", relationship="continuation")
        for sid in ("s1", "s2", "s3"):
            self.call(sid, "a" + sid, "WebSearch")
        monitor.update(self.source, self.stats, config())
        roles = sorted(r[6] for r in monitor.snapshot(self.stats)["rows"] if r[3] == "W")
        self.assertEqual(roles, ["main", "main", "subagent"])

    def test_skills_carry_the_role(self):
        self.session(relationship="subagent")
        self.call("s1", "a", "Skill")
        monitor.update(self.source, self.stats, config())
        self.assertEqual(monitor.snapshot(self.stats)["skills"][0][5], "subagent")

    def test_counts_from_before_the_split_are_rebuilt(self):
        con = sqlite3.connect(self.stats)
        con.executescript("create table counts(session_id text, day text, host text, project text, cell text, calls integer);"
                          "create table skills(session_id text, day text, host text, project text, skill text, calls integer);"
                          "create table seen(session_id text primary key, fingerprint text not null);"
                          "insert into seen values('old','x');")
        con.close()
        self.session()
        self.call("s1", "a", "WebSearch")
        monitor.update(self.source, self.stats, config())
        self.assertEqual(self.cells()["W"], 1)

    def test_a_half_finished_rebuild_recovers(self):
        con = sqlite3.connect(self.stats)
        con.executescript("create table counts(session_id text, day text, host text, role text, project text, cell text, calls integer);"
                          "create table skills(session_id text, day text, host text, project text, skill text, calls integer);")
        con.close()
        self.session()
        self.call("s1", "a", "Skill")
        monitor.update(self.source, self.stats, config())
        self.assertEqual(self.cells()["S"], 1)

    def test_repeated_tool_use_id_counts_once(self):
        self.session()
        self.call("s1", "a", "WebSearch")
        self.call("s1", "a", "WebSearch")
        monitor.update(self.source, self.stats, config())
        self.assertEqual(self.cells()["W"], 1)

    def test_tool_outside_scope_is_listed_not_counted(self):
        self.session()
        self.call("s1", "a", "SendMessage")
        monitor.update(self.source, self.stats, config())
        self.assertEqual(self.cells(), {})
        self.assertEqual(monitor.snapshot(self.stats)["out_of_scope"], [("claude", "SendMessage", 1)])

    def test_other_hosts_are_ignored(self):
        self.session(agent="cursor-ide")
        self.call("s1", "a", "WebSearch")
        self.assertEqual(monitor.update(self.source, self.stats, config())["sessions"], 0)

    def test_only_changed_sessions_are_recounted(self):
        self.session()
        self.call("s1", "a", "WebSearch")
        self.assertEqual(monitor.update(self.source, self.stats, config())["recounted"], 1)
        self.assertEqual(monitor.update(self.source, self.stats, config())["recounted"], 0)
        self.call("s1", "b", "WebSearch")
        self.assertEqual(monitor.update(self.source, self.stats, config())["recounted"], 1)
        self.assertEqual(self.cells()["W"], 2)

    def test_recount_replaces_rather_than_adds(self):
        self.session()
        self.call("s1", "a", "WebSearch")
        monitor.update(self.source, self.stats, config())
        self.session(size=2)
        monitor.update(self.source, self.stats, config())
        self.assertEqual(self.cells()["W"], 1)

    def test_deleted_session_disappears(self):
        self.session()
        self.call("s1", "a", "WebSearch")
        monitor.update(self.source, self.stats, config())
        self.session(deleted="2026-11-05")
        monitor.update(self.source, self.stats, config())
        self.assertEqual(self.cells(), {})

    def test_excluded_project_and_cwd(self):
        self.session("s1", project="private-one")
        self.session("s2", project="other", cwd="/work/private-one/x")
        self.session("s3", project="alpha")
        for sid in ("s1", "s2", "s3"):
            self.call(sid, "a" + sid, "WebSearch")
        monitor.update(self.source, self.stats, config(exclude_projects=["private-one"]))
        self.assertEqual(self.cells()["W"], 1)
        self.assertNotIn("private-one", json.dumps(monitor.snapshot(self.stats), ensure_ascii=False))

    def test_adding_an_exclusion_removes_what_was_counted(self):
        self.session(project="private-one")
        self.call("s1", "a", "WebSearch")
        monitor.update(self.source, self.stats, config())
        monitor.update(self.source, self.stats, config(exclude_projects=["private-one"]))
        self.assertEqual(self.cells(), {})

    def test_aliases_merge_projects(self):
        self.session("s1", project="odd_name")
        self.session("s2", project="Real")
        self.call("s1", "a", "WebSearch")
        self.call("s2", "b", "WebSearch")
        monitor.update(self.source, self.stats, config(project_aliases={"odd_name": "Real"}))
        self.assertEqual({r[2] for r in monitor.snapshot(self.stats)["rows"]}, {"Real"})

    def test_changing_an_alias_drops_the_old_name(self):
        self.session(project="odd_name")
        self.call("s1", "a", "WebSearch")
        monitor.update(self.source, self.stats, config(project_aliases={"odd_name": "First"}))
        monitor.update(self.source, self.stats, config(project_aliases={"odd_name": "Second"}))
        self.assertEqual({r[2] for r in monitor.snapshot(self.stats)["rows"]}, {"Second"})

    def test_adding_excluded_text_removes_what_was_counted(self):
        self.session()
        self.call("s1", "a", "Bash", "curl https://example.com/passport")
        monitor.update(self.source, self.stats, config())
        self.assertEqual(self.cells()["W"], 1)
        monitor.update(self.source, self.stats, config(exclude_text="passport"))
        self.assertEqual(self.cells(), {})

    def test_renamed_project_is_recounted(self):
        self.session(project="old")
        self.call("s1", "a", "WebSearch")
        monitor.update(self.source, self.stats, config())
        self.session(project="new")
        monitor.update(self.source, self.stats, config())
        self.assertEqual({r[2] for r in monitor.snapshot(self.stats)["rows"]}, {"new"})

    def test_forget_clears_every_table(self):
        self.session()
        self.call("s1", "a", "Skill")
        monitor.update(self.source, self.stats, config())
        self.session(deleted="2026-11-05")
        monitor.update(self.source, self.stats, config())
        con = sqlite3.connect(self.stats)
        self.addCleanup(con.close)
        for table in ("seen", "counts", "tools", "skills"):
            self.assertEqual(con.execute(f"select count(*) from {table}").fetchone()[0], 0, table)

    def test_changed_code_recounts_unchanged_sessions(self):
        self.session()
        self.call("s1", "a", "WebSearch")
        monitor.update(self.source, self.stats, config())
        with mock.patch.object(monitor, "code_sha256", return_value="changed"):
            self.assertEqual(monitor.update(self.source, self.stats, config())["recounted"], 1)

    def test_failed_recount_keeps_the_old_numbers(self):
        self.session()
        self.call("s1", "a", "WebSearch")
        monitor.update(self.source, self.stats, config())
        with mock.patch.object(monitor, "count_session", side_effect=RuntimeError("boom")), self.assertRaises(RuntimeError):
            monitor.update(self.source, self.stats, config(project_aliases={"x": "y"}))
        self.assertEqual(self.cells()["W"], 1)

    def test_excluded_text_skips_the_call(self):
        self.session()
        self.call("s1", "a", "Bash", "curl https://example.com/passport")
        self.call("s1", "b", "WebSearch")
        monitor.update(self.source, self.stats, config(exclude_text="passport"))
        self.assertEqual(self.cells(), {"ALL": 1, "W": 1})

    def test_no_call_text_reaches_stats(self):
        self.session()
        self.call("s1", "a", "Bash", f"cat docs/{SECRET}.md")
        monitor.update(self.source, self.stats, config())
        self.assertNotIn(SECRET.encode(), self.stats.read_bytes())
        self.assertNotIn(b"s1|", self.stats.read_bytes())
        self.assertNotIn(SECRET, json.dumps(monitor.snapshot(self.stats)))

    def test_source_is_not_written(self):
        self.session()
        self.call("s1", "a", "WebSearch")
        before = self.source.read_bytes()
        monitor.update(self.source, self.stats, config())
        self.assertEqual(before, self.source.read_bytes())

    def test_changed_source_schema_is_refused(self):
        self.con.execute("alter table tool_calls rename column input_json to payload")
        self.con.commit()
        with self.assertRaises(RuntimeError):
            monitor.update(self.source, self.stats, config())

    def test_calls_group_by_monday_of_their_week(self):
        self.session()
        self.call("s1", "a", "WebSearch", message=1)
        self.call("s1", "b", "WebSearch", message=2)
        monitor.update(self.source, self.stats, config())
        weeks = {r[0] for r in monitor.snapshot(self.stats)["rows"]}
        self.assertEqual(weeks, {"2026-11-02", "2026-11-09"})

    def test_tool_first_seen_after_validation_is_flagged(self):
        self.session()
        self.call("s1", "a", "BrandNewTool", message=2)
        self.call("s1", "b", "OldTool", message=1)
        monitor.update(self.source, self.stats, config())
        self.assertEqual([r[1] for r in monitor.snapshot(self.stats)["new_tools"]], ["BrandNewTool"])


class Report(Base):
    def report(self, **config_values):
        monitor.update(self.source, self.stats, config(**config_values))
        target = self.dir / "report.html"
        monitor.render(self.stats, target)
        return target.read_text(encoding="utf-8")

    def test_report_is_self_contained(self):
        self.session()
        self.call("s1", "a", "WebSearch")
        page = self.report()
        self.assertNotIn("/*DATA*/null", page)
        self.assertNotRegex(page, r"(src|href)=|fetch\(|https?://(?!www\.w3\.org)")

    def test_a_name_cannot_close_the_script(self):
        self.session(project="</script><img>")
        self.call("s1", "a", "WebSearch")
        self.assertNotIn("</script><img>", self.report())

    def test_no_call_text_or_session_id_in_report(self):
        self.session("session-id-private")
        self.call("session-id-private", "a", "Bash", f"cat docs/{SECRET}.md")
        page = self.report()
        self.assertNotIn(SECRET, page)
        self.assertNotIn("session-id-private", page)

    def test_every_view_reads_a_cell_through_one_status(self):
        """A second reading of `usable` is how the summary and the charts came to disagree about a cell."""
        self.assertEqual((ROOT / "tools" / "harness_monitor" / "page.html").read_text(encoding="utf-8").count(".usable"), 1)

    def test_report_has_no_view_that_mixes_roles(self):
        self.session()
        self.call("s1", "a", "WebSearch")
        page = self.report()
        self.assertIn('<option value="subagent">', page)
        self.assertNotIn('<option value="">全部会话', page)

    def test_failed_sync_is_shown(self):
        self.session()
        monitor.update(self.source, self.stats, config())
        self.assertEqual(monitor.snapshot(self.stats, {"sync_error": "x"})["health"]["sync_error"], "x")


class Main(Base):
    def run_main(self, state):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = monitor.main(["--state-dir", str(state), "--no-sync"])
        return code, json.loads(out.getvalue())

    def write_config(self, state, data_dir):
        state.mkdir(exist_ok=True)
        (state / "config.json").write_text(json.dumps({"agentsview_data_dir": str(data_dir)}), encoding="utf-8")

    def test_report_from_existing_data(self):
        self.session("private-session-id")
        self.call("private-session-id", "a", "WebSearch")
        self.con.close()
        state = self.dir / "state"
        self.write_config(state, self.dir)
        self.assertEqual(self.run_main(state)[0], 0)
        self.assertTrue((state / "report.html").exists())
        self.assertNotIn(b"private-session-id", (state / "stats.db").read_bytes())

    def test_bad_exclude_pattern_is_reported(self):
        state = self.dir / "state"
        state.mkdir()
        (state / "config.json").write_text(json.dumps({"agentsview_data_dir": str(self.dir), "exclude_text": "("}), encoding="utf-8")
        self.con.close()
        code, summary = self.run_main(state)
        self.assertEqual(code, 1)
        self.assertTrue(summary["sync_error"])

    def test_missing_data_still_writes_a_report_that_says_so(self):
        state = self.dir / "state"
        self.write_config(state, self.dir / "nowhere")
        code, summary = self.run_main(state)
        self.assertEqual(code, 1)
        self.assertTrue(summary["sync_error"])
        self.assertIn(summary["sync_error"][:20], (state / "report.html").read_text(encoding="utf-8"))


class Skills(Base):
    def test_names_from_tool_and_path(self):
        self.session()
        self.call("s1", "a", "Bash", "cat .claude/skills/alpha/SKILL.md")
        self.call("s1", "b", "Read", path="/x/.agents/skills/beta/SKILL.md")
        self.con.execute("insert into tool_calls(session_id,message_id,tool_use_id,tool_name,input_json,file_path) values('s1',1,'c','Skill',?,'')", (json.dumps({"skill": "gamma"}),))
        self.con.commit()
        monitor.update(self.source, self.stats, config())
        self.assertEqual({r[3] for r in monitor.snapshot(self.stats)["skills"]}, {"alpha", "beta", "gamma"})


class Reach(Base):
    def test_rows_keep_sessions_apart_without_their_ids(self):
        for sid in ("s1", "s2"):
            self.session(sid)
            self.call(sid, "a" + sid, "WebSearch")
        monitor.update(self.source, self.stats, config())
        rows = [r for r in monitor.snapshot(self.stats)["rows"] if r[3] == "W"]
        self.assertEqual(sorted(r[5] for r in rows), [0, 1])


class Sync(unittest.TestCase):
    def test_missing_binary_is_reported_not_raised(self):
        self.assertTrue(monitor.sync({"agentsview_bin": "no-such-binary-here", "agentsview_data_dir": "."}))


if __name__ == "__main__":
    unittest.main()
