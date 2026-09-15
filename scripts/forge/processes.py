"""Forge processes."""

from __future__ import annotations

from pathlib import Path
import os
import selectors
import signal
import subprocess
import sys
import tempfile
import time

from . import detached_contract as _detached_contract
from . import models as _models

def _directory_open_flags() -> int:
    return os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)


def _procfs_mount_hides_processes(raw_mounts: bytes) -> bool | None:
    for raw_line in raw_mounts.splitlines():
        fields = raw_line.split()
        if len(fields) < 4 or fields[1:3] != [b"/proc", b"proc"]:
            continue
        for option in fields[3].split(b","):
            if option == b"hidepid":
                return True
            if option.startswith(b"hidepid="):
                return option.split(b"=", 1)[1] != b"0"
        return False
    return None


def _linux_proc_stat_state_and_group(raw_stat: bytes) -> tuple[bytes, int] | None:
    closing_parenthesis = raw_stat.rfind(b")")
    fields = raw_stat[closing_parenthesis + 1 :].split() if closing_parenthesis >= 0 else []
    if len(fields) < 3 or len(fields[0]) != 1:
        return None
    try:
        return fields[0], int(fields[2])
    except ValueError:
        return None


def _linux_process_tasks_have_live_members(
    process_dir: Path,
    process_group: int,
) -> bool | None:
    try:
        task_entries = list(os.scandir(process_dir / "task"))
    except OSError:
        return None

    uncertain = False
    saw_matching_task = False
    for entry in task_entries:
        if not entry.name.isdigit():
            continue
        try:
            parsed = _linux_proc_stat_state_and_group(
                (Path(entry.path) / "stat").read_bytes()
            )
        except OSError:
            uncertain = True
            continue
        if parsed is None or parsed[1] != process_group:
            uncertain = True
            continue
        saw_matching_task = True
        if parsed[0] not in {b"Z", b"X", b"x"}:
            return True
    return None if uncertain or not saw_matching_task else False


def _linux_process_group_has_live_members(
    process_group: int,
    proc_root: Path = Path("/proc"),
) -> bool | None:
    if proc_root == Path("/proc"):
        try:
            procfs_hides_processes = _procfs_mount_hides_processes(
                (proc_root / "mounts").read_bytes()
            )
        except OSError:
            return None
        if procfs_hides_processes is not False:
            return None
    try:
        process_entries = list(os.scandir(proc_root))
    except OSError:
        return None

    uncertain = False
    saw_matching_group_member = False
    for entry in process_entries:
        if not entry.name.isdigit():
            continue
        try:
            parsed = _linux_proc_stat_state_and_group(
                (Path(entry.path) / "stat").read_bytes()
            )
        except FileNotFoundError:
            continue
        except OSError:
            uncertain = True
            continue
        if parsed is None:
            uncertain = True
            continue
        _, observed_group = parsed
        if observed_group != process_group:
            continue
        saw_matching_group_member = True
        tasks_have_live_members = _linux_process_tasks_have_live_members(
            Path(entry.path),
            process_group,
        )
        if tasks_have_live_members is True:
            return True
        if tasks_have_live_members is None:
            uncertain = True
    return None if uncertain or not saw_matching_group_member else False


