"""Per-Codex-home kernel lock with capability-bound file access."""

from __future__ import annotations

import ctypes
import errno
import math
import os
import stat
from threading import Lock
import time
from typing import Never

from . import paths as _paths
from .contracts import ForgeError
from .paths import OwnedRoot
from .policies import LIMIT_POLICY


DEFAULT_LOCK_TIMEOUT_SECONDS = LIMIT_POLICY.value("lock_default_seconds")
MAX_LOCK_TIMEOUT_SECONDS = LIMIT_POLICY.value("lock_max_seconds")
_LOCK_COMPONENT = "install.lock"
_POLL_SECONDS = 0.02
_LOCK_TOKEN = object()


def _error(code: str, message: str) -> ForgeError:
    return ForgeError(code, 14, message)


def _timeout(value: float | int | None) -> float:
    selected: object = DEFAULT_LOCK_TIMEOUT_SECONDS if value is None else value
    if isinstance(selected, bool) or not isinstance(selected, (int, float)):
        raise _error("lock.timeout", "The installer lock timeout is invalid.")
    try:
        normalized = float(selected)
    except (OverflowError, ValueError):
        raise _error("lock.timeout", "The installer lock timeout is invalid.") from None
    if (
        not math.isfinite(normalized)
        or normalized <= 0
        or normalized > MAX_LOCK_TIMEOUT_SECONDS
    ):
        raise _error("lock.timeout", "The installer lock timeout is invalid.")
    return normalized


def _open_posix_lock(root: OwnedRoot, parent: int) -> int:
    required = ("O_CLOEXEC", "O_NOFOLLOW")
    if any(not hasattr(os, name) for name in required):
        raise _error(
            "lock.unsupported_filesystem",
            "The platform cannot establish a safe installer lock.",
        )
    flags = os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW
    descriptor = -1
    try:
        try:
            descriptor = os.open(
                _LOCK_COMPONENT,
                flags | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=parent,
            )
            os.fchmod(descriptor, 0o600)
            os.fsync(parent)
        except FileExistsError:
            descriptor = os.open(_LOCK_COMPONENT, flags, dir_fd=parent)
        status = os.fstat(descriptor)
        if (
            not root._validate_control_descriptor(parent)
            or not stat.S_ISREG(status.st_mode)
            or status.st_uid != os.geteuid()
            or status.st_gid != os.getegid()
            or stat.S_IMODE(status.st_mode) != 0o600
            or status.st_nlink != 1
            or status.st_dev != root.control_identity[0]
            or not root._filesystem_guard(descriptor)
            or not _paths._posix_security_metadata_supported(descriptor, status)
        ):
            raise OSError(errno.EPERM, "installer lock metadata is unsafe")
        return descriptor
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        raise


def _open_windows_lock(root: OwnedRoot, parent: int) -> int:
    handle = 0
    try:
        try:
            handle = _paths._windows_create_private_file(parent, _LOCK_COMPONENT)
        except FileExistsError:
            handle = _paths._windows_open_child(
                parent,
                _LOCK_COMPONENT,
                directory=False,
                read_data=True,
                write_data=True,
            )
        status = _paths._windows_handle_status(handle)
        if (
            not root._validate_control_descriptor(parent)
            or status.is_directory
            or status.is_reparse
            or status.link_count != 1
            or status.identity[0] != root.control_identity[0]
            or status.attributes & ~(0x00000020 | 0x00000080)
            or not root._filesystem_guard(handle)
            or not _paths._windows_private_authorization(handle, exact=True)
        ):
            raise OSError(errno.EPERM, "installer lock metadata is unsafe")
        return handle
    except BaseException:
        if handle:
            _paths._windows_close(handle)
        raise


def _try_posix_lock(descriptor: int) -> bool:
    import fcntl

    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def _unlock_posix(descriptor: int) -> None:
    import fcntl

    fcntl.flock(descriptor, fcntl.LOCK_UN)


def _new_overlapped() -> ctypes.Structure:
    from ctypes import wintypes

    class Overlapped(ctypes.Structure):
        _fields_ = [
            ("Internal", ctypes.c_size_t),
            ("InternalHigh", ctypes.c_size_t),
            ("Offset", wintypes.DWORD),
            ("OffsetHigh", wintypes.DWORD),
            ("hEvent", wintypes.HANDLE),
        ]

    return Overlapped()


def _try_windows_lock(handle: int) -> ctypes.Structure | None:
    from ctypes import wintypes

    overlapped = _new_overlapped()
    kernel32 = _paths._windows_dll("kernel32")
    kernel32.LockFileEx.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
    ]
    kernel32.LockFileEx.restype = wintypes.BOOL
    if kernel32.LockFileEx(
        handle,
        0x00000001 | 0x00000002,
        0,
        1,
        0,
        ctypes.byref(overlapped),
    ):
        return overlapped
    number = _paths._windows_last_error()
    if number == 33:  # ERROR_LOCK_VIOLATION
        return None
    raise OSError(number, "LockFileEx failed")


