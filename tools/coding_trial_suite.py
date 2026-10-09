"""Fresh studies and attempt preparation over explicit frozen evaluator inputs."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import coding_trial_assessment as assessment
import coding_trial_delivery as delivery
import coding_trial_loading as loading
import coding_trial_preparation as preparation
import coding_trial_runner as runner
from coding_trial_git import _literal_exclusion, initial_git, initialize_repository
from coding_trial_inventory import copy_snapshot, fingerprint, inventory
from coding_trial_loading import _resources
from coding_trial_manifest import validate_suite
from coding_trial_process import execute
from coding_trial_qualification import boolean, integer, parse_json, read_bytes, require, text
from coding_trial_study import (
    BUDGET, EVALUATOR_SOURCES, ROOT,
    _absolute, _auth_guard, _fresh, _raw, _read_study, _reference, _save, _schedule,
)


def _controller(executable):
    executable = Path(executable).resolve(strict=True)
    digest = hashlib.sha256(_raw(executable)).hexdigest()
    result = execute([str(executable), "-I", "-B", "--version"], ROOT, timeout=10, output_limit=4096,
                     env={"PYTHONDONTWRITEBYTECODE": "1", "PYTHONOPTIMIZE": "0"}, inherit_env=False)
    require(result["returncode"] == 0 and not result.get("timed_out")
            and not result.get("stdout_truncated") and not result.get("stderr_truncated")
            and not result.get("termination_error"), "unsupported-profile: Cannot identify assessor interpreter")
    version = (result["stdout"] or result["stderr"]).strip()
    text(version, "Assessor interpreter version")
    require(hashlib.sha256(_raw(executable)).hexdigest() == digest, "input-drift: Assessor interpreter changed")
    return {"executable": str(executable), "executable_sha256": digest, "version": version}


def _freeze_study(destination: Path, suite_path: Path, schedule: list,
                  assessor_python: Path, *, qualify_loading: bool = False, auth_file: Path | None = None) -> dict:
    """Freeze the one declared input closure and ordered schedule before attempts."""
    boolean(qualify_loading, "Loading authorization")
    suite_path = Path(suite_path).absolute()
    source = _absolute(suite_path.parent, existing=True)
    destination = _fresh(destination, outside=(source, *(ROOT / name for name in EVALUATOR_SOURCES)))
    _auth_guard(auth_file, source, destination, assessor_python, *(ROOT / name for name in EVALUATOR_SOURCES))
    raw = read_bytes(source, suite_path.name)
    suite = validate_suite(parse_json(raw), source)
    _schedule(suite, schedule)
    try:
        for row in schedule:
            arm = suite["arms"][row["arm"]]
            if suite["purpose"] == "prospective" and arm["product"] is not None:
                require(arm["loading"].get("selected_entry") is not None, "Native product requires a selected entry")
            for name in suite["tasks"][row["task"]]["scope"]["generated"] + arm["artifacts"]:
                _literal_exclusion(name)
    except ValueError as exc:
        raise ValueError(f"suite-invalid: {exc}") from exc
    if suite["purpose"] == "prospective" and qualify_loading:
        require(not os.path.lexists(source / "_attempts"), "input-exists: Native derived evidence namespace is already present")
    resources = tuple(sorted({suite_path.name, *_resources(suite, source)}))
    inputs = inventory(source, included=resources)
    evaluator = inventory(ROOT, included=EVALUATOR_SOURCES)
    controller = _controller(assessor_python)
    if suite["purpose"] == "prospective":
        from coding_trial_native import _package_identity, native_recipe
        for row in schedule:
            product = suite["arms"][row["arm"]]["product"]
            installed = None if product is None else {"plugin_id": _package_identity(source / product["payload"])[0] + "@forge-evaluator"}
            native_recipe(suite, row["arm"], {"installed": installed})
        require((suite["host"]["credentials"] is None) == (auth_file is None), "loading-unqualified: Native credential pairing differs")
    try:
        destination.mkdir(parents=True, mode=0o700)
    except FileExistsError as exc:
        raise ValueError(f"input-exists: {destination}") from exc
    copy_snapshot(source, destination / "suite", inputs)
    copy_snapshot(ROOT, destination / "evaluator", evaluator)
    require(read_bytes(destination / "suite", suite_path.name) == raw,
            "input-drift: Manifest changed during study freeze")
    record = {
        "schema": "coding-trial-study/v1", "suite_root": str(destination / "suite"),
        "manifest": {"path": suite_path.name, "sha256": hashlib.sha256(raw).hexdigest(),
                     "suite_sha256": fingerprint(suite)},
        "input_resources": list(resources), "input_inventory": _save(destination / "inputs.json", inputs),
        "input_inventory_sha256": fingerprint(inputs),
        "evaluator": {"root": str(destination / "evaluator"), "resources": list(EVALUATOR_SOURCES),
                      "inventory": _save(destination / "evaluator.json", evaluator)},
        "evaluator_sha256": fingerprint(evaluator), "controller": controller,
        "qualify_loading": qualify_loading, "schedule": schedule,
    }
    reference = _save(destination / "study.json", record)
    return {**reference, "path": str(destination / "study.json")}


def _gates(suite, task, arm):
    isolation = suite["host"]["isolation"]
    resources = {"environment": task["dependencies"]["environment"], "task": task["admission"],
                 "isolation": None if isolation is None else isolation["probe_receipt"],
                 "loading": arm["loading"]["receipt"]}
    absent = "unmeasured" if suite["purpose"] == "synthetic" else "missing"
    return {name: {"status": "passed" if resource is not None else absent,
                   "reason": "Static supplied evidence validated; current launch checks remain required"
                   if resource is not None else "No supplied qualification"}
            for name, resource in resources.items()}


def _prepare_attempt(trial: Path, study_ref: dict, position: int, *, status: str,
                     qualify_loading: bool, auth_file: Path | None) -> dict:
    """Prepare one fresh attempt selected from an already frozen shared study."""
    trial = _fresh(trial)
    _auth_guard(auth_file, trial)
    study, suite, root = _read_study(study_ref, auth_file=auth_file)
    study_ref = dict(study_ref)
    study_path = Path(study_ref["path"])
    trial = _fresh(trial, outside=(study_path.parent,))
    integer(position, "Schedule position", minimum=0)
    require(position < len(study["schedule"]), "suite-invalid: Unknown schedule position")
    require(status in {"prepared", "running"}, "suite-invalid: Invalid initial attempt status")
    boolean(qualify_loading, "Loading authorization")
    require(qualify_loading == study["qualify_loading"], "suite-invalid: Loading authorization changed")
    row = study["schedule"][position]
    task, arm = suite["tasks"][row["task"]], suite["arms"][row["arm"]]
    native = suite["purpose"] == "prospective"
    require(native or auth_file is None, "suite-invalid: Fixtures cannot receive native credentials")
    if native:
        require((suite["host"]["credentials"] is None) == (auth_file is None), "loading-unqualified: Native credential pairing differs")
    try:
        trial.mkdir(parents=True, mode=0o700)
    except FileExistsError as exc:
        raise ValueError(f"input-exists: {trial}") from exc
    record = {
        "schema": "coding-trial-attempt/v1",
        "identity": {"suite_sha256": study["manifest"]["suite_sha256"], "task": row["task"], "arm": row["arm"],
                     "baseline_sha256": task["source"]["baseline_sha256"],
                     "configuration_sha256": fingerprint(arm["configuration"])},
        "study": study_ref, "scheduled_position": position, "budget": dict(BUDGET),
        "qualify_loading": qualify_loading, "status": status, "roots": {}, "initial_git": None,
        "admission_gates": _gates(suite, task, arm), "native": None, "runner": None,
        "candidate": None, "assessments": [], "error": None,
    }
    try:
        private, assessor = trial / "private", trial / "assessor"
        private.mkdir(mode=0o700)
        assessor.mkdir(mode=0o700)
        roots = {name: str(trial / name) for name in ("workspace", "product", "public", "generated")}
        roots.update(native_runtime=str(trial / "native-runtime") if native else None,
                     private=[str(private), str(assessor), str(study_path.parent)])
        record["roots"] = roots
        baseline_root = root / task["source"]["export"]
        baseline = inventory(baseline_root)
        copy_snapshot(baseline_root, trial / "workspace", baseline)
        if arm["product"] is None:
            (trial / "product").mkdir(mode=0o700)
        else:
            source = root / arm["product"]["payload"]
            copy_snapshot(source, trial / "product", inventory(source))
        for name in ("public", "generated"):
            (trial / name).mkdir(mode=0o700)
        initialize_repository(trial / "workspace", {name: entry for name, entry in baseline.items()
                              if entry["type"] != "directory"},
                              artifact_exclusions=tuple(task["scope"]["generated"] + arm["artifacts"]))
        record["initial_git"] = initial_git(trial / "workspace", private / "initial-history.txt")
        record["initial_git"]["history"]["path"] = "private/initial-history.txt"
        if native:
            prepared = preparation.prepare_native(suite, root, record, auth_file=auth_file)
            record["native"] = {name: None for name in ("runtime", "preparation", "setup", "reservation",
                                                       "loading_source", "loading_derived", "execution_manifest", "restoration")}
            record["native"].update({name: prepared[name] for name in ("runtime", "preparation", "setup")})
            if prepared["error"] is not None:
                error = prepared["error"]
                raise ValueError(f"{error['code']}: {error['message']}")
        _read_study(study_ref, auth_file=auth_file)
    except (OSError, ValueError) as exc:
        record["status"] = "failed"
        code = str(exc).split(":", 1)[0]
        if code not in {"suite-invalid", "input-exists", "unsupported-profile", "environment-unqualified",
                        "task-unqualified", "isolation-unqualified", "loading-unqualified", "input-drift", "scope-invalid"}:
            code = "suite-invalid"
        record["error"] = {"code": code, "message": str(exc), "path": str(trial)}
        _save(trial / "trial.json", record)
        raise ValueError(f"{code}: Preparation failed: {exc}") from exc
    _save(trial / "trial.json", record)
    _save(trial / "private/initial-attempt.json", record)
    if native:
        try:
            preparation.reserve_native(suite, study, record, auth_file=auth_file)
        except (OSError, ValueError, RuntimeError) as exc:
            record.update(status="failed", error={"code": "loading-unqualified", "message": str(exc), "path": str(trial)})
            (trial / "trial.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
            raise ValueError(f"loading-unqualified: Reservation creation failed: {exc}") from exc
    return record


def _standalone(trial, suite_path, task_id, arm_id, assessor_python, *, qualify_loading, auth_file):
    source = _absolute(Path(suite_path).absolute().parent, existing=True)
    trial = _fresh(trial, outside=(source,))
    study = _fresh(trial.with_name(trial.name + ".study"), outside=(source,))
    _auth_guard(auth_file, source, trial, study, assessor_python, *(ROOT / name for name in EVALUATOR_SOURCES))
    if auth_file is not None or qualify_loading:
        try:
            supplied = parse_json(read_bytes(source, Path(suite_path).name))
            require(supplied["host"]["adapter"] != "fixture", "Fixtures cannot receive native credentials or loading authorization")
        except (OSError, KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"suite-invalid: {exc}") from exc
    row = {"position": 0, "task": task_id, "arm": arm_id, "repeat": 0, "budget": dict(BUDGET)}
    return trial, _freeze_study(study, suite_path, [row], assessor_python,
                                qualify_loading=qualify_loading, auth_file=auth_file)


def prepare_suite(trial: Path, suite_path: Path, task_id: str, arm_id: str, *,
                  assessor_python: Path, auth_file: Path | None = None) -> dict:
    trial, study = _standalone(trial, suite_path, task_id, arm_id, assessor_python,
                               qualify_loading=False, auth_file=auth_file)
    return _prepare_attempt(trial, study, 0, status="prepared", qualify_loading=False, auth_file=auth_file)


def run_suite(trial: Path, suite_path: Path, task_id: str, arm_id: str, *,
              assessor_python: Path, qualify_loading: bool = False, auth_file: Path | None = None) -> dict:
    trial, study = _standalone(trial, suite_path, task_id, arm_id, assessor_python,
                               qualify_loading=qualify_loading, auth_file=auth_file)
    return _run_attempt(trial, study, 0, qualify_loading=qualify_loading, auth_file=auth_file)


def _run_attempt(trial, study_ref, position, *, qualify_loading, auth_file):
    record = _prepare_attempt(trial, study_ref, position, status="running",
                              qualify_loading=qualify_loading, auth_file=auth_file)
    _, suite, root = _read_study(study_ref, auth_file=auth_file)
    try:
        execution = suite
        if record["native"] is not None:
            preparation.verify_setup_evidence(trial, record, suite, root)
            record["native"]["reservation"] = {"path": "private/reservation.json",
                "sha256": hashlib.sha256(read_bytes(trial, "private/reservation.json")).hexdigest()}
            if qualify_loading:
                receipt = loading.qualify_native_loading(suite, root, record["identity"]["task"], record["identity"]["arm"],
                                                 record["roots"], trial / "private/loading", auth_file=auth_file)
                execution, references = preparation.package_loading(suite, root, record, receipt)
                record["native"].update(references)
                preparation.verify_setup_evidence(trial, record, suite, root)
        result = runner.run_suite_writer(execution, root, record["identity"]["task"], record["identity"]["arm"],
                                  record["roots"], trial / "private/writer", auth_file=auth_file)
        writer_raw = read_bytes(trial, "private/writer/writer.json")
        require(parse_json(writer_raw) == result, "input-drift: Writer result differs from its saved receipt")
        record["runner"] = {"path": "private/writer/writer.json", "sha256": hashlib.sha256(writer_raw).hexdigest()}
        _read_study(study_ref, auth_file=auth_file)
        if record["native"] is not None:
            require(result["isolation"]["status"] == "passed" and result["isolation"]["lifecycle"]["status"] == "verified",
                    "isolation-unqualified: Writer cleanup was not verified; output remains diagnostic")
            for name in ("isolation", "loading"):
                record["admission_gates"][name] = {"status": "passed", "reason": "Current native writer admission verified"}
        record["candidate"] = delivery.freeze_delivery(trial, record, suite, root)
        record["status"] = "completed"
    except (OSError, ValueError, RuntimeError) as exc:
        if record["native"] is not None:
            for key, name in (("loading_source", "qualification.json"), ("restoration", "restoration.json")):
                path = trial / "private/loading" / name
                if path.exists():
                    raw = _raw(path)
                    record["native"][key] = {"path": path.relative_to(trial).as_posix(), "sha256": hashlib.sha256(raw).hexdigest()}
        record["status"] = "failed"
        code = str(exc).split(":", 1)[0]
        if code not in {"suite-invalid", "input-exists", "unsupported-profile", "environment-unqualified",
                        "task-unqualified", "isolation-unqualified", "loading-unqualified", "input-drift", "candidate-drift"}:
            code = "suite-invalid"
        record["error"] = {"code": code, "message": str(exc), "path": str(trial)}
        (trial / "trial.json").write_text(json.dumps(record, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        raise ValueError(f"{code}: {exc}") from exc
    (trial / "trial.json").write_text(json.dumps(record, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    assessment.assess_suite(trial)
    return parse_json(read_bytes(trial, "trial.json"))
