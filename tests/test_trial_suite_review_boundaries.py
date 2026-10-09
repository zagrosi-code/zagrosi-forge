"""Independent review boundary regressions; no real provider or Docker calls.

The existing logical fixture supplies executions. Failed review evidence below
is saved by the real assessor after an injected final input-read failure.
"""
from copy import deepcopy
import hashlib
from pathlib import Path

import pytest

from test_trial_suite_comparison import (
    blind_and_review, compare, comparison_case, comparisons, invoke, load_json,
    matrix_cli,
)
from test_trial_suite_comparison_lineage import guard_execution
from trial_suite_fixtures import link
from trial_suite_prepare_cases import bytes_at
from coding_trial_inventory import inventory
import coding_trial_assessment as assessment


@pytest.mark.parametrize("redirect", ["review-history", "review-history/packet-inventories"])
def test_blind_never_writes_private_inventory_through_redirected_parent(tmp_path, monkeypatch, capsys, redirect):
    path, _, directory, _ = comparison_case(tmp_path, monkeypatch)
    compare(path, directory, monkeypatch, capsys)
    external = tmp_path / "external-owned-by-test"
    external.mkdir()
    (external / "sentinel.txt").write_text("These existing bytes must remain untouched.\n")
    destination = directory / redirect
    destination.parent.mkdir(parents=True, exist_ok=True)
    link(destination, str(external), directory=True)
    original_files, original_inventory = bytes_at(external), inventory(external)
    guard_execution(monkeypatch)
    status, _, _ = invoke(matrix_cli, monkeypatch, capsys, "blind", directory)
    assert bytes_at(external) == original_files
    assert inventory(external) == original_inventory
    assert status != 0


def test_saved_failed_assessment_cannot_be_overruled_by_projected_material_pass(tmp_path, monkeypatch, capsys):
    path, _, directory, _ = comparison_case(tmp_path, monkeypatch)
    compare(path, directory, monkeypatch, capsys)
    blind_and_review(directory, monkeypatch, capsys)
    guard_execution(monkeypatch)
    real_assess, failed = assessment.assess_suite, []

    def final_read_failure(trial, *, review=None):
        if failed:
            return real_assess(trial, review=review)
        actual_read, reads = assessment._read_study, []

        def read_once(*args, **kwargs):
            reads.append(args)
            if len(reads) == 2:
                raise ValueError("input-drift: Synthetic final read rejected after material review")
            return actual_read(*args, **kwargs)

        with monkeypatch.context() as local:
            local.setattr(assessment, "_read_study", read_once)
            verdict = real_assess(trial, review=review)
        assert len(reads) == 2
        attempt = load_json(Path(trial) / "trial.json")
        failed.append((Path(trial), deepcopy(verdict), attempt["assessments"][-1]))
        return verdict

    monkeypatch.setattr(assessment, "assess_suite", final_read_failure)
    status, result, stderr = invoke(matrix_cli, monkeypatch, capsys, "apply-reviews", directory)
    assert len(failed) == 1
    trial, verdict, reference = failed[0]
    raw = (trial / reference["path"]).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == reference["sha256"]
    assert load_json(trial / reference["path"]) == verdict
    assert verdict["common_quality"] == verdict["synthetic_validation"] == "failed"
    assert "input-drift" in {error["code"] for error in verdict["errors"]}
    assert status == 1 and not stderr and result["success"] is False
    assert result["study_complete"] is False
    before = bytes_at(directory)
    report = comparisons.report_suite(directory)
    assert report["success"] is False and report["study_complete"] is False
    assert bytes_at(directory) == before
    monkeypatch.setattr(assessment, "assess_suite", real_assess)
    status, recovered, stderr = invoke(matrix_cli, monkeypatch, capsys, "apply-reviews", directory)
    assert status == 0 and not stderr
    assert recovered["success"] is True and recovered["study_complete"] is True
    assert (trial / reference["path"]).read_bytes() == raw
