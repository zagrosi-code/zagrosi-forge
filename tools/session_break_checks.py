#!/usr/bin/env python3
"""Withheld-decision clauses, their probes and per-test outcomes for session-break trials.

`tests` and `probe` import candidate code; run them in a disposable copy of the workspace.
"""
from __future__ import annotations

import argparse
import contextlib
import importlib
import io
import json
from pathlib import Path
import sys
import unittest

STATUSES = ("draft", "confirmed", "shipped", "cancelled")

# Each clause leaves the workspace brief and reaches only session 1, as the user's answer.
# The oracle is unchanged; each probe isolates one clause so the oracle's first failure cannot mask it.
CLAUSES = {
    "godfile": (
        {"id": "latest_created_on_all_orders",
         "clarification": "`latest_created_on` covers every order, including cancelled ones.",
         "edits": (("latest normalized ISO date across every order, or `None` when empty.",
                    "latest normalized ISO date, or `None` when empty."),)},
        {"id": "status_counts_zero",
         "clarification": "`status_counts` lists all four statuses, including zero counts.",
         "edits": (("every existing status (`draft`, `confirmed`, `shipped`, `cancelled`), including zero counts.",
                    "counts by status."),
                   ("Empty input returns zero counts,\nzero confirmed_total", "Empty input returns\nzero confirmed_total"))},
    ),
    "import-preview": (
        {"id": "tax_per_line",
         "clarification": "Tax rounds down per line; never round again over the combined subtotal.",
         "edits": (("sums of the existing per-line amounts. Tax still\n  rounds down per line; do not round again over "
                    "the combined subtotal.", "sums of the existing amounts."),)},
    ),
}
MODULES = {"godfile": "dispatch", "import-preview": "orders"}
FEATURES = {"godfile": ("summarize_orders", "OrderBook.summary"), "import-preview": ("preview_import",)}


def derived_brief(text: str, case: str) -> str:
    """Remove every withheld clause; each edit must match the fixture brief exactly once."""
    for clause in CLAUSES[case]:
        for old, new in clause["edits"]:
            if text.count(old) != 1:
                raise ValueError(f"Withheld clause {clause['id']} does not match the {case} brief exactly once")
            text = text.replace(old, new)
    return text


class ControlFailure(Exception):
    """A clause-neutral value is wrong, so the clause itself cannot be judged."""


def _order(order_id, status, created_on):
    return {"id": order_id, "customer": "Ada", "region": "uk", "created_on": created_on, "status": status,
            "items": [{"sku": "PEN", "price": 125, "quantity": 1, "weight_grams": 50}]}


def _summaries(module, orders):
    summaries = [module.summarize_orders(list(orders)), module.OrderBook(orders).summary()]
    if any(not isinstance(summary, dict) or summary.get("order_count") != len(orders) for summary in summaries):
        raise ControlFailure("order_count")
    return summaries


def _preview(module, text, catalog):
    preview = module.preview_import(text, catalog)
    if not isinstance(preview, dict) or (preview.get("order_count"), preview.get("item_count")) != (2, 2):
        raise ControlFailure("order_count/item_count")
    return preview


def probes(case: str, module) -> dict:
    if case == "godfile":
        latest = [_order("O-1", "confirmed", "2026-02-01"), _order("O-2", "cancelled", "2026-02-09")]
        sparse = [_order("O-1", "draft", "2026-02-01"), _order("O-2", "confirmed", "2026-02-02")]
        return {
            "latest_created_on_all_orders": lambda: all(
                summary["latest_created_on"] == "2026-02-09" for summary in _summaries(module, latest)),
            "status_counts_zero": lambda: all(
                summary["status_counts"] == {"draft": 1, "confirmed": 1, "shipped": 0, "cancelled": 0}
                for summary in _summaries(module, sparse)) and all(
                summary["status_counts"] == dict.fromkeys(STATUSES, 0) for summary in _summaries(module, [])),
        }
    # Each 3-cent line has 0 tax; rounding the combined 6-cent subtotal again would give 1.
    return {"tax_per_line": lambda: _preview(module, "order_id,sku,quantity\nA,X,1\nB,Y,1\n", {"X": 3, "Y": 3})["tax"] == 0}


def _present(module, name: str) -> bool:
    value = module
    for part in name.split("."):
        value = getattr(value, part, None)
    return callable(value)


def run_probes(workspace: Path, case: str) -> dict[str, str]:
    """pass, fail, missing (feature not yet added) or error, per clause."""
    sys.path.insert(0, str(workspace / "src"))
    try:
        module = importlib.import_module(MODULES[case])
    except Exception as exc:  # Any candidate import failure is a probe result, not a harness failure.
        return {clause["id"]: f"error: {type(exc).__name__}" for clause in CLAUSES[case]}
    if not all(_present(module, name) for name in FEATURES[case]):
        return {clause["id"]: "missing" for clause in CLAUSES[case]}
    results = {}
    for name, probe in probes(case, module).items():
        try:
            results[name] = "pass" if probe() is True else "fail"
        except Exception as exc:
            results[name] = f"error: {type(exc).__name__}"
    return results


class _Outcomes(unittest.TestResult):
    """Keep each test ID's worst observed outcome; subtest failures fail their test."""
    RANK = {"passed": 0, "skipped": 1, "expected_failure": 1, "unexpected_success": 2, "failed": 3, "error": 4}

    def __init__(self):
        super().__init__()
        self.outcomes = {}

    def _record(self, test, outcome):
        current = self.outcomes.get(test.id())
        if current is None or self.RANK[outcome] > self.RANK[current]:
            self.outcomes[test.id()] = outcome

    def addSuccess(self, test):
        self._record(test, "passed")

    def addFailure(self, test, err):
        self._record(test, "failed")

    def addError(self, test, err):
        self._record(test, "error")

    def addSkip(self, test, reason):
        self._record(test, "skipped")

    def addExpectedFailure(self, test, err):
        self._record(test, "expected_failure")

    def addUnexpectedSuccess(self, test):
        self._record(test, "unexpected_success")

    def addSubTest(self, test, subtest, err):
        if err is not None:
            self._record(test, "failed" if issubclass(err[0], test.failureException) else "error")


def test_outcomes(workspace: Path) -> dict[str, str]:
    """Outcome per unittest ID, discovered as `python -m unittest discover -s tests` would."""
    sys.path.insert(0, str(workspace / "src"))
    result = _Outcomes()
    # Candidate output must not corrupt the JSON report on stdout.
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        suite = unittest.defaultTestLoader.discover(str(workspace / "tests"), top_level_dir=str(workspace / "tests"))
        suite.run(result)
    return result.outcomes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("tests", "probe"))
    parser.add_argument("workspace", type=Path)
    parser.add_argument("case", nargs="?", choices=sorted(CLAUSES))
    args = parser.parse_args()
    sys.dont_write_bytecode = True
    workspace = args.workspace.resolve()
    # As with `python -m unittest` from the workspace: its root, not this script's directory, leads sys.path.
    sys.path[0] = str(workspace)
    if args.operation == "probe" and args.case is None:
        parser.error("probe requires a case")
    report = test_outcomes(workspace) if args.operation == "tests" else run_probes(workspace, args.case)
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
