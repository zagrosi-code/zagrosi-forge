"""Order quoting, validation, exports, persistence, and workflow management."""

import csv
import io
import json
from copy import deepcopy
from datetime import date
from pathlib import Path


STATUSES = ("draft", "confirmed", "shipped", "cancelled")
TRANSITIONS = {
    "draft": {"confirmed", "cancelled"},
    "confirmed": {"shipped", "cancelled"},
    "shipped": set(),
    "cancelled": set(),
}
SHIPPING_RATES = {"uk": (299, 10), "eu": (799, 15), "row": (1499, 25)}
CSV_FIELDS = ("id", "customer", "region", "created_on", "status", "items", "notes")


def _integer(value, name, minimum=0):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        adjective = "positive" if minimum else "non-negative"
        raise ValueError(f"{name} must be a {adjective} integer")
    return value


def validate_item(item):
    if not isinstance(item, dict):
        raise ValueError("Item must be an object")
    sku = item.get("sku")
    if not isinstance(sku, str) or not sku.strip():
        raise ValueError("Item sku is required")
    return {
        "sku": sku.strip(),
        "price": _integer(item.get("price"), "price"),
        "quantity": _integer(item.get("quantity"), "quantity", 1),
        "weight_grams": _integer(item.get("weight_grams", 0), "weight_grams"),
    }


def validate_order(order):
    if not isinstance(order, dict):
        raise ValueError("Order must be an object")
    for name in ("id", "customer"):
        if not isinstance(order.get(name), str) or not order[name].strip():
            raise ValueError(f"Order {name} is required")
    region = order.get("region", "uk")
    if not isinstance(region, str) or region not in SHIPPING_RATES:
        raise ValueError("Unknown region")
    status = order.get("status", "draft")
    if status not in STATUSES:
        raise ValueError("Unknown status")
    created_on = order.get("created_on")
    try:
        if not isinstance(created_on, str):
            raise ValueError
        normalized_date = date.fromisoformat(created_on).isoformat()
    except ValueError as exc:
        raise ValueError("created_on must be an ISO date") from exc
    if not isinstance(order.get("items"), list):
        raise ValueError("Order items must be a list")
    notes = order.get("notes", "")
    if not isinstance(notes, str):
        raise ValueError("Order notes must be text")
    return {
        "id": order["id"].strip(),
        "customer": order["customer"].strip(),
        "region": region,
        "created_on": normalized_date,
        "status": status,
        "items": [validate_item(item) for item in order["items"]],
        "notes": notes,
    }


def line_total(item):
    checked = validate_item(item)
    return checked["price"] * checked["quantity"]


def quote_shipping(items, region="uk", express=False):
    if not isinstance(region, str) or region not in SHIPPING_RATES:
        raise ValueError("Unknown region")
    if not isinstance(express, bool):
        raise ValueError("express must be bool")
    checked = [validate_item(item) for item in items]
    if not checked:
        return 0
    weight = sum(item["weight_grams"] * item["quantity"] for item in checked)
    base, increment = SHIPPING_RATES[region]
    half_kilos = (weight + 499) // 500
    return base + half_kilos * increment + (500 if express else 0)


def order_totals(order):
    checked = validate_order(order)
    subtotal = sum(line_total(item) for item in checked["items"])
    tax = subtotal * 20 // 100
    shipping = quote_shipping(checked["items"], checked["region"])
    return {"subtotal": subtotal, "tax": tax, "shipping": shipping,
            "total": subtotal + tax + shipping}


def packing_slip(order):
    checked = validate_order(order)
    lines = [f"Order: {checked['id']}", f"Customer: {checked['customer']}"]
    lines.extend(f"{item['quantity']} x {item['sku']}" for item in checked["items"])
    lines.append(f"Region: {checked['region'].upper()}")
    return "\n".join(lines) + "\n"


