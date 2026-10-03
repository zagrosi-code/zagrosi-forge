"""One fixed evaluator, balanced arms, honest usage and source-bound blind reviews."""
import json

import pytest

from test_coding_trials import trials, write_cleanup, write_review
from test_trial_matrix import matrix
from coding_trial_comparison import CRITERIA, packets, reviews
from coding_trial_runner import Telemetry, command_phase


@pytest.mark.parametrize("plain", [False, True])
@pytest.mark.parametrize("failure", [None, "behavior", "scope", "review", "runner", "evaluator"])
def test_common_quality_uses_the_same_nonworkflow_rules_for_every_arm(tmp_path, plain, failure):
    trial = tmp_path / "trial"
    trials.prepare(trial, "cleanup", plain_agent=plain)
    write_cleanup(trial)
    review = write_review(trial)
    if failure == "behavior":
        source = trial / "workspace/src/ledger.py"
        original = source.read_text()
        changed = original.replace("Total: ", "TOTAL: ")
        assert changed != original
        source.write_text(changed)
        review = write_review(trial)
    elif failure == "scope":
        (trial / "workspace/unrelated.txt").write_text("outside scope")
    elif failure == "review":
        review = None
    elif failure in {"runner", "evaluator"}:
        record = json.loads((trial / "trial.json").read_text())
        record.update({"runner": {"returncode": 7}} if failure == "runner" else {"oracle_sha256": "stale"})
        (trial / "trial.json").write_text(json.dumps(record))
    result = trials.check(trial, review=review)
    assert result["common_quality"] == {"success": failure is None}
    assert result["success"] is (plain and failure is None)


def test_resume_protocol_failure_still_blocks_delivery_but_not_common_code_quality(tmp_path, monkeypatch):
    trial = tmp_path / "trial"
    trials.prepare(trial, "cleanup")
    write_cleanup(trial)
    monkeypatch.setattr(trials, "workflow_verdict", lambda *args: {"success": True})
    monkeypatch.setattr(trials, "resume_verdict", lambda *args: {"success": False})
    result = trials.check(trial, review=write_review(trial))
    assert not result["success"]
    assert result["common_quality"] == {"success": True}
    assert result["resume"]["success"] is False


def test_common_quality_retains_plugin_source_provenance(tmp_path):
    trial = tmp_path / "trial"
    trials.prepare(trial, "cleanup")
    write_cleanup(trial)
    record = json.loads((trial / "trial.json").read_text())
    record["plugin_sha256"]["scripts/zagrosi_skills.py"] = "changed-source"
    (trial / "trial.json").write_text(json.dumps(record))
    result = trials.check(trial, review=write_review(trial))
    assert result["behavior"]["success"] and result["cleanup"]["success"]
    assert not result["plugin_provenance"]["success"]
    assert not result["common_quality"]["success"]


@pytest.mark.parametrize("depth", ["lean", "standard", "deep"])
def test_plain_agent_keeps_behavior_and_cleanup_checks_without_forge(tmp_path, depth):
    trial = tmp_path / "plain"
    trials.prepare(trial, "cleanup", depth, plain_agent=True)
    prompt = (trial / "prompt.md").read_text()
    assert "Use your normal engineering workflow" in prompt
    assert "applicable Forge skills" not in prompt
    assert "Start with" not in prompt
    write_cleanup(trial)
    result = trials.check(trial, review=write_review(trial))
    assert result["success"], result
    assert result["behavior"]["success"] and result["cleanup"]["success"]
    assert result["workflow"] == {"success": None, "status": "not_applicable"}
    assert result["plugin_provenance"]["status"] == "not_applicable"
    (trial / "workspace/src/ledger.py").write_text("raise RuntimeError('broken')")
    assert not trials.check(trial, review=trial / "review.json")["success"]


