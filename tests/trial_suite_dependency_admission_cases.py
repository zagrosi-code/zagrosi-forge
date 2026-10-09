"""Allowed lock edits require admitted policy; all qualification data is synthetic.

Reuses the independent static proof fixture and fictional role executor. This
does not install packages, execute qualifications, or add a candidate-proof schema.
"""
from copy import deepcopy

import pytest

from test_trial_suite_qualification import qualified_shape, persist_proofs, replace_object
from trial_suite_assessment_cases import (
    RoleBoundary, assess_suite, assessment_fixture, codes, last_assessment,
    load_ref, review_for, save_review,
)
from trial_suite_fixtures import digest, regular_entries
from trial_suite_prepare_cases import PYTHON, save_manifest
import coding_trial_isolation
from coding_trial_suite import run_suite


def declared_lock_policy(shape, tmp_path):
    path, suite = shape["path"], shape["suite"]
    template_path, template = assessment_fixture(tmp_path / "assessment-template")
    for name in ("profile.json", "checks/oracle.py", "checks/scenario.json"):
        (path.parent / name).write_bytes((template_path.parent / name).read_bytes())
    suite["host"]["isolation"] = deepcopy(template["host"]["isolation"])
    task = suite["tasks"]["normalize"]
    task["checks"]["oracle"] = deepcopy(template["tasks"]["normalize"]["checks"]["oracle"])
    task["scope"]["protected"].remove("deps.lock")
    task["scope"]["allowed_changes"].append("deps.lock")
    task["dependencies"]["allow_lock_changes"] = True
    image = suite["host"]["isolation"]["image_digest"]
    shape["runtime"]["image_digest"] = image
    shape["environment"]["bindings"]["image_digest"] = image
    replace_object(shape, "runtime", shape["runtime"])
    commands = [*task["checks"]["native"], task["checks"]["oracle"], task["checks"]["worker"]]
    resources = tuple(sorted({name for command in commands
                              for name in [command["entry"], *command["support"]] if name is not None}))
    shape["task"]["bindings"]["checks_sha256"] = digest(regular_entries(path.parent, resources))
    shape["task"]["bindings"]["task_policy_sha256"] = digest(
        {key: value for key, value in task.items() if key != "admission"})
    persist_proofs(shape)
    return path, suite


class TestSuiteDependencyAdmission:
    @pytest.mark.parametrize("proofs", ["missing-task", "missing-both", "bound-policy"])
    def test_allowed_lock_edits_need_bound_policy_but_admitted_edits_remain_legal(
            self, qualified_shape, tmp_path, monkeypatch, proofs):
        path, suite = declared_lock_policy(qualified_shape, tmp_path)
        task = suite["tasks"]["normalize"]
        if proofs != "bound-policy":
            task["admission"] = None
        if proofs == "missing-both":
            task["dependencies"]["environment"] = None
        save_manifest(path, suite)

        def edit_lock(workspace):
            lock = workspace / "deps.lock"
            # The declared environment still supports this permitted annotation;
            # policy admission is distinct from permission and from lock bytes.
            lock.write_bytes(lock.read_bytes() + b"# permitted predeclared annotation\n")

        boundary = RoleBoundary(writer=edit_lock)
        monkeypatch.setattr(coding_trial_isolation, "execute_isolated", boundary)
        trial = tmp_path / "trial"
        run_suite(trial, path, "normalize", "alpha", assessor_python=PYTHON)
        verdict = last_assessment(trial)
        behavior = load_ref(trial, verdict["behavior"])
        assert behavior["checks"]["scope"]["status"] == "passed", "The lock edit must be in allowed scope"
        reviewed = assess_suite(trial, review=save_review(trial, review_for(trial, verdict)))
        if proofs == "bound-policy":
            assert behavior["checks"]["dependencies"]["status"] == "passed"
            assert reviewed["common_quality"] == "passed" and reviewed["synthetic_validation"] == "passed"
            assert reviewed["study_eligible"] is False and reviewed["acceptance"] != "passed"
        else:
            assert behavior["checks"]["dependencies"]["status"] == "failed"
            assert "dependency-invalid" in codes(verdict) | codes(behavior)
            assert reviewed["common_quality"] == "failed" and reviewed["synthetic_validation"] != "passed"
