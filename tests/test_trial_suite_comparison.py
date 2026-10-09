"""Independent suite-comparison controls; no real provider/container qualification.

The public CLI and matrix/assessment composition are real. Temporary Git and a
tiny private oracle run when the parent activates these tests. Existing isolated
writer/native/worker and workflow execution boundaries are explicit doubles;
their fixed responses prove bookkeeping and routing, not candidate correctness.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import json
import math
from pathlib import Path
import random
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import coding_trial_isolation as isolation
import coding_trial_suite_matrix as comparisons
import coding_trials as trial_cli
import trial_matrix as matrix_cli
from coding_trial_inventory import fingerprint, inventory
from coding_trial_suite import _read_study
from test_trial_suite_workflow import WorkflowBoundary, workflow_inputs, write_fixture_plan
from trial_suite_assessment_cases import RoleBoundary, load_json, process_result
from trial_suite_fixtures import make_suite
from trial_suite_observation_cases import OBSERVING_ORACLE, encoded, request
from trial_suite_prepare_cases import bytes_at, save_manifest


def invoke(module, monkeypatch, capsys, *arguments):
    monkeypatch.setattr(sys, "argv", [module.__name__, *map(str, arguments)])
    try:
        status = module.main()
    except SystemExit as exc:
        status = exc.code
    captured = capsys.readouterr()
    return status, json.loads(captured.out) if captured.out.strip() else None, captured.err


def forbidden(*args, **kwargs):
    pytest.fail("Report, blinding and review application must not execute any process")


def attempts(directory):
    result = []
    for path in directory.rglob("trial.json"):
        value = load_json(path)
        if value.get("schema") == "coding-trial-attempt/v1":
            result.append((path.parent, value))
    return sorted(result, key=lambda item: item[1]["scheduled_position"])


def forms(directory):
    result = []
    for path in directory.rglob("review.json"):
        value = load_json(path)
        if value.get("schema") == "coding-trial-review/v1" and len(value.get("candidates", {})) == 3:
            result.append(path)
    return sorted(result)


def complete_form(path, *, rejected=False, empty_cleanup=False):
    value = load_json(path)
    produced = sorted(label for label, row in value["candidates"].items() if row.get("candidate_sha256") is not None)
    value.update(reviewer="Independent synthetic-fixture reviewer", independent=True,
                 preferred=[] if rejected else produced[:1],
                 rationale="All three synthetic records and the complete code projections were inspected.")
    for row in value["candidates"].values():
        if row.get("candidate_sha256") is None:
            continue  # The existing exact no-output row is already verdict=fail.
        row.update(verdict="fail" if rejected else "pass", findings=[], criteria={
            name: "backend/app/service.py: inspected the shared implementation and retained checks."
            for name in ("readability", "cohesion", "duplication", "regressions")})
        if row["cleanup"] is not None:
            row["cleanup"] = {"meaningful": not rejected, "changed_files": [] if empty_cleanup else ["backend/app/service.py"],
                              "rationale": "No implementation change was made." if empty_cleanup else "Removed duplicate parsing.",
                              "regression_evidence": "The retained synthetic checks were inspected; no live execution is claimed."}
    path.write_text(json.dumps(value, indent=2) + "\n")
    return value


class ComparisonBoundary:
    def __init__(self, path, suite, directory, monkeypatch, *, fault=None, unchanged=False):
        self.directory, self.fault, self.calls, self.first_writer = directory, fault, [], True
        original = (path.parent / "export/backend/app/service.py").read_text()

        def writer(workspace):
            arm = load_json(workspace.parent / "trial.json")["identity"]["arm"]
            if unchanged:
                (workspace / "backend/app/service.py").write_text(original)
            if arm == "bravo":
                write_fixture_plan(workspace)
            elif arm == "charlie":
                folder = workspace / ".third-work"
                folder.mkdir()
                (folder / "notes.txt").write_text("FAKE_WORKFLOW_PRIVATE_MARKER\n")

        def worker(value, roots, cancel_event):
            arm = load_json(Path(roots["workspace"]).parents[1] / "trial.json")["identity"]["arm"]
            result = "incorrect" if self.fault == "worker" and arm == "alpha" else "1.0"
            return process_result(stdout=encoded({"schema": "coding-trial-observation/v1",
                "id": value["id"], "result": result, "error": None}))

        self.roles = RoleBoundary(writer=writer, worker=worker)
        self.workflow = WorkflowBoundary(monkeypatch)
        monkeypatch.setattr(isolation, "execute_isolated", self)

    def __call__(self, suite, suite_root, layout, argv, **kwargs):
        role, arm = layout["role"], layout["arm"]
        self.calls.append((role, arm))
        if role == "writer":
            attempt = load_json(Path(kwargs["roots"]["workspace"]).parent / "trial.json")
            study, _, _ = _read_study(attempt["study"])
            assert len(study["schedule"]) == 3
            assert [row["position"] for row in study["schedule"]] == [0, 1, 2]
            assert all(row["budget"] == {"timeout_seconds": 900, "output_bytes": 8388608}
                       for row in study["schedule"])
            if self.first_writer and self.fault == "interrupt":
                self.first_writer = False
                raise KeyboardInterrupt
            self.first_writer = False
            if arm == "alpha" and self.fault == "no-output":
                raise ValueError("unsupported-profile: Synthetic unavailable execution boundary")
        result = self.roles(suite, suite_root, layout, argv, **kwargs)
        if arm == "alpha" and ((role == "writer" and self.fault in {"writer", "timeout"})
                               or (role == "native" and self.fault == "native")):
            result["process"]["returncode"] = 7
            result["process"]["timed_out"] = self.fault == "timeout"
        return result


def comparison_case(tmp_path, monkeypatch, *, fault=None, cleanup_required=False, unchanged=False):
    path, suite = workflow_inputs(tmp_path)
    oracle = OBSERVING_ORACLE.replace("response['result']==request['input']",
                                    "response['result']==scenario['expected'][request['id']]")
    (path.parent / "checks/oracle.py").write_text(oracle + "\n# PRIVATE_ORACLE_MARKER\n")
    (path.parent / "checks/scenario.json").write_text(json.dumps({
        "requests": [request("feature", "v1.0"), request("preservation", "1.0")],
        "expected": {"feature": "1.0", "preservation": "1.0"},
        "ignore_failure": False, "expect_error": False, "mailbox_fault": None}) + "\n")
    suite["tasks"]["normalize"]["cleanup_required"] = cleanup_required
    for arm in suite["arms"].values():
        with (path.parent / arm["entry"]).open("a") as stream:
            stream.write("ARM_PROMPT_PRIVATE_MARKER\n")
    save_manifest(path, suite)
    directory = tmp_path / "comparison"
    boundary = ComparisonBoundary(path, suite, directory, monkeypatch, fault=fault, unchanged=unchanged)
    return path, suite, directory, boundary


def compare(path, directory, monkeypatch, capsys):
    status, value, stderr = invoke(matrix_cli, monkeypatch, capsys, "compare", directory, "--suite", path)
    assert status == 1 and not stderr and value["success"] is False
    return value


def blind_and_review(directory, monkeypatch, capsys, *, rejected=False, empty_cleanup=False):
    status, _, stderr = invoke(matrix_cli, monkeypatch, capsys, "blind", directory)
    assert status == 0 and not stderr
    selected = forms(directory)
    assert len(selected) == 1
    complete_form(selected[0], rejected=rejected, empty_cleanup=empty_cleanup)
    return selected[0]


def assert_ineligible(report):
    assert report["study_eligible"] is False
    assert report["accepted_outcomes"]["accepted"] == 0
    assert all(row["acceptance"] != "passed" for row in report["attempts"])


class TestSuiteComparison:
    def test_public_three_arm_vertical_retains_neutral_code_and_private_evidence(self, tmp_path, monkeypatch, capsys):
        path, suite, directory, boundary = comparison_case(tmp_path, monkeypatch)
        first = compare(path, directory, monkeypatch, capsys)
        assert_ineligible(first)
        assert first["study_complete"] is False and len(first["attempts"]) == 3
        saved = attempts(directory)
        assert len(saved) == 3 and {row["identity"]["arm"] for _, row in saved} == set(suite["arms"])
        assert len({json.dumps(row["study"], sort_keys=True) for _, row in saved}) == 1
        assert len({row["candidate"]["assessed_sha256"] for _, row in saved}) == 1
        for trial, record in saved:
            assert record["status"] == "completed" and record["assessments"]
            assert (trial / "assessor/candidate/backend/app/service.py").is_file()
            assert (trial / "assessor/candidate/deps.lock").read_bytes() == (path.parent / "export/deps.lock").read_bytes()
            assert (trial / "assessor/candidate/pyproject.toml").read_bytes() == (path.parent / "export/pyproject.toml").read_bytes()
            status, fresh, stderr = invoke(trial_cli, monkeypatch, capsys, "check", trial)
            assert status == 1 and not stderr and fresh["common_quality"] == "pending"
        assert [row["stage"] for row in boundary.workflow.calls] == ["exposure", "validation"]
        assert Counter(role for role, _ in boundary.calls) == {"writer": 3, "native": 6, "worker": 12}
        report = comparisons.report_suite(directory)
        assert {row["arm"]: row["workflow"] for row in report["attempts"]} == {
            "alpha": "not_applicable", "bravo": "passed", "charlie": "unmeasured"}
        assert all(row["common_quality"] == "pending" for row in report["attempts"])
        calls = deepcopy(boundary.calls)
        with monkeypatch.context() as guarded:
            guarded.setattr(subprocess, "Popen", forbidden)
            guarded.setattr(subprocess, "run", forbidden)
            form = blind_and_review(directory, guarded, capsys)
            block = form.parent
            assert (block / "task.md").read_text().startswith((path.parent / "brief.md").read_text())
            labels = set(load_json(form)["candidates"])
            assert labels == {"C001", "C002", "C003"}
            for label in labels:
                assert fingerprint(inventory(block / label / "candidate")) == saved[0][1]["candidate"]["assessed_sha256"]
                assert (block / label / "evidence.json").is_file()
            packet_bytes = b"\n".join(bytes_at(block).values())
            for marker in (b"PRIVATE_ORACLE_MARKER", b"ARM_PROMPT_PRIVATE_MARKER", b"FAKE_WORKFLOW_PRIVATE_MARKER"):
                assert marker not in packet_bytes
            assert not any(name in packet_bytes for name in (b'"alpha"', b'"bravo"', b'"charlie"'))
            assert not any(part in {".planning", ".third-work", "workflow-validation"}
                           for item in block.rglob("*") for part in item.relative_to(block).parts)
            status, applied, stderr = invoke(matrix_cli, guarded, capsys, "apply-reviews", directory)
            assert status == 0 and not stderr and applied["success"] is True
            before = bytes_at(directory)
            final = comparisons.report_suite(directory)
            assert bytes_at(directory) == before
            status, cli_report, stderr = invoke(matrix_cli, guarded, capsys, "report", directory)
            assert status == 0 and not stderr and cli_report == final
        assert boundary.calls == calls and final["study_complete"] is True
        assert_ineligible(final)
        assert all(row["common_quality"] == "passed" for row in final["attempts"])
        assert all(row["reported_telemetry"] is None for row in final["attempts"])
        aggregate = final["accepted_outcomes"]
        assert aggregate["scheduled"] == 3 and aggregate["elapsed_seconds"]["per_accepted"] is None
        assert aggregate["elapsed_seconds"]["total"] == pytest.approx(sum(row["attempt_seconds"] for row in final["attempts"]))
        assert all(math.isfinite(row["attempt_seconds"]) and row["attempt_seconds"] >= 0 for row in final["attempts"])
        assert aggregate["reported_cost_usd"]["total"] is None
        assert all(value["total"] is None for value in aggregate["tokens"].values())

    @pytest.mark.parametrize("fault", ["writer", "timeout", "native", "worker"])
    def test_failed_produced_arm_keeps_its_effort_and_cannot_be_preferred(self, tmp_path, monkeypatch, capsys, fault):
        path, _, directory, _ = comparison_case(tmp_path, monkeypatch, fault=fault)
        report = compare(path, directory, monkeypatch, capsys)
        assert len(report["attempts"]) == report["accepted_outcomes"]["scheduled"] == 3
        failed = next(row for row in report["attempts"] if row["arm"] == "alpha")
        assert failed["produced"] is True and failed["terminal"] is True and failed["common_quality"] == "failed"
        assert failed["attempt_seconds"] is not None
        form = blind_and_review(directory, monkeypatch, capsys)
        value = load_json(form)
        value["preferred"] = sorted(value["candidates"])
        form.write_text(json.dumps(value))
        before = {str(trial): bytes_at(trial / "assessor/assessments") for trial, _ in attempts(directory)}
        status, _, _ = invoke(matrix_cli, monkeypatch, capsys, "apply-reviews", directory)
        assert status != 0
        assert {str(trial): bytes_at(trial / "assessor/assessments") for trial, _ in attempts(directory)} == before
        assert_ineligible(comparisons.report_suite(directory))

    def test_no_output_has_real_terminal_evidence_and_no_candidate_fingerprint(self, tmp_path, monkeypatch, capsys):
        path, _, directory, _ = comparison_case(tmp_path, monkeypatch, fault="no-output")
        report = compare(path, directory, monkeypatch, capsys)
        absent = next(row for row in report["attempts"] if row["arm"] == "alpha")
        assert absent["terminal"] is True and absent["produced"] is False
        form = blind_and_review(directory, monkeypatch, capsys)
        value = load_json(form)
        absent_labels = [label for label, row in value["candidates"].items() if "not_produced_sha256" in row]
        assert len(absent_labels) == 1
        label = absent_labels[0]
        assert not (form.parent / label / "candidate").exists()
        evidence = load_json(form.parent / label / "evidence.json")
        assert all(evidence[key] is None for key in ("baseline_sha256", "candidate_sha256", "assessment_sha256"))
        assert evidence["not_produced_sha256"] == value["candidates"][label]["not_produced_sha256"]
        status, final, stderr = invoke(matrix_cli, monkeypatch, capsys, "apply-reviews", directory)
        assert status == 1 and not stderr and final["study_complete"] is True
        assert_ineligible(final)

    def test_interruption_retains_unentered_positions_and_creates_no_partial_blind_generation(self, tmp_path, monkeypatch, capsys):
        path, _, directory, boundary = comparison_case(tmp_path, monkeypatch, fault="interrupt")
        report = compare(path, directory, monkeypatch, capsys)
        assert len(report["attempts"]) == 3 and report["study_complete"] is False
        assert sum(row["status"] == "pending" for row in report["attempts"]) == 2
        assert all(row["terminal"] is False for row in report["attempts"])
        assert len(boundary.calls) == 1
        before = bytes_at(directory)
        status, _, _ = invoke(matrix_cli, monkeypatch, capsys, "blind", directory)
        assert status != 0 and bytes_at(directory) == before
        assert forms(directory) == []

    @pytest.mark.parametrize("change", ["delete-original", "stale-unpreferred"])
    def test_original_all_row_review_remains_authoritative_after_apply(self, tmp_path, monkeypatch, capsys, change):
        path, _, directory, _ = comparison_case(tmp_path, monkeypatch)
        compare(path, directory, monkeypatch, capsys)
        form = blind_and_review(directory, monkeypatch, capsys)
        status, _, _ = invoke(matrix_cli, monkeypatch, capsys, "apply-reviews", directory)
        assert status == 0
        before = {str(trial): bytes_at(trial / "assessor/assessments") for trial, _ in attempts(directory)}
        if change == "delete-original":
            form.unlink()
        else:
            value = load_json(form)
            label = next(name for name in value["candidates"] if name not in value["preferred"])
            value["candidates"][label]["assessment_sha256"] = "0" * 64
            form.write_text(json.dumps(value))
        monkeypatch.setattr(subprocess, "Popen", forbidden)
        monkeypatch.setattr(subprocess, "run", forbidden)
        status, report, stderr = invoke(matrix_cli, monkeypatch, capsys, "report", directory)
        assert status == 1 and not stderr and report["study_complete"] is False
        assert report["success"] is False and report["accepted_outcomes"]["accepted"] == 0
        assert {str(trial): bytes_at(trial / "assessor/assessments") for trial, _ in attempts(directory)} == before

    @pytest.mark.parametrize("rejected", [True, False])
    def test_unchanged_required_cleanup_can_be_reviewed_as_failed_but_never_passed(self, tmp_path, monkeypatch, capsys, rejected):
        path, _, directory, _ = comparison_case(tmp_path, monkeypatch, cleanup_required=True, unchanged=True)
        compare(path, directory, monkeypatch, capsys)
        form = blind_and_review(directory, monkeypatch, capsys, rejected=rejected, empty_cleanup=True)
        before = {str(trial): len(row["assessments"]) for trial, row in attempts(directory)}
        monkeypatch.setattr(subprocess, "Popen", forbidden)
        monkeypatch.setattr(subprocess, "run", forbidden)
        status, _, _ = invoke(matrix_cli, monkeypatch, capsys, "apply-reviews", directory)
        assert status != 0
        final = comparisons.report_suite(directory)
        assert final["study_complete"] is rejected
        assert_ineligible(final)
        for trial, row in attempts(directory):
            assert len(row["assessments"]) == before[str(trial)] + int(rejected)


@pytest.mark.parametrize("task_count,repeats,arm_count", [(1, 4, 3), (2, 2, 3), (1, 3, 2), (1, 1, 1)])
def test_schedule_is_frozen_balanced_and_uses_independent_seeds(tmp_path, task_count, repeats, arm_count):
    _, suite = make_suite(tmp_path / "curator")
    suite["arms"] = {name: suite["arms"][name] for name in sorted(suite["arms"])[:arm_count]}
    suite["tasks"] = {f"task-{index}": deepcopy(suite["tasks"]["normalize"]) for index in range(task_count)}
    suite["repeats"] = repeats
    before = deepcopy(suite)
    actual = comparisons.suite_schedule(suite)
    assert suite == before and actual == comparisons.suite_schedule(deepcopy(suite))
    assert len(actual) == task_count * repeats * arm_count
    assert [row["position"] for row in actual] == list(range(len(actual)))
    assert {(row["task"], row["arm"], row["repeat"]) for row in actual} == {
        (task, arm, repeat) for task in suite["tasks"] for arm in suite["arms"] for repeat in range(repeats)}
    assert all(set(row) == {"position", "task", "arm", "repeat", "budget"}
               and row["budget"] == {"timeout_seconds": 900, "output_bytes": 8388608} for row in actual)
    shuffled = sorted(suite["arms"])
    random.Random(suite["execution_seed"]).shuffle(shuffled)
    assert [row["arm"] for row in actual[:arm_count]] == shuffled
    for start in range(0, len(actual), arm_count):
        block = actual[start:start + arm_count]
        assert len({(row["task"], row["repeat"]) for row in block}) == 1
        assert {row["arm"] for row in block} == set(shuffled)
    for arm in shuffled:
        positions = Counter(row["position"] % arm_count for row in actual if row["arm"] == arm)
        counts = [positions[index] for index in range(arm_count)]
        assert max(counts) - min(counts) <= 1
    suite["blind_seed"] += 1
    assert comparisons.suite_schedule(suite) == actual


LEGACY_OVERRIDES = [
    ["--plugin-root", str(ROOT)], ["--previous-root", str(ROOT)], ["--model", "unused"],
    ["--effort", "medium"], ["--host", "codex"], ["--codex", "codex"], ["--claude", "claude"],
    ["--cases", "summary"], ["--depths", "standard"], ["--repeats", "2"], ["--jobs", "1"],
    ["--timeout", "900"], ["--seed", "0"], ["--runner", "unused"],
]


@pytest.mark.parametrize("override", LEGACY_OVERRIDES)
def test_suite_cli_rejects_explicit_legacy_overrides_before_creation(tmp_path, monkeypatch, capsys, override):
    path, _, directory, _ = comparison_case(tmp_path, monkeypatch)
    status, _, _ = invoke(matrix_cli, monkeypatch, capsys, "compare", directory, "--suite", path, *override)
    assert status == 2 and not directory.exists()


@pytest.mark.parametrize("operation", ["run", "report", "blind", "apply-reviews"])
def test_suite_selector_cannot_override_other_matrix_operations(tmp_path, monkeypatch, capsys, operation):
    path, _, directory, _ = comparison_case(tmp_path, monkeypatch)
    status, _, _ = invoke(matrix_cli, monkeypatch, capsys, operation, directory, "--suite", path)
    assert status == 2 and not directory.exists()


@pytest.mark.parametrize("flag", [["--auth-file", "synthetic-auth"], ["--qualify-loading"]])
def test_new_native_flags_do_not_change_legacy_commands(tmp_path, monkeypatch, capsys, flag):
    status, _, _ = invoke(matrix_cli, monkeypatch, capsys, "compare", tmp_path / "comparison", *flag)
    assert status == 2 and not (tmp_path / "comparison").exists()


@pytest.mark.parametrize("operation", ["run", "compare"])
def test_omitted_legacy_options_preserve_original_defaults_and_runner_seams(tmp_path, monkeypatch, capsys, operation):
    directory, calls = tmp_path / "legacy", []
    monkeypatch.setattr(matrix_cli, "run_trial", lambda *args: calls.append(args))
    extra = ["--runner", "unused"] if operation == "run" else ["--model", "exact-model", "--effort", "medium"]
    status, result, stderr = invoke(matrix_cli, monkeypatch, capsys, operation, directory, *extra)
    assert status == 1 and not stderr and result["success"] is False
    manifest = load_json(directory / "matrix.json")
    expected_cases = ["summary", "cleanup", "resume"] if operation == "run" else ["godfile", "import-preview"]
    expected_depths = ["lean", "standard", "deep"] if operation == "run" else ["standard"]
    assert manifest["trials"] == matrix_cli.schedule(expected_cases, expected_depths, 2,
                                                     operation == "compare", previous=False)
    assert len(calls) == len(manifest["trials"])
    assert manifest["timeout"] == 900 and manifest["seed"] == 0
    assert manifest["settings"] == {"host": "codex", "model": None if operation == "run" else "exact-model",
                                     "effort": None if operation == "run" else "medium", "jobs": 1}
    assert all(args[1] == ROOT and args[3] == manifest["runner"] and args[4] == 900 for args in calls)


def test_shared_outcomes_keep_the_original_public_exports():
    import coding_trial_comparison as legacy_reviews
    import coding_trial_outcomes as outcomes
    assert matrix_cli.accepted_outcomes is outcomes.accepted_outcomes
    assert legacy_reviews.CRITERIA is outcomes.CRITERIA
