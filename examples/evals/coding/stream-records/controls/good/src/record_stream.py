"""Incremental UTF-8 newline-delimited JSON object decoder."""
import codecs
import json

__all__ = ["RecordError", "RecordDecoder", "decode_records"]


class RecordError(ValueError):
    """Malformed stream content, with stable messages for callers."""


class RecordDecoder:
    def __init__(self):
        self._buffer = ""
        self._line = 0
        self._closed = False
        self._utf8 = codecs.getincrementaldecoder("utf-8")()

    def _ensure_open(self):
        if self._closed:
            raise RuntimeError("decoder is closed")

    def _decode(self, chunk, final=False):
        try:
            return self._utf8.decode(chunk, final=final)
        except UnicodeDecodeError as exc:
            self._closed = True
            raise RecordError("invalid UTF-8") from exc

    def _record(self, line):
        self._line += 1
        if not line.strip():
            return []
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            self._closed = True
            raise RecordError(f"line {self._line}: invalid JSON") from exc
        if not isinstance(record, dict):
            self._closed = True
            raise RecordError(f"line {self._line}: expected object")
        return [record]

    def feed(self, chunk):
        self._ensure_open()
        if not isinstance(chunk, bytes):
            raise TypeError("chunk must be bytes")
        self._buffer += self._decode(chunk)
        lines = self._buffer.split("\n")
        self._buffer = lines.pop()
        records = []
        for line in lines:
            records.extend(self._record(line))
        return records

    def finish(self):
        self._ensure_open()
        self._closed = True
        self._buffer += self._decode(b"", final=True)
        tail, self._buffer = self._buffer, ""
        return self._record(tail) if tail else []


def decode_records(chunks):
    """Consume chunks once, returning objects in stream order."""
    decoder = RecordDecoder()
    records = []
    for chunk in chunks:
        records.extend(decoder.feed(chunk))
    records.extend(decoder.finish())
    return records
