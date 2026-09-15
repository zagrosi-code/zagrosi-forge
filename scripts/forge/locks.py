"""Forge locks."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import errno
import fcntl
import os
import stat
import time

from . import detached_contract as _detached_contract
from . import models as _models
from . import processes as _processes
from . import secure_io as _secure_io

def require_safe_detached_global_anchor(anchor_fd: int) -> tuple[int, int, int, int, int, int]:
    observed = os.fstat(anchor_fd)
    mode = stat.S_IMODE(observed.st_mode)
    if (
        not stat.S_ISDIR(observed.st_mode)
        or observed.st_uid != 0
        or observed.st_nlink < 1
        or mode & 0o022
    ):
        raise _models.DetachedImplementationError(
            "unsafe-detached-global-lock",
            "The fixed global detached lock anchor must remain a root-owned, non-writable filesystem root directory.",
        )
    return (
        observed.st_dev,
        observed.st_ino,
        observed.st_mode,
        observed.st_uid,
        observed.st_gid,
        observed.st_nlink,
    )


@contextmanager
def detached_global_lock(deadline: float):
    anchor_fd: int | None = None
    locked = False
    try:
        anchor_fd = os.open(_detached_contract.DETACHED_GLOBAL_LOCK_PATH, _processes._directory_open_flags())
        os.set_inheritable(anchor_fd, False)
        acquired_metadata = require_safe_detached_global_anchor(anchor_fd)
        while True:
            try:
                fcntl.flock(anchor_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise _models.DetachedImplementationError(
                        "detached-lock-timeout",
                        "Timed out waiting for the fixed global detached lifecycle lock.",
                        path=str(_detached_contract.DETACHED_GLOBAL_LOCK_PATH),
                    )
                time.sleep(0.01)
            except OSError as exc:
                if exc.errno in {errno.ENOTSUP, errno.EOPNOTSUPP, errno.ENOSYS, errno.EINVAL}:
                    raise _models.DetachedImplementationError(
                        "detached-global-lock-unsupported",
                        "The fixed filesystem-root anchor does not support the required directory flock semantics.",
                    ) from exc
                raise

        def require_current_global_authority() -> None:
            if anchor_fd is None or not locked:
                raise _models.DetachedImplementationError(
                    "unsafe-detached-global-lock",
                    "The fixed global detached lifecycle lock is not held.",
                )
            if require_safe_detached_global_anchor(anchor_fd) != acquired_metadata:
                raise _models.DetachedImplementationError(
                    "unsafe-detached-global-lock",
                    "The fixed global detached lock anchor metadata changed while held.",
                )
            reopened_fd = os.open(_detached_contract.DETACHED_GLOBAL_LOCK_PATH, _processes._directory_open_flags())
            try:
                os.set_inheritable(reopened_fd, False)
                reopened_metadata = require_safe_detached_global_anchor(reopened_fd)
                if reopened_metadata != acquired_metadata:
                    raise _models.DetachedImplementationError(
                        "unsafe-detached-global-lock",
                        "The lexical filesystem root no longer names the acquired global lock anchor.",
                    )
            finally:
                os.close(reopened_fd)

        require_current_global_authority()
        yield require_current_global_authority
    finally:
        if anchor_fd is not None:
            if locked:
                try:
                    fcntl.flock(anchor_fd, fcntl.LOCK_UN)
                except OSError:
                    pass
            try:
                os.close(anchor_fd)
            except OSError:
                pass


@contextmanager
def section_record_lock(
    root_fd: int,
    implementation_root: Path,
    timeout_seconds: float = 5.0,
    *,
    create_marker_parent: bool = False,
    defer_marker: bool = False,
):
    parent_fd: int | None = None
    lock_fd: int | None = None
    authority_fd = os.dup(root_fd)
    os.set_inheritable(authority_fd, False)
    locked = False
    created = False
    deadline = time.monotonic() + timeout_seconds
    try:
        while True:
            try:
                fcntl.flock(authority_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise _models.DetachedImplementationError(
                        "detached-lock-timeout",
                        "Timed out waiting for the OS-released section-record lock.",
                        path=_detached_contract.SECTION_RECORD_LOCK_PATH,
                    )
                time.sleep(0.01)
            except OSError as exc:
                if exc.errno in {errno.ENOTSUP, errno.EOPNOTSUPP}:
                    raise _models.DetachedImplementationError(
                        "section-record-lock-unsupported",
                        "The detached implementation filesystem does not support directory flock authority.",
                    ) from exc
                raise
        reopened_root_fd = _secure_io.open_directory_chain_no_follow(implementation_root)
        try:
            if _secure_io._fd_identity(reopened_root_fd) != _secure_io._fd_identity(root_fd):
                raise _models.DetachedImplementationError(
                    "detached-root-replaced",
                    "Detached implementation root path no longer names the flocked authority descriptor.",
                )
        finally:
            os.close(reopened_root_fd)

        def ensure_marker(*, create_parent: bool, allow_create: bool) -> None:
            nonlocal parent_fd, lock_fd, created
            if lock_fd is not None:
                return
            parent_fd, marker_name = _secure_io.open_relative_parent(
                root_fd,
                _detached_contract.SECTION_RECORD_LOCK_PATH,
                create=create_parent,
            )
            if allow_create:
                try:
                    lock_fd = os.open(
                        marker_name,
                        os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                        0o600,
                        dir_fd=parent_fd,
                    )
                    created = True
                except FileExistsError:
                    lock_fd = os.open(
                        marker_name,
                        os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
                        dir_fd=parent_fd,
                    )
            else:
                try:
                    lock_fd = os.open(
                        marker_name,
                        os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
                        dir_fd=parent_fd,
                    )
                except FileNotFoundError as exc:
                    raise _models.DetachedImplementationError(
                        "unsafe-section-record-lock",
                        "The required diagnostic section-record marker is missing.",
                    ) from exc
            observed = os.fstat(lock_fd)
            if (
                not stat.S_ISREG(observed.st_mode)
                or stat.S_IMODE(observed.st_mode) != 0o600
                or observed.st_uid != os.getuid()
                or observed.st_nlink != 1
            ):
                raise _models.DetachedImplementationError(
                    "unsafe-section-record-lock",
                    "Section-record lock must be an owner-0600 regular single-link file.",
                )
            if created:
                os.fsync(lock_fd)
                os.fsync(parent_fd)

        if not defer_marker:
            ensure_marker(create_parent=create_marker_parent, allow_create=create_marker_parent)

        def require_current_lock_authority(
            *,
            create_marker: bool = False,
            require_marker: bool = False,
        ) -> None:
            reopened_root_fd = _secure_io.open_directory_chain_no_follow(implementation_root)
            try:
                if _secure_io._fd_identity(reopened_root_fd) != _secure_io._fd_identity(root_fd):
                    raise _models.DetachedImplementationError(
                        "detached-root-replaced",
                        "Detached implementation root path no longer names the flocked authority descriptor.",
                    )
            finally:
                os.close(reopened_root_fd)
            if create_marker:
                ensure_marker(create_parent=True, allow_create=True)
            elif require_marker:
                ensure_marker(create_parent=False, allow_create=False)
            if lock_fd is None or parent_fd is None:
                return
            current_parent_fd, current_name = _secure_io.open_relative_parent(root_fd, _detached_contract.SECTION_RECORD_LOCK_PATH)
            reopened_fd: int | None = None
            try:
                if _secure_io._fd_identity(current_parent_fd) != _secure_io._fd_identity(parent_fd):
                    raise _models.DetachedImplementationError(
                        "unsafe-section-record-lock",
                        "The lexical pinners directory no longer names the marker's acquired parent inode.",
                    )
                reopened_fd = os.open(
                    current_name,
                    os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=current_parent_fd,
                )
                reopened = os.fstat(reopened_fd)
                held = os.fstat(lock_fd)
                if (
                    (reopened.st_dev, reopened.st_ino) != (held.st_dev, held.st_ino)
                    or not stat.S_ISREG(reopened.st_mode)
                    or stat.S_IMODE(reopened.st_mode) != 0o600
                    or reopened.st_uid != os.getuid()
                    or reopened.st_nlink != 1
                ):
                    raise _models.DetachedImplementationError(
                        "unsafe-section-record-lock",
                        "Section-record lock path no longer names the acquired safe lock inode.",
                    )
            finally:
                if reopened_fd is not None:
                    os.close(reopened_fd)
                os.close(current_parent_fd)

        require_current_lock_authority()
        yield require_current_lock_authority
    finally:
        if locked:
            try:
                fcntl.flock(authority_fd, fcntl.LOCK_UN)
            except OSError:
                pass
        try:
            os.close(authority_fd)
        except OSError:
            pass
        if lock_fd is not None:
            try:
                os.close(lock_fd)
            except OSError:
                pass
        if parent_fd is not None:
            try:
                os.close(parent_fd)
            except OSError:
                pass
