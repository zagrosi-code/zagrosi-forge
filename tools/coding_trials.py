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
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "examples/evals/coding"
CASES = json.loads((PACK / "cases.json").read_text())


def files(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file() and not set(p.parts) & {"__pycache__", ".git", ".pytest_cache"}}


def code_metrics(workspace: Path) -> dict:
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
    if case == "resume":
        path = workspace / "src/ledger.py"
        text = path.read_text().replace('    if action == "total":',
            '    if action == "summary":\n        return json.loads(invoice("json", items, customer))\n    if action == "total":', 1)
        path.write_text(text)
        (workspace / ".planning").mkdir()
        (workspace / ".planning/progress.md").write_text(
            "# Interrupted summary feature\n\nExisting actions pass. Summary fields are implemented except item_count.\n"
            "Remaining: quantity aggregation, regression coverage, review, final verification.\n")
        with (workspace / "tests/test_ledger.py").open("a") as handle:
            handle.write('\n    def test_summary_preserves_totals(self):\n        self.assertEqual(invoice("summary", [])["total"], 0)\n')
    selected = depth or CASES[case]["depth"]
    prompt = (f"Work only in {workspace}. Use Forge at {selected} depth.\n"
              f"Read {ROOT / 'skills/zagrosi-implement/references/engineering.md'} and the applicable Forge skills.\n\n"
              f"{CASES[case]['request']}\n\n"
              "Preserve public APIs. Standard library only. Keep .planning records compact.\n"
              f"Set PYTHONPATH to src and run existing/added tests with `{sys.executable} -m unittest discover -s tests`.\n"
              "Report tests, cleanup, remaining issues, and observed usage if available.\n")
    (trial / "prompt.md").write_text(prompt)
    sources = [ROOT / "scripts/zagrosi_skills.py", *sorted((ROOT / "skills").rglob("*.md"))]
    record = {"case": case, "depth": selected, "baseline_files": files(workspace),
              "baseline_metrics": code_metrics(workspace), "oracle_sha256": hashlib.sha256(
                  oracle.read_bytes()).hexdigest(),
              "fixture_sha256": files(fixture),
              "plugin_sha256": {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}}
    (trial / "trial.json").write_text(json.dumps(record, indent=2) + "\n")
    return {"workspace": str(workspace), "prompt": str(trial / "prompt.md"), "case": case, "depth": selected}


def execute(argv: list[str], workspace: Path, *, prompt: str | None = None, timeout: int = 60) -> dict:
    start = time.monotonic()
    env = {**os.environ, "PYTHONPATH": str(workspace / "src"), "PYTHONDONTWRITEBYTECODE": "1", "PYTHONOPTIMIZE": "0"}
    try:
        result = subprocess.run(argv, cwd=workspace, input=prompt, text=True, capture_output=True, timeout=timeout, env=env)
        return {"returncode": result.returncode, "seconds": round(time.monotonic() - start, 3),
                "stdout": result.stdout[-12000:], "stderr": result.stderr[-12000:]}
    except subprocess.TimeoutExpired:
        return {"returncode": 124, "seconds": round(time.monotonic() - start, 3), "stderr": "Timed out"}


def check(trial: Path, telemetry: Path | None = None) -> dict:
    record = json.loads((trial / "trial.json").read_text())
    workspace = trial / "workspace"
    actual = files(workspace)
    changed = sorted(name for name in actual.keys() | record["baseline_files"].keys()
                     if actual.get(name) != record["baseline_files"].get(name))
    outside_scope = [name for name in changed if not name.startswith(("src/", "tests/", ".planning/"))]
    oracle_path = ROOT / CASES[record["case"]].get("oracle", "tools/coding_trial_checks.py")
    fixture = PACK / CASES[record["case"]].get("fixture", "fixture")
    oracle = execute([sys.executable, "-B", str(oracle_path), str(workspace), record["case"]], workspace)
    tests = execute([sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests"], workspace)
    try:
        oracle_complete = json.loads(oracle.get("stdout", "")) == {"case": record["case"], "assertions": CASES[record["case"]]["assertions"]}
    except json.JSONDecodeError:
        oracle_complete = False
    try:
        metrics = code_metrics(workspace)
    except (SyntaxError, UnicodeError) as exc:
        metrics = {"error": str(exc)}
    reported = json.loads(telemetry.read_text()) if telemetry else None
    evaluator_changed = record["oracle_sha256"] != hashlib.sha256(oracle_path.read_bytes()).hexdigest()
    evaluator_changed |= record["fixture_sha256"] != files(fixture)
    result = {"success": oracle["returncode"] == tests["returncode"] == 0 and oracle_complete and not outside_scope
              and "error" not in metrics and not metrics.get("external_imports") and not evaluator_changed
              and (record.get("runner") or {}).get("returncode", 0) == 0,
              "case": record["case"], "depth": record["depth"], "changed_files": changed, "outside_scope": outside_scope,
              "evaluator_changed": evaluator_changed,
              "oracle_complete": oracle_complete,
              "before": record["baseline_metrics"], "after": metrics, "oracle": oracle, "tests": tests,
              "runner": record.get("runner"), "reported_telemetry": reported,
              "limits": "Structural metrics are review aids, not readability scores. Missing model usage is unknown. Resume uses a prepared interruption checkpoint."}
    (trial / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("prepare", "check", "run"))
    parser.add_argument("trial", type=Path)
    parser.add_argument("--case", choices=CASES, default="summary")
    parser.add_argument("--depth", choices=("lean", "standard", "deep"))
    parser.add_argument("--telemetry", type=Path)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--runner", nargs=argparse.REMAINDER, help="Agent argv; prompt arrives on stdin, cwd is the disposable workspace")
    args = parser.parse_args()
    trial = args.trial.resolve()
    if args.operation == "check":
        result = check(trial, args.telemetry)
    else:
        if args.operation == "run" and not args.runner:
            parser.error("run requires --runner followed by an agent executable and arguments")
        result = prepare(trial, args.case, args.depth)
        if args.operation == "run":
            runner = execute(args.runner, trial / "workspace", prompt=(trial / "prompt.md").read_text(), timeout=args.timeout)
            record = json.loads((trial / "trial.json").read_text())
            record["runner"] = runner
            (trial / "trial.json").write_text(json.dumps(record, indent=2) + "\n")
            result = check(trial, args.telemetry)
            result["success"] = result["success"] and runner["returncode"] == 0
            (trial / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return 0 if result.get("success", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
