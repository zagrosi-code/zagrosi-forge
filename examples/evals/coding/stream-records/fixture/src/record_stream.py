"""Decode UTF-8 newline-delimited JSON objects received from a byte stream.

Public API: RecordError, RecordDecoder(), and decode_records(chunks).
feed(chunk) accepts bytes and returns complete objects. finish() returns an
optional final object without a newline. Blank lines (including whitespace)
are ignored. Physical lines start at 1. Both methods reject a closed decoder.
Invalid byte types leave the decoder usable; malformed content closes it.
"""
import json

__all__ = ["RecordError", "RecordDecoder", "decode_records"]


class RecordError(ValueError):
    """Malformed stream content, with stable messages for callers."""


class RecordDecoder:
    def __init__(self):
        self._buffer = ""
        self._line = 0
        self._closed = False

    def feed(self, chunk):
        if self._closed:
            raise RuntimeError("decoder is closed")
        if not isinstance(chunk, bytes):
            raise TypeError("chunk must be bytes")
        try:
            self._buffer += chunk.decode("utf-8")
        except UnicodeDecodeError as exc:
            self._closed = True
            raise RecordError("invalid UTF-8") from exc
        lines = self._buffer.split("\n")
        self._buffer = lines.pop()
        records = []
        for line in lines:
            self._line += 1
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                self._closed = True
                raise RecordError(f"line {self._line}: invalid JSON") from exc
            if not isinstance(record, dict):
                self._closed = True
                raise RecordError(f"line {self._line}: expected object")
            records.append(record)
        return records

    def finish(self):
        if self._closed:
            raise RuntimeError("decoder is closed")
        self._closed = True
        line = self._buffer
        self._buffer = ""
        if not line:
            return []
        self._line += 1
        if not line.strip():
            return []
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RecordError(f"line {self._line}: invalid JSON") from exc
        if not isinstance(record, dict):
            raise RecordError(f"line {self._line}: expected object")
        return [record]


def decode_records(chunks):
    """Consume chunks once, returning objects in stream order."""
    decoder = RecordDecoder()
    records = []
    for chunk in chunks:
        batch = decoder.feed(chunk)
        for record in list(batch):
            records.append(record)
    for record in list(decoder.finish()):
        records.append(record)
    return records
