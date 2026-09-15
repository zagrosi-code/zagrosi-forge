import unittest

from dispatch import OrderBook, order_totals, orders_from_csv, orders_to_csv


def order():
    return {
        "id": "O-1", "customer": "Ada", "created_on": "2026-02-01",
        "items": [{"sku": "PEN", "price": 125, "quantity": 2, "weight_grams": 50}],
    }


class DispatchTests(unittest.TestCase):
    def test_quote(self):
        self.assertEqual(order_totals(order()),
                         {"subtotal": 250, "tax": 50, "shipping": 309, "total": 609})

    def test_csv_round_trip(self):
        restored = orders_from_csv(orders_to_csv([order()]))
        self.assertEqual(restored[0]["items"], order()["items"])
        self.assertEqual(restored[0]["status"], "draft")

    def test_order_transitions(self):
        book = OrderBook([order()])
        self.assertEqual(book.transition("O-1", "confirmed")["status"], "confirmed")
        self.assertEqual(book.inventory_requirements(), {"PEN": 2})
        self.assertEqual(book.outstanding_total(), 609)
