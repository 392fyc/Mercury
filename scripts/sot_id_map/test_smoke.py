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
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
PACKAGE_MAP = Path(__file__).resolve().parent / "id_map.json"


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
    run_env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(argv, capture_output=True, text=True, cwd=ROOT,
                          env=run_env)


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
    proc = subprocess.run([sys.executable, "-m", "scripts.sot_id_map", "--help"],
                          capture_output=True, text=True, cwd=ROOT)
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
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run([sys.executable, "-m", "scripts.sot_id_map"],
                          capture_output=True, text=True, cwd=ROOT, env=env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    print("PASS test_real_repositories_when_available")


def main() -> int:
    test_help()
    test_fixture_passes_clean()
    test_removed_mapping_is_reported()
    test_new_engine_file_is_reported()
    test_nonexistent_id_is_reported()
    test_bad_reason_code_is_reported()
    test_duplicate_claim_is_reported()
    test_missing_carrier_is_reported()
    test_bad_cardinality_is_reported()
    test_undeclared_scope_is_reported()
    test_missing_env_exits_2_with_guidance()
    test_packaged_map_is_wellformed()
    test_real_repositories_when_available()
    print("ALL SMOKE PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
