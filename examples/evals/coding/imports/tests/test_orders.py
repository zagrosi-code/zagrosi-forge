import unittest

from orders import import_orders
from receipts import export_receipts


class OrderTests(unittest.TestCase):
    def test_one_order(self):
        rows = import_orders("order_id,sku,quantity\nO-1,PEN,2\n", {"PEN": 100})
        self.assertEqual(rows[0]["total"], 240)

    def test_text_receipt(self):
        self.assertEqual(export_receipts("order_id,sku,quantity\nO-1,PEN,2\n", {"PEN": 100}, "text"),
                         "O-1: PEN x2 = 240")
