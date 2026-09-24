"""Legacy import receipt export."""
import csv
from io import StringIO
import json

from orders import FIELDS
from pricing import quote


def export_receipts(text, catalog, format="json"):
    if format not in ("json", "text"):
        raise ValueError("Unknown format: " + str(format))
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
    records = []
    for row in rows:
        amounts = quote(row["sku"], row["quantity"], catalog)
        records.append({**row, **amounts})
    if format == "json":
        return json.dumps(records, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "\n".join(row["order_id"] + ": " + row["sku"] + " x" + str(row["quantity"])
                     + " = " + str(row["total"]) for row in records)
