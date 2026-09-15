"""Independent behavior oracle; run outside the editable trial workspace."""
from __future__ import annotations

import importlib.util
import json
import random
import sys
from pathlib import Path

FIXTURE = Path(__file__).resolve().parents[1] / "examples/evals/coding/fixture/src/ledger.py"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify(workspace: Path, case: str) -> int:
    sys.path.insert(0, str(workspace / "src"))
    baseline = load(FIXTURE, "baseline_ledger")
    candidate = load(workspace / "src/ledger.py", "candidate_ledger")
    rng = random.Random(31)
    carts = [[], [{"price": 101, "quantity": 2}]] + [
        [{"price": rng.randrange(10000), "quantity": rng.randrange(5)} for _ in range(rng.randrange(6))]
        for _ in range(40)
    ]
    assertions = 0
    for items in carts:
        for customer in ("Guest", "Zoë\nLtd", ""):
            for action in ("total", "json", "receipt"):
                assert candidate.invoice(action, items, customer) == baseline.invoice(action, items, customer), (action, items)
                assertions += 1
            assert candidate.InvoiceManager().total(items) == baseline.InvoiceManager().total(items)
            assertions += 1
            if case in {"summary", "resume"}:
                expected = json.loads(baseline.invoice("json", items, customer))
                expected["item_count"] = sum(item["quantity"] for item in items)
                assert candidate.invoice("summary", items, customer) == expected
                assertions += 1
            elif case == "discount":
                for percent in (0, 1, 25, 99, 100):
                    subtotal = sum(item["price"] * item["quantity"] for item in items)
                    subtotal -= subtotal * percent // 100
                    discounted = [{"price": subtotal, "quantity": 1}]
                    for action in ("total", "json", "receipt"):
                        assert candidate.invoice(action, items, customer, discount_percent=percent) == baseline.invoice(action, discounted, customer)
                        assertions += 1
    for action in ("missing", None, 42):
        try:
            candidate.invoice(action, [])
        except ValueError as exc:
            assert str(exc) == "Unknown action: " + str(action)
            assertions += 1
        else:
            raise AssertionError("Unknown action accepted")
    if case == "discount":
        for value in (-1, 101, True, None, "10", 1.5):
            try:
                candidate.invoice("total", [], discount_percent=value)
            except ValueError:
                assertions += 1
            else:
                raise AssertionError(f"Invalid discount accepted: {value!r}")
    return assertions


if __name__ == "__main__":
    print(json.dumps({"case": sys.argv[2], "assertions": verify(Path(sys.argv[1]), sys.argv[2])}))
