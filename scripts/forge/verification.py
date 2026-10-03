"""Explicit verification receipts; plan text is never executed or treated as an outcome."""

from __future__ import annotations

from pathlib import Path
import subprocess

from . import markdown, mutable_inputs, output, storage


def receipt_path(planning_dir: Path, section: str | None = None) -> Path:
    return planning_dir / "implementation" / "verification" / f"{section or 'integration'}.json"


def result_error(result) -> str | None:
    if not isinstance(result, dict) or result.get("version") != 1:
        return "Legacy verification has no explicit outcome; record an attestation, inspection, or captured result."
    if result.get("outcome") != "passed":
        return f"Verification outcome is {result.get('outcome', 'unknown')}, not passed."
    source = result.get("source")
    if source == "captured":
        command = result.get("command")
        if type(result.get("exit_code")) is not int or result["exit_code"] != 0 or not isinstance(command, list) or not command or not all(isinstance(value, str) and value for value in command):
            return "Captured verification requires a command and exit code zero."
    elif source in ("attestation", "inspection"):
        evidence = result.get("evidence")
        if not isinstance(evidence, list) or not any(isinstance(value, str) and value.strip().lower() not in {"", "none", "n/a", "tbd", "todo", "pending"} for value in evidence):
            return "Attestation and inspection require substantive evidence."
    else:
        return "Unknown verification evidence source."
    return None


def receipt_error(receipt, planning_dir: Path, target_dir: Path, section: str | None = None) -> str | None:
    if error := result_error(receipt):
        return error
    try:
        fresh = receipt.get("snapshot") == mutable_inputs.verification_snapshot(planning_dir, target_dir, section)
    except (OSError, ValueError, TypeError, subprocess.SubprocessError) as exc:
        return f"Cannot verify current inputs: {exc}"
    return None if fresh else "Verification inputs changed; rerun verification against the current source and contract."


def section_result(args, planning_dir: Path, target_dir: Path) -> dict:
    if getattr(args, "verification_receipt", None):
        if args.verification or getattr(args, "verification_source", None) or getattr(args, "verification_outcome", None):
            raise ValueError("Use a verification receipt or an explicit manual result, not both.")
        receipt = storage.load_json(storage.resolve_path(args.verification_receipt))
        if error := receipt_error(receipt, planning_dir, target_dir, args.section):
            raise ValueError(error)
        return receipt
    return {"version": 1, "source": getattr(args, "verification_source", None),
            "outcome": getattr(args, "verification_outcome", None),
            "evidence": markdown.normalize_repeated(args.verification)}


def integration_report(planning_dir: Path, target_dir: Path) -> dict:
    path = receipt_path(planning_dir)
    try:
        receipt = storage.load_json(path) if path.is_file() else None
        error = receipt_error(receipt, planning_dir, target_dir)
    except (OSError, ValueError) as exc:
        receipt, error = None, str(exc)
    return {"success": error is None, "receipt_path": str(path), "error": error,
            "source": receipt.get("source") if isinstance(receipt, dict) else None,
            "outcome": receipt.get("outcome") if isinstance(receipt, dict) else "missing"}


def _capture(command: list[str], target: Path, timeout: float) -> dict:
    from .child_process import execute

    result = execute(command, target, timeout=timeout, output_limit=2000)
    return {"source": "captured", "command": command, "exit_code": result["returncode"],
            "outcome": "timed_out" if result["timed_out"] else "passed" if result["returncode"] == 0 else "failed",
            "stdout_tail": result["stdout"], "stderr_tail": result["stderr"],
            **{key: result[key] for key in ("stdout_bytes", "stderr_bytes", "stdout_truncated", "stderr_truncated", "termination_error") if key in result}}


def implement_verify(args) -> int:
    planning = storage.resolve_path(args.planning_dir)
    target = mutable_inputs.target_directory(planning, getattr(args, "target_dir", None))
    command = args.command_argv[1:] if args.command_argv[:1] == ["--"] else args.command_argv
    try:
        if command and args.command_argv[:1] != ["--"]:
            raise ValueError("Separate the explicit verification command with --.")
        if not 0 < args.timeout <= 86400:
            raise ValueError("Verification timeout must be positive and at most 86400 seconds.")
        if bool(command) == bool(args.source):
            raise ValueError("Provide an explicit command after --, or --source attestation|inspection with --outcome and --evidence.")
        if command and (args.outcome or args.evidence):
            raise ValueError("Captured execution determines its own outcome; omit --outcome and --evidence.")
        if getattr(args, "stage", None):
            from .compatibility import capture

            if not args.section or args.integration or not command:
                raise ValueError("Compatibility stages require --section and explicit argv; omit --integration and manual evidence.")
            result = capture(planning, target, args.section, args.stage, command, args.timeout)
            return output.print_json(result, 0 if result["success"] else 1)
        before = mutable_inputs.verification_snapshot(planning, target, args.section)
        path = receipt_path(planning, args.section)
        receipts = {path: before}
        if args.integration and args.section:
            receipts[receipt_path(planning)] = {**before, "section": None}
        for destination, snapshot in receipts.items():
            storage.write_json(destination, {"version": 1, "source": "captured" if command else args.source,
                                             "outcome": "pending", "snapshot": snapshot})
        if command:
            result = _capture(command, target, args.timeout)
        else:
            result = {"source": args.source, "outcome": args.outcome,
                      "evidence": markdown.normalize_repeated(args.evidence)}
        result.update(version=1, completed_at=storage.now_iso(), snapshot=before)
        error = receipt_error(result, planning, target, args.section)
        if error and result["outcome"] == "passed":
            result.update(outcome="failed", error=error)
        for destination, snapshot in receipts.items():
            storage.write_json(destination, {**result, "snapshot": snapshot})
        return output.print_json({"success": error is None, "receipt_path": str(path), "error": error,
                                  "integration_receipt_path": str(receipt_path(planning)) if args.integration or not args.section else None,
                                  "source": result["source"], "outcome": result["outcome"],
                                  **{key: result[key] for key in ("exit_code", "stdout_tail", "stderr_tail") if key in result}},
                                 0 if error is None else 1)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return output.print_json({"success": False, "error": str(exc)}, 1)
