"""Independent Section03 CLI cases; no matrix/report implementation is tested.

Authored from suite-format and the section03 interface before CLI replacement.
Parent-run integration uses temporary Git and a tiny trusted oracle. The existing
role executor double makes no claim about Docker, native loading or providers.
"""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from trial_suite_prepare_cases import PYTHON, bytes_at, marked_suite, read_reference
from trial_suite_assessment_cases import (
    RoleBoundary, assessment_fixture, codes, last_assessment, load_json,
    review_for, run_case, save_review,
)
import coding_trial_isolation as isolation
import coding_trial_process as process
import coding_trial_suite as suite_api
import coding_trials as trials


def invoke(monkeypatch, capsys, *arguments):
    monkeypatch.setattr(sys, "argv", ["coding_trials.py", *map(str, arguments)])
    try:
        code = trials.main()
    except SystemExit as exc:
        code = exc.code
    captured = capsys.readouterr()
    value = json.loads(captured.out) if captured.out.strip() else None
    return code, value, captured.err


def forbidden(*args, **kwargs):
    pytest.fail("This CLI operation must not execute a writer, check or qualification")


@pytest.mark.parametrize("arm", ["alpha", "bravo", "charlie"])
def test_prepare_routes_opaque_suite_selection_and_binds_its_actual_controller(tmp_path, monkeypatch, capsys, arm):
    path, _, marker = marked_suite(tmp_path)
    trial = tmp_path / "trial"
    code, output, stderr = invoke(monkeypatch, capsys, "prepare", trial,
                                   "--suite", path, "--task", "normalize", "--arm", arm)
    assert code == 0 and not stderr
    attempt = load_json(trial / "trial.json")
    assert output == attempt and attempt["status"] == "prepared"
    assert attempt["identity"]["task"] == "normalize" and attempt["identity"]["arm"] == arm
    assert set(output["admission_gates"]) == {"environment", "task", "isolation", "loading"}
    assert all(gate["status"] == "unmeasured" for gate in output["admission_gates"].values())
    study = json.loads(read_reference(trial, attempt["study"])[1])
    assert study["controller"]["executable"] == str(Path(sys.executable).resolve()) == str(PYTHON)
    assert not marker.exists() and attempt["runner"] is None


@pytest.mark.parametrize("failure", [None, "native", "writer"])
def test_run_uses_suite_owned_writer_and_does_not_accept_unreviewed_or_failed_output(tmp_path, monkeypatch, capsys, failure):
    path, _ = assessment_fixture(tmp_path)
    boundary = RoleBoundary(native_code=7 if failure == "native" else 0,
                            writer_code=9 if failure == "writer" else 0)
    monkeypatch.setattr(isolation, "execute_isolated", boundary)
    trial = tmp_path / "trial"
    code, output, stderr = invoke(monkeypatch, capsys, "run", trial,
                                   "--suite", path, "--task", "normalize", "--arm", "alpha")
    assert code == 1 and isinstance(output, dict) and not stderr
    attempt, verdict = load_json(trial / "trial.json"), last_assessment(trial)
    assert attempt["status"] == "completed" and attempt["runner"] and attempt["candidate"]
    assert [call["role"] for call in boundary.calls] == ["writer", "native"]
    assert verdict["common_quality"] == ("pending" if failure is None else "failed")
    assert verdict["study_eligible"] is False and verdict["acceptance"] != "passed"


def test_saved_schema_check_review_and_fresh_recheck_preserve_outcomes_and_history(tmp_path, monkeypatch, capsys):
    trial, _, boundary, first = run_case(tmp_path, monkeypatch)
    original = bytes_at(trial / "assessor/assessments/000001")
    original_runner = deepcopy(load_json(trial / "trial.json")["runner"])
    calls = deepcopy(boundary.calls)
    review = save_review(trial, review_for(trial, first))
    with monkeypatch.context() as guarded:
        guarded.setattr(isolation, "execute_isolated", forbidden)
        guarded.setattr(process, "execute", forbidden)
        guarded.setattr(subprocess, "Popen", forbidden)
        guarded.setattr(subprocess, "run", forbidden)
        code, reviewed, stderr = invoke(guarded, capsys, "check", trial, "--review", review)
    assert code == 0 and not stderr
    assert reviewed["synthetic_validation"] == "passed" and reviewed["common_quality"] == "passed"
    assert reviewed["study_eligible"] is False and reviewed["acceptance"] != "passed"
    assert reviewed["behavior"] == first["behavior"] and boundary.calls == calls
    assert bytes_at(trial / "assessor/assessments/000001") == original
    code, fresh, stderr = invoke(monkeypatch, capsys, "check", trial)
    assert code == 1 and not stderr and fresh["kind"] == "check"
    assert fresh["behavior"] != first["behavior"] and fresh["common_quality"] == "pending"
    code, stale, stderr = invoke(monkeypatch, capsys, "check", trial, "--review", review)
    assert code == 1 and not stderr and "review-stale" in codes(stale)
    assert load_json(trial / "trial.json")["runner"] == original_runner
    assert len(load_json(trial / "trial.json")["assessments"]) == 4


