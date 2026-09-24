#!/usr/bin/env python3
"""Prepare, run, and independently check isolated Forge coding trials."""
from __future__ import annotations

import argparse
import ast
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from coding_trial_evidence import (
    cleanup_verdict, evaluator_files, files, plugin_files, plugin_provenance, review_template, semantic_files,
)
from coding_trial_process import execute
from coding_trial_resume import prepare_resume, resume_verdict

PACK = ROOT / "examples/evals/coding"
CASES = json.loads((PACK / "cases.json").read_text())


def test_command(case: dict) -> list[str]:
    return ["node", "--test", "tests/ledger.test.js"] if case.get("runtime") == "node" else [sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests"]


def code_metrics(workspace: Path, runtime: str = "python") -> dict:
    if runtime == "node":
        paths = sorted(path for path in (workspace / "src").rglob("*") if path.suffix in {".js", ".cjs", ".mjs"})
        return {"source_lines": sum(len(path.read_text().splitlines()) for path in paths), "modules": len(paths),
                "largest_function_lines": None, "branches": None, "repeated_loops": None,
                "external_imports": None, "limits": "JavaScript complexity and dependency metrics are unmeasured."}
    paths = sorted((workspace / "src").rglob("*.py"))
    functions, statements, imports, lines, branches = [], Counter(), set(), 0, 0
    for path in paths:
        text = path.read_text()
        tree = ast.parse(text)
        lines += len(text.splitlines())
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions.append(node.end_lineno - node.lineno + 1)
            if isinstance(node, (ast.If, ast.For, ast.While, ast.ExceptHandler)):
                branches += 1
            if isinstance(node, (ast.For, ast.While)):
                statements[ast.dump(node, include_attributes=False)] += 1
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                imports.add(node.module.split(".")[0])
    local = {p.stem for p in paths} | {p.parent.name for p in paths}
    return {"source_lines": lines, "modules": len(paths), "largest_function_lines": max(functions, default=0),
            "branches": branches, "repeated_loops": sum(n - 1 for n in statements.values()),
            "external_imports": sorted(imports - sys.stdlib_module_names - local)}


def prepare(trial: Path, case: str, depth: str | None = None) -> dict:
    trial.mkdir(parents=True, exist_ok=False)
    workspace = trial / "workspace"
    fixture = PACK / CASES[case].get("fixture", "fixture")
    oracle = ROOT / CASES[case].get("oracle", "tools/coding_trial_checks.py")
    shutil.copytree(fixture, workspace, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    selected = depth or CASES[case]["depth"]
    tests = test_command(CASES[case])
    checkpoint = prepare_resume(ROOT, workspace, selected, tests) if case == "resume" else None
    displayed_command = subprocess.list2cmdline(tests) if os.name == "nt" else shlex.join(tests)
    protected = CASES[case].get("protected_paths", [])
    prompt = (f"Work only in {workspace}. Use Forge at {selected} depth.\n"
              f"Read {ROOT / 'skills/zagrosi-implement/references/engineering.md'} and the applicable Forge skills.\n\n"
              f"{CASES[case]['request']}\n\n"
              "Preserve public APIs. Standard library only. Keep .planning records compact.\n"
              f"Run existing/added tests with `{displayed_command}`. Python trials require PYTHONPATH=src.\n"
              + (f"Leave these unrelated files unchanged: {', '.join(protected)}.\n" if protected else "") +
              "Report tests, cleanup, remaining issues, and observed usage if available.\n")
    (trial / "prompt.md").write_text(prompt)
    record = {"case": case, "depth": selected, "baseline_files": files(workspace),
              "baseline_semantics": semantic_files(workspace), "provenance_version": 2,
              "baseline_metrics": code_metrics(workspace, CASES[case].get("runtime", "python")),
              "prepared_checkpoint": checkpoint, "oracle_sha256": hashlib.sha256(
                  oracle.read_bytes()).hexdigest(),
              "fixture_sha256": files(fixture),
              "evaluator_sha256": evaluator_files(ROOT, oracle, PACK / "cases.json"),
              "plugin_sha256": plugin_files(ROOT)}
    (trial / "trial.json").write_text(json.dumps(record, indent=2) + "\n")
    return {"workspace": str(workspace), "prompt": str(trial / "prompt.md"), "case": case, "depth": selected}


def workflow_verdict(workspace: Path, depth: str) -> dict:
    result = execute([sys.executable, "-B", str(Path(__file__).with_name("coding_trial_evidence.py")),
                      str(ROOT), str(workspace), depth], workspace)
    try:
        report = json.loads(result.get("stdout", ""))
    except json.JSONDecodeError:
        report = None
    success = (result["returncode"] == 0 and isinstance(report, dict)
               and report.get("success") is True and report.get("sections_recorded_complete") is True)
    return {"success": success, "process": result, "report": report}


def check(trial: Path, telemetry: Path | None = None, *, review: Path | None = None) -> dict:
    record = json.loads((trial / "trial.json").read_text())
    workspace = trial / "workspace"
    oracle_path = ROOT / CASES[record["case"]].get("oracle", "tools/coding_trial_checks.py")
    fixture = PACK / CASES[record["case"]].get("fixture", "fixture")
    case = CASES[record["case"]]
    oracle_runner = ["node"] if case.get("runtime") == "node" else [sys.executable, "-B"]
    oracle = execute([*oracle_runner, str(oracle_path), str(workspace), record["case"]], workspace)
    tests = execute(test_command(case), workspace)
    workflow = workflow_verdict(workspace, record["depth"])
    resume = resume_verdict(record, workspace)
    actual = files(workspace)
    changed = sorted(name for name in actual.keys() | record["baseline_files"].keys()
                     if actual.get(name) != record["baseline_files"].get(name))
    protected_changes = sorted(set(changed).intersection(case.get("protected_paths", [])))
    ignored_planning = workspace / ".gitignore"
    safe_ignore = (ignored_planning.is_file() and not ignored_planning.is_symlink()
                   and all(line.strip() in {".planning/", "/.planning/"}
                           for line in ignored_planning.read_text().splitlines()
                           if line.strip() and not line.lstrip().startswith("#")))
    outside_scope = [name for name in changed if name in protected_changes
                     or not name.startswith(("src/", "tests/", ".planning/"))
                     and not (name == ".gitignore" and safe_ignore)]
    try:
        oracle_complete = json.loads(oracle.get("stdout", "")) == {"case": record["case"], "assertions": CASES[record["case"]]["assertions"]}
    except json.JSONDecodeError:
        oracle_complete = False
    try:
        metrics = code_metrics(workspace, case.get("runtime", "python"))
    except (SyntaxError, UnicodeError) as exc:
        metrics = {"error": str(exc)}
    reported = json.loads(telemetry.read_text()) if telemetry else None
    evaluator_changed = record["oracle_sha256"] != hashlib.sha256(oracle_path.read_bytes()).hexdigest()
    evaluator_changed |= record["fixture_sha256"] != files(fixture)
    if record.get("evaluator_sha256") is not None:
        evaluator_changed |= record["evaluator_sha256"] != evaluator_files(ROOT, oracle_path, PACK / "cases.json")
    provenance = plugin_provenance(record, ROOT)
    behavior = {"success": oracle["returncode"] == tests["returncode"] == 0 and oracle_complete
               and not evaluator_changed}
    cleanup = cleanup_verdict(record, workspace, CASES[record["case"]].get("cleanup_required", False), review)
    result = {"success": behavior["success"] and workflow["success"] and cleanup["success"] is not False
              and resume["success"] is not False
              and provenance["success"] and not outside_scope and "error" not in metrics
              and not metrics.get("external_imports") and (record.get("runner") or {}).get("returncode", 0) == 0,
              "case": record["case"], "depth": record["depth"], "runtime": case.get("runtime", "python"), "changed_files": changed, "outside_scope": outside_scope,
              "evaluator_changed": evaluator_changed, "protected_changes": protected_changes,
              "behavior": behavior, "workflow": workflow, "cleanup": cleanup, "resume": resume,
              "plugin_provenance": provenance,
              "oracle_complete": oracle_complete,
              "before": record["baseline_metrics"], "after": metrics, "oracle": oracle, "tests": tests,
              "runner": record.get("runner"), "reported_telemetry": reported,
              "limits": "Structural metrics and AST changes are review aids, not proof of useful cleanup. Independent review is an external attestation, not authenticated identity. Trials are not a security sandbox. POSIX timeout cleanup covers the process group; detached sessions may escape. Windows tree cleanup uses taskkill and is reported if unproven. Missing model usage is unknown. Resume starts from admitted planning, setup and an actual recorded failing test; it does not simulate killing an agent."}
    (trial / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("prepare", "check", "run", "review-template"))
    parser.add_argument("trial", type=Path)
    parser.add_argument("--case", choices=CASES, default="summary")
    parser.add_argument("--depth", choices=("lean", "standard", "deep"))
    parser.add_argument("--telemetry", type=Path)
    parser.add_argument("--review", type=Path, help="Independent review JSON outside the candidate workspace")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--runner", nargs=argparse.REMAINDER, help="Agent argv; prompt arrives on stdin, cwd is the disposable workspace")
    args = parser.parse_args()
    trial = args.trial.resolve()
    if args.operation == "check":
        result = check(trial, args.telemetry, review=args.review)
    elif args.operation == "review-template":
        result = review_template(trial)
    else:
        if args.operation == "run" and not args.runner:
            parser.error("run requires --runner followed by an agent executable and arguments")
        result = prepare(trial, args.case, args.depth)
        if args.operation == "run":
            runner = execute(args.runner, trial / "workspace", prompt=(trial / "prompt.md").read_text(), timeout=args.timeout)
            record = json.loads((trial / "trial.json").read_text())
            record["runner"] = runner
            (trial / "trial.json").write_text(json.dumps(record, indent=2) + "\n")
            result = check(trial, args.telemetry, review=args.review)
            result["success"] = result["success"] and runner["returncode"] == 0
            (trial / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return 0 if result.get("success", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
