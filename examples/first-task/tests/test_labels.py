import unittest

from labels import normalize


class LabelTests(unittest.TestCase):
    def test_surrounding_whitespace_only(self):
        self.assertEqual(normalize(" \tAda  Lovelace\n"), "Ada  Lovelace")

    def test_empty_and_unchanged(self):
        for label in ("", "MiXeD", "two  spaces"):
            with self.subTest(label=label):
                self.assertEqual(normalize(label), label)

    def test_non_strings_keep_public_error(self):
        for value in (None, 0, [], object()):
            with self.subTest(value=value):
                with self.assertRaisesRegex(TypeError, "^label must be a string$"):
                    normalize(value)

    def test_non_string_methods_are_not_called(self):
        calls = []

        class StripLike:
            def strip(self):
                calls.append("strip")
                return "accepted"

        with self.assertRaises(TypeError) as error:
            normalize(StripLike())
        self.assertEqual(str(error.exception), "label must be a string")
        self.assertEqual(calls, [])