def test_suite_review_template_uses_current_behavior_without_running_or_accepting_it(tmp_path, monkeypatch, capsys):
    trial, _, boundary, verdict = run_case(tmp_path, monkeypatch)
    before, calls = bytes_at(trial), deepcopy(boundary.calls)
    monkeypatch.setattr(isolation, "execute_isolated", forbidden)
    monkeypatch.setattr(process, "execute", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    code, form, stderr = invoke(monkeypatch, capsys, "review-template", trial)
    assert code == 0 and not stderr
    assert form["schema"] == "coding-trial-review/v1" and form["block"] == "normalize"
    assert form["independent"] is False and form["preferred"] == []
    assert set(form["candidates"]) == {"C001"}
    row, attempt = form["candidates"]["C001"], load_json(trial / "trial.json")
    assert row["baseline_sha256"] == attempt["identity"]["baseline_sha256"]
    assert row["candidate_sha256"] == attempt["candidate"]["assessed_sha256"]
    assert row["assessment_sha256"] == verdict["behavior"]["sha256"]
    assert set(row["criteria"]) == {"readability", "cohesion", "duplication", "regressions"}
    assert row["cleanup"] is None and bytes_at(trial) == before and boundary.calls == calls


@pytest.mark.parametrize("operation", ["prepare", "run"])
@pytest.mark.parametrize("status", ["prepared", "running", "failed", "completed"])
def test_suite_cli_never_adopts_an_existing_destination(tmp_path, monkeypatch, capsys, operation, status):
    path, _, marker = marked_suite(tmp_path)
    trial = tmp_path / "trial"
    trial.mkdir()
    raw = json.dumps({"schema": "coding-trial-attempt/v1", "status": status}).encode()
    (trial / "trial.json").write_bytes(raw)
    code, output, stderr = invoke(monkeypatch, capsys, operation, trial,
                                   "--suite", path, "--task", "normalize", "--arm", "alpha")
    assert code == 2 and not stderr
    assert set(output) == {"success", "error"} and output["success"] is False
    assert set(output["error"]) == {"code", "message", "path"}
    assert output["error"]["code"] == "input-exists" and output["error"]["message"]
    assert output["error"]["path"] in (None, str(trial))
    assert bytes_at(trial) == {"trial.json": raw}
    assert not trial.with_name("trial.study").exists() and not marker.exists()


def test_invalid_suite_is_a_structured_input_error_without_creation(tmp_path, monkeypatch, capsys):
    path, _, marker = marked_suite(tmp_path)
    path.write_text('{"schema":"coding-trial-suite/v1","schema":"duplicate"}')
    trial = tmp_path / "trial"
    code, output, stderr = invoke(monkeypatch, capsys, "prepare", trial,
                                   "--suite", path, "--task", "normalize", "--arm", "alpha")
    assert code == 2 and not stderr and output["success"] is False
    assert set(output["error"]) == {"code", "message", "path"}
    assert output["error"]["code"] == "suite-invalid" and output["error"]["message"]
    assert output["error"]["path"] in (None, str(path))
    assert not trial.exists() and not trial.with_name("trial.study").exists() and not marker.exists()


@pytest.mark.parametrize("code", ["suite-invalid", "input-exists", "unsupported-profile",
    "environment-unqualified", "task-unqualified", "isolation-unqualified", "loading-unqualified"])
def test_named_preflight_errors_remain_json_errors_not_tracebacks(tmp_path, monkeypatch, capsys, code):
    # Only command adaptation is doubled here; no fake qualification is admitted.
    path, _, _ = marked_suite(tmp_path)
    def unavailable(*args, **kwargs):
        raise ValueError(f"{code}: Deliberate unavailable gate without a path")
    monkeypatch.setattr(suite_api, "prepare_suite", unavailable)
    if hasattr(trials, "prepare_suite"):
        monkeypatch.setattr(trials, "prepare_suite", unavailable)
    status, output, stderr = invoke(monkeypatch, capsys, "prepare", tmp_path / "trial",
                                     "--suite", path, "--task", "normalize", "--arm", "alpha")
    assert status == 2 and not stderr
    assert set(output) == {"success", "error"} and output["success"] is False
    assert set(output["error"]) == {"code", "message", "path"}
    assert output["error"]["code"] == code and output["error"]["message"]
    assert output["error"]["path"] is None


LEGACY_OVERRIDES = [
    ["--case", "summary"], ["--depth", "standard"], ["--plugin-root", str(trials.ROOT)],
    ["--plain-agent"], ["--telemetry", "telemetry.json"], ["--timeout", "600"],
    ["--interrupt-after", "1", "--resume-runner", '["unused"]'], ["--runner", "unused"],
]


@pytest.mark.parametrize("override", LEGACY_OVERRIDES)
def test_suite_mode_rejects_explicit_legacy_overrides_even_at_their_defaults(tmp_path, monkeypatch, capsys, override):
    path, _, marker = marked_suite(tmp_path)
    trial = tmp_path / "trial"
    code, _, _ = invoke(monkeypatch, capsys, "prepare", trial,
                        "--suite", path, "--task", "normalize", "--arm", "alpha", *override)
    assert code == 2 and not trial.exists() and not trial.with_name("trial.study").exists() and not marker.exists()


@pytest.mark.parametrize("selector", [["--task", "normalize"], ["--arm", "alpha"], []])
def test_missing_suite_selector_keeps_argparse_exit_two(tmp_path, monkeypatch, capsys, selector):
    path, _, _ = marked_suite(tmp_path)
    code, output, stderr = invoke(monkeypatch, capsys, "prepare", tmp_path / "trial", "--suite", path, *selector)
    assert code == 2 and output is None and "usage:" in stderr
    assert not (tmp_path / "trial").exists()


@pytest.mark.parametrize("extra", [["--task", "normalize"], ["--arm", "alpha"], ["--qualify-loading"]])
def test_suite_only_flags_do_not_change_legacy_commands(tmp_path, monkeypatch, capsys, extra):
    monkeypatch.setattr(trials, "prepare", forbidden)
    code, _, _ = invoke(monkeypatch, capsys, "prepare", tmp_path / "trial", *extra)
    assert code == 2 and not (tmp_path / "trial").exists()


@pytest.mark.parametrize("operation", ["prepare", "run", "check", "review-template"])
def test_legacy_omitted_options_keep_the_original_dispatch_defaults(tmp_path, monkeypatch, capsys, operation):
    trial = tmp_path / "trial"
    calls = []
    def prepare(destination, case, depth, *, plugin_root, plain_agent):
        calls.append(("prepare", destination, case, depth, plugin_root, plain_agent))
        destination.mkdir()
        (destination / "trial.json").write_text(json.dumps({"case": case}))
        return {"legacy": "prepare"}
    def sessions(destination, runner, timeout, *, resume_runner, interrupt_after):
        calls.append(("sessions", destination, runner, timeout, resume_runner, interrupt_after))
        return {"returncode": 0}
    def check(destination, telemetry, *, review):
        calls.append(("check", destination, telemetry, review))
        return {"success": True, "legacy": "check"}
    def template(destination):
        calls.append(("template", destination))
        return {"legacy": "template"}
    for name, function in (("prepare", prepare), ("run_sessions", sessions),
                           ("check", check), ("review_template", template)):
        monkeypatch.setattr(trials, name, function)
    if operation in {"check", "review-template"}:
        trial.mkdir()
        (trial / "trial.json").write_text('{"case":"summary"}')
    argv = [operation, trial] + (["--runner", "unused"] if operation == "run" else [])
    code, output, stderr = invoke(monkeypatch, capsys, *argv)
    assert code == 0 and not stderr and output["legacy"]
    expected = []
    if operation in {"prepare", "run"}:
        expected.append(("prepare", trial, "summary", None, trials.ROOT, False))
    if operation == "run":
        expected.append(("sessions", trial, ["unused"], 600, None, None))
    if operation in {"run", "check"}:
        expected.append(("check", trial, None, None))
    if operation == "review-template":
        expected.append(("template", trial))
    assert calls == expected
