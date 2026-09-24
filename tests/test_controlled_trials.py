"""One fixed evaluator, balanced arms, honest usage and source-bound blind reviews."""
import json

import pytest

from test_coding_trials import trials, write_cleanup, write_review
from test_trial_matrix import matrix
from coding_trial_comparison import CRITERIA, packets, reviews
from coding_trial_runner import Telemetry, command_phase


@pytest.mark.parametrize("depth", ["lean", "standard", "deep"])
def test_plain_agent_keeps_behavior_and_cleanup_checks_without_forge(tmp_path, depth):
    trial = tmp_path / "plain"
    trials.prepare(trial, "cleanup", depth, plain_agent=True)
    prompt = (trial / "prompt.md").read_text()
    assert "Use your normal engineering workflow" in prompt
    assert "applicable Forge skills" not in prompt
    write_cleanup(trial)
    result = trials.check(trial, review=write_review(trial))
    assert result["success"], result
    assert result["behavior"]["success"] and result["cleanup"]["success"]
    assert result["workflow"] == {"success": None, "status": "not_applicable"}
    assert result["plugin_provenance"]["status"] == "not_applicable"
    (trial / "workspace/src/ledger.py").write_text("raise RuntimeError('broken')")
    assert not trials.check(trial, review=trial / "review.json")["success"]


def test_plugin_under_test_cannot_replace_evaluator_or_fixture(tmp_path):
    plugin = tmp_path / "previous"
    (plugin / "scripts/forge").mkdir(parents=True)
    (plugin / "scripts/zagrosi_skills.py").write_text("raise RuntimeError('must not judge itself')")
    (plugin / "scripts/forge/old.py").write_text("OLD = True")
    (plugin / "tools").mkdir()
    (plugin / "tools/coding_trials.py").write_text("raise RuntimeError('foreign evaluator')")
    trial = tmp_path / "trial"
    trials.prepare(trial, "cleanup", plugin_root=plugin)
    assert str(plugin) in (trial / "prompt.md").read_text()
    record = json.loads((trial / "trial.json").read_text())
    assert record["plugin_sha256"]["scripts/forge/old.py"]
    assert record["fixture_sha256"] == trials.files(trials.PACK / "fixture")
    result = trials.check(trial)
    assert result["behavior"]["success"]
    assert result["plugin_provenance"]["success"]
    assert not result["workflow"]["success"]
    assert "must not judge itself" not in result["workflow"]["process"]["stderr"]


def test_matrix_invokes_its_own_evaluator(tmp_path, monkeypatch):
    evaluator = tmp_path / "evaluator"
    (evaluator / "tools").mkdir(parents=True)
    (evaluator / "tools/coding_trials.py").write_text('''import json, pathlib, sys
trial = pathlib.Path(sys.argv[2]); trial.mkdir()
(trial / 'argv.json').write_text(json.dumps(sys.argv))
raise SystemExit(7)
''')
    previous = tmp_path / "previous"
    previous.mkdir()
    monkeypatch.setattr(matrix, "ROOT", evaluator)
    matrix.run_trial(tmp_path, previous, {"id": "one", "case": "godfile", "depth": "deep", "arm": "previous"}, ["unused"], 20)
    assert json.loads((tmp_path / "one/attempt.json").read_text())["returncode"] == 7
    arguments = json.loads((tmp_path / "one/argv.json").read_text())
    assert arguments[arguments.index("--plugin-root") + 1] == str(previous)


def test_comparison_rotates_arms_and_preserves_every_case_depth_repeat():
    items = matrix.schedule(["godfile", "import-preview"], ["lean", "standard", "deep"], 2, True)
    assert len(items) == 36 and len({row["id"] for row in items}) == 36
    for start in range(0, len(items), 3):
        block = items[start:start + 3]
        assert len({row["block"] for row in block}) == 1
        assert {row["arm"] for row in block} == {"previous", "current", "plain"}
    assert [items[index]["arm"] for index in (0, 3, 6)] == ["previous", "current", "plain"]


