"""Feature trials assess incidental cleanup, scope, another runtime and real resume."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys

import pytest

from test_coding_trials import trials, write_cleanup, write_review


def add_summary(path: Path):
    path.write_text(path.read_text().replace('    if action not in',
        '    if action == "summary":\n        result = json.loads(invoice("json", items, customer))\n'
        '        result["item_count"] = sum(item["quantity"] for item in items)\n'
        '        return result\n    if action not in', 1))


def test_ordinary_summary_requires_independently_reviewed_incidental_cleanup(tmp_path):
    trial = tmp_path / "trial"
    trials.prepare(trial, "summary")
    request = trials.CASES["summary"]["request"].lower()
    assert "cleanup" not in request and "refactor" not in request
    write_cleanup(trial)
    add_summary(trial / "workspace/src/ledger.py")
    unreviewed = trials.check(trial)
    assert unreviewed["behavior"]["success"]
    assert not unreviewed["cleanup"]["success"]
    reviewed = trials.check(trial, review=write_review(trial))
    assert reviewed["cleanup"]["success"]
    assert not reviewed["success"]  # Coding evidence alone cannot complete the workflow.
    protected = trial / "workspace/src/legacy_reports.py"
    protected.write_text('def report(labels):\n    return "".join(str(label) + "\\n" for label in labels)\n')
    result = trials.check(trial, review=write_review(trial))
    assert result["behavior"]["success"]
    assert result["protected_changes"] == ["src/legacy_reports.py"]
    assert result["outside_scope"] == ["src/legacy_reports.py"]
    assert not result["success"]


def add_node_summary(path):
    path.write_text(path.read_text().replace("  if (action === 'total')", """  if (action === 'summary') {
    const summary = JSON.parse(invoice('json', items, customer));
    summary.item_count = items.reduce((n, item) => n + item.quantity, 0);
    return summary;
  }
  if (action === 'total')""", 1))


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is unavailable")
def test_node_case_runs_native_tests_and_independent_oracle(tmp_path):
    trial = tmp_path / "trial"
    trials.prepare(trial, "node-summary")
    baseline = trials.check(trial)
    assert baseline["tests"]["returncode"] == 0
    assert not baseline["behavior"]["success"]
    path = trial / "workspace/src/ledger.js"
    add_node_summary(path)
    result = trials.check(trial)
    assert result["behavior"]["success"]
    assert json.loads(result["oracle"]["stdout"])["assertions"] == 1267
    assert result["after"]["largest_function_lines"] is None
    assert result["reported_telemetry"] is None
    workspace = trial / "workspace"
    planning = workspace / ".planning"
    shutil.copytree(trials.PACK / "resume-plan", planning)
    for artifact in planning.rglob("*.md"):
        text = artifact.read_text().replace('"depth_mode":"standard"', '"depth_mode":"lean"')
        text = text.replace("src/ledger.py", "src/ledger.js").replace("tests/test_ledger.py", "tests/ledger.test.js")
        text = text.replace("python -m unittest discover -s tests", "node --test tests/ledger.test.js")
        text = text.replace("runtime: python3", "runtime: node").replace("Python standard library", "Node standard library")
        text = text.replace("with PYTHONPATH=src", "using the built-in Node test runner")
        text = text.replace("test_summary_counts_quantities", "summary counts quantities")
        text = text.replace("unittest discovers the test module", "the test command discovers the module")
        text = text.replace("current partial summary omits the field", "the original API rejects summary")
        text = text.replace("The partial\nsummary reuses JSON serialization and lacks item_count", "The original API has no summary action")
        artifact.write_text(text)
    tests = workspace / "tests/ledger.test.js"
    with tests.open("a") as handle:
        handle.write("\ntest('summary counts quantities', () => assert.equal(invoice('summary', [{price: 3, quantity: 5}]).item_count, 5));\n")
    assert trials.execute(trials.test_command(trials.CASES["node-summary"]), workspace)["returncode"] == 0
    cli = [sys.executable, str(trials.ROOT / "scripts/zagrosi_skills.py")]
    for command in (
        ["postflight", "--phase", "plan", "--planning-dir", str(planning), "--strict"],
        ["implement-setup", "--sections-dir", str(planning / "sections"), "--target-dir", str(workspace)],
        ["implement-record-section", "--sections-dir", str(planning / "sections"), "--target-dir", str(workspace),
         "--section", "section-01-invoice-summary", "--file", "src/ledger.js", "--test-file", "tests/ledger.test.js",
         "--review-status", "pass", "--verification", "node --test tests/ledger.test.js: passed; independent oracle:1267 assertions"],
    ):
        process = trials.execute(cli + command, workspace)
        assert process["returncode"] == 0, process
    assert trials.check(trial)["success"]
    (trial / "workspace/tests/ledger.test.js").write_text("// Candidate tests are not the independent oracle.\n")
    path.write_text(path.read_text().replace("n + item.quantity", "n + 1"))
    result = trials.check(trial)
    assert result["tests"]["returncode"] == 0
    assert not result["behavior"]["success"]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is unavailable")
@pytest.mark.parametrize("mutation", ["exports", "error-type"])
def test_node_oracle_pins_public_exports_and_error_types(tmp_path, mutation):
    trial = tmp_path / "trial"
    trials.prepare(trial, "node-summary")
    path = trial / "workspace/src/ledger.js"
    add_node_summary(path)
    if mutation == "exports":
        path.write_text(path.read_text() + "\nmodule.exports.unrequested = true;\n")
    else:
        path.write_text(path.read_text().replace("throw new Error(", "throw new TypeError("))
    result = trials.check(trial)
    assert result["tests"]["returncode"] == 0
    assert not result["behavior"]["success"]


@pytest.mark.parametrize("depth", ["lean", "standard", "deep"])
def test_resume_prepares_admitted_state_and_a_real_red_checkpoint(tmp_path, depth):
    trial = tmp_path / "trial"
    trials.prepare(trial, "resume", depth)
    workspace = trial / "workspace"
    planning = workspace / ".planning"
    record = json.loads((trial / "trial.json").read_text())
    prepared = record["prepared_checkpoint"]
    assert prepared["admitted"] and prepared["test_process"]["returncode"] != 0
    assert "test_summary_counts_quantities" in prepared["test_process"]["stderr"]
    state = json.loads((planning / "implementation/zagrosi_implement_state.json").read_text())
    assert state["completed_sections"] == {}
    checkpoint = json.loads((workspace / prepared["checkpoint"]).read_text())["events"][-1]
    assert checkpoint["stage"] == "red"
    assert sys.executable in checkpoint["command"]
    assert checkpoint["snapshot"]["code"] and checkpoint["snapshot"]["contract"]
    result = trials.execute([sys.executable, str(trials.ROOT / "scripts/zagrosi_skills.py"),
                             "status", "--path", str(planning)], workspace)
    assert result["returncode"] == 0, result
    status = json.loads(result["stdout"])
    resume = status.get("resume") or status.get("implementation", {}).get("resume")
    assert resume["stage"] == "red" and resume["evidence_current"]
    assert not trials.check(trial)["workflow"]["success"]


def test_resume_template_is_bound_to_evaluator_provenance(tmp_path):
    trial = tmp_path / "trial"
    trials.prepare(trial, "summary")
    provenance = json.loads((trial / "trial.json").read_text())["evaluator_sha256"]
    assert "tools/coding_trial_resume.py" in provenance
    assert "examples/evals/coding/resume-plan/sections/section-01-invoice-summary.md" in provenance


@pytest.mark.parametrize("runtime", ["python", "node"])
def test_oracle_rejects_candidate_input_mutation(tmp_path, runtime):
    if runtime == "node" and shutil.which("node") is None:
        pytest.skip("Node is unavailable")
    trial = tmp_path / "trial"
    trials.prepare(trial, "summary" if runtime == "python" else "node-summary")
    if runtime == "python":
        path = trial / "workspace/src/ledger.py"
        text = path.read_text().replace('    if action == "total":', '''    if len(items) > 1:
        del items[1:]
    if action == "summary":
        result = json.loads(invoice("json", items, customer))
        result["item_count"] = sum(item["quantity"] for item in items)
        return result
    if action == "total":''', 1)
    else:
        path = trial / "workspace/src/ledger.js"
        text = path.read_text().replace("  if (action === 'total')", """  if (items.length > 1) items.splice(1);
  if (action === 'summary') {
    const result = JSON.parse(invoice('json', items, customer));
    result.item_count = items.reduce((n, item) => n + item.quantity, 0);
    return result;
  }
  if (action === 'total')""", 1)
    path.write_text(text)
    result = trials.check(trial)
    assert result["tests"]["returncode"] == 0
    assert not result["behavior"]["success"]


@pytest.mark.parametrize("change", ["remove-test", "replace-test", "skip-test", "remove-progress", "change-event", "append"])
def test_resume_preserves_original_test_and_history_while_allowing_progress(tmp_path, change):
    trial = tmp_path / "trial"
    trials.prepare(trial, "resume", "lean")
    workspace = trial / "workspace"
    source = workspace / "src/ledger.py"
    source.write_text(source.read_text().replace('        return json.loads(invoice("json", items, customer))',
        '        result = json.loads(invoice("json", items, customer))\n'
        '        result["item_count"] = sum(item["quantity"] for item in items)\n        return result', 1))
    tests = workspace / "tests/test_ledger.py"
    history = workspace / ".planning/implementation/forge-progress.json"
    if change == "remove-test":
        tests.write_text(tests.read_text().split("    def test_summary_counts_quantities", 1)[0])
    elif change == "replace-test":
        tests.write_text(tests.read_text().replace('self.assertEqual(invoice("summary", items)["item_count"], 5)', "self.assertTrue(True)"))
    elif change == "skip-test":
        tests.write_text(tests.read_text().replace("    def test_summary_counts_quantities", "    @unittest.skip('unverified')\n    def test_summary_counts_quantities"))
    elif change == "remove-progress":
        history.unlink()
    elif change == "change-event":
        events = json.loads(history.read_text())
        events["events"][0]["result"] = "not observed"
        history.write_text(json.dumps(events))
    else:
        with tests.open("a") as handle:
            handle.write('\n    def test_empty_summary_count(self):\n        self.assertEqual(invoice("summary", [])["item_count"], 0)\n')
        args = ["implement-progress", "--planning-dir", str(workspace / ".planning"), "--section", "section-01-invoice-summary",
                "--stage", "green", "--command", "python -m unittest discover -s tests", "--result", "passed"]
        assert trials.execute([sys.executable, str(trials.ROOT / "scripts/zagrosi_skills.py"), *args], workspace)["returncode"] == 0
    process = trials.execute([sys.executable, str(trials.ROOT / "scripts/zagrosi_skills.py"),
                              "implement-record-section", "--sections-dir", str(workspace / ".planning/sections"),
                              "--target-dir", str(workspace), "--section", "section-01-invoice-summary",
                              "--review-status", "pass", "--verification", "python -m unittest discover -s tests: passed",
                              "--flight", "strict"], workspace)
    assert process["returncode"] == 0, process
    result = trials.check(trial)
    assert result["workflow"]["success"] and result["behavior"]["success"]
    assert result["success"] is (change == "append")


def complete_import_preview(workspace):
    """Known-good candidate for testing the evaluator, not a model-trial result."""
    with (workspace / "src/orders.py").open("a") as handle:
        handle.write('''

def preview_import(text, catalog):
    rows = import_orders(text, catalog)
    quantities = {}
    for row in rows:
        quantities[row["sku"]] = quantities.get(row["sku"], 0) + row["quantity"]
    return {"order_count": len({row["order_id"] for row in rows}),
            "item_count": sum(row["quantity"] for row in rows),
            **{key: sum(row[key] for row in rows) for key in ("subtotal", "tax", "total")},
            "sku_quantities": dict(sorted(quantities.items()))}
''')
    (workspace / "src/receipts.py").write_text('''import json
from orders import import_orders


def export_receipts(text, catalog, format="json"):
    if format not in ("json", "text"):
        raise ValueError("Unknown format: " + str(format))
    records = import_orders(text, catalog)
    if format == "json":
        return json.dumps(records, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "\\n".join(row["order_id"] + ": " + row["sku"] + " x" + str(row["quantity"])
                     + " = " + str(row["total"]) for row in records)
''')


def test_import_preview_requires_feature_review_and_protected_scope(tmp_path):
    trial = tmp_path / "trial"
    trials.prepare(trial, "import-preview")
    workspace = trial / "workspace"
    request = trials.CASES["import-preview"]["request"] + (workspace / "prompt.md").read_text()
    assert "cleanup" not in request and "refactor" not in request
    baseline = trials.check(trial)
    assert baseline["tests"]["returncode"] == 0
    assert not baseline["behavior"]["success"]
    complete_import_preview(workspace)
    unreviewed = trials.check(trial)
    assert unreviewed["behavior"]["success"], unreviewed["oracle"]
    assert not unreviewed["cleanup"]["success"]
    assert unreviewed["after"]["repeated_loops"] < unreviewed["before"]["repeated_loops"]
    review = trials.review_template(trial)
    review.update(reviewer="independent-test-reviewer", independent=True, verdict="pass")
    review["cleanup"].update(meaningful=True, changed_files=["src/receipts.py"],
        rationale="Receipts share validated imports; duplicate CSV validation and pricing loops are removed.",
        regression_evidence="Original tests and 638 independent assertions preserve imports, exports and errors.")
    review_path = trial / "review.json"
    review_path.write_text(json.dumps(review))
    reviewed = trials.check(trial, review=review_path)
    assert reviewed["cleanup"]["success"]
    assert not reviewed["success"]  # A real admitted/completed workflow is still required.
    protected = workspace / "src/customer_reports.py"
    protected.write_text(protected.read_text() + "\n# Unrelated change.\n")
    result = trials.check(trial, review=review_path)
    assert result["behavior"]["success"]
    assert result["outside_scope"] == ["src/customer_reports.py"]
    assert not result["success"]


@pytest.mark.parametrize("mutation", ["count", "float-count", "bool-count", "rounding", "aggregate-tax", "mutation", "export", "error", "duplicate"])
def test_import_preview_oracle_rejects_regressions_with_vacuous_candidate_tests(tmp_path, mutation):
    trial = tmp_path / "trial"
    trials.prepare(trial, "import-preview")
    workspace = trial / "workspace"
    complete_import_preview(workspace)
    (workspace / "tests/test_orders.py").write_text(
        "import unittest\nclass VacuousTest(unittest.TestCase):\n    def test_nothing(self):\n        self.assertTrue(True)\n")
    files = {"count": "orders", "float-count": "orders", "bool-count": "orders", "rounding": "pricing", "mutation": "orders",
             "aggregate-tax": "orders", "export": "receipts", "error": "pricing", "duplicate": "orders"}
    path = workspace / "src" / (files[mutation] + ".py")
    before, after = {
        "count": ('len({row["order_id"] for row in rows})', 'len(rows)'),
        "float-count": ('len({row["order_id"] for row in rows})', 'float(len({row["order_id"] for row in rows}))'),
        "bool-count": ('len({row["order_id"] for row in rows})', 'len({row["order_id"] for row in rows}) or False'),
        "rounding": ('subtotal * 20 // 100', 'round(subtotal * 20 / 100)'),
        "aggregate-tax": ('"sku_quantities": dict(sorted(quantities.items()))',
                          '"tax": sum(row["subtotal"] for row in rows) * 20 // 100, "sku_quantities": dict(sorted(quantities.items()))'),
        "mutation": ('    rows = import_orders(text, catalog)', '    rows = import_orders(text, catalog)\n    catalog.clear()'),
        "export": ('separators=(",", ":")', 'separators=(", ", ": ")'),
        "error": ('raise KeyError("Unknown SKU: " + sku)', 'raise ValueError("Unknown SKU: " + sku)'),
        "duplicate": ('if (order_id, sku) in seen:', 'if False:'),
    }[mutation]
    source = path.read_text()
    assert before in source
    path.write_text(source.replace(before, after))
    result = trials.check(trial)
    assert result["tests"]["returncode"] == 0
    assert not result["behavior"]["success"]
