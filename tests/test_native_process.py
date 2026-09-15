from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from forge_test_helpers import (
    load_zagrosi_module,
)


def test_linux_process_group_probe_treats_zombie_only_group_as_inactive(
    tmp_path: Path,
) -> None:
    module = load_zagrosi_module()
    proc_root = tmp_path / "proc"
    proc_root.mkdir()

    def write_stat(pid: int, state: str, process_group: int) -> None:
        process_dir = proc_root / str(pid)
        process_dir.mkdir(exist_ok=True)
        (process_dir / "stat").write_text(
            f"{pid} (child {pid}) {state} 1 {process_group} {process_group} 0\n"
        )
        task_dir = process_dir / "task" / str(pid)
        task_dir.mkdir(parents=True)
        (task_dir / "stat").write_text(
            f"{pid} (child {pid}) {state} 1 {process_group} {process_group} 0\n"
        )

    assert module.processes._linux_process_group_has_live_members(4321, proc_root) is None

    write_stat(100, "S", 9999)

    assert module.processes._linux_process_group_has_live_members(4321, proc_root) is None

    write_stat(101, "Z", 4321)
    write_stat(102, "Z", 4321)

    assert module.processes._linux_process_group_has_live_members(4321, proc_root) is False

    malformed_dir = proc_root / "104"
    malformed_dir.mkdir()
    (malformed_dir / "stat").write_bytes(b"malformed\n")

    assert module.processes._linux_process_group_has_live_members(4321, proc_root) is None

    write_stat(103, "S", 4321)

    assert module.processes._linux_process_group_has_live_members(4321, proc_root) is True


def test_linux_process_group_probe_detects_live_worker_behind_zombie_leader(
    tmp_path: Path,
) -> None:
    module = load_zagrosi_module()
    proc_root = tmp_path / "proc"
    process_dir = proc_root / "101"
    task_dir = process_dir / "task"
    (task_dir / "101").mkdir(parents=True)
    (task_dir / "201").mkdir()
    (process_dir / "stat").write_text("101 (leader) Z 1 4321 4321 0\n")
    (task_dir / "101" / "stat").write_text("101 (leader) Z 1 4321 4321 0\n")
    (task_dir / "201" / "stat").write_text("201 (worker) S 1 4321 4321 0\n")

    assert module.processes._linux_process_group_has_live_members(4321, proc_root) is True


def test_procfs_mount_visibility_gate_rejects_hidden_or_unknown_process_views() -> None:
    module = load_zagrosi_module()

    assert module.processes._procfs_mount_hides_processes(
        b"proc /proc proc rw,nosuid,nodev,noexec,relatime 0 0\n"
    ) is False
    assert module.processes._procfs_mount_hides_processes(
        b"proc /proc proc rw,nosuid,hidepid=2 0 0\n"
    ) is True
    assert module.processes._procfs_mount_hides_processes(b"tmpfs /tmp tmpfs rw 0 0\n") is None


