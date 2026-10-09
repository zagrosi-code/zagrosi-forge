"""Independent saved-lineage controls using the explicit logical role doubles.

Only the actual private oracle/controller and temporary Git run when activated.
Review/report boundaries are process-free; no native qualification is asserted.
"""
import json
import subprocess

import pytest

from test_trial_suite_comparison import (
    attempts, blind_and_review, compare, comparison_case, comparisons, forbidden,
    invoke, isolation, load_json, matrix_cli, trial_cli,
)
from trial_suite_assessment_cases import last_assessment, review_for, save_review
from trial_suite_prepare_cases import bytes_at
import coding_trial_assessment as assessment
from coding_trial_assessment import assess_suite


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def guard_execution(monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)


def applied_case(tmp_path, monkeypatch, capsys):
    path, _, directory, boundary = comparison_case(tmp_path, monkeypatch)
    compare(path, directory, monkeypatch, capsys)
    form = blind_and_review(directory, monkeypatch, capsys)
    status, report, stderr = invoke(matrix_cli, monkeypatch, capsys, "apply-reviews", directory)
    assert status == 0 and not stderr and report["study_complete"] is True
    return directory, form, boundary


def test_terminal_basis_assessments_remain_an_exact_current_prefix(tmp_path, monkeypatch, capsys):
    path, _, directory, _ = comparison_case(tmp_path, monkeypatch)
    compare(path, directory, monkeypatch, capsys)
    trial, before = attempts(directory)[0]
    original = before["assessments"][:]
    assert original
    status, _, stderr = invoke(trial_cli, monkeypatch, capsys, "check", trial)
    assert status == 1 and not stderr
    guard_execution(monkeypatch)
    valid = comparisons.report_suite(directory)
    assert all(row["status"] != "invalid" for row in valid["attempts"])
    current = load_json(trial / "trial.json")
    assert current["assessments"][:len(original)] == original
    assert len(current["assessments"]) > len(original)
    del current["assessments"][0]
    save(trial / "trial.json", current)
    frozen = bytes_at(directory)
    report = comparisons.report_suite(directory)
    assert report["attempts"][before["scheduled_position"]]["status"] == "invalid"
    assert report["study_complete"] is False and report["success"] is False
    assert report["accepted_outcomes"]["scheduled"] == 3
    assert bytes_at(directory) == frozen


def test_applied_assessment_must_stay_linked_after_a_valid_later_review(tmp_path, monkeypatch, capsys):
    directory, _, _ = applied_case(tmp_path, monkeypatch, capsys)
    trial, before = attempts(directory)[0]
    applied = before["assessments"][-1]
    guard_execution(monkeypatch)
    form = review_for(trial, last_assessment(trial))
    reviewed = assess_suite(trial, review=save_review(trial, form, "extra-review.json"))
    assert reviewed["common_quality"] == "passed"
    assert comparisons.report_suite(directory)["success"] is True
    current = load_json(trial / "trial.json")
    assert applied in current["assessments"] and current["assessments"][-1] != applied
    current["assessments"].remove(applied)
    save(trial / "trial.json", current)
    frozen = bytes_at(directory)
    report = comparisons.report_suite(directory)
    assert report["study_complete"] is False and report["success"] is False
    assert report["accepted_outcomes"]["accepted"] == 0
    assert bytes_at(directory) == frozen


@pytest.mark.parametrize("change", ["form", "candidate"])
def test_unpreferred_row_tampering_prevents_every_application_write(tmp_path, monkeypatch, capsys, change):
    path, _, directory, _ = comparison_case(tmp_path, monkeypatch)
    compare(path, directory, monkeypatch, capsys)
    form = blind_and_review(directory, monkeypatch, capsys)
    value = load_json(form)
    label = next(label for label in value["candidates"] if label not in value["preferred"])
    if change == "form":
        value["candidates"][label]["candidate_sha256"] = "0" * 64
        save(form, value)
    else:
        code = form.parent / label / "candidate/backend/app/service.py"
        code.write_text(code.read_text() + "\n# Changed after packet creation.\n")
    frozen = {str(trial): bytes_at(trial) for trial, _ in attempts(directory)}
    guard_execution(monkeypatch)
    status, _, _ = invoke(matrix_cli, monkeypatch, capsys, "apply-reviews", directory)
    assert status != 0
    assert {str(trial): bytes_at(trial) for trial, _ in attempts(directory)} == frozen
    report = comparisons.report_suite(directory)
    assert report["study_complete"] is False and report["success"] is False


def test_all_no_output_is_complete_without_fictitious_reviews_or_application(tmp_path, monkeypatch, capsys):
    path, _, directory, boundary = comparison_case(tmp_path, monkeypatch)

    def unavailable(suite, suite_root, layout, argv, **kwargs):
        assert layout["role"] == "writer"
        boundary.calls.append((layout["role"], layout["arm"]))
        raise ValueError("unsupported-profile: Explicit synthetic unavailable boundary")

    monkeypatch.setattr(isolation, "execute_isolated", unavailable)
    initial = compare(path, directory, monkeypatch, capsys)
    assert len(boundary.calls) == 3
    assert initial["study_complete"] is True and initial["success"] is False
    assert all(row["terminal"] is True and row["produced"] is False
               and row["material_review"] == "not_applicable" for row in initial["attempts"])
    assert initial["accepted_outcomes"]["scheduled"] == 3
    assert initial["accepted_outcomes"]["elapsed_seconds"]["observed_attempts"] == 3
    assert initial["accepted_outcomes"]["accepted"] == 0
    guard_execution(monkeypatch)
    before = bytes_at(directory)
    assert comparisons.apply_suite_reviews(directory) == initial
    assert bytes_at(directory) == before
    status, _, stderr = invoke(matrix_cli, monkeypatch, capsys, "blind", directory)
    assert status == 0 and not stderr
    # An all-no-output block cannot acquire a code-review requirement by blinding.
    for form in (directory / "blind").rglob("review.json"):
        form.unlink()
    before = bytes_at(directory)
    report = comparisons.apply_suite_reviews(directory)
    assert report["study_complete"] is True and report["success"] is False
    assert not (directory / "review-history/applications").exists()
    assert bytes_at(directory) == before


def test_latest_failed_application_never_falls_back_to_a_completed_one(tmp_path, monkeypatch, capsys):
    directory, _, _ = applied_case(tmp_path, monkeypatch, capsys)
    guard_execution(monkeypatch)
    real_assess, calls = assessment.assess_suite, []

    def fail_second(trial, *, review=None):
        calls.append((trial, review))
        if len(calls) == 2:
            raise ValueError("review-stale: Explicit synthetic second-row application failure")
        return real_assess(trial, review=review)

    monkeypatch.setattr(assessment, "assess_suite", fail_second)
    status, report, stderr = invoke(matrix_cli, monkeypatch, capsys, "apply-reviews", directory)
    assert status == 1 and not stderr and len(calls) == 2
    assert report["study_complete"] is False and report["success"] is False
    records = [load_json(path) for path in sorted((directory / "review-history/applications").glob("*/result.json"))]
    assert [record["status"] for record in records] == ["completed", "failed"]
    assert len(records[-1]["assessments"]) == 1 and records[-1]["error"] is not None
    before = bytes_at(directory)
    current = comparisons.report_suite(directory)
    assert current["study_complete"] is False and current["success"] is False
    assert current["accepted_outcomes"]["scheduled"] == 3
    assert len(calls) == 2 and bytes_at(directory) == before
