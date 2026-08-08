"""Module smoke tests — the checker's failure paths, on a synthetic fixture.

Run from repo root:

    python -m scripts.sot_id_map.test_smoke

Every negative test follows the same two-step shape:

    1. assert the pristine fixture passes (exit 0)
    2. break exactly one thing, assert exit 1 and that the offending id or
       path is named in the output

Step 1 is not decoration. A fixture that is already broken would make every
negative test "pass" for the wrong reason, and a fixture that the checker
never actually reads would do the same. Asserting the good state first is
what makes the negative assertions mean something.

The fixture is built with `json.dump` from Python dicts — never by pasting
JSON text — so escaping mistakes cannot silently corrupt it.

The last test runs the checker against the two real repositories, but only
when `SOT_ENGINE_REPO` / `SOT_DESIGN_REPO` are set; otherwise it reports a
skip rather than pretending to have covered that ground.
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
PACKAGE_MAP = Path(__file__).resolve().parent / "id_map.json"

#: Every child process gets a deadline. Measured on this machine: the
#: heaviest single invocation is the full two-repository run at 0.22-0.24 s,
#: `--help` is 0.13 s, and the whole 35-case suite is ~18 s. The budgets
#: below are therefore ~100-250x the observed cost — deliberately generous,
#: because a budget tight enough to trip on a slow or loaded machine would
#: turn a passing suite flaky, and a flaky timeout teaches people to ignore
#: timeouts. They exist to bound a *hang*, not to police performance.
CHECKER_TIMEOUT = 60.0      # any `python -m scripts.sot_id_map` invocation
HELP_TIMEOUT = 30.0         # `--help`, which reads nothing
TOOL_TIMEOUT = 30.0         # external tools: icacls, mklink


def _partial(stream: object) -> str:
    """Whatever the killed child managed to emit, in a printable form."""
    if stream is None:
        return "(none)"
    if isinstance(stream, bytes):
        stream = stream.decode("utf-8", "replace")
    text = str(stream).strip()
    return text[:2000] or "(empty)"


def _spawn(argv: list[str], timeout: float, **kwargs) -> subprocess.CompletedProcess:
    """The one place this suite starts a child process.

    Single boundary on purpose: a bare `subprocess.run` anywhere in here is
    an unbounded wait, and one hung command would stall the entire suite
    with no indication of which command it was. Routing everything through
    here means the deadline cannot be forgotten at a new call site.

    A timeout is reported as an `AssertionError` naming the argv and the
    output captured before the kill — `TimeoutExpired` alone says a command
    took too long without saying which, which is the failure mode that
    wastes the most time to diagnose.
    """
    try:
        return subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout, **kwargs)
    except subprocess.TimeoutExpired as exc:
        raise AssertionError(
            f"child process exceeded its {timeout}s budget and was killed\n"
            f"  argv:   {argv}\n"
            f"  stdout: {_partial(exc.stdout)}\n"
            f"  stderr: {_partial(exc.stderr)}"
        ) from exc


# --------------------------------------------------------------------------
# fixture
# --------------------------------------------------------------------------

def _build_fixture(tmp: Path) -> tuple[Path, Path, dict]:
    """A two-repo miniature reproducing every shape the real map uses.

    engine: two skills (one shared by two design entries), one class that
    carries a field-implemented passive, one out-of-scope config dir.
    design: three skills (two of them the split pair, one field-implemented),
    one class, one design-only rule.
    """
    engine = tmp / "engine"
    design = tmp / "design"
    (engine / "data" / "skills").mkdir(parents=True)
    (engine / "data" / "classes").mkdir(parents=True)
    (engine / "data" / "runloop").mkdir(parents=True)
    (design / "snapshots").mkdir(parents=True)

    def w(path: Path, obj: object) -> None:
        path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")

    w(engine / "data" / "skills" / "sw_slash.json",
      {"id": "sw_slash", "name": "slash"})
    w(engine / "data" / "skills" / "sw_parry.json",
      {"id": "sw_parry", "name": "parry"})
    w(engine / "data" / "classes" / "hero.json",
      {"id": "hero", "passive_config": {"crit_per_qi": 2}})
    w(engine / "data" / "runloop" / "run_config.json", {"difficulty": 1})

    w(design / "snapshots" / "skills.json",
      [{"id": "a_slash"}, {"id": "b_slash"}, {"id": "a_eye"}])
    w(design / "snapshots" / "classes.json", [{"id": "hero"}])
    w(design / "snapshots" / "rules.json", [{"code": "R1.1"}])
    # `hero` deliberately exists on both sides: it is what the cross-side
    # contradiction test breaks, and what proves the clean fixture does not
    # trip that check.

    spec = {
        "schema_version": 1,
        "sides": {"engine": {"root_env": "SOT_ENGINE_REPO"},
                  "design": {"root_env": "SOT_DESIGN_REPO"}},
        "engine_scope": {
            "data_dir": "data",
            "excluded_paths": {"data/runloop": "config, not entities"},
        },
        "design_scope": {"snapshot_dir": "snapshots", "excluded_paths": {}},
        "reason_codes": {
            "engine_not_implemented": "design has it, engine does not yet",
            "engine_implements_as_field": "engine built it as a field",
            "engine_no_entity_kind": "engine has no such entity kind",
        },
        "entity_types": [
            {"type": "skill", "coverage": "required",
             "engine": {"kind": "json_dir", "path": "data/skills",
                        "id_field": "id"},
             "design": {"kind": "json_array", "path": "snapshots/skills.json",
                        "id_field": "id"}},
            {"type": "class", "coverage": "required",
             "engine": {"kind": "json_dir", "path": "data/classes",
                        "id_field": "id"},
             "design": {"kind": "json_array", "path": "snapshots/classes.json",
                        "id_field": "id"}},
            {"type": "rule", "coverage": "required", "engine": None,
             "design": {"kind": "json_array", "path": "snapshots/rules.json",
                        "id_field": "code"}},
        ],
        "mappings": [
            {"type": "skill", "engine_ids": ["sw_slash"],
             "design_ids": ["a_slash", "b_slash"], "cardinality": "1:N",
             "basis": "both design classes list sw_slash"},
            {"type": "class", "engine_ids": ["hero"], "design_ids": ["hero"],
             "cardinality": "1:1", "basis": "same id both sides"},
        ],
        "unmapped": [
            {"type": "skill", "side": "engine", "id": "sw_parry",
             "reason": "engine_not_implemented"},
            {"type": "skill", "side": "design", "id": "a_eye",
             "reason": "engine_implements_as_field",
             "engine_carrier": "data/classes/hero.json#passive_config"},
            {"type": "rule", "side": "design", "id": "R1.1",
             "reason": "engine_no_entity_kind"},
        ],
    }
    return engine, design, spec


def _run(spec: dict, engine: Path, design: Path, tmp: Path,
         env: dict | None = None,
         pass_repo_flags: bool = True) -> subprocess.CompletedProcess:
    map_path = tmp / "map.json"
    map_path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    argv = [sys.executable, "-m", "scripts.sot_id_map", "--map", str(map_path)]
    if pass_repo_flags:
        argv += ["--engine-repo", str(engine), "--design-repo", str(design)]
    run_env = dict(os.environ) if env is None else env
    # Decode the child explicitly. Without this the parent falls back to the
    # console codepage (GBK on this machine) and a finding containing a
    # non-ASCII id blows up in the reader thread instead of failing an
    # assertion — a fixture failure wearing the costume of a code failure.
    return _spawn(argv, CHECKER_TIMEOUT, cwd=ROOT, env=run_env,
                  encoding="utf-8", errors="replace")


def _assert_pristine(spec: dict, engine: Path, design: Path,
                     tmp: Path) -> None:
    """Fixture sanity self-check — see the module docstring."""
    proc = _run(spec, engine, design, tmp)
    assert proc.returncode == 0, (
        "fixture is not green before mutation, so the negative assertion "
        f"below would prove nothing:\n{proc.stdout}\n{proc.stderr}")


# --------------------------------------------------------------------------
# tests
# --------------------------------------------------------------------------

def test_help() -> None:
    proc = _spawn([sys.executable, "-m", "scripts.sot_id_map", "--help"],
                  HELP_TIMEOUT, cwd=ROOT)
    assert proc.returncode == 0, proc.stderr
    assert "--engine-repo" in proc.stdout and "--design-repo" in proc.stdout
    print("PASS test_help")


def test_fixture_passes_clean() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        engine, design, spec = _build_fixture(tmp_path)
        proc = _run(spec, engine, design, tmp_path)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "OK:" in proc.stdout, proc.stdout
        # the checker really did read both repositories
        assert "engine ids: 3" in proc.stdout, proc.stdout
        assert "design ids: 5" in proc.stdout, proc.stdout
    print("PASS test_fixture_passes_clean")


def test_removed_mapping_is_reported() -> None:
    """Criterion 1: an id covered by neither mappings nor unmapped fails."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        engine, design, spec = _build_fixture(tmp_path)
        _assert_pristine(spec, engine, design, tmp_path)

        broken = copy.deepcopy(spec)
        broken["mappings"] = [m for m in broken["mappings"]
                              if m["type"] != "skill"]
        proc = _run(broken, engine, design, tmp_path)
        assert proc.returncode == 1, proc.stdout
        assert "uncovered_id" in proc.stdout, proc.stdout
        for missing in ("sw_slash", "a_slash", "b_slash"):
            assert missing in proc.stdout, (missing, proc.stdout)
    print("PASS test_removed_mapping_is_reported")


