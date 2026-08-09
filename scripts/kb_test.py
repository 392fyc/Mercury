#!/usr/bin/env python3
"""Smoke tests for scripts/kb.py.

Run: python scripts/kb_test.py

Everything runs against a throwaway vault under the system temp directory, so
the real Mercury_KB is never written to. Nothing here needs Obsidian running —
that is the point of the tool, so the tests must not depend on it either. The
REST path is exercised only for its *unconfigured* behaviour, which is the part
that must stay correct when Obsidian is closed.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

KB = str(Path(__file__).resolve().parent / "kb.py")
PASS = FAIL = 0


def run(args: list[str], vault: str, stdin: str = "",
        env_extra: dict | None = None):
    """Invoke kb.py and return (returncode, stdout, stderr)."""
    env = dict(os.environ)
    env.pop("OBSIDIAN_API_KEY", None)
    env.pop("OBSIDIAN_HOST", None)
    env["MERCURY_KB_DIR"] = vault
    env["PYTHONIOENCODING"] = "utf-8"
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run([sys.executable, KB, *args], input=stdin,
                          env=env, text=True, encoding="utf-8",
                          errors="replace", capture_output=True)
    return proc.returncode, proc.stdout, proc.stderr


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"PASS {name}")
    else:
        FAIL += 1
        print(f"FAIL {name}" + (f"\n     {detail}" if detail else ""))


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="kbtest-") as tmp:
        vault = str(Path(tmp) / "vault")
        (Path(vault) / "sub").mkdir(parents=True)
        (Path(vault) / "sub" / "seed.md").write_text(
            "seed note\n找得到的中文\n", encoding="utf-8")
        outside = Path(tmp) / "OUTSIDE.md"
        outside.write_text("must not be touched\n", encoding="utf-8")

        # --- baseline: the happy paths must work, or every negative test
        # --- below would "pass" for the wrong reason.
        rc, out, _ = run(["status"], vault)
        check("status exits 0 and finds the vault",
              rc == 0 and "1 markdown" in out, f"rc={rc} out={out!r}")

        rc, out, _ = run(["ls"], vault)
        check("ls lists the seeded note", rc == 0 and "sub/seed.md" in out,
              f"rc={rc} out={out!r}")

        rc, out, _ = run(["cat", "sub/seed.md"], vault)
        check("cat round-trips UTF-8", rc == 0 and "找得到的中文" in out,
              f"rc={rc} out={out!r}")

        rc, out, _ = run(["grep", "找得到"], vault)
        check("grep finds CJK", rc == 0 and "sub/seed.md:2" in out,
              f"rc={rc} out={out!r}")

        rc, out, _ = run(["find", "seed"], vault)
        check("find matches by filename", rc == 0 and "sub/seed.md" in out,
              f"rc={rc} out={out!r}")

        # --- write / append land on disk, verified by reading back
        rc, _, _ = run(["write", "notes/new.md"], vault, stdin="# Hi\n汉字\n")
        body = (Path(vault) / "notes" / "new.md").read_text(encoding="utf-8")
        check("write creates parent dirs and writes UTF-8",
              rc == 0 and body == "# Hi\n汉字\n", f"rc={rc} body={body!r}")

        rc, _, _ = run(["append", "notes/new.md"], vault, stdin="more\n")
        body = (Path(vault) / "notes" / "new.md").read_text(encoding="utf-8")
        check("append preserves prior content",
              rc == 0 and body == "# Hi\n汉字\nmore\n", f"rc={rc} body={body!r}")

        rc, _, _ = run(["write", "lf.md"], vault, stdin="a\nb\n")
        raw = (Path(vault) / "lf.md").read_bytes()
        check("writes LF, not CRLF", b"\r\n" not in raw, f"raw={raw!r}")

        # --- containment. Both directions: escapes refused AND a legal path
        # --- still accepted, otherwise a blanket refusal would look correct.
        for bad in ["../OUTSIDE.md", "../../OUTSIDE.md", "sub/../../OUTSIDE.md",
                    "/etc/passwd", "C:/Windows/System32/drivers/etc/hosts"]:
            rc, _, err = run(["write", bad], vault, stdin="PWNED\n")
            check(f"write refuses escape {bad!r} with exit 2",
                  rc == 2 and "outside the vault" in err, f"rc={rc} err={err!r}")
        rc, _, err = run(["cat", "../OUTSIDE.md"], vault)
        check("cat refuses escape with exit 2", rc == 2, f"rc={rc} err={err!r}")
        check("escaped target was never modified",
              outside.read_text(encoding="utf-8") == "must not be touched\n")
        rc, _, _ = run(["write", "legal.md"], vault, stdin="ok\n")
        check("legal path still accepted (guard is not blanket-deny)", rc == 0,
              f"rc={rc}")

        # --- containment through a directory alias. This is the case the
        # --- straightforward `os.walk(followlinks=False)` does NOT cover on
        # --- Windows: measured, a junction inside the vault gets walked
        # --- straight through, so ls/grep returned a file living outside it.
        # --- The fixture must be checked before the assertion is worth
        # --- anything — a mklink that silently fails looks identical to a
        # --- correctly-blocked alias.
        alias = Path(vault) / "aliasdir"
        secret_dir = Path(tmp) / "secret"
        secret_dir.mkdir()
        (secret_dir / "leak.md").write_text("SECRETSTRING\n", encoding="utf-8")
        made = False
        try:
            if os.name == "nt":
                subprocess.run(["cmd", "/c", "mklink", "/J", str(alias),
                                str(secret_dir)], capture_output=True,
                               check=False)
            else:
                os.symlink(secret_dir, alias, target_is_directory=True)
            made = alias.exists() and (alias / "leak.md").exists()
        except OSError:
            made = False
        check("fixture: directory alias was actually created", made,
              "could not create a junction/symlink — the two alias assertions "
              "below would pass vacuously, so treat them as unproven")
        # kb.py guards this twice over — it prunes alias directories during the
        # walk AND re-checks containment on every yielded file. Disabling
        # either one alone leaves these tests green, because the other still
        # holds; only disabling both turns them red. That is defence in depth
        # working as intended, not a weak test — but it does mean a single-layer
        # sabotage is not a valid way to check that these assertions bite.
        if made:
            rc, out, _ = run(["ls"], vault)
            check("ls does not descend into an alias out of the vault",
                  "leak.md" not in out, f"out={out!r}")
            rc, out, _ = run(["grep", "SECRETSTRING"], vault)
            check("grep cannot read through an alias out of the vault",
                  rc == 1 and "SECRETSTRING" not in out,
                  f"rc={rc} out={out!r}")
            rc, out, _ = run(["grep", "seed note"], vault)
            check("in-vault content still found (alias guard is not "
                  "blanket-deny)", rc == 0 and "sub/seed.md" in out,
                  f"rc={rc} out={out!r}")

        # --- write boundary errors are reported, not tracebacked. Writing
        # --- onto an existing directory is the one failure of this class that
        # --- is trivial to construct; disk-full and ACL failures are NOT
        # --- covered here, so those branches remain unexercised.
        (Path(vault) / "adir").mkdir()
        rc, _, err = run(["write", "adir"], vault, stdin="x\n")
        check("write onto a directory reports cleanly, no traceback",
              rc == 1 and "cannot write" in err and "Traceback" not in err,
              f"rc={rc} err={err!r}")

        # --- missing things fail as failures, not as success
        rc, _, _ = run(["cat", "nope.md"], vault)
        check("cat on a missing note exits 1", rc == 1, f"rc={rc}")
        rc, _, _ = run(["grep", "zzz-no-such-string-zzz"], vault)
        check("grep with no hits exits 1", rc == 1, f"rc={rc}")

        # --- unconfigured vault is an environment error, not a crash
        env_no_vault = dict(os.environ)
        env_no_vault.pop("OBSIDIAN_API_KEY", None)
        env_no_vault["MERCURY_KB_DIR"] = str(Path(tmp) / "does-not-exist")
        proc = subprocess.run([sys.executable, KB, "status"], env=env_no_vault,
                              text=True, encoding="utf-8", errors="replace",
                              capture_output=True)
        check("bad vault path exits 2", proc.returncode == 2,
              f"rc={proc.returncode} err={proc.stderr!r}")

        # --- REST subcommands must fail loudly when unconfigured, and must
        # --- say that the filesystem path is unaffected. No Obsidian needed.
        rc, _, err = run(["osearch", "x"], vault)
        check("osearch without a key exits 3 and points at the filesystem path",
              rc == 3 and "Filesystem subcommands" in err,
              f"rc={rc} err={err!r}")
        rc, _, err = run(["osearch", "x"], vault,
                         env_extra={"OBSIDIAN_API_KEY": "dummy",
                                    "OBSIDIAN_HOST": "http://127.0.0.1:9"})
        check("osearch against a dead port exits 3 and names Obsidian",
              rc == 3 and "Is Obsidian running" in err, f"rc={rc} err={err!r}")

        # urlopen honours any scheme it is given, so an OBSIDIAN_HOST of
        # `file://` would quietly become a local file read instead of a
        # network call. Also covers the likelier mistake of omitting http://.
        for bad_host, label in [
                (f"file:///{Path(tmp).as_posix()}/", "file:// scheme"),
                ("localhost:27123", "missing scheme"),
                ("http://", "no host")]:
            rc, out, err = run(["osearch", "x"], vault,
                               env_extra={"OBSIDIAN_API_KEY": "dummy",
                                          "OBSIDIAN_HOST": bad_host})
            check(f"osearch rejects OBSIDIAN_HOST with {label} (exit 3)",
                  rc == 3 and f"invalid {'OBSIDIAN_HOST'}" in err,
                  f"rc={rc} err={err!r} out={out!r}")

        # A wrong port or a proxy answers with HTML. json.loads raises
        # JSONDecodeError, which is NOT an OSError, so main()'s handler does
        # not catch it — without the guard this tracebacks instead of
        # reporting REST trouble. Served from a real socket rather than mocked.
        import http.server
        import threading

        class _Html(http.server.BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802 - stdlib callback name
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b"<html><body>not json</body></html>")
            do_GET = do_POST
            def log_message(self, *a):  # silence per-request logging
                pass

        # A redirect must NOT be followed: urllib resends the Authorization
        # header to the new authority, so a hijacked or mistyped host could
        # harvest OBSIDIAN_API_KEY by answering 302. Asserting on the error
        # alone would be too weak — the real requirement is that the key never
        # reaches the second hop, so the second hop records what it saw.
        got_auth: list = []

        class _Sink(http.server.BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                got_auth.append(self.headers.get("Authorization"))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b"{}")
            do_GET = do_POST
            def log_message(self, *a):
                pass

        sink = http.server.HTTPServer(("127.0.0.1", 0), _Sink)
        threading.Thread(target=sink.serve_forever, daemon=True).start()

        class _Redir(http.server.BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                self.send_response(302)
                self.send_header(
                    "Location", f"http://127.0.0.1:{sink.server_port}/moved")
                self.end_headers()
            do_GET = do_POST
            def log_message(self, *a):
                pass

        redir = http.server.HTTPServer(("127.0.0.1", 0), _Redir)
        threading.Thread(target=redir.serve_forever, daemon=True).start()
        try:
            rc, _, err = run(
                ["osearch", "x"], vault,
                env_extra={"OBSIDIAN_API_KEY": "SECRET-TOKEN",
                           "OBSIDIAN_HOST": f"http://127.0.0.1:{redir.server_port}"})
            check("a redirect is refused rather than followed (exit 3)",
                  rc == 3 and "redirect" in err, f"rc={rc} err={err!r}")
            check("the API key never reached the redirect target",
                  got_auth == [], f"second hop saw: {got_auth!r}")
        finally:
            redir.shutdown()
            sink.shutdown()

        srv = http.server.HTTPServer(("127.0.0.1", 0), _Html)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            rc, _, err = run(
                ["osearch", "x"], vault,
                env_extra={"OBSIDIAN_API_KEY": "dummy",
                           "OBSIDIAN_HOST": f"http://127.0.0.1:{srv.server_port}"})
            check("non-JSON REST body exits 3 instead of tracebacking",
                  rc == 3 and "non-JSON" in err and "Traceback" not in err,
                  f"rc={rc} err={err!r}")
        finally:
            srv.shutdown()

        # A key with an embedded newline would reach http.client as a malformed
        # header and raise ValueError, which is not an OSError.
        rc, _, err = run(["osearch", "x"], vault,
                         env_extra={"OBSIDIAN_API_KEY": "abc\ndef",
                                    "OBSIDIAN_HOST": "http://127.0.0.1:9"})
        check("a key containing a newline exits 3, not a traceback",
              rc == 3 and "newline" in err and "Traceback" not in err,
              f"rc={rc} err={err!r}")

        # rest_call must NOT force a dict: the real /search/simple/ answers
        # with an array, and enforcing dict there broke osearch outright.
        # Only callers needing a mapping check, so a JSON array must be fine
        # for osearch and rejected for status.
        class _Array(http.server.BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'[{"filename":"a.md"}]')
            do_GET = do_POST
            def log_message(self, *a):
                pass

        asrv = http.server.HTTPServer(("127.0.0.1", 0), _Array)
        threading.Thread(target=asrv.serve_forever, daemon=True).start()
        try:
            aenv = {"OBSIDIAN_API_KEY": "dummy",
                    "OBSIDIAN_HOST": f"http://127.0.0.1:{asrv.server_port}"}
            rc, out, err = run(["osearch", "x"], vault, env_extra=aenv)
            check("osearch accepts a JSON array (regression guard)",
                  rc == 0 and "a.md" in out, f"rc={rc} err={err!r}")
            rc, out, _ = run(["status"], vault, env_extra=aenv)
            check("status reports rest unavailable on a non-object body "
                  "instead of AttributeError",
                  rc == 0 and "unavailable" in out and "Traceback" not in out,
                  f"rc={rc} out={out!r}")
        finally:
            asrv.shutdown()

        # The 8 MiB cap had no test until the audit pointed that out: deleting
        # the capped read would have left the guard silently unexercised.
        class _Huge(http.server.BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                chunk = b"x" * (1 << 20)
                for _ in range(9):          # 9 MiB > MAX_REST_BODY
                    try:
                        self.wfile.write(chunk)
                    except OSError:
                        return
            do_GET = do_POST
            def log_message(self, *a):
                pass

        hsrv = http.server.HTTPServer(("127.0.0.1", 0), _Huge)
        threading.Thread(target=hsrv.serve_forever, daemon=True).start()
        try:
            rc, _, err = run(
                ["osearch", "x"], vault,
                env_extra={"OBSIDIAN_API_KEY": "dummy",
                           "OBSIDIAN_HOST": f"http://127.0.0.1:{hsrv.server_port}"})
            check("an oversized REST body is rejected, not buffered whole",
                  rc == 3 and "more than" in err, f"rc={rc} err={err!r}")
        finally:
            hsrv.shutdown()

        # Invalid UTF-8 is the same class of hole one line earlier than the
        # JSON guard: UnicodeDecodeError is a ValueError, so main()'s OSError
        # handler misses it too.
        class _Binary(http.server.BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b"\xff\xfe\x00 not utf-8 at all")
            do_GET = do_POST
            def log_message(self, *a):
                pass

        bsrv = http.server.HTTPServer(("127.0.0.1", 0), _Binary)
        threading.Thread(target=bsrv.serve_forever, daemon=True).start()
        try:
            rc, _, err = run(
                ["osearch", "x"], vault,
                env_extra={"OBSIDIAN_API_KEY": "dummy",
                           "OBSIDIAN_HOST": f"http://127.0.0.1:{bsrv.server_port}"})
            check("non-UTF-8 REST body exits 3 instead of tracebacking",
                  rc == 3 and "not valid UTF-8" in err
                  and "Traceback" not in err, f"rc={rc} err={err!r}")
        finally:
            bsrv.shutdown()

        # --- broken pipe. Needs output long enough that the downstream reader
        # --- closes mid-write; a short listing finishes first and never
        # --- reproduces it. Windows surfaces this as OSError EINVAL rather
        # --- than BrokenPipeError, and the interpreter's exit-time flush
        # --- re-raises it, which is how this first showed up as exit 120.
        #
        # NOTE ON HOW THIS IS MEASURED: an earlier version of this test ran
        # `kb grep needle | head -2` through the shell and asserted on the
        # return code. That assertion was worthless — a shell pipeline reports
        # the *last* command's status, and `head` always exits 0, so it passed
        # no matter what kb.py did. It was caught by deliberately breaking the
        # pipe handling and seeing this test stay green. Close the read end
        # directly instead, so the code under test is what determines the
        # result.
        for i in range(400):
            (Path(vault) / f"bulk{i:03d}.md").write_text(
                f"needle line {i}\n" * 5, encoding="utf-8")
        env = dict(os.environ)
        env.pop("OBSIDIAN_API_KEY", None)
        env["MERCURY_KB_DIR"] = vault
        env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.Popen(
            [sys.executable, KB, "grep", "needle"], env=env, text=True,
            encoding="utf-8", errors="replace",
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        proc.stdout.readline()
        proc.stdout.readline()
        proc.stdout.close()          # reader goes away mid-write
        pipe_err = proc.stderr.read()
        proc.stderr.close()
        pipe_rc = proc.wait()
        check("grep exits 0 when the reader closes early",
              pipe_rc == 0, f"rc={pipe_rc} err={pipe_err!r}")
        check("no 'Exception ignored' trailer on the closed pipe",
              "Exception ignored" not in pipe_err, f"err={pipe_err!r}")
        # COVERAGE, established by sabotage rather than assumed. kb.py guards
        # the pipe twice: emit() maps a failed write to _PipeClosed, and
        # _finish() proactively flushes before returning. Disabling either
        # alone leaves these two checks green; disabling BOTH fails them. So
        # they do bite, but they cannot attribute the behaviour to one layer.
        # Recorded consequence: the emit() path and _is_pipe_error() itself
        # are effectively unexercised here — the flush reaches the failure
        # first on this platform and buffer size. They are kept as defence for
        # orderings that do not reproduce on this machine, not because the
        # green suite demonstrates them.
        rc, out, _ = run(["grep", "needle"], vault)
        check("same grep unpiped still exits 0 and is not truncated",
              rc == 0 and len(out.splitlines()) == 2000,
              f"rc={rc} lines={len(out.splitlines())}")

    print()
    print(f"{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
