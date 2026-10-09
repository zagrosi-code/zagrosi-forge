"""Suite command adaptation; legacy trials retain their original dispatch."""
from __future__ import annotations

import json
from pathlib import Path
import sys

import coding_trial_assessment as assessment
import coding_trial_suite as suite
from coding_trial_qualification import require

LEGACY_OPTIONS = {"case", "depth", "plugin_root", "plain_agent", "telemetry", "timeout",
                  "interrupt_after", "resume_runner", "runner"}
PREFLIGHT_ERRORS = {"suite-invalid", "input-exists", "unsupported-profile", "environment-unqualified",
                    "task-unqualified", "isolation-unqualified", "loading-unqualified"}
OUTCOME_ERRORS = {"input-drift", "candidate-drift", "receipt-invalid", "scope-invalid",
                  "dependency-invalid", "runner-failed", "review-pending", "review-stale"}


def _error(exc):
    code = str(exc).split(":", 1)[0]
    if code not in PREFLIGHT_ERRORS | OUTCOME_ERRORS:
        code = "suite-invalid"
    return {"success": False, "error": {"code": code, "message": str(exc), "path": None}}, (
        1 if code in OUTCOME_ERRORS else 2)


def _passed(verdict):
    return verdict["acceptance"] == "passed" or verdict["synthetic_validation"] == "passed"


def dispatch(args, parser):
    """Return a suite result/status, or None for the unchanged legacy branch."""
    supplied = set(vars(args))
    selected = "suite" in supplied
    if selected:
        if args.operation not in {"prepare", "run"}:
            parser.error("--suite selects only prepare or run; check/template use the saved attempt")
        if not {"task", "arm"} <= supplied:
            parser.error("--suite requires --task and --arm")
    elif supplied & {"task", "arm", "auth_file", "qualify_loading"}:
        parser.error("--task, --arm, --auth-file and --qualify-loading require --suite")
    elif args.operation in {"check", "review-template"}:
        try:
            record = json.loads((args.trial / "trial.json").read_text())
        except (OSError, ValueError):
            return None
        if not isinstance(record, dict) or "schema" not in record:
            return None
        if record["schema"] != "coding-trial-attempt/v1":
            return _error(ValueError("suite-invalid: Unsupported saved attempt schema"))
        selected = True
    if not selected:
        return None
    if supplied & LEGACY_OPTIONS:
        parser.error("Suite commands reject explicit legacy options: " + ", ".join(
            "--" + name.replace("_", "-") for name in sorted(supplied & LEGACY_OPTIONS)))
    if "qualify_loading" in supplied and args.operation != "run":
        parser.error("--qualify-loading is available only for suite run")
    if "review" in supplied and args.operation != "check":
        parser.error("Suite reviews apply through check --review after a behavior assessment")
    trial = args.trial.absolute()
    try:
        if args.operation == "review-template":
            return assessment.review_template(trial), 0
        if args.operation == "check":
            result = assessment.assess_suite(trial, review=getattr(args, "review", None))
            return result, 0 if _passed(result) else 1
        options = {"assessor_python": Path(sys.executable).resolve(),
                   "auth_file": getattr(args, "auth_file", None)}
        if args.operation == "prepare":
            return suite.prepare_suite(trial, args.suite, args.task, args.arm, **options), 0
        result = suite.run_suite(trial, args.suite, args.task, args.arm,
                                qualify_loading=getattr(args, "qualify_loading", False), **options)
        require(result["assessments"], "receipt-invalid: Run produced no assessment")
        verdict = assessment._read_ref(trial, result["assessments"][-1])
        require(verdict["schema"] == "coding-trial-assessment/v1"
                and verdict["identity"] == result["identity"], "receipt-invalid: Assessment identity differs")
        return result, 0 if _passed(verdict) else 1
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        return _error(exc)