def test_new_engine_file_is_reported() -> None:
    """The same guard, triggered from the other direction: new engine data."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        engine, design, spec = _build_fixture(tmp_path)
        _assert_pristine(spec, engine, design, tmp_path)

        (engine / "data" / "skills" / "sw_new.json").write_text(
            json.dumps({"id": "sw_new"}), encoding="utf-8")
        proc = _run(spec, engine, design, tmp_path)
        assert proc.returncode == 1, proc.stdout
        assert "uncovered_id" in proc.stdout and "sw_new" in proc.stdout, \
            proc.stdout
    print("PASS test_new_engine_file_is_reported")


def test_nonexistent_id_is_reported() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        engine, design, spec = _build_fixture(tmp_path)
        _assert_pristine(spec, engine, design, tmp_path)

        broken = copy.deepcopy(spec)
        broken["mappings"][1]["engine_ids"] = ["hero_ghost"]
        broken["unmapped"].append({"type": "class", "side": "engine",
                                   "id": "hero",
                                   "reason": "engine_not_implemented"})
        proc = _run(broken, engine, design, tmp_path)
        assert proc.returncode == 1, proc.stdout
        assert "unknown_id" in proc.stdout and "hero_ghost" in proc.stdout, \
            proc.stdout
    print("PASS test_nonexistent_id_is_reported")


def test_bad_reason_code_is_reported() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        engine, design, spec = _build_fixture(tmp_path)
        _assert_pristine(spec, engine, design, tmp_path)

        broken = copy.deepcopy(spec)
        broken["unmapped"][0]["reason"] = "probably_fine"
        proc = _run(broken, engine, design, tmp_path)
        assert proc.returncode == 1, proc.stdout
        assert "bad_reason" in proc.stdout and "probably_fine" in proc.stdout, \
            proc.stdout
    print("PASS test_bad_reason_code_is_reported")


def test_duplicate_claim_is_reported() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        engine, design, spec = _build_fixture(tmp_path)
        _assert_pristine(spec, engine, design, tmp_path)

        broken = copy.deepcopy(spec)
        broken["unmapped"].append({"type": "skill", "side": "engine",
                                   "id": "sw_slash",
                                   "reason": "engine_not_implemented"})
        proc = _run(broken, engine, design, tmp_path)
        assert proc.returncode == 1, proc.stdout
        assert "duplicate_id" in proc.stdout and "sw_slash" in proc.stdout, \
            proc.stdout
    print("PASS test_duplicate_claim_is_reported")


def test_missing_carrier_is_reported() -> None:
    """`engine_implements_as_field` may not be used as a vague escape hatch."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        engine, design, spec = _build_fixture(tmp_path)
        _assert_pristine(spec, engine, design, tmp_path)

        dropped = copy.deepcopy(spec)
        del dropped["unmapped"][1]["engine_carrier"]
        proc = _run(dropped, engine, design, tmp_path)
        assert proc.returncode == 1, proc.stdout
        assert "missing_carrier" in proc.stdout, proc.stdout

        dangling = copy.deepcopy(spec)
        dangling["unmapped"][1]["engine_carrier"] = "data/classes/ghost.json#x"
        proc = _run(dangling, engine, design, tmp_path)
        assert proc.returncode == 1, proc.stdout
        assert "missing_carrier" in proc.stdout and "ghost" in proc.stdout, \
            proc.stdout
    print("PASS test_missing_carrier_is_reported")


