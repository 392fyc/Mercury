"""scripts/ci/check-test-coverage.py (#686): which tests a workflow runs, on which platform, and what the lists may say.

Each case builds a throw-away Git repository with a few test files and one workflow, then asks the checker.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "ci" / "check-test-coverage.py"


def load():
    spec = importlib.util.spec_from_file_location("check_test_coverage", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CHECK = load()


PULL_REQUEST = "on: pull_request"


def workflow(*steps, runs_on="ubuntu-latest", matrix=None, name="job", on=PULL_REQUEST, job_if=None, job_coe=None, shell=None):
    """A workflow with one job. A step is a run text, or a dict {run, if, coe, shell}. `on` is the full `on:` block."""
    lines = ["name: t", on, "jobs:", f"  {name}:", f"    runs-on: {runs_on}"]
    if job_if is not None:
        lines.append(f"    if: {job_if}")
    if job_coe is not None:
        lines.append(f"    continue-on-error: {job_coe}")
    if matrix:
        lines += ["    strategy:", "      matrix:", f"        os: [{', '.join(matrix)}]"]
    if shell:
        lines += ["    defaults:", "      run:", f"        shell: {shell}"]
    lines.append("    steps:")
    lines.append("      - uses: actions/checkout@v7")
    for n, step in enumerate(steps, 1):
        opts = step if isinstance(step, dict) else {"run": step}
        lines.append(f"      - name: step {n}")
        if opts.get("if") is not None:
            lines.append(f"        if: {opts['if']}")
        if opts.get("coe") is not None:
            lines.append(f"        continue-on-error: {opts['coe']}")
        if opts.get("shell"):
            lines.append(f"        shell: {opts['shell']}")
        lines.append("        run: |")
        lines += ["          " + ln for ln in opts["run"].strip("\n").split("\n")]
    return "\n".join(lines) + "\n"


def on_block(*events, **filters):
    """on_block('pull_request', paths=['docs/**']) -> a mapping `on:` with the filters under pull_request."""
    lines = ["on:"]
    for event in events:
        lines.append(f"  {event}:")
        if event == "pull_request":
            for key, values in filters.items():
                lines.append(f"    {key.replace('_', '-')}: [{', '.join(values)}]")
    return "\n".join(lines)


class Repo:
    def __init__(self, testcase, files, workflows, config=None, newline="\n"):
        tmp = tempfile.TemporaryDirectory()
        testcase.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        subprocess.run(["git", "-C", str(self.root), "init", "-q"], check=True)
        for rel in files:
            self.write(rel, "x\n")
        for name, text in workflows.items():
            self.write(f".github/workflows/{name}", text.replace("\n", newline))
        self.write("scripts/ci/test-coverage.json", json.dumps(config if config is not None else {}))
        subprocess.run(["git", "-C", str(self.root), "add", "-A"], check=True, capture_output=True)

    def write(self, rel, text):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))

    def analyse(self):
        return CHECK.analyse(self.root)

    def run_main(self, *extra):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = CHECK.main(["--root", str(self.root), *extra])
        return code, out.getvalue(), err.getvalue()


class WhatIsATestFile(unittest.TestCase):
    def test_names_that_count_and_names_that_do_not(self):
        for rel in ("tests/test_a.py", "tests/test-a.py", "x/a_test.py", "tests/test_a.sh", "tests/test-a.sh", "x/a-test.sh",
                    "x/a.test.sh", "t/test_a.ps1", "t/test-a.ps1", "t/a.test.ps1", "t/a.Tests.ps1", "p/test/a.test.cjs",
                    "p/a.test.js", "p/a.test.mjs", "p/a.test.ts"):
            with self.subTest(rel=rel):
                self.assertTrue(CHECK.is_test_name(rel))
        for rel in ("tests/dot/pack_paths.py", "tests/helper.py", "scripts/run-tests.sh", "docs/testing.md", "p/test_data.json",
                    "tests/conftest.py", "attest.py"):
            with self.subTest(rel=rel):
                self.assertFalse(CHECK.is_test_name(rel))

    def test_vendored_sources_are_ignored(self):
        self.assertEqual(CHECK.test_files(["sources/up/test_x.py", "tests/test_y.py"]), ["tests/test_y.py"])


class WhatCountsAsRun(unittest.TestCase):
    def covered(self, files, *steps, **kw):
        repo = Repo(self, files, {"w.yml": workflow(*steps, **kw)})
        problems, matrix = repo.analyse()
        return {t: bool(p) for t, p in matrix.items()}, problems

    def test_a_test_that_no_step_names_is_a_problem(self):
        got, problems = self.covered(["tests/test_a.py", "tests/test_b.py"], "python tests/test_a.py")
        self.assertEqual(got, {"tests/test_a.py": True, "tests/test_b.py": False})
        unrun = [p for p in problems if p.startswith("no workflow runs")]  # (the Linux-only gap of test_a is a separate problem)
        self.assertEqual(len(unrun), 1)
        self.assertIn("tests/test_b.py", unrun[0])

    def test_direct_paths_wrappers_and_line_continuations(self):
        files = ["tests/a.test.sh", "tests/b.test.sh", "tests/c.test.sh", "t/d.test.ps1", "tests/test_e.py"]
        got, _ = self.covered(files,
                              "bash tests/a.test.sh",
                              "bash scripts/ci/run-isolated.sh ./tests/b.test.sh",
                              "bash scripts/ci/run-isolated.sh \\\n  tests/c.test.sh",
                              "./t/d.test.ps1",
                              "python -B -m unittest tests.test_e")
        self.assertTrue(all(got.values()), got)

    def test_mentions_in_comments_and_echo_do_not_count(self):
        got, _ = self.covered(["tests/test_a.py", "tests/test_b.py"], "# python tests/test_a.py\necho python tests/test_b.py")
        self.assertEqual(got, {"tests/test_a.py": False, "tests/test_b.py": False})

    def test_discover_covers_the_directory_and_the_pattern(self):
        files = ["tests/tools/test_a.py", "tests/tools/check_b.py", "tests/tools/sub/test_c.py", "tests/other/test_d.py"]
        got, _ = self.covered(files, "python -B -m unittest discover -s tests/tools -p 'test_*.py'")
        self.assertEqual(got["tests/tools/test_a.py"], True)
        self.assertEqual(got["tests/tools/sub/test_c.py"], False)  # unittest does not descend into a directory without __init__.py
        self.assertEqual(got["tests/other/test_d.py"], False)
        got, _ = self.covered(["tests/tools/test_a.py", "tests/tools/test_b.py"], "python -m unittest discover -s tests/tools -p test_a.py")
        self.assertEqual(got, {"tests/tools/test_a.py": True, "tests/tools/test_b.py": False})

    def test_discover_defaults_and_attached_options(self):
        got, _ = self.covered(["test_top.py", "tests/test_a.py"], "python -m unittest discover")
        self.assertEqual(got, {"test_top.py": True, "tests/test_a.py": False})
        got, _ = self.covered(["tests/test_a.py"], "python -m unittest discover -stests -p'test_*.py'")
        self.assertEqual(got, {"tests/test_a.py": True})

    def test_discover_cannot_import_a_file_name_with_a_dash(self):
        got, _ = self.covered(["tests/test-dash.py"], "python -m unittest discover -s tests -p 'test*.py'")
        self.assertEqual(got, {"tests/test-dash.py": False})

    def test_a_narrower_pattern_leaves_the_others_out(self):
        got, _ = self.covered(["tests/test_a.py", "tests/test_b.py"], "python -m unittest discover -s tests -p 'test_a*.py'")
        self.assertEqual(got, {"tests/test_a.py": True, "tests/test_b.py": False})

    def test_working_directory_prefixes_relative_paths(self):
        text = workflow("python test_a.py").replace("        run: |", "        working-directory: tests\n        run: |")
        repo = Repo(self, ["tests/test_a.py"], {"w.yml": text})
        self.assertEqual(repo.analyse()[1], {"tests/test_a.py": ["linux"]})

    def test_node_globs_count_only_for_node(self):
        files = ["pkg/test/a.test.cjs", "pkg/test/sub/b.test.cjs"]
        got, _ = self.covered(files, 'node --test "pkg/test/*.cjs"')
        self.assertEqual(got, {"pkg/test/a.test.cjs": True, "pkg/test/sub/b.test.cjs": False})
        got, _ = self.covered(files, 'ls "pkg/test/*.cjs"')
        self.assertEqual(got, {"pkg/test/a.test.cjs": False, "pkg/test/sub/b.test.cjs": False})

    def test_for_loops_count_when_the_body_uses_the_variable(self):
        files = ["tests/lanes/test-a.sh", "tests/lanes/test-b.sh", "tests/other/test-c.sh"]
        loop = 'for t in tests/lanes/*.sh; do\n  bash scripts/ci/run-isolated.sh "$t"\ndone'
        got, _ = self.covered(files, loop)
        self.assertEqual(got, {"tests/lanes/test-a.sh": True, "tests/lanes/test-b.sh": True, "tests/other/test-c.sh": False})
        unused = 'for t in tests/lanes/*.sh; do\n  echo hello\ndone'
        got, _ = self.covered(files, unused)
        self.assertFalse(any(got.values()), got)
        listed = 'for t in tests/other/test-c.sh \\\n  tests/lanes/test-a.sh; do\n  bash "$t"\ndone'
        got, _ = self.covered(files, listed)
        self.assertEqual(got, {"tests/lanes/test-a.sh": True, "tests/lanes/test-b.sh": False, "tests/other/test-c.sh": True})


class Platforms(unittest.TestCase):
    def matrix(self, files, wf, config=None, newline="\n"):
        repo = Repo(self, files, wf, config, newline)
        return repo.analyse()

    def test_matrix_jobs_run_on_every_listed_os(self):
        wf = {"w.yml": workflow("python tests/test_a.py", runs_on="${{ matrix.os }}", matrix=["ubuntu-latest", "windows-latest"])}
        problems, matrix = self.matrix(["tests/test_a.py"], wf)
        self.assertEqual((problems, matrix), ([], {"tests/test_a.py": ["linux", "windows"]}))

    def test_a_platform_gap_needs_a_reason(self):
        wf = {"w.yml": workflow("python tests/test_a.py")}
        problems, matrix = self.matrix(["tests/test_a.py"], wf)
        self.assertEqual(matrix, {"tests/test_a.py": ["linux"]})
        self.assertEqual(len(problems), 1)
        self.assertIn("runs on linux but not on windows", problems[0])
        config = {"exempt": {}, "not_on": {"windows": {"tests/test_a.py": "needs symlinks, which Git Bash lacks"}}}
        self.assertEqual(self.matrix(["tests/test_a.py"], wf, config)[0], [])

    def test_a_windows_only_test_needs_a_linux_reason(self):
        wf = {"w.yml": workflow("pwsh t/a.test.ps1", runs_on="windows-latest")}
        problems, _ = self.matrix(["t/a.test.ps1"], wf)
        self.assertIn("runs on windows but not on linux", problems[0])

    def test_step_conditions_on_runner_os_narrow_the_platforms(self):
        wf = {"w.yml": workflow({"run": "python tests/test_a.py", "if": "runner.os != 'Windows'"},
                                {"run": "python tests/test_b.py", "if": "${{ runner.os == 'Windows' }}"},
                                runs_on="${{ matrix.os }}", matrix=["ubuntu-latest", "windows-latest"])}
        _, matrix = self.matrix(["tests/test_a.py", "tests/test_b.py"], wf)
        self.assertEqual(matrix, {"tests/test_a.py": ["linux"], "tests/test_b.py": ["windows"]})

    def test_stale_platform_entries_are_errors(self):
        wf = {"w.yml": workflow("python tests/test_a.py", runs_on="${{ matrix.os }}", matrix=["ubuntu-latest", "windows-latest"])}
        config = {"exempt": {}, "not_on": {"windows": {"tests/test_a.py": "was broken on Windows once, long ago"}}}
        problems, _ = self.matrix(["tests/test_a.py"], wf, config)
        self.assertTrue(any("does run on windows; remove it" in p for p in problems), problems)
        config = {"exempt": {}, "not_on": {"windows": {"tests/gone.py": "the file was deleted a while back"}}}
        problems, _ = self.matrix(["tests/test_a.py"], wf, config)
        self.assertTrue(any("not a tracked test file" in p for p in problems), problems)

    def test_crlf_workflows_read_the_same(self):
        wf = {"w.yml": workflow("python -m unittest discover -s tests -p 'test_*.py'", runs_on="windows-latest")}
        _, matrix = self.matrix(["tests/test_a.py"], wf, newline="\r\n")
        self.assertEqual(matrix, {"tests/test_a.py": ["windows"]})


class ExemptionList(unittest.TestCase):
    def test_an_exemption_with_a_reason_passes(self):
        config = {"exempt": {"tests/test_a.py": "needs a GPU the runners do not have"}, "not_on": {}}
        repo = Repo(self, ["tests/test_a.py"], {"w.yml": workflow("echo nothing")}, config)
        self.assertEqual(repo.analyse()[0], [])
        self.assertEqual(repo.run_main()[0], 0)

    def test_an_exemption_that_runs_anyway_is_an_error(self):
        config = {"exempt": {"tests/test_a.py": "needs a GPU the runners do not have"}, "not_on": {}}
        repo = Repo(self, ["tests/test_a.py"], {"w.yml": workflow("python tests/test_a.py")}, config)
        self.assertTrue(any("is run by a workflow" in p for p in repo.analyse()[0]))

    def test_an_exemption_for_a_file_that_is_not_a_test_is_an_error(self):
        config = {"exempt": {"tests/helper.py": "a helper, not a test at all"}, "not_on": {}}
        repo = Repo(self, ["tests/helper.py"], {"w.yml": workflow("echo nothing")}, config)
        self.assertTrue(any("not a tracked test file" in p for p in repo.analyse()[0]))

    def test_a_missing_or_short_reason_is_a_usage_error(self):
        for reason in ("", "short", None, 5):
            with self.subTest(reason=reason):
                config = {"exempt": {"tests/test_a.py": reason}, "not_on": {}}
                repo = Repo(self, ["tests/test_a.py"], {"w.yml": workflow("echo nothing")}, config)
                self.assertEqual(repo.run_main()[0], 2)

    def test_unknown_keys_and_platforms_are_usage_errors(self):
        for config in ({"exempt": {}, "not_on": {}, "other": 1}, {"exempt": {}, "not_on": {"plan9": {}}}, {"exempt": []}):
            with self.subTest(config=config):
                repo = Repo(self, ["tests/test_a.py"], {"w.yml": workflow("python tests/test_a.py")}, config)
                self.assertEqual(repo.run_main()[0], 2)


class StepsThatNeverRunOrNeverFail(unittest.TestCase):
    """F1 (#686): a step counts only if it runs on every pull request and can fail the check."""
    T = "tests/test_a.py"
    RUN = "python tests/test_a.py"

    def result(self, wf, config=None, files=None):
        repo = Repo(self, files or [self.T], {"w.yml": wf}, config)
        problems, matrix = repo.analyse()
        return matrix[self.T], problems

    def assertCounts(self, wf, platforms=("linux",), config=None):
        got, problems = self.result(wf, config)
        self.assertEqual(got, list(platforms), problems)

    def assertDoesNotCount(self, wf, config=None):
        got, problems = self.result(wf, config)
        self.assertEqual(got, [], problems)
        self.assertTrue(any("tests/test_a.py" in p for p in problems), problems)

    # -- `if:` that switches the step or the job off
    def test_step_if_false_never_runs(self):
        for expr in ("false", "${{ false }}", "${{false}}", "False"):
            with self.subTest(expr=expr):
                self.assertDoesNotCount(workflow({"run": self.RUN, "if": expr}))

    def test_job_if_false_never_runs(self):
        for expr in ("false", "${{ false }}"):
            with self.subTest(expr=expr):
                self.assertDoesNotCount(workflow(self.RUN, job_if=expr))

    def test_a_switched_off_step_cannot_be_rescued_by_the_conditional_list(self):
        """`never` and `unknown` both fail to count, so only a listing tells them apart: `never` must stay uncounted."""
        config = {"conditional": {self.T: "listed in the hope that it makes a disabled step count"}}
        for wf in (workflow({"run": self.RUN, "if": "false"}), workflow(self.RUN, job_if="${{ false }}"),
                   workflow({"run": self.RUN, "coe": "true"}), workflow(self.RUN, job_coe="true"),
                   workflow(self.RUN, on="on: push")):
            with self.subTest(wf=wf):
                self.assertEqual(self.result(wf, config)[0], [])

    def test_harmless_conditions_still_count(self):
        for expr in ("true", "${{ true }}", "success()", "always()", "${{ !cancelled() }}"):
            with self.subTest(expr=expr):
                self.assertCounts(workflow({"run": self.RUN, "if": expr}))
                self.assertCounts(workflow(self.RUN, job_if=expr))

    def test_an_if_the_checker_cannot_read_counts_only_when_listed(self):
        wf = workflow({"run": self.RUN, "if": "github.event_name == 'push'"})
        self.assertDoesNotCount(wf)
        _, problems = self.result(wf)
        self.assertTrue(any("cannot evaluate" in p and "'conditional'" in p for p in problems), problems)
        config = {"conditional": {self.T: "the step is gated on the event, and the event is always a pull request here"}}
        self.assertCounts(wf, ("linux~",), config)
        wf_job = workflow(self.RUN, job_if="github.actor != 'dependabot[bot]'")
        self.assertDoesNotCount(wf_job)
        self.assertCounts(wf_job, ("linux~",), config)

    def test_a_conditional_entry_that_adds_nothing_is_stale(self):
        config = {"conditional": {self.T: "no longer needed, the step has no condition any more"}}
        _, problems = self.result(workflow(self.RUN), config)
        self.assertTrue(any("conditional entry adds nothing" in p for p in problems), problems)

    def test_runner_os_conditions_narrow_but_never_hide_a_step(self):
        self.assertCounts(workflow({"run": self.RUN, "if": "runner.os == 'Linux'"}))
        self.assertDoesNotCount(workflow({"run": self.RUN, "if": "runner.os == 'Windows'"}))  # a Linux job: no platform left

    # -- the workflow's own trigger
    def test_workflows_without_a_pull_request_trigger_do_not_count(self):
        for on in ("on: push", "on: schedule", "on: [push, workflow_dispatch]", on_block("push", "schedule"),
                   on_block("workflow_dispatch"), "on: pull_request_target"):
            with self.subTest(on=on):
                self.assertDoesNotCount(workflow(self.RUN, on=on))

    def test_pull_request_triggers_that_cover_every_pull_request_count(self):
        for on in ("on: pull_request", "on: [push, pull_request]", on_block("push", "pull_request"),
                   on_block("pull_request", branches=["develop", "master"]),
                   on_block("pull_request", branches=["master"], types=["opened", "synchronize"]),
                   on_block("pull_request", branches=["'**'"])):
            with self.subTest(on=on):
                self.assertCounts(workflow(self.RUN, on=on))

    def test_filtered_pull_request_triggers_count_only_when_listed(self):
        filtered = (on_block("pull_request", paths=["'docs/**'"]), on_block("pull_request", paths_ignore=["'docs/**'"]),
                    on_block("pull_request", branches=["main"]), on_block("pull_request", types=["opened"]))
        config = {"path_filtered": {self.T: "the test only depends on docs, which is what the filter watches"}}
        for on in filtered:
            with self.subTest(on=on):
                wf = workflow(self.RUN, on=on)
                self.assertDoesNotCount(wf)
                _, problems = self.result(wf)
                self.assertTrue(any("path-filtered workflow" in p and "'path_filtered'" in p for p in problems), problems)
                self.assertCounts(wf, ("linux*",), config)

    def test_a_path_filtered_entry_is_stale_when_an_unfiltered_run_already_covers_the_test(self):
        both = {"a.yml": workflow(self.RUN, runs_on="${{ matrix.os }}", matrix=["ubuntu-latest", "windows-latest"]),
                "b.yml": workflow(self.RUN, on=on_block("pull_request", paths=["'docs/**'"]))}
        config = {"path_filtered": {self.T: "listed although another workflow runs it on every pull request"}}
        problems, matrix = Repo(self, [self.T], both, config).analyse()
        self.assertEqual(matrix[self.T], ["linux", "windows"])
        self.assertTrue(any("path_filtered entry adds nothing" in p for p in problems), problems)
        self.assertEqual(Repo(self, [self.T], both).analyse()[0], [])  # and without the entry all is fine

    def test_the_unfiltered_part_and_the_filtered_part_are_told_apart_per_platform(self):
        wf = {"a.yml": workflow(self.RUN),
              "b.yml": workflow(self.RUN, runs_on="windows-latest", on=on_block("pull_request", paths=["'docs/**'"]))}
        config = {"path_filtered": {self.T: "the Windows run is only worth it when the docs change"}}
        problems, matrix = Repo(self, [self.T], wf, config).analyse()
        self.assertEqual(matrix[self.T], ["linux", "windows*"])
        self.assertEqual(problems, [])

    # -- failures that cannot fail the check
    def test_continue_on_error_true_never_counts(self):
        self.assertDoesNotCount(workflow({"run": self.RUN, "coe": "true"}))
        self.assertDoesNotCount(workflow({"run": self.RUN, "coe": "${{ true }}"}))
        self.assertDoesNotCount(workflow(self.RUN, job_coe="true"))
        self.assertCounts(workflow({"run": self.RUN, "coe": "false"}))
        self.assertCounts(workflow(self.RUN, job_coe="false"))

    def test_continue_on_error_with_an_expression_is_conditional(self):
        wf = workflow({"run": self.RUN, "coe": "${{ matrix.experimental }}"})
        self.assertDoesNotCount(wf)
        self.assertCounts(wf, ("linux~",), {"conditional": {self.T: "experimental is false for every job in this workflow"}})

    def test_commands_that_swallow_a_failure_do_not_count(self):
        for line in ("python tests/test_a.py || true", "python tests/test_a.py || :", "python tests/test_a.py ||true",
                     "python tests/test_a.py || exit 0", "python tests/test_a.py || echo failed",
                     "python tests/test_a.py; true", "python tests/test_a.py ; :"):
            with self.subTest(line=line):
                self.assertDoesNotCount(workflow(line))

    def test_commands_that_handle_a_failure_properly_still_count(self):
        for line in ("python tests/test_a.py", "python tests/test_a.py && echo done", "python tests/test_a.py || exit 1",
                     "python tests/test_a.py || { echo failed; fail=1; }", "python tests/test_a.py; echo after"):
            with self.subTest(line=line):
                self.assertCounts(workflow(line))

    def test_a_swallowed_failure_inside_a_loop_does_not_count(self):
        loop = 'for t in tests/test_a.py; do\n  python "$t" || true\ndone'
        self.assertDoesNotCount(workflow(loop))

    def test_only_the_last_command_counts_where_only_the_last_one_decides(self):
        two = "python tests/test_a.py\npython tests/test_b.py"
        files = [self.T, "tests/test_b.py"]
        # pwsh (the Windows default shell) and `set +e` report only the last command's status
        repo = Repo(self, files, {"w.yml": workflow(two, runs_on="windows-latest")})
        self.assertEqual(repo.analyse()[1], {self.T: [], "tests/test_b.py": ["windows"]})
        repo = Repo(self, files, {"w.yml": workflow(two, runs_on="windows-latest", shell="bash")})
        self.assertEqual(repo.analyse()[1], {self.T: ["windows"], "tests/test_b.py": ["windows"]})
        repo = Repo(self, files, {"w.yml": workflow("set +e\n" + two)})
        self.assertEqual(repo.analyse()[1], {self.T: [], "tests/test_b.py": ["linux"]})
        repo = Repo(self, files, {"w.yml": workflow({"run": two, "shell": "pwsh"})})
        self.assertEqual(repo.analyse()[1], {self.T: [], "tests/test_b.py": ["linux"]})
        repo = Repo(self, files, {"w.yml": workflow(two)})  # bash on Linux: -e stops at the first failure, so both count
        self.assertEqual(repo.analyse()[1], {self.T: ["linux"], "tests/test_b.py": ["linux"]})


class OnlyExecutorsRunATest(unittest.TestCase):
    """F2 (#686): naming a test path is not running it."""

    def covered(self, files, *steps, **kw):
        repo = Repo(self, files, {"w.yml": workflow(*steps, **kw)})
        return {t: bool(p) for t, p in repo.analyse()[1].items()}

    def test_commands_that_only_mention_a_path_do_not_count(self):
        files = ["tests/test-x.sh"]
        for line in ("cat tests/test-x.sh", "echo tests/test-x.sh", "ls tests/test-x.sh", "git add tests/test-x.sh", "chmod +x tests/test-x.sh",
                     "shellcheck tests/test-x.sh", "grep -n foo tests/test-x.sh", "cp tests/test-x.sh /tmp/x.sh", "wc -l tests/test-x.sh",
                     "bash -c 'cat tests/test-x.sh'", "python -c 'print(1)' tests/test-x.sh"):
            with self.subTest(line=line):
                self.assertEqual(self.covered(files, line), {"tests/test-x.sh": False})

    def test_executors_count(self):
        cases = {
            "tests/test-x.sh": ["bash tests/test-x.sh", "bash -e tests/test-x.sh", "sh tests/test-x.sh", "./tests/test-x.sh", "tests/test-x.sh",
                           "bash scripts/ci/run-isolated.sh tests/test-x.sh", "scripts/ci/run-isolated.sh tests/test-x.sh",
                           "bash scripts/ci/run-isolated.sh -- bash tests/test-x.sh"],
            "tests/test_y.py": ["python tests/test_y.py", "python3 -B tests/test_y.py", "python -B -I -X utf8 tests/test_y.py",
                                "python -m unittest tests.test_y", "python -m pytest tests/test_y.py",
                                "bash scripts/ci/run-isolated.sh -- python -B tests/test_y.py"],
            "tests/z.test.ps1": ["pwsh -NoProfile -File tests/z.test.ps1", "pwsh -File ./tests/z.test.ps1", "pwsh tests/z.test.ps1",
                                 "powershell -ExecutionPolicy Bypass -File tests/z.test.ps1",
                                 "pwsh -ExecutionPolicy Bypass tests/z.test.ps1"],
            "pkg/a.test.cjs": ["node pkg/a.test.cjs", "node --test pkg/a.test.cjs", "node --test 'pkg/*.cjs'",
                               "bash scripts/ci/run-isolated.sh pkg/a.test.cjs"],
        }
        for test, lines in cases.items():
            for line in lines:
                with self.subTest(line=line):
                    self.assertEqual(self.covered([test], line), {test: True})

    def test_the_wrong_argument_does_not_count(self):
        self.assertEqual(self.covered(["tests/test-x.sh", "tests/test-y.sh"], "bash tests/test-y.sh tests/test-x.sh"),
                         {"tests/test-x.sh": False, "tests/test-y.sh": True})  # the second path is only an argument of y.sh
        self.assertEqual(self.covered(["tests/test-x.sh"], "bash scripts/ci/run-isolated.sh -- echo tests/test-x.sh"), {"tests/test-x.sh": False})
        self.assertEqual(self.covered(["tests/test-x.sh"], "FOO=1 bash tests/test-x.sh"), {"tests/test-x.sh": True})

    def test_a_loop_counts_only_when_the_variable_reaches_an_executor(self):
        files = ["tests/test-a.sh", "tests/test-b.sh"]
        none = {"tests/test-a.sh": False, "tests/test-b.sh": False}
        both = {"tests/test-a.sh": True, "tests/test-b.sh": True}
        self.assertEqual(self.covered(files, 'for t in tests/*.sh; do echo "$t"; done'), none)
        self.assertEqual(self.covered(files, 'for t in tests/*.sh; do\n  cat $t\ndone'), none)
        self.assertEqual(self.covered(files, 'for t in tests/*.sh; do\n  ls "${t}"\n  wc -l $t\ndone'), none)
        self.assertEqual(self.covered(files, 'for t in tests/*.sh; do\n  echo bash\n  true "$t"\ndone'), none)
        self.assertEqual(self.covered(files, 'for t in tests/*.sh; do\n  bash "$t"\ndone'), both)
        self.assertEqual(self.covered(files, 'for t in tests/*.sh; do bash scripts/ci/run-isolated.sh "$t"; done'), both)
        self.assertEqual(self.covered(files, 'for t in tests/test-a.sh; do\n  echo "$t"\n  bash "$t"\ndone'),
                         {"tests/test-a.sh": True, "tests/test-b.sh": False})


class ExitStatus(unittest.TestCase):
    def test_zero_one_and_two(self):
        ok = Repo(self, ["tests/test_a.py"], {"w.yml": workflow("python tests/test_a.py", runs_on="${{ matrix.os }}",
                                                                matrix=["ubuntu-latest", "windows-latest"])})
        code, out, err = ok.run_main("--matrix")
        self.assertEqual(code, 0, err)
        self.assertIn("tests/test_a.py", out)
        self.assertIn("linux,windows", out)
        miss = Repo(self, ["tests/test_a.py"], {"w.yml": workflow("echo hi")})
        code, _, err = miss.run_main()
        self.assertEqual(code, 1)
        self.assertIn("tests/test_a.py", err)
        broken = Repo(self, ["tests/test_a.py"], {"w.yml": "jobs: [not a mapping]\n"})
        self.assertEqual(broken.run_main()[0], 2)


class Yaml(unittest.TestCase):
    def test_the_reader_handles_the_constructs_workflows_use(self):
        doc = CHECK.parse_yaml(
            "name: n  # a comment\n"
            "on:\n  push:\n    branches: [develop, 'main']\n"
            "jobs:\n"
            "  a:\n    name: 'Quoted: name'\n    runs-on: ${{ matrix.os }}\n"
            "    steps:\n"
            "      - uses: actions/checkout@v7\n        with:\n          submodules: true\n"
            "      - name: multi\n        run: |\n          echo one\n          # kept\n\n          echo two\n"
            "      - run: >-\n          folded\n          text\n"
            "      - run: echo \"a: b\"\n")
        self.assertEqual(doc["on"]["push"]["branches"], ["develop", "main"])
        job = doc["jobs"]["a"]
        self.assertEqual(job["name"], "Quoted: name")
        steps = job["steps"]
        self.assertEqual(steps[0]["with"], {"submodules": "true"})
        self.assertEqual(steps[1]["run"], "echo one\n# kept\n\necho two\n")
        self.assertEqual(steps[2]["run"], "folded text\n")
        self.assertEqual(steps[3]["run"], 'echo "a: b"')

    def test_what_it_cannot_read_is_an_error_not_a_guess(self):
        for bad in ("a: {b: 1}\n", "a: [1, 2\n", "a: 'open\n", "a:\n\tb: 1\n"):
            with self.subTest(bad=bad):
                with self.assertRaises(CHECK.CoverageError):
                    CHECK.parse_yaml(bad)

    def test_every_workflow_of_this_repository_parses(self):
        names = {}
        for path in sorted((ROOT / ".github/workflows").glob("*.yml")):
            doc = CHECK.parse_yaml(path.read_text(encoding="utf-8"))
            self.assertIsInstance(doc["jobs"], dict, path.name)
            names[path.name] = list(doc["jobs"])
        self.assertIn("template-contract", names["auto-verify.yml"])
        self.assertIn("test-coverage", names["auto-verify.yml"])


class ThisRepository(unittest.TestCase):
    def test_every_test_file_is_run_or_listed(self):
        problems, matrix = CHECK.analyse(ROOT)
        self.assertEqual(problems, [])
        self.assertIn("tests/ci/test_check_test_coverage.py", matrix)  # this file is itself run by a workflow
        self.assertTrue(all(matrix.values()), [t for t, p in matrix.items() if not p])


if __name__ == "__main__":
    unittest.main()
