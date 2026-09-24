"""Coding trial verdicts use independent behavior checks and real process results."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys
import time
import tracemalloc

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("coding_trials", ROOT / "tools/coding_trials.py")
trials = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trials)


@pytest.mark.parametrize("case", ["summary", "discount", "resume", "godfile"])
def test_unfinished_feature_fails_independent_oracle(tmp_path, case):
    trials.prepare(tmp_path / "trial", case)
    result = trials.check(tmp_path / "trial")
    assert not result["success"]
    assert result["oracle"]["returncode"] != 0
    assert (result["tests"]["returncode"] != 0) is (case == "resume")
    assert result["reported_telemetry"] is None


def test_behavior_preservation_checks_exact_exports_and_public_wrapper(tmp_path):
    trial = tmp_path / "trial"
    trials.prepare(trial, "cleanup")
    baseline = trials.check(trial)
    assert baseline["behavior"]["success"]
    assert not baseline["success"]
    assert not baseline["workflow"]["success"]
    assert not baseline["cleanup"]["success"]
    assert json.loads(baseline["oracle"]["stdout"])["assertions"] >= 500
    path = trial / "workspace/src/ledger.py"
    path.write_text(path.read_text().replace('"Total: "', '"Total:"'))
    result = trials.check(trial)
    assert not result["success"]
    assert result["after"]["source_lines"] == result["before"]["source_lines"]


def test_only_planning_ignore_rules_are_within_trial_scope(tmp_path):
    trial = tmp_path / "trial"
    trials.prepare(trial, "cleanup")
    ignore = trial / "workspace/.gitignore"
    ignore.write_text("# Local Forge records and generated test caches\n.planning\n__pycache__/\n.pytest_cache/\n*.pyc\n")
    assert not trials.check(trial)["outside_scope"]
    ignore.write_text(".planning/\n/tests/\n")
    assert trials.check(trial)["outside_scope"] == [".gitignore"]


def test_independent_oracle_survives_replaced_candidate_tests(tmp_path):
    trial = tmp_path / "trial"
    trials.prepare(trial, "summary")
    (trial / "workspace/tests/test_ledger.py").write_text("# No candidate tests\n")
    result = trials.check(trial)
    assert not result["success"]
    assert result["oracle"]["returncode"] != 0


def test_scope_expansion_is_reported_and_rejected(tmp_path):
    trial = tmp_path / "trial"
    trials.prepare(trial, "cleanup")
    (trial / "workspace/requirements.txt").write_text("unrequested-package\n")
    result = trials.check(trial)
    assert not result["success"]
    assert result["outside_scope"] == ["requirements.txt"]


def test_prepare_refuses_to_overwrite_existing_trial(tmp_path):
    trials.prepare(tmp_path / "trial", "cleanup")
    with pytest.raises(FileExistsError):
        trials.prepare(tmp_path / "trial", "cleanup")


def test_runner_failures_and_timeouts_are_real_results(tmp_path):
    failed = trials.execute([sys.executable, "-c", "raise SystemExit(3)"], tmp_path)
    assert failed["returncode"] == 3
    timed_out = trials.execute([sys.executable, "-c", "import time; time.sleep(5)"], tmp_path, timeout=0.05)
    assert timed_out["returncode"] == 124


@pytest.mark.parametrize("returncode", [3, 124])
def test_recheck_preserves_failed_runner_verdict(tmp_path, returncode):
    trial = tmp_path / "trial"
    trials.prepare(trial, "cleanup")
    path = trial / "trial.json"
    record = json.loads(path.read_text())
    record["runner"] = {"returncode": returncode, "seconds": 1.0}
    path.write_text(json.dumps(record))
    result = trials.check(trial)
    assert result["oracle"]["returncode"] == 0
    assert not result["success"]
    assert not json.loads((trial / "result.json").read_text())["success"]


@pytest.mark.parametrize("injected", [
    'raise SystemExit(0)',
    'print("not an oracle result"); raise SystemExit(0)',
    'print(\'{"case": "summary", "assertions": 1}\'); raise SystemExit(0)',
])
def test_premature_oracle_exit_cannot_pass(tmp_path, injected):
    trial = tmp_path / "trial"
    trials.prepare(trial, "summary")
    path = trial / "workspace/src/ledger.py"
    with path.open("a") as handle:
        handle.write('\nif __name__ == "candidate_ledger":\n    ' + injected + '\n')
    result = trials.check(trial)
    assert result["oracle"]["returncode"] == 0
    assert not result["oracle_complete"]
    assert not result["success"]


def test_optimized_parent_environment_cannot_disable_oracle(tmp_path, monkeypatch):
    trial = tmp_path / "trial"
    trials.prepare(trial, "summary")
    monkeypatch.setenv("PYTHONOPTIMIZE", "1")
    result = trials.check(trial)
    assert not result["success"]
    assert result["oracle"]["returncode"] != 0


def write_workflow(workspace, depth="standard"):
    planning = workspace / ".planning"
    sections = planning / "sections"
    sections.mkdir(parents=True)
    (planning / "spec.md").write_text("REQ-1: Remove duplicate invoice calculations while preserving behavior.\n")
    (sections / "index.md").write_text('''<!-- PROJECT_CONFIG
runtime: python3
test_command: PYTHONPATH=src python3 -m unittest discover -s tests
END_PROJECT_CONFIG -->
<!-- SECTION_MANIFEST
section-01-invoice
END_MANIFEST -->
<!-- FORGE_META
{"artifact_type":"compact_plan","depth_mode":"standard","source":"spec.md"}
END_FORGE_META -->
Dependencies: none. Execution order: section-01-invoice. Parallel: no siblings.
'''.replace('"depth_mode":"standard"', '"depth_mode":' + json.dumps(depth)))
    (sections / "section-01-invoice.md").write_text('''## Goal
REQ-1: preserve behavior while sharing invoice calculations.
## Dependencies
None.
## Owned files
Ownership is exclusive to this section.
- src/ledger.py
- tests/test_ledger.py
## Tests first
Case: REQ-1 exact receipt and pricing behavior before and after cleanup.
Expected: same public result, including integer tax truncation.
Command: PYTHONPATH=src python3 -m unittest discover -s tests
## Implementation contract
Keep invoice and InvoiceManager signatures and validation, exports and errors.
## Evidence
Command `rg --files` found the invoice module and existing tests. Runtime: Python
standard library. `.planning/spec.md` identifies the requirement. The fixture
repeats subtotal/tax calculations across actions. Assumption: standard library only.
## Decisions
Rationale: share one calculation function. Alternative: retain duplication, rejected
because independent changes could drift. Keep exports in the existing API.
## Risks
Security/privacy: no secrets or external service access. Risk: serialization or
exception drift; pin exact behavior with tests. Rollback:
restore the prior function if compatibility fails. No external data changes.
## Review
Reviewed: source, regression tests, exact outputs and failure paths; no findings.
Verdict: pass.
## Acceptance
REQ-1: independent behavior oracle and existing/added tests pass after refactor.
''')
    implementation = planning / "implementation"
    implementation.mkdir()
    state = {"completed_sections": {"section-01-invoice": {
        "review_status": "pass", "verification": ["python3 -m unittest discover -s tests: passed"],
        "files_changed": ["src/ledger.py"], "test_files": ["tests/test_ledger.py"],
        "completed_at": "2026-09-15T15:00:00+00:00", "review_artifacts": [],
    }}, "pending_sections": {}}
    state_path = implementation / "zagrosi_implement_state.json"
    state_path.write_text(json.dumps(state))
    return state_path


def write_cleanup(trial):
    workspace = trial / "workspace"
    (workspace / "src/ledger.py").write_text('''import json

def invoice(action, items, customer="Guest", **options):
    if action not in ("total", "json", "receipt"):
        raise ValueError("Unknown action: " + str(action))
    subtotal = sum(item["price"] * item["quantity"] for item in items)
    tax = subtotal * 20 // 100
    total = subtotal + tax
    if action == "total":
        return total
    if action == "json":
        return json.dumps({"customer": customer, "subtotal": subtotal, "tax": tax,
                           "total": total}, sort_keys=True)
    return ("Customer: " + str(customer) + "\\nSubtotal: " + str(subtotal)
            + "\\nTax: " + str(tax) + "\\nTotal: " + str(total) + "\\n")

class InvoiceManager:
    def total(self, items):
        return invoice("total", items)
''')
    with (workspace / "tests/test_ledger.py").open("a") as handle:
        handle.write('''
    def test_tax_rounds_after_aggregate(self):
        self.assertEqual(invoice("total", [{"price": 3, "quantity": 2}]), 7)
''')


def write_review(trial, regression_evidence="The aggregate-rounding regression and compatibility oracle pass before and after cleanup."):
    review = trials.review_template(trial)
    review.update(reviewer="independent-test-reviewer", independent=True, verdict="pass")
    review["cleanup"].update(
        meaningful=True, changed_files=["src/ledger.py"],
        rationale="All three actions share aggregate subtotal and tax; public exports remain intact.",
        regression_evidence=regression_evidence,
    )
    path = trial / "review.json"
    path.write_text(json.dumps(review))
    return path


def test_cleanup_requires_actual_changes_workflow_and_independent_review(tmp_path):
    trial = tmp_path / "trial"
    trials.prepare(trial, "cleanup")
    write_workflow(trial / "workspace")
    unchanged = trials.check(trial)
    assert unchanged["behavior"]["success"]
    assert unchanged["workflow"]["success"]
    assert not unchanged["cleanup"]["success"]
    write_cleanup(trial)
    unreviewed = trials.check(trial)
    assert unreviewed["behavior"]["success"]
    assert not unreviewed["cleanup"]["success"]
    result = trials.check(trial, review=write_review(trial))
    assert result["cleanup"]["success"]
    assert result["success"]


def test_review_cannot_qualify_comment_only_changes(tmp_path):
    trial = tmp_path / "trial"
    trials.prepare(trial, "cleanup")
    for name in ("src/ledger.py", "tests/test_ledger.py"):
        with (trial / "workspace" / name).open("a") as handle:
            handle.write("\n# reviewed cleanup\n")
    result = trials.check(trial, review=write_review(trial))
    assert result["behavior"]["success"]
    assert not result["cleanup"]["success"]


@pytest.mark.parametrize("mutation", ["stale", "not-independent", "inside-workspace", "malformed", "null"])
def test_cleanup_review_must_be_valid_independent_and_bound_to_candidate(tmp_path, mutation):
    trial = tmp_path / "trial"
    trials.prepare(trial, "cleanup")
    write_cleanup(trial)
    review_path = write_review(trial)
    review = json.loads(review_path.read_text())
    if mutation == "stale":
        review["candidate_sha256"] = "0" * 64
    elif mutation == "not-independent":
        review["independent"] = False
    elif mutation == "inside-workspace":
        review_path = trial / "workspace/review.json"
    review_path.write_text("[]" if mutation == "malformed" else "null" if mutation == "null" else json.dumps(review))
    result = trials.check(trial, review=review_path)
    assert not result["cleanup"]["success"]


@pytest.mark.parametrize("depth", ["lean", "standard", "deep"])
def test_cleanup_accepts_existing_sufficient_regression_coverage(tmp_path, depth):
    trial = tmp_path / "trial"
    trials.prepare(trial, "cleanup", depth=depth)
    original_tests = (trial / "workspace/tests/test_ledger.py").read_text()
    write_cleanup(trial)
    (trial / "workspace/tests/test_ledger.py").write_text(original_tests)
    write_workflow(trial / "workspace", depth)
    review = write_review(trial, regression_evidence="Existing test_total, test_receipt, and the independent compatibility oracle pass before and after cleanup.")
    result = trials.check(trial, review=review)
    assert result["cleanup"]["test_changes"] == []
    assert result["cleanup"]["success"]
    assert result["success"]


@pytest.mark.parametrize("depth", ["lean", "standard", "deep"])
@pytest.mark.parametrize("problem", ["missing-spec", "blocked-review"])
def test_workflow_requires_strict_planning_admission(tmp_path, depth, problem):
    trial = tmp_path / "trial"
    trials.prepare(trial, "cleanup", depth=depth)
    write_cleanup(trial)
    write_workflow(trial / "workspace", depth)
    planning = trial / "workspace/.planning"
    if problem == "missing-spec":
        (planning / "spec.md").unlink()
    else:
        section = planning / "sections/section-01-invoice.md"
        section.write_text(section.read_text().replace("Verdict: pass.", "Verdict: blocked."))
    result = trials.check(trial, review=write_review(trial))
    assert result["behavior"]["success"] and result["cleanup"]["success"]
    assert not result["success"] and not result["workflow"]["success"]
    report = result["workflow"]["report"]
    assert report["phase"] == "plan" and not report["admission_success"]
    assert report["reasons"] and report["blocking_gate_count"] > 0
    assert not result["workflow"]["process"]["stdout_truncated"]


@pytest.mark.parametrize("selected,actual", [
    (selected, actual) for selected in ("lean", "standard", "deep")
    for actual in ("lean", "standard", "deep") if selected != actual
])
def test_workflow_rejects_plan_depth_different_from_trial(tmp_path, selected, actual):
    trial = tmp_path / "trial"
    trials.prepare(trial, "cleanup", depth=selected)
    write_cleanup(trial)
    write_workflow(trial / "workspace", actual)
    result = trials.check(trial, review=write_review(trial))
    assert not result["success"] and not result["workflow"]["success"]
    report = result["workflow"]["report"]
    assert not report["admission_success"]
    assert report["selected_depth"] == selected and report["planning_depth"] == actual
    assert "does not match" in report["reasons"][0]


def test_empty_new_modules_and_docstrings_are_not_semantic_work(tmp_path):
    trial = tmp_path / "trial"
    trials.prepare(trial, "cleanup")
    (trial / "workspace/src/extra.py").write_text('"""Unused module."""\n# cleanup\n')
    (trial / "workspace/tests/test_extra.py").write_text('"""Regression tests."""\n')
    result = trials.check(trial, review=write_review(trial))
    assert result["cleanup"]["source_changes"] == []
    assert result["cleanup"]["test_changes"] == []
    assert not result["cleanup"]["success"]


def test_workflow_requires_real_completed_section_records(tmp_path):
    trial = tmp_path / "trial"
    trials.prepare(trial, "cleanup")
    state_path = write_workflow(trial / "workspace")
    state = json.loads(state_path.read_text())
    state["pending_sections"] = {"section-01-invoice": {"reason": "tests pending"}}
    state_path.write_text(json.dumps(state))
    result = trials.check(trial)
    assert result["behavior"]["success"]
    assert not result["workflow"]["success"]
    state["pending_sections"] = {}
    del state["completed_sections"]["section-01-invoice"]["verification"]
    state_path.write_text(json.dumps(state))
    assert not trials.check(trial)["workflow"]["success"]


def test_prepare_provenance_includes_runtime_and_reports_legacy_gaps(tmp_path):
    trial = tmp_path / "trial"
    trials.prepare(trial, "cleanup")
    record_path = trial / "trial.json"
    record = json.loads(record_path.read_text())
    assert "scripts/forge/processes.py" in record["plugin_sha256"]
    record["plugin_sha256"] = {name: digest for name, digest in record["plugin_sha256"].items()
                               if not name.startswith("scripts/forge/")}
    record_path.write_text(json.dumps(record))
    result = trials.check(trial)
    assert result["plugin_provenance"]["status"] == "incomplete"
    assert not result["plugin_provenance"]["success"]


def test_runtime_drift_is_separate_from_behavior(tmp_path):
    trial = tmp_path / "trial"
    trials.prepare(trial, "cleanup")
    record_path = trial / "trial.json"
    record = json.loads(record_path.read_text())
    record["plugin_sha256"]["scripts/forge/processes.py"] = "0" * 64
    record_path.write_text(json.dumps(record))
    result = trials.check(trial)
    assert result["behavior"]["success"]
    assert result["plugin_provenance"]["status"] == "drift"
    assert "scripts/forge/processes.py" in result["plugin_provenance"]["changed_files"]


def test_evaluator_provenance_includes_verdict_helpers_and_case_requirements(tmp_path):
    trial = tmp_path / "trial"
    trials.prepare(trial, "cleanup")
    record_path = trial / "trial.json"
    record = json.loads(record_path.read_text())
    assert {"tools/coding_trials.py", "tools/coding_trial_evidence.py", "tools/coding_trial_process.py",
            "examples/evals/coding/cases.json"} <= record["evaluator_sha256"].keys()
    record["evaluator_sha256"]["tools/coding_trial_evidence.py"] = "0" * 64
    record_path.write_text(json.dumps(record))
    result = trials.check(trial)
    assert result["oracle"]["returncode"] == 0
    assert result["evaluator_changed"]
    assert not result["success"]


@pytest.mark.parametrize("failing", [False, True])
def test_workflow_compacts_large_reports_before_output_capture(tmp_path, monkeypatch, failing):
    plugin = tmp_path / "plugin"
    (plugin / "scripts").mkdir(parents=True)
    (plugin / "scripts/zagrosi_skills.py").write_text('''import json,sys,types
output = types.ModuleType("trial_large_output")
def print_json(payload, exit_code=0):
    print(json.dumps(payload))
    return exit_code
output.print_json = print_json
sys.modules[output.__name__] = output
artifacts = types.ModuleType("trial_large_artifacts")
artifacts.planning_depth = lambda path: "deep"
sys.modules[artifacts.__name__] = artifacts
def load_runtime():
    return types.SimpleNamespace(MODULE_NAMES={"forge/output.py": output.__name__, "forge/artifacts.py": artifacts.__name__})
def main(argv):
    assert argv[0] == "postflight"
    phase = argv[argv.index("--phase") + 1]
    assert phase == "implement" or "--strict" in argv
    if FAILING and phase == "plan":
        return output.print_json({"phase":phase, "stage":"postflight", "success":False,
            "blocking_gates":["lint-plan"], "gates":[{"name":"lint-plan", "payload":{
                "findings":[{"message":"🙂" * 10000} for _ in range(100)]}}]}, 1)
    return output.print_json({"phase":phase, "stage":"postflight", "success":True,
        "sections_recorded_complete":True, "gates":[{"details":"x" * 500000}],
        "remaining_sections":[], "pending_sections":[], "blocking_gates":[]})
'''.replace("FAILING", repr(failing)), encoding="utf-8")
    monkeypatch.setattr(trials, "ROOT", plugin)
    result = trials.workflow_verdict(tmp_path, "deep")
    assert result["success"] is not failing
    assert not result["process"]["stdout_truncated"]
    assert result["process"]["stdout_bytes"] < 12000
    if failing:
        assert result["report"]["reasons"]
    else:
        assert result["report"]["sections_recorded_complete"]


def test_execute_retains_bounded_tails_without_buffering_all_output(tmp_path):
    tracemalloc.start()
    try:
        result = trials.execute([sys.executable, "-c", (
            "import os; "
            "[(os.write(1, b'o' * 65536), os.write(2, b'e' * 65536)) for _ in range(256)]; "
            "os.write(1, b'END-OUT'); os.write(2, b'END-ERR')"
        )], tmp_path)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert result["returncode"] == 0
    assert result["stdout"].endswith("END-OUT") and result["stderr"].endswith("END-ERR")
    assert len(result["stdout"]) <= 12000 and len(result["stderr"]) <= 12000
    assert peak < 4 * 1024 * 1024


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group integration test")
@pytest.mark.parametrize("leader_exits", [False, True])
def test_timeout_stops_descendants_even_after_leader_exit(tmp_path, leader_exits):
    marker = tmp_path / "child-survived"
    child = ("import signal,time,pathlib; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
             f"time.sleep(.5); pathlib.Path({str(marker)!r}).write_text('leaked')")
    leader = (f"import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',{child!r}]); "
              + ("pass" if leader_exits else "time.sleep(10)"))
    result = trials.execute([sys.executable, "-c", leader], tmp_path, timeout=.15)
    assert result["returncode"] == 124
    time.sleep(.55)
    assert not marker.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group integration test")
@pytest.mark.parametrize("leader_exits", [False, True])
def test_redirected_descendants_stop_before_execute_returns(tmp_path, leader_exits):
    ready = tmp_path / "child-ready"
    release = tmp_path / "release-child"
    marker = tmp_path / "child-survived"
    child = ("import pathlib,time\n"
             f"pathlib.Path({str(ready)!r}).write_text('ready')\n"
             f"while not pathlib.Path({str(release)!r}).exists(): time.sleep(.01)\n"
             f"pathlib.Path({str(marker)!r}).write_text('leaked')\n")
    leader = ("import pathlib,subprocess,sys,time\n"
              f"subprocess.Popen([sys.executable,'-c',{child!r}], "
              "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
              f"while not pathlib.Path({str(ready)!r}).exists(): time.sleep(.01)\n"
              "print('child ready', flush=True)\n"
              + ("pass\n" if leader_exits else "time.sleep(10)\n"))
    result = trials.execute([sys.executable, "-c", leader], tmp_path, timeout=2)
    # Release a surviving child even if an assertion fails, so the regression
    # itself cannot leak the deliberately started process.
    release.touch()
    assert ready.exists() and "child ready" in result["stdout"]
    assert result["returncode"] == (0 if leader_exits else 124)
    time.sleep(.2)
    assert not marker.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX termination-error contract")
def test_failed_residual_group_termination_cannot_report_process_success(tmp_path, monkeypatch):
    import signal

    process_tools = sys.modules[trials.execute.__module__]
    signals = []

    def denied_killpg(_process_group, sig):
        signals.append(sig)
        if sig:
            raise PermissionError("process group could not be terminated")

    monkeypatch.setattr(process_tools.os, "killpg", denied_killpg)
    result = trials.execute([sys.executable, "-c", "pass"], tmp_path)
    assert signal.SIGKILL in signals
    assert result["returncode"] != 0
    assert "could not be terminated" in result["termination_error"]


def test_execute_decodes_arbitrary_output_and_preserves_timeout_tail(tmp_path):
    result = trials.execute([sys.executable, "-c",
                             "import os,time; os.write(1,b'prefix\\xff'); time.sleep(5)"],
                            tmp_path, timeout=1)
    assert result["returncode"] == 124
    assert result["stdout"] == "prefix\ufffd"
    assert result["timed_out"]


def test_execute_handles_prompt_and_missing_runner(tmp_path):
    result = trials.execute([sys.executable, "-c", "import sys; print(sys.stdin.read())"],
                            tmp_path, prompt="a reviewable prompt")
    assert result["returncode"] == 0
    assert result["stdout"] == "a reviewable prompt" + os.linesep
    missing = trials.execute([str(tmp_path / "missing-runner")], tmp_path)
    assert missing["returncode"] == 127
    assert missing["stderr"]
