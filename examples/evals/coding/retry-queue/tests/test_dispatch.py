import unittest
from dispatch import dispatch_batch


class DispatchTests(unittest.TestCase):
    def test_delivery_and_duplicate(self):
        event = {"tenant": "acme", "id": "42", "topic": "invoice", "payload": {"total": 100}}
        sent, completed, audit = [], set(), []
        self.assertEqual(dispatch_batch([event, event], lambda *args: sent.append(args), completed, audit),
                         {"delivered": 1, "duplicates": 1})
        self.assertEqual(sent, [("acme", "invoice", {"total": 100})])
        self.assertEqual(audit, [{"tenant": "acme", "id": "42", "topic": "invoice"}])

    def test_validation_happens_before_delivery(self):
        sent, completed, audit = [], set(), []
        with self.assertRaisesRegex(ValueError, "Invalid tenant"):
            dispatch_batch([{"tenant": ""}], lambda *args: sent.append(args), completed, audit)
        self.assertEqual((sent, completed, audit), ([], set(), []))
