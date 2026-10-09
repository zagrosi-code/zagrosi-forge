"""Independent pre-extension behavior; intentionally excludes new selected_entry.

Frozen preservation only. The parent captures this alongside unchanged existing
manifest/native/preparation tests before the additive entry-selection source edit.
Static native records are fictional and never qualify a live host.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from coding_trial_manifest import read_suite, validate_suite
from test_trial_suite_native_runtime import make_native, read
from trial_suite_fixtures import digest, make_suite


@pytest.fixture(autouse=True)
def forbid_processes(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Entry-preservation characterizations must not start a process")
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)


def test_omitted_entry_preserves_complete_canonical_manifest(tmp_path):
    path, suite = make_suite(tmp_path / "curator")
    before, raw = deepcopy(suite), path.read_bytes()
    expected = digest(before)
    validated = validate_suite(suite, path.parent)
    loaded = read_suite(path)
    assert validated == loaded == before
    assert digest(validated) == digest(loaded) == expected
    assert all(set(arm["loading"]) == {"adapter", "receipt"} for arm in validated["arms"].values())
    assert suite == before and path.read_bytes() == raw


@pytest.mark.parametrize("unknown", ["unexpected", "selected_entries"])
def test_other_unknown_loading_keys_remain_invalid_without_mutation(tmp_path, unknown):
    path, suite = make_suite(tmp_path / "curator")
    suite["arms"]["bravo"]["loading"][unknown] = "SKILL.md"
    before, raw = deepcopy(suite), path.read_bytes()
    with pytest.raises(ValueError, match="^suite-invalid:"):
        validate_suite(suite, path.parent)
    assert suite == before and path.read_bytes() == raw


@pytest.mark.parametrize("plain", [False, True])
def test_omitted_entry_keeps_existing_prepared_native_callers(tmp_path, plain):
    fixture = make_native(tmp_path, plain=plain)
    before = deepcopy(fixture["suite"])
    assert "selected_entry" not in before["arms"][fixture["arm"]]["loading"]
    observed = read(fixture)
    assert observed == fixture["runtime"]
    assert (observed["selected_entry"] is None) == plain
    assert fixture["suite"] == before
