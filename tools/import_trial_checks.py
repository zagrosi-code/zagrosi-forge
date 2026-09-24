#!/usr/bin/env python3
"""Independent CSV import compatibility and preview oracle."""
from __future__ import annotations

from copy import deepcopy
import csv
import importlib
import inspect
from io import StringIO
import json
from pathlib import Path
import random
import sys

REFERENCE = Path(__file__).resolve().parents[1] / "examples/evals/coding/imports/src"
PUBLIC = {"pricing": ("quote",), "orders": ("parse_orders", "import_orders"),
          "receipts": ("export_receipts",)}


def outcome(call, arguments):
    try:
        return "return", call(*arguments)
    except Exception as exc:
        return "raise", type(exc).__name__, str(exc)


def csv_text(rows):
    stream = StringIO()
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(["order_id", "sku", "quantity"])
    writer.writerows(rows)
    return stream.getvalue()


def verify(workspace: Path, compatibility_only: bool = False) -> int:
    source = (workspace / "src").resolve()
    sys.path.insert(0, str(REFERENCE))
    reference = {name: importlib.import_module(name) for name in PUBLIC}
    for name in PUBLIC:
        del sys.modules[name]
    sys.path[0] = str(source)
    candidate = {name: importlib.import_module(name) for name in PUBLIC}
    assertions = 0

    def equal(actual, expected):
        nonlocal assertions
        assert actual == expected, f"Expected {expected!r}; got {actual!r}"
        assertions += 1

    def compatible(module, name, arguments):
        expected = outcome(getattr(reference[module], name), deepcopy(arguments))
        inputs = deepcopy(arguments)
        equal(outcome(getattr(candidate[module], name), inputs), expected)
        equal(inputs, arguments)

    for module, names in PUBLIC.items():
        equal(Path(candidate[module].__file__).resolve(), source / f"{module}.py")
        for name in names:
            equal(str(inspect.signature(getattr(candidate[module], name))),
                  str(inspect.signature(getattr(reference[module], name))))
    equal(candidate["orders"].FIELDS, reference["orders"].FIELDS)
    catalog = {"PEN": 101, "BOOK": 499, "Zoë,\"雪\"": 3, "FREE": 0}
    batches = [[], [(" O-1 ", " PEN ", 2)], [("O-1", "PEN", 1), ("O-1", "BOOK", 3)],
               [("B", "Zoë,\"雪\"", 1), ("A\n2", "FREE", 999), ("C", "PEN", 2)],
               [("O-1", "Zoë,\"雪\"", 1), ("O-2", "Zoë,\"雪\"", 1)]]
    rng = random.Random(83)
    for size in range(1, 21):
        batches.append([(f"O-{index // 2}", ("PEN", "BOOK")[index % 2], rng.randrange(1, 50))
                        for index in range(size)])
    valid = [csv_text(rows) for rows in batches]
    invalid = ["", "sku,order_id,quantity\nPEN,O-1,1\n", "order_id,sku,quantity\nO-1,PEN\n",
               "order_id,sku,quantity\nO-1,PEN,1,extra\n"]
    invalid += [csv_text(rows) for rows in (
        [("", "PEN", 1)], [("O-1", " ", 1)], [("O-1", "PEN", 0)],
        [("O-1", "PEN", -1)], [("O-1", "PEN", "1.5")], [("O-1", "PEN", "x")],
        [("O-1", "MISSING", 1)], [("O-1", "PEN", 1), (" O-1 ", " PEN ", 2)],
        [("O-1", "PEN", 1), ("O-2", "MISSING", 1)],
    )]
    for text in valid + invalid:
        compatible("orders", "parse_orders", (text,))
        compatible("orders", "import_orders", (text, catalog))
        for format in ("json", "text", "missing", None):
            compatible("receipts", "export_receipts", (text, catalog, format))
    for sku, quantity, prices in (
        (" PEN ", 3, catalog), ("FREE", 1, catalog), ("MISSING", 1, catalog),
        ("", 1, catalog), (None, 1, catalog), ("PEN", True, catalog),
        ("PEN", 0, catalog), ("PEN", "1", catalog), ("PEN", 1.5, catalog),
        ("PEN", 1, {"PEN": True}), ("PEN", 1, {"PEN": -1}), ("PEN", 1, {"PEN": "1"}),
    ):
        compatible("pricing", "quote", (sku, quantity, prices))
    for price in (True, -1, "1", None):
        arguments = (valid[1], {"PEN": price})
        compatible("orders", "import_orders", arguments)
        compatible("receipts", "export_receipts", arguments)
    if compatibility_only:
        return assertions

    preview = candidate["orders"].preview_import
    for text in valid:
        rows = reference["orders"].import_orders(text, deepcopy(catalog))
        quantities = {}
        for row in rows:
            quantities[row["sku"]] = quantities.get(row["sku"], 0) + row["quantity"]
        expected = {"order_count": len({row["order_id"] for row in rows}),
                    "item_count": sum(row["quantity"] for row in rows),
                    **{key: sum(row[key] for row in rows) for key in ("subtotal", "tax", "total")},
                    "sku_quantities": dict(sorted(quantities.items()))}
        prices = deepcopy(catalog)
        actual = preview(text, prices)
        equal(actual, expected)
        equal(prices, catalog)
        equal(list(actual["sku_quantities"]), sorted(quantities))
        actual["sku_quantities"]["MUTATED"] = 999
        equal(preview(text, prices), expected)
    for arguments in [(text, catalog) for text in invalid] + [(valid[1], {"PEN": price}) for price in (True, -1, "1", None)]:
        expected = outcome(reference["orders"].import_orders, deepcopy(arguments))
        inputs = deepcopy(arguments)
        equal(outcome(preview, inputs), expected)
        equal(inputs, arguments)
    return assertions


if __name__ == "__main__":
    print(json.dumps({"case": sys.argv[2], "assertions": verify(Path(sys.argv[1]), "--compatibility-only" in sys.argv)}))
