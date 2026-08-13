from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import time

import pytest


_LOCK_CHILD = r"""
from pathlib import Path
import sys
import time

from zagrosi_forge.install.contracts import RunnerProvenance, RunnerState
from zagrosi_forge.install.lock import acquire_install_lock
from zagrosi_forge.install.paths import PlatformPathAuthority

home = Path(sys.argv[1])
ready = Path(sys.argv[2])
release = None if sys.argv[3] == "-" else Path(sys.argv[3])
runner = RunnerProvenance(
    state=RunnerState.VERIFIED_INSTALLED_DISTRIBUTION,
    origin="installed-wheel",
    artifact_digest="a" * 64,
    runner_version="0.2.0",
    verification_authority="wheel-sha256",
    policy_digest="b" * 64,
)
root = PlatformPathAuthority().bootstrap_forge_root(home, runner=runner).unwrap()
try:
    held = acquire_install_lock(root, timeout_seconds=5.0)
    try:
        ready.write_text("ready", encoding="utf-8")
        if release is None:
            time.sleep(60)
        else:
            deadline = time.monotonic() + 10
            while not release.exists():
                if time.monotonic() >= deadline:
                    raise TimeoutError("release signal timed out")
                time.sleep(0.01)
    finally:
        held.release()
finally:
    root.close()
"""


_BOOTSTRAP_CHILD = r"""
import json
from pathlib import Path
import sys

from zagrosi_forge.install.contracts import RunnerProvenance, RunnerState
from zagrosi_forge.install.paths import PlatformPathAuthority

runner = RunnerProvenance(
    state=RunnerState.VERIFIED_INSTALLED_DISTRIBUTION,
    origin="installed-wheel",
    artifact_digest="a" * 64,
    runner_version="0.2.0",
    verification_authority="wheel-sha256",
    policy_digest="b" * 64,
)
root = PlatformPathAuthority().bootstrap_forge_root(
    Path(sys.argv[1]), runner=runner
).unwrap()
try:
    Path(sys.argv[2]).write_text(json.dumps(root.identity), encoding="utf-8")
finally:
    root.close()
"""


def _runner():
    from zagrosi_forge.install.contracts import RunnerProvenance, RunnerState

    return RunnerProvenance(
        state=RunnerState.VERIFIED_INSTALLED_DISTRIBUTION,
        origin="installed-wheel",
        artifact_digest="a" * 64,
        runner_version="0.2.0",
        verification_authority="wheel-sha256",
        policy_digest="b" * 64,
    )


def _private_directory(path: Path) -> None:
    if os.name != "nt":
        path.mkdir(mode=0o700)
        return
    import zagrosi_forge.install.paths as paths

    parent = paths._windows_open_path(os.fspath(path.parent))
    child = 0
    try:
        child = paths._windows_create_private_directory(parent, path.name)
    finally:
        if child:
            paths._windows_close(child)
        paths._windows_close(parent)


