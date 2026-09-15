from __future__ import annotations

import errno
import json
import os
import shutil
import subprocess
import sys
import time
from contextlib import ExitStack
from pathlib import Path

import pytest
from detached_test_support import (
    assert_canonical_json_file,
    copy_implementation_plugin,
    detached_record_arguments,
    file_sha256,
    implementation_source_args,
    make_detached_record_fixture,
    write_test_admission_pinner,
)
from fault_injection_support import (
    instrument_record_crashpoints,
    instrument_root_lifecycle_points,
)
from forge_test_helpers import (
    load_zagrosi_module,
    write_single_section_fixture,
)


def test_global_anchor_serializes_concurrent_setup_processes(tmp_path: Path) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / "setup-global-contention")
    script = instrument_root_lifecycle_points(plugin_root)
    planning = tmp_path / "planning"
    sections = write_single_section_fixture(planning)
    target = tmp_path / "target"
    target.mkdir()
    implementation_root = tmp_path / "detached-implementation"
    admission_pinner = write_test_admission_pinner(
        tmp_path / "admission-pinner.json",
        planning_dir=planning,
    )
    arguments = [
        sys.executable,
        str(script),
        "implement-setup",
        "--sections-dir",
        str(sections),
        "--target-dir",
        str(target),
        "--implementation-root",
        str(implementation_root),
        "--admission-pinner",
        str(admission_pinner),
        "--expected-admission-pinner-sha256",
        file_sha256(admission_pinner),
        *implementation_source_args(plugin_root),
        "--flight",
        "off",
    ]
    ready = tmp_path / "setup-global-ready"
    release = tmp_path / "setup-global-release"
    environment = dict(os.environ)
    environment.update(
        {
            "ZAGROSI_TEST_ROOT_PAUSEPOINT": "setup-global-acquired",
            "ZAGROSI_TEST_ROOT_READY": str(ready),
            "ZAGROSI_TEST_ROOT_RELEASE": str(release),
        }
    )
    first = subprocess.Popen(
        arguments,
        cwd=plugin_root,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + 5.0
    while not ready.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert ready.exists()
    second = subprocess.Popen(
        arguments,
        cwd=plugin_root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    time.sleep(0.2)
    assert second.poll() is None
    release.write_text("release\n")
    first_stdout, first_stderr = first.communicate(timeout=10)
    second_stdout, second_stderr = second.communicate(timeout=10)
    assert first.returncode == 0, first_stderr + first_stdout
    assert second.returncode == 0, second_stderr + second_stdout
    assert json.loads(first_stdout)["planning_tree_sha256"] == json.loads(second_stdout)["planning_tree_sha256"]


def test_u_root_flock_hides_candidate_window_from_concurrent_reader(tmp_path: Path) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / "concurrent-candidate")
    script = instrument_record_crashpoints(plugin_root)
    fixture = make_detached_record_fixture(tmp_path / "fixture-concurrent", plugin_root=plugin_root)
    ready = tmp_path / "candidate-ready"
    release = tmp_path / "candidate-release"
    environment = dict(os.environ)
    environment.update(
        {
            "ZAGROSI_TEST_RECORD_PAUSEPOINT": "state-cas-fsync",
            "ZAGROSI_TEST_RECORD_READY": str(ready),
            "ZAGROSI_TEST_RECORD_RELEASE": str(release),
        }
    )
    writer = subprocess.Popen(
        [sys.executable, str(script), *detached_record_arguments(fixture)],
        cwd=plugin_root,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + 5.0
    while not ready.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert ready.exists()
    reader = subprocess.Popen(
        [
            sys.executable,
            str(script),
            "next-section",
            "--planning-dir",
            str(fixture.planning),
            "--implementation-root",
            str(fixture.implementation_root),
        ],
        cwd=plugin_root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    time.sleep(0.2)
    assert reader.poll() is None
    release.write_text("release\n")
    writer_stdout, writer_stderr = writer.communicate(timeout=10)
    reader_stdout, reader_stderr = reader.communicate(timeout=10)
    assert writer.returncode == 0, writer_stderr + writer_stdout
    assert reader.returncode == 0, reader_stderr + reader_stdout
    assert json.loads(reader_stdout)["completed_sections"] == [fixture.section]


def test_late_commit_reopens_staged_and_final_pinner_before_journal_removal(tmp_path: Path) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / "late-pinner-reopen")
    script = instrument_record_crashpoints(plugin_root)
    fixture = make_detached_record_fixture(tmp_path / "fixture-late-pinner-reopen", plugin_root=plugin_root)
    ready = tmp_path / "late-pinner-ready"
    release = tmp_path / "late-pinner-release"
    environment = dict(os.environ)
    environment.update(
        {
            "ZAGROSI_TEST_RECORD_PAUSEPOINT": "post-state-validation",
            "ZAGROSI_TEST_RECORD_READY": str(ready),
            "ZAGROSI_TEST_RECORD_RELEASE": str(release),
        }
    )
    writer = subprocess.Popen(
        [sys.executable, str(script), *detached_record_arguments(fixture)],
        cwd=plugin_root,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + 5.0
    while not ready.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert ready.exists()
    transaction_dir = fixture.implementation_root / "pinners" / ".record-section-transaction-v1"
    transaction = assert_canonical_json_file(transaction_dir / "transaction.json")
    final_path = fixture.implementation_root / transaction["pinner_path"]
    assert final_path.stat().st_ino == (transaction_dir / "pinner.json").stat().st_ino
    final_path.write_bytes(b'{"schema":"mutated-after-validation"}\n')
    release.write_text("release\n")

    writer_stdout, writer_stderr = writer.communicate(timeout=10)
    assert writer.returncode == 1, writer_stderr + writer_stdout
    assert json.loads(writer_stdout)["error_code"] == "section-record-recovery-required"
    assert (transaction_dir / "transaction.json").is_file()
    state = assert_canonical_json_file(fixture.implementation_root / "zagrosi_implement_state.json")
    assert fixture.section in state["completed_sections"]


def test_global_anchor_blocks_replacement_u_contender_until_original_holder_closes(tmp_path: Path) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / "global-u-replacement")
    instrument_record_crashpoints(plugin_root)
    script = instrument_root_lifecycle_points(plugin_root)
    fixture = make_detached_record_fixture(tmp_path / "fixture-global-u-replacement", plugin_root=plugin_root)
    ready = tmp_path / "replacement-ready"
    release = tmp_path / "replacement-release"
    environment = dict(os.environ)
    environment.update(
        {
            "ZAGROSI_TEST_RECORD_PAUSEPOINT": "state-cas-fsync",
            "ZAGROSI_TEST_RECORD_READY": str(ready),
            "ZAGROSI_TEST_RECORD_RELEASE": str(release),
        }
    )
    writer = subprocess.Popen(
        [sys.executable, str(script), *detached_record_arguments(fixture)],
        cwd=plugin_root,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + 5.0
    while not ready.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert ready.exists()

    displaced = tmp_path / "displaced-implementation-root"
    replacement = fixture.implementation_root
    replacement.rename(displaced)
    replacement.mkdir(mode=0o700)
    reader = subprocess.Popen(
        [
            sys.executable,
            str(script),
            "next-section",
            "--planning-dir",
            str(fixture.planning),
            "--implementation-root",
            str(replacement),
        ],
        cwd=plugin_root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    time.sleep(0.2)
    assert reader.poll() is None
    replacement.rmdir()
    displaced.rename(replacement)
    release.write_text("release\n")

    writer_stdout, writer_stderr = writer.communicate(timeout=10)
    reader_stdout, reader_stderr = reader.communicate(timeout=10)
    assert writer.returncode == 0, writer_stderr + writer_stdout
    assert reader.returncode == 0, reader_stderr + reader_stdout
    assert json.loads(reader_stdout)["completed_sections"] == [fixture.section]


def test_global_and_u_lock_fds_are_noninheritable_closed_and_reverse_released(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = load_zagrosi_module()
    root = tmp_path / "detached-root"
    (root / "pinners").mkdir(parents=True, mode=0o700)
    marker = root / "pinners" / ".record-section.lock"
    marker.write_bytes(b"")
    marker.chmod(0o600)
    root_fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    real_flock = module.locks.fcntl.flock
    operations: list[tuple[int, int]] = []

    def tracked_flock(file_fd: int, operation: int) -> None:
        operations.append((file_fd, operation))
        real_flock(file_fd, operation)

    monkeypatch.setattr(module.locks.fcntl, "flock", tracked_flock)
    locked_fds: list[int] = []
    try:
        with ExitStack() as stack:
            stack.enter_context(module.locks.detached_global_lock(time.monotonic() + 2.0))
            stack.enter_context(module.locks.section_record_lock(root_fd, root))
            locked_fds = [file_fd for file_fd, operation in operations if operation & module.locks.fcntl.LOCK_EX]
            assert len(locked_fds) == 2
            assert all(os.get_inheritable(file_fd) is False for file_fd in locked_fds)
        unlock_fds = [file_fd for file_fd, operation in operations if operation == module.locks.fcntl.LOCK_UN]
        assert unlock_fds == list(reversed(locked_fds))
        for file_fd in locked_fds:
            with pytest.raises(OSError):
                os.fstat(file_fd)
    finally:
        os.close(root_fd)


def test_global_anchor_unsupported_flock_fails_closed_and_closes_fd(monkeypatch) -> None:
    module = load_zagrosi_module()
    real_open = os.open
    opened: list[int] = []

    def tracked_open(path, flags, *args, **kwargs):
        file_fd = real_open(path, flags, *args, **kwargs)
        if os.fspath(path) == os.sep:
            opened.append(file_fd)
        return file_fd

    def unsupported_flock(file_fd: int, operation: int) -> None:
        raise OSError(errno.EOPNOTSUPP, "unsupported test flock")

    monkeypatch.setattr(os, "open", tracked_open)
    monkeypatch.setattr(module.locks.fcntl, "flock", unsupported_flock)
    with pytest.raises(module.models.DetachedImplementationError) as caught:
        with module.locks.detached_global_lock(time.monotonic() + 1.0):
            raise AssertionError("unreachable")
    assert caught.value.code == "detached-global-lock-unsupported"
    assert len(opened) == 1
    with pytest.raises(OSError):
        os.fstat(opened[0])


def test_pinners_directory_replacement_during_candidate_rolls_back_and_retains_transaction(
    tmp_path: Path,
    monkeypatch,
) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / "pinners-replacement")
    fixture = make_detached_record_fixture(tmp_path / "fixture-pinners-replacement", plugin_root=plugin_root)
    module = load_zagrosi_module(fixture.script)
    original_replace_state = module.transaction_io.replace_state_from_transaction
    initial_state = (fixture.implementation_root / "zagrosi_implement_state.json").read_bytes()
    displaced_pinners = tmp_path / "displaced-pinners"
    replacement_pinners = fixture.implementation_root / "pinners"
    replaced = False

    def replace_state_then_swap_pinners(root_fd, transaction_fd, expected_raw, replacement):
        nonlocal replaced
        replacement_raw = original_replace_state(root_fd, transaction_fd, expected_raw, replacement)
        if not replaced and replacement.get("completed_sections", {}).get(fixture.section) is not None:
            replaced = True
            replacement_pinners.rename(displaced_pinners)
            replacement_pinners.mkdir(mode=0o700)
            marker = replacement_pinners / ".record-section.lock"
            marker.write_bytes(b"")
            marker.chmod(0o600)
        return replacement_raw

    captured: list[tuple[dict, int]] = []
    monkeypatch.setattr(module.transaction_io, "replace_state_from_transaction", replace_state_then_swap_pinners)
    monkeypatch.setattr(
        module.output,
        "print_json",
        lambda payload, exit_code=0: captured.append((payload, exit_code)) or exit_code,
    )
    parsed = module.cli.build_parser().parse_args(detached_record_arguments(fixture))
    assert parsed.func(parsed) == 1
    assert replaced is True
    assert captured[-1][0]["error_code"] == "section-record-recovery-required"
    retained_state_raw = (fixture.implementation_root / "zagrosi_implement_state.json").read_bytes()
    assert retained_state_raw != initial_state
    assert fixture.section in json.loads(retained_state_raw)["completed_sections"]
    assert (displaced_pinners / ".record-section-transaction-v1" / "transaction.json").is_file()

    shutil.rmtree(replacement_pinners)
    displaced_pinners.rename(replacement_pinners)
