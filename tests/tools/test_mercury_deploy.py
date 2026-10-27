"""The `deploy` pack (#688): param validation, workflow rendering, YAML read-back, deletion scope, the ignore check.

The pack under test is the real packs/deploy, installed from a throw-away Mercury repository (a minimal core plus a copy of
the deploy pack), so what is checked is exactly what a downstream project receives. No network."""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "mercury.py"
LOCK, CONFIG = ".mercury/lock.json", ".mercury/config.json"
UPDATE, VERIFY = ".github/workflows/mercury-update.yml", ".github/workflows/mercury-verify.yml"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


TOOLMOD = load("mercury_tool_deploy", TOOL)
UPDATER = load("mercury_update_for_deploy_tests", ROOT / "tools" / "mercury_update.py")
CHECKER = load("coverage_checker_for_deploy_tests", ROOT / "scripts" / "ci" / "check-test-coverage.py")  # its YAML reader


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))


def git(repo, *args, check=True):
    r = subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                       capture_output=True, text=True, encoding="utf-8")
    if check:
        assert r.returncode == 0, r.stderr
    return r.stdout


PARAMS = {"integration_branch": "develop", "required_checks": ["Godot headless 回归", "Repository contract"],
          "update_schedule": "17 3 * * *"}


class Params(unittest.TestCase):
    def check(self, params):
        return TOOLMOD.load_params(params)

    def refuses(self, params, text=None):
        with self.assertRaises(TOOLMOD.BuildError) as cm:
            self.check(params)
        if text:
            self.assertIn(text, str(cm.exception))

    def test_valid_params_are_accepted_unchanged(self):
        self.assertEqual(self.check(PARAMS), PARAMS)
        self.assertEqual(self.check({"integration_branch": "release/1.x-beta_2"}), {"integration_branch": "release/1.x-beta_2"})
        self.assertEqual(self.check({"required_checks": []}), {"required_checks": []})

    def test_branch_names_follow_git_rules(self):
        for bad in ("", " main", "main ", "-x", "a..b", "a//b", "a/", "/a", ".hidden", "a/.b", "a.", "x.lock", "a/b.lock", "a b",
                    "a~1", "a^", "a:b", "a?b", "a*b", "a[b", "a\\b", "a@{b", "a\nb", "x" * 101, "mercury/update-abc", 5, None, ["main"]):
            with self.subTest(bad=bad):
                self.refuses({"integration_branch": bad}, "integration_branch")

    def test_branch_rules_agree_with_git_check_ref_format(self):
        """Every name this tool accepts must be a valid branch name for git itself, and the pseudo refs and the names that
        would collide with update branches are refused even though git accepts some of them."""
        names = ["develop", "main", "master", "release/1.x-beta_2", "a", "a.b", "a-b", "feature/x/y", "v1.0", "HEAD", "FETCH_HEAD",
                 "mercury", "mercury/x", "refs/heads/x", "-x", "a..b", "a//b", "a/", ".a", "a/.b", "a.lock", "a/b.lock", "a.", "@",
                 "a@{b", "a b", "a~b", "a^b", "a:b", "a?b", "a*b", "a[b", "a\\b", "x" * 100, "x" * 101]
        for name in names:
            ours = True
            try:
                TOOLMOD.branch_name(name, "x")
            except TOOLMOD.BuildError:
                ours = False
            r = subprocess.run(["git", "check-ref-format", "--branch", name], capture_output=True, text=True)
            git_ok = r.returncode == 0 and r.stdout.strip() == name
            with self.subTest(name=name):
                if ours:
                    self.assertTrue(git_ok, f"accepted by this tool but not by git: {name!r}")
        for name in ("HEAD", "FETCH_HEAD", "ORIG_HEAD", "MERGE_HEAD", "CHERRY_PICK_HEAD", "mercury", "refs/heads/x", "refs/x",
                     "head", "Head", "fetch_head", "Mercury", "MERCURY", "MERCURY/x", "Mercury/update-abc", "Refs/heads/x", "REFS/x"):
            with self.subTest(refused=name), self.assertRaises(TOOLMOD.BuildError):
                TOOLMOD.branch_name(name, "x")

    def test_check_names_are_one_plain_line_and_never_an_expression(self):
        invisible = ["\u061c", "\u180e", *map(chr, range(0x200B, 0x2010)), *map(chr, range(0x2060, 0x2065)), "\u00ad", "\u034f",
                     "\u115f", "\u1160", "\u3164", "\uffa0", "\u2800", "\u00a0", "\u2003", "\u3000", "\ufe0f", "\ufeff"]
        for bad in ("", " x", "x ", "a\nb", "a\rb", "a\tb", "a\x00b", "a\x7fb", "a\u2028b", "a\u202eb", "x" * 101, "${{ secrets.X }}",
                    "pre ${{ x", 5, None, *[f"CI{c}x" for c in invisible], *[c + "CI" for c in invisible[:3]]):
            with self.subTest(bad=bad):
                self.refuses({"required_checks": [bad]}, "required_checks")
        # Two names that look the same must not both pass: the invisible one is refused, the plain one is fine.
        self.check({"required_checks": ["CI"]})
        self.refuses({"required_checks": ["CI", "C\u200bI"]}, "required_checks")
        self.refuses({"required_checks": "x"})
        self.refuses({"required_checks": ["a", "a"]}, "duplicate")
        self.refuses({"required_checks": [f"c{i}" for i in range(21)]})
        # Characters that are special in YAML or shells are fine as data: they are escaped when rendered.
        self.check({"required_checks": ['quote " and \\ backslash', "colon: x # not a comment", "'single'", "{{ braces }}", "$HOME", "`tick`"]})

    def test_cron_is_five_fields_in_range(self):
        for good in ("0 0 * * *", "*/15 * * * *", "5,35 1-5 1,15 */2 0-6", "59 23 31 12 6"):
            self.assertEqual(self.check({"update_schedule": good}), {"update_schedule": good})
        for bad in ("", "* * * *", "* * * * * *", "60 * * * *", "* 24 * * *", "* * 0 * *", "* * 32 * *", "* * * 13 *", "* * * * 7",
                    "*/0 * * * *", "5-1 * * * *", "a * * * *", "@daily", "0  0 * * *", " 0 0 * * *", "0 0 * * *\n", "MON * * * *", "1.5 * * * *",
                    "0 0 * * * # x", 5, None):
            with self.subTest(bad=bad):
                self.refuses({"update_schedule": bad}, "update_schedule")

    def accepted_name(self, name):
        try:
            TOOLMOD.check_names([name], "x")
            return True
        except TOOLMOD.BuildError:
            return False

    def test_every_code_point_is_judged_by_its_unicode_category(self):
        """Walk all 1,114,112 code points. Nothing that Unicode calls a control, format, surrogate, private-use, unassigned,
        line or paragraph separator, enclosing mark, or a space other than U+0020 may be inside a check name; and no letter,
        number, punctuation or symbol is refused unless it is one of the listed invisible fillers and selectors."""
        refused = {"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp", "Me"}
        wrongly_accepted, wrongly_refused = [], []
        for cp in range(0x110000):
            ch = chr(cp)
            cat = unicodedata.category(ch)
            accepted = self.accepted_name("CI" + ch + "x")
            if (cat in refused or (cat == "Zs" and ch != " ")) and accepted:
                wrongly_accepted.append(f"U+{cp:04X} {cat}")
            if cat[0] in "LNPS" and not accepted and ch not in TOOLMOD.INVISIBLE_EXTRA:
                wrongly_refused.append(f"U+{cp:04X} {cat}")
        self.assertEqual(wrongly_accepted[:10], [])
        self.assertEqual(wrongly_refused[:10], [])
        self.assertTrue(self.accepted_name("CI x"))  # the ordinary space is fine inside a name

    def test_the_characters_a_review_found_are_refused(self):
        found = [0x0600, 0x0601, 0x0602, 0x0603, 0x0604, 0x0605, 0x110BD, 0x1D173, 0x1BCA0, *range(0xE0000, 0xE0080)]
        for cp in found:
            with self.subTest(cp=hex(cp)):
                self.assertFalse(self.accepted_name("CI" + chr(cp)))
                self.assertFalse(self.accepted_name(chr(cp) + "CI"))
        for cp in (0x034F, 0x115F, 0x1160, 0x17B4, 0x17B5, 0x3164, 0xFFA0, 0x2800, 0x180B, 0x180F, 0xFE0F, 0xE0100):
            with self.subTest(filler=hex(cp)):
                self.assertFalse(self.accepted_name("CI" + chr(cp)))

    def test_look_alike_letters_are_not_detected(self):
        """A documented limit, pinned here: a Cyrillic capital I (U+0406) looks like the Latin I and is an ordinary letter, so a
        name with it passes. Project required_checks must be reviewed like code, because a name can be claimed by a status."""
        self.assertTrue(self.accepted_name("CI"))
        self.assertTrue(self.accepted_name("C" + chr(0x0406)))
        self.assertNotEqual("CI", "C" + chr(0x0406))

    def test_unknown_keys_and_the_old_param_still_behave(self):
        self.refuses({"other": 1}, "may only hold")
        self.refuses(["x"], "may only hold")
        self.assertEqual(self.check({"guard_write_scripts": ["a.sh"]}), {"guard_write_scripts": ["a.sh"]})


