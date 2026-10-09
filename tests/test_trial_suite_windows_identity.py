"""Public file boundaries under documented Windows stat/descriptor differences.

Metadata controls simulate CPython 3.12 Windows values over real temporary files.
No private identity helper is imported; no process or provider is used.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import coding_trial_inventory as inventory_module
import coding_trial_qualification as qualification_module


def windows_metadata(monkeypatch, path, *, descriptor_change=None, opened_change=None):
    """Model pathname birthtime, descriptor ChangeTime and filename execute bits."""
    real_stat, real_fstat = Path.stat, os.fstat
    initial = real_stat(path)
    target = initial.st_dev, initial.st_ino
    calls = []

    def projected(info, *, descriptor):
        values = {name: getattr(info, name) for name in (
            "st_dev", "st_ino", "st_mode", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns"
        )}
        values["st_birthtime_ns"] = initial.st_ctime_ns
        values["st_ctime_ns"] = initial.st_ctime_ns + (100 if descriptor else 0)
        values["st_mode"] &= ~0o111
        if not descriptor and path.suffix == ".exe":
            values["st_mode"] |= 0o111
        return values

    def path_stat(self, *args, **kwargs):
        info = real_stat(self, *args, **kwargs)
        return SimpleNamespace(**projected(info, descriptor=False)) if self == path else info

    def descriptor_stat(fd):
        info = real_fstat(fd)
        if (info.st_dev, info.st_ino) != target:
            return info
        values = projected(info, descriptor=True)
        calls.append(fd)
        if opened_change is not None:
            opened_change(values)
        if len(calls) > 1 and descriptor_change is not None:
            descriptor_change(values)
        return SimpleNamespace(**values)

    monkeypatch.setattr(Path, "stat", path_stat)
    # Isolate the platform projection to the reviewed modules. Globally changing
    # os.name would incorrectly make POSIX pathlib instantiate WindowsPath.
    projected_os = SimpleNamespace(**{**vars(os), "name": "nt", "fstat": descriptor_stat})
    monkeypatch.setattr(inventory_module, "os", projected_os)
    monkeypatch.setattr(qualification_module, "os", projected_os)
    return calls


def consume(root, name, consumer):
    if consumer == "resource":
        return qualification_module.read_bytes(root, name, max_bytes=128)
    return inventory_module.inventory(root)[name]["sha256"]


@pytest.mark.parametrize("name", ["resource.json", "reader.exe"])
@pytest.mark.parametrize("consumer", ["resource", "inventory"])
def test_unchanged_windows_file_accepts_documented_path_descriptor_differences(
        tmp_path, monkeypatch, name, consumer):
    root = tmp_path.resolve()
    raw = b"stable bytes\x00\xff"
    path = root / name
    path.write_bytes(raw)
    calls = windows_metadata(monkeypatch, path)
    result = consume(root, name, consumer)
    assert result == (raw if consumer == "resource" else hashlib.sha256(raw).hexdigest())
    assert len(calls) >= 2


@pytest.mark.parametrize("consumer", ["resource", "inventory"])
def test_windows_descriptor_change_time_is_still_checked_during_read(tmp_path, monkeypatch, consumer):
    root = tmp_path.resolve()
    path = root / "resource.bin"
    path.write_bytes(b"stable bytes")
    calls = windows_metadata(monkeypatch, path,
        descriptor_change=lambda values: values.update(st_ctime_ns=values["st_ctime_ns"] + 1))
    with pytest.raises(ValueError):
        consume(root, path.name, consumer)
    assert len(calls) >= 2, "Reject the observed change after accepting the initial compatible descriptor"


@pytest.mark.parametrize("change", ["inode", "hardlink", "readonly"])
@pytest.mark.parametrize("consumer", ["resource", "inventory"])
def test_windows_bridge_still_rejects_real_identity_or_permission_changes(
        tmp_path, monkeypatch, consumer, change):
    root = tmp_path.resolve()
    path = root / "resource.exe"
    path.write_bytes(b"stable bytes")
    def mutate(values):
        if change == "inode":
            values["st_ino"] += 1
        elif change == "hardlink":
            values["st_nlink"] = 2
        else:
            values["st_mode"] = stat.S_IFREG | 0o444
    windows_metadata(monkeypatch, path, opened_change=mutate)
    with pytest.raises(ValueError):
        consume(root, path.name, consumer)
