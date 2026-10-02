"""Public command sequences preserve target, dependency and evidence invariants."""
import json
from pathlib import Path
import sys

import pytest

from forge_test_helpers import ROOT
from runtime_support import load_runtime
from test_compact_plan import SECTION, make_plan
from test_mutable_state_safety import add_dependent_section, record_args, setup_args


@pytest.mark.parametrize("depth", ["lean", "standard", "deep"])
def test_interrupted_dependency_sequence_keeps_one_effective_target(tmp_path, monkeypatch, capsys, depth):
    forge = load_runtime(ROOT / "scripts/zagrosi_skills.py")
    planning = make_plan(tmp_path / "planning", depth)
    second = add_dependent_section(planning)
    target = tmp_path / "repo with spaces"
    target.mkdir()
    source = target / "source.py"
    source.write_text("value = 1\n")
    user_notes = target / "notes.txt"
    user_notes.write_text("Keep my unfinished work\n")

    def invoke(*argv):
        code = forge.entrypoint.main(list(argv))
        payload = json.loads(capsys.readouterr().out)
        assert code == 0, payload
        return payload

    # Direct recording precedes setup; a later session starts from another cwd.
    invoke(*record_args(planning, target))
    invoke(*setup_args(planning, target), "--depth", depth)
    monkeypatch.chdir(tmp_path)
    invoke("implement-progress", "--planning-dir", str(planning), "--section", second,
           "--stage", "red", "--command", "python -m unittest", "--result", "Missing export reproduced")
    progress_path = planning / "implementation/forge-progress.json"
    original = json.loads(progress_path.read_text())["events"][0]
    for _ in range(2):
        next_work = invoke("next-section", "--planning-dir", str(planning))
        status = invoke("status", "--path", str(planning))
        parallel = invoke("parallel-plan", "--planning-dir", str(planning))
        assert next_work["next_section"] == status["next_section"] == second
        assert parallel["completed_sections"] == [SECTION]
        assert parallel["layers"] == [[second]]
        assert next_work["resume"]["stage"] == "red"
        assert next_work["resume"]["evidence_current"]
        assert next_work["commands"]["record"][next_work["commands"]["record"].index("--target-dir") + 1] == str(target)
    assert json.loads(progress_path.read_text())["events"] == [original]

    receipt = invoke("implement-verify", "--planning-dir", str(planning), "--section", second,
                     "--integration", "--", sys.executable, "-c", "from pathlib import Path; assert Path('source.py').read_text() == 'value = 1\\n'")
    invoke("implement-record-section", "--sections-dir", str(planning / "sections"), "--section", second,
           "--review-status", "pass", "--verification-receipt", receipt["receipt_path"], "--flight", "off")
    for argv in (setup_args(planning), ["next-section", "--planning-dir", str(planning)],
                 ["status", "--path", str(planning)]):
        payload = invoke(*argv)
        assert payload["next_section"] is None
        assert payload["integration_verification"]["success"]
        assert "--run-tests" not in payload["commands"]["postflight"]
    assert invoke("parallel-plan", "--planning-dir", str(planning))["layers"] == []

    source.write_text("value = 2\n")
    assert not invoke("status", "--path", str(planning))["integration_verification"]["success"]
    assert json.loads(progress_path.read_text())["events"][0] == original
    assert user_notes.read_text() == "Keep my unfinished work\n"


@pytest.mark.parametrize("depth", ["lean", "standard", "deep"])
def test_retarget_then_corruption_fails_without_erasing_prior_evidence(tmp_path, capsys, depth):
    forge = load_runtime(ROOT / "scripts/zagrosi_skills.py")
    planning = make_plan(tmp_path / "planning", depth)
    original, replacement = tmp_path / "old", tmp_path / "new"
    original.mkdir()
    replacement.mkdir()
    for argv in (setup_args(planning, original), record_args(planning), setup_args(planning, replacement)):
        assert forge.entrypoint.main(argv) == 0
        capsys.readouterr()
    state = planning / "implementation/zagrosi_implement_state.json"
    state.write_text(state.read_text()[:-2])
    before = {p: p.read_bytes() for p in planning.rglob("*") if p.is_file()}
    commands = (setup_args(planning), record_args(planning), ["next-section", "--planning-dir", str(planning)],
                ["status", "--path", str(planning)], ["parallel-plan", "--planning-dir", str(planning)],
                ["postflight", "--phase", "implement", "--planning-dir", str(planning), "--strict"])
    for argv in commands:
        assert forge.entrypoint.main(argv) != 0
        assert json.loads(capsys.readouterr().out)["success"] is False
        assert {p: p.read_bytes() for p in planning.rglob("*") if p.is_file()} == before
