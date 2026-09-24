Add `orders.preview_import(text, catalog)` so users can inspect a CSV order batch
before importing it. Keep the existing input format and validation/error behavior.
Return exactly:

- `order_count`: number of distinct normalized order IDs.
- `item_count`: sum of all quantities.
- `subtotal`, `tax`, `total`: sums of the existing per-line amounts. Tax still
  rounds down per line; do not round again over the combined subtotal.
- `sku_quantities`: SKU keys in sorted order mapped to their summed quantities.

A header-only batch returns zeros and an empty `sku_quantities`. Repeated order
IDs with different SKUs are valid; a repeated normalized order/SKU pair is an
error. Reject invalid CSV, quantities, unknown SKUs and invalid prices exactly as
`import_orders` does. Preserve every existing public callable signature and exact
receipt output, including Unicode and quoting. Leave inputs and the catalog
unchanged on both successful and failed calls; results must be independent copies.

Use only the standard library and run `PYTHONPATH=src python -m unittest discover -s tests`.
`src/customer_reports.py` belongs to a different feature and must stay unchanged.
