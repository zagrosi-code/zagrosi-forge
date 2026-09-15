from __future__ import annotations

import json
from pathlib import Path

import pytest
from runtime_support import load_runtime
from test_compact_plan import SECTION, make_plan

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def forge():
    return load_runtime(ROOT / "scripts/zagrosi_skills.py")


def make_legacy(path: Path, filename: str) -> Path:
    make_plan(path)
    index = path / "sections/index.md"
    index.write_text(index.read_text().split("END_FORGE_META -->\n", 1)[1])
    (path / filename).write_text((path / "sections" / f"{SECTION}.md").read_text())
    return path


def write_suite(root: Path, payload) -> None:
    (root / "evals").mkdir(parents=True, exist_ok=True)
    (root / "evals/suite.json").write_text(json.dumps(payload))


def test_suite_preserves_order_and_explicit_depth_for_compact_and_legacy(forge, tmp_path, capsys):
    make_plan(tmp_path / "compact", "deep")
    make_legacy(tmp_path / "legacy", "claude-plan.md")
    write_suite(tmp_path, {"benchmarks": [
        {"name": "explicit", "planning_dir": "../compact", "depth": "standard"},
        {"name": "metadata", "planning_dir": "../compact"},
        {"name": "legacy", "planning_dir": "../legacy"},
    ]})
    mode, _, _, benchmarks, errors = forge.evaluations.eval_suite_benchmarks(tmp_path, "lean")
    assert mode == "suite" and not errors
    assert [(row["name"], row["depth"]) for row in benchmarks] == [("explicit", "standard"), ("metadata", "deep"), ("legacy", "lean")]
    code = forge.entrypoint.main(["eval-suite", "--examples-dir", str(tmp_path)])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0, payload
    assert [row["name"] for row in payload["rows"]] == ["explicit", "metadata", "legacy"]


def test_glob_discovers_each_valid_plan_once_and_skips_invalid_fixture_paths(forge, tmp_path):
    make_legacy(tmp_path / "a-claude", "claude-plan.md")
    make_plan(tmp_path / "b-compact", "deep")
    codex = make_legacy(tmp_path / "c-codex", "codex-plan.md")
    (codex / "claude-plan.md").write_text("# Superseded legacy plan\n")
    make_plan(tmp_path / "invalid/excluded")
    incomplete = make_plan(tmp_path / "d-incomplete")
    (incomplete / "sections" / f"{SECTION}.md").unlink()
    unmarked = make_plan(tmp_path / "e-unmarked")
    index = unmarked / "sections/index.md"
    index.write_text(index.read_text().split("END_FORGE_META -->\n", 1)[1])
    malformed = make_plan(tmp_path / "f-malformed")
    index = malformed / "sections/index.md"
    index.write_text(index.read_text().replace('"lean"', '"unknown"'))
    empty = make_legacy(tmp_path / "g-empty", "codex-plan.md")
    (empty / "codex-plan.md").write_text("")
    mode, _, _, benchmarks, errors = forge.evaluations.eval_suite_benchmarks(tmp_path, "standard")
    assert mode == "glob" and not errors
    assert [(row["name"], row["depth"]) for row in benchmarks] == [("a-claude", "standard"), ("b-compact", "deep"), ("c-codex", "standard")]


def test_glob_order_uses_canonical_plan_paths_when_legacy_alias_exists(forge, tmp_path):
    parent = make_legacy(tmp_path / "parent", "codex-plan.md")
    (parent / "claude-plan.md").write_text("# Superseded legacy plan\n")
    child = make_legacy(parent / "cm-child", "codex-plan.md")
    _, _, _, benchmarks, errors = forge.evaluations.eval_suite_benchmarks(tmp_path, "lean")
    assert not errors
    assert [row["planning_dir"] for row in benchmarks] == [child, parent]


@pytest.mark.parametrize("malformation", ["missing-section", "bad-depth", "empty-physical"])
def test_listed_invalid_compact_fixture_returns_suite_error(forge, tmp_path, malformation):
    planning = make_plan(tmp_path / "fixture")
    if malformation == "missing-section":
        (planning / "sections" / f"{SECTION}.md").unlink()
    elif malformation == "bad-depth":
        index = planning / "sections/index.md"
        index.write_text(index.read_text().replace('"lean"', '"unknown"'))
    else:
        (planning / "codex-plan.md").write_text("")
    write_suite(tmp_path, {"benchmarks": [{"name": "bad", "planning_dir": "../fixture"}]})
    _, _, _, benchmarks, errors = forge.evaluations.eval_suite_benchmarks(tmp_path, "lean")
    assert not benchmarks
    assert errors and errors[0]["name"] == "bad"


@pytest.mark.parametrize("payload", [[], {"benchmarks": "fixture"}, {"benchmarks": [], "snapshots_dir": []}])
def test_malformed_suite_returns_errors_instead_of_traceback(forge, tmp_path, payload):
    write_suite(tmp_path, payload)
    _, _, _, benchmarks, errors = forge.evaluations.eval_suite_benchmarks(tmp_path, "lean")
    assert not benchmarks and errors
