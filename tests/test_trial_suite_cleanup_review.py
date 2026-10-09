"""Pure review semantics; synthetic identities never attest executed behavior."""
from copy import deepcopy
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from coding_trial_assessment import _review


def review_context(*, verdict="fail", meaningful=False, paths=()):
    attempt = {"identity": {"task": "cleanup", "baseline_sha256": "a" * 64},
               "candidate": {"assessed_sha256": "b" * 64}}
    behavior = {"path": "assessor/assessments/000001/behavior.json", "sha256": "c" * 64}
    task = {"cleanup_required": True, "scope": {
        "implementation": ["backend/app"], "tests": ["specs"], "config": ["config"]}}
    baseline = {"backend/app/service.py": {"sha256": "d" * 64},
                "config/settings.json": {"sha256": "e" * 64}}
    candidate = deepcopy(baseline)
    cleanup = {"meaningful": meaningful, "changed_files": list(paths),
               "rationale": "The implementation is unchanged, so no useful cleanup was delivered.",
               "regression_evidence": "The supplied feature and preservation check records were inspected."}
    form = {"schema": "coding-trial-review/v1", "block": "cleanup",
            "reviewer": "Independent fixture reviewer", "independent": True,
            "preferred": [], "rationale": "Reject unchanged implementation after inspecting the code.",
            "candidates": {"C001": {
                "baseline_sha256": "a" * 64, "candidate_sha256": "b" * 64,
                "assessment_sha256": "c" * 64, "verdict": verdict, "findings": [],
                "criteria": {key: "backend/app/service.py: unchanged implementation was inspected."
                             for key in ("readability", "cohesion", "duplication", "regressions")},
                "cleanup": cleanup}}}
    return form, attempt, behavior, task, baseline, candidate


def test_honest_failed_cleanup_with_no_changed_files_is_complete_review():
    context = review_context()
    original = deepcopy(context)
    passed, cleanup = _review(*context)
    assert passed is False
    assert cleanup == context[0]["candidates"]["C001"]["cleanup"]
    assert cleanup["meaningful"] is False and cleanup["changed_files"] == []
    assert context == original


def test_passing_cleanup_still_requires_changed_implementation():
    with pytest.raises(ValueError):
        _review(*review_context(verdict="pass", meaningful=True))


@pytest.mark.parametrize("path", ["backend/app/service.py", "config/settings.json", "outside.py"])
def test_failing_cleanup_still_validates_every_supplied_path(path):
    with pytest.raises(ValueError):
        _review(*review_context(paths=[path]))


def test_passing_cleanup_with_an_actual_implementation_change_remains_valid():
    context = review_context(verdict="pass", meaningful=True, paths=["backend/app/service.py"])
    context[-1]["backend/app/service.py"] = {"sha256": "f" * 64}
    passed, cleanup = _review(*context)
    assert passed is True and cleanup["changed_files"] == ["backend/app/service.py"]