@pytest.mark.parametrize("has_router", [False, True])
def test_plugin_under_test_cannot_replace_evaluator_or_fixture(tmp_path, has_router):
    plugin = tmp_path / "previous"
    (plugin / "scripts/forge").mkdir(parents=True)
    (plugin / "scripts/zagrosi_skills.py").write_text("raise RuntimeError('must not judge itself')")
    (plugin / "scripts/forge/old.py").write_text("OLD = True")
    (plugin / "tools").mkdir()
    (plugin / "tools/coding_trials.py").write_text("raise RuntimeError('foreign evaluator')")
    router = plugin / "skills/zagrosi-forge/SKILL.md"
    if has_router:
        router.parent.mkdir(parents=True)
        router.write_text("Use the selected Forge workflow.")
    trial = tmp_path / "trial"
    trials.prepare(trial, "cleanup", plugin_root=plugin)
    prompt = (trial / "prompt.md").read_text()
    assert str(plugin) in prompt
    assert (f"Start with {router}" in prompt) is has_router
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


def test_current_plain_comparison_rotates_pairs_without_legacy_arm():
    items = matrix.schedule(["godfile", "typescript-access"], ["standard"], 2, True, previous=False)
    assert len(items) == len({row["id"] for row in items}) == 8
    for start in range(0, len(items), 2):
        pair = items[start:start + 2]
        assert len({row["block"] for row in pair}) == 1
        assert {row["arm"] for row in pair} == {"current", "plain"}
    assert [items[index]["arm"] for index in (0, 2, 4, 6)] == ["current", "plain", "current", "plain"]


def test_compare_cli_without_previous_pins_four_matched_attempts(tmp_path, monkeypatch):
    destination = tmp_path / "comparison"
    monkeypatch.setattr(matrix.sys, "argv", ["trial_matrix", "compare", str(destination),
        "--model", "exact-model", "--effort", "medium", "--cases", "godfile", "typescript-access",
        "--depths", "standard", "--repeats", "1", "--timeout", "600"])
    calls = []
    monkeypatch.setattr(matrix, "run_trial", lambda *args: calls.append(args))
    assert matrix.main() == 1  # Fake calls leave every attempt pending; they cannot claim success.
    manifest = json.loads((destination / "matrix.json").read_text())
    assert set(manifest["roots"]) == {"current", "plain"}
    assert len(calls) == len(manifest["trials"]) == 4
    assert manifest["settings"] == {"host": "codex", "model": "exact-model", "effort": "medium", "jobs": 1}
    assert all(args[3] == manifest["runner"] and args[4] == 600 for args in calls)


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


def make_blind_matrix(directory, *, previous=True):
    items = matrix.schedule(["cleanup"], ["standard"], 1, True, previous=previous)
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


@pytest.mark.parametrize("previous", [True, False])
def test_blind_packets_hide_arms_and_reviews_bind_actual_code(tmp_path, previous):
    path = make_blind_matrix(tmp_path, previous=previous)
    packet = path.parent
    labels = {"A", "B", "C"} if previous else {"A", "B"}
    assert {p.name for p in packet.iterdir()} == labels | {"baseline", "README.md", "review.json", "task.md"}
    assert (packet / "task.md").read_text().startswith(trials.CASES["cleanup"]["request"])
    assert not (packet / "A/.planning").exists()
    assert not reviews(tmp_path)[0]["valid"]
    complete_review(path)
    assert reviews(tmp_path, apply=True)[0]["valid"]
    assert len(list(tmp_path.glob("cleanup-*/review.json"))) == len(labels)
    with (packet / "A/src/ledger.py").open("a") as handle:
        handle.write("\n# Post-review edit\n")
    assert not reviews(tmp_path)[0]["valid"]
    with pytest.raises(ValueError, match="stale"):
        reviews(tmp_path, apply=True)


