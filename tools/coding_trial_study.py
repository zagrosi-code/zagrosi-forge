"""Shared frozen-study identities and resource checks, without attempt orchestration."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from coding_trial_inventory import fingerprint, inventory, relative_path
from coding_trial_isolation import ADAPTER_SOURCES
from coding_trial_loading import _resources
from coding_trial_manifest import validate_suite
from coding_trial_qualification import boolean, fields, integer, parse_json, read_bytes, require, sha, text
from coding_trial_reservation import _auth_identity

ROOT = Path(__file__).resolve().parents[1]
BUDGET = {"timeout_seconds": 900, "output_bytes": 8388608}
EVALUATOR_SOURCES = tuple(sorted(set(ADAPTER_SOURCES) | {
    "tools/coding_trial_study.py", "tools/coding_trial_suite.py", "tools/coding_trial_git.py", "tools/coding_trial_runner.py",
    "tools/coding_trial_assessment.py", "tools/coding_trial_assessment_execution.py",
    "tools/coding_trial_delivery.py", "tools/coding_trial_observation_client.py",
    "tools/coding_trial_workflow.py",
    "tools/coding_trial_preparation.py",
    "tools/coding_trials.py", "tools/coding_trial_suite_cli.py",
    "tools/coding_trial_comparison.py", "tools/coding_trial_evidence.py",
}))


def _save(path, value):
    raw = (json.dumps(value, indent=2, allow_nan=False) + "\n").encode("utf-8")
    with path.open("xb") as stream:
        stream.write(raw)
    return {"path": path.name, "sha256": hashlib.sha256(raw).hexdigest()}


def _absolute(path, *, existing=False):
    path = Path(path).absolute()
    require(path.resolve(strict=existing) == path, f"suite-invalid: Redirected path: {path}")
    return path


def _fresh(path, *, outside=()):
    path = Path(path).absolute()
    if os.path.lexists(path):
        raise ValueError(f"input-exists: {path}")
    path = _absolute(path)
    require(not any(path.is_relative_to(root) or root.is_relative_to(path) for root in outside),
            "suite-invalid: Destination overlaps frozen inputs")
    return path


def _auth_guard(auth_file, *blocked):
    """Reject metadata aliases before any protected resource can be read."""
    if auth_file is None:
        return
    try:
        identity = _auth_identity(auth_file)
        path = Path(identity["path"])
        require(not any(path.is_relative_to(Path(root).absolute().resolve()) for root in blocked),
                "Credential source overlaps evaluator or candidate material")
    except (OSError, ValueError, RuntimeError) as exc:
        raise ValueError(f"loading-unqualified: {exc}") from exc


def _raw(path):
    path = _absolute(path, existing=True)
    return read_bytes(path.parent, path.name)


def _reference(root, reference):
    fields(reference, "path sha256", "file reference")
    relative_path(reference["path"])
    sha(reference["sha256"])
    raw = _raw(root / reference["path"])
    require(hashlib.sha256(raw).hexdigest() == reference["sha256"], "Saved file bytes changed")
    return raw


def _schedule(suite, schedule):
    require(type(schedule) is list and schedule, "suite-invalid: Schedule must be nonempty")
    for position, row in enumerate(schedule):
        fields(row, "position task arm repeat budget", "Schedule row")
        integer(row["position"], "Schedule position", minimum=0)
        integer(row["repeat"], "Schedule repeat", minimum=0)
        require(row["position"] == position, "suite-invalid: Schedule positions must match their list indexes")
        require(isinstance(row["task"], str) and row["task"] in suite["tasks"]
                and isinstance(row["arm"], str) and row["arm"] in suite["arms"],
                "suite-invalid: Unknown task or arm")
        fields(row["budget"], "timeout_seconds output_bytes", "Writer budget")
        require(all(type(row["budget"][key]) is int and row["budget"][key] == value
                    for key, value in BUDGET.items()), "suite-invalid: Writer budget changed")


def _read_study(study_ref: dict, *, auth_file=None) -> tuple[dict, dict, Path]:
    """Verify saved/current identities without consulting mutable source inputs."""
    try:
        fields(study_ref, "path sha256", "Study reference")
        require(Path(study_ref["path"]).is_absolute(), "Study reference must be absolute")
        path = _absolute(study_ref["path"], existing=True)
        _auth_guard(auth_file, path.parent, *(ROOT / name for name in EVALUATOR_SOURCES))
        raw = _raw(path)
        sha(study_ref["sha256"])
        require(hashlib.sha256(raw).hexdigest() == study_ref["sha256"], "Study bytes changed")
        study = parse_json(raw)
        fields(study, "schema suite_root manifest input_resources input_inventory input_inventory_sha256 "
               "evaluator evaluator_sha256 controller qualify_loading schedule", "Study")
        require(study["schema"] == "coding-trial-study/v1", "Unsupported study schema")
        boolean(study["qualify_loading"], "Loading authorization")
        root = _absolute(study["suite_root"], existing=True)
        require(root == path.parent / "suite", "Frozen suite location changed")
        fields(study["manifest"], "path sha256 suite_sha256", "Frozen manifest")
        manifest = study["manifest"]
        manifest_raw = _reference(root, {key: manifest[key] for key in ("path", "sha256")})
        suite = validate_suite(parse_json(manifest_raw), root)
        require(fingerprint(suite) == manifest["suite_sha256"], "Canonical suite changed")
        expected_resources = sorted({manifest["path"], *_resources(suite, root)})
        require(study["input_resources"] == expected_resources, "Declared input closure changed")
        inputs = parse_json(_reference(path.parent, study["input_inventory"]))
        require(fingerprint(inputs) == study["input_inventory_sha256"]
                and inventory(root, included=tuple(expected_resources)) == inputs, "Frozen inputs changed")
        evaluator = study["evaluator"]
        fields(evaluator, "root resources inventory", "Frozen evaluator")
        require(evaluator["resources"] == list(EVALUATOR_SOURCES), "Evaluator resource declaration changed")
        evaluator_root = _absolute(evaluator["root"], existing=True)
        require(evaluator_root == path.parent / "evaluator", "Frozen evaluator location changed")
        expected = parse_json(_reference(path.parent, evaluator["inventory"]))
        require(fingerprint(expected) == study["evaluator_sha256"]
                and inventory(evaluator_root, included=EVALUATOR_SOURCES) == expected
                and inventory(ROOT, included=EVALUATOR_SOURCES) == expected, "Evaluator source changed")
        controller = study["controller"]
        fields(controller, "executable executable_sha256 version", "Assessor interpreter")
        _auth_guard(auth_file, controller["executable"])
        text(controller["version"], "Assessor interpreter version")
        require(Path(controller["executable"]).is_absolute()
                and hashlib.sha256(_raw(Path(controller["executable"]))).hexdigest()
                == controller["executable_sha256"], "Assessor interpreter changed")
        _schedule(suite, study["schedule"])
        return study, suite, root
    except (OSError, KeyError, TypeError, ValueError, RuntimeError) as exc:
        raise ValueError(f"input-drift: {exc}") from exc
