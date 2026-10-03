import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from record_stream import RecordDecoder, RecordError, decode_records
from stream_report import summarize


class RecordTests(unittest.TestCase):
    def test_complete_and_unterminated_records(self):
        self.assertEqual(decode_records([b'{"id":1}\n', b'{"id":2}']),
                         [{"id": 1}, {"id": 2}])

    def test_ascii_chunks_and_blank_lines(self):
        decoder = RecordDecoder()
        self.assertEqual(decoder.feed(b' \r\n{"i'), [])
        self.assertEqual(decoder.feed(b'd":3}\n'), [{"id": 3}])
        self.assertEqual(decoder.finish(), [])

    def test_whole_unicode_record(self):
        self.assertEqual(decode_records(['{"name":"Zoë"}\n'.encode()]),
                         [{"name": "Zoë"}])

    def test_bad_record_closes_decoder(self):
        decoder = RecordDecoder()
        with self.assertRaisesRegex(RecordError, "line 2: expected object"):
            decoder.feed(b'\n[]\n')
        with self.assertRaisesRegex(RuntimeError, "decoder is closed"):
            decoder.finish()

    def test_reporting_caller(self):
        self.assertEqual(summarize([b'{"kind":"order"}\n{}\n']),
                         {"records": 2, "kinds": {"order": 1, "unknown": 1}})


if __name__ == "__main__":
    unittest.main()
