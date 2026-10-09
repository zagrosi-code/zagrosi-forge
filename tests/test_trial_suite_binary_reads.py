"""Portable byte-preservation controls; no newline/EOF normalization is allowed."""
from pathlib import Path
import hashlib
import stat
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from coding_trial_inventory import copy_snapshot, inventory
from coding_trial_qualification import read_bytes


RAW = b"first\r\nsecond\x1aafter-eof\x00tail\r\n"


def test_inventory_and_snapshot_keep_crlf_control_z_and_nul_bytes(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    path = source / "payload.bin"
    path.write_bytes(RAW)
    expected = {"payload.bin": {"type": "file", "mode": stat.S_IMODE(path.stat().st_mode),
                                "sha256": hashlib.sha256(RAW).hexdigest()}}
    assert path.read_bytes() == RAW
    assert inventory(source) == expected
    copied = tmp_path / "copied"
    copy_snapshot(source, copied, expected)
    assert (copied / "payload.bin").read_bytes() == RAW
    assert inventory(copied) == expected
    assert path.read_bytes() == RAW


@pytest.mark.parametrize("max_bytes", [None, len(RAW)])
def test_resource_reader_keeps_literal_binary_bytes_at_its_exact_bound(tmp_path, max_bytes):
    path = tmp_path / "payload.bin"
    path.write_bytes(RAW)
    actual = read_bytes(tmp_path.resolve(), path.name, max_bytes=max_bytes)
    assert actual == RAW
    assert hashlib.sha256(actual).hexdigest() == hashlib.sha256(path.read_bytes()).hexdigest()
    assert path.read_bytes() == RAW