def test_blind_packet_includes_original_feature_requirements(tmp_path):
    items = matrix.schedule(["godfile"], ["standard"], 1, True, previous=False)
    (tmp_path / "matrix.json").write_text(json.dumps({"comparison": True, "seed": 4,
        "evaluator_root": str(trials.ROOT), "cases": trials.CASES, "trials": items}))
    for item in items:
        trial = tmp_path / item["id"]
        trials.prepare(trial, "godfile", plain_agent=True)
        trials.check(trial)
    packets(tmp_path)
    brief = (tmp_path / "blind/godfile-standard-1/task.md").read_text()
    assert (trials.PACK / "godfile/prompt.md").read_text() in brief
    assert str(trials.ROOT) not in brief
    assert "Use Forge" not in brief


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


def test_required_quality_acceptance_is_shared_by_all_arms_and_reviewers(tmp_path):
    from coding_trial_comparison import task_acceptance
    for plain in (True, False):
        trial = tmp_path / str(plain)
        trials.prepare(trial, "summary", plain_agent=plain)
        assert task_acceptance(trials.CASES["summary"]) in (trial / "prompt.md").read_text()
    assert "Required acceptance includes useful cleanup" in task_acceptance(trials.CASES["summary"])
    path = make_blind_matrix(tmp_path)
    assert task_acceptance(trials.CASES["cleanup"]) in (path.parent / "task.md").read_text()
    assert "Leave these unrelated files unchanged: src/untouched.py." in task_acceptance(
        {"protected_paths": ["src/untouched.py"]})


@pytest.mark.parametrize("data", [None, [], "invalid", 42])
def test_nonobject_blind_review_is_invalid_without_crashing(tmp_path, data):
    path = make_blind_matrix(tmp_path)
    path.write_text(json.dumps(data))
    assert not reviews(tmp_path)[0]["valid"]
    with pytest.raises(ValueError, match="Incomplete or stale"):
        reviews(tmp_path, apply=True)


@pytest.mark.parametrize("checks", [{}, [], None, {"behavior_passed": True},
    {"behavior_passed": 1, "oracle_complete": True, "candidate_tests_passed": True, "scope_passed": True}])
def test_blind_checks_require_complete_boolean_evidence(tmp_path, checks):
    path = make_blind_matrix(tmp_path)
    complete_review(path)
    (path.parent / "A/checks.json").write_text(json.dumps(checks))
    assert not reviews(tmp_path)[0]["valid"]


@pytest.mark.parametrize("field,value", [("behavior", {"success": False}), ("oracle_complete", False),
    ("tests", {"returncode": 1}), ("outside_scope", ["unexpected.txt"]), ("candidate_sha256", "stale")])
def test_blind_checks_match_the_source_bound_result_even_for_unpreferred_candidates(tmp_path, field, value):
    path = make_blind_matrix(tmp_path)
    complete_review(path)
    key = json.loads((tmp_path / "blind-key.json").read_text())
    result_path = tmp_path / key[path.parent.name + "/C"] / "result.json"
    result = json.loads(result_path.read_text())
    result[field] = value
    result_path.write_text(json.dumps(result))
    assert not reviews(tmp_path)[0]["valid"]


@pytest.mark.parametrize("cleanup", [None, {}, {"meaningful": "yes", "changed_files": [],
    "rationale": "Evidence", "regression_evidence": "Tests"}])
def test_blind_review_requires_a_complete_cleanup_judgment(tmp_path, cleanup):
    path = make_blind_matrix(tmp_path)
    complete_review(path)
    data = json.loads(path.read_text())
    data["candidates"]["A"]["cleanup"] = cleanup
    path.write_text(json.dumps(data))
    assert not reviews(tmp_path)[0]["valid"]


def test_comparison_can_prefer_correct_code_without_accepting_its_cleanup(tmp_path):
    path = make_blind_matrix(tmp_path)
    complete_review(path)
    data = json.loads(path.read_text())
    data["candidates"]["A"]["cleanup"].update(meaningful=False, changed_files=[], rationale="No useful structural gain")
    path.write_text(json.dumps(data))
    assert reviews(tmp_path, apply=True)[0]["valid"]
    key = json.loads((tmp_path / "blind-key.json").read_text())
    trial = tmp_path / key[path.parent.name + "/A"]
    assert not trials.check(trial, review=trial / "review.json")["cleanup"]["success"]
