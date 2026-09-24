"""Trial fingerprints and explicitly attested cleanup review evidence."""
from __future__ import annotations

import ast
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import sys


def files(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()
            and not set(p.parts) & {"__pycache__", ".git", ".pytest_cache"}}


def plugin_files(root: Path) -> dict[str, str]:
    paths = [root / "scripts/zagrosi_skills.py", *sorted((root / "scripts/forge").rglob("*.py")),
             *sorted((root / "skills").rglob("*.md"))]
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths if p.is_file()}


def evaluator_files(root: Path, oracle: Path, cases: Path) -> dict[str, str]:
    paths = [oracle, cases, *sorted((cases.parent / "resume-plan").rglob("*.md")),
             *sorted((root / "tools").glob("coding_trial*.py")), root / "tools/trial_matrix.py"]
    return {**plugin_files(root), **{p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in paths if p.is_file()}}


def plugin_provenance(record: dict, root: Path) -> dict:
    before = record.get("plugin_sha256", {})
    current = plugin_files(root)
    changed = sorted(name for name in before.keys() | current.keys() if before.get(name) != current.get(name))
    complete = bool(record.get("provenance_version") == 2
                    and any(name.startswith("scripts/forge/") for name in before))
    status = "incomplete" if not complete else "drift" if changed else "verified"
    return {"success": status == "verified", "status": status, "changed_files": changed,
            "coverage": "launcher, runtime Python modules, and skill Markdown"}


def semantic_files(workspace: Path) -> dict[str, str]:
    result = {}
    for folder in ("src", "tests"):
        for path in sorted((workspace / folder).rglob("*.py")):
            tree = ast.parse(path.read_text())
            # Comments/formatting and docstrings cannot establish a substantive cleanup.
            for node in ast.walk(tree):
                if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    body = node.body
                    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                        if isinstance(body[0].value.value, str):
                            del body[0]
            if tree.body:
                result[path.relative_to(workspace).as_posix()] = hashlib.sha256(
                    ast.dump(tree, include_attributes=False).encode()).hexdigest()
    return result


