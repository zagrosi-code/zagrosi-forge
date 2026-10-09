"""Immutable behavior receipts and separately bound material-review decisions."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from coding_trial_comparison import CRITERIA
from coding_trial_evidence import _text
from coding_trial_inventory import contains_path, fingerprint, inventory, project, relative_path
from coding_trial_observation_client import observe
from coding_trial_qualification import fields, parse_json, read_bytes, require, strings
from coding_trial_study import _absolute, _read_study, _reference, _save


def _save_ref(trial, path, value):
    reference = _save(path, value)
    reference["path"] = path.relative_to(trial).as_posix()
    return reference


def _read_ref(trial, reference):
    return parse_json(_reference(trial, reference))


def _error(code, message):
    return {"code": code, "message": str(message), "path": None}


def _failure(exc, default="receipt-invalid"):
    code = str(exc).split(":", 1)[0]
    if code not in {"input-drift", "candidate-drift", "receipt-invalid", "scope-invalid",
                    "dependency-invalid", "runner-failed", "unsupported-profile", "review-stale"}:
        code = default
    return _error(code, exc)



def _execution_evidence(trial, reference, identity):
    value = _read_ref(trial, reference)
    require(value["schema"] == "coding-trial-check-execution/v1" and value["identity"] == identity,
            "receipt-invalid: Execution identity differs")
    isolation = value["isolation"]
    _reference(trial, isolation["qualification"])
    for evidence in isolation["lifecycle"]["evidence"]:
        _reference(trial, evidence)


def _verified_behavior(trial, reference, identity):
    """Follow the fixed receipt schemas; arbitrary JSON paths grant no read authority."""
    behavior = _read_ref(trial, reference)
    fields(behavior, "schema identity attempt_basis descriptor inputs candidate git native oracle observations checks workflow errors",
           "Behavior receipt")
    require(behavior["schema"] == "coding-trial-behavior/v1" and behavior["identity"] == identity,
            "receipt-invalid: Behavior identity differs")
    _reference(trial, behavior["attempt_basis"])
    _reference(trial, behavior["descriptor"])
    for row in behavior["native"]:
        _execution_evidence(trial, row["record"], identity)
    for reference in behavior["oracle"].values():
        if reference is not None:
            _reference(trial, reference)
    for reference in behavior["observations"]:
        observation = _read_ref(trial, reference)
        fields(observation, "schema identity number request execution response errors", "Observation receipt")
        require(observation["schema"] == "coding-trial-observation-result/v1" and observation["identity"] == identity,
                "receipt-invalid: Observation identity differs")
        for name in ("request", "response"):
            if observation[name] is not None:
                _reference(trial, observation[name])
        if observation["execution"] is not None:
            _execution_evidence(trial, observation["execution"], identity)
    for reference in behavior["workflow"]["evidence"]:
        _reference(trial, reference)
    return behavior


def _review(form, attempt, behavior_ref, task, baseline, candidate):
    """Validate an external attestation; never treat reviewer identity as authenticated."""
    fields(form, "schema block reviewer independent preferred rationale candidates", "Material review")
    require(form["schema"] == "coding-trial-review/v1" and form["block"] == attempt["identity"]["task"],
            "review-stale: Review task differs")
    require(form["independent"] is True and _text(form["reviewer"]) and _text(form["rationale"]),
            "review-stale: An independent material review is required")
    strings(form["preferred"], "Preferred candidates", unique=True)
    require(set(form["preferred"]) <= {"C001"} and type(form["candidates"]) is dict
            and set(form["candidates"]) == {"C001"}, "review-stale: Review candidates differ")
    row = form["candidates"]["C001"]
    fields(row, "baseline_sha256 candidate_sha256 assessment_sha256 verdict findings criteria cleanup", "Candidate review")
    require(row["baseline_sha256"] == attempt["identity"]["baseline_sha256"]
            and row["candidate_sha256"] == attempt["candidate"]["assessed_sha256"]
            and row["assessment_sha256"] == behavior_ref["sha256"], "review-stale: Review evidence changed")
    require(row["verdict"] in {"pass", "fail"}, "review-stale: Invalid material verdict")
    strings(row["findings"], "Material findings")
    require(type(row["criteria"]) is dict and set(row["criteria"]) == set(CRITERIA)
            and all(_text(value) for value in row["criteria"].values()), "review-stale: Incomplete criteria evidence")
    cleanup = row["cleanup"]
    if task["cleanup_required"]:
        fields(cleanup, "meaningful changed_files rationale regression_evidence", "Cleanup review")
        strings(cleanup["changed_files"], "Cleanup paths", nonempty=True, unique=True)
        scope = task["scope"]
        for name in cleanup["changed_files"]:
            relative_path(name)
            require(any(contains_path(root, name) for root in scope["implementation"])
                    and not any(contains_path(root, name) for root in scope["tests"] + scope["config"])
                    and baseline.get(name) != candidate.get(name), "review-stale: Cleanup needs changed implementation paths")
        require(type(cleanup["meaningful"]) is bool and _text(cleanup["rationale"])
                and _text(cleanup["regression_evidence"]), "review-stale: Incomplete cleanup evidence")
    else:
        require(cleanup is None, "review-stale: Unexpected cleanup requirement")
    passed = row["verdict"] == "pass" and not row["findings"] and (
        cleanup is None or cleanup["meaningful"] is True)
    require(not form["preferred"] or passed, "review-stale: Preferred candidate has material failures")
    return passed, cleanup


def _oracle_receipt(trial, receipt_ref, task_id, candidate_sha256, task):
    require(receipt_ref is not None, "receipt-invalid: Private oracle produced no valid receipt")
    value = _read_ref(trial, receipt_ref)
    fields(value, "schema task candidate_sha256 checks", "Oracle receipt")
    require(value["schema"] == "coding-trial-oracle/v1" and value["task"] == task_id
            and value["candidate_sha256"] == candidate_sha256, "receipt-invalid: Oracle identity differs")
    require(type(value["checks"]) is list, "receipt-invalid: Oracle checks must be a list")
    expected = set(task["checks"]["feature_ids"] + task["checks"]["preservation_ids"])
    actual = []
    for check in value["checks"]:
        fields(check, "id status detail", "Oracle check")
        require(isinstance(check["id"], str) and check["status"] in {"passed", "failed"}
                and isinstance(check["detail"], str), "receipt-invalid: Invalid oracle check")
        actual.append(check["id"])
    require(set(actual) == expected and len(actual) == len(expected), "receipt-invalid: Oracle checks differ")
    require(all(row["status"] == "passed" for row in value["checks"]), "receipt-invalid: Private oracle checks failed")


def _behavior(trial, attempt, study, suite, suite_root, folder, basis, verify_inputs):
    from coding_trial_assessment_execution import prepare_worker_roots, run_checks
    from coding_trial_delivery import read_delivery

    task_id, arm_id = attempt["identity"]["task"], attempt["identity"]["arm"]
    task = suite["tasks"][task_id]
    candidate, baseline, audit = read_delivery(trial, attempt, suite, suite_root)
    client = Path(study["evaluator"]["root"]) / "tools/coding_trial_observation_client.py"
    descriptor = {
        "schema": "coding-trial-assessment-input/v1", "identity": attempt["identity"],
        "suite_root": str(suite_root), "manifest": study["manifest"],
        "input_inventory_sha256": study["input_inventory_sha256"], "evaluator_sha256": study["evaluator_sha256"],
        "controller": study["controller"],
        "observation_client": {"path": str(client), "sha256": hashlib.sha256(read_bytes(client.parent, client.name)).hexdigest()},
        "candidate": {**{key: attempt["candidate"][key] for key in ("full_inventory_sha256", "assessed_sha256")},
                      "path": str(trial / attempt["candidate"]["path"])},
        "worker": {"command": task["checks"]["worker"],
                   "roots": prepare_worker_roots(suite, suite_root, trial, attempt, folder),
                   "protocol": "coding-trial-observation/v1"}, "evidence_dir": str(folder),
    }
    descriptor_ref = _save_ref(trial, folder / "assessment.json", descriptor)
    result = run_checks(suite, suite_root, trial, folder / "assessment.json", verify_inputs=verify_inputs)
    errors = [*audit["errors"], *result["errors"]]
    try:
        _oracle_receipt(trial, result["oracle"]["receipt"], task_id, attempt["candidate"]["assessed_sha256"], task)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        errors.append(_failure(exc))
    inputs_after = candidate_after = None
    try:
        verify_inputs()
        inputs_after = study["input_inventory_sha256"]
        candidate_after = fingerprint(inventory(trial / attempt["candidate"]["path"]))
    except (OSError, ValueError, TypeError, KeyError) as exc:
        errors.append(_failure(exc, "input-drift"))
    checks = dict(audit["checks"])
    checks["integrity"] = {"status": "passed" if inputs_after is not None else "failed",
                           "detail": "Frozen inputs and delivery rechecked after execution", "evidence": []}
    behavior = {
        "schema": "coding-trial-behavior/v1", "identity": attempt["identity"], "attempt_basis": basis,
        "descriptor": descriptor_ref,
        "inputs": {"before_sha256": study["input_inventory_sha256"], "after_sha256": inputs_after},
        "candidate": {"before_sha256": attempt["candidate"]["full_inventory_sha256"], "after_sha256": candidate_after},
        "git": {"snapshot_inventory_sha256": (attempt["candidate"]["git"]["snapshot"] or {}).get("inventory_sha256"),
                "audit_sha256": attempt["candidate"]["git"]["audit"]["sha256"]},
        "native": result["native"], "oracle": result["oracle"], "observations": result["observations"],
        "checks": checks, "workflow": audit["workflow"], "errors": errors,
    }
    return _save_ref(trial, folder / "behavior.json", behavior), behavior


def assess_suite(trial: Path, *, review: Path | None = None) -> dict:
    """Append one assessment; review-only decisions never repeat candidate execution."""
    from coding_trial_delivery import read_delivery

    trial = _absolute(trial, existing=True)
    attempt_raw = read_bytes(trial, "trial.json")
    attempt = parse_json(attempt_raw)
    require(attempt["schema"] == "coding-trial-attempt/v1" and attempt["status"] in {"completed", "failed"},
            "suite-invalid: Assessment requires a terminal attempt")
    number = len(attempt["assessments"]) + 1
    _absolute(trial / "assessor", existing=True)
    parent = _absolute(trial / "assessor/assessments")
    parent.mkdir(exist_ok=True)
    folder = parent / f"{number:06d}"
    try:
        folder.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise ValueError(f"input-exists: {folder}") from exc
    basis_path = folder / "attempt-basis.json"
    with basis_path.open("xb") as stream:
        stream.write(attempt_raw)
    basis = {"path": basis_path.relative_to(trial).as_posix(), "sha256": hashlib.sha256(attempt_raw).hexdigest()}
    verdict = {"schema": "coding-trial-assessment/v1", "identity": attempt["identity"], "number": number,
               "kind": "check" if review is None else "review", "attempt_basis": basis,
               "behavior": None, "review": None, "cleanup": None, "common_quality": "failed",
               "acceptance": "failed", "synthetic_validation": "failed", "study_eligible": False, "errors": []}
    unsupported = None
    try:
        if review is not None:
            require(attempt["assessments"], "review-stale: No behavior receipt to review")
            previous = _read_ref(trial, attempt["assessments"][-1])
            verdict["behavior"] = previous["behavior"]
            require(verdict["behavior"] is not None, "review-stale: No completed behavior receipt")
            behavior = _verified_behavior(trial, verdict["behavior"], attempt["identity"])
            original = _read_ref(trial, behavior["attempt_basis"])
            require({key: value for key, value in attempt.items() if key != "assessments"}
                    == {key: value for key, value in original.items() if key != "assessments"},
                    "review-stale: Attempt changed since the behavior assessment")
        study, suite, suite_root = _read_study(attempt["study"])
        task = suite["tasks"][attempt["identity"]["task"]]
        require(attempt["candidate"] is not None, "runner-failed: No candidate was produced")
        candidate, baseline, audit = read_delivery(trial, attempt, suite, suite_root)
        def verify_inputs():
            _read_study(attempt["study"])
            read_delivery(trial, attempt, suite, suite_root)
        if review is None:
            require(suite["host"]["isolation"] is not None,
                    "unsupported-profile: Candidate assessment requires an explicit denied-private profile")
            verdict["behavior"], behavior = _behavior(trial, attempt, study, suite, suite_root, folder, basis, verify_inputs)
        else:
            review_path = _absolute(review, existing=True)
            raw = read_bytes(review_path.parent, review_path.name)
            with (folder / "review.json").open("xb") as stream:
                stream.write(raw)
            verdict["review"] = {"path": (folder / "review.json").relative_to(trial).as_posix(),
                                 "sha256": hashlib.sha256(raw).hexdigest()}
        verdict["errors"].extend(behavior["errors"])
        runner = _read_ref(trial, attempt["runner"])
        from coding_trial_assessment_execution import process_passed
        if not process_passed(runner["process"]):
            verdict["errors"].append(_error("runner-failed", "Writer did not complete successfully"))
        if runner["isolation"]["lifecycle"]["status"] != "verified":
            verdict["errors"].append(_error("runner-failed", "Writer cleanup was not verified"))
        reviewed = False
        if review is not None:
            reviewed, verdict["cleanup"] = _review(parse_json(raw), attempt, verdict["behavior"], task, baseline, candidate)
            if not reviewed:
                verdict["errors"].append(_error("review-stale", "Material review or required cleanup failed"))
        if not verdict["errors"]:
            verdict["common_quality"] = "passed" if reviewed else "pending"
            if not reviewed:
                verdict["errors"].append(_error("review-pending", "Independent material review is required"))
        verdict["study_eligible"] = (suite["purpose"] == "prospective"
            and all(gate["status"] == "passed" for gate in attempt["admission_gates"].values())
            and runner["isolation"]["status"] == "passed")
        quality = verdict["common_quality"]
        verdict["synthetic_validation"] = quality if suite["purpose"] == "synthetic" else "not_applicable"
        verdict["acceptance"] = quality if verdict["study_eligible"] else "failed"
        verify_inputs()
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        error = _failure(exc)
        verdict["errors"].append(error)
        verdict.update(common_quality="failed", acceptance="failed", synthetic_validation="failed", study_eligible=False)
        if error["code"] == "unsupported-profile":
            unsupported = str(exc)
    reference = _save_ref(trial, folder / "assessment-result.json", verdict)
    require(read_bytes(trial, "trial.json") == attempt_raw, "input-drift: Attempt changed during assessment")
    attempt["assessments"].append(reference)
    (trial / "trial.json").write_text(json.dumps(attempt, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    if unsupported is not None:
        raise ValueError(unsupported)
    return verdict


def review_template(trial: Path) -> dict:
    """Describe the latest verified behavior for an external material review."""
    from coding_trial_delivery import read_delivery

    trial = _absolute(trial, existing=True)
    attempt = parse_json(read_bytes(trial, "trial.json"))
    require(attempt["schema"] == "coding-trial-attempt/v1" and attempt["assessments"],
            "suite-invalid: Review template requires a completed behavior assessment")
    _, suite, suite_root = _read_study(attempt["study"])
    read_delivery(trial, attempt, suite, suite_root)
    latest = _read_ref(trial, attempt["assessments"][-1])
    require(latest["schema"] == "coding-trial-assessment/v1" and latest["identity"] == attempt["identity"]
            and latest["behavior"] is not None, "receipt-invalid: No current behavior receipt")
    behavior = _verified_behavior(trial, latest["behavior"], attempt["identity"])
    original = _read_ref(trial, behavior["attempt_basis"])
    require({key: value for key, value in attempt.items() if key != "assessments"}
            == {key: value for key, value in original.items() if key != "assessments"},
            "review-stale: Attempt changed since the behavior assessment")
    task_id = attempt["identity"]["task"]
    cleanup = ({"meaningful": None, "changed_files": [], "rationale": "", "regression_evidence": ""}
               if suite["tasks"][task_id]["cleanup_required"] else None)
    return {"schema": "coding-trial-review/v1", "block": task_id, "reviewer": "", "independent": False,
            "preferred": [], "rationale": "", "candidates": {"C001": {
                "baseline_sha256": attempt["identity"]["baseline_sha256"],
                "candidate_sha256": attempt["candidate"]["assessed_sha256"],
                "assessment_sha256": latest["behavior"]["sha256"], "verdict": "pending", "findings": [],
                "criteria": {name: "" for name in CRITERIA}, "cleanup": cleanup}}}
