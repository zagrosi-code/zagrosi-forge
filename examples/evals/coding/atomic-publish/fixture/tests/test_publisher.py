from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from publisher import BundlePublisher, publish_files, publish_text
from release import publish_release


class PublicationTests(unittest.TestCase):
    def test_write_and_callback(self):
        with TemporaryDirectory() as folder:
            events = []
            names = publish_files(folder, [{"name": "a", "data": b"hello"}],
                                  lambda name, path: events.append((name, path.read_bytes())))
            self.assertEqual(names, ["a"])
            self.assertEqual(events, [("a", b"hello")])

    def test_wrapper_replaces_existing(self):
        with TemporaryDirectory() as folder:
            Path(folder, "a").write_bytes(b"old")
            self.assertEqual(BundlePublisher(folder).write([{"name": "a", "data": b"new"}]), ["a"])
            self.assertEqual(Path(folder, "a").read_bytes(), b"new")

    def test_text_and_release(self):
        with TemporaryDirectory() as folder:
            self.assertEqual(publish_text(folder, [("readme", "Café")]), ["readme"])
            result = publish_release(folder, "v2", "Ready")
            self.assertEqual(result, {"version": "v2", "written": ["version.txt", "notes.txt"]})

    def test_validation_before_writes(self):
        with TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, "^Invalid document name$"):
                publish_files(folder, [{"name": "a", "data": b"new"}, {"name": "../b", "data": b"x"}])
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_duplicate_rejected(self):
        with TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, "^Duplicate document: a$"):
                publish_files(folder, [{"name": "a", "data": b"1"}, {"name": "a", "data": b"2"}])

    def test_empty_bundle(self):
        with TemporaryDirectory() as folder:
            self.assertEqual(publish_files(folder, []), [])