def test_cross_side_contradiction_is_reported() -> None:
    """Coverage asks "accounted for"; this asks "accounted for truthfully".

    `hero` exists on both sides. Splitting it into two mutually
    contradictory `unmapped` entries — one claiming the design library
    never registered it, the other claiming the engine never implemented
    it — satisfies coverage while stating something false.
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        engine, design, spec = _build_fixture(tmp_path)
        _assert_pristine(spec, engine, design, tmp_path)

        broken = copy.deepcopy(spec)
        broken["reason_codes"]["design_not_registered"] = "engine has it only"
        broken["mappings"] = [m for m in broken["mappings"]
                              if m["type"] != "class"]
        broken["unmapped"] += [
            {"type": "class", "side": "engine", "id": "hero",
             "reason": "design_not_registered"},
            {"type": "class", "side": "design", "id": "hero",
             "reason": "engine_not_implemented"},
        ]
        proc = _run(broken, engine, design, tmp_path)
        assert proc.returncode == 1, proc.stdout
        assert "cross_side_contradiction" in proc.stdout, proc.stdout
        assert proc.stdout.count("cross_side_contradiction") == 2, proc.stdout
        assert "hero" in proc.stdout, proc.stdout

        # The documented escape hatch for a genuine id collision: state why
        # the same-named entity on the other side is not the same thing.
        excused = copy.deepcopy(broken)
        for item in excused["unmapped"]:
            if item["id"] == "hero":
                item["same_id_other_side"] = "unrelated namesake, verified"
        proc = _run(excused, engine, design, tmp_path)
        assert proc.returncode == 0, proc.stdout
        # ...and using it must be visible in the report, not only in the file
        assert "same_id_other_side escape hatch: 2 in use" in proc.stdout, \
            proc.stdout
        assert "unverifiable by machine" in proc.stdout, proc.stdout
        assert proc.stdout.count("unrelated namesake, verified") == 2, \
            proc.stdout
    print("PASS test_cross_side_contradiction_is_reported")


def test_escape_hatch_usage_is_always_reported() -> None:
    """Zero uses still prints a line — silence would be indistinguishable."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        engine, design, spec = _build_fixture(tmp_path)
        proc = _run(spec, engine, design, tmp_path)
        assert proc.returncode == 0, proc.stdout
        assert "same_id_other_side escape hatch: 0 in use" in proc.stdout, \
            proc.stdout
        assert "unverifiable by machine" not in proc.stdout, proc.stdout
    print("PASS test_escape_hatch_usage_is_always_reported")


def test_stale_escape_hatch_is_reported() -> None:
    """The hatch may not be pre-authorised on entries where it does nothing."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        engine, design, spec = _build_fixture(tmp_path)
        _assert_pristine(spec, engine, design, tmp_path)

        broken = copy.deepcopy(spec)
        # `sw_parry` exists on the engine side only, so the field suppresses
        # nothing here — carrying it would silently pre-arm the hatch for the
        # day a design entry of the same id shows up.
        broken["unmapped"][0]["same_id_other_side"] = "just in case"
        proc = _run(broken, engine, design, tmp_path)
        assert proc.returncode == 1, proc.stdout
        assert "suppresses nothing" in proc.stdout, proc.stdout
        assert "sw_parry" in proc.stdout, proc.stdout
    print("PASS test_stale_escape_hatch_is_reported")


def test_carrier_field_must_exist() -> None:
    """The field half of `engine_carrier` is the evidence — verify it."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        engine, design, spec = _build_fixture(tmp_path)
        _assert_pristine(spec, engine, design, tmp_path)

        for bad, needle in (
            ("data/classes/hero.json#no_such_field_at_all", "no_such_field"),
            ("data/classes/hero.json#passive_config.no_such_key", "no_such_key"),
            ("data/classes/hero.json#", "empty field name"),
            ("data/classes/hero.json", "no #<field> part"),
        ):
            broken = copy.deepcopy(spec)
            broken["unmapped"][1]["engine_carrier"] = bad
            proc = _run(broken, engine, design, tmp_path)
            assert proc.returncode == 1, (bad, proc.stdout)
            assert "missing_carrier" in proc.stdout, (bad, proc.stdout)
            assert needle in proc.stdout, (bad, proc.stdout)

        # a nested dot path that *does* resolve must still pass
        ok = copy.deepcopy(spec)
        ok["unmapped"][1]["engine_carrier"] = \
            "data/classes/hero.json#passive_config.crit_per_qi"
        proc = _run(ok, engine, design, tmp_path)
        assert proc.returncode == 0, proc.stdout
    print("PASS test_carrier_field_must_exist")


def test_file_without_id_is_reported() -> None:
    """A new entity whose author forgot the id field must not read green."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        engine, design, spec = _build_fixture(tmp_path)
        _assert_pristine(spec, engine, design, tmp_path)

        ghost = engine / "data" / "skills" / "sw_ghost.json"
        ghost.write_text(json.dumps({"name": "ghost"}), encoding="utf-8")
        proc = _run(spec, engine, design, tmp_path)
        assert proc.returncode == 1, proc.stdout
        assert "missing_entity_id" in proc.stdout, proc.stdout
        assert "sw_ghost.json" in proc.stdout, proc.stdout
    print("PASS test_file_without_id_is_reported")


def test_scope_guard_covers_loose_and_nested_files() -> None:
    """Two blind spots: loose `data/*.json`, and nested design snapshots."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        engine, design, spec = _build_fixture(tmp_path)
        _assert_pristine(spec, engine, design, tmp_path)

        loose = engine / "data" / "loose_entity.json"
        loose.write_text(json.dumps({"id": "loose"}), encoding="utf-8")
        proc = _run(spec, engine, design, tmp_path)
        assert proc.returncode == 1, proc.stdout
        assert "undeclared_scope" in proc.stdout, proc.stdout
        assert "data/loose_entity.json" in proc.stdout, proc.stdout
        loose.unlink()
        _assert_pristine(spec, engine, design, tmp_path)

        nested = design / "snapshots" / "archive"
        nested.mkdir()
        (nested / "old_skills.json").write_text(json.dumps([]),
                                                encoding="utf-8")
        proc = _run(spec, engine, design, tmp_path)
        assert proc.returncode == 1, proc.stdout
        assert "snapshots/archive/old_skills.json" in proc.stdout, proc.stdout
    print("PASS test_scope_guard_covers_loose_and_nested_files")