def run_bounded_child(
    argv: list[str],
    input_bytes: bytes,
    *,
    cwd_fd: int,
    timeout_seconds: float,
    stdout_cap: int,
    stderr_cap: int,
    child_env: dict[str, str] | None = None,
) -> tuple[int, bytes, bytes]:
    dirty_status_probe = argv == [_detached_contract.HANDOFF_GIT, *_detached_contract.HANDOFF_GIT_STATUS_ARGS] and stdout_cap == 1

    def process_group_has_live_members(process_group: int) -> bool:
        try:
            os.killpg(process_group, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        linux_live_members = (
            _linux_process_group_has_live_members(process_group)
            if sys.platform.startswith("linux")
            else None
        )
        return True if linux_live_members is None else linux_live_members

    def reap_leader_if_group_absent(process: subprocess.Popen[bytes], process_group: int) -> bool:
        process.poll()
        if process_group_has_live_members(process_group):
            return False
        try:
            process.wait(timeout=0)
        except subprocess.TimeoutExpired:
            return False
        return True

    def wait_for_group_exit(process: subprocess.Popen[bytes], process_group: int, seconds: float) -> bool:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if reap_leader_if_group_absent(process, process_group):
                return True
            time.sleep(0.01)
        return reap_leader_if_group_absent(process, process_group)

    def terminate_and_reap(process: subprocess.Popen[bytes]) -> None:
        process_group = process.pid
        if reap_leader_if_group_absent(process, process_group):
            return
        try:
            os.killpg(process_group, signal.SIGTERM)
        except (ProcessLookupError, PermissionError) as exc:
            if wait_for_group_exit(process, process_group, 2.0):
                return
            raise _models.DetachedImplementationError(
                "handoff-child-termination-unproven",
                "Privileged handoff child process group could not be signalled for bounded termination.",
            ) from exc
        if wait_for_group_exit(process, process_group, 2.0):
            return
        if reap_leader_if_group_absent(process, process_group):
            return
        try:
            os.killpg(process_group, signal.SIGKILL)
        except (ProcessLookupError, PermissionError) as exc:
            if wait_for_group_exit(process, process_group, 2.0):
                return
            raise _models.DetachedImplementationError(
                "handoff-child-termination-unproven",
                "Privileged handoff child process group could not be killed for bounded termination.",
            ) from exc
        if not wait_for_group_exit(process, process_group, 2.0):
            raise _models.DetachedImplementationError(
                "handoff-child-termination-unproven",
                "Privileged handoff child process group could not be boundedly terminated and reaped.",
            )

    process: subprocess.Popen[bytes] | None = None
    process_group_closed = False
    selector = selectors.DefaultSelector()
    stdout = bytearray()
    stderr = bytearray()

    def enter_descriptor_cwd() -> None:
        os.fchdir(cwd_fd)
        os.close(cwd_fd)

    with tempfile.TemporaryFile() as child_stdin:
        child_stdin.write(input_bytes)
        child_stdin.flush()
        child_stdin.seek(0)
        try:
            process = subprocess.Popen(
                argv,
                stdin=child_stdin,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=None,
                env=_detached_contract.HANDOFF_ENV if child_env is None else child_env,
                shell=False,
                close_fds=True,
                pass_fds=(cwd_fd,),
                preexec_fn=enter_descriptor_cwd,
                start_new_session=True,
            )
            assert process.stdout is not None and process.stderr is not None
            for stream, label in ((process.stdout, "stdout"), (process.stderr, "stderr")):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, label)
            deadline = time.monotonic() + timeout_seconds
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise _models.DetachedImplementationError(
                        "handoff-child-timeout",
                        "Privileged handoff child exceeded its fixed deadline.",
                    )
                events = selector.select(min(remaining, 0.25))
                if not events and process.poll() is not None:
                    events = [(key, selectors.EVENT_READ) for key in list(selector.get_map().values())]
                for key, _ in events:
                    stream = key.fileobj
                    target = stdout if key.data == "stdout" else stderr
                    cap = stdout_cap if key.data == "stdout" else stderr_cap
                    read_size = 65536
                    if key.data == "stdout" and dirty_status_probe:
                        read_size = cap - len(target)
                        if read_size <= 0:
                            raise _models.DetachedImplementationError(
                                "handoff-child-output-cap",
                                "Privileged handoff child exceeded its fixed stdout cap.",
                            )
                    try:
                        chunk = os.read(stream.fileno(), read_size)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        selector.unregister(stream)
                        continue
                    if key.data == "stdout" and dirty_status_probe:
                        raise _models.DetachedImplementationError(
                            "handoff-source-dirty",
                            "Protected source contains tracked or untracked worktree changes.",
                        )
                    target.extend(chunk)
                    if len(target) > cap:
                        raise _models.DetachedImplementationError(
                            "handoff-child-output-cap",
                            f"Privileged handoff child exceeded its fixed {key.data} cap.",
                        )
            return_code = process.wait(timeout=max(0.0, deadline - time.monotonic()))
            if process_group_has_live_members(process.pid):
                raise _models.DetachedImplementationError(
                    "handoff-child-residual-process-group",
                    "Privileged handoff child left a residual process-group member after apparent success.",
                )
            process_group_closed = True
            return return_code, bytes(stdout), bytes(stderr)
        except Exception:
            if process is not None and not process_group_closed:
                terminate_and_reap(process)
            raise
        finally:
            selector.close()
            if process is not None:
                if process.stdout is not None:
                    process.stdout.close()
                if process.stderr is not None:
                    process.stderr.close()
