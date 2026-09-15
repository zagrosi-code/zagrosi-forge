import unittest
from ledger import invoice


class InvoiceTests(unittest.TestCase):
    def test_total(self):
        self.assertEqual(invoice("total", [{"price": 101, "quantity": 2}]), 242)

    def test_receipt(self):
        self.assertEqual(invoice("receipt", [], "Ada"),
                         "Customer: Ada\nSubtotal: 0\nTax: 0\nTotal: 0\n")