class DeployInstall(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.src, self.dst = Path(tmp.name) / "src", Path(tmp.name) / "dst"
        for repo in (self.src, self.dst):
            repo.mkdir(parents=True)
            git(repo, "init", "-q")
            git(repo, "config", "core.autocrlf", "false")
        git(self.src, "remote", "add", "origin", "https://github.com/test/mercury.git")
        write(self.src / "mercury.json", json.dumps({"schema_version": 1, "packs": {
            "core": {"enabled": True, "required": True}, "deploy": {"enabled": True}}}))
        write(self.src / "packs/core/pack.json", json.dumps({"schema_version": 1, "name": "core", "skills": {}}))
        write(self.src / "packs/core/agents.md", "## Core\n\nCore text.\n")
        write(self.src / "packs/core/claude.md", "@AGENTS.md\n")
        shutil.copytree(ROOT / "packs" / "deploy", self.src / "packs" / "deploy")
        git(self.src, "add", "-A")
        git(self.src, "commit", "-q", "-m", "one")
        git(self.src, "update-ref", "refs/remotes/origin/master", "HEAD")
        self.set_config(["core", "deploy"], PARAMS)

    def set_config(self, packs, params=None):
        cfg = {"schema_version": 1, "packs": packs}
        if params is not None:
            cfg["params"] = params
        write(self.dst / CONFIG, json.dumps(cfg, indent=2) + "\n")

    def tool(self, mode="apply", *extra):
        r = subprocess.run([sys.executable, "-B", str(TOOL), mode, "--source", str(self.src), "--target", str(self.dst), *extra],
                           capture_output=True, text=True, encoding="utf-8")
        return r.returncode, r.stdout + r.stderr

    def yaml(self, rel):
        return CHECKER.parse_yaml((self.dst / rel).read_text(encoding="utf-8"))

    @staticmethod
    def evaluate(expression, event, ref):
        """Evaluate a workflow `if:` made only of github.event_name, github.ref_name, string literals, ==, != , && and ||.
        GitHub compares strings ignoring case, so the expression and the values are folded to lower case first."""
        allowed = r"(?:github\.event_name|github\.ref_name|==|!=|&&|\|\||'[A-Za-z0-9._/-]*'|\s)+"
        assert re.fullmatch(allowed, expression), f"unexpected syntax in {expression!r}"
        text = expression.lower().replace("&&", " and ").replace("||", " or ")
        text = text.replace("github.event_name", repr(event.lower())).replace("github.ref_name", repr(ref.lower()))
        return eval(text, {"__builtins__": {}}, {})

    def required_checks(self):
        """The rendered MERCURY_REQUIRED_CHECKS value: a YAML double-quoted scalar (= a JSON string) holding a JSON list."""
        line = next(l for l in (self.dst / UPDATE).read_text(encoding="utf-8").splitlines()
                    if l.strip().startswith("MERCURY_REQUIRED_CHECKS:"))
        return json.loads(json.loads(line.split(":", 1)[1].strip()))

    def lock(self):
        return json.loads((self.dst / LOCK).read_text(encoding="utf-8"))

    # --- rendering, read back with a YAML reader ---

    def test_install_writes_both_workflows_and_records_them_in_the_lock(self):
        code, out = self.tool()
        self.assertEqual(code, 0, out)
        for rel in (UPDATE, VERIFY):
            self.assertTrue((self.dst / rel).is_file())
            self.assertIn(rel, self.lock()["files"])
        self.assertEqual(self.tool("check")[0], 0)
        before = (self.dst / UPDATE).read_bytes()
        self.assertEqual(self.tool()[0], 0)
        self.assertEqual((self.dst / UPDATE).read_bytes(), before)

    def test_update_workflow_reads_back_with_the_params_and_the_required_shape(self):
        self.assertEqual(self.tool()[0], 0)
        doc = self.yaml(UPDATE)
        self.assertEqual(doc["on"]["schedule"], [{"cron": "17 3 * * *"}])
        self.assertIn("workflow_dispatch", doc["on"])
        self.assertEqual(doc["permissions"], {"contents": "read"})
        self.assertIn("group", doc["concurrency"])
        job = doc["jobs"]["update"]
        self.assertIn("timeout-minutes", job)
        self.assertEqual(job["if"], "github.event_name != 'schedule' || github.ref_name == 'develop'")
        self.assertEqual(job["env"]["MERCURY_BASE"], "develop")
        steps = job["steps"]
        token = next(s for s in steps if str(s.get("uses", "")).startswith("actions/create-github-app-token@"))
        self.assertEqual(token["uses"], "actions/create-github-app-token@v3")
        self.assertEqual(token["with"], {
            "client-id": "${{ vars.MERCURY_APP_CLIENT_ID }}", "private-key": "${{ secrets.MERCURY_APP_PRIVATE_KEY }}",
            "permission-contents": "write", "permission-pull-requests": "write", "permission-workflows": "write",
            "permission-checks": "read", "permission-statuses": "read"})
        checkouts = [s for s in steps if str(s.get("uses", "")).startswith("actions/checkout@")]
        self.assertEqual([c["uses"] for c in checkouts], ["actions/checkout@v7"] * 2)
        mercury, mine = checkouts
        self.assertEqual((mine["with"]["ref"], mine["with"]["path"]), ("develop", "target"))
        self.assertEqual(mine["with"]["token"], "${{ steps.app-token.outputs.token }}")
        self.assertEqual((mercury["with"]["ref"], mercury["with"]["persist-credentials"]), ("master", "false"))  # the checker's reader keeps scalars as text
        self.assertTrue(any(s.get("uses") == "actions/setup-python@v7" for s in steps))
        runs = [s["run"] for s in steps if "run" in s]
        self.assertEqual([r.split()[3] for r in runs], ["publish", "merge"])
        self.assertEqual(self.required_checks(), PARAMS["required_checks"])

    def test_the_app_token_is_created_late_and_only_in_the_update_job(self):
        self.assertEqual(self.tool()[0], 0)
        steps = self.yaml(UPDATE)["jobs"]["update"]["steps"]
        uses = [str(s.get("uses", "")) for s in steps]
        token = next(i for i, u in enumerate(uses) if u.startswith("actions/create-github-app-token@"))
        self.assertLess(next(i for i, u in enumerate(uses) if u.startswith("actions/setup-python@")), token)  # nothing slow runs once it exists
        self.assertLess(next(i for i, s in enumerate(steps) if s.get("with", {}).get("repository") == "392fyc/Mercury"), token)
        self.assertEqual(token, next(i for i, s in enumerate(steps) if s.get("with", {}).get("path") == "target") - 1)  # right before first use
        text = (self.dst / UPDATE).read_text(encoding="utf-8")
        before_update_job = text.split("\n  update:\n")[0]
        for needle in ("MERCURY_APP_PRIVATE_KEY", "MERCURY_APP_CLIENT_ID", "create-github-app-token", "app-token"):
            self.assertNotIn(needle, before_update_job.replace("# ", "#"), f"{needle} appears before the update job")

    def test_a_scheduled_run_off_the_integration_branch_only_dispatches(self):
        """Scheduled workflows run from the default branch only. When that is not the integration branch, the scheduled run
        starts this workflow there with `gh workflow run` (a workflow_dispatch started with GITHUB_TOKEN creates a run) and
        stops. The dispatching job has actions: write and nothing else, and no App token exists in it."""
        self.set_config(["core", "deploy"], {**PARAMS, "integration_branch": "release/2.x"})
        self.assertEqual(self.tool()[0], 0)
        doc = self.yaml(UPDATE)
        dispatch, update = doc["jobs"]["dispatch"], doc["jobs"]["update"]
        self.assertEqual(dispatch["if"], "github.event_name == 'schedule' && github.ref_name != 'release/2.x'")
        self.assertEqual(update["if"], "github.event_name != 'schedule' || github.ref_name == 'release/2.x'")
        self.assertEqual(dispatch["permissions"], {"actions": "write"})
        self.assertEqual(doc["permissions"], {"contents": "read"})
        self.assertEqual(len(dispatch["steps"]), 1)
        step = dispatch["steps"][0]
        self.assertEqual(step["env"], {"GH_TOKEN": "${{ github.token }}"})
        self.assertEqual(step["run"], 'gh workflow run mercury-update.yml --repo "$GITHUB_REPOSITORY" --ref "$MERCURY_BASE"')
        self.assertEqual(dispatch["env"]["MERCURY_BASE"], "release/2.x")
        self.assertNotIn("uses", step)
        dispatch_text = (self.dst / UPDATE).read_text(encoding="utf-8").split("\n  update:\n")[0]
        for needle in ("secrets.", "vars.", "create-github-app-token", "contents: write", "pull-requests"):
            self.assertNotIn(needle, "\n".join(l for l in dispatch_text.splitlines() if not l.strip().startswith("#")))
        # Evaluate the rendered `if:` texts themselves (a tiny evaluator for the only operators they use) for every event and
        # branch: exactly one of the two jobs runs, and the dispatching one runs exactly for a scheduled run off the branch.
        for event in ("schedule", "workflow_dispatch"):
            for ref in ("main", "release/2.x", "develop", "release/2.x-old", "Release/2.x"):
                runs_dispatch = self.evaluate(dispatch["if"], event, ref)
                runs_update = self.evaluate(update["if"], event, ref)
                with self.subTest(event=event, ref=ref):
                    self.assertNotEqual(runs_dispatch, runs_update)
                    self.assertEqual(runs_dispatch, event == "schedule" and ref.lower() != "release/2.x")
        self.assertEqual(self.dst.joinpath(UPDATE).name, "mercury-update.yml")  # the file name used in `gh workflow run`

    def test_verify_workflow_reads_back_and_uses_the_job_name_that_merge_requires(self):
        self.assertEqual(self.tool()[0], 0)
        doc = self.yaml(VERIFY)
        self.assertEqual(doc["on"], {"pull_request": {"branches": ["develop"]}})
        self.assertEqual(doc["permissions"], {"contents": "read"})
        job = doc["jobs"]["verify"]
        self.assertEqual(job["name"], UPDATER.VERIFY_CHECK_NAME)
        for step in job["steps"]:
            if str(step.get("uses", "")).startswith("actions/checkout@"):
                self.assertEqual(step["with"]["persist-credentials"], "false")
        self.assertTrue(any("mercury.py check --target target" in str(s.get("run", "")) for s in job["steps"]))
        self.assertTrue(any("mercury_update.py verify" in str(s.get("run", "")) for s in job["steps"]))

    def test_no_pull_request_target_and_no_expression_inside_a_command_line(self):
        self.assertEqual(self.tool()[0], 0)
        for rel in (UPDATE, VERIFY):
            text = (self.dst / rel).read_text(encoding="utf-8")
            code_lines = [l for l in text.splitlines() if not l.strip().startswith("#")]
            self.assertNotIn("pull_request_target", "\n".join(code_lines))
            doc = self.yaml(rel)
            for job in doc["jobs"].values():
                for step in job["steps"]:
                    self.assertNotIn("${{", str(step.get("run", "")), f"{rel}: an expression is written into a command line")
                    if "uses" in step:
                        self.assertRegex(step["uses"], r"@v\d+$", f"{rel}: action not pinned to a major")
            self.assertNotIn("secrets.", "\n".join(l for l in code_lines if "MERCURY_APP_PRIVATE_KEY" not in l))

    def test_hostile_check_names_survive_the_round_trip_as_data(self):
        names = ['quote " and \\ backslash', "colon: x # hash", "'single'", "{{ braces }}", "$HOME `tick` $(x)", "回归 \U0001f600", "a, b]"]
        self.set_config(["core", "deploy"], {**PARAMS, "required_checks": names})
        code, out = self.tool()
        self.assertEqual(code, 0, out)
        text = (self.dst / UPDATE).read_text(encoding="utf-8")
        self.assertEqual(self.required_checks(), names)
        self.assertEqual(len((self.dst / UPDATE).read_text(encoding="utf-8").splitlines()),
                         len((ROOT / "packs/deploy/templates/mercury-update.yml").read_text(encoding="utf-8").splitlines()))  # no extra line

    def test_round_trip_with_a_real_yaml_parser(self):
        """The checker's reader is a minimal one. In CI PyYAML (pinned in the workflow) must be present, so the rendered files
        are read by a real parser there; elsewhere the test is skipped, visibly."""
        try:
            import yaml
        except ImportError:
            if os.environ.get("CI"):
                self.fail("PyYAML must be installed in CI (see the pip install step of .github/workflows/auto-verify.yml)")
            self.skipTest("PyYAML is not installed; the rendered workflows are read back by a real YAML parser only where it is")
        names = ['quote " and \\ backslash', "colon: x # hash", "'single'", "{{ braces }}", "$HOME `tick` $(x)", "回归 \U0001f600", "a, b]", "- dash"]
        self.set_config(["core", "deploy"], {**PARAMS, "required_checks": names, "integration_branch": "release/2.x",
                                             "update_schedule": "*/30 6-18 * * 1-5"})
        self.assertEqual(self.tool()[0], 0)
        update = yaml.safe_load((self.dst / UPDATE).read_text(encoding="utf-8"))
        verify = yaml.safe_load((self.dst / VERIFY).read_text(encoding="utf-8"))
        on = update.get("on", update.get(True))  # YAML 1.1 reads the key `on` as the boolean True
        self.assertEqual(on["schedule"], [{"cron": "*/30 6-18 * * 1-5"}])
        self.assertIn("workflow_dispatch", on)
        self.assertEqual(update["permissions"], {"contents": "read"})
        merge = next(s for s in update["jobs"]["update"]["steps"] if "merge" in str(s.get("run", "")))
        self.assertEqual(json.loads(merge["env"]["MERCURY_REQUIRED_CHECKS"]), names)
        self.assertEqual(update["jobs"]["update"]["env"]["MERCURY_BASE"], "release/2.x")
        self.assertEqual(update["jobs"]["dispatch"]["if"], "github.event_name == 'schedule' && github.ref_name != 'release/2.x'")
        self.assertEqual(update["jobs"]["dispatch"]["permissions"], {"actions": "write"})
        token = next(s for s in update["jobs"]["update"]["steps"] if str(s.get("uses", "")).startswith("actions/create-github-app-token"))
        self.assertEqual(token["with"]["permission-workflows"], "write")
        von = verify.get("on", verify.get(True))
        self.assertEqual(von, {"pull_request": {"branches": ["release/2.x"]}})
        self.assertEqual(verify["jobs"]["verify"]["name"], UPDATER.VERIFY_CHECK_NAME)
        for doc in (update, verify):
            for job in doc["jobs"].values():
                for step in job["steps"]:
                    self.assertNotIn("${{", str(step.get("run", "")))

    def test_the_schedule_and_branch_come_from_the_params(self):
        self.set_config(["core", "deploy"], {**PARAMS, "integration_branch": "release/2.x", "update_schedule": "*/30 6-18 * * 1-5"})
        self.assertEqual(self.tool()[0], 0)
        self.assertEqual(self.yaml(UPDATE)["on"]["schedule"], [{"cron": "*/30 6-18 * * 1-5"}])
        self.assertEqual(self.yaml(UPDATE)["jobs"]["update"]["env"]["MERCURY_BASE"], "release/2.x")
        self.assertEqual(self.yaml(VERIFY)["on"]["pull_request"]["branches"], ["release/2.x"])

    def test_the_schedule_has_a_default_but_branch_and_checks_are_required(self):
        self.set_config(["core", "deploy"], {"integration_branch": "main", "required_checks": []})
        self.assertEqual(self.tool()[0], 0)
        self.assertEqual(self.yaml(UPDATE)["on"]["schedule"], [{"cron": "17 3 * * *"}])
        for params in ({"required_checks": []}, {"integration_branch": "main"}, {}):
            self.set_config(["core", "deploy"], params)
            code, out = self.tool("check")
            self.assertEqual(code, 2, out)
            self.assertIn("is required by", out)

    def test_invalid_params_stop_before_anything_is_written(self):
        shutil.rmtree(self.dst / ".github", ignore_errors=True)
        self.set_config(["core", "deploy"], {**PARAMS, "required_checks": ["${{ secrets.X }}"]})
        code, out = self.tool()
        self.assertEqual(code, 2, out)
        self.assertFalse((self.dst / ".github").exists())
        self.assertFalse((self.dst / LOCK).exists())

    # --- the installer's safety properties for this kind of file ---

    def test_a_hand_edited_workflow_is_never_overwritten_or_deleted(self):
        self.assertEqual(self.tool()[0], 0)
        (self.dst / VERIFY).write_text("name: mine\n", encoding="utf-8")
        code, out = self.tool()
        self.assertEqual(code, 1, out)
        self.assertIn("modified by hand", out)
        self.assertEqual((self.dst / VERIFY).read_text(encoding="utf-8"), "name: mine\n")
        self.set_config(["core"])  # deselecting deploy would delete the files, but not an edited one
        code, out = self.tool()
        self.assertEqual(code, 1, out)
        self.assertTrue((self.dst / VERIFY).exists())

    def test_deselecting_deploy_deletes_only_its_unmodified_workflows(self):
        self.assertEqual(self.tool()[0], 0)
        write(self.dst / ".github/workflows/ci.yml", "name: ci\n")
        write(self.dst / ".github/workflows/mercury-other.yml", "name: not ours\n")
        self.set_config(["core"])
        code, out = self.tool()
        self.assertEqual(code, 0, out)
        self.assertFalse((self.dst / UPDATE).exists())
        self.assertFalse((self.dst / VERIFY).exists())
        self.assertTrue((self.dst / ".github/workflows/ci.yml").exists())
        self.assertTrue((self.dst / ".github/workflows/mercury-other.yml").exists())  # not in the lock: never ours to delete

    def test_a_lock_entry_cannot_steer_deletion_to_another_workflow(self):
        self.assertEqual(self.tool()[0], 0)
        write(self.dst / ".github/workflows/ci.yml", "name: ci\n")
        lock = self.lock()
        for rel in (".github/workflows/ci.yml", ".github/workflows/sub/mercury-x.yml", ".github/mercury-x.yml",
                    ".github/workflows/mercury-x.yaml", ".github/workflows/mercury-.yml"):
            with self.subTest(rel=rel):
                bad = {**lock, "files": {**lock["files"], rel: "0" * 64}}
                write(self.dst / LOCK, json.dumps(bad))
                code, out = self.tool("check")
                self.assertEqual(code, 2, out)
                self.assertIn("outside the managed namespace", out)
        self.assertTrue((self.dst / ".github/workflows/ci.yml").exists())

    def test_an_existing_unmanaged_workflow_of_the_same_name_is_a_conflict(self):
        write(self.dst / UPDATE, "name: someone else's\n")
        code, out = self.tool()
        self.assertEqual(code, 1, out)
        self.assertIn("not in the lock", out)
        self.assertEqual((self.dst / UPDATE).read_text(encoding="utf-8"), "name: someone else's\n")

    def test_ignored_workflow_paths_stop_the_run(self):
        write(self.dst / ".gitignore", ".github/\n")
        code, out = self.tool()
        self.assertEqual(code, 2, out)
        self.assertIn("Git ignore rules", out)
        self.assertFalse((self.dst / UPDATE).exists())

    def test_a_rendered_destination_outside_the_workflow_namespace_is_refused(self):
        pack = self.src / "packs/deploy/pack.json"
        for dest in (".github/workflows/ci.yml", ".mercury/lock.json", "AGENTS.md", ".github/workflows/x/mercury-a.yml", "../x.yml"):
            with self.subTest(dest=dest):
                write(pack, json.dumps({"schema_version": 1, "name": "deploy", "skills": {}, "rendered": {dest: "templates/mercury-verify.yml"}}))
                git(self.src, "add", "-A")
                git(self.src, "commit", "-q", "--allow-empty", "-m", f"x {dest}")
                git(self.src, "update-ref", "refs/remotes/origin/master", "HEAD")
                code, out = self.tool("check")
                self.assertEqual(code, 2, out)

    def test_templates_reject_unknown_placeholders(self):
        for text in ("a: @@NOPE@@\n", "a: @@INTEGRATION_BRANCH@@ @@\n", "a: @@\n"):
            with self.subTest(text=text), self.assertRaises(TOOLMOD.BuildError):
                TOOLMOD.render_template(text.encode(), PARAMS, "t")

    # --- install --params-json: a first installation can name the params ---

    def test_a_first_install_can_create_the_config_with_params(self):
        (self.dst / CONFIG).unlink()
        params = json.dumps({"integration_branch": "main", "required_checks": ["CI"]})
        code, out = self.tool("install", "--packs", "core,deploy", "--params-json", params, "--dry-run")
        self.assertEqual(code, 0, out)
        self.assertFalse((self.dst / CONFIG).exists())  # a dry run writes nothing
        code, out = self.tool("install", "--packs", "core,deploy", "--params-json", params)
        self.assertEqual(code, 0, out)
        cfg = json.loads((self.dst / CONFIG).read_text(encoding="utf-8"))
        self.assertEqual(cfg["params"], {"integration_branch": "main", "required_checks": ["CI"]})
        self.assertEqual(self.yaml(VERIFY)["on"]["pull_request"]["branches"], ["main"])
        # With an existing config the params belong to the project: a different value is an error, the same one is fine.
        self.assertEqual(self.tool("install", "--params-json", params)[0], 0)
        code, out = self.tool("install", "--params-json", json.dumps({"integration_branch": "other", "required_checks": []}))
        self.assertEqual(code, 2, out)
        self.assertIn("does not match", out)

    def test_install_without_params_for_deploy_and_bad_params_json_are_errors(self):
        (self.dst / CONFIG).unlink()
        code, out = self.tool("install", "--packs", "core,deploy")
        self.assertEqual(code, 2, out)
        self.assertIn("is required by", out)
        self.assertFalse((self.dst / CONFIG).exists())
        for bad in ("{", "[]", '{"integration_branch": "a..b"}', '{"x": 1}', "null"):
            with self.subTest(bad=bad):
                code, out = self.tool("install", "--packs", "core,deploy", "--params-json", bad)
                self.assertEqual(code, 2, out)
                self.assertFalse((self.dst / CONFIG).exists())

    # --- the real repository ---

    def test_the_real_pack_is_registered_enabled_and_renders(self):
        master, _ = TOOLMOD.load_master(ROOT)
        self.assertTrue(master["packs"]["deploy"]["enabled"])
        rendered = TOOLMOD.load_rendered((ROOT / "packs/deploy/pack.json").read_bytes(), "deploy")
        self.assertEqual(set(rendered), {UPDATE, VERIFY})
        agents = (ROOT / "packs/deploy/agents.md").read_text(encoding="utf-8")
        self.assertTrue(agents.isascii())

    def test_mercury_itself_does_not_select_deploy(self):
        cfg = json.loads((ROOT / CONFIG).read_text(encoding="utf-8"))
        self.assertNotIn("deploy", cfg["packs"])


if __name__ == "__main__":
    unittest.main()