def _wait_for(path: Path, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if path.is_file():
            return
        if process.poll() is not None:
            raise AssertionError("lock child exited before readiness")
        time.sleep(0.01)
    raise AssertionError("lock child readiness timed out")


def _spawn_holder(
    home: Path, ready: Path, release: Path | None
) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [
            sys.executable,
            "-c",
            _LOCK_CHILD,
            os.fspath(home),
            os.fspath(ready),
            "-" if release is None else os.fspath(release),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _bootstrap(home: Path):
    from zagrosi_forge.install.paths import PlatformPathAuthority

    authority = PlatformPathAuthority()
    return authority.bootstrap_forge_root(home, runner=_runner()).unwrap()


def _write_private_lock(path: Path, raw: bytes) -> None:
    if os.name != "nt":
        path.write_bytes(raw)
        path.chmod(0o600)
        return
    import zagrosi_forge.install.paths as paths

    parent = paths._windows_open_path(os.fspath(path.parent))
    descriptor = 0
    try:
        descriptor = paths._windows_create_private_file(parent, path.name)
        paths._windows_write(descriptor, raw)
    finally:
        if descriptor:
            paths._windows_close(descriptor)
        paths._windows_close(parent)


def test_lock_serializes_one_codex_home(tmp_path: Path) -> None:
    from zagrosi_forge.install.contracts import ForgeError
    from zagrosi_forge.install.lock import acquire_install_lock

    home = tmp_path / "codex-home"
    _private_directory(home)
    root = _bootstrap(home)
    root.close()
    ready = tmp_path / "ready"
    release = tmp_path / "release"
    holder = _spawn_holder(home, ready, release)
    contender_root = None
    try:
        _wait_for(ready, holder)
        contender_root = _bootstrap(home)
        with pytest.raises(ForgeError) as raised:
            acquire_install_lock(contender_root, timeout_seconds=0.05)
        assert raised.value.code == "lock.timeout"
        assert raised.value.exit_category == 14
        release.write_bytes(b"release")
        assert holder.wait(timeout=10) == 0
        acquired = acquire_install_lock(contender_root, timeout_seconds=2.0)
        acquired.release()
    finally:
        if holder.poll() is None:
            holder.kill()
            holder.wait(timeout=10)
        if contender_root is not None:
            contender_root.close()


def test_lock_timeout_is_bounded_and_stable(tmp_path: Path) -> None:
    from zagrosi_forge.install.contracts import ForgeError
    from zagrosi_forge.install.lock import (
        DEFAULT_LOCK_TIMEOUT_SECONDS,
        MAX_LOCK_TIMEOUT_SECONDS,
        acquire_install_lock,
    )
    from zagrosi_forge.install.policies import LIMIT_POLICY

    assert DEFAULT_LOCK_TIMEOUT_SECONDS == LIMIT_POLICY.value("lock_default_seconds")
    assert MAX_LOCK_TIMEOUT_SECONDS == LIMIT_POLICY.value("lock_max_seconds")
    home = tmp_path / "codex-home"
    _private_directory(home)
    root = _bootstrap(home)
    try:
        with pytest.raises(ForgeError) as raised:
            acquire_install_lock(
                root,
                timeout_seconds=float(MAX_LOCK_TIMEOUT_SECONDS + 1),
            )
        assert raised.value.code == "lock.timeout"
        assert raised.value.exit_category == 14
        with pytest.raises(ForgeError) as extreme:
            acquire_install_lock(root, timeout_seconds=10**1000)
        assert extreme.value.code == "lock.timeout"
        assert extreme.value.exit_category == 14
        assert not (home / "plugins/.zagrosi/install.lock").exists()
    finally:
        root.close()


def test_process_death_releases_kernel_lock(tmp_path: Path) -> None:
    from zagrosi_forge.install.contracts import ForgeError
    from zagrosi_forge.install.lock import acquire_install_lock

    home = tmp_path / "codex-home"
    _private_directory(home)
    root = _bootstrap(home)
    root.close()
    ready = tmp_path / "ready"
    holder = _spawn_holder(home, ready, None)
    contender_root = None
    try:
        _wait_for(ready, holder)
        contender_root = _bootstrap(home)
        with pytest.raises(ForgeError) as raised:
            acquire_install_lock(contender_root, timeout_seconds=0.05)
        assert raised.value.code == "lock.timeout"
        holder.kill()
        holder.wait(timeout=10)
        acquired = acquire_install_lock(contender_root, timeout_seconds=2.0)
        acquired.release()
    finally:
        if holder.poll() is None:
            holder.kill()
            holder.wait(timeout=10)
        if contender_root is not None:
            contender_root.close()


def test_stale_diagnostic_metadata_never_grants_lock_stealing(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.contracts import ForgeError
    from zagrosi_forge.install.lock import acquire_install_lock

    home = tmp_path / "codex-home"
    _private_directory(home)
    root = _bootstrap(home)
    lock_path = home / "plugins/.zagrosi/install.lock"
    _write_private_lock(
        lock_path,
        b'{"pid":1,"started":"1970-01-01T00:00:00Z","runner":"forged"}',
    )
    second_root = _bootstrap(home)
    first = acquire_install_lock(root, timeout_seconds=1.0)
    try:
        with pytest.raises(ForgeError) as raised:
            acquire_install_lock(second_root, timeout_seconds=0.05)
        assert raised.value.code == "lock.timeout"
        first.release()
        acquired = acquire_install_lock(second_root, timeout_seconds=1.0)
        acquired.release()
    finally:
        first.release()
        second_root.close()
        root.close()


def test_failed_validation_never_unlinks_shared_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from zagrosi_forge.install.contracts import ForgeError
    import zagrosi_forge.install.lock as lock_module

    home = tmp_path / "codex-home"
    _private_directory(home)
    root = _bootstrap(home)
    lock_path = home / "plugins/.zagrosi/install.lock"
    contender = 0 if os.name == "nt" else -1
    try:
        if os.name == "nt":
            original = lock_module._paths._windows_private_authorization

            def reject_windows(handle: int, *, exact: bool) -> bool:
                nonlocal contender
                status = lock_module._paths._windows_handle_status(handle)
                if status.is_directory or not lock_path.exists():
                    return original(handle, exact=exact)
                parent = lock_module._paths._windows_open_path(
                    os.fspath(lock_path.parent)
                )
                named = 0
                try:
                    named = lock_module._paths._windows_open_child(
                        parent,
                        lock_path.name,
                        directory=False,
                        read_data=True,
                        write_data=True,
                    )
                    if (
                        lock_module._paths._windows_handle_status(named).identity
                        != status.identity
                    ):
                        return original(handle, exact=exact)
                    if contender == 0:
                        contender = named
                        named = 0
                finally:
                    if named:
                        lock_module._paths._windows_close(named)
                    lock_module._paths._windows_close(parent)
                assert exact
                return False

            monkeypatch.setattr(
                lock_module._paths,
                "_windows_private_authorization",
                reject_windows,
            )
        else:
            original = lock_module._paths._posix_security_metadata_supported

            def reject_posix(descriptor: int, status: os.stat_result) -> bool:
                nonlocal contender
                try:
                    named_status = lock_path.stat()
                except FileNotFoundError:
                    return original(descriptor, status)
                if not stat.S_ISREG(status.st_mode) or (
                    status.st_dev,
                    status.st_ino,
                ) != (named_status.st_dev, named_status.st_ino):
                    return original(descriptor, status)
                if contender < 0:
                    contender = os.open(lock_path, os.O_RDWR | os.O_CLOEXEC)
                return False

            monkeypatch.setattr(
                lock_module._paths,
                "_posix_security_metadata_supported",
                reject_posix,
            )
        with pytest.raises(ForgeError) as raised:
            lock_module.acquire_install_lock(root, timeout_seconds=0.1)
        assert raised.value.code == "lock.unsupported_filesystem"
        assert lock_path.is_file()
        if os.name == "nt":
            parent = lock_module._paths._windows_open_path(os.fspath(lock_path.parent))
            named = 0
            try:
                named = lock_module._paths._windows_open_child(
                    parent,
                    lock_path.name,
                    directory=False,
                    read_data=True,
                )
                assert (
                    lock_module._paths._windows_handle_status(contender).identity
                    == lock_module._paths._windows_handle_status(named).identity
                )
            finally:
                if named:
                    lock_module._paths._windows_close(named)
                lock_module._paths._windows_close(parent)
        else:
            contender_status = os.fstat(contender)
            named_status = lock_path.stat()
            assert (contender_status.st_dev, contender_status.st_ino) == (
                named_status.st_dev,
                named_status.st_ino,
            )
        if os.name == "nt":
            monkeypatch.setattr(
                lock_module._paths, "_windows_private_authorization", original
            )
        else:
            monkeypatch.setattr(
                lock_module._paths,
                "_posix_security_metadata_supported",
                original,
            )
        acquired = lock_module.acquire_install_lock(root, timeout_seconds=1.0)
        acquired.release()
    finally:
        if os.name == "nt":
            if contender:
                lock_module._paths._windows_close(contender)
        elif contender >= 0:
            os.close(contender)
        root.close()


def test_concurrent_first_bootstrap_creates_one_safe_owned_root(
    tmp_path: Path,
) -> None:
    home = tmp_path / "codex-home"
    _private_directory(home)
    outputs = [tmp_path / "one.json", tmp_path / "two.json"]
    children = [
        subprocess.Popen(
            [sys.executable, "-c", _BOOTSTRAP_CHILD, os.fspath(home), os.fspath(out)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for out in outputs
    ]
    for child in children:
        stdout, stderr = child.communicate(timeout=15)
        assert child.returncode == 0, (stdout, stderr)
    identities = [
        tuple(json.loads(path.read_text(encoding="utf-8"))) for path in outputs
    ]
    assert identities[0] == identities[1]
    if os.name != "nt":
        assert stat.S_IMODE((home / "plugins/.zagrosi").stat().st_mode) == 0o700
    root = _bootstrap(home)
    try:
        assert root.identity == identities[0]
    finally:
        root.close()


def test_network_or_unknown_filesystem_is_rejected(tmp_path: Path) -> None:
    from zagrosi_forge.install.paths import PlatformPathAuthority

    home = tmp_path / "codex-home"
    _private_directory(home)
    authority = PlatformPathAuthority._non_authoritative_for_testing()
    result = authority.bootstrap_forge_root(home, runner=_runner())
    assert result.error is not None
    assert result.error.code == "path.unsupported_filesystem"
    assert not (home / "plugins").exists()