def test_declared_path_cannot_escape_the_repository(tmp_outside: Path) -> None:
    """A declared path may not walk out of the repository it belongs to.

    `tmp_outside` holds a JSON file that lives *outside* both fixture repos.
    If the checker ever reads it, the escape happened — so the assertion is
    on the file's own contents appearing in the report, not merely on the
    exit code.
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        engine, design, spec = _build_fixture(tmp_path)
        _assert_pristine(spec, engine, design, tmp_path)

        escapes = [
            "../../outside",                       # parent traversal
            "data/../../outside",                  # traversal mid-path
            str(tmp_outside).replace("\\", "/"),   # absolute / drive-qualified
            "/etc",                                # rooted
        ]
        for escape in escapes:
            broken = copy.deepcopy(spec)
            broken["entity_types"][0]["engine"]["path"] = escape
            proc = _run(broken, engine, design, tmp_path)
            assert proc.returncode == 1, (escape, proc.stdout, proc.stderr)
            assert "bad_map" in proc.stdout, (escape, proc.stdout)
            assert "Traceback" not in proc.stderr, (escape, proc.stderr)
            # nothing outside was read, and no repository was read at all
            assert "CANARY_OUTSIDE_REPO" not in proc.stdout, \
                (escape, proc.stdout)
            assert "no repository data was read" in proc.stdout, \
                (escape, proc.stdout)

        # same guard on the scope directories and on an engine_carrier
        for key, scope in (("data_dir", "engine_scope"),
                           ("snapshot_dir", "design_scope")):
            broken = copy.deepcopy(spec)
            broken[scope][key] = "../../outside"
            proc = _run(broken, engine, design, tmp_path)
            assert proc.returncode == 1, (scope, proc.stdout)
            assert "bad_map" in proc.stdout, (scope, proc.stdout)
        broken = copy.deepcopy(spec)
        broken["unmapped"][1]["engine_carrier"] = "../../outside/leak.json#x"
        proc = _run(broken, engine, design, tmp_path)
        assert proc.returncode == 1, proc.stdout
        assert "bad_map" in proc.stdout, proc.stdout
        assert "CANARY_OUTSIDE_REPO" not in proc.stdout, proc.stdout
    print("PASS test_declared_path_cannot_escape_the_repository")


def test_excluded_paths_must_be_a_dict_with_reasons() -> None:
    """The list form silently swallowed whole entity types with no reason."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        engine, design, spec = _build_fixture(tmp_path)
        _assert_pristine(spec, engine, design, tmp_path)

        # The exact shape that used to exit 0: exclude `skill` on both sides
        # as a *list*, drop every skill entry, and watch 2 engine + 3 design
        # ids disappear without a word.
        broken = copy.deepcopy(spec)
        broken["entity_types"] = [e for e in broken["entity_types"]
                                  if e["type"] != "skill"]
        broken["mappings"] = [m for m in broken["mappings"]
                              if m["type"] != "skill"]
        broken["unmapped"] = [u for u in broken["unmapped"]
                              if u["type"] != "skill"]
        broken["engine_scope"]["excluded_paths"] = ["data/runloop",
                                                    "data/skills"]
        broken["design_scope"]["excluded_paths"] = ["snapshots/skills.json"]
        proc = _run(broken, engine, design, tmp_path)
        assert proc.returncode == 1, (proc.returncode, proc.stdout)
        assert "excluded_paths must be an object" in proc.stdout, proc.stdout
        assert "nowhere to record why" in proc.stdout, proc.stdout

        # dict form with an empty reason is just as unaccountable
        blank = copy.deepcopy(spec)
        blank["engine_scope"]["excluded_paths"]["data/runloop"] = "   "
        proc = _run(blank, engine, design, tmp_path)
        assert proc.returncode == 1, proc.stdout
        assert "no stated reason" in proc.stdout, proc.stdout
    print("PASS test_excluded_paths_must_be_a_dict_with_reasons")


def test_non_utf8_file_exits_2_without_traceback() -> None:
    """Both repos are full of Chinese; one editor save can produce this."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        engine, design, spec = _build_fixture(tmp_path)
        _assert_pristine(spec, engine, design, tmp_path)

        target = engine / "data" / "skills" / "sw_slash.json"
        target.write_bytes(
            json.dumps({"id": "sw_slash", "name": "斩击"},
                       ensure_ascii=False).encode("gbk"))
        proc = _run(spec, engine, design, tmp_path)
        assert proc.returncode == 2, (proc.returncode, proc.stdout, proc.stderr)
        assert "Traceback" not in proc.stderr, proc.stderr
        assert "not valid UTF-8" in proc.stderr, proc.stderr

        # same for the map file itself
        target.write_text(json.dumps({"id": "sw_slash"}), encoding="utf-8")
        _assert_pristine(spec, engine, design, tmp_path)
        gbk_map = tmp_path / "gbk_map.json"
        # The fixture map is pure ASCII, and ASCII is byte-identical in GBK
        # and UTF-8 — encoding it as GBK would prove nothing. Put a Chinese
        # note in first so the bytes really are undecodable as UTF-8, then
        # assert that they are (fixture self-check before the real one).
        chinese_map = copy.deepcopy(spec)
        chinese_map["_note"] = "中文说明，用来让这份文件在 GBK 下真的不是 UTF-8"
        payload = json.dumps(chinese_map, ensure_ascii=False).encode("gbk")
        try:
            payload.decode("utf-8")
        except UnicodeDecodeError:
            pass
        else:
            raise AssertionError("fixture is wrong: the GBK map still decodes "
                                 "as UTF-8, so this proves nothing")
        gbk_map.write_bytes(payload)
        proc = _spawn(
            [sys.executable, "-m", "scripts.sot_id_map", "--map", str(gbk_map),
             "--engine-repo", str(engine), "--design-repo", str(design)],
            CHECKER_TIMEOUT, cwd=ROOT, encoding="utf-8", errors="replace")
        assert proc.returncode == 2, (proc.returncode, proc.stderr)
        assert "Traceback" not in proc.stderr, proc.stderr
    print("PASS test_non_utf8_file_exits_2_without_traceback")


def test_malformed_map_reports_findings_not_traceback() -> None:
    """A `null` in `mappings` used to crash out through `main()` as exit 1."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        engine, design, spec = _build_fixture(tmp_path)
        _assert_pristine(spec, engine, design, tmp_path)

        for mutate, needle in (
            (lambda s: s["mappings"].append(None), "must be an object"),
            (lambda s: s["unmapped"].append(None), "must be an object"),
            (lambda s: s.update(entity_types="nope"), "must be a list"),
            (lambda s: s["entity_types"][0].update(coverage="maybe"),
             "expected 'required' or 'excluded'"),
            (lambda s: s["entity_types"][0]["engine"].update(kind="magic"),
             "unknown kind"),
        ):
            broken = copy.deepcopy(spec)
            mutate(broken)
            proc = _run(broken, engine, design, tmp_path)
            assert proc.returncode == 1, (needle, proc.returncode, proc.stderr)
            assert "Traceback" not in proc.stderr, (needle, proc.stderr)
            assert "bad_map" in proc.stdout and needle in proc.stdout, \
                (needle, proc.stdout)

        # a missing top-level key is an environment-class problem: exit 2
        gutted = copy.deepcopy(spec)
        del gutted["reason_codes"]
        proc = _run(gutted, engine, design, tmp_path)
        assert proc.returncode == 2, (proc.returncode, proc.stderr)
        assert "Traceback" not in proc.stderr, proc.stderr
        assert "reason_codes" in proc.stderr, proc.stderr
    print("PASS test_malformed_map_reports_findings_not_traceback")


