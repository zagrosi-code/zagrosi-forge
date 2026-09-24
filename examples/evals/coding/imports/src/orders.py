"""CSV order import API."""
import csv
from io import StringIO

from pricing import quote

FIELDS = ["order_id", "sku", "quantity"]


def parse_orders(text):
    reader = csv.DictReader(StringIO(text))
    if reader.fieldnames != FIELDS:
        raise ValueError("Expected columns: order_id,sku,quantity")
    rows = []
    seen = set()
    for number, row in enumerate(reader, 2):
        if None in row or any(value is None for value in row.values()):
            raise ValueError("Malformed row: " + str(number))
        order_id = row["order_id"].strip()
        sku = row["sku"].strip()
        if not order_id or not sku:
            raise ValueError("Missing order or SKU: " + str(number))
        try:
            quantity = int(row["quantity"])
        except ValueError:
            raise ValueError("Invalid quantity: " + str(number)) from None
        if quantity < 1:
            raise ValueError("Invalid quantity: " + str(number))
        if (order_id, sku) in seen:
            raise ValueError("Duplicate order item: " + order_id + "/" + sku)
        seen.add((order_id, sku))
        rows.append({"order_id": order_id, "sku": sku, "quantity": quantity})
    return rows


def import_orders(text, catalog):
    result = []
    for row in parse_orders(text):
        amounts = quote(row["sku"], row["quantity"], catalog)
        result.append({**row, **amounts})
    return result
