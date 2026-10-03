import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from record_stream import RecordDecoder, RecordError, decode_records


class StreamBoundaryTests(unittest.TestCase):
    def test_all_splits_inside_multibyte_characters(self):
        data = '{"name":"Zoë € 🛰"}\n'.encode()
        for cut in range(len(data) + 1):
            with self.subTest(cut=cut):
                self.assertEqual(decode_records([data[:cut], data[cut:]]),
                                 [{"name": "Zoë € 🛰"}])

    def test_incomplete_utf8_fails_only_at_end_of_stream(self):
        decoder = RecordDecoder()
        self.assertEqual(decoder.feed(b'{"name":"\xe2\x82'), [])
        with self.assertRaisesRegex(RecordError, "invalid UTF-8"):
            decoder.finish()
        with self.assertRaisesRegex(RuntimeError, "decoder is closed"):
            decoder.feed(b"\xac")

    def test_invalid_chunk_type_preserves_buffered_bytes(self):
        decoder = RecordDecoder()
        self.assertEqual(decoder.feed(b'{"name":"\xc3'), [])
        with self.assertRaisesRegex(TypeError, "chunk must be bytes"):
            decoder.feed(None)
        self.assertEqual(decoder.feed(b'\xab"}\n'), [{"name": "ë"}])
        self.assertEqual(decoder.finish(), [])


if __name__ == "__main__":
    unittest.main()
