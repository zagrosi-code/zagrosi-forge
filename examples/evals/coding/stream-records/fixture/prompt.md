# Decode records across arbitrary network chunks

The import service receives UTF-8 newline-delimited JSON in byte chunks. It
works for ordinary chunks but rejects valid text when a multi-byte character
crosses a chunk boundary. Fix that defect and simplify the duplicated row
validation in `src/record_stream.py` and its unnecessary batch copies. Keep
the solution small and readable, using only Python's standard library.

## Acceptance

- Preserve `RecordError(ValueError)`, `RecordDecoder()`,
  `RecordDecoder.feed(chunk)`, `RecordDecoder.finish()`, and
  `decode_records(chunks)`, including the declared `__all__` exports.
- `feed` accepts `bytes` (including byte subclasses), emits only complete
  LF-terminated JSON objects in order, and retains a partial record. An empty
  chunk is harmless. Every partition of the same valid UTF-8 byte stream must
  produce the same records, including splits inside 2-, 3-, and 4-byte
  characters. Decoding must be strict, without dropping or replacing bytes.
- `finish` flushes a final object without an LF; a trailing LF or an empty or
  whitespace-only tail produces no extra record. LF and CRLF input work.
  Blank and whitespace-only lines are ignored but count as physical lines.
- Malformed JSON raises `RecordError('line N: invalid JSON')`; JSON values
  other than objects raise `RecordError('line N: expected object')`. `N` is
  the physical line, starting at 1, including ignored blank lines. Complete
  malformed records fail during `feed`; an unterminated malformed record
  fails during `finish`.
- Invalid UTF-8 raises `RecordError('invalid UTF-8')`. An incomplete sequence
  at the end of a chunk is held for the next chunk; an incomplete sequence at
  end of stream must fail during `finish`. Immediately invalid byte sequences
  still fail in `feed`. Content errors close the decoder. Successful `finish`
  also closes it. Any later `feed` or `finish` raises
  `RuntimeError('decoder is closed')`, with that check preceding type checks.
- A non-bytes chunk raises `TypeError('chunk must be bytes')` without closing
  or discarding buffered data. Inputs are not modified. `decode_records`
  consumes its iterable once in order, stops requesting chunks at the first
  error, and propagates errors from the iterable unchanged.
- Preserve the existing `stream_report.summarize` caller and its outputs,
  including counts and first-seen kind order. Leave `src/stream_report.py`
  unchanged. Add useful regression tests for the fix and uncovered boundaries.

Run the existing tests with `python -m unittest discover -s tests`.
