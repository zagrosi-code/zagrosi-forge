"""Held-out stream oracle: public inputs/outputs only, no candidate internals."""
from copy import deepcopy
import importlib
import json
from pathlib import Path
import sys


def verify(workspace: Path) -> int:
    sys.path.insert(0, str(workspace / "src"))
    api = importlib.import_module("record_stream")
    report = importlib.import_module("stream_report")
    assertions = 0

    def equal(actual, expected, label):
        nonlocal assertions
        assert actual == expected, f"{label}: {actual!r} != {expected!r}"
        assertions += 1

    def raises(call, error_type, message):
        nonlocal assertions
        try:
            call()
        except Exception as exc:
            assert type(exc) is error_type, f"Wrong error type: {type(exc).__name__}"
            assert str(exc) == message, f"Wrong error message: {str(exc)!r}"
        else:
            raise AssertionError(f"Expected {error_type.__name__}: {message}")
        assertions += 2

    def closed(decoder):
        raises(lambda: decoder.feed(b""), RuntimeError, "decoder is closed")
        raises(lambda: decoder.feed(None), RuntimeError, "decoder is closed")
        raises(decoder.finish, RuntimeError, "decoder is closed")

    equal(api.__all__, ["RecordError", "RecordDecoder", "decode_records"], "exports")
    equal(issubclass(api.RecordError, ValueError), True, "RecordError ancestry")
    # Acceptance: records and input ownership for every single split, single-byte
    # chunks, empty chunks, CRLF, nested values, final tails, and UTF-8 widths.
    samples = [
        (b"", []),
        (b' \r\n{"id":1}\n\t\n{"id":2}', [{"id": 1}, {"id": 2}]),
        ('{"name":"Zoë € 🛰"}\r\n{"nested":[true,null,{"a":1}]}\n'.encode(),
         [{"name": "Zoë € 🛰"}, {"nested": [True, None, {"a": 1}]}]),
        ('{"text":"escaped \\n newline","name":"東京"}\n  '.encode(),
         [{"text": "escaped \n newline", "name": "東京"}]),
    ]
    for data, expected in samples:
        partitions = [[data[:cut], b"", data[cut:]] for cut in range(len(data) + 1)]
        partitions += [[bytes([byte]) for byte in data], [data]]
        for chunks in partitions:
            before = list(chunks)
            equal(api.decode_records(chunks=chunks), expected, "chunk-independent records")
            equal(chunks, before, "input chunks unchanged")

    decoder = api.RecordDecoder()
    equal(decoder.feed(chunk=b'{"id":0}\n{"name":"\xf0'), [{"id": 0}], "emit complete prefix")
    equal(decoder.feed(b""), [], "empty chunk during UTF-8 sequence")
    equal(decoder.feed(b'\x9f\x9b\xb0"}'), [], "hold unterminated object")
    equal(decoder.finish(), [{"name": "🛰"}], "flush decoded tail")
    closed(decoder)

    class Bytes(bytes):
        pass

    equal(api.decode_records([Bytes(b'{}\n')]), [{}], "byte subclass")
    for invalid in [None, "{}", bytearray(b"{}"), memoryview(b"{}"), 3]:
        decoder = api.RecordDecoder()
        equal(decoder.feed(b'{"name":"\xc3'), [], "hold partial before bad type")
        raises(lambda: decoder.feed(invalid), TypeError, "chunk must be bytes")
        equal(decoder.feed(b'\xab"}\n'), [{"name": "ë"}], "bad type preserves pending bytes")
        equal(decoder.finish(), [], "finish after bad type recovery")

    # Acceptance: exact public errors, physical line accounting, error timing,
    # and closure after malformed complete records or final tails.
    for body, reason in [
        (b'{"a":}', "invalid JSON"), (b"oops", "invalid JSON"),
        (b"[]", "expected object"), (b"null", "expected object"),
        (b"3", "expected object"), (b"true", "expected object"),
        (b'"value"', "expected object"),
    ]:
        for terminated in [True, False]:
            decoder = api.RecordDecoder()
            equal(decoder.feed(b'\n \r\n{"ok":1}\r\n'), [{"ok": 1}], "blank physical lines")
            if terminated:
                raises(lambda: decoder.feed(body + b"\n"), api.RecordError, f"line 4: {reason}")
            else:
                equal(decoder.feed(body), [], "defer unterminated validation")
                raises(decoder.finish, api.RecordError, f"line 4: {reason}")
            closed(decoder)

    for invalid in [b"\xff", b"\x80", b"\xc0\xaf", b"\xed\xa0\x80", b"\xf4\x90\x80\x80"]:
        decoder = api.RecordDecoder()
        raises(lambda: decoder.feed(b'{"value":"' + invalid), api.RecordError, "invalid UTF-8")
        closed(decoder)
    for incomplete in [b"\xc2", b"\xe2\x82", b"\xf0\x9f\x92"]:
        decoder = api.RecordDecoder()
        equal(decoder.feed(b'{"value":"' + incomplete), [], "defer incomplete UTF-8")
        raises(decoder.finish, api.RecordError, "invalid UTF-8")
        closed(decoder)

    for tail in [b"", b"\n", b" \r\n\t", b"{}\n"]:
        decoder = api.RecordDecoder()
        equal(decoder.feed(tail), [{}] if tail == b"{}\n" else [], "empty/tail feed")
        equal(decoder.finish(), [], "no phantom final record")
        closed(decoder)

    # Acceptance: iterable consumption and caller compatibility are observable.
    seen = []

    def invalid_stream():
        for chunk in [b'{}\n', b'bad\n', b'{"never":true}\n']:
            seen.append(chunk)
            yield chunk

    raises(lambda: api.decode_records(invalid_stream()), api.RecordError, "line 2: invalid JSON")
    equal(seen, [b'{}\n', b'bad\n'], "stop consuming after content error")
    marker = RuntimeError("transport disconnected")

    def broken_stream():
        yield b'{}\n'
        raise marker

    try:
        api.decode_records(broken_stream())
    except RuntimeError as exc:
        equal(exc is marker, True, "iterable error identity")
    else:
        raise AssertionError("Transport error swallowed")

    class Once:
        def __init__(self, chunks):
            self.chunks = chunks
            self.used = False

        def __iter__(self):
            assert not self.used, "Iterable consumed twice"
            self.used = True
            yield from self.chunks

    equal(api.decode_records(Once([b'{}\n', b'{"n":2}'])), [{}, {"n": 2}], "single pass")
    data = '{"kind":"更新"}\n{}\n{"kind":"order"}\n{"kind":"更新"}'.encode()
    chunks = [data[:11], data[11:12], data[12:]]
    before = deepcopy(chunks)
    summary = report.summarize(chunks)
    equal(summary, {"records": 4, "kinds": {"更新": 2, "unknown": 1, "order": 1}}, "report output")
    equal(list(summary["kinds"]), ["更新", "unknown", "order"], "first-seen kind order")
    equal(chunks, before, "report input ownership")
    return assertions


if __name__ == "__main__":
    print(json.dumps({"case": sys.argv[2], "assertions": verify(Path(sys.argv[1]))}))
