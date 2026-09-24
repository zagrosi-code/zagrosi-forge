"""Integer-cent prices used by CSV imports and receipts."""


def quote(sku, quantity, catalog):
    if not isinstance(sku, str) or not sku.strip():
        raise ValueError("SKU must not be empty")
    sku = sku.strip()
    if type(quantity) is not int or quantity < 1:
        raise ValueError("Quantity must be a positive integer")
    if sku not in catalog:
        raise KeyError("Unknown SKU: " + sku)
    price = catalog[sku]
    if type(price) is not int or price < 0:
        raise ValueError("Invalid price: " + sku)
    subtotal = price * quantity
    tax = subtotal * 20 // 100
    return {"subtotal": subtotal, "tax": tax, "total": subtotal + tax}