def test_empty_and_odd_ids_rejected_on_both_sides() -> None:
    """The two sides judge ids by one rule, not two."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        engine, design, spec = _build_fixture(tmp_path)
        _assert_pristine(spec, engine, design, tmp_path)

        # design side: empty string used to be accepted as a real id
        skills = design / "snapshots" / "skills.json"
        original = skills.read_text(encoding="utf-8")
        for value, needle in ((" ", "empty string"),
                              (None, "absent or null"),
                              (True, "boolean"),
                              (1.5, "id is a float"),
                              ({"a": 1}, "id is a dict")):
            skills.write_text(json.dumps(
                [{"id": "a_slash"}, {"id": "b_slash"}, {"id": "a_eye"},
                 {"id": value}]), encoding="utf-8")
            proc = _run(spec, engine, design, tmp_path)
            assert proc.returncode == 1, (value, proc.stdout, proc.stderr)
            assert "missing_entity_id" in proc.stdout, (value, proc.stdout)
            assert needle in proc.stdout, (value, proc.stdout)
        skills.write_text(original, encoding="utf-8")
        _assert_pristine(spec, engine, design, tmp_path)

        # engine side: same rule, same finding
        (engine / "data" / "skills" / "sw_blank.json").write_text(
            json.dumps({"id": "  "}), encoding="utf-8")
        proc = _run(spec, engine, design, tmp_path)
        assert proc.returncode == 1, proc.stdout
        assert "missing_entity_id" in proc.stdout, proc.stdout
        assert "empty string" in proc.stdout, proc.stdout
    print("PASS test_empty_and_odd_ids_rejected_on_both_sides")


def test_null_is_not_a_justification() -> None:
    """`null` must not satisfy any "you must state something" rule.

    One root cause, several doors: `str(None)` is `"None"`, which is
    non-empty and truthy, so every field guarded by `str(x).strip()` could
    be satisfied by writing `null` — the ordinary slip of typing a key and
    not filling it in. The worst of them suppressed a
    `cross_side_contradiction` outright.
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        engine, design, spec = _build_fixture(tmp_path)
        _assert_pristine(spec, engine, design, tmp_path)

        # the Critical: null justification must not suppress the finding
        contradiction = copy.deepcopy(spec)
        contradiction["reason_codes"]["design_not_registered"] = "engine only"
        contradiction["mappings"] = [m for m in contradiction["mappings"]
                                     if m["type"] != "class"]
        contradiction["unmapped"] += [
            {"type": "class", "side": "engine", "id": "hero",
             "reason": "design_not_registered", "same_id_other_side": None},
            {"type": "class", "side": "design", "id": "hero",
             "reason": "engine_not_implemented", "same_id_other_side": None},
        ]
        proc = _run(contradiction, engine, design, tmp_path)
        assert proc.returncode == 1, (proc.returncode, proc.stdout)
        assert "bad_map" in proc.stdout, proc.stdout
        assert "same_id_other_side must be a non-empty string" in proc.stdout, \
            proc.stdout
        # and it must not be counted as a justified use of the hatch
        assert "escape hatch: 2 in use" not in proc.stdout, proc.stdout

        # every other "must be stated" field, same root cause
        for mutate, needle in (
            (lambda s: s["mappings"][0].update(basis=None),
             "mappings[0].basis must be a non-empty string"),
            (lambda s: s["mappings"][0].update(cardinality=None),
             "cardinality must be a non-empty string"),
            (lambda s: s["mappings"][0].update(type=None),
             "mappings[0].type must be a non-empty string"),
            (lambda s: s["unmapped"][0].update(reason=None),
             "unmapped[0].reason must be a non-empty string"),
            (lambda s: s["unmapped"][0].update(id=None),
             "unmapped[0].id must be a non-empty string"),
            (lambda s: s["unmapped"][0].update(side=None),
             "side must be 'engine' or 'design'"),
            (lambda s: s["unmapped"][1].update(engine_carrier=None),
             "engine_carrier"),
            (lambda s: s["entity_types"][0].update(type=None),
             "entity_types[0].type must be a non-empty string"),
            (lambda s: s["engine_scope"]["excluded_paths"].update(
                {"data/runloop": None}), "has no stated reason"),
            (lambda s: s["reason_codes"].update(engine_not_implemented=None),
             "has no description"),
            # the same coercion trap wearing different clothes
            (lambda s: s["mappings"][0].update(basis=False),
             "basis must be a non-empty string"),
            (lambda s: s["mappings"][0].update(basis=[]),
             "basis must be a non-empty string"),
            (lambda s: s["mappings"][0].update(basis="   "),
             "basis must be a non-empty string"),
        ):
            broken = copy.deepcopy(spec)
            mutate(broken)
            proc = _run(broken, engine, design, tmp_path)
            assert proc.returncode == 1, (needle, proc.returncode, proc.stderr)
            assert "Traceback" not in proc.stderr, (needle, proc.stderr)
            assert needle in proc.stdout, (needle, proc.stdout)

        # unhashable `type` used to reach `by_type.get([])` and raise
        broken = copy.deepcopy(spec)
        broken["entity_types"][0]["type"] = []
        proc = _run(broken, engine, design, tmp_path)
        assert proc.returncode == 1, (proc.returncode, proc.stderr)
        assert "Traceback" not in proc.stderr, proc.stderr
        assert "must be a non-empty string" in proc.stdout, proc.stdout
    print("PASS test_null_is_not_a_justification")


def test_empty_repo_flag_is_an_error_not_a_fallback() -> None:
    """`--engine-repo=` must not quietly resolve to the environment."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        engine, design, spec = _build_fixture(tmp_path)
        _assert_pristine(spec, engine, design, tmp_path)

        map_path = tmp_path / "map.json"
        map_path.write_text(json.dumps(spec), encoding="utf-8")
        env = dict(os.environ)
        env["SOT_ENGINE_REPO"] = str(engine)
        env["SOT_DESIGN_REPO"] = str(design)
        # fixture self-check: with the flag omitted, the environment works
        ok = _spawn(
            [sys.executable, "-m", "scripts.sot_id_map", "--map", str(map_path)],
            CHECKER_TIMEOUT, cwd=ROOT, env=env, encoding="utf-8",
            errors="replace")
        assert ok.returncode == 0, (ok.stdout, ok.stderr)

        for flag in ("--engine-repo=", "--design-repo=", "--engine-repo=   "):
            proc = _spawn(
                [sys.executable, "-m", "scripts.sot_id_map",
                 "--map", str(map_path), flag],
                CHECKER_TIMEOUT, cwd=ROOT, env=env, encoding="utf-8",
                errors="replace")
            assert proc.returncode == 2, (flag, proc.returncode, proc.stdout)
            assert "empty value" in proc.stderr, (flag, proc.stderr)
            # the point: it did NOT fall back and run against the env repo
            assert "OK:" not in proc.stdout, (flag, proc.stdout)
    print("PASS test_empty_repo_flag_is_an_error_not_a_fallback")


def test_directory_symlink_is_reported_not_skipped() -> None:
    """A linked entity directory must not vanish in silence."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        engine, design, spec = _build_fixture(tmp_path)
        _assert_pristine(spec, engine, design, tmp_path)

        hidden = engine / "shared_skills"
        hidden.mkdir()
        (hidden / "sw_linked.json").write_text(json.dumps({"id": "sw_linked"}),
                                               encoding="utf-8")
        link = engine / "data" / "skills" / "shared"
        try:
            link.symlink_to(hidden, target_is_directory=True)
            kind = "symlink"
        except (OSError, NotImplementedError) as exc:
            # Unprivileged Windows cannot create symlinks, but it can create
            # junctions — and a junction is the case this machine would
            # actually hit, so fall back to it rather than skipping. It also
            # exercises the reparse-tag branch, which `is_symlink()` misses.
            if os.name != "nt" or not shutil.which("cmd"):
                print("SKIP test_directory_symlink_is_reported_not_skipped "
                      f"(cannot create a directory symlink here: {exc})")
                return
            made = _spawn(["cmd", "/c", "mklink", "/J", str(link), str(hidden)],
                          TOOL_TIMEOUT)
            if made.returncode != 0:
                print("SKIP test_directory_symlink_is_reported_not_skipped "
                      f"(symlink denied and mklink /J failed: "
                      f"{made.stdout.strip() or made.stderr.strip()})")
                return
            kind = "junction"
        # fixture self-check: the link is real and leads to the entity
        assert (link / "sw_linked.json").is_file(), \
            "symlink did not take effect, so the assertion below is empty"

        proc = _run(spec, engine, design, tmp_path)
        assert proc.returncode == 1, (proc.returncode, proc.stdout)
        assert "unfollowed_link" in proc.stdout, proc.stdout
        assert "shared" in proc.stdout, proc.stdout
        # the failure mode guarded against: green run, entity simply gone
        assert "OK:" not in proc.stdout, proc.stdout
        assert "sw_linked" not in proc.stdout, \
            "the linked entity was walked after all: " + proc.stdout
    print(f"PASS test_directory_symlink_is_reported_not_skipped ({kind})")


