"""Existing export API; item prices and totals use integer cents."""
import json


def invoice(action, items, customer="Guest", **options):
    if action == "total":
        subtotal = 0
        for item in items:
            subtotal = subtotal + item["price"] * item["quantity"]
        tax = subtotal * 20 // 100
        return subtotal + tax
    elif action == "json":
        value = 0
        for item in items:
            value = value + item["price"] * item["quantity"]
        amount = value * 20 // 100
        return json.dumps({"customer": customer, "subtotal": value,
                           "tax": amount, "total": value + amount}, sort_keys=True)
    elif action == "receipt":
        v = 0
        for item in items:
            v = v + item["price"] * item["quantity"]
        t = v * 20 // 100
        answer = "Customer: " + str(customer) + "\n"
        answer = answer + "Subtotal: " + str(v) + "\n"
        answer = answer + "Tax: " + str(t) + "\n"
        answer = answer + "Total: " + str(v + t) + "\n"
        return answer
    else:
        raise ValueError("Unknown action: " + str(action))


class InvoiceManager:
    """Unused historical wrapper."""
    def total(self, items):
        return invoice("total", items)