def test_run_bounded_child_accepts_zombie_only_process_group_after_success(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = load_zagrosi_module()
    real_killpg = os.killpg
    zero_probes: list[int] = []

    def zombie_only_group(process_group: int, sig: int) -> None:
        if sig == 0:
            zero_probes.append(process_group)
            return
        real_killpg(process_group, sig)

    monkeypatch.setattr(os, "killpg", zombie_only_group)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(
        module.processes,
        "_linux_process_group_has_live_members",
        lambda process_group: False,
    )
    cwd_fd = os.open(tmp_path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        return_code, stdout, stderr = module.processes.run_bounded_child(
            [sys.executable, "-I", "-B", "-c", "pass"],
            b"",
            cwd_fd=cwd_fd,
            timeout_seconds=2.0,
            stdout_cap=4096,
            stderr_cap=4096,
        )
    finally:
        os.close(cwd_fd)

    assert return_code == 0
    assert stdout == b""
    assert stderr == b""
    assert zero_probes


@pytest.mark.parametrize(
    ("child_shape", "expected_code"),
    (
        ("stubborn_leader", "handoff-child-timeout"),
        ("inherited_pipe_descendant", "handoff-child-timeout"),
        ("closed_pipe_descendant_after_success", "handoff-child-residual-process-group"),
    ),
)
def test_run_bounded_child_terminates_and_reaps_the_complete_process_group(
    tmp_path: Path,
    child_shape: str,
    expected_code: str,
) -> None:
    module = load_zagrosi_module()
    pid_path = tmp_path / f"{child_shape}.pid"
    if child_shape == "stubborn_leader":
        source = (
            "import os,signal,time\n"
            f"open({str(pid_path)!r},'w').write(str(os.getpid()))\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "while True: time.sleep(1)\n"
        )
    else:
        close_pipes = " os.close(1); os.close(2)\n" if child_shape == "closed_pipe_descendant_after_success" else ""
        source = (
            "import os,signal,time\n"
            "child=os.fork()\n"
            "if child==0:\n"
            f"{close_pipes}"
            " signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            " while True: time.sleep(1)\n"
            f"open({str(pid_path)!r},'w').write(str(child))\n"
            "os._exit(0)\n"
        )
    cwd_fd = os.open(tmp_path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    started = time.monotonic()
    try:
        with pytest.raises(module.models.DetachedImplementationError) as caught:
            module.processes.run_bounded_child(
                [sys.executable, "-I", "-B", "-c", source],
                b"",
                cwd_fd=cwd_fd,
                timeout_seconds=0.25,
                stdout_cap=4096,
                stderr_cap=4096,
            )
    finally:
        os.close(cwd_fd)
    assert caught.value.code == expected_code
    assert time.monotonic() - started < 5.0
    pid = int(pid_path.read_text())
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        pass
    else:
        assert sys.platform.startswith("linux")
        try:
            raw_stat = (Path("/proc") / str(pid) / "stat").read_bytes()
        except FileNotFoundError:
            pass
        else:
            closing_parenthesis = raw_stat.rfind(b")")
            fields = raw_stat[closing_parenthesis + 1 :].split()
            assert fields and fields[0] in {b"Z", b"X", b"x"}


@pytest.mark.parametrize("stream_fd", (1, 2))
def test_run_bounded_child_rejects_complete_output_cap_mutants(
    tmp_path: Path,
    stream_fd: int,
) -> None:
    module = load_zagrosi_module()
    source = f"import os,time; os.write({stream_fd}, b'x' * 65); time.sleep(10)"
    cwd_fd = os.open(tmp_path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        with pytest.raises(module.models.DetachedImplementationError) as caught:
            module.processes.run_bounded_child(
                [sys.executable, "-I", "-B", "-c", source],
                b"",
                cwd_fd=cwd_fd,
                timeout_seconds=2.0,
                stdout_cap=64,
                stderr_cap=64,
            )
    finally:
        os.close(cwd_fd)
    assert caught.value.code == "handoff-child-output-cap"


@pytest.mark.skipif(sys.platform != "darwin", reason="requires Apple Git process behaviour")
def test_run_bounded_child_real_apple_git_dirty_probe_preserves_semantic_error(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = load_zagrosi_module()
    apple_git = Path("/usr/bin/git")
    if not apple_git.is_file():
        pytest.skip("Apple Git is unavailable")
    version = subprocess.run(
        [str(apple_git), "--version"],
        check=True,
        capture_output=True,
        text=True,
        env=module.detached_contract.HANDOFF_GIT_ENV,
    ).stdout.strip()
    if version != "git version 2.50.1 (Apple Git-155)":
        pytest.skip(f"requires Apple Git 2.50.1 (Apple Git-155), found {version}")

    repository = tmp_path / "dirty-repository"
    repository.mkdir()
    subprocess.run(
        [str(apple_git), "init", "--quiet"],
        cwd=repository,
        check=True,
        capture_output=True,
        env=module.detached_contract.HANDOFF_GIT_ENV,
    )
    (repository / "dirty-untracked.txt").write_text("dirty\n")
    monkeypatch.setattr(module.detached_contract, "HANDOFF_GIT", str(apple_git))
    cwd_fd = os.open(repository, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        with pytest.raises(module.models.DetachedImplementationError) as caught:
            module.processes.run_bounded_child(
                [module.detached_contract.HANDOFF_GIT, *module.detached_contract.HANDOFF_GIT_STATUS_ARGS],
                b"",
                cwd_fd=cwd_fd,
                timeout_seconds=10.0,
                stdout_cap=1,
                stderr_cap=65536,
                child_env=module.detached_contract.HANDOFF_GIT_ENV,
            )
    finally:
        os.close(cwd_fd)

    assert caught.value.code == "handoff-source-dirty"
