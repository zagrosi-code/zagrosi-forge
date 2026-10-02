"""Invoice cleanup keeps real caller contracts despite passing happy-path tests."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "examples/evals/coding/fixture"
ORACLE = ROOT / "tools/coding_trial_checks.py"

SHARED = '''import json

def _totals(items, count_items):
    subtotal = count = 0
    for item in items:
        price = item["price"]
        quantity = item["quantity"]
        subtotal = subtotal + price * quantity
        if count_items:
            count += quantity
    return subtotal, count

def invoice(action, items, customer="Guest", **options):
    if action not in ("total", "json", "receipt", "summary"):
        raise ValueError("Unknown action: " + str(action))
    subtotal, count = _totals(items, action == "summary")
    tax = subtotal * 20 // 100
    total = subtotal + tax
    if action == "total":
        return total
    result = {"customer": customer, "subtotal": subtotal, "tax": tax, "total": total}
    if action == "summary":
        return {**result, "item_count": count}
    if action == "json":
        return json.dumps(result, sort_keys=True)
    return ("Customer: " + str(customer) + "\\nSubtotal: " + str(subtotal)
            + "\\nTax: " + str(tax) + "\\nTotal: " + str(total) + "\\n")

class InvoiceManager:
    def total(self, items):
        return invoice("total", items)
'''


@pytest.fixture
def workspace(tmp_path):
    return Path(shutil.copytree(FIXTURE, tmp_path / "workspace"))


def check(workspace, case="cleanup"):
    return subprocess.run([sys.executable, "-B", str(ORACLE), str(workspace), case],
                          capture_output=True, text=True, timeout=10)


@pytest.mark.parametrize("mutation", [
    "read-order", "error-type", "eager-input", "second-pass", "signature", "default",
    "ignored-option", "import", "wildcard-export", "wrapper-signature", "eager-count",
    "unhashable-action-type", "unhashable-action-args", "unhashable-action-consumption",
])
def test_oracle_rejects_compatibility_regressions_with_passing_writer_tests(workspace, mutation):
    path = workspace / "src/ledger.py"
    source = path.read_text()
    if mutation == "read-order":
        source = source.replace('item["price"] * item["quantity"]', 'item["quantity"] * item["price"]')
    elif mutation == "error-type":
        source = source.replace('item["price"]', 'item.get("price", 0)')
    elif mutation == "eager-input":
        source = source.replace('    if action == "total":', '    items = list(items)\n    if action == "total":')
    elif mutation == "second-pass":
        source = source.replace('    if action == "total":', '    if action == "total":\n        sum(1 for _ in items)')
    elif mutation == "signature":
        source = source.replace(', **options', '')
    elif mutation == "default":
        source = source.replace('customer="Guest"', 'customer="Anonymous"')
        # The two original tests pass with their explicit/default customer paths retained.
        source = source.replace('    if action == "total":',
                                '    if customer == "Anonymous":\n        customer = "Guest"\n    if action == "total":')
    elif mutation == "ignored-option":
        source = source.replace('    if action == "total":',
                                '    if options:\n        raise ValueError("options rejected")\n    if action == "total":')
    elif mutation == "import":
        source = source.replace('import json', 'import json as _json').replace('json.dumps', '_json.dumps')
    elif mutation == "wildcard-export":
        source += '\n__all__ = ["invoice", "InvoiceManager"]\n'
    elif mutation == "eager-count":
        source = SHARED.replace('        if count_items:\n            count += quantity',
                                '        count += quantity')
    elif mutation == "unhashable-action-type":
        source = SHARED.replace('not in ("total", "json", "receipt", "summary")',
                                'not in {"total", "json", "receipt", "summary"}')
    elif mutation in {"unhashable-action-args", "unhashable-action-consumption"}:
        action = 'raise ValueError("Unknown action")' if mutation.endswith("args") else 'list(items)'
        source = SHARED.replace('    if action not in',
            f'    if isinstance(action, (list, dict)):\n        {action}\n    if action not in')
    else:
        source = source.replace('def total(self, items):', 'def total(self, items, required):')
        # Candidate tests do not cover the public wrapper.
    path.write_text(source)
    tests = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests"],
                           cwd=workspace, env={**os.environ, "PYTHONPATH": "src"},
                           capture_output=True, text=True, timeout=10)
    assert tests.returncode == 0, tests.stderr
    result = check(workspace)
    assert result.returncode != 0, f"Oracle accepted {mutation}: {result.stdout}"


def test_summary_rejects_two_pass_quantity_counting(workspace):
    path = workspace / "src/ledger.py"
    path.write_text(SHARED.replace('return {**result, "item_count": count}',
                                  'return {**result, "item_count": sum(item["quantity"] for item in items)}'))
    result = check(workspace, "summary")
    assert result.returncode != 0, result.stdout


@pytest.mark.parametrize("case", ["baseline", "cleanup", "summary", "resume", "discount", "discount-options"])
def test_baseline_and_shared_calculation_preserve_public_contracts(workspace, case):
    if case != "baseline":
        source = SHARED
        if case.startswith("discount"):
            if case == "discount":
                source = source.replace('customer="Guest", **options', 'customer="Guest", *, discount_percent=0, **options')
            else:
                source = source.replace('    subtotal, count =',
                    '    discount_percent = options.get("discount_percent", 0)\n    subtotal, count =')
            source = source.replace('    subtotal, count =',
                '    if type(discount_percent) is not int or not 0 <= discount_percent <= 100:\n'
                '        raise ValueError("invalid discount")\n    subtotal, count =')
            source = source.replace('    tax = subtotal', '    subtotal -= subtotal * discount_percent // 100\n    tax = subtotal')
        (workspace / "src/ledger.py").write_text(source)
    selected = "cleanup" if case == "baseline" else case.removesuffix("-options")
    result = check(workspace, selected)
    assert result.returncode == 0, result.stderr
    cases = json.loads((ROOT / "examples/evals/coding/cases.json").read_text())
    assert json.loads(result.stdout) == {"case": selected, "assertions": cases[selected]["assertions"]}