def _make_dir_alias(link: Path, target: Path) -> str | None:
    """Create a directory alias, returning its kind, or None if impossible.

    Symlinks need a privilege this machine does not have; junctions do not,
    and a junction is the alias this machine would actually meet. Falling
    back keeps the test real instead of skipping it.
    """
    try:
        link.symlink_to(target, target_is_directory=True)
        return "symlink"
    except (OSError, NotImplementedError):
        pass
    if os.name != "nt" or not shutil.which("cmd"):
        return None
    made = _spawn(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                  TOOL_TIMEOUT)
    return "junction" if made.returncode == 0 else None


def test_source_directory_itself_being_an_alias_is_reported() -> None:
    """The *declared* source directory may itself be the alias.

    `resolve_within` cannot notice this: resolving is precisely what erases
    the evidence, so the alias was followed in silence while an alias one
    level deeper was reported. Containment was never at risk here, but the
    README promised aliases are never followed, and they were.
    """
    tmp_path = Path(tempfile.mkdtemp())
    try:
        engine, design, spec = _build_fixture(tmp_path)
        _assert_pristine(spec, engine, design, tmp_path)

        real = engine / "real_skills"
        shutil.move(str(engine / "data" / "skills"), str(real))
        kind = _make_dir_alias(engine / "data" / "skills", real)
        if kind is None:
            shutil.move(str(real), str(engine / "data" / "skills"))
            print("SKIP test_source_directory_itself_being_an_alias_is_reported "
                  "(cannot create a directory alias here)")
            return
        # fixture self-check: the alias is real and the entities are readable
        # through it, i.e. the old code really would have sailed on
        assert (engine / "data" / "skills" / "sw_slash.json").is_file(), \
            "alias did not take effect, so this proves nothing"

        proc = _run(spec, engine, design, tmp_path)
        assert proc.returncode == 1, (proc.returncode, proc.stdout)
        assert "unfollowed_link" in proc.stdout, proc.stdout
        assert "data/skills" in proc.stdout, proc.stdout
        assert "OK:" not in proc.stdout, proc.stdout
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)
    print(f"PASS test_source_directory_itself_being_an_alias_is_reported ({kind})")


def test_renamed_source_is_drift_not_a_crash() -> None:
    """A renamed source directory is the headline case for this whole tool.

    It used to exit 2 with one line about a missing path, throwing away the
    report that already knew about the *new* directory. What the reader
    needs is both halves at once — "classes is gone" and "classes_v2 is
    undeclared" — which together say "this was a rename, update the map".

    Also asserts the degradation stays loud: the vanished source becomes an
    empty id set so the run can finish, and that must not quietly turn into
    "this entity type no longer exists".
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        engine, design, spec = _build_fixture(tmp_path)
        _assert_pristine(spec, engine, design, tmp_path)

        shutil.move(str(engine / "data" / "classes"),
                    str(engine / "data" / "classes_v2"))
        assert not (engine / "data" / "classes").exists(), "rename did not happen"

        proc = _run(spec, engine, design, tmp_path)
        assert proc.returncode == 1, (proc.returncode, proc.stdout, proc.stderr)
        assert "Traceback" not in proc.stderr, proc.stderr
        # both halves of the rename, in one run
        assert "missing_source" in proc.stdout, proc.stdout
        assert "data/classes is declared" in proc.stdout, proc.stdout
        assert "undeclared_scope" in proc.stdout, proc.stdout
        assert "data/classes_v2" in proc.stdout, proc.stdout
        # the degradation is not silent: the mapping that still points at the
        # vanished source fails loudly rather than being skipped
        assert "unknown_id" in proc.stdout, proc.stdout
        assert "'hero'" in proc.stdout, proc.stdout
        assert "OK:" not in proc.stdout, proc.stdout

        # ...and the run really did continue: findings from *other* entity
        # types are present too, which is the whole point of not aborting
        skills = design / "snapshots" / "skills.json"
        skills.write_text(json.dumps([{"id": "a_slash"}, {"id": "b_slash"},
                                      {"id": "a_eye"}, {"id": "a_extra"}]),
                          encoding="utf-8")
        proc = _run(spec, engine, design, tmp_path)
        assert "missing_source" in proc.stdout, proc.stdout
        assert "a_extra" in proc.stdout, proc.stdout
    print("PASS test_renamed_source_is_drift_not_a_crash")


def test_deleted_snapshot_and_duplicate_ids_are_findings() -> None:
    """The other two "data disagrees with the map" cases, same treatment."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        engine, design, spec = _build_fixture(tmp_path)
        _assert_pristine(spec, engine, design, tmp_path)

        skills = design / "snapshots" / "skills.json"
        original = skills.read_text(encoding="utf-8")
        skills.unlink()
        proc = _run(spec, engine, design, tmp_path)
        assert proc.returncode == 1, (proc.returncode, proc.stdout, proc.stderr)
        assert "Traceback" not in proc.stderr, proc.stderr
        assert "missing_source" in proc.stdout, proc.stdout
        assert "snapshots/skills.json" in proc.stdout, proc.stdout
        # mappings pointing into the vanished snapshot fail loudly
        assert "unknown_id" in proc.stdout and "a_slash" in proc.stdout, \
            proc.stdout
        skills.write_text(original, encoding="utf-8")
        _assert_pristine(spec, engine, design, tmp_path)

        # duplicate id, engine side (two files claiming one id)
        (engine / "data" / "skills" / "sw_slash_copy.json").write_text(
            json.dumps({"id": "sw_slash"}), encoding="utf-8")
        proc = _run(spec, engine, design, tmp_path)
        assert proc.returncode == 1, (proc.returncode, proc.stdout)
        assert "duplicate_entity_id" in proc.stdout, proc.stdout
        assert "sw_slash" in proc.stdout, proc.stdout
        (engine / "data" / "skills" / "sw_slash_copy.json").unlink()
        _assert_pristine(spec, engine, design, tmp_path)

        # duplicate id, design side
        skills.write_text(json.dumps([{"id": "a_slash"}, {"id": "b_slash"},
                                      {"id": "a_eye"}, {"id": "a_slash"}]),
                          encoding="utf-8")
        proc = _run(spec, engine, design, tmp_path)
        assert proc.returncode == 1, (proc.returncode, proc.stdout)
        assert "duplicate_entity_id" in proc.stdout, proc.stdout
    print("PASS test_deleted_snapshot_and_duplicate_ids_are_findings")


