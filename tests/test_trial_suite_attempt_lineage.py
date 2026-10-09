"""A review reuses one frozen attempt basis, not interchangeable current receipts.

Initial assessment uses the existing explicit executor fixture. Review controls
forbid further execution and make no native/provider/isolation claim.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from coding_trial_assessment import assess_suite
import coding_trial_isolation as isolation
import coding_trial_process as process
from coding_trial_suite import _read_study
from trial_suite_assessment_cases import (
    RoleBoundary, codes, load_json, load_ref, review_for, run_case, save_review,
)
from trial_suite_prepare_cases import bytes_at, read_reference
from trial_suite_fixtures import link


def save(path, value):
    raw = (json.dumps(value, indent=2) + "\n").encode()
    with path.open("xb") as stream:
        stream.write(raw)
    return {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest()}


def refuse_review_execution(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("A material review cannot repair lineage by rerunning execution")
    monkeypatch.setattr(isolation, "execute_isolated", forbidden)
    monkeypatch.setattr(process, "execute", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)


def replace_attempt(trial, attempt):
    (trial / "trial.json").write_text(json.dumps(attempt, indent=2) + "\n")


class TestAttemptLineage:
    @pytest.mark.parametrize("change", ["successful-replacement", "timing-only"])
    def test_review_cannot_replace_the_runner_bound_to_existing_behavior(self, tmp_path, monkeypatch, change):
        boundary = RoleBoundary(writer_code=7 if change == "successful-replacement" else 0)
        trial, _, _, first = run_case(tmp_path, monkeypatch, boundary=boundary)
        behavior = load_ref(trial, first["behavior"])
        assert behavior["errors"] == [], "The counterexample isolates writer provenance from behavior"
        if change == "successful-replacement":
            assert "runner-failed" in codes(first) and first["common_quality"] == "failed"
        attempt = load_json(trial / "trial.json")
        original_runner_path, original_runner = read_reference(trial, attempt["runner"])
        replacement = json.loads(original_runner)
        if change == "successful-replacement":
            replacement["process"]["returncode"] = 0
        else:
            replacement["process"]["seconds"] += 1
        reference = save(trial / "private/replacement-writer.json", replacement)
        reference["path"] = "private/replacement-writer.json"
        attempt["runner"] = reference
        original = bytes_at(trial / "assessor/assessments/000001")
        review = save_review(trial, review_for(trial, first))
        replace_attempt(trial, attempt)
        refuse_review_execution(monkeypatch)
        result = assess_suite(trial, review=review)
        assert result["kind"] == "review" and result["common_quality"] == "failed"
        assert result["synthetic_validation"] == "failed" and "review-stale" in codes(result)
        assert original_runner_path.read_bytes() == original_runner
        assert bytes_at(trial / "assessor/assessments/000001") == original

    @pytest.mark.parametrize("changed", ["candidate-audit", "study-schedule"])
    def test_review_rejects_valid_looking_candidate_or_study_rebinding(self, tmp_path, monkeypatch, changed):
        trial, _, _, first = run_case(tmp_path, monkeypatch)
        attempt = load_json(trial / "trial.json")
        original = bytes_at(trial / "assessor/assessments/000001")
        review = save_review(trial, review_for(trial, first))
        if changed == "candidate-audit":
            original_path, raw = read_reference(trial, attempt["candidate"]["git"]["audit"])
            destination = trial / "assessor/copied-delivery.json"
            destination.write_bytes(raw)
            attempt["candidate"]["git"]["audit"] = {
                "path": destination.relative_to(trial).as_posix(), "sha256": hashlib.sha256(raw).hexdigest()}
            assert destination.read_bytes() == original_path.read_bytes()
        else:
            original_path = Path(attempt["study"]["path"])
            changed_study = load_json(original_path)
            changed_study["schedule"][0]["repeat"] += 1
            attempt["study"] = save(original_path.with_name("replacement-study.json"), changed_study)
            # All frozen resources remain valid; creation identity is what changed.
            observed, _, _ = _read_study(attempt["study"])
            assert observed == changed_study
        replace_attempt(trial, attempt)
        refuse_review_execution(monkeypatch)
        result = assess_suite(trial, review=review)
        assert result["common_quality"] == "failed" and result["synthetic_validation"] == "failed"
        assert "review-stale" in codes(result)
        assert bytes_at(trial / "assessor/assessments/000001") == original

    def test_only_assessment_appends_may_differ_across_repeated_reviews(self, tmp_path, monkeypatch):
        trial, _, boundary, first = run_case(tmp_path, monkeypatch)
        original = load_json(trial / "trial.json")
        basis = load_ref(trial, load_ref(trial, first["behavior"])["attempt_basis"])
        assert {k: v for k, v in original.items() if k != "assessments"} == {
            k: v for k, v in basis.items() if k != "assessments"}
        calls = deepcopy(boundary.calls)
        review = save_review(trial, review_for(trial, first))
        refuse_review_execution(monkeypatch)
        for number in (2, 3):
            result = assess_suite(trial, review=review)
            assert result["number"] == number and result["behavior"] == first["behavior"]
            assert result["common_quality"] == result["synthetic_validation"] == "passed"
            current = load_json(trial / "trial.json")
            assert len(current["assessments"]) == number
            assert {k: v for k, v in current.items() if k != "assessments"} == {
                k: v for k, v in original.items() if k != "assessments"}
        assert boundary.calls == calls

    @pytest.mark.parametrize("target", ["qualification", "lifecycle"])
    def test_review_requires_unchanged_nested_writer_evidence(self, tmp_path, monkeypatch, target):
        trial, _, _, first = run_case(tmp_path, monkeypatch)
        attempt = load_json(trial / "trial.json")
        writer_path, writer_raw = read_reference(trial, attempt["runner"])
        writer = json.loads(writer_raw)
        original_behavior = bytes_at(trial / "assessor/assessments/000001")
        review = save_review(trial, review_for(trial, first))
        if target == "qualification":
            selected = writer["isolation"]["qualification"]["path"]
        else:
            selected = writer["isolation"]["lifecycle"]["evidence"][0]
        path = Path(selected)
        path = path if path.is_absolute() else trial / path
        assert path.is_file() and path.is_relative_to(trial / "private/writer")
        before = path.read_bytes()
        path.write_bytes(before + b" ")  # Still parseable; exact evidence identity changed.
        refuse_review_execution(monkeypatch)
        result = assess_suite(trial, review=review)
        assert result["common_quality"] == result["synthetic_validation"] == "failed"
        assert codes(result) & {"receipt-invalid", "candidate-drift", "input-drift"}
        assert writer_path.read_bytes() == writer_raw
        assert bytes_at(trial / "assessor/assessments/000001") == original_behavior
        assert path.read_bytes() == before + b" "

    def test_review_rejects_same_byte_writer_lifecycle_alias(self, tmp_path, monkeypatch):
        trial, _, _, first = run_case(tmp_path, monkeypatch)
        assert first["common_quality"] == "pending" and codes(first) == {"review-pending"}
        attempt = load_json(trial / "trial.json")
        writer_path, writer_raw = read_reference(trial, attempt["runner"])
        writer = json.loads(writer_raw)
        qualification = writer["isolation"]["qualification"]
        qualification_path = Path(qualification["path"])
        qualification_raw = qualification_path.read_bytes()
        assert hashlib.sha256(qualification_raw).hexdigest() == qualification["sha256"]
        selected = Path(writer["isolation"]["lifecycle"]["evidence"][0])
        selected = selected if selected.is_absolute() else writer_path.parent / selected
        assert selected.is_file() and not selected.is_symlink()
        assert selected.is_relative_to(trial / "private/writer")
        retained = selected.read_bytes()
        sibling = selected.with_name("same-byte-" + selected.name)
        with sibling.open("xb") as stream:
            stream.write(retained)
        original_behavior = bytes_at(trial / "assessor/assessments/000001")
        review = save_review(trial, review_for(trial, first))
        selected.unlink()
        link(selected, sibling.name)
        assert selected.is_symlink() and selected.read_bytes() == retained
        refuse_review_execution(monkeypatch)
        result = assess_suite(trial, review=review)
        assert result["common_quality"] == result["synthetic_validation"] == "failed"
        assert codes(result) & {"receipt-invalid", "candidate-drift", "input-drift"}
        assert writer_path.read_bytes() == writer_raw
        assert qualification_path.read_bytes() == qualification_raw
        assert bytes_at(trial / "assessor/assessments/000001") == original_behavior
        assert selected.is_symlink() and sibling.read_bytes() == retained
