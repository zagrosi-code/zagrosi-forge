#!/usr/bin/env python3
"""Independent compatibility and behavior checks for the dispatch coding trial."""

from __future__ import annotations

import argparse
from copy import deepcopy
import importlib
import importlib.util
import inspect
import json
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "examples/evals/coding/godfile/src/dispatch.py"
FUNCTIONS = (
    "validate_item", "validate_order", "line_total", "quote_shipping", "order_totals",
    "packing_slip", "order_receipt", "order_to_json", "order_from_json",
    "orders_to_csv", "orders_from_csv", "save_order", "load_order",
)
METHODS = ("add", "get", "list", "transition", "remove", "inventory_requirements",
           "outstanding_total", "to_json", "from_json")
ORDER = {
    "id": "O-1", "customer": "Ada", "region": "uk", "created_on": "2026-02-01",
    "status": "draft", "notes": "Leave by desk",
    "items": [{"sku": "PEN", "price": 125, "quantity": 2, "weight_grams": 50},
              {"sku": "BOOK", "price": 499, "quantity": 1, "weight_grams": 200}],
}


def exception_signature(call):
    try:
        call()
    except Exception as exc:
        return type(exc).__name__, str(exc)
    raise AssertionError("Expected an exception")


def book_scenario(module):
    original = deepcopy(ORDER)
    book = module.OrderBook([original])
    original["items"][0]["quantity"] = 99
    copy = book.get("O-1")
    copy["items"][0]["quantity"] = 88
    outputs = [book.list()]
    outputs.append(book.transition("O-1", "confirmed"))
    outputs.extend([book.list("confirmed"), book.inventory_requirements(), book.outstanding_total()])
    outputs.append(book.to_json())
    restored = module.OrderBook.from_json(book.to_json())
    outputs.append(restored.list())
    second = dict(deepcopy(ORDER), id="A-0", customer="Béa", notes="Line 1\nLine 2")
    returned = restored.add(second)
    returned["items"].clear()
    outputs.append(restored.list())
    outputs.append(restored.transition("A-0", "cancelled"))
    outputs.append(restored.remove("A-0"))
    outputs.append(restored.list())
    outputs.append(exception_signature(lambda: restored.get("A-0")))
    outputs.append(exception_signature(lambda: restored.remove("O-1")))
    outputs.append(restored.transition("O-1", "shipped"))
    outputs.append(exception_signature(lambda: restored.transition("O-1", "cancelled")))
    return outputs