def test_plain_resume_rejected_before_creating_trial(tmp_path):
    with pytest.raises(ValueError, match="no comparable"):
        trials.prepare(tmp_path / "trial", "resume", plain_agent=True)
    assert not (tmp_path / "trial").exists()


def test_telemetry_preserves_unknowns_and_counts_observed_retries():
    telemetry = Telemetry()
    assert telemetry.summary()["totals"]["input_tokens"] is None
    for index, code in enumerate((1, 0, 0)):
        command = "python -m unittest" if index < 2 else "forge plan-setup"
        item = {"id": str(index), "type": "command_execution", "command": command}
        telemetry.observe({"type": "item.started", "item": item}, index * 2)
        telemetry.observe({"type": "item.completed", "item": {**item, "exit_code": code,
                            "aggregated_output": "é"}}, index * 2 + 1)
    for usage in ({"input_tokens": 100, "cached_input_tokens": 60, "output_tokens": 9},
                  {"input_tokens": 50, "cached_input_tokens": 10, "output_tokens": 4}):
        telemetry.observe({"type": "turn.completed", "usage": usage}, 10)
    result = telemetry.summary()
    assert result["totals"] == {"input_tokens": 150, "cached_input_tokens": 70,
        "uncached_input_tokens": 80, "output_tokens": 13, "reasoning_output_tokens": None}
    assert result["observed_command_retries"] == 1 and result["api_retries"] is None
    assert result["commands_by_phase"]["verification"] == {"commands": 2,
        "observed_output_bytes": 4, "observed_command_seconds": 2, "failed_commands": 1}
    telemetry.observe({"type": "turn.completed", "usage": {"input_tokens": 5}}, 11)
    assert telemetry.summary()["totals"]["cached_input_tokens"] is None
    assert telemetry.summary()["totals"]["uncached_input_tokens"] is None


@pytest.mark.parametrize(("command", "phase"), [
    ("python forge.py postflight --phase plan", "forge_planning"),
    ("python forge.py implement-record-section", "forge_implementation"),
    ("node --test tests/a.js", "verification"), ("cat src/a.py", "other"),
])
def test_phase_labels_describe_commands_only(command, phase):
    assert command_phase(command) == phase


def make_blind_matrix(directory):
    items = matrix.schedule(["cleanup"], ["standard"], 1, True)
    (directory / "matrix.json").write_text(json.dumps({"comparison": True, "seed": 4,
        "evaluator_root": str(trials.ROOT), "cases": trials.CASES, "trials": items}))
    for item in items:
        trial = directory / item["id"]
        trials.prepare(trial, "cleanup", plain_agent=True)
        write_cleanup(trial)
        trials.check(trial)
    packets(directory)
    return directory / "blind/cleanup-standard-1/review.json"


def complete_review(path):
    data = json.loads(path.read_text())
    data.update(reviewer="independent reader", independent=True, preferred=["A", "B"],
                rationale="Both candidates share the calculation and retain direct dispatch.")
    for row in data["candidates"].values():
        row.update(verdict="pass", criteria={name: f"src/ledger.py: {name} checked against original behavior" for name in CRITERIA})
        row["cleanup"].update(meaningful=True, changed_files=["src/ledger.py"],
            rationale="Shared calculation removes duplicate loops", regression_evidence="Existing tests and independent oracle pass")
    path.write_text(json.dumps(data))


def test_blind_packets_hide_arms_and_reviews_bind_actual_code(tmp_path):
    path = make_blind_matrix(tmp_path)
    packet = path.parent
    assert {p.name for p in packet.iterdir()} == {"A", "B", "C", "baseline", "README.md", "review.json"}
    assert not (packet / "A/.planning").exists()
    assert not reviews(tmp_path)[0]["valid"]
    complete_review(path)
    assert reviews(tmp_path, apply=True)[0]["valid"]
    assert len(list(tmp_path.glob("cleanup-*/review.json"))) == 3
    with (packet / "A/src/ledger.py").open("a") as handle:
        handle.write("\n# Post-review edit\n")
    assert not reviews(tmp_path)[0]["valid"]
    with pytest.raises(ValueError, match="stale"):
        reviews(tmp_path, apply=True)