def _unlock_windows(handle: int, overlapped: ctypes.Structure) -> None:
    from ctypes import wintypes

    kernel32 = _paths._windows_dll("kernel32")
    kernel32.UnlockFileEx.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
    ]
    kernel32.UnlockFileEx.restype = wintypes.BOOL
    if not kernel32.UnlockFileEx(
        handle,
        0,
        1,
        0,
        ctypes.byref(overlapped),
    ):
        raise OSError(_paths._windows_last_error(), "UnlockFileEx failed")


class HeldInstallLock:
    """A sealed, idempotently releasable kernel lock capability."""

    __slots__ = (
        "_descriptor",
        "_guard",
        "_overlapped",
        "_parent",
        "_released",
        "_windows",
        "codex_home_identity",
    )

    def __init__(
        self,
        descriptor: int,
        parent: int,
        codex_home_identity: tuple[int, int],
        *,
        windows: bool,
        overlapped: ctypes.Structure | None,
        _token: object,
    ) -> None:
        if _token is not _LOCK_TOKEN:
            raise TypeError("installer locks are created only by lock authority")
        self._descriptor = descriptor
        self._parent = parent
        self.codex_home_identity = codex_home_identity
        self._windows = windows
        self._overlapped = overlapped
        self._released = False
        self._guard = Lock()

    def release(self) -> None:
        descriptor = 0 if self._windows else -1
        parent = 0 if self._windows else -1
        overlapped: ctypes.Structure | None = None
        with self._guard:
            if self._released:
                return
            self._released = True
            descriptor = self._descriptor
            parent = self._parent
            overlapped = self._overlapped
            self._descriptor = 0 if self._windows else -1
            self._parent = 0 if self._windows else -1
            self._overlapped = None
        failure = False
        try:
            if self._windows:
                if descriptor and overlapped is not None:
                    _unlock_windows(descriptor, overlapped)
            elif descriptor >= 0:
                _unlock_posix(descriptor)
        except OSError:
            failure = True
        finally:
            if self._windows:
                if descriptor:
                    _paths._windows_close(descriptor)
                if parent:
                    _paths._windows_close(parent)
            else:
                if descriptor >= 0:
                    os.close(descriptor)
                if parent >= 0:
                    os.close(parent)
        if failure:
            raise _error(
                "lock.unsupported_filesystem",
                "The installer lock could not be released cleanly.",
            )

    def __enter__(self) -> HeldInstallLock:
        return self

    def __exit__(self, *_args: object) -> None:
        self.release()

    def __setattr__(self, name: str, value: object) -> None:
        if name == "codex_home_identity" and hasattr(self, name):
            raise AttributeError("codex_home_identity is read-only")
        object.__setattr__(self, name, value)

    def __reduce__(self) -> Never:
        raise TypeError("installer lock capabilities are not serializable")


def acquire_install_lock(
    root: OwnedRoot,
    *,
    timeout_seconds: float | int | None = None,
    platform: object | None = None,
) -> HeldInstallLock:
    """Acquire one kernel lock for the Codex home bound to ``root``."""

    del platform
    timeout = _timeout(timeout_seconds)
    if not isinstance(root, OwnedRoot):
        raise _error(
            "lock.unsupported_filesystem",
            "A live owned Codex root is required for locking.",
        )
    windows = os.name == "nt"
    parent = 0 if windows else -1
    descriptor = 0 if windows else -1
    try:
        parent = root._duplicate_control_descriptor()
        if not root._validate_control_descriptor(parent):
            raise _error(
                "lock.unsupported_filesystem",
                "The installer lock filesystem is not supported.",
            )
        descriptor = (
            _open_windows_lock(root, parent)
            if windows
            else _open_posix_lock(root, parent)
        )
        deadline = time.monotonic() + timeout
        while True:
            try:
                if windows:
                    overlapped = _try_windows_lock(descriptor)
                    acquired = overlapped is not None
                else:
                    overlapped = None
                    acquired = _try_posix_lock(descriptor)
            except OSError as exc:
                raise _error(
                    "lock.unsupported_filesystem",
                    "The platform kernel lock is unavailable.",
                ) from exc
            if acquired:
                held = HeldInstallLock(
                    descriptor,
                    parent,
                    root.home_identity,
                    windows=windows,
                    overlapped=overlapped,
                    _token=_LOCK_TOKEN,
                )
                descriptor = 0 if windows else -1
                parent = 0 if windows else -1
                return held
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise _error(
                    "lock.timeout",
                    "The installer lock could not be acquired before the timeout.",
                )
            time.sleep(min(_POLL_SECONDS, remaining))
    except ForgeError:
        raise
    except (OSError, TypeError, ValueError) as exc:
        raise _error(
            "lock.unsupported_filesystem",
            "The installer lock could not be established safely.",
        ) from exc
    finally:
        if windows:
            if descriptor:
                _paths._windows_close(descriptor)
            if parent:
                _paths._windows_close(parent)
        else:
            if descriptor >= 0:
                os.close(descriptor)
            if parent >= 0:
                os.close(parent)