def test_deeply_nested_json_exits_2_without_traceback() -> None:
    """A valid but absurdly nested JSON file blows the decoder's stack.

    `RecursionError` is a `RuntimeError`, so none of the OSError /
    UnicodeDecodeError / JSONDecodeError handlers caught it and it escaped
    as a traceback exiting 1 — indistinguishable from "the map has
    findings".
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        engine, design, spec = _build_fixture(tmp_path)
        _assert_pristine(spec, engine, design, tmp_path)

        depth = 20000
        payload = "[" * depth + "]" * depth
        (engine / "data" / "skills" / "sw_deep.json").write_text(
            payload, encoding="utf-8")
        proc = _run(spec, engine, design, tmp_path)
        assert proc.returncode == 2, (proc.returncode, proc.stdout, proc.stderr)
        assert "Traceback" not in proc.stderr, proc.stderr
        assert "nested too deeply" in proc.stderr, proc.stderr
    print("PASS test_deeply_nested_json_exits_2_without_traceback")


def test_file_symlink_out_of_repo_is_reported() -> None:
    """A .json symlink pointing out of the repository is not read."""
    with tempfile.TemporaryDirectory() as tmp, \
            tempfile.TemporaryDirectory() as outside:
        tmp_path = Path(tmp)
        engine, design, spec = _build_fixture(tmp_path)
        _assert_pristine(spec, engine, design, tmp_path)

        target = Path(outside) / "outsider.json"
        target.write_text(json.dumps({"id": "CANARY_OUTSIDE_REPO"}),
                          encoding="utf-8")
        link = engine / "data" / "skills" / "sw_outside.json"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError) as exc:
            print("SKIP test_file_symlink_out_of_repo_is_reported "
                  f"(cannot create a file symlink here: {exc})")
            return
        assert link.is_file(), "symlink did not take effect"

        proc = _run(spec, engine, design, tmp_path)
        assert proc.returncode == 1, (proc.returncode, proc.stdout)
        assert "path_escape" in proc.stdout, proc.stdout
        assert "CANARY_OUTSIDE_REPO" not in proc.stdout, proc.stdout
    print("PASS test_file_symlink_out_of_repo_is_reported")


def test_unreadable_directory_exits_2_not_silently_empty() -> None:
    """An unreadable directory must not read as "this directory is empty".

    Windows-only in practice (it needs `icacls`), and skipped elsewhere
    rather than faked: a mocked permission error would only prove the mock
    works. The measured behaviour this guards against is real — `rglob`
    over a denied directory yields nothing at all, so every entity in it
    disappears while the run still looks healthy.
    """
    if os.name != "nt" or not shutil.which("icacls"):
        print("SKIP test_unreadable_directory_exits_2_not_silently_empty "
              "(needs Windows icacls)")
        return
    # Managed by hand rather than with `TemporaryDirectory`: a denied ACL
    # makes the automatic cleanup itself raise, which would turn a passing
    # test into a teardown failure.
    tmp_path = Path(tempfile.mkdtemp())
    target = tmp_path / "engine" / "data" / "skills"
    user = os.environ.get("USERNAME", "")
    try:
        engine, design, spec = _build_fixture(tmp_path)
        _assert_pristine(spec, engine, design, tmp_path)

        denied = _spawn(["icacls", str(target), "/deny", f"{user}:(RX)"],
                        TOOL_TIMEOUT)
        if denied.returncode != 0:
            print("SKIP test_unreadable_directory_exits_2_not_silently_empty "
                  f"(icacls deny failed: {denied.stdout.strip()})")
            return
        # Fixture self-check: the denial really took effect. Done by trying
        # a real listing, not `os.access` — on Windows that only reports the
        # read-only attribute and answers "readable" for a directory the ACL
        # denies, which would leave this guard permanently satisfied and
        # useless.
        try:
            os.listdir(target)
        except PermissionError:
            pass
        else:
            print("SKIP test_unreadable_directory_exits_2_not_silently_empty "
                  "(icacls deny did not take effect; likely elevated or an "
                  "unusual account setup)")
            return

        proc = _run(spec, engine, design, tmp_path)
        assert proc.returncode == 2, (proc.returncode, proc.stdout, proc.stderr)
        assert "Traceback" not in proc.stderr, proc.stderr
        assert "cannot read" in proc.stderr, proc.stderr
        # the failure mode being guarded against: a green run in which the
        # skills simply ceased to exist
        assert "OK:" not in proc.stdout, proc.stdout
    finally:
        # Teardown runs even when the body already failed, so it must not be
        # the thing that hangs: a stuck ACL reset would leave the deny in
        # place *and* stall the suite.
        _spawn(["icacls", str(target), "/reset"], TOOL_TIMEOUT)
        shutil.rmtree(tmp_path, ignore_errors=True)
    print("PASS test_unreadable_directory_exits_2_not_silently_empty")


def test_mapping_ids_must_be_lists() -> None:
    """A bare string is iterable; a single-character id used to slip through."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        engine, design, spec = _build_fixture(tmp_path)
        _assert_pristine(spec, engine, design, tmp_path)

        for value in ("sw_slash", "x", 3, {"a": 1}):
            broken = copy.deepcopy(spec)
            broken["mappings"][0]["engine_ids"] = value
            proc = _run(broken, engine, design, tmp_path)
            assert proc.returncode == 1, (value, proc.stdout)
            assert "must be a list of ids" in proc.stdout, (value, proc.stdout)

        blank = copy.deepcopy(spec)
        blank["mappings"][0]["design_ids"] = ["a_slash", ""]
        proc = _run(blank, engine, design, tmp_path)
        assert proc.returncode == 1, proc.stdout
        assert "expected a non-empty string id" in proc.stdout, proc.stdout
    print("PASS test_mapping_ids_must_be_lists")


def test_bad_cardinality_is_reported() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        engine, design, spec = _build_fixture(tmp_path)
        _assert_pristine(spec, engine, design, tmp_path)

        broken = copy.deepcopy(spec)
        broken["mappings"][0]["cardinality"] = "1:1"
        proc = _run(broken, engine, design, tmp_path)
        assert proc.returncode == 1, proc.stdout
        assert "bad_cardinality" in proc.stdout, proc.stdout
    print("PASS test_bad_cardinality_is_reported")