def test_pinned_runner_records_requested_configuration_and_nonzero_exit(tmp_path, monkeypatch):
    import io
    from types import SimpleNamespace
    import coding_trial_runner as runner
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    monkeypatch.setattr(runner.sys, "argv", ["runner", "--model", "pinned-model", "--effort", "medium", "--codex", "exact-binary"])
    monkeypatch.setattr(runner.sys, "stdin", io.StringIO("bounded task"))
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="codex test-version"))
    def launch(command, **kwargs):
        assert command[:3] == ["exact-binary", "exec", "--ignore-user-config"]
        assert "--ignore-rules" in command and command[command.index("--model") + 1] == "pinned-model"
        assert 'model_reasoning_effort="medium"' in command
        return SimpleNamespace(stdin=io.StringIO(), stdout=[json.dumps({"type": "turn.completed", "usage": {
            "input_tokens": 10, "cached_input_tokens": 4, "output_tokens": 2}}) + "\n"], wait=lambda: 7)
    monkeypatch.setattr(runner.subprocess, "Popen", launch)
    assert runner.main() == 7
    telemetry = json.loads((tmp_path / "telemetry.json").read_text())
    assert telemetry["model"] == "pinned-model" and telemetry["effort"] == "medium"
    assert telemetry["totals"]["uncached_input_tokens"] == 6
    assert telemetry["returncode"] == 7 and telemetry["api_retries"] is None
    assert not (tmp_path / "telemetry.tmp").exists()


def test_comparison_needs_every_blind_review_even_when_trial_checks_pass(tmp_path, monkeypatch):
    items = matrix.schedule(["godfile", "import-preview"], ["standard"], 1, True)
    (tmp_path / "matrix.json").write_text(json.dumps({"comparison": True, "trials": items,
        "plugin_root": str(trials.ROOT), "runner": ["pinned"]}))
    for item in items:
        trial = tmp_path / item["id"]
        trial.mkdir()
        (trial / "attempt.json").write_text('{"returncode": 0}')
        (trial / "result.json").write_text('{"success": true, "runner": {"returncode": 0}}')
    for rows, expected in (([], False), ([{"block": items[0]["block"], "valid": True}], False),
                           ([{"block": items[0]["block"], "valid": True}, {"block": items[3]["block"], "valid": True}], True)):
        monkeypatch.setattr(matrix, "reviews", lambda directory: rows)
        result = matrix.report(tmp_path)
        assert result["success"] is expected
        assert result["comparative_review"] == {"expected": 2, "completed": len(rows), "complete": expected}


def test_failed_attempt_without_result_does_not_leave_partial_blind_packet(tmp_path):
    items = matrix.schedule(["cleanup"], ["standard"], 1, True)
    (tmp_path / "matrix.json").write_text(json.dumps({"comparison": True, "trials": items}))
    with pytest.raises(ValueError, match="Cannot rank unchecked attempt"):
        packets(tmp_path)
    assert not (tmp_path / "blind").exists()
    assert not (tmp_path / "blind-key.json").exists()


def test_deleted_block_review_remains_in_comparison_denominator(tmp_path):
    path = make_blind_matrix(tmp_path)
    manifest = json.loads((tmp_path / "matrix.json").read_text())
    manifest.update(plugin_root=str(trials.ROOT), runner=["pinned"])
    (tmp_path / "matrix.json").write_text(json.dumps(manifest))
    complete_review(path)
    assert matrix.report(tmp_path)["comparative_review"] == {"expected": 1, "completed": 1, "complete": True}
    path.unlink()
    result = matrix.report(tmp_path)
    assert not result["success"]
    assert result["comparative_review"] == {"expected": 1, "completed": 0, "complete": False}
