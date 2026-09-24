Work only in the provided disposable workspace.

Add `summarize_orders(orders)` and `OrderBook.summary()` to the order-dispatch API.
Return a JSON-compatible dictionary with these exact keys:

- `order_count`: number of orders.
- `status_counts`: every existing status (`draft`, `confirmed`, `shipped`, `cancelled`), including zero counts.
- `confirmed_total`: sum of existing quoted totals, including tax and shipping, for confirmed orders only.
- `outstanding_skus`: sorted SKU keys mapped to summed quantities from confirmed orders only.
- `latest_created_on`: latest normalized ISO date across every order, or `None` when empty.

Accept finite iterables, including generators. Use existing validation and duplicate-ID rules.
Do not mutate inputs or expose mutable OrderBook state. Empty input returns zero counts,
zero confirmed_total, an empty outstanding_skus dictionary, and latest_created_on=None.
The method must describe the current book, including completed transitions.

`src/dispatch.py` has grown into a mixed-responsibility module: validation, monetary/shipping
calculations, rendering/CSV/JSON, file access, and workflow state. Repair those boundaries
while adding the feature. Keep every existing public import from `dispatch`, callable
signature, constant, output (including exact serialization), exception type/message,
transition rule, and copy-isolation behavior compatible. Extract cohesive implementations
into focused modules; do not simply move the entire file behind a wrapper or add speculative layers.

Add regression coverage for the existing responsibilities before refactoring. Use only
the standard library. Run existing and added tests with
`PYTHONPATH=src python3 -m unittest discover -s tests`. Report changes, tests, cleanup,
remaining issues, and observed usage if available. Do not inspect external trial checkers,
other trial outputs, or modify plugin/source infrastructure.
