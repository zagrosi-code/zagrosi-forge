"""Freeze one delivery and audit its complete scope before any code projection."""
from __future__ import annotations

import hashlib
from pathlib import Path

from coding_trial_inventory import contains_path, copy_snapshot, fingerprint, inventory, project
from coding_trial_qualification import parse_json, read_bytes, require
from coding_trial_study import _reference, _save


def _save_ref(trial, path, value):
    reference = _save(path, value)
    reference["path"] = path.relative_to(trial).as_posix()
    return reference


def _under(name, roots):
    return any(contains_path(root, name) for root in roots)


def _filtered(entries, exclusions):
    return {name: entry for name, entry in entries.items() if not _under(name, exclusions)}


def _writer_evidence(trial, reference):
    """Bind the writer's finite evidence graph without interpreting candidate paths."""
    require(reference["path"] == "private/writer/writer.json", "Unexpected writer receipt location")
    writer = parse_json(_reference(trial, reference))
    parent = trial / "private/writer"
    bound = {reference["path"]: reference}

    def retain(path, expected=None):
        require(path.is_relative_to(parent), "Writer evidence leaves its private directory")
        require(path.resolve(strict=True) == path, "Writer evidence path was redirected")
        name = path.relative_to(trial).as_posix()
        raw = read_bytes(trial, name)
        digest = hashlib.sha256(raw).hexdigest()
        require(expected is None or digest == expected, "Writer evidence bytes changed")
        bound[name] = {"path": name, "sha256": digest}
        return raw

    isolation = writer["isolation"]
    qualification = isolation["qualification"]
    if qualification is not None:
        path = Path(qualification["path"])
        require(path.is_absolute(), "Writer qualification locator must be absolute")
        receipt = parse_json(retain(path, qualification["sha256"]))
        if receipt.get("schema") == "coding-trial-qualification/v1":
            require(receipt["kind"] == "isolation", "Writer qualification kind differs")
            for item in receipt["evidence"]:
                _reference(path.parent, item)
                retain(path.parent / item["path"], item["sha256"])
    for name in isolation["lifecycle"]["evidence"]:
        path = Path(name)
        retain(path if path.is_absolute() else parent / path)
    return list(bound.values())


def _scope_checks(baseline, delivered, task, arm):
    scope = task["scope"]
    changed = sorted(name for name in baseline.keys() | delivered.keys() if baseline.get(name) != delivered.get(name))
    allowed = scope["allowed_changes"] + scope["generated"] + arm["artifacts"]
    outside = []
    for name in changed:
        entry = delivered.get(name, baseline.get(name))
        structural = entry["type"] == "directory" and name not in baseline and any(
            contains_path(name, child) for child in allowed)
        if _under(name, scope["protected"]) or not (_under(name, allowed) or structural):
            outside.append(name)
    dependencies = task["dependencies"]
    locks = [name for name in changed if _under(name, dependencies["locks"])]
    admitted = (dependencies["allow_lock_changes"] and task["admission"] is not None
                and dependencies["environment"] is not None)
    failed_locks = [] if admitted else locks
    checks = {
        "scope": {"status": "failed" if outside else "passed", "detail": "Complete delivery compared with allowed/protected paths",
                  "evidence": outside},
        "dependencies": {"status": "failed" if failed_locks else "passed", "detail": "Declared dependency lock policy",
                         "evidence": failed_locks},
    }
    errors = []
    if outside:
        errors.append({"code": "scope-invalid", "message": "Changes outside allowed scope: " + ", ".join(outside), "path": None})
    if failed_locks:
        errors.append({"code": "dependency-invalid", "message": "Unadmitted lock changes: " + ", ".join(failed_locks), "path": None})
    return checks, errors