def code_fingerprint(file_hashes: dict[str, str]) -> str:
    code = {name: digest for name, digest in file_hashes.items() if name.startswith(("src/", "tests/"))}
    return hashlib.sha256(json.dumps(code, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def review_template(trial: Path) -> dict:
    record = json.loads((trial / "trial.json").read_text())
    return {"reviewer": "", "independent": False, "verdict": "pending",
            "baseline_sha256": code_fingerprint(record["baseline_files"]),
            "candidate_sha256": code_fingerprint(files(trial / "workspace")),
            "cleanup": {"meaningful": None, "changed_files": [], "rationale": "",
                        "regression_evidence": ""}}


def cleanup_verdict(record: dict, workspace: Path, required: bool, review: Path | None) -> dict:
    if not required:
        return {"success": None, "status": "not_required", "reasons": []}
    reasons = []
    before = record.get("baseline_semantics")
    try:
        current = semantic_files(workspace)
    except (SyntaxError, UnicodeError) as exc:
        return {"success": False, "status": "fail", "reasons": ["invalid Python source: " + str(exc)]}
    source_changes, test_changes = [], []
    if before is None:
        reasons.append("baseline semantic fingerprints unavailable; prepare a fresh trial")
    else:
        changed = {name for name in before.keys() | current.keys() if before.get(name) != current.get(name)}
        source_changes = sorted(name for name in changed if name.startswith("src/"))
        test_changes = sorted(name for name in changed if name.startswith("tests/") and name in current)
        if not source_changes:
            reasons.append("no semantic implementation changes")
    evidence = None
    if review is None:
        reasons.append("independent cleanup review is required")
    elif review.resolve().is_relative_to(workspace.resolve()):
        reasons.append("independent review must be supplied from outside the candidate workspace")
    else:
        try:
            evidence = json.loads(review.read_text())
            if evidence is None:
                reasons.append("independent review must be a JSON object")
        except (OSError, ValueError) as exc:
            reasons.append("cannot read independent review: " + str(exc))
    if evidence is not None:
        if not isinstance(evidence, dict):
            reasons.append("independent review must be a JSON object")
        else:
            cleanup = evidence.get("cleanup")
            if evidence.get("independent") is not True or not _text(evidence.get("reviewer")):
                reasons.append("reviewer identity and independent attestation are required")
            if evidence.get("verdict") not in ("pass", "fixed"):
                reasons.append("independent review has no passing verdict")
            if (evidence.get("baseline_sha256") != code_fingerprint(record["baseline_files"])
                    or evidence.get("candidate_sha256") != code_fingerprint(files(workspace))):
                reasons.append("independent review fingerprints do not match this baseline and candidate")
            if not isinstance(cleanup, dict) or cleanup.get("meaningful") is not True:
                reasons.append("review must confirm meaningful cleanup")
            else:
                reviewed_paths = cleanup.get("changed_files")
                if (not isinstance(reviewed_paths, list) or not reviewed_paths
                        or any(name not in source_changes for name in reviewed_paths)):
                    reasons.append("review must identify concrete implementation changes")
                if not _text(cleanup.get("rationale")) or not _text(cleanup.get("regression_evidence")):
                    reasons.append("review must explain cleanup usefulness and before/after regression evidence")
    return {"success": not reasons, "status": "fail" if reasons else "pass", "reasons": reasons,
            "source_changes": source_changes, "test_changes": test_changes,
            "review_file": str(review) if review else None,
            "reviewer": evidence.get("reviewer") if isinstance(evidence, dict) else None}


def _text(value) -> bool:
    return isinstance(value, str) and value.strip().lower() not in {"", "none", "pending", "todo", "tbd", "n/a"}


def workflow_summary(plugin_root: Path, workspace: Path, depth: str) -> int:
    """Require admitted planning at the trial depth before completed implementation."""
    spec = importlib.util.spec_from_file_location("_trial_workflow_launcher", plugin_root / "scripts/zagrosi_skills.py")
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)
    argv = ["postflight", "--phase", "implement", "--planning-dir", str(workspace / ".planning"),
            "--sections-dir", str(workspace / ".planning/sections"), "--target-dir", str(workspace),
            "--depth", depth]
    try:
        runtime = launcher.load_runtime()
    except ImportError:
        return launcher.main(argv)  # Preserve the launcher's integrity-failure contract.
    output = importlib.import_module(runtime.MODULE_NAMES["forge/output.py"])
    artifacts = importlib.import_module(runtime.MODULE_NAMES["forge/artifacts.py"])
    original_print = output.print_json
    resolved_depth = artifacts.planning_depth(workspace / ".planning")
    if ("lean" if resolved_depth == "fast" else resolved_depth) != depth:
        return original_print({"success": False, "sections_recorded_complete": False,
                               "admission_success": False, "selected_depth": depth,
                               "planning_depth": resolved_depth, "phase": "plan",
                               "reasons": [f"Plan depth {resolved_depth!r} does not match trial depth {depth!r}."]}, 1)
    report = {}

    def compact_print(payload, exit_code=0):
        if payload.get("stage") == "postflight" and payload.get("phase") in {"plan", "implement"}:
            blocking = payload.get("blocking_gates", [])
            reasons = []
            for gate in payload.get("gates", []):
                if gate.get("name") not in blocking:
                    continue
                details = gate.get("payload", {})
                findings = details.get("findings") or [{"message": details.get("error", "gate failed")}]
                reasons.extend(f"{gate.get('name')}: {item.get('code', '')} {item.get('message', '')}"[:120]
                               for item in findings[:6 - len(reasons)])
                if len(reasons) >= 6:
                    break
            report.clear()
            report.update({
                "success": payload.get("success") is True,
                "phase": payload.get("phase"),
                "sections_recorded_complete": payload.get("sections_recorded_complete") is True,
                "blocking_gates": [str(name)[:80] for name in blocking[:12]],
                "blocking_gate_count": len(blocking),
                "remaining_section_count": len(payload.get("remaining_sections", [])),
                "pending_section_count": len(payload.get("pending_sections", [])),
                "reasons": reasons,
            })
            return exit_code
        return original_print(payload, exit_code)

    output.print_json = compact_print
    try:
        admitted = False
        for phase in ("plan", "implement"):
            report.clear()
            argv[2] = phase
            code = launcher.main(argv + (["--strict"] if phase == "plan" else []))
            if code != 0 or report.get("success") is not True:
                report["success"] = False
                break
            if phase == "plan":
                admitted = True
        report.update(admission_success=admitted, selected_depth=depth, planning_depth=resolved_depth)
        return original_print(report, 0 if report.get("success") is True else 1)
    finally:
        output.print_json = original_print


if __name__ == "__main__":
    raise SystemExit(workflow_summary(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]))
