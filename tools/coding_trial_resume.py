"""Prepare an admitted Forge checkpoint with a genuinely failing feature test."""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import shlex
import subprocess
import sys
import unittest

from coding_trial_process import execute

SECTION = "section-01-invoice-summary"
TEST_PATH = "tests/test_ledger.py"
TEST_CLASS = "InvoiceTests"
TEST_METHOD = "test_summary_counts_quantities"


def test_identity(workspace: Path) -> str | None:
    """Bind the prepared method and its discovery/skip semantics, not added tests."""
    tree = ast.parse((workspace / TEST_PATH).read_text())
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == TEST_CLASS]
    if len(classes) != 1:
        return None
    cls = classes[0]
    methods = [node for node in cls.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == TEST_METHOD]
    if len(methods) != 1:
        return None
    method = methods[0]
    if (method.body and isinstance(method.body[0], ast.Expr)
            and isinstance(method.body[0].value, ast.Constant) and isinstance(method.body[0].value.value, str)):
        method.body.pop(0)
    signature = {"class_bases": [ast.dump(node) for node in cls.bases],
                 "class_decorators": [ast.dump(node) for node in cls.decorator_list],
                 "class_keywords": [ast.dump(node) for node in cls.keywords], "method": ast.dump(method)}
    return hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()


def resume_verdict(record: dict, workspace: Path) -> dict:
    if record["case"] != "resume":
        return {"success": None, "status": "not_required", "reasons": []}
    prepared = record.get("prepared_checkpoint") or {}
    reasons = []
    try:
        events = json.loads((workspace / ".planning/implementation/forge-progress.json").read_text())["events"]
        if not isinstance(events, list) or not events or events[0] != prepared.get("event"):
            reasons.append("the original red checkpoint was removed or changed")
    except (OSError, ValueError, KeyError, TypeError):
        reasons.append("the original red checkpoint is missing or unreadable")
    try:
        identity = test_identity(workspace)
        if not identity or identity != prepared.get("test_identity"):
            reasons.append("the prepared regression test was removed, replaced or disabled")
    except (OSError, ValueError, SyntaxError):
        reasons.append("the prepared regression test is missing or unreadable")
    process = None
    if not reasons:
        process = execute([sys.executable, "-B", str(Path(__file__).resolve()), str(workspace)], workspace)
        try:
            passed = json.loads(process["stdout"]) == {"success": True, "tests_run": 1}
        except ValueError:
            passed = False
        if process["returncode"] != 0 or not passed:
            reasons.append("the prepared regression test must run and pass without skipping")
    return {"success": not reasons, "reasons": reasons, "test_process": process}


def run_prepared_test(workspace: Path) -> int:
    spec = importlib.util.spec_from_file_location("forge_prepared_test", workspace / TEST_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    suite = unittest.defaultTestLoader.loadTestsFromName(f"{TEST_CLASS}.{TEST_METHOD}", module)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    success = result.wasSuccessful() and result.testsRun == 1 and not result.skipped
    print(json.dumps({"success": success, "tests_run": result.testsRun}))
    return 0 if success else 1


def prepare_resume(root: Path, workspace: Path, depth: str, test_argv: list[str]) -> dict:
    source = workspace / "src/ledger.py"
    source.write_text(source.read_text().replace('    if action == "total":',
        '    if action == "summary":\n        return json.loads(invoice("json", items, customer))\n    if action == "total":', 1))
    with (workspace / "tests/test_ledger.py").open("a") as handle:
        handle.write('''
    def test_summary_counts_quantities(self):
        items = [{"price": 101, "quantity": 2}, {"price": 3, "quantity": 3}]
        self.assertEqual(invoice("summary", items)["item_count"], 5)
''')
    planning = workspace / ".planning"
    shutil.copytree(root / "examples/evals/coding/resume-plan", planning)
    index = planning / "sections/index.md"
    index.write_text(index.read_text().replace('"depth_mode":"standard"', '"depth_mode":' + json.dumps(depth)))
    cli = [sys.executable, "-B", str(root / "scripts/zagrosi_skills.py")]
    for name, args in (
        ("admission", ["postflight", "--phase", "plan", "--planning-dir", str(planning), "--depth", depth, "--strict"]),
        ("setup", ["implement-setup", "--sections-dir", str(planning / "sections"), "--target-dir", str(workspace), "--depth", depth]),
    ):
        result = execute(cli + args, workspace)
        try:
            payload = json.loads(result["stdout"])
        except ValueError:
            payload = {}
        if result["returncode"] != 0 or payload.get("success") is not True:
            raise ValueError(f"Resume {name} failed: {result}")
    red = execute(test_argv, workspace)
    if (red["returncode"] == 0 or "test_summary_counts_quantities" not in red["stderr"]
            or "KeyError: 'item_count'" not in red["stderr"]):
        raise ValueError(f"Resume preparation did not observe the missing-field regression: {red}")
    command = subprocess.list2cmdline(test_argv) if sys.platform == "win32" else shlex.join(test_argv)
    progress = execute(cli + ["implement-progress", "--planning-dir", str(planning), "--section", SECTION,
                       "--stage", "red", "--command", command,
                       "--result", "failed: test_summary_counts_quantities; missing item_count",
                       "--notes", "Complete quantity aggregation, preserve existing actions, then verify and review."], workspace)
    if progress["returncode"] != 0 or json.loads(progress["stdout"]).get("success") is not True:
        raise ValueError(f"Resume checkpoint failed: {progress}")
    checkpoint = ".planning/implementation/forge-progress.json"
    event = json.loads((workspace / checkpoint).read_text())["events"][0]
    return {"section": SECTION, "admitted": True, "stage": "red", "test_process": red,
            "checkpoint": checkpoint, "event": event, "test_identity": test_identity(workspace)}


if __name__ == "__main__":
    raise SystemExit(run_prepared_test(Path(sys.argv[1])))