def freeze_delivery(trial, attempt, suite, suite_root):
    """Capture terminal bytes once; later checks never consult the writer workspace."""
    from coding_trial_git import _capture_git
    from coding_trial_preparation import verify_native_evidence
    from coding_trial_workflow import _validate_workflow

    trial, suite_root = Path(trial), Path(suite_root)
    verify_native_evidence(trial, attempt, suite, suite_root)
    task = suite["tasks"][attempt["identity"]["task"]]
    arm = suite["arms"][attempt["identity"]["arm"]]
    workspace = Path(attempt["roots"]["workspace"])
    baseline = inventory(suite_root / task["source"]["export"])
    delivered = inventory(workspace, excluded=(".git",))
    checks, errors = _scope_checks(baseline, delivered, task, arm)
    assessor = trial / "assessor"
    workflow = project(delivered, tuple(arm["artifacts"]))
    initial_history = _reference(trial, attempt["initial_git"]["history"])
    git = _capture_git(workspace, attempt["initial_git"], initial_history, assessor / "git",
                       local_commits=task["local_commits"])
    for key in ("snapshot", "audit"):
        if git[key] is not None:
            git[key]["path"] = (assessor / "git" / git[key]["path"]).relative_to(trial).as_posix()
    git_audit = parse_json(_reference(trial, git["audit"]))
    checks["git"] = {"status": git_audit["status"], "detail": git_audit["detail"], "evidence": [git["audit"]]}
    if git_audit["status"] != "passed":
        errors.append({"code": "scope-invalid", "message": git_audit["detail"], "path": git["audit"]["path"]})
    workflow_result = _validate_workflow(trial, attempt, suite, suite_root, delivered, workflow, git)
    copy_snapshot(workspace, assessor / "workflow", workflow)
    # Validate the complete original tree before creating assessment-only projections.
    code = _filtered(delivered, tuple(task["scope"]["generated"] + arm["artifacts"]))
    copy_snapshot(workspace, assessor / "candidate", code)
    for name in task["scope"]["generated"]:
        (assessor / "candidate" / name).mkdir(parents=True, exist_ok=True)
    candidate = inventory(assessor / "candidate")
    assessed = project(_filtered(candidate, tuple(task["scope"]["generated"])),
                       tuple(task["scope"]["implementation"] + task["scope"]["tests"] + task["scope"]["config"]))
    audit = {
        "schema": "coding-trial-delivery/v1", "identity": attempt["identity"],
        "delivered": delivered, "candidate": candidate, "workflow_inventory": workflow,
        "git": git, "checks": checks, "errors": errors, "writer_evidence": _writer_evidence(trial, attempt["runner"]),
        "workflow": workflow_result, "native": attempt["native"],
    }
    require(inventory(workspace, excluded=(".git",)) == delivered, "candidate-drift: Delivery changed during freeze")
    audit_ref = _save_ref(trial, assessor / "delivery.json", audit)
    return {"path": "assessor/candidate", "full_inventory_sha256": fingerprint(candidate),
            "assessed_sha256": fingerprint(assessed), "workflow_sha256": fingerprint(workflow),
            "git": {"snapshot": git["snapshot"], "audit": audit_ref}}


def read_delivery(trial, attempt, suite, suite_root):
    """Verify frozen delivery and Git evidence without running Git or candidate code."""
    try:
        record = attempt["candidate"]
        require(record["path"] == "assessor/candidate", "Unexpected frozen candidate location")
        candidate = inventory(trial / record["path"])
        require(fingerprint(candidate) == record["full_inventory_sha256"], "Frozen candidate changed")
        audit = parse_json(_reference(trial, record["git"]["audit"]))
        require(audit["schema"] == "coding-trial-delivery/v1" and audit["identity"] == attempt["identity"]
                and audit["candidate"] == candidate, "Delivery audit differs")
        require(audit["native"] == attempt["native"], "Native delivery references changed")
        from coding_trial_preparation import verify_native_evidence
        verify_native_evidence(trial, attempt, suite, suite_root)
        require(audit["writer_evidence"] == _writer_evidence(trial, attempt["runner"]), "Writer evidence changed")
        workflow = inventory(trial / "assessor/workflow")
        require(workflow == audit["workflow_inventory"] and fingerprint(workflow) == record["workflow_sha256"],
                "Frozen workflow changed")
        for reference in audit["workflow"]["evidence"]:
            _reference(trial, reference)
        require(record["git"]["snapshot"] == audit["git"]["snapshot"], "Frozen Git reference differs")
        snapshot = record["git"]["snapshot"]
        if snapshot is not None:
            require(fingerprint(inventory(trial / snapshot["path"])) == snapshot["inventory_sha256"], "Frozen Git changed")
        git_audit = parse_json(_reference(trial, audit["git"]["audit"]))
        _reference(trial, attempt["initial_git"]["history"])
        history = git_audit["terminal"]["history"]
        if history is not None:
            # The low-level Git record explicitly uses its fixed private root.
            _reference(trial / "assessor/git", history)
        task = suite["tasks"][attempt["identity"]["task"]]
        baseline = inventory(suite_root / task["source"]["export"])
        assessed = project(_filtered(candidate, tuple(task["scope"]["generated"])),
                           tuple(task["scope"]["implementation"] + task["scope"]["tests"] + task["scope"]["config"]))
        require(fingerprint(assessed) == record["assessed_sha256"], "Assessed code identity changed")
        return candidate, baseline, audit
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        raise ValueError(f"candidate-drift: {exc}") from exc
