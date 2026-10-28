"""lane-bus.py (#712): seats (one active session per harness and lane, replaced on handoff), messages between them,
the MCP server over stdio, the turn-boundary hook and their limits."""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "packs" / "lanes" / "scripts" / "lane-bus.py"
TAG = "cross-lane message, not user authorization"


class LaneBus(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = Path(tmp.name) / "bus.sqlite"
        self.env = {k: v for k, v in os.environ.items()
                    if k not in ("MERCURY_LANE", "MERCURY_LANE_BUS_DB", "CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID")}

    def run_tool(self, command, harness, stdin="", session=None, lane=None, env=None, db=None):
        args = [sys.executable, "-B", str(TOOL), command, "--harness", harness, "--db", str(db or self.db)]
        if session:
            args += [f"--session={session}"]
        if lane:
            args += ["--lane", lane]
        r = subprocess.run(args, input=stdin, capture_output=True, text=True, encoding="utf-8",
                           env={**self.env, **(env or {})}, timeout=60)
        return r.returncode, r.stdout, r.stderr

    def seat(self, harness, session, lane=None):
        rc, out, err = self.run_tool("claim", harness, session=session, lane=lane)
        self.assertEqual(rc, 0, err)
        return out

    # --- seats and messages through the CLI ---

    def test_active_sessions_exchange_messages(self):
        self.seat("claude", "c-1")
        self.seat("codex", "x-1")
        rc, _, err = self.run_tool("send", "claude", "please review PR 9", session="c-1")
        self.assertEqual(rc, 0, err)
        self.assertIn("No unread", self.run_tool("read", "claude", session="c-1")[1])  # nothing comes back to the sender
        out = self.run_tool("read", "codex", session="x-1")[1]
        self.assertIn(f"[FROM-LANE=main HARNESS=claude SESSION=c-1] {TAG}", out)
        self.assertIn("please review PR 9", out)
        self.assertIn("No unread", self.run_tool("read", "codex", session="x-1")[1])  # read once
        self.assertEqual(self.run_tool("send", "codex", "done, two findings", session="x-1")[0], 0)
        self.assertIn("done, two findings", self.run_tool("read", "claude", session="c-1")[1])

    def test_a_session_without_the_seat_can_neither_read_nor_send(self):
        self.seat("codex", "x-1")
        for command, stdin in (("send", "hi"), ("read", "")):
            with self.subTest(command=command):
                rc, _, err = self.run_tool(command, "claude", stdin, session="c-9")
                self.assertEqual(rc, 2)
                self.assertIn("is not the active claude session of lane main (active: none)", err)
                rc, _, err = self.run_tool(command, "claude", stdin)  # no session at all
                self.assertEqual(rc, 2)
                self.assertIn("not attached to a session", err)

    def test_handoff_retires_the_old_session_and_hands_over_its_unread_messages(self):
        self.seat("claude", "c-old")
        self.seat("codex", "x-1")
        self.run_tool("send", "codex", "message for whoever holds the claude seat", session="x-1")
        out = self.seat("claude", "c-new")  # the session the handoff opened
        self.assertIn("retired c-old", out)
        rc, _, err = self.run_tool("read", "claude", session="c-old")
        self.assertEqual(rc, 2)
        self.assertIn("is not the active claude session of lane main (active: c-new)", err)
        self.assertEqual(self.run_tool("send", "claude", "late", session="c-old")[0], 2)
        self.assertIn("message for whoever holds the claude seat", self.run_tool("read", "claude", session="c-new")[1])
        rc, _, err = self.run_tool("claim", "claude", session="c-old")  # a retired session cannot come back
        self.assertEqual(rc, 2)
        self.assertIn("cannot take the seat back", err)
        self.assertIn("claude=c-new, codex=x-1", self.run_tool("status", "codex")[1])
        self.assertNotIn("retired", self.seat("claude", "c-new"))  # claiming the seat it holds changes nothing

    def test_lanes_are_separate(self):
        self.seat("codex", "x-art", lane="art")
        self.seat("claude", "c-art", lane="art")
        self.seat("claude", "c-guard", lane="guard")
        self.assertEqual(self.run_tool("send", "codex", "art only", session="x-art", lane="art")[0], 0)
        self.assertIn("No unread", self.run_tool("read", "claude", session="c-guard", lane="guard")[1])
        out = self.run_tool("read", "claude", session="c-art", lane="art")[1]
        self.assertIn("[FROM-LANE=art HARNESS=codex SESSION=x-art]", out)
        self.assertEqual(self.run_tool("read", "claude", session="c-art", lane="guard")[0], 2)  # its seat is in art

    def test_the_lane_and_session_come_from_the_environment(self):
        env = {"MERCURY_LANE": "guard", "CODEX_THREAD_ID": "x-env"}
        self.assertEqual(self.run_tool("claim", "codex", env=env)[0], 0)
        self.seat("claude", "c-g", lane="guard")
        self.assertEqual(self.run_tool("send", "codex", "via env", env=env)[0], 0)
        self.assertIn("via env", self.run_tool("read", "claude", session="c-g", lane="guard")[1])

    def test_bad_input_is_refused(self):
        self.seat("claude", "c-1")
        for body in ("", "   \n", "a\x00b", "hidden\u202eflip", "tag\U000E0041x", "c1\x85x", "soft\xadhyphen", "line\u2028sep", "x" * 16001):
            with self.subTest(body=body[:20]):
                rc, _, err = self.run_tool("send", "claude", body, session="c-1")
                self.assertEqual(rc, 2)
                self.assertIn("lane-bus:", err)
        for session in ("bad id", "-x", "a" * 129, "c\n1"):
            with self.subTest(session=session):
                rc, _, err = self.run_tool("claim", "claude", session=session)
                self.assertEqual(rc, 2)
                self.assertIn("invalid session id", err)
        self.assertEqual(self.run_tool("send", "claude", "tab\tand\nnewline ok", session="c-1")[0], 0)
        self.assertEqual(self.run_tool("send", "claude", "emoji \U0001F468\u200d\U0001F4BB ok", session="c-1")[0], 0)
        rc, _, err = self.run_tool("claim", "claude", session="c-2", lane="Bad/Lane")
        self.assertEqual(rc, 2)
        self.assertIn("invalid lane name", err)

    # --- MCP server ---

    def mcp(self, harness, requests, env=None):
        """Run the server over stdio with these requests (dicts or raw lines); return the parsed responses."""
        lines = "".join((r if isinstance(r, str) else json.dumps(r)) + "\n" for r in requests)
        rc, out, err = self.run_tool("serve", harness, lines, env=env)
        self.assertEqual(rc, 0, err)
        return [json.loads(line) for line in out.splitlines() if line.strip()]

    @staticmethod
    def call(mid, name, **arguments):
        return {"jsonrpc": "2.0", "id": mid, "method": "tools/call", "params": {"name": name, "arguments": arguments}}

    @staticmethod
    def text(response):
        return response["result"]["content"][0]["text"]

    def test_mcp_handshake_claim_and_messages(self):
        out = self.mcp("claude", [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            self.call(3, "lane_bus_send", body="before claiming"),
            self.call(4, "lane_bus_claim", session_id="c-1"),
            self.call(5, "lane_bus_send", body="draft ready"),
            self.call(6, "lane_bus_status"),
            {"jsonrpc": "2.0", "id": 7, "method": "nope"},
            "{not json",
        ])
        self.assertEqual([r.get("id") for r in out], [1, 2, 3, 4, 5, 6, 7, None])  # the notification gets no response
        self.assertEqual(out[0]["result"]["capabilities"], {"tools": {}})
        self.assertEqual({t["name"] for t in out[1]["result"]["tools"]},
                         {"lane_bus_claim", "lane_bus_status", "lane_bus_send", "lane_bus_read", "lane_bus_wait"})
        self.assertTrue(out[2]["result"]["isError"])
        self.assertIn("not attached to a session", self.text(out[2]))
        self.assertIn("session c-1 holds the claude seat of lane main", self.text(out[3]))
        self.assertFalse(out[4]["result"]["isError"])
        self.assertIn("claude=c-1, codex=none; this connection: claude c-1", self.text(out[5]))
        self.assertEqual(out[6]["error"]["code"], -32601)
        self.assertEqual(out[7]["error"]["code"], -32700)
        out = self.mcp("codex", [self.call(1, "lane_bus_claim", session_id="x-1"), self.call(2, "lane_bus_read"),
                                 self.call(3, "lane_bus_read")])
        self.assertIn("draft ready", self.text(out[1]))
        self.assertNotIn("before claiming", self.text(out[1]))
        self.assertIn("No unread", self.text(out[2]))

    def test_mcp_attaches_to_the_host_session_only_when_it_holds_the_seat(self):
        self.seat("codex", "x-1")
        self.run_tool("send", "codex", "hello", session="x-1")
        out = self.mcp("claude", [self.call(1, "lane_bus_read")], env={"CLAUDE_CODE_SESSION_ID": "c-1"})
        self.assertTrue(out[0]["result"]["isError"])  # c-1 does not hold the seat: no silent takeover
        self.assertIn("claude=none", self.run_tool("status", "claude")[1])
        self.seat("claude", "c-1")
        out = self.mcp("claude", [self.call(1, "lane_bus_read")], env={"CLAUDE_CODE_SESSION_ID": "c-1"})
        self.assertIn("hello", self.text(out[0]))

    def test_mcp_errors_are_tool_errors(self):
        out = self.mcp("codex", [self.call(1, "lane_bus_claim", session_id="x-1"), self.call(2, "lane_bus_send", body=""),
                                 self.call(3, "lane_bus_wait", timeout_seconds=0), self.call(4, "lane_bus_wait", timeout_seconds=56),
                                 self.call(5, "lane_bus_wait", timeout_seconds="5"), self.call(6, "lane_bus_claim", session_id=""),
                                 self.call(7, "no_such_tool")])
        self.assertFalse(out[0]["result"]["isError"])
        self.assertEqual(len(out), 7)
        for r in out[1:]:
            with self.subTest(id=r["id"]):
                self.assertTrue(r["result"]["isError"])
                self.assertTrue(self.text(r).startswith("lane-bus:"))

    def test_mcp_survives_malformed_requests(self):
        out = self.mcp("codex", [{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": ["x"]},
                                 {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                  "params": {"name": "lane_bus_read", "arguments": [1]}},
                                 {"jsonrpc": "2.0", "id": 3, "method": "tools/list"}])
        self.assertEqual(out[0]["error"]["code"], -32602)
        self.assertTrue(out[1]["result"]["isError"])
        self.assertIn("tools", out[2]["result"])  # still serving

    def test_wait_is_bounded(self):
        self.seat("codex", "x-1")
        start = time.monotonic()
        out = self.mcp("codex", [self.call(1, "lane_bus_claim", session_id="x-1"), self.call(2, "lane_bus_wait", timeout_seconds=1)])
        self.assertIn("No message within 1 s", self.text(out[1]))
        self.assertLess(time.monotonic() - start, 30)

    def test_wait_picks_up_a_message_sent_meanwhile(self):
        self.seat("claude", "c-1")
        args = [sys.executable, "-B", str(TOOL), "serve", "--harness", "codex", "--db", str(self.db)]
        server = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8", env=self.env)
        self.addCleanup(server.kill)
        for r in (self.call(1, "lane_bus_claim", session_id="x-1"), self.call(2, "lane_bus_wait", timeout_seconds=20)):
            server.stdin.write(json.dumps(r) + "\n")
        server.stdin.flush()
        time.sleep(1.5)
        self.assertEqual(self.run_tool("send", "claude", "arrived later", session="c-1")[0], 0)
        out, _ = server.communicate(timeout=30)
        self.assertIn("arrived later", self.text(json.loads(out.splitlines()[1])))

    # --- watch (Claude Code plugin monitor) ---

    def start_watch(self, session):
        args = [sys.executable, "-B", str(TOOL), "watch", "--harness", "claude", "--db", str(self.db),
                f"--session={session}", "--interval", "0.2", "--max-hours", "0.0015"]  # ends by itself after 5.4 s
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", env=self.env)
        self.addCleanup(proc.kill)
        return proc

    def test_watch_announces_new_messages_to_the_seat_holder_only(self):
        self.seat("claude", "c-1")
        self.seat("codex", "x-1")
        holder, other = self.start_watch("c-1"), self.start_watch("c-2")
        time.sleep(1.0)
        self.run_tool("send", "codex", "first", session="x-1")
        time.sleep(1.0)
        self.run_tool("send", "codex", "second", session="x-1")
        out, err = holder.communicate(timeout=30)
        self.assertEqual(holder.returncode, 0, err)
        lines = [line for line in out.splitlines() if line.strip()]
        self.assertEqual(len(lines), 2, out)  # one line per new batch, never the same message twice
        self.assertIn("1 new message(s) for the claude seat of lane main (ids 1)", lines[0])
        self.assertIn("(ids 2)", lines[1])
        self.assertIn("not user authorization", lines[0])
        out, _ = other.communicate(timeout=30)
        self.assertEqual(out.strip(), "")  # a session without the seat hears nothing
        self.assertIn("first", self.run_tool("read", "claude", session="c-1")[1])  # watch marked nothing read

    def test_watch_ends_when_its_seat_passes_to_another_session(self):
        self.seat("claude", "c-1")
        proc = self.start_watch("c-1")
        time.sleep(1.0)
        self.seat("claude", "c-2")  # a handoff: the monitor of the retired session must not run on
        start = time.monotonic()
        proc.communicate(timeout=30)
        self.assertEqual(proc.returncode, 0)
        self.assertLess(time.monotonic() - start, 4)  # well before its own 5.4 s deadline
        log = (self.db.parent / "lane-bus-watch.log").read_text(encoding="utf-8")
        self.assertIn("session=c-1", log)
        self.assertIn("reason=retired", log)

    # --- wait (a session's own background command) ---

    def run_wait(self, session, timeout="10"):
        start = time.monotonic()
        args = [sys.executable, "-B", str(TOOL), "wait", "--harness", "claude", "--db", str(self.db),
                f"--session={session}", "--timeout", timeout]
        return subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                env=self.env), start

    def test_wait_ends_with_0_when_a_message_arrives(self):
        self.seat("claude", "c-1")
        self.seat("codex", "x-1")
        proc, _ = self.run_wait("c-1", "30")
        self.addCleanup(proc.kill)
        time.sleep(1.0)
        self.assertIsNone(proc.poll())  # still waiting
        self.run_tool("send", "codex", "wake up", session="x-1")
        out, _ = proc.communicate(timeout=30)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("1 unread message(s) for the claude seat of lane main (ids 1)", out)
        self.assertIn("wake up", self.run_tool("read", "claude", session="c-1")[1])  # wait marked nothing read

    def test_wait_times_out_with_3_and_refuses_a_non_holder_with_4(self):
        self.seat("claude", "c-1")
        proc, start = self.run_wait("c-1", "1")
        out, _ = proc.communicate(timeout=30)
        self.assertEqual(proc.returncode, 3)
        self.assertIn("no message within 1 s", out)
        proc, _ = self.run_wait("c-9", "30")
        out, _ = proc.communicate(timeout=30)
        self.assertEqual(proc.returncode, 4)
        self.assertIn("does not hold the claude seat", out)

    def test_wait_ends_with_4_when_the_seat_passes_on(self):
        self.seat("claude", "c-1")
        proc, _ = self.run_wait("c-1", "30")
        self.addCleanup(proc.kill)
        time.sleep(1.0)
        self.seat("claude", "c-2")
        proc.communicate(timeout=30)
        self.assertEqual(proc.returncode, 4)
        for bad in ("0", "86401"):
            r = subprocess.run([sys.executable, "-B", str(TOOL), "wait", "--harness", "claude", "--db", str(self.db),
                                "--session=c-2", "--timeout", bad], capture_output=True, text=True, timeout=30, env=self.env)
            self.assertEqual(r.returncode, 2)
            self.assertIn("--timeout must be", r.stderr)

    def test_watch_refuses_bad_bounds(self):
        for extra in (["--interval", "0"], ["--max-hours", "0"], ["--max-hours", "100"]):
            with self.subTest(extra=extra):
                r = subprocess.run([sys.executable, "-B", str(TOOL), "watch", "--harness", "claude", "--db", str(self.db),
                                    "--session=c-1", *extra], capture_output=True, text=True, timeout=30, env=self.env)
                self.assertEqual(r.returncode, 2)
                self.assertIn("--interval must be", r.stderr)

    # --- hook ---

    def hook(self, harness, event, **kw):
        rc, out, _ = self.run_tool("hook", harness, json.dumps(event) if isinstance(event, dict) else event, **kw)
        self.assertEqual(rc, 0)
        return json.loads(out) if out.strip() else None

    def test_hook_speaks_only_to_the_active_session(self):
        self.seat("claude", "c-1")
        self.seat("codex", "x-1")
        self.assertIsNone(self.hook("codex", {"hook_event_name": "UserPromptSubmit", "session_id": "x-1"}))  # nothing unread
        self.run_tool("send", "claude", "ping", session="c-1")
        for name in ("UserPromptSubmit", "SessionStart"):
            out = self.hook("codex", {"hook_event_name": name, "session_id": "x-1"})
            self.assertEqual(out["hookSpecificOutput"]["hookEventName"], name)
            self.assertIn("1 unread lane-bus message(s) for the codex seat of lane main",
                          out["hookSpecificOutput"]["additionalContext"])
        out = self.hook("codex", {"hook_event_name": "Stop", "session_id": "x-1", "stop_hook_active": False})
        self.assertEqual(out["decision"], "block")
        self.assertIn("lane-bus.sh read --harness codex --lane main --session x-1", out["reason"])
        self.assertIsNone(self.hook("codex", {"hook_event_name": "Stop", "session_id": "x-1", "stop_hook_active": True}))
        self.assertIsNone(self.hook("codex", {"hook_event_name": "PreToolUse", "session_id": "x-1"}))
        self.assertIsNone(self.hook("claude", {"hook_event_name": "Stop", "session_id": "c-1"}))  # the sender has nothing unread
        # Another codex session in the same lane is not told about the seat's messages.
        self.assertIsNone(self.hook("codex", {"hook_event_name": "Stop", "session_id": "x-2"}))
        self.assertIsNone(self.hook("codex", {"hook_event_name": "UserPromptSubmit", "session_id": "x-2"}))
        self.assertIn("ping", self.run_tool("read", "codex", session="x-1")[1])  # the hook marked nothing read

    def test_hook_tells_a_new_session_how_to_take_over(self):
        self.seat("claude", "c-old")
        out = self.hook("claude", {"hook_event_name": "SessionStart", "session_id": "c-new"})
        note = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("lane main was assumed; its claude seat is held by c-old", note)
        self.assertIn("lane-bus.sh claim --harness claude --lane main --session c-new", note)
        self.assertIn("never infer", note)  # no lane was named: main is only assumed
        out = self.hook("claude", {"hook_event_name": "SessionStart", "session_id": "c-new"}, lane="main")
        note = out["hookSpecificOutput"]["additionalContext"]
        self.assertNotIn("never infer", note)
        self.assertIn("does not hold the claude seat of lane main (active: c-old)", note)
        self.assertIn("If this session takes over the lane", note)
        out = self.hook("codex", {"hook_event_name": "SessionStart", "session_id": "x-1"})
        self.assertIn("its codex seat is held by none", out["hookSpecificOutput"]["additionalContext"])

    def test_hook_fails_open(self):
        self.assertIsNone(self.hook("codex", "{broken"))
        self.assertIsNone(self.hook("codex", {"hook_event_name": "Stop"}))  # no session id
        missing = self.db.parent / "missing" / "bus.sqlite"
        self.assertIsNone(self.hook("codex", {"hook_event_name": "Stop", "session_id": "x-1"}, db=missing))
        self.assertIsNone(self.hook("codex", {"hook_event_name": "Stop", "session_id": "x-1"}, lane="Bad/Lane"))


class FindBash(unittest.TestCase):
    """#712: on Windows the WSL launcher in the system directory is never the bash that runs the lane scripts."""

    @unittest.skipUnless(os.name == "nt", "Windows PATH resolution")
    def test_the_wsl_launcher_is_skipped(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("lane_bus", TOOL)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel in ("win/System32/bash.exe", "git/cmd/git.exe", "git/bin/bash.exe", "other/bash.exe",
                        "WindowsApps/bash.exe"):
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).write_bytes(b"")
            env = {"SystemRoot": str(root / "win"), "PATHEXT": ".EXE", "MERCURY_LANE_BUS_BASH": "", "CLAUDE_CODE_GIT_BASH_PATH": ""}
            cases = {  # PATH -> expected
                os.pathsep.join([str(root / "win/System32"), str(root / "other")]): root / "other/bash.exe",
                # The Git Bash next to git.exe wins over PATH order, and the WindowsApps alias is never taken (#712).
                os.pathsep.join([str(root / "win/System32"), str(root / "WindowsApps"), str(root / "git/cmd")]): root / "git/bin/bash.exe",
                os.pathsep.join([str(root / "WindowsApps"), str(root / "other")]): root / "other/bash.exe",
                os.pathsep.join([str(root / "win/System32"), str(root / "WindowsApps")]): None,
            }
            for path, expected in cases.items():
                with self.subTest(path=path), unittest.mock.patch.dict(os.environ, {**env, "PATH": path}):
                    found = mod.find_bash()
                    self.assertEqual(None if found is None else Path(found).resolve(),
                                     None if expected is None else expected.resolve())


if __name__ == "__main__":
    unittest.main()
