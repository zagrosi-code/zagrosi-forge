"""Independent bounded resource reads; no process, provider or implementation inspection."""
from __future__ import annotations

import builtins
import io
import os
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from coding_trial_qualification import read_bytes
from test_trial_suite_native_runtime import forbid_credential_reads


def watch_resource_reads(monkeypatch, path, maximum, *, before_first=None, after_first=None):
    """Observe public file I/O and inject one deterministic concurrent-file change."""
    identity = path.stat()
    calls, consumed = [], [0]
    def selected(value):
        try:
            info = os.fstat(value) if isinstance(value, int) else os.stat(value)
        except (OSError, TypeError, ValueError):
            return False
        return (info.st_dev, info.st_ino) == (identity.st_dev, identity.st_ino)
    def read(operation, size):
        assert isinstance(size, int) and 0 <= size <= maximum + 1, "Resource read was unbounded"
        first = not calls
        if first and before_first is not None:
            before_first()
        calls.append(size)
        data = operation(size)
        consumed[0] += len(data)
        assert consumed[0] <= maximum + 1, "Reader consumed more than its one-byte overflow sentinel"
        if first and after_first is not None:
            after_first()
        return data
    class Observed:
        def __init__(self, stream):
            self.stream = stream
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return self.stream.__exit__(*args)
        def __getattr__(self, name):
            return getattr(self.stream, name)
        def read(self, size=-1):
            return read(self.stream.read, size)
        def read1(self, size=-1):
            return read(self.stream.read1, size)
    def opened(original):
        def wrapper(file, *args, **kwargs):
            matched = selected(file)
            stream = original(file, *args, **kwargs)
            return Observed(stream) if matched else stream
        return wrapper
    for module in (builtins, io):
        monkeypatch.setattr(module, "open", opened(module.open))
    original_read = os.read
    def descriptor_read(fd, size):
        return read(lambda count: original_read(fd, count), size) if selected(fd) else original_read(fd, size)
    monkeypatch.setattr(os, "read", descriptor_read)
    if hasattr(os, "pread"):
        original_pread = os.pread
        def positioned_read(fd, size, offset):
            return (read(lambda count: original_pread(fd, count, offset), size) if selected(fd)
                    else original_pread(fd, size, offset))
        monkeypatch.setattr(os, "pread", positioned_read)
    return calls, consumed


@pytest.mark.parametrize("raw", [b"\x00small\xff", b"\x00\xff" * 8])
def test_bounded_resource_read_returns_exact_below_and_at_limit_bytes(tmp_path, raw):
    path = tmp_path / "resource.bin"
    path.write_bytes(raw)
    assert read_bytes(tmp_path.resolve(), path.name, max_bytes=16) == raw


def test_default_and_explicit_none_keep_trusted_resource_reads_unbounded(tmp_path):
    path = tmp_path / "trusted.bin"
    raw = b"x" * 65537
    path.write_bytes(raw)
    assert read_bytes(tmp_path.resolve(), path.name) == raw
    assert read_bytes(tmp_path.resolve(), path.name, max_bytes=None) == raw


def test_oversized_resource_is_refused_before_content_io(tmp_path, monkeypatch):
    path = tmp_path / "oversized.bin"
    path.write_bytes(b"x" * 17)
    forbid_credential_reads(monkeypatch, path)  # Existing metadata-only I/O guard; synthetic bytes only.
    with pytest.raises(ValueError):
        read_bytes(tmp_path.resolve(), path.name, max_bytes=16)


def test_resource_growth_during_read_is_bounded_and_never_truncated_into_success(tmp_path, monkeypatch):
    path = tmp_path / "growing.json"
    path.write_bytes(b'{"x":1}')
    def grow():
        with path.open("ab") as stream:
            stream.write(b" " * 32)
    calls, consumed = watch_resource_reads(monkeypatch, path, 16, before_first=grow)
    with pytest.raises(ValueError):
        read_bytes(tmp_path.resolve(), path.name, max_bytes=16)
    assert calls and consumed[0] <= 17


@pytest.mark.skipif(os.name != "posix", reason="The controlled open-file pathname replacement requires POSIX")
def test_bounded_read_preserves_same_byte_path_replacement_detection(tmp_path, monkeypatch):
    path = tmp_path / "replaced.bin"
    raw = b"stable bytes"
    path.write_bytes(raw)
    def replace():
        replacement = tmp_path / "replacement.bin"
        replacement.write_bytes(raw)
        replacement.replace(path)
    calls, _ = watch_resource_reads(monkeypatch, path, 16, after_first=replace)
    with pytest.raises(ValueError):
        read_bytes(tmp_path.resolve(), path.name, max_bytes=16)
    assert calls
