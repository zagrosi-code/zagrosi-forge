"""Frozen serial comparisons over the ordinary suite attempt lifecycle."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import math
import os
from pathlib import Path
import random
import time

from coding_trial_assessment import _read_ref, _review_context, _save_ref
from coding_trial_assessment_execution import process_passed
from coding_trial_loading_evidence import native_usage
from coding_trial_inventory import fingerprint
from coding_trial_manifest import validate_suite
from coding_trial_outcomes import accepted_outcomes
from coding_trial_qualification import fields, parse_json, read_bytes, require
from coding_trial_study import BUDGET, EVALUATOR_SOURCES, ROOT, _absolute, _auth_guard, _fresh, _read_study, _reference, _save
from coding_trial_suite import _freeze_study, _run_attempt
import coding_trial_suite_review as reviews


def suite_schedule(suite):
    arms = sorted(suite["arms"])
    random.Random(suite["execution_seed"]).shuffle(arms)
    rows = []
    for repeat in range(suite["repeats"]):
        for task in sorted(suite["tasks"]):
            shift = (len(rows) // len(arms)) % len(arms)
            for arm in arms[shift:] + arms[:shift]:
                rows.append({"position": len(rows), "task": task, "arm": arm,
                             "repeat": repeat, "budget": dict(BUDGET)})
    return rows


def _error(exc, default="receipt-invalid"):
    code = str(exc).split(":", 1)[0]
    allowed = {"suite-invalid", "input-exists", "unsupported-profile", "environment-unqualified",
               "task-unqualified", "isolation-unqualified", "loading-unqualified", "input-drift",
               "candidate-drift", "receipt-invalid", "scope-invalid", "dependency-invalid",
               "runner-failed", "review-pending", "review-stale", "execution-cancelled"}
    return {"code": code if code in allowed else default, "message": str(exc) or default, "path": None}


def _bytes_ref(root, path, raw):
    _absolute(path.parent, existing=True)
    with path.open("xb") as stream:
        stream.write(raw)
    return {"path": path.relative_to(root).as_posix(), "sha256": hashlib.sha256(raw).hexdigest()}


def _utc():
    return datetime.now(timezone.utc).isoformat()


def compare_suite(directory, suite_path, *, assessor_python, qualify_loading=False, auth_file=None):
    source = _absolute(Path(suite_path).absolute().parent, existing=True)
    directory = _fresh(directory, outside=(source, *(ROOT / name for name in EVALUATOR_SOURCES)))
    _auth_guard(auth_file, source, directory, assessor_python, *(ROOT / name for name in EVALUATOR_SOURCES))
    suite = validate_suite(parse_json(read_bytes(source, Path(suite_path).name)), source)
    require(suite["host"]["adapter"] != "fixture" or (auth_file is None and not qualify_loading),
            "suite-invalid: Fixtures cannot receive native credentials or loading authorization")
    schedule = suite_schedule(suite)
    study = _freeze_study(directory / "study", suite_path, schedule, assessor_python,
                          qualify_loading=qualify_loading, auth_file=auth_file)
    suite_sha256 = _read_study(study, auth_file=auth_file)[0]["manifest"]["suite_sha256"]
    _save(directory / "matrix.json", {"schema": "coding-trial-matrix/v1", "study": study,
                                       "suite_sha256": suite_sha256})
    for row in schedule:
        position = row["position"]
        trial = directory / "attempts" / f"{position:06d}"
        folder = directory / "outcomes" / f"{position:06d}"
        folder.mkdir(parents=True, mode=0o700)
        started_at, started = _utc(), time.monotonic()
        status, error = "completed", None
        try:
            _run_attempt(trial, study, position, qualify_loading=qualify_loading, auth_file=auth_file)
        except KeyboardInterrupt:
            status, error = "interrupted", _error("execution-cancelled: Comparison interrupted")
        except (OSError, ValueError, RuntimeError) as exc:
            status, error = "failed", _error(exc, "runner-failed")
        ended_at, seconds = _utc(), time.monotonic() - started
        attempt, basis = None, None
        if (trial / "trial.json").exists():
            raw = read_bytes(trial, "trial.json")
            attempt = parse_json(raw)
            basis = _bytes_ref(directory, folder / "attempt-basis.json", raw)
        identity = {"suite_sha256": suite_sha256, **{key: row[key] for key in ("position", "task", "arm", "repeat")}}
        absent = None
        if status == "failed" and (attempt is None or attempt["candidate"] is None):
            absent = _save_ref(directory, folder / "not-produced.json", {
                "schema": "coding-trial-not-produced/v1", **identity,
                "reason": "admission-failed" if error["code"].endswith("unqualified")
                          or error["code"] == "unsupported-profile" else "attempt-failed",
                "error": error, "attempt_basis": basis,
                "runner": None if attempt is None or attempt["runner"] is None else {
                    "trial_path": trial.relative_to(directory).as_posix(), "reference": attempt["runner"]}})
        _save(folder / "result.json", {"schema": "coding-trial-matrix-outcome/v1", **identity,
            "status": status, "started_at": started_at, "ended_at": ended_at, "seconds": seconds,
            "attempt_basis": basis, "not_produced": absent, "error": error})
        if status == "interrupted" or (error is not None and error["code"] == "input-drift"):
            break
    return report_suite(directory)


def _file_ref(directory, path):
    raw = read_bytes(directory, path)
    return {"path": path, "sha256": hashlib.sha256(raw).hexdigest()}, parse_json(raw)


def _outcome(directory, study, row):
    path = f"outcomes/{row['position']:06d}/result.json"
    if not os.path.lexists(directory / path):
        return None, None, None
    reference, value = _file_ref(directory, path)
    fields(value, "schema suite_sha256 position task arm repeat status started_at ended_at seconds "
                  "attempt_basis not_produced error", "Comparison outcome")
    require(value["schema"] == "coding-trial-matrix-outcome/v1"
            and value["suite_sha256"] == study["manifest"]["suite_sha256"]
            and all(value[key] == row[key] for key in ("position", "task", "arm", "repeat")),
            "receipt-invalid: Outcome schedule identity differs")
    require(value["status"] in {"completed", "failed", "interrupted"}
            and type(value["seconds"]) in (int, float) and math.isfinite(value["seconds"])
            and value["seconds"] >= 0, "receipt-invalid: Invalid outcome status or duration")
    for key in ("started_at", "ended_at"):
        require(isinstance(value[key], str) and datetime.fromisoformat(value[key]).utcoffset() is not None,
                "receipt-invalid: Invalid observation timestamp")
    require((value["error"] is None) == (value["status"] == "completed"),
            "receipt-invalid: Outcome error differs from status")
    if value["error"] is not None:
        fields(value["error"], "code message path", "Outcome error")
        require(all(isinstance(value["error"][key], str) and value["error"][key] for key in ("code", "message")),
                "receipt-invalid: Invalid outcome error")
    basis = None if value["attempt_basis"] is None else _read_ref(directory, value["attempt_basis"])
    return reference, value, basis


def _attempt_identity(attempt, matrix, row, suite):
    require(attempt["schema"] == "coding-trial-attempt/v1" and attempt["study"] == matrix["study"]
            and type(attempt["scheduled_position"]) is int and attempt["scheduled_position"] == row["position"]
            and attempt["budget"] == row["budget"], "receipt-invalid: Attempt differs from scheduled position")
    require(attempt["identity"] == {"suite_sha256": matrix["suite_sha256"], "task": row["task"], "arm": row["arm"],
            "baseline_sha256": suite["tasks"][row["task"]]["source"]["baseline_sha256"],
            "configuration_sha256": fingerprint(suite["arms"][row["arm"]]["configuration"])},
            "receipt-invalid: Attempt input identity differs")


def _not_produced(directory, item):
    outcome, attempt = item["outcome"], item["attempt"]
    reference = outcome["not_produced"]
    require(reference is not None and outcome["status"] == "failed"
            and (attempt is None or attempt["candidate"] is None), "receipt-invalid: Missing terminal no-output evidence")
    value = _read_ref(directory, reference)
    fields(value, "schema suite_sha256 position task arm repeat reason error attempt_basis runner", "No-output receipt")
    require(value["schema"] == "coding-trial-not-produced/v1"
            and all(value[key] == outcome[key] for key in ("suite_sha256", "position", "task", "arm", "repeat", "error", "attempt_basis"))
            and value["reason"] in {"attempt-failed", "admission-failed"}, "receipt-invalid: No-output identity differs")
    runner = None if attempt is None or attempt["runner"] is None else {
        "trial_path": item["trial"].relative_to(directory).as_posix(), "reference": attempt["runner"]}
    require(value["runner"] == runner, "receipt-invalid: No-output writer changed")
    if runner is not None:
        _reference(item["trial"], runner["reference"])


def _telemetry(trial, attempt, writer):
    stages = []
    native = attempt["native"]
    if native is not None and attempt["qualify_loading"]:
        usage = None
        if native["loading_source"] is not None:
            receipt = _read_ref(trial, native["loading_source"])
            evidence = [reference for reference in receipt["evidence"] if reference["path"] == "loading-execution.json"]
            if len(evidence) == 1:
                value = parse_json(_reference(trial / "private/loading", evidence[0]))
                require(value["schema"] == "coding-trial-native-loading-execution/v1",
                        "receipt-invalid: Native loading usage source differs")
                smoke = [call for call in value["invocations"] if call["id"] == "smoke"]
                if len(smoke) == 1:
                    usage = native_usage(smoke[0]["process"])
        stages.append({"stage": "loading", "telemetry": usage})
    stages.append({"stage": "writer", "telemetry": None if writer is None else writer["telemetry"]})
    values = [row["telemetry"] for row in stages]
    if not any(value is not None for value in values):
        return stages, None
    def total(key):
        observations = [((value or {}).get("totals") or {}).get(key) for value in values]
        return sum(observations) if all(type(value) is int and value >= 0 for value in observations) else None
    totals = {key: total(key) for key in ("input_tokens", "cached_input_tokens", "output_tokens")}
    uncached = None
    if totals["input_tokens"] is not None and totals["cached_input_tokens"] is not None:
        difference = totals["input_tokens"] - totals["cached_input_tokens"]
        uncached = difference if difference >= 0 else None
    return stages, {"totals": {**totals, "uncached_input_tokens": uncached},
                    "reported_cost_usd": None, "interventions": None}


def _context(directory):
    directory = _absolute(directory, existing=True)
    matrix_ref, matrix = _file_ref(directory, "matrix.json")
    fields(matrix, "schema study suite_sha256", "Suite matrix")
    require(matrix["schema"] == "coding-trial-matrix/v1", "suite-invalid: Unsupported matrix schema")
    study, suite, suite_root = _read_study(matrix["study"])
    require(matrix["suite_sha256"] == study["manifest"]["suite_sha256"]
            and study["schedule"] == suite_schedule(suite), "input-drift: Matrix schedule changed")
    context = {"directory": directory, "matrix": matrix, "matrix_ref": matrix_ref, "study": study,
               "suite": suite, "suite_root": suite_root, "items": [], "errors": []}
    for scheduled in study["schedule"]:
        trial = directory / "attempts" / f"{scheduled['position']:06d}"
        report = {**{key: scheduled[key] for key in ("position", "task", "arm", "repeat")},
            "terminal": False, "produced": None, "status": "pending", "common_quality": "pending",
            "workflow": "unmeasured", "acceptance": "pending", "synthetic_validation": "pending"
            if suite["purpose"] == "synthetic" else "not_applicable", "material_review": "pending",
            "errors": [], "attempt_seconds": None, "reported_telemetry": None, "stage_telemetry": []}
        item = {"schedule": scheduled, "trial": trial, "outcome_ref": None, "outcome": None,
                "attempt": None, "review": None, "runner": None, "eligible": False, "behavior_ok": False, "report": report}
        context["items"].append(item)
        try:
            reference, outcome, basis = _outcome(directory, study, scheduled)
            item.update(outcome_ref=reference, outcome=outcome)
            if outcome is None:
                continue
            report.update(status=outcome["status"], attempt_seconds=outcome["seconds"],
                          terminal=outcome["status"] != "interrupted")
            if outcome["error"] is not None:
                report["errors"].append(outcome["error"]["code"])
            if basis is not None:
                attempt = parse_json(read_bytes(trial, "trial.json"))
                _attempt_identity(attempt, matrix, scheduled, suite)
                require({key: value for key, value in attempt.items() if key != "assessments"}
                        == {key: value for key, value in basis.items() if key != "assessments"}
                        and attempt["assessments"][:len(basis["assessments"])] == basis["assessments"],
                        "receipt-invalid: Terminal attempt or assessment history changed")
                item["attempt"] = attempt
                if attempt["runner"] is not None:
                    item["runner"] = _read_ref(trial, attempt["runner"])
                stages, telemetry = _telemetry(trial, attempt, item["runner"])
                report.update(stage_telemetry=stages, reported_telemetry=telemetry)
            else:
                require(not (trial / "trial.json").exists(), "receipt-invalid: An unbound attempt appeared")
            if outcome["status"] == "interrupted":
                require(outcome["not_produced"] is None, "receipt-invalid: Interrupted work is not terminal no-output")
                continue
            require(basis is None or basis["status"] in {"completed", "failed"},
                    "receipt-invalid: Outcome refers to a nonterminal attempt")
            if basis is None or basis["candidate"] is None:
                _not_produced(directory, item)
                report.update(produced=False, common_quality="failed", acceptance="failed", material_review="not_applicable",
                              synthetic_validation="failed" if suite["purpose"] == "synthetic" else "not_applicable")
                continue
            require(outcome["not_produced"] is None, "receipt-invalid: Produced candidate relabeled absent")
            report["produced"] = True
            review = _review_context(trial)
            item["review"] = review
            writer = item["runner"]
            passed = not review["behavior"]["errors"] and process_passed(writer["process"]) \
                and writer["isolation"]["lifecycle"]["status"] == "verified"
            item.update(behavior_ok=passed, eligible=review["latest"]["study_eligible"] is True)
            report.update(common_quality="pending" if passed else "failed", workflow=review["behavior"]["workflow"]["status"])
            report["errors"].extend(error["code"] for error in review["behavior"]["errors"])
            if not process_passed(writer["process"]) or writer["isolation"]["lifecycle"]["status"] != "verified":
                report["errors"].append("runner-failed")
        except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
            error = _error(exc)
            context["errors"].append(error)
            report.update(status="invalid", common_quality="failed", acceptance="failed", material_review="stale")
            report["errors"].append(error["code"])
    return context


def report_suite(directory):
    context = _context(directory)
    states, review_errors = reviews.review_status(context)
    rows = []
    for item in context["items"]:
        row = deepcopy(item["report"])
        if item["review"] is not None and row["status"] != "invalid":
            material = states.get(row["position"], "pending")
            row["material_review"] = material
            row["common_quality"] = ("failed" if not item["behavior_ok"] or material == "failed" else
                                     "passed" if material == "passed" else "pending")
            row["acceptance"] = row["common_quality"] if item["eligible"] else "failed"
            if material in {"pending", "stale"}:
                row["errors"].append("review-pending" if material == "pending" else "review-stale")
        row["synthetic_validation"] = row["common_quality"] if context["suite"]["purpose"] == "synthetic" else "not_applicable"
        row["errors"] = sorted(set(row["errors"]))
        rows.append(row)
    complete = all(row["terminal"] and row["status"] != "invalid" and (
        row["produced"] is False or row["produced"] is True and row["material_review"] in {"passed", "failed"}) for row in rows)
    prospective = context["suite"]["purpose"] == "prospective"
    synthetic = "not_applicable" if prospective else (
        "passed" if complete and all(row["synthetic_validation"] == "passed" for row in rows) else
        "failed" if any(row["synthetic_validation"] == "failed" for row in rows) else "pending")
    return {"schema": "coding-trial-suite-report/v1", "success": complete and (
        all(row["acceptance"] == "passed" for row in rows) if prospective else synthetic == "passed"),
        "suite_sha256": context["matrix"]["suite_sha256"], "purpose": context["suite"]["purpose"],
        "study_complete": complete, "study_eligible": prospective and all(item["eligible"] for item in context["items"]),
        "attempts": rows, "accepted_outcomes": accepted_outcomes(rows, status_key="acceptance"),
        "synthetic_validation": synthetic, "errors": [*context["errors"], *review_errors]}


def blind_suite(directory):
    return reviews.make_packets(_context(directory))


def apply_suite_reviews(directory):
    context = _context(directory)
    reviews.apply_reviews(context)
    return report_suite(directory)