def test_undeclared_scope_is_reported() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        engine, design, spec = _build_fixture(tmp_path)
        _assert_pristine(spec, engine, design, tmp_path)

        (engine / "data" / "buildings").mkdir()
        (engine / "data" / "buildings" / "hut.json").write_text(
            json.dumps({"id": "hut"}), encoding="utf-8")
        proc = _run(spec, engine, design, tmp_path)
        assert proc.returncode == 1, proc.stdout
        assert "undeclared_scope" in proc.stdout, proc.stdout
        assert "data/buildings" in proc.stdout, proc.stdout

        (design / "snapshots" / "terms.json").write_text(
            json.dumps([]), encoding="utf-8")
        proc = _run(spec, engine, design, tmp_path)
        assert "snapshots/terms.json" in proc.stdout, proc.stdout
    print("PASS test_undeclared_scope_is_reported")


def test_missing_env_exits_2_with_guidance() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        engine, design, spec = _build_fixture(tmp_path)
        _assert_pristine(spec, engine, design, tmp_path)

        env = {k: v for k, v in os.environ.items()
               if k not in ("SOT_ENGINE_REPO", "SOT_DESIGN_REPO")}
        proc = _run(spec, engine, design, tmp_path, env=env,
                    pass_repo_flags=False)
        assert proc.returncode == 2, (proc.returncode, proc.stdout, proc.stderr)
        assert "SOT_ENGINE_REPO" in proc.stderr, proc.stderr
        assert "SOT_DESIGN_REPO" in proc.stderr, proc.stderr
        assert "Traceback" not in proc.stderr, proc.stderr

        env["SOT_ENGINE_REPO"] = str(tmp_path / "nowhere")
        env["SOT_DESIGN_REPO"] = str(design)
        proc = _run(spec, engine, design, tmp_path, env=env,
                    pass_repo_flags=False)
        assert proc.returncode == 2, (proc.returncode, proc.stderr)
        assert "does not exist" in proc.stderr, proc.stderr
    print("PASS test_missing_env_exits_2_with_guidance")


def test_packaged_map_is_wellformed() -> None:
    """Structure-only check of the shipped map; no repositories needed."""
    spec = json.loads(PACKAGE_MAP.read_text(encoding="utf-8"))
    reasons = set(spec["reason_codes"])
    types = {e["type"] for e in spec["entity_types"]}
    assert spec["mappings"], "shipped map has no mappings at all"
    for mapping in spec["mappings"]:
        assert mapping["type"] in types, mapping
        assert mapping["engine_ids"] and mapping["design_ids"], mapping
        assert mapping["basis"].strip(), mapping
    for item in spec["unmapped"]:
        assert item["type"] in types, item
        assert item["side"] in ("engine", "design"), item
        assert item["reason"] in reasons, item
        if item["reason"] == "engine_implements_as_field":
            assert item.get("engine_carrier"), item
    used = {i["reason"] for i in spec["unmapped"]}
    assert used <= reasons, used - reasons
    print("PASS test_packaged_map_is_wellformed "
          f"({len(spec['mappings'])} mappings, {len(spec['unmapped'])} unmapped)")


def test_real_repositories_when_available() -> None:
    engine = os.environ.get("SOT_ENGINE_REPO", "")
    design = os.environ.get("SOT_DESIGN_REPO", "")
    if not (engine and design and Path(engine).is_dir()
            and Path(design).is_dir()):
        print("SKIP test_real_repositories_when_available "
              "(SOT_ENGINE_REPO / SOT_DESIGN_REPO not both set to real dirs)")
        return
    proc = _spawn([sys.executable, "-m", "scripts.sot_id_map"],
                  CHECKER_TIMEOUT, cwd=ROOT, env=dict(os.environ),
                  encoding="utf-8", errors="replace")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    print("PASS test_real_repositories_when_available")


def test_non_ascii_finding_is_printable() -> None:
    """A finding naming a non-ASCII id must print, not raise.

    The design library keys its tag registry by Chinese strings, so this is
    a live path, not a hypothetical one. Run with the console codepage
    default (no `PYTHONIOENCODING`) so a regression here really would fail.
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        engine, design, spec = _build_fixture(tmp_path)
        (design / "snapshots" / "rules.json").write_text(
            json.dumps([{"code": "R1.1"}, {"code": "破格"}],
                       ensure_ascii=False), encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if k != "PYTHONIOENCODING"}
        proc = _run(spec, engine, design, tmp_path, env=env)
        assert proc.returncode == 1, (proc.returncode, proc.stdout, proc.stderr)
        assert "Traceback" not in proc.stderr, proc.stderr
        assert "uncovered_id" in proc.stdout and "破格" in proc.stdout, \
            proc.stdout
    print("PASS test_non_ascii_finding_is_printable")


def main() -> int:
    test_help()
    test_fixture_passes_clean()
    with tempfile.TemporaryDirectory() as outside:
        # A canary living outside both repositories. Any test that finds this
        # string in the checker's output has caught a containment escape.
        outside_path = Path(outside)
        (outside_path / "leak.json").write_text(
            json.dumps({"id": "CANARY_OUTSIDE_REPO"}), encoding="utf-8")
        test_declared_path_cannot_escape_the_repository(outside_path)
    test_removed_mapping_is_reported()
    test_new_engine_file_is_reported()
    test_nonexistent_id_is_reported()
    test_bad_reason_code_is_reported()
    test_duplicate_claim_is_reported()
    test_missing_carrier_is_reported()
    test_cross_side_contradiction_is_reported()
    test_escape_hatch_usage_is_always_reported()
    test_stale_escape_hatch_is_reported()
    test_excluded_paths_must_be_a_dict_with_reasons()
    test_non_utf8_file_exits_2_without_traceback()
    test_malformed_map_reports_findings_not_traceback()
    test_empty_and_odd_ids_rejected_on_both_sides()
    test_mapping_ids_must_be_lists()
    test_null_is_not_a_justification()
    test_empty_repo_flag_is_an_error_not_a_fallback()
    test_directory_symlink_is_reported_not_skipped()
    test_source_directory_itself_being_an_alias_is_reported()
    test_renamed_source_is_drift_not_a_crash()
    test_deleted_snapshot_and_duplicate_ids_are_findings()
    test_deeply_nested_json_exits_2_without_traceback()
    test_file_symlink_out_of_repo_is_reported()
    test_unreadable_directory_exits_2_not_silently_empty()
    test_carrier_field_must_exist()
    test_file_without_id_is_reported()
    test_scope_guard_covers_loose_and_nested_files()
    test_bad_cardinality_is_reported()
    test_undeclared_scope_is_reported()
    test_missing_env_exits_2_with_guidance()
    test_non_ascii_finding_is_printable()
    test_packaged_map_is_wellformed()
    test_real_repositories_when_available()
    print("ALL SMOKE PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