def order_receipt(order):
    checked = validate_order(order)
    totals = order_totals(checked)
    lines = [f"Order: {checked['id']}", f"Customer: {checked['customer']}"]
    for item in checked["items"]:
        lines.append(f"{item['quantity']} x {item['sku']}: {line_total(item)}")
    for name in ("subtotal", "tax", "shipping", "total"):
        lines.append(f"{name.title()}: {totals[name]}")
    if checked["notes"]:
        lines.append(f"Notes: {checked['notes']}")
    return "\n".join(lines) + "\n"


def order_to_json(order):
    return json.dumps(validate_order(order), sort_keys=True, ensure_ascii=False) + "\n"


def order_from_json(text):
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError("Invalid order JSON") from exc
    return validate_order(value)


def orders_to_csv(orders):
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=CSV_FIELDS, lineterminator="\n")
    writer.writeheader()
    for order in orders:
        row = validate_order(order)
        row["items"] = json.dumps(row["items"], sort_keys=True, separators=(",", ":"))
        writer.writerow(row)
    return output.getvalue()


def orders_from_csv(text):
    reader = csv.DictReader(io.StringIO(text, newline=""))
    required = {"id", "customer", "region", "created_on", "items"}
    missing = sorted(required - set(reader.fieldnames or []))
    if missing:
        raise ValueError("Missing CSV columns: " + ", ".join(missing))
    orders = []
    seen = set()
    for row_number, row in enumerate(reader, 2):
        if not any(row.values()):
            continue
        try:
            items = json.loads(row["items"])
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError(f"Invalid items JSON on row {row_number}") from exc
        order = validate_order({
            "id": row["id"], "customer": row["customer"], "region": row["region"],
            "created_on": row["created_on"], "items": items,
            "status": row.get("status") or "draft", "notes": row.get("notes") or "",
        })
        if order["id"] in seen:
            raise ValueError("Duplicate order: " + order["id"])
        seen.add(order["id"])
        orders.append(order)
    return orders


def save_order(path, order):
    destination = Path(path)
    destination.write_text(order_to_json(order), encoding="utf-8")
    return destination


def load_order(path):
    return order_from_json(Path(path).read_text(encoding="utf-8"))


class OrderBook:
    def __init__(self, orders=()):
        self._orders = {}
        for order in orders:
            self.add(order)

    def add(self, order):
        checked = validate_order(order)
        if checked["id"] in self._orders:
            raise ValueError("Duplicate order: " + checked["id"])
        self._orders[checked["id"]] = checked
        return deepcopy(checked)

    def get(self, order_id):
        if order_id not in self._orders:
            raise KeyError("Unknown order: " + str(order_id))
        return deepcopy(self._orders[order_id])

    def list(self, status=None):
        if status is not None and status not in STATUSES:
            raise ValueError("Unknown status")
        return [deepcopy(order) for _, order in sorted(self._orders.items())
                if status is None or order["status"] == status]

    def transition(self, order_id, status):
        current = self.get(order_id)
        if status not in STATUSES:
            raise ValueError("Unknown status")
        if status not in TRANSITIONS[current["status"]]:
            raise ValueError(f"Cannot move order from {current['status']} to {status}")
        current["status"] = status
        self._orders[order_id] = current
        return deepcopy(current)

    def remove(self, order_id):
        current = self.get(order_id)
        if current["status"] not in {"draft", "cancelled"}:
            raise ValueError("Only draft or cancelled orders can be removed")
        del self._orders[order_id]
        return current

    def inventory_requirements(self):
        quantities = {}
        for order in self._orders.values():
            if order["status"] == "confirmed":
                for item in order["items"]:
                    quantities[item["sku"]] = quantities.get(item["sku"], 0) + item["quantity"]
        return dict(sorted(quantities.items()))

    def outstanding_total(self):
        return sum(order_totals(order)["total"] for order in self._orders.values()
                   if order["status"] == "confirmed")

    def to_json(self):
        return json.dumps(self.list(), sort_keys=True, ensure_ascii=False) + "\n"

    @classmethod
    def from_json(cls, text):
        try:
            orders = json.loads(text)
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError("Invalid order book JSON") from exc
        if not isinstance(orders, list):
            raise ValueError("Order book JSON must contain a list")
        return cls(orders)
