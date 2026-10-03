"""Independent behavior oracle; run outside the editable trial workspace."""
from __future__ import annotations

import importlib.util
from copy import deepcopy
import inspect
import json
import random
import sys
from pathlib import Path

FIXTURE = Path(__file__).resolve().parents[1] / "examples/evals/coding/fixture/src/ledger.py"


class CountingGuard(int):
    """Legacy pricing multiplies quantities; only the new summary counts them."""

    def __radd__(self, other):
        raise AssertionError("Legacy actions must not add quantities")


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def compare(call, expected, items) -> int:
    candidate_items = deepcopy(items)
    assert call(candidate_items) == expected
    assert candidate_items == items, "Candidate mutated invoice inputs"
    return 2


def outcome(call):
    try:
        return "return", call()
    except Exception as exc:
        return "raise", type(exc), exc.args


def parameters(function, *, discount=False):
    signature = inspect.signature(function).parameters.copy()
    if discount and "discount_percent" in signature:
        added = signature.pop("discount_percent")
        assert added.kind in (added.POSITIONAL_OR_KEYWORD, added.KEYWORD_ONLY)
        assert type(added.default) is int and added.default == 0, "Discount must default to integer zero"
    return [(name, value.kind, value.default) for name, value in signature.items()]


def observed_call(function, action, items):
    events = []

    class ObservedItem(dict):
        def __getitem__(self, key):
            events.append(key)
            return super().__getitem__(key)

    def once():
        for item in items:
            events.append("next item")
            yield ObservedItem(item)

    return outcome(lambda: function(action, once())), events


def caller_contract(baseline, candidate, case):
    exports = {name for name in vars(baseline) if not name.startswith("_")}
    visible = set(getattr(candidate, "__all__", vars(candidate)))
    assert exports <= visible, "Public invoice exports narrowed"
    assert candidate.json is baseline.json, "Public imported json binding changed"
    invoice_parameters = parameters(candidate.invoice, discount=case == "discount")
    assert invoice_parameters == parameters(baseline.invoice), "Invoice signature changed"
    wrapper_parameters = parameters(candidate.InvoiceManager.total)
    assert wrapper_parameters == parameters(baseline.InvoiceManager.total), "InvoiceManager signature changed"
    assertions = 4
    items = [{"price": 101, "quantity": 2}, {"price": 3, "quantity": 3}]
    guarded_items = [{"price": 100, "quantity": CountingGuard(2)}]
    malformed = [None, [{}], [{"price": 1}], [{"quantity": 2}], [None],
                 [{"price": None, "quantity": 2}], [{"price": 1, "quantity": "two"}]]
    for action in ("total", "json", "receipt"):
        for value in malformed:
            expected = outcome(lambda: baseline.invoice(action, deepcopy(value)))
            actual = outcome(lambda: candidate.invoice(action, deepcopy(value)))
            assert actual == expected, f"Changed {action} error: {value!r}"
            assertions += 1
        for cart in (items, [{}], [items[0], {}, items[1]]):
            expected = observed_call(baseline.invoice, action, cart)
            assert observed_call(candidate.invoice, action, cart) == expected, "Input access/consumption changed"
            assertions += 1
        for cart in (items, guarded_items):
            expected = baseline.invoice(action, deepcopy(cart))
            assertions += compare(lambda value: candidate.invoice(action, value, unused_option="ignored"), expected, cart)
    for action in ("missing", None, 42, [], {}):
        expected = observed_call(baseline.invoice, action, items)
        assert observed_call(candidate.invoice, action, items) == expected, "Rejected action error or input consumption changed"
        assertions += 1
    if case in {"summary", "resume"}:
        expected = json.loads(baseline.invoice("json", items))
        expected["item_count"] = 5
        actual = candidate.invoice("summary", (deepcopy(item) for item in items))
        assert actual == expected, "Summary must support one-shot input"
        assertions += 1
    return assertions


def verify(workspace: Path, case: str) -> int:
    sys.path.insert(0, str(workspace / "src"))
    baseline = load(FIXTURE, "baseline_ledger")
    candidate = load(workspace / "src/ledger.py", "candidate_ledger")
    rng = random.Random(31)
    carts = [[], [{"price": 101, "quantity": 2}]] + [
        [{"price": rng.randrange(10000), "quantity": rng.randrange(5)} for _ in range(rng.randrange(6))]
        for _ in range(40)
    ]
    assertions = caller_contract(baseline, candidate, case)
    for items in carts:
        for customer in ("Guest", "Zoë\nLtd", ""):
            for action in ("total", "json", "receipt"):
                expected = baseline.invoice(action, deepcopy(items), customer)
                assertions += compare(lambda cart: candidate.invoice(action, cart, customer), expected, items)
            expected = baseline.InvoiceManager().total(deepcopy(items))
            assertions += compare(candidate.InvoiceManager().total, expected, items)
            if case in {"summary", "resume"}:
                expected = json.loads(baseline.invoice("json", deepcopy(items), customer))
                expected["item_count"] = sum(item["quantity"] for item in items)
                assertions += compare(lambda cart: candidate.invoice("summary", cart, customer), expected, items)
            elif case == "discount":
                for percent in (0, 1, 25, 99, 100):
                    subtotal = sum(item["price"] * item["quantity"] for item in items)
                    subtotal -= subtotal * percent // 100
                    discounted = [{"price": subtotal, "quantity": 1}]
                    for action in ("total", "json", "receipt"):
                        expected = baseline.invoice(action, deepcopy(discounted), customer)
                        assertions += compare(lambda cart: candidate.invoice(action, cart, customer, discount_percent=percent), expected, items)
    for action in ("missing", None, 42):
        items = []
        try:
            candidate.invoice(action, items)
        except ValueError as exc:
            assert str(exc) == "Unknown action: " + str(action)
            assert items == [], "Candidate mutated rejected inputs"
            assertions += 2
        else:
            raise AssertionError("Unknown action accepted")
    if case == "discount":
        for value in (-1, 101, True, None, "10", 1.5):
            items = []
            try:
                candidate.invoice("total", items, discount_percent=value)
            except ValueError:
                assert items == [], "Candidate mutated rejected inputs"
                assertions += 2
            else:
                raise AssertionError(f"Invalid discount accepted: {value!r}")
    return assertions


if __name__ == "__main__":
    print(json.dumps({"case": sys.argv[2], "assertions": verify(Path(sys.argv[1]), sys.argv[2])}))
