"""Detached approvals bind the extracted tests through the tool manifest."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

import pytest

from detached_test_support import (
    copy_implementation_plugin,
    file_sha256,
    implementation_source_args,
    make_detached_record_fixture,
    planning_tree_snapshot,
)
from forge_test_helpers import ROOT, run_script_raw
from runtime_support import load_runtime


@pytest.mark.parametrize("stage", ["admission", "after-setup"])
@pytest.mark.parametrize("change", ["bytes", "missing", "symlink", "hardlink"])
def test_moved_test_drift_cannot_admit_or_mutate_detached_state(tmp_path, stage, change):
    bundle = copy_implementation_plugin(tmp_path / "bundle")
    fixture = make_detached_record_fixture(tmp_path / "fixture", plugin_root=bundle)
    source_args = implementation_source_args(bundle)
    before = planning_tree_snapshot(fixture.implementation_root)
    test = bundle / "tests/test_detached_recording.py"
    if change == "bytes":
        test.write_bytes(test.read_bytes() + b"\n# changed after approval\n")
    elif change == "missing":
        test.unlink()
    elif change == "symlink":
        replacement = tmp_path / "replacement.py"
        test.rename(replacement)
        test.symlink_to(replacement)
    else:
        os.link(test, tmp_path / "alias.py")

    if stage == "admission":
        new_root = tmp_path / "new-implementation"
        args = (
            "implement-setup", "--sections-dir", str(fixture.sections),
            "--target-dir", str(fixture.target), "--implementation-root", str(new_root),
            "--admission-pinner", str(fixture.admission_pinner),
            "--expected-admission-pinner-sha256", file_sha256(fixture.admission_pinner),
            *source_args, "--flight", "off",
        )
    else:
        args = ("next-section", "--planning-dir", str(fixture.planning),
                "--implementation-root", str(fixture.implementation_root))
    result = run_script_raw(fixture.script, *args)
    assert result.returncode == 1, result.stdout
    assert json.loads(result.stdout)["error_code"] in {"implement-source-drift", "unsafe-implement-source"}
    assert planning_tree_snapshot(fixture.implementation_root) == before
    if stage == "admission":
        assert not new_root.exists()


def test_test_manifest_check_detects_stale_sources_without_writing(tmp_path):
    bundle = copy_implementation_plugin(tmp_path)
    script = bundle / "scripts/zagrosi_skills.py"
    sources = bundle / "scripts/forge/sources.py"
    test = bundle / "tests/test_detached_recording.py"
    test.write_bytes(test.read_bytes() + b"\n# changed test\n")
    original = (script.read_bytes(), sources.read_bytes())
    command = [sys.executable, str(ROOT / "tools/update_runtime_manifest.py"), "--plugin-root", str(bundle)]
    checked = subprocess.run([*command, "--check"], capture_output=True, text=True)
    assert checked.returncode == 1
    assert (script.read_bytes(), sources.read_bytes()) == original
    subprocess.run(command, check=True, capture_output=True)
    subprocess.run([*command, "--check"], check=True, capture_output=True)
    assert script.read_bytes() != original[0]
    assert sources.read_bytes() != original[1]


@pytest.mark.parametrize("name", ["../outside.py", "/outside.py", "nested/test.py", "nested\\test.py", "test.txt"])
def test_test_manifest_rejects_unsafe_names_before_opening(tmp_path, monkeypatch, name):
    bundle = copy_implementation_plugin(tmp_path)
    runtime = load_runtime(bundle / "scripts/zagrosi_skills.py")
    monkeypatch.setattr(runtime.sources, "TEST_MANIFEST", {**runtime.sources.TEST_MANIFEST, name: "0" * 64})
    with pytest.raises(runtime.models.DetachedImplementationError) as caught:
        runtime.sources.reopen_implementation_sources()
    assert caught.value.code == "unsafe-implement-source"


def test_ordinary_cli_does_not_require_the_extracted_test_sources(tmp_path):
    bundle = copy_implementation_plugin(tmp_path)
    (bundle / "tests/test_detached_recording.py").unlink()
    result = run_script_raw(bundle / "scripts/zagrosi_skills.py", "commands")
    assert result.returncode == 0, result.stdout


def test_test_directory_swap_cannot_hide_unbound_sources_and_closes_fds(tmp_path, monkeypatch):
    bundle = copy_implementation_plugin(tmp_path)
    runtime = load_runtime(bundle / "scripts/zagrosi_skills.py")
    sources = runtime.sources
    tests_dir = bundle / "tests"
    descriptors = []
    open_directory = sources._secure_io.open_directory_chain_no_follow

    def tracked_open(path):
        fd = open_directory(path)
        if path == tests_dir:
            descriptors.append(fd)
        return fd

    reopen = sources.reopen_implementation_source

    def swap_directory(source, path):
        if source == "test" and path.name == "detached_test_support.py":
            original = bundle / "original-tests"
            tests_dir.rename(original)
            shutil.copytree(original, tests_dir)
            (tests_dir / "conftest.py").write_text("# Unbound source in the replacement directory.\n")
            monkeypatch.setattr(sources, "reopen_implementation_source", reopen)
        return reopen(source, path)

    monkeypatch.setattr(sources._secure_io, "open_directory_chain_no_follow", tracked_open)
    monkeypatch.setattr(sources, "reopen_implementation_source", swap_directory)
    with pytest.raises(runtime.models.DetachedImplementationError) as caught:
        sources.reopen_implementation_sources()
    assert caught.value.code == "implement-source-changed"
    assert (tests_dir / "conftest.py").exists()
    assert descriptors
    for fd in descriptors:
        with pytest.raises(OSError):
            os.fstat(fd)