def check_workspace(workspace: Path, compatibility_only: bool = False) -> int:
    sys.dont_write_bytecode = True
    source = (workspace / "src").resolve()
    if source == REFERENCE.parent.resolve():
        assert compatibility_only, "Full trials must use a disposable candidate workspace"
    spec = importlib.util.spec_from_file_location("godfile_reference", REFERENCE)
    reference = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reference)
    sys.path.insert(0, str(source))
    candidate = importlib.import_module("dispatch")
    assert Path(candidate.__file__).resolve() == source / "dispatch.py"
    assertions = 0

    def equal(actual, expected):
        nonlocal assertions
        assert actual == expected, f"Expected {expected!r}; got {actual!r}"
        assertions += 1

    for name in ("Path", "csv", "date", "deepcopy", "io", "json"):
        assert getattr(candidate, name, None) is getattr(reference, name), f"Public import changed or missing: {name}"
        assertions += 1
    for name in ("STATUSES", "TRANSITIONS", "SHIPPING_RATES", "CSV_FIELDS"):
        equal(getattr(candidate, name), getattr(reference, name))
    for name in FUNCTIONS:
        equal(str(inspect.signature(getattr(candidate, name))), str(inspect.signature(getattr(reference, name))))
    equal(str(inspect.signature(candidate.OrderBook)), str(inspect.signature(reference.OrderBook)))
    for name in METHODS:
        equal(str(inspect.signature(getattr(candidate.OrderBook, name))), str(inspect.signature(getattr(reference.OrderBook, name))))

    untrimmed = dict(deepcopy(ORDER), id=" O-1 ", customer=" Ada ")
    equal(candidate.validate_order(untrimmed), reference.validate_order(untrimmed))
    equal(candidate.validate_item({"sku": " PEN ", "price": 1, "quantity": 1}),
          {"sku": "PEN", "price": 1, "quantity": 1, "weight_grams": 0})
    equal(candidate.line_total(ORDER["items"][0]), 250)
    equal(candidate.order_totals(ORDER), {"subtotal": 749, "tax": 149, "shipping": 309, "total": 1207})
    for region, express in (("uk", False), ("uk", True), ("eu", False), ("row", True)):
        equal(candidate.quote_shipping(iter(ORDER["items"]), region, express),
              reference.quote_shipping(iter(ORDER["items"]), region, express))
    equal(candidate.quote_shipping([], express=True), 0)
    for name in ("packing_slip", "order_receipt", "order_to_json"):
        equal(getattr(candidate, name)(ORDER), getattr(reference, name)(ORDER))
    encoded = reference.order_to_json(ORDER)
    equal(candidate.order_from_json(encoded), reference.validate_order(ORDER))
    csv_orders = [ORDER, dict(deepcopy(ORDER), id="O-2", customer='Béa, "B"', notes="First\nSecond")]
    csv_text = reference.orders_to_csv(csv_orders)
    equal(candidate.orders_to_csv(iter(csv_orders)), csv_text)
    equal(candidate.orders_from_csv(csv_text), reference.orders_from_csv(csv_text))
    equal(book_scenario(candidate), book_scenario(reference))

    error_cases = (
        lambda m: m.validate_item({"sku": "PEN", "price": True, "quantity": 1}),
        lambda m: m.validate_item({"sku": "PEN", "price": 1, "quantity": 0}),
        lambda m: m.validate_order(dict(ORDER, customer=" ")),
        lambda m: m.validate_order(dict(ORDER, created_on="invalid")),
        lambda m: m.validate_order(dict(ORDER, items=None)),
        lambda m: m.quote_shipping([], "moon"),
        lambda m: m.quote_shipping([], express=1),
        lambda m: m.order_from_json("{"),
        lambda m: m.order_from_json("[]"),
        lambda m: m.orders_from_csv("id,customer\nO-1,Ada\n"),
        lambda m: m.orders_from_csv(reference.orders_to_csv([ORDER, ORDER])),
        lambda m: m.OrderBook([ORDER, ORDER]),
        lambda m: m.OrderBook().get("missing"),
        lambda m: m.OrderBook().list("missing"),
        lambda m: m.OrderBook.from_json("{}"),
        lambda m: m.OrderBook.from_json("{"),
    )
    for operation in error_cases:
        equal(exception_signature(lambda: operation(candidate)), exception_signature(lambda: operation(reference)))
    with tempfile.TemporaryDirectory(prefix=".godfile-oracle-", dir=workspace) as temporary:
        actual_path = Path(temporary) / "actual.json"
        expected_path = Path(temporary) / "expected.json"
        equal(candidate.save_order(actual_path, ORDER), actual_path)
        reference.save_order(expected_path, ORDER)
        equal(actual_path.read_bytes(), expected_path.read_bytes())
        equal(candidate.load_order(actual_path), reference.load_order(expected_path))
        missing = Path(temporary) / "missing.json"
        equal(exception_signature(lambda: candidate.load_order(missing)),
              exception_signature(lambda: reference.load_order(missing)))

    if compatibility_only:
        return assertions

    orders = [deepcopy(ORDER)]
    for order_id, status, created_on, region, quantity in (
        ("O-2", "confirmed", "2026-02-03", "eu", 1),
        ("O-3", "confirmed", "2026-02-02", "uk", 3),
        ("O-4", "shipped", "2026-02-04", "row", 2),
        ("O-5", "cancelled", "2026-02-05", "uk", 2),
    ):
        order = dict(deepcopy(ORDER), id=order_id, status=status, created_on=created_on, region=region)
        order["items"] = [{"sku": "PEN", "price": 125, "quantity": quantity, "weight_grams": 50}]
        orders.append(order)
    untouched = deepcopy(orders)
    expected = {"order_count": 5, "status_counts": {"draft": 1, "confirmed": 2, "shipped": 1, "cancelled": 1},
                "confirmed_total": 1723, "outstanding_skus": {"PEN": 4}, "latest_created_on": "2026-02-05"}
    equal(candidate.summarize_orders(iter(orders)), expected)
    equal(orders, untouched)
    book = candidate.OrderBook(orders)
    equal(book.summary(), expected)
    report = book.summary()
    report["status_counts"]["confirmed"] = 999
    report["outstanding_skus"]["PEN"] = 999
    equal(book.summary(), expected)
    book.transition("O-1", "confirmed")
    changed = {"order_count": 5, "status_counts": {"draft": 0, "confirmed": 3, "shipped": 1, "cancelled": 1},
               "confirmed_total": 2930, "outstanding_skus": {"BOOK": 1, "PEN": 6}, "latest_created_on": "2026-02-05"}
    equal(book.summary(), changed)
    equal(list(book.summary()["outstanding_skus"]), ["BOOK", "PEN"])
    empty = {"order_count": 0, "status_counts": {"draft": 0, "confirmed": 0, "shipped": 0, "cancelled": 0},
             "confirmed_total": 0, "outstanding_skus": {}, "latest_created_on": None}
    equal(candidate.summarize_orders(iter(())), empty)
    equal(candidate.OrderBook().summary(), empty)
    equal(exception_signature(lambda: candidate.summarize_orders([ORDER, ORDER])),
          ("ValueError", "Duplicate order: O-1"))
    equal(exception_signature(lambda: candidate.summarize_orders([dict(ORDER, items=None)])),
          ("ValueError", "Order items must be a list"))

    # Check actual implementation locations, not extra empty files or a renamed godfile.
    responsibilities = (candidate.validate_order, candidate.order_totals,
                        candidate.orders_from_csv, candidate.order_receipt, candidate.OrderBook)
    locations = {Path(inspect.getsourcefile(value)).resolve() for value in responsibilities}
    equal(all(path.is_relative_to(source) for path in locations), True)
    equal(len(locations) >= 3, True)
    return assertions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path)
    parser.add_argument("case", choices=["godfile"])
    parser.add_argument("--compatibility-only", action="store_true")
    args = parser.parse_args()
    assertions = check_workspace(args.workspace.resolve(), args.compatibility_only)
    print(json.dumps({"case": args.case, "assertions": assertions}, sort_keys=True))


if __name__ == "__main__":
    main()
