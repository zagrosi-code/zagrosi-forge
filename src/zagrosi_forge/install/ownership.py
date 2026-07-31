"""Receipt-bound ownership and the sole recursive removal capability."""

from __future__ import annotations

import ctypes
from _thread import LockType
from dataclasses import dataclass
from datetime import datetime
import errno
from enum import Enum
import hashlib
from importlib import resources
import os
from pathlib import PurePosixPath, PureWindowsPath
import re
import secrets
import stat
import sys
from threading import Lock
import time
from typing import TYPE_CHECKING, Callable, Mapping, Never, cast
import unicodedata

from . import lock as _kernel_lock
from .contracts import (
    ActiveInstallRelation,
    ForgeError,
    InstallIdentity,
    ManagedConfigProjection,
    Result,
    canonical_json_bytes,
    decode_persistent_record,
    install_identity_digest as _contract_install_identity_digest,
)
from . import paths as _paths
from .paths import OpenedRegularFile, OwnedRoot, PathProof, SafeRelativePath
from .policies import LIMIT_POLICY

if TYPE_CHECKING:
    from .journal import JournalPathIdentity, LoadedJournal


RECEIPT_SCHEMA_DIGEST = (
    "4e110c2312c112652913e820c498c35e6c98be371dbf90a20d90e9ab636fb1a5"
)
_CAPABILITY_TOKEN = object()
_OBSERVATION_TOKEN = object()
_RELATION_TOKEN = object()
_LEGACY_TOKEN = object()
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_TRANSACTION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_IDENTIFIER = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_RELEASE_VERSION = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z"
)
_WRITER_VERSION = "0.2.0"
_RECEIPT_SCHEMA_VERSION = "1.0"
_STATE_MACHINE_VERSION = "1.0"
_POLICY_VERSION = "1.0"
_TRANSFORMATION_VERSION = "plugin-v1"
_PLUGIN_ID = "zagrosi-forge"
_LEGACY_CATALOG_SCHEMA_DIGEST = (
    "c86c4e5f2393a80d04ee4a84fb0e882d9987a007c689158697f17cf7a9ba1bf2"
)
_LEGACY_CATALOG_RESOURCE_DIGEST = (
    "45ef63eaafbd03127c7fa9d225c3fd073082999a25ee26484fa30319fb21e992"
)
_LEGACY_CATALOG_RECORD_DIGEST = (
    "e69354c3a3c4bcc9628d271ceaea4a838a51027133b8fc4f1ffcd81c65b7c71a"
)
_CLEANUP_MAX_DEPTH = LIMIT_POLICY.value("path_components")
_CLEANUP_MAX_ENTRIES = LIMIT_POLICY.value("bundle_files")
_TRANSACTION_STATE_LOCK_TIMEOUT_SECONDS = LIMIT_POLICY.value("lock_default_seconds")
_TRANSACTION_STATE_LOCK_POLL_SECONDS = 0.02
_PERSISTENT_TRANSACTION = re.compile(r"tx-[0-9a-f]{32}\Z")
_TRANSACTION_STAGE = re.compile(r"\.root-[0-9a-f]{32}\.tmp\Z")
_TRANSACTION_PENDING_CLAIM = re.compile(r"\.claim-[0-9a-f]{32}\.tmp\Z")
_TRANSACTION_DELETE = re.compile(r"\.delete-[0-9a-f]{32}\.tmp\Z")
_TRANSACTION_ANCHOR = re.compile(r"(tx-[0-9a-f]{32})\.json\Z")
_TRANSACTION_CREATION_RECORD = re.compile(
    r"\.(tx-[0-9a-f]{32})\.(?:create|reserve)\.json\Z"
)
_TRANSACTION_CLEANUP_RECORD = re.compile(
    r"(tx-[0-9a-f]{32})\.(?:removed|removing)\.json\Z"
)
_TRANSACTION_RECOVERY_CLEANUP_RECORD = re.compile(
    r"(tx-[0-9a-f]{32})\.rc-(?:auth|final|done)\.json\Z"
)
_TRANSACTION_QUARANTINE = re.compile(r"\.zagrosi-quarantine-[0-9a-f]{24}\Z")
_TRANSACTION_RETIRED_RECORD = re.compile(r"\.retired-[0-9a-f]{64}\.json\Z")
_TRANSACTION_RECORD_STAGE = re.compile(r"\.record-[0-9a-f]{32}\.tmp\Z")
_TRANSACTION_STORE_COMPONENT = "transactions"
_TRANSACTION_CLAIMS_COMPONENT = "claims"
_TRANSACTION_STORE_CONTROL = "control-v1.json"
_TRANSACTION_RECORD_LIMIT = 8 * 1024
_TRANSACTION_STATE_LOCK_OFFSET = _TRANSACTION_RECORD_LIMIT + 1
_TRANSACTION_STORE_SCHEMA_DIGEST = (
    "fc1809f697c590f522f0fe9b6281d81123f7193a59cf1a209547cfe7fb908d5b"
)
_TRANSACTION_BINDING_SCHEMA_DIGEST = (
    "57dfa73352f792d8e0c2775d5248d802eb25c0ea0ee4bf83a6e67632feed13a6"
)
_TRANSACTION_CREATE_RESERVATION_SCHEMA_DIGEST = (
    "066d3ae25970752d495aca2d6d6a2e9ca21fa0a547e2afae0cd317b04e7978c0"
)
_TRANSACTION_CREATE_INTENT_SCHEMA_DIGEST = (
    "5024b0824c133f903d71082fbf0ca640ba06ff370dfdd1ab2507259760de614f"
)
_TRANSACTION_CLEANUP_SCHEMA_DIGEST = (
    "348e089f2bc194332ea8e2189a1847efa01502293b497688e9759ccc0c8d1664"
)
_TRANSACTION_RECOVERY_CLEANUP_SCHEMA_DIGEST = (
    "fd94ea12fe57a94b1c3372b79446e344b58956e0deccf2886cc51b9be7a6d1fc"
)
_ZERO_DIGEST = "0" * 64
_PERSISTENT_BINDING_TOKEN = object()
_PERSISTENT_ROOT_TOKEN = object()
_REBOUND_TRANSACTION_TOKEN = object()
_TRANSACTION_JOURNAL_ACCESS_TOKEN = object()
_QUARANTINED_RECOVERY_JOURNAL_ACCESS_TOKEN = object()
_QUARANTINED_RECOVERY_JOURNAL_WRITER_TOKEN = object()
_PENDING_TRANSACTION_OBSERVATION_TOKEN = object()
_RECOVERY_CLEANUP_AUTHORIZATION_TOKEN = object()
_RECOVERY_CLEANUP_OBSERVATION_TOKEN = object()
_RECOVERY_FINALIZATION_OBSERVATION_TOKEN = object()


def _error(code: str, message: str, *, recovery: tuple[str, ...] = ()) -> ForgeError:
    return ForgeError(code, 11, message, recovery_instructions=recovery)


def _failure(code: str, message: str) -> Result[object]:
    return Result.failure(_error(code, message))


def _receipt_failure_from(exc: ForgeError) -> ForgeError:
    if exc.code.startswith("ownership."):
        return exc
    if exc.code == "record.reader_unsupported":
        return _error(
            "ownership.receipt_unsupported",
            "The ownership receipt cannot be trusted.",
        )
    if exc.code.startswith("path."):
        return _error(
            "ownership.identity_mismatch",
            "The ownership receipt containment changed.",
        )
    return _error(
        "ownership.receipt_corrupt", "The ownership receipt cannot be trusted."
    )


@dataclass(frozen=True, slots=True)
class _SafeReferenceSnapshot:
    value: str
    components: tuple[str, ...]
    collision_key: str


def _snapshot_safe_reference(reference: object) -> _SafeReferenceSnapshot | None:
    """Authenticate and freeze every projection before native path access."""

    if not _paths._safe_reference_invariants(reference):
        return None
    candidate = cast(SafeRelativePath, reference)
    snapshot = _SafeReferenceSnapshot(
        candidate.value,
        candidate.components,
        candidate.collision_key,
    )
    if (
        not _paths._safe_reference_invariants(candidate)
        or candidate.value != snapshot.value
        or candidate.components != snapshot.components
        or candidate.collision_key != snapshot.collision_key
    ):
        return None
    return snapshot


def _identity(descriptor: int) -> tuple[int, int]:
    status = os.fstat(descriptor)
    return status.st_dev, status.st_ino


def _native_identity(descriptor: int) -> tuple[int, int]:
    if os.name == "posix":
        return _identity(descriptor)
    return _paths._windows_handle_status(descriptor).identity


def _close_native(descriptor: int) -> None:
    if os.name == "posix":
        os.close(descriptor)
    else:
        _paths._windows_close(descriptor)


def _close_all_native_descriptors(*descriptors: int) -> None:
    empty = 0 if os.name == "nt" else -1
    first_failure: BaseException | None = None
    for descriptor in descriptors:
        if descriptor == empty:
            continue
        try:
            _close_native(descriptor)
        except BaseException as exc:
            if first_failure is None:
                first_failure = exc
    if first_failure is not None:
        raise first_failure


def _windows_open_raw_child(
    parent: int,
    component: str,
    *,
    directory: bool | None,
    read_data: bool = False,
    write_data: bool = False,
    delete_access: bool = False,
    create: bool = False,
    security_descriptor: int | None = None,
) -> int:
    """Open one Windows child by held parent without following reparse points."""

    from ctypes import wintypes

    if (
        not component
        or component in {".", ".."}
        or "/" in component
        or "\\" in component
        or "\0" in component
    ):
        raise ValueError("Windows child component")

    class UnicodeString(ctypes.Structure):
        _fields_ = [
            ("Length", wintypes.USHORT),
            ("MaximumLength", wintypes.USHORT),
            ("Buffer", wintypes.LPWSTR),
        ]

    class ObjectAttributes(ctypes.Structure):
        _fields_ = [
            ("Length", wintypes.ULONG),
            ("RootDirectory", wintypes.HANDLE),
            ("ObjectName", ctypes.POINTER(UnicodeString)),
            ("Attributes", wintypes.ULONG),
            ("SecurityDescriptor", wintypes.LPVOID),
            ("SecurityQualityOfService", wintypes.LPVOID),
        ]

    class StatusOrPointer(ctypes.Union):
        _fields_ = [("Status", wintypes.LONG), ("Pointer", wintypes.LPVOID)]

    class IoStatusBlock(ctypes.Structure):
        _anonymous_ = ("result",)
        _fields_ = [("result", StatusOrPointer), ("Information", ctypes.c_size_t)]

    name_buffer = ctypes.create_unicode_buffer(component)
    name_bytes = component.encode("utf-16-le")
    name = UnicodeString(
        len(name_bytes),
        len(name_bytes) + 2,
        ctypes.cast(name_buffer, wintypes.LPWSTR),
    )
    attributes = ObjectAttributes(
        ctypes.sizeof(ObjectAttributes),
        parent,
        ctypes.pointer(name),
        0x00000040,
        security_descriptor,
        None,
    )
    result_handle = wintypes.HANDLE()
    io_status = IoStatusBlock()
    desired_access = 0x00000080 | 0x00020000 | 0x00100000
    if directory is True:
        desired_access |= 0x00000020
    if read_data:
        desired_access |= 0x00000001
    if write_data:
        desired_access |= 0x00000002
    if delete_access:
        desired_access |= 0x00010000
    create_options = 0x00200000 | 0x00000020
    if directory is True:
        create_options |= 0x00000001
    elif directory is False:
        create_options |= 0x00000040
    ntdll = _paths._windows_dll("ntdll")
    ntdll.NtCreateFile.argtypes = [
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.DWORD,
        ctypes.POINTER(ObjectAttributes),
        ctypes.POINTER(IoStatusBlock),
        ctypes.POINTER(ctypes.c_longlong),
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    ntdll.NtCreateFile.restype = wintypes.LONG
    status = int(
        ntdll.NtCreateFile(
            ctypes.byref(result_handle),
            desired_access,
            ctypes.byref(attributes),
            ctypes.byref(io_status),
            None,
            0,
            0x00000001 | 0x00000002 | 0x00000004,
            2 if create else 1,
            create_options,
            None,
            0,
        )
    )
    if status < 0:
        ntdll.RtlNtStatusToDosError.argtypes = [wintypes.LONG]
        ntdll.RtlNtStatusToDosError.restype = wintypes.ULONG
        number = int(ntdll.RtlNtStatusToDosError(status))
        if number in {80, 183}:
            raise FileExistsError(number, "Windows child already exists")
        raise _paths._windows_error(number)
    if not result_handle.value:
        raise _error("path.outside_root", "Windows returned no child handle.")
    return int(result_handle.value)


def _windows_rename_handle(source: int, parent: int, destination: str) -> None:
    _paths._windows_rename_handle(source, parent, destination)


def _windows_delete_handle(handle: int) -> None:
    from ctypes import wintypes

    class FileDispositionInfo(ctypes.Structure):
        _fields_ = [("DeleteFile", wintypes.BOOL)]

    information = FileDispositionInfo(True)
    kernel32 = _paths._windows_dll("kernel32")
    kernel32.SetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    kernel32.SetFileInformationByHandle.restype = wintypes.BOOL
    if not kernel32.SetFileInformationByHandle(
        handle, 4, ctypes.byref(information), ctypes.sizeof(information)
    ):
        raise _paths._windows_error(_paths._windows_last_error())


def _windows_list_names(handle: int, *, limit: int) -> tuple[str, ...]:
    from ctypes import wintypes

    class FileIdBothDirectoryInfo(ctypes.Structure):
        _fields_ = [
            ("NextEntryOffset", wintypes.DWORD),
            ("FileIndex", wintypes.DWORD),
            ("CreationTime", ctypes.c_longlong),
            ("LastAccessTime", ctypes.c_longlong),
            ("LastWriteTime", ctypes.c_longlong),
            ("ChangeTime", ctypes.c_longlong),
            ("EndOfFile", ctypes.c_longlong),
            ("AllocationSize", ctypes.c_longlong),
            ("FileAttributes", wintypes.DWORD),
            ("FileNameLength", wintypes.DWORD),
            ("EaSize", wintypes.DWORD),
            ("ShortNameLength", ctypes.c_ubyte),
            ("ShortName", ctypes.c_wchar * 12),
            ("FileId", ctypes.c_longlong),
            ("FileName", ctypes.c_wchar * 1),
        ]

    kernel32 = _paths._windows_dll("kernel32")
    kernel32.GetFileInformationByHandleEx.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL
    names: list[str] = []
    seen: set[str] = set()
    restart = True
    while True:
        buffer = ctypes.create_string_buffer(64 * 1024)
        if not kernel32.GetFileInformationByHandleEx(
            handle,
            11 if restart else 10,
            buffer,
            len(buffer),
        ):
            number = _paths._windows_last_error()
            if number in {18, 259}:
                break
            raise _paths._windows_error(number)
        restart = False
        offset = 0
        while True:
            entry = FileIdBothDirectoryInfo.from_buffer(buffer, offset)
            name = ctypes.wstring_at(
                ctypes.addressof(buffer)
                + offset
                + FileIdBothDirectoryInfo.FileName.offset,
                int(entry.FileNameLength) // 2,
            )
            if name not in {".", ".."} and name not in seen:
                names.append(name)
                seen.add(name)
                if len(names) > limit:
                    raise OSError(errno.E2BIG, "cleanup entry limit exceeded")
            if not entry.NextEntryOffset:
                break
            offset += int(entry.NextEntryOffset)
        if not entry.NextEntryOffset:
            continue
    return tuple(sorted(names))


def _windows_write_all(handle: int, raw: bytes) -> None:
    from ctypes import wintypes

    kernel32 = _paths._windows_dll("kernel32")
    kernel32.WriteFile.argtypes = [
        wintypes.HANDLE,
        wintypes.LPCVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    ]
    kernel32.WriteFile.restype = wintypes.BOOL
    offset = 0
    while offset < len(raw):
        chunk = raw[offset : offset + 64 * 1024]
        buffer = ctypes.create_string_buffer(chunk)
        written = wintypes.DWORD()
        if not kernel32.WriteFile(
            handle, buffer, len(chunk), ctypes.byref(written), None
        ):
            raise _paths._windows_error(_paths._windows_last_error())
        if written.value < 1:
            raise OSError(errno.EIO, "short Windows receipt write")
        offset += int(written.value)


def _windows_replace_bytes(handle: int, raw: bytes) -> None:
    from ctypes import wintypes

    kernel32 = _paths._windows_dll("kernel32")
    kernel32.SetFilePointerEx.argtypes = [
        wintypes.HANDLE,
        ctypes.c_longlong,
        ctypes.POINTER(ctypes.c_longlong),
        wintypes.DWORD,
    ]
    kernel32.SetFilePointerEx.restype = wintypes.BOOL
    if not kernel32.SetFilePointerEx(handle, 0, None, 0):
        raise _paths._windows_error(_paths._windows_last_error())
    kernel32.SetEndOfFile.argtypes = [wintypes.HANDLE]
    kernel32.SetEndOfFile.restype = wintypes.BOOL
    if not kernel32.SetEndOfFile(handle):
        raise _paths._windows_error(_paths._windows_last_error())
    _paths._windows_write(handle, raw)


def _windows_flush(handle: int) -> None:
    from ctypes import wintypes

    kernel32 = _paths._windows_dll("kernel32")
    kernel32.FlushFileBuffers.argtypes = [wintypes.HANDLE]
    kernel32.FlushFileBuffers.restype = wintypes.BOOL
    if not kernel32.FlushFileBuffers(handle):
        raise _paths._windows_error(_paths._windows_last_error())


def _windows_flush_directory(handle: int) -> None:
    """Synchronously flush directory metadata through its native handle."""

    from ctypes import wintypes

    class StatusOrPointer(ctypes.Union):
        _fields_ = [("status", wintypes.LONG), ("pointer", wintypes.LPVOID)]

    class IoStatusBlock(ctypes.Structure):
        _anonymous_ = ("result",)
        _fields_ = [
            ("result", StatusOrPointer),
            ("information", ctypes.c_size_t),
        ]

    io_status = IoStatusBlock()
    ntdll = _paths._windows_dll("ntdll")
    ntdll.NtFlushBuffersFileEx.argtypes = [
        wintypes.HANDLE,
        wintypes.ULONG,
        wintypes.LPVOID,
        wintypes.ULONG,
        ctypes.POINTER(IoStatusBlock),
    ]
    ntdll.NtFlushBuffersFileEx.restype = wintypes.LONG
    result = int(
        ntdll.NtFlushBuffersFileEx(
            handle,
            0,
            None,
            0,
            ctypes.byref(io_status),
        )
    )
    if result < 0:
        ntdll.RtlNtStatusToDosError.argtypes = [wintypes.LONG]
        ntdll.RtlNtStatusToDosError.restype = wintypes.ULONG
        number = int(ntdll.RtlNtStatusToDosError(result))
        raise _paths._windows_error(number)


def _windows_flush_directory_binding(
    parent: int,
    component: str,
    expected_identity: tuple[int, int],
) -> None:
    """Flush one exact private directory through a write-capable handle."""

    handle = 0
    try:
        handle = _windows_open_raw_child(
            parent,
            component,
            directory=True,
            write_data=True,
        )
        before = _paths._windows_handle_status(handle)
        if (
            before.identity != expected_identity
            or before.is_reparse
            or not _paths._windows_private_directory(handle, exact=True)
        ):
            raise OSError(errno.ESTALE, "Windows durability binding changed")
        _windows_flush_directory(handle)
        after = _paths._windows_handle_status(handle)
        if (
            after.identity != before.identity
            or after.is_reparse
            or not _paths._windows_private_directory(handle, exact=True)
        ):
            raise OSError(errno.ESTALE, "Windows durability binding changed")
    finally:
        if handle:
            _paths._windows_close(handle)


def _durable_windows_file_rename(
    source: int,
    parent: int,
    destination: str,
    *,
    after_rename: Callable[[], None] | None = None,
) -> None:
    """Publish a file name, expose commit state, then flush the renamed file."""

    _paths._windows_rename_handle(source, parent, destination)
    if after_rename is not None:
        after_rename()
    _windows_flush(source)


def _durable_windows_directory_rename(
    source: int,
    parent: int,
    destination: str,
    expected_identity: tuple[int, int],
    *,
    after_rename: Callable[[], None] | None = None,
) -> None:
    """Publish and flush one exact directory namespace binding."""

    _windows_rename_handle(source, parent, destination)
    if after_rename is not None:
        after_rename()
    _windows_flush_directory_binding(parent, destination, expected_identity)


def _windows_private_file(handle: int) -> bool:
    status = _paths._windows_handle_status(handle)
    if status.is_directory or status.is_reparse or status.link_count != 1:
        return False
    return _paths._windows_private_authorization(handle, exact=True)


def _windows_open_parent(root: int, components: tuple[str, ...]) -> int:
    empty = 0
    current = _paths._windows_duplicate(root)
    child = empty
    try:
        for component in components:
            child = _paths._windows_open_child(current, component, directory=True)
            previous = current
            current = empty
            _close_native(previous)
            current = child
            child = empty
        return current
    except BaseException:
        try:
            _close_all_native_descriptors(child, current)
        except BaseException:
            pass
        raise


def _windows_open_private_directory_chain(
    root: int,
    components: tuple[str, ...],
    *,
    volume: int,
    create_missing: bool = True,
) -> int:
    empty = 0
    current = _paths._windows_duplicate(root)
    child = empty
    try:
        for component in components:
            try:
                try:
                    child = _paths._windows_open_child(
                        current, component, directory=True
                    )
                except OSError as exc:
                    if not isinstance(exc, FileNotFoundError) and getattr(
                        exc, "winerror", None
                    ) not in {2, 3}:
                        raise
                    if not create_missing:
                        raise
                    child = _paths._windows_create_private_directory(current, component)
                status = _paths._windows_handle_status(child)
                if status.identity[
                    0
                ] != volume or not _paths._windows_private_directory(child, exact=True):
                    raise _error(
                        "ownership.receipt_conflict",
                        "The receipt parent is not privately owned.",
                    )
            except BaseException:
                raise
            previous = current
            current = empty
            _close_native(previous)
            current = child
            child = empty
        return current
    except BaseException:
        try:
            _close_all_native_descriptors(child, current)
        except BaseException:
            pass
        raise


def _rollback_windows_transaction(
    parent: int,
    leaf: str,
    created_handle: int,
    created_identity: tuple[int, int],
) -> None:
    """Best-effort rollback only while the created name still binds its handle."""

    current = 0
    try:
        held_status = _paths._windows_handle_status(created_handle)
        current = _windows_open_raw_child(
            parent, leaf, directory=True, delete_access=True
        )
        current_status = _paths._windows_handle_status(current)
        if (
            held_status.identity != created_identity
            or current_status.identity != created_identity
            or held_status.is_reparse
            or current_status.is_reparse
            or not _paths._windows_private_directory(current, exact=True)
        ):
            return
        _windows_delete_handle(current)
    except (ForgeError, OSError):
        return
    finally:
        if current:
            _paths._windows_close(current)


def _directory_flags() -> int:
    required = ("O_CLOEXEC", "O_DIRECTORY", "O_NOFOLLOW")
    if os.name != "posix" or any(not hasattr(os, name) for name in required):
        raise _error(
            "path.unsupported_filesystem",
            "Native ownership primitives are unavailable on this platform.",
        )
    return os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW


def _open_parent(root: int, components: tuple[str, ...]) -> int:
    empty = -1
    current = os.dup(root)
    child = empty
    try:
        for component in components:
            child = os.open(component, _directory_flags(), dir_fd=current)
            previous = current
            current = empty
            _close_native(previous)
            current = child
            child = empty
        return current
    except BaseException:
        try:
            _close_all_native_descriptors(child, current)
        except BaseException:
            pass
        raise


def _rollback_posix_transaction(
    parent: int,
    leaf: str,
    created_descriptor: int,
    created_identity: tuple[int, int],
) -> None:
    """Best-effort rollback only while the created name still binds its handle."""

    try:
        held = os.fstat(created_descriptor)
        current = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
        if (
            (held.st_dev, held.st_ino) != created_identity
            or (current.st_dev, current.st_ino) != created_identity
            or not stat.S_ISDIR(held.st_mode)
            or not stat.S_ISDIR(current.st_mode)
            or held.st_uid != os.geteuid()
            or current.st_uid != os.geteuid()
            or stat.S_IMODE(held.st_mode) != 0o700
            or stat.S_IMODE(current.st_mode) != 0o700
        ):
            return
        os.rmdir(leaf, dir_fd=parent)
    except OSError:
        return


def _exclusive_rename(parent: int, source: str, destination: str) -> None:
    for component in (source, destination):
        if (
            not component
            or component in {".", ".."}
            or "/" in component
            or "\0" in component
        ):
            raise ValueError("rename component")
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform.startswith("linux"):
        try:
            rename = libc.renameat2
        except AttributeError as exc:
            raise _error(
                "path.unsupported_filesystem", "Exclusive rename is unavailable."
            ) from exc
        rename.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        rename.restype = ctypes.c_int
        result = rename(
            parent,
            os.fsencode(source),
            parent,
            os.fsencode(destination),
            1,
        )
    elif sys.platform == "darwin":
        try:
            rename = libc.renameatx_np
        except AttributeError as exc:
            raise _error(
                "path.unsupported_filesystem", "Exclusive rename is unavailable."
            ) from exc
        rename.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        rename.restype = ctypes.c_int
        result = rename(
            parent,
            os.fsencode(source),
            parent,
            os.fsencode(destination),
            0x00000004,
        )
    else:
        raise _error("path.unsupported_filesystem", "Exclusive rename is unavailable.")
    if result:
        number = ctypes.get_errno()
        if number in {errno.EEXIST, errno.ENOTEMPTY}:
            raise FileExistsError(number, "quarantine destination exists")
        raise OSError(number, os.strerror(number))


def install_identity_digest(identity: InstallIdentity) -> str:
    """Return the full canonical identity digest used by receipt keys."""

    return _contract_install_identity_digest(identity)


def committed_receipt_reference(
    effective_marketplace_id: str, identity: InstallIdentity
) -> SafeRelativePath:
    """Build the one trusted internal reference allowed to carry a full digest key."""

    if not re.fullmatch(
        r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", effective_marketplace_id
    ) or not isinstance(identity, InstallIdentity):
        raise ValueError("receipt identity")
    raw = (
        f".zagrosi/ownership/{effective_marketplace_id}/{identity.plugin_id}/"
        f"{install_identity_digest(identity)}.json"
    )
    return _paths._validate_internal_reference(
        raw, role="committed-receipt", limits=LIMIT_POLICY
    ).unwrap()


@dataclass(frozen=True, slots=True, init=False)
class ObservedGenerationIdentity:
    effective_marketplace_id: str
    root_role: str
    identity: InstallIdentity
    path: PathProof
    manifest_digest: str
    _binding_digest: str
    _seal: object

    def __init__(
        self,
        *,
        effective_marketplace_id: str,
        root_role: str,
        identity: InstallIdentity,
        path: PathProof,
        manifest_digest: str,
        _token: object,
    ) -> None:
        if _token is not _OBSERVATION_TOKEN:
            raise TypeError(
                "ObservedGenerationIdentity is created only by verified observation"
            )
        if (
            not effective_marketplace_id
            or root_role not in {"source", "cache"}
            or not isinstance(identity, InstallIdentity)
            or not isinstance(path, PathProof)
            or _DIGEST.fullmatch(manifest_digest) is None
        ):
            raise ValueError("observed generation identity")
        object.__setattr__(self, "effective_marketplace_id", effective_marketplace_id)
        object.__setattr__(self, "root_role", root_role)
        object.__setattr__(self, "identity", identity)
        object.__setattr__(self, "path", path)
        object.__setattr__(self, "manifest_digest", manifest_digest)
        object.__setattr__(
            self,
            "_binding_digest",
            _observed_generation_binding_digest(
                effective_marketplace_id,
                root_role,
                identity,
                path,
                manifest_digest,
            ),
        )
        object.__setattr__(self, "_seal", _OBSERVATION_TOKEN)

    def __reduce__(self) -> Never:
        raise TypeError("generation observations are not serializable")


def _observed_generation_binding_digest(
    effective_marketplace_id: str,
    root_role: str,
    identity: InstallIdentity,
    path: PathProof,
    manifest_digest: str,
) -> str:
    return hashlib.sha256(
        canonical_json_bytes(
            {
                "effective_marketplace_id": effective_marketplace_id,
                "identity": identity,
                "manifest_digest": manifest_digest,
                "path_identity": path.leaf_identity,
                "path_relative": path.relative.value,
                "root_identity": path.owned_ancestor_identity,
                "root_role": root_role,
            }
        )
    ).hexdigest()


def _observed_generation_invariants(value: object) -> bool:
    if type(value) is not ObservedGenerationIdentity:
        return False
    observed = value
    try:
        return (
            observed._seal is _OBSERVATION_TOKEN
            and observed.root_role in {"source", "cache"}
            and type(observed.identity) is InstallIdentity
            and isinstance(observed.path, PathProof)
            and _DIGEST.fullmatch(observed.manifest_digest) is not None
            and observed._binding_digest
            == _observed_generation_binding_digest(
                observed.effective_marketplace_id,
                observed.root_role,
                observed.identity,
                observed.path,
                observed.manifest_digest,
            )
        )
    except (ForgeError, TypeError, ValueError):
        return False


def observe_generation_identity(
    *,
    effective_marketplace_id: str,
    root_role: str,
    identity: InstallIdentity,
    path: PathProof,
    manifest: OpenedRegularFile,
) -> Result[ObservedGenerationIdentity]:
    """Hash the exact already-opened generation manifest and seal its observation."""

    if (
        not isinstance(path, PathProof)
        or not isinstance(manifest, OpenedRegularFile)
        or type(identity) is not InstallIdentity
        or _IDENTIFIER.fullmatch(effective_marketplace_id) is None
        or root_role not in {"source", "cache"}
    ):
        return Result.failure(
            _error(
                "ownership.identity_mismatch",
                "The generation observation input is invalid.",
            )
        )
    expected_generation = (
        f"sources/{effective_marketplace_id}/{identity.plugin_id}/"
        f"{identity.install_version}/marketplace"
        if root_role == "source"
        else f"cache/{effective_marketplace_id}/{identity.plugin_id}/"
        f"{identity.install_version}"
    )
    manifest_suffix = (
        f"plugins/{identity.plugin_id}/.codex-plugin/bundle-manifest.json"
        if root_role == "source"
        else ".codex-plugin/bundle-manifest.json"
    )
    expected_manifest = f"{expected_generation}/{manifest_suffix}"
    descriptor = -1
    try:
        path_reference = _snapshot_safe_reference(path.relative)
        manifest_reference = _snapshot_safe_reference(manifest.relative)
        if (
            path_reference is None
            or manifest_reference is None
            or path_reference.value != expected_generation
            or manifest_reference.value != expected_manifest
            or path.leaf_identity is None
            or manifest.root_identity != path.owned_ancestor_identity
        ):
            raise _error(
                "ownership.identity_mismatch",
                "The generation observation does not match.",
            )
        descriptor = path._duplicate_descriptor()
        if _native_identity(descriptor) != path.leaf_identity:
            raise _error(
                "ownership.identity_mismatch",
                "The generation observation identity changed.",
            )
        raw = manifest.read_bytes(limit=256 * 1024)
        observed = ObservedGenerationIdentity(
            effective_marketplace_id=effective_marketplace_id,
            root_role=root_role,
            identity=identity,
            path=path,
            manifest_digest=hashlib.sha256(raw).hexdigest(),
            _token=_OBSERVATION_TOKEN,
        )
        if not _observed_generation_invariants(observed):
            raise _error(
                "ownership.identity_mismatch",
                "The generation observation identity changed.",
            )
        return Result.success(observed)
    except (ForgeError, OSError, TypeError, ValueError):
        return Result.failure(
            _error(
                "ownership.identity_mismatch",
                "The generation cannot be observed safely.",
            )
        )
    finally:
        if descriptor >= 0:
            _close_native(descriptor)


@dataclass(frozen=True, slots=True, init=False)
class ValidatedInstallRelation:
    """Receipt-derived selected-state authority over two observed generations."""

    _active: ActiveInstallRelation
    _config_before_snapshot_digest: str
    _config_after_snapshot_digest: str
    _source_manifest_digest: str
    _cache_manifest_digest: str
    _source_identity: tuple[int, int]
    _cache_identity: tuple[int, int]
    _receipt_identity: tuple[int, int]
    _source_observation: ObservedGenerationIdentity
    _cache_observation: ObservedGenerationIdentity
    _binding_digest: str
    _seal: object

    def __init__(
        self,
        *,
        active: ActiveInstallRelation,
        config_before_snapshot_digest: str,
        config_after_snapshot_digest: str,
        source_manifest_digest: str,
        cache_manifest_digest: str,
        source_identity: tuple[int, int],
        cache_identity: tuple[int, int],
        receipt_identity: tuple[int, int],
        source_observation: ObservedGenerationIdentity,
        cache_observation: ObservedGenerationIdentity,
        _token: object,
    ) -> None:
        if _token is not _RELATION_TOKEN:
            raise TypeError(
                "ValidatedInstallRelation is created only by receipt validation"
            )
        if type(active) is not ActiveInstallRelation:
            raise ValueError("validated install relation")
        for digest in (
            config_before_snapshot_digest,
            config_after_snapshot_digest,
            source_manifest_digest,
            cache_manifest_digest,
        ):
            if not isinstance(digest, str) or _DIGEST.fullmatch(digest) is None:
                raise ValueError("validated install relation")
        for identity in (source_identity, cache_identity, receipt_identity):
            if (
                not isinstance(identity, tuple)
                or len(identity) != 2
                or any(type(member) is not int or member < 0 for member in identity)
            ):
                raise ValueError("validated install relation")
        if (
            not _observed_generation_invariants(source_observation)
            or not _observed_generation_invariants(cache_observation)
            or source_observation.root_role != "source"
            or cache_observation.root_role != "cache"
            or source_observation.identity != active.identity
            or cache_observation.identity != active.identity
            or source_observation.manifest_digest != source_manifest_digest
            or cache_observation.manifest_digest != cache_manifest_digest
            or source_observation.path.leaf_identity != source_identity
            or cache_observation.path.leaf_identity != cache_identity
        ):
            raise ValueError("validated install relation")
        active_copy = _copy_active_install_relation(active)
        object.__setattr__(self, "_active", active_copy)
        object.__setattr__(
            self, "_config_before_snapshot_digest", config_before_snapshot_digest
        )
        object.__setattr__(
            self, "_config_after_snapshot_digest", config_after_snapshot_digest
        )
        object.__setattr__(self, "_source_manifest_digest", source_manifest_digest)
        object.__setattr__(self, "_cache_manifest_digest", cache_manifest_digest)
        object.__setattr__(self, "_source_identity", source_identity)
        object.__setattr__(self, "_cache_identity", cache_identity)
        object.__setattr__(self, "_receipt_identity", receipt_identity)
        object.__setattr__(self, "_source_observation", source_observation)
        object.__setattr__(self, "_cache_observation", cache_observation)
        object.__setattr__(
            self,
            "_binding_digest",
            _validated_relation_binding_digest(
                active_copy,
                config_before_snapshot_digest,
                config_after_snapshot_digest,
                source_manifest_digest,
                cache_manifest_digest,
                source_identity,
                cache_identity,
                receipt_identity,
            ),
        )
        object.__setattr__(self, "_seal", _RELATION_TOKEN)

    def _require_valid(self) -> None:
        try:
            expected = _validated_relation_binding_digest(
                self._active,
                self._config_before_snapshot_digest,
                self._config_after_snapshot_digest,
                self._source_manifest_digest,
                self._cache_manifest_digest,
                self._source_identity,
                self._cache_identity,
                self._receipt_identity,
            )
        except (ForgeError, TypeError, ValueError):
            expected = ""
        if self._seal is not _RELATION_TOKEN or self._binding_digest != expected:
            raise _error(
                "ownership.identity_mismatch",
                "The validated install relation changed.",
            )
        if not (
            _validated_observation_is_live(
                self._source_observation,
                expected_role="source",
                expected_identity=self._source_identity,
                expected_manifest=self._source_manifest_digest,
            )
            and _validated_observation_is_live(
                self._cache_observation,
                expected_role="cache",
                expected_identity=self._cache_identity,
                expected_manifest=self._cache_manifest_digest,
            )
            and _validated_observation_is_live(
                self._source_observation,
                expected_role="source",
                expected_identity=self._source_identity,
                expected_manifest=self._source_manifest_digest,
            )
        ):
            raise _error(
                "ownership.identity_mismatch",
                "The validated install relation containment changed.",
            )

    @property
    def active(self) -> ActiveInstallRelation:
        self._require_valid()
        return _copy_active_install_relation(self._active)

    @property
    def config_before_snapshot_digest(self) -> str:
        self._require_valid()
        return self._config_before_snapshot_digest

    @property
    def config_after_snapshot_digest(self) -> str:
        self._require_valid()
        return self._config_after_snapshot_digest

    @property
    def source_manifest_digest(self) -> str:
        self._require_valid()
        return self._source_manifest_digest

    @property
    def cache_manifest_digest(self) -> str:
        self._require_valid()
        return self._cache_manifest_digest

    @property
    def source_identity(self) -> tuple[int, int]:
        self._require_valid()
        return self._source_identity

    @property
    def cache_identity(self) -> tuple[int, int]:
        self._require_valid()
        return self._cache_identity

    @property
    def receipt_identity(self) -> tuple[int, int]:
        self._require_valid()
        return self._receipt_identity

    def __reduce__(self) -> Never:
        raise TypeError("validated install relations are not serializable")


def _copy_active_install_relation(
    relation: ActiveInstallRelation,
) -> ActiveInstallRelation:
    identity = relation.identity
    identity_copy = InstallIdentity(
        marketplace_id=identity.marketplace_id,
        plugin_id=identity.plugin_id,
        base_version=identity.base_version,
        install_version=identity.install_version,
        base_payload_digest=identity.base_payload_digest,
        rendered_payload_digest=identity.rendered_payload_digest,
        policy_digest=identity.policy_digest,
        transformation_profile=identity.transformation_profile,
        contract_versions=tuple(identity.contract_versions),
    )
    return ActiveInstallRelation(
        effective_marketplace_id=relation.effective_marketplace_id,
        identity=identity_copy,
        managed_config_projection=ManagedConfigProjection.v1(
            effective_marketplace_id=relation.effective_marketplace_id,
            plugin_id=identity_copy.plugin_id,
            source_generation=relation.source_generation,
        ),
        source_generation=relation.source_generation,
        cache_generation=relation.cache_generation,
        committed_receipt_ref=relation.committed_receipt_ref,
    )


def _validated_observation_is_live(
    observed: ObservedGenerationIdentity,
    *,
    expected_role: str,
    expected_identity: tuple[int, int],
    expected_manifest: str,
) -> bool:
    descriptor = -1
    try:
        if (
            not _observed_generation_invariants(observed)
            or observed.root_role != expected_role
            or observed.path.leaf_identity != expected_identity
            or observed.manifest_digest != expected_manifest
        ):
            return False
        descriptor = observed.path._duplicate_descriptor()
        return _native_identity(descriptor) == expected_identity
    except (ForgeError, OSError, TypeError, ValueError):
        return False
    finally:
        if descriptor >= 0:
            _close_native(descriptor)


def _validated_relation_binding_digest(
    active: ActiveInstallRelation,
    config_before_snapshot_digest: str,
    config_after_snapshot_digest: str,
    source_manifest_digest: str,
    cache_manifest_digest: str,
    source_identity: tuple[int, int],
    cache_identity: tuple[int, int],
    receipt_identity: tuple[int, int],
) -> str:
    return hashlib.sha256(
        canonical_json_bytes(
            {
                "active": active,
                "cache_identity": cache_identity,
                "cache_manifest_digest": cache_manifest_digest,
                "config_after_snapshot_digest": config_after_snapshot_digest,
                "config_before_snapshot_digest": config_before_snapshot_digest,
                "receipt_identity": receipt_identity,
                "source_identity": source_identity,
                "source_manifest_digest": source_manifest_digest,
            }
        )
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class _TransactionObservation:
    path: PathProof
    transaction_id: str
    persistent_binding: PersistentTransactionBinding | None = None


def _file_identity_invariants(value: object) -> bool:
    return (
        isinstance(value, tuple)
        and len(value) == 2
        and all(
            isinstance(component, int)
            and not isinstance(component, bool)
            and component >= 0
            for component in value
        )
    )


def _persistent_transaction_references(transaction_id: str) -> tuple[str, str, str]:
    if _PERSISTENT_TRANSACTION.fullmatch(transaction_id) is None:
        raise ValueError("persistent transaction id")
    root = f".zagrosi/{_TRANSACTION_STORE_COMPONENT}/{transaction_id}"
    components = tuple(root.split("/"))
    _destination, quarantine = _quarantine_target(
        _SafeReferenceSnapshot(root, components, root.casefold()), transaction_id
    )
    claim = (
        f".zagrosi/{_TRANSACTION_STORE_COMPONENT}/"
        f"{_TRANSACTION_CLAIMS_COMPONENT}/{transaction_id}.json"
    )
    return root, quarantine, claim


@dataclass(frozen=True, slots=True, init=False)
class PersistentTransactionBinding:
    """Persistent data projection; never deletion authority by itself."""

    transaction_id: str
    root_relative: str
    quarantine_relative: str
    claim_relative: str
    plugins_identity: tuple[int, int]
    control_identity: tuple[int, int]
    store_identity: tuple[int, int]
    claims_identity: tuple[int, int]
    transaction_identity: tuple[int, int]
    claim_identity: tuple[int, int]
    claim_digest: str
    _seal: object

    def __init__(
        self,
        *,
        transaction_id: str,
        root_relative: str,
        quarantine_relative: str,
        claim_relative: str,
        plugins_identity: tuple[int, int],
        control_identity: tuple[int, int],
        store_identity: tuple[int, int],
        claims_identity: tuple[int, int],
        transaction_identity: tuple[int, int],
        claim_identity: tuple[int, int],
        claim_digest: str,
        _token: object,
    ) -> None:
        expected_root, expected_quarantine, expected_claim = (
            _persistent_transaction_references(transaction_id)
        )
        identities = (
            plugins_identity,
            control_identity,
            store_identity,
            claims_identity,
            transaction_identity,
            claim_identity,
        )
        if (
            _token is not _PERSISTENT_BINDING_TOKEN
            or root_relative != expected_root
            or quarantine_relative != expected_quarantine
            or claim_relative != expected_claim
            or not all(_file_identity_invariants(identity) for identity in identities)
            or _DIGEST.fullmatch(claim_digest) is None
        ):
            raise TypeError(
                "persistent transaction bindings are loaded only by ownership authority"
            )
        object.__setattr__(self, "transaction_id", transaction_id)
        object.__setattr__(self, "root_relative", root_relative)
        object.__setattr__(self, "quarantine_relative", quarantine_relative)
        object.__setattr__(self, "claim_relative", claim_relative)
        object.__setattr__(self, "plugins_identity", plugins_identity)
        object.__setattr__(self, "control_identity", control_identity)
        object.__setattr__(self, "store_identity", store_identity)
        object.__setattr__(self, "claims_identity", claims_identity)
        object.__setattr__(self, "transaction_identity", transaction_identity)
        object.__setattr__(self, "claim_identity", claim_identity)
        object.__setattr__(self, "claim_digest", claim_digest)
        object.__setattr__(self, "_seal", _PERSISTENT_BINDING_TOKEN)

    def canonical_projection(self) -> Mapping[str, object]:
        return {
            "claim_digest": self.claim_digest,
            "claim_identity": self.claim_identity,
            "claim_relative": self.claim_relative,
            "claims_identity": self.claims_identity,
            "control_identity": self.control_identity,
            "plugins_identity": self.plugins_identity,
            "quarantine_relative": self.quarantine_relative,
            "root_relative": self.root_relative,
            "store_identity": self.store_identity,
            "transaction_id": self.transaction_id,
            "transaction_identity": self.transaction_identity,
        }


class TransactionLocation(str, Enum):
    LIVE = "live"
    QUARANTINED = "quarantined"
    REMOVED = "removed"


@dataclass(frozen=True, slots=True, init=False)
class PendingTransactionObservation:
    """Effect-free restart observation; never mutation or cleanup authority."""

    binding: PersistentTransactionBinding
    location: TransactionLocation
    journal_relative: str
    _seal: object

    def __init__(
        self,
        *,
        binding: PersistentTransactionBinding,
        location: TransactionLocation,
        journal_relative: str,
        _token: object,
    ) -> None:
        valid_relative = (
            location is TransactionLocation.LIVE
            and journal_relative == binding.root_relative
        ) or (
            location is TransactionLocation.QUARANTINED
            and _persistent_cleanup_reference_is_valid(binding, journal_relative)
        )
        if (
            _token is not _PENDING_TRANSACTION_OBSERVATION_TOKEN
            or not _persistent_binding_invariants(binding)
            or not valid_relative
        ):
            raise TypeError(
                "pending transactions are observed only by ownership authority"
            )
        object.__setattr__(self, "binding", binding)
        object.__setattr__(self, "location", location)
        object.__setattr__(self, "journal_relative", journal_relative)
        object.__setattr__(self, "_seal", _PENDING_TRANSACTION_OBSERVATION_TOKEN)

    def __reduce__(self) -> Never:
        raise TypeError("pending transaction observations are not serializable")


class _RecoveryCleanupPhase(str, Enum):
    AUTHORIZED = "AUTHORIZED"
    FINALIZING = "FINALIZING"
    COMPLETE = "COMPLETE"


@dataclass(frozen=True, slots=True, init=False)
class RecoveryCleanupAuthorization:
    """Inert durable terminal evidence; never deletion authority by itself."""

    binding: PersistentTransactionBinding
    location: TransactionLocation
    journal_relative: str
    journal_access_digest: str
    journal_evidence_digest: str
    journal_head_sequence: int
    journal_head_record_digest: str
    transaction_binding_digest: str
    delete_component: str
    authorization_digest: str
    _component: str
    _identity: tuple[int, int]
    _raw: bytes
    _seal: object

    def __init__(
        self,
        *,
        binding: PersistentTransactionBinding,
        location: TransactionLocation,
        journal_relative: str,
        journal_access_digest: str,
        journal_evidence_digest: str,
        journal_head_sequence: int,
        journal_head_record_digest: str,
        transaction_binding_digest: str,
        delete_component: str,
        authorization_digest: str,
        _component: str,
        _identity: tuple[int, int],
        _raw: bytes,
        _token: object,
    ) -> None:
        if _token is not _RECOVERY_CLEANUP_AUTHORIZATION_TOKEN:
            raise TypeError(
                "recovery cleanup authorizations are loaded only by ownership authority"
            )
        object.__setattr__(self, "binding", binding)
        object.__setattr__(self, "location", location)
        object.__setattr__(self, "journal_relative", journal_relative)
        object.__setattr__(self, "journal_access_digest", journal_access_digest)
        object.__setattr__(self, "journal_evidence_digest", journal_evidence_digest)
        object.__setattr__(self, "journal_head_sequence", journal_head_sequence)
        object.__setattr__(
            self,
            "journal_head_record_digest",
            journal_head_record_digest,
        )
        object.__setattr__(
            self,
            "transaction_binding_digest",
            transaction_binding_digest,
        )
        object.__setattr__(self, "delete_component", delete_component)
        object.__setattr__(self, "authorization_digest", authorization_digest)
        object.__setattr__(self, "_component", _component)
        object.__setattr__(self, "_identity", _identity)
        object.__setattr__(self, "_raw", _raw)
        object.__setattr__(self, "_seal", _RECOVERY_CLEANUP_AUTHORIZATION_TOKEN)
        self._require_valid()

    @property
    def transaction_id(self) -> str:
        return self.binding.transaction_id

    def _require_valid(self) -> None:
        if not _recovery_cleanup_authorization_invariants(self):
            raise TypeError("recovery cleanup authorization changed")

    def __reduce__(self) -> Never:
        raise TypeError("recovery cleanup authorizations are not serializable")


@dataclass(frozen=True, slots=True)
class _RecoveryCleanupObservationCapture:
    transaction_id: str
    authorization_identity: tuple[int, int]
    authorization_active: bool
    phase: str
    current_reference: str | None
    finalizing_record_digest: str | None
    finalizing_identity: tuple[int, int] | None
    finalizing_retired: bool | None
    complete_record_digest: str | None
    complete_identity: tuple[int, int] | None
    complete_retired: bool | None
    cleanup_intent_digest: str | None
    cleanup_intent_identity: tuple[int, int] | None
    cleanup_complete_digest: str | None
    cleanup_complete_identity: tuple[int, int] | None


@dataclass(frozen=True, slots=True, init=False)
class RecoveryCleanupObservation:
    """Sealed effect-free observation of one exact recovery cleanup WAL state."""

    authorization: RecoveryCleanupAuthorization
    phase: str
    current_reference: str | None
    observation_digest: str
    _capture: _RecoveryCleanupObservationCapture
    _seal: object

    def __init__(
        self,
        *,
        authorization: RecoveryCleanupAuthorization,
        capture: _RecoveryCleanupObservationCapture,
        _token: object,
    ) -> None:
        if (
            _token is not _RECOVERY_CLEANUP_OBSERVATION_TOKEN
            or not _recovery_cleanup_observation_capture_invariants(
                authorization,
                capture,
            )
        ):
            raise TypeError(
                "recovery cleanup observations are loaded only by ownership authority"
            )
        digest = _recovery_cleanup_observation_digest(authorization, capture)
        object.__setattr__(self, "authorization", authorization)
        object.__setattr__(self, "phase", capture.phase)
        object.__setattr__(self, "current_reference", capture.current_reference)
        object.__setattr__(self, "observation_digest", digest)
        object.__setattr__(self, "_capture", capture)
        object.__setattr__(self, "_seal", _RECOVERY_CLEANUP_OBSERVATION_TOKEN)

    @property
    def transaction_id(self) -> str:
        return self.authorization.transaction_id

    def _require_valid(self) -> None:
        try:
            valid = _recovery_cleanup_observation_capture_invariants(
                self.authorization,
                self._capture,
            )
            expected = _recovery_cleanup_observation_digest(
                self.authorization,
                self._capture,
            )
        except (AttributeError, ForgeError, TypeError, ValueError):
            valid = False
            expected = ""
        if (
            not valid
            or self._seal is not _RECOVERY_CLEANUP_OBSERVATION_TOKEN
            or self.phase != self._capture.phase
            or self.current_reference != self._capture.current_reference
            or _DIGEST.fullmatch(self.observation_digest) is None
            or self.observation_digest != expected
        ):
            raise TypeError("recovery cleanup observation changed")

    def __reduce__(self) -> Never:
        raise TypeError("recovery cleanup observations are not serializable")


@dataclass(frozen=True, slots=True)
class _RecoveryGenerationCapture:
    relative_path: str
    parent_identity: tuple[int, int]
    leaf_identity: tuple[int, int]
    manifest_relative: str
    manifest_parent_identity: tuple[int, int]
    manifest_identity: tuple[int, int]
    manifest_digest: str


@dataclass(frozen=True, slots=True)
class _RecoveryFinalizationCapture:
    transaction_id: str
    journal_state: str
    journal_access_digest: str
    journal_evidence_digest: str
    journal_head_sequence: int
    journal_head_record_digest: str
    source: _RecoveryGenerationCapture
    cache: _RecoveryGenerationCapture
    receipt_status: str
    receipt_relative: str
    receipt_control_identity: tuple[int, int]
    receipt_existing_depth: int
    receipt_ancestor_identity: tuple[int, int]
    receipt_parent_identity: tuple[int, int] | None
    receipt_identity: tuple[int, int] | None
    receipt_digest: str | None
    prepared_receipt_digest: str


def _recovery_generation_capture_invariants(
    capture: object,
) -> bool:
    return (
        type(capture) is _RecoveryGenerationCapture
        and type(capture.relative_path) is str
        and bool(capture.relative_path)
        and _file_identity_invariants(capture.parent_identity)
        and _file_identity_invariants(capture.leaf_identity)
        and type(capture.manifest_relative) is str
        and capture.manifest_relative.startswith(f"{capture.relative_path}/")
        and _file_identity_invariants(capture.manifest_parent_identity)
        and _file_identity_invariants(capture.manifest_identity)
        and type(capture.manifest_digest) is str
        and _DIGEST.fullmatch(capture.manifest_digest) is not None
    )


def _recovery_finalization_capture_invariants(
    capture: object,
) -> bool:
    if (
        type(capture) is not _RecoveryFinalizationCapture
        or _PERSISTENT_TRANSACTION.fullmatch(capture.transaction_id) is None
        or capture.journal_state
        not in {"COMMIT_INTENT", "CONFIG_COMMITTED", "RECEIPT_COMMITTED"}
        or _DIGEST.fullmatch(capture.journal_access_digest) is None
        or _DIGEST.fullmatch(capture.journal_evidence_digest) is None
        or type(capture.journal_head_sequence) is not int
        or capture.journal_head_sequence < 0
        or _DIGEST.fullmatch(capture.journal_head_record_digest) is None
        or not _recovery_generation_capture_invariants(capture.source)
        or not _recovery_generation_capture_invariants(capture.cache)
        or capture.source.relative_path == capture.cache.relative_path
        or capture.receipt_status not in {"absent", "matching"}
        or type(capture.receipt_relative) is not str
        or not capture.receipt_relative.startswith(".zagrosi/")
        or not _file_identity_invariants(capture.receipt_control_identity)
        or type(capture.receipt_existing_depth) is not int
        or capture.receipt_existing_depth < 0
        or not _file_identity_invariants(capture.receipt_ancestor_identity)
        or _DIGEST.fullmatch(capture.prepared_receipt_digest) is None
    ):
        return False
    components = capture.receipt_relative.split("/")
    if (
        any(not component for component in components)
        or len(components) < 3
        or capture.receipt_existing_depth > len(components) - 2
    ):
        return False
    if capture.receipt_status == "absent":
        return (
            capture.receipt_parent_identity is None
            and capture.receipt_identity is None
            and capture.receipt_digest is None
        )
    return (
        capture.receipt_existing_depth == len(components) - 2
        and _file_identity_invariants(capture.receipt_parent_identity)
        and capture.receipt_ancestor_identity == capture.receipt_parent_identity
        and _file_identity_invariants(capture.receipt_identity)
        and type(capture.receipt_digest) is str
        and capture.receipt_digest == capture.prepared_receipt_digest
    )


def _recovery_finalization_projection(
    capture: _RecoveryFinalizationCapture,
) -> dict[str, object]:
    def generation(value: _RecoveryGenerationCapture) -> dict[str, object]:
        return {
            "leaf_identity": value.leaf_identity,
            "manifest_digest": value.manifest_digest,
            "manifest_identity": value.manifest_identity,
            "manifest_parent_identity": value.manifest_parent_identity,
            "manifest_relative": value.manifest_relative,
            "parent_identity": value.parent_identity,
            "relative_path": value.relative_path,
        }

    return {
        "cache": generation(capture.cache),
        "journal_access_digest": capture.journal_access_digest,
        "journal_evidence_digest": capture.journal_evidence_digest,
        "journal_head_record_digest": capture.journal_head_record_digest,
        "journal_head_sequence": capture.journal_head_sequence,
        "journal_state": capture.journal_state,
        "prepared_receipt_digest": capture.prepared_receipt_digest,
        "receipt_ancestor_identity": capture.receipt_ancestor_identity,
        "receipt_control_identity": capture.receipt_control_identity,
        "receipt_digest": capture.receipt_digest,
        "receipt_existing_depth": capture.receipt_existing_depth,
        "receipt_identity": capture.receipt_identity,
        "receipt_parent_identity": capture.receipt_parent_identity,
        "receipt_relative": capture.receipt_relative,
        "receipt_status": capture.receipt_status,
        "source": generation(capture.source),
        "transaction_id": capture.transaction_id,
    }


def _recovery_finalization_observation_digest(
    capture: _RecoveryFinalizationCapture,
) -> str:
    return hashlib.sha256(
        canonical_json_bytes(_recovery_finalization_projection(capture))
    ).hexdigest()


@dataclass(frozen=True, slots=True, init=False)
class RecoveryFinalizationObservation:
    """Sealed effect-free evidence for one exact committed recovery candidate."""

    transaction_id: str
    journal_state: str
    receipt_status: str
    observation_digest: str
    _capture: _RecoveryFinalizationCapture
    _seal: object

    def __init__(
        self,
        *,
        capture: _RecoveryFinalizationCapture,
        _token: object,
    ) -> None:
        if (
            _token is not _RECOVERY_FINALIZATION_OBSERVATION_TOKEN
            or not _recovery_finalization_capture_invariants(capture)
        ):
            raise TypeError(
                "recovery finalization observations are loaded only by ownership authority"
            )
        object.__setattr__(self, "transaction_id", capture.transaction_id)
        object.__setattr__(self, "journal_state", capture.journal_state)
        object.__setattr__(self, "receipt_status", capture.receipt_status)
        object.__setattr__(
            self,
            "observation_digest",
            _recovery_finalization_observation_digest(capture),
        )
        object.__setattr__(self, "_capture", capture)
        object.__setattr__(
            self,
            "_seal",
            _RECOVERY_FINALIZATION_OBSERVATION_TOKEN,
        )

    def _require_valid(self) -> None:
        try:
            valid = _recovery_finalization_capture_invariants(self._capture)
            expected = _recovery_finalization_observation_digest(self._capture)
        except (AttributeError, TypeError, ValueError):
            valid = False
            expected = ""
        if (
            not valid
            or self._seal is not _RECOVERY_FINALIZATION_OBSERVATION_TOKEN
            or self.transaction_id != self._capture.transaction_id
            or self.journal_state != self._capture.journal_state
            or self.receipt_status != self._capture.receipt_status
            or self.observation_digest != expected
        ):
            raise TypeError("recovery finalization observation changed")

    def __reduce__(self) -> Never:
        raise TypeError("recovery finalization observations are not serializable")


class TransactionPathClaim:
    _consumed: bool
    _identity: tuple[int, int]
    _lock: LockType
    _persistent_binding: PersistentTransactionBinding | None
    _relative: SafeRelativePath
    _root_identity: tuple[int, int]
    _transaction_id: str

    __slots__ = (
        "_consumed",
        "_identity",
        "_lock",
        "_persistent_binding",
        "_relative",
        "_root_identity",
        "_transaction_id",
    )

    def __init__(
        self,
        transaction_id: str,
        relative: SafeRelativePath,
        root_identity: tuple[int, int],
        identity: tuple[int, int],
        *,
        persistent_binding: PersistentTransactionBinding | None = None,
        _token: object,
    ) -> None:
        if _token is not _CAPABILITY_TOKEN or (
            persistent_binding is not None
            and (
                not _persistent_binding_invariants(persistent_binding)
                or persistent_binding.transaction_id != transaction_id
                or persistent_binding.root_relative != relative.value
                or persistent_binding.plugins_identity != root_identity
                or persistent_binding.transaction_identity != identity
            )
        ):
            raise TypeError(
                "TransactionPathClaim is created only by exclusive creation"
            )
        object.__setattr__(self, "_transaction_id", transaction_id)
        object.__setattr__(self, "_relative", relative)
        object.__setattr__(self, "_root_identity", root_identity)
        object.__setattr__(self, "_identity", identity)
        object.__setattr__(self, "_persistent_binding", persistent_binding)
        object.__setattr__(self, "_consumed", False)
        object.__setattr__(self, "_lock", Lock())

    @property
    def transaction_id(self) -> str:
        return self._transaction_id

    @property
    def relative(self) -> SafeRelativePath:
        return self._relative

    @property
    def root_identity(self) -> tuple[int, int]:
        return self._root_identity

    @property
    def identity(self) -> tuple[int, int]:
        return self._identity

    def _consume(self) -> bool:
        with self._lock:
            if self._consumed:
                return False
            object.__setattr__(self, "_consumed", True)
            return True

    def __setattr__(self, _name: str, _value: object) -> Never:
        raise AttributeError("transaction ownership claims are read-only")

    def __reduce__(self) -> Never:
        raise TypeError("ownership capabilities are not serializable")


@dataclass(frozen=True, slots=True, init=False)
class PersistentTransactionRoot:
    binding: PersistentTransactionBinding
    claim: TransactionPathClaim

    def __init__(
        self,
        binding: PersistentTransactionBinding,
        claim: TransactionPathClaim,
        *,
        _token: object,
    ) -> None:
        if (
            _token is not _PERSISTENT_ROOT_TOKEN
            or not _persistent_binding_invariants(binding)
            or not isinstance(claim, TransactionPathClaim)
            or claim.transaction_id != binding.transaction_id
            or claim.relative.value != binding.root_relative
            or claim.root_identity != binding.plugins_identity
            or claim.identity != binding.transaction_identity
            or claim._persistent_binding != binding
        ):
            raise TypeError(
                "persistent transaction roots are created only by ownership authority"
            )
        object.__setattr__(self, "binding", binding)
        object.__setattr__(self, "claim", claim)

    def __reduce__(self) -> Never:
        raise TypeError("ownership capabilities are not serializable")


@dataclass(frozen=True, slots=True, init=False)
class LegacyInstallCatalog:
    """Digest-bound installed authority for inert legacy recognition."""

    marketplace_id: str
    source_type: str
    source_leaf: str
    plugin_key: str
    cache_pattern: str
    catalog_digest: str
    _seal: object

    def __init__(
        self,
        *,
        marketplace_id: str,
        source_type: str,
        source_leaf: str,
        plugin_key: str,
        cache_pattern: str,
        catalog_digest: str,
        _token: object,
    ) -> None:
        if _token is not _LEGACY_TOKEN:
            raise TypeError(
                "LegacyInstallCatalog is loaded only from installed resources"
            )
        if (
            marketplace_id != "zagrosi"
            or source_type != "local"
            or source_leaf != _PLUGIN_ID
            or plugin_key != f"{_PLUGIN_ID}@{marketplace_id}"
            or cache_pattern != f"cache/{marketplace_id}/{_PLUGIN_ID}/<base-version>"
            or catalog_digest != _LEGACY_CATALOG_RECORD_DIGEST
        ):
            raise ValueError("legacy install catalog")
        object.__setattr__(self, "marketplace_id", marketplace_id)
        object.__setattr__(self, "source_type", source_type)
        object.__setattr__(self, "source_leaf", source_leaf)
        object.__setattr__(self, "plugin_key", plugin_key)
        object.__setattr__(self, "cache_pattern", cache_pattern)
        object.__setattr__(self, "catalog_digest", catalog_digest)
        object.__setattr__(self, "_seal", _LEGACY_TOKEN)

    def __reduce__(self) -> Never:
        raise TypeError("legacy install catalogs are not serializable")


def _legacy_catalog_invariants(value: object) -> bool:
    if type(value) is not LegacyInstallCatalog:
        return False
    catalog = value
    return (
        catalog._seal is _LEGACY_TOKEN
        and catalog.marketplace_id == "zagrosi"
        and catalog.source_type == "local"
        and catalog.source_leaf == _PLUGIN_ID
        and catalog.plugin_key == f"{_PLUGIN_ID}@zagrosi"
        and catalog.cache_pattern == f"cache/zagrosi/{_PLUGIN_ID}/<base-version>"
        and catalog.catalog_digest == _LEGACY_CATALOG_RECORD_DIGEST
    )


@dataclass(frozen=True, slots=True, init=False)
class LegacyRecognition:
    """Inert exact-match evidence; never deletion or mutation authority."""

    marketplace_id: str
    cache_relative: str
    base_version: str
    catalog_digest: str
    projection_digest: str
    _seal: object

    def __init__(
        self,
        *,
        marketplace_id: str,
        cache_relative: str,
        base_version: str,
        catalog_digest: str,
        projection_digest: str,
        _token: object,
    ) -> None:
        if _token is not _LEGACY_TOKEN:
            raise TypeError("LegacyRecognition is created only by the legacy matcher")
        if (
            marketplace_id != "zagrosi"
            or not cache_relative
            or _RELEASE_VERSION.fullmatch(base_version) is None
            or _DIGEST.fullmatch(catalog_digest) is None
            or _DIGEST.fullmatch(projection_digest) is None
        ):
            raise ValueError("legacy recognition")
        object.__setattr__(self, "marketplace_id", marketplace_id)
        object.__setattr__(self, "cache_relative", cache_relative)
        object.__setattr__(self, "base_version", base_version)
        object.__setattr__(self, "catalog_digest", catalog_digest)
        object.__setattr__(self, "projection_digest", projection_digest)
        object.__setattr__(self, "_seal", _LEGACY_TOKEN)

    def __reduce__(self) -> Never:
        raise TypeError("legacy recognitions are not serializable")


class OwnershipProof:
    _closed: bool
    _identity: tuple[int, int]
    _lock: LockType
    _namespace: _paths._NamespaceCapability | None
    _observed: ObservedGenerationIdentity | _TransactionObservation
    _relative: SafeRelativePath
    _root: int
    _root_identity: tuple[int, int]
    _used: bool

    __slots__ = (
        "_closed",
        "_identity",
        "_lock",
        "_namespace",
        "_observed",
        "_relative",
        "_root",
        "_root_identity",
        "_used",
    )

    def __init__(
        self,
        root_descriptor: int,
        namespace: _paths._NamespaceCapability,
        relative: SafeRelativePath,
        identity: tuple[int, int],
        observed: ObservedGenerationIdentity | _TransactionObservation,
        *,
        _token: object,
    ) -> None:
        if _token is not _CAPABILITY_TOKEN:
            raise TypeError("OwnershipProof is created only by ownership validation")
        object.__setattr__(self, "_root", root_descriptor)
        object.__setattr__(self, "_namespace", namespace)
        object.__setattr__(
            self, "_root_identity", observed.path.owned_ancestor_identity
        )
        object.__setattr__(self, "_relative", relative)
        object.__setattr__(self, "_identity", identity)
        object.__setattr__(self, "_observed", observed)
        object.__setattr__(self, "_used", False)
        object.__setattr__(self, "_closed", False)
        object.__setattr__(self, "_lock", Lock())

    @property
    def relative(self) -> SafeRelativePath:
        return self._relative

    @property
    def identity(self) -> tuple[int, int]:
        return self._identity

    @property
    def observed(self) -> ObservedGenerationIdentity | _TransactionObservation:
        return self._observed

    def quarantine_leaf(self, transaction_id: str) -> str:
        if _TRANSACTION.fullmatch(transaction_id) is None:
            raise ValueError("transaction_id")
        reference = _snapshot_safe_reference(self.relative)
        if reference is None:
            raise ValueError("ownership reference")
        domain = f"{transaction_id}\0{reference.value}".encode()
        return f".zagrosi-quarantine-{hashlib.sha256(domain).hexdigest()[:24]}"

    def _take_authority(self) -> tuple[int, _paths._NamespaceCapability]:
        with self._lock:
            if self._used:
                raise _error(
                    "ownership.already_quarantined",
                    "The ownership proof was already used.",
                )
            if self._closed:
                raise _error("ownership.unowned", "The ownership proof is closed.")
            namespace = self._namespace
            if namespace is None:
                raise _error("ownership.unowned", "The ownership proof is closed.")
            object.__setattr__(self, "_used", True)
            descriptor = self._root
            object.__setattr__(self, "_root", -1)
            object.__setattr__(self, "_namespace", None)
            object.__setattr__(self, "_closed", True)
            if namespace._validate_namespace_binding():
                return descriptor, namespace
        _close_native(descriptor)
        namespace.close()
        raise _error(
            "ownership.identity_mismatch",
            "The ownership proof containment changed.",
        )

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                _close_native(self._root)
                namespace = self._namespace
                if namespace is not None:
                    namespace.close()
                object.__setattr__(self, "_root", -1)
                object.__setattr__(self, "_namespace", None)
                object.__setattr__(self, "_closed", True)

    def __setattr__(self, _name: str, _value: object) -> Never:
        raise AttributeError("ownership proofs are read-only")

    def __reduce__(self) -> Never:
        raise TypeError("ownership capabilities are not serializable")


class QuarantineTicket:
    _binding: PersistentTransactionBinding | None
    _closed: bool
    _identity: tuple[int, int]
    _lock: LockType
    _namespace: _paths._NamespaceCapability | None
    _recovery_reference: str
    _root: int
    _root_identity: tuple[int, int]
    _used: bool

    __slots__ = (
        "_binding",
        "_closed",
        "_identity",
        "_lock",
        "_namespace",
        "_recovery_reference",
        "_root",
        "_root_identity",
        "_used",
    )

    def __init__(
        self,
        root_descriptor: int,
        namespace: _paths._NamespaceCapability,
        recovery_reference: str,
        identity: tuple[int, int],
        root_identity: tuple[int, int],
        *,
        binding: PersistentTransactionBinding | None = None,
        _token: object,
    ) -> None:
        if _token is not _CAPABILITY_TOKEN or (
            binding is not None
            and (
                not _persistent_binding_invariants(binding)
                or not _persistent_cleanup_reference_is_valid(
                    binding,
                    recovery_reference,
                )
                or binding.transaction_identity != identity
                or binding.plugins_identity != root_identity
            )
        ):
            raise TypeError("QuarantineTicket is created only by quarantine_owned")
        object.__setattr__(self, "_binding", binding)
        object.__setattr__(self, "_root", root_descriptor)
        object.__setattr__(self, "_namespace", namespace)
        object.__setattr__(self, "_recovery_reference", recovery_reference)
        object.__setattr__(self, "_identity", identity)
        object.__setattr__(self, "_root_identity", root_identity)
        object.__setattr__(self, "_used", False)
        object.__setattr__(self, "_closed", False)
        object.__setattr__(self, "_lock", Lock())

    @property
    def recovery_reference(self) -> str:
        return self._recovery_reference

    def _take_authority(self) -> tuple[int, _paths._NamespaceCapability]:
        with self._lock:
            if self._closed or self._used:
                raise _error(
                    "ownership.cleanup_incomplete",
                    "The quarantine ticket was already used.",
                )
            namespace = self._namespace
            if namespace is None:
                raise _error(
                    "ownership.cleanup_incomplete",
                    "The quarantine ticket was already used.",
                )
            object.__setattr__(self, "_used", True)
            object.__setattr__(self, "_closed", True)
            descriptor = self._root
            object.__setattr__(self, "_root", -1)
            object.__setattr__(self, "_namespace", None)
            if namespace._validate_namespace_binding():
                return descriptor, namespace
        _close_native(descriptor)
        namespace.close()
        raise _error(
            "ownership.cleanup_incomplete",
            "The quarantine containment changed.",
            recovery=(self._recovery_reference,),
        )

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                _close_native(self._root)
                namespace = self._namespace
                if namespace is not None:
                    namespace.close()
                object.__setattr__(self, "_root", -1)
                object.__setattr__(self, "_namespace", None)
                object.__setattr__(self, "_closed", True)

    def __setattr__(self, _name: str, _value: object) -> Never:
        raise AttributeError("quarantine tickets are read-only")

    def __reduce__(self) -> Never:
        raise TypeError("ownership capabilities are not serializable")


@dataclass(frozen=True, slots=True, init=False)
class ReboundTransaction:
    location: TransactionLocation
    binding: PersistentTransactionBinding
    claim: TransactionPathClaim | None
    ticket: QuarantineTicket | None

    def __init__(
        self,
        *,
        location: TransactionLocation,
        binding: PersistentTransactionBinding,
        claim: TransactionPathClaim | None,
        ticket: QuarantineTicket | None,
        _token: object,
    ) -> None:
        valid_payload = (
            (
                location is TransactionLocation.LIVE
                and isinstance(claim, TransactionPathClaim)
                and ticket is None
            )
            or (
                location is TransactionLocation.QUARANTINED
                and claim is None
                and isinstance(ticket, QuarantineTicket)
            )
            or (
                location is TransactionLocation.REMOVED
                and claim is None
                and ticket is None
            )
        )
        if (
            _token is not _REBOUND_TRANSACTION_TOKEN
            or not _persistent_binding_invariants(binding)
            or not valid_payload
        ):
            raise TypeError(
                "rebound transactions are created only by ownership authority"
            )
        object.__setattr__(self, "location", location)
        object.__setattr__(self, "binding", binding)
        object.__setattr__(self, "claim", claim)
        object.__setattr__(self, "ticket", ticket)

    def close(self) -> None:
        if self.ticket is not None:
            self.ticket.close()

    def __enter__(self) -> ReboundTransaction:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def __reduce__(self) -> Never:
        raise TypeError("ownership capabilities are not serializable")


@dataclass(frozen=True, slots=True)
class CleanupResult:
    removed: bool
    recovery_reference: str


@dataclass(frozen=True, slots=True)
class ReceiptPublication:
    reference: SafeRelativePath
    created: bool


@dataclass(slots=True)
class _TransactionStore:
    control: int
    store: int
    claims: int
    plugins_identity: tuple[int, int]
    control_identity: tuple[int, int]
    store_identity: tuple[int, int]
    claims_identity: tuple[int, int]
    windows: bool

    def close(self) -> None:
        close = _paths._windows_close if self.windows else os.close
        empty = 0 if self.windows else -1
        for name in ("claims", "store", "control"):
            descriptor = cast(int, getattr(self, name))
            if descriptor != empty:
                close(descriptor)
                setattr(self, name, empty)


def _transaction_store_namespace_is_valid(store: _TransactionStore) -> bool:
    if store.windows:
        return _paths._windows_namespace_binds(
            store.control,
            _TRANSACTION_STORE_COMPONENT,
            store.store_identity,
        ) and _paths._windows_namespace_binds(
            store.store,
            _TRANSACTION_CLAIMS_COMPONENT,
            store.claims_identity,
        )
    return _paths._posix_namespace_binds(
        store.control,
        _TRANSACTION_STORE_COMPONENT,
        store.store_identity,
    ) and _paths._posix_namespace_binds(
        store.store,
        _TRANSACTION_CLAIMS_COMPONENT,
        store.claims_identity,
    )


def _private_record_name_binds(
    parent: int,
    component: str,
    expected_identity: tuple[int, int],
    *,
    expected_raw: bytes,
    windows: bool,
) -> bool:
    try:
        if windows:
            observed_raw, observed_identity = _read_windows_private_record(
                parent,
                component,
                volume=expected_identity[0],
            )
        else:
            observed_raw, observed_identity = _read_posix_private_record(
                parent,
                component,
                device=expected_identity[0],
            )
        return observed_identity == expected_identity and observed_raw == expected_raw
    except (ForgeError, OSError):
        return False


def _transaction_store_record_bytes(
    *,
    plugins_identity: tuple[int, int],
    control_identity: tuple[int, int],
    store_identity: tuple[int, int],
    claims_identity: tuple[int, int],
) -> bytes:
    body: dict[str, object] = {
        "authority": "zagrosi-forge-transaction-authority-v1",
        "claims_identity": claims_identity,
        "control_identity": control_identity,
        "minimum_reader_version": _WRITER_VERSION,
        "plugins_identity": plugins_identity,
        "record_kind": "persistent-transaction-store",
        "schema_digest": _TRANSACTION_STORE_SCHEMA_DIGEST,
        "schema_version": "1.0",
        "store_identity": store_identity,
        "writer_version": _WRITER_VERSION,
    }
    body["record_digest"] = hashlib.sha256(canonical_json_bytes(body)).hexdigest()
    return canonical_json_bytes(body, final_newline=True)


def _valid_transaction_store_record(
    raw: bytes,
    *,
    plugins_identity: tuple[int, int],
    control_identity: tuple[int, int],
    store_identity: tuple[int, int],
    claims_identity: tuple[int, int],
) -> bool:
    try:
        decode_persistent_record(raw, supported_major=1, reader_version=_WRITER_VERSION)
    except ForgeError:
        return False
    return raw == _transaction_store_record_bytes(
        plugins_identity=plugins_identity,
        control_identity=control_identity,
        store_identity=store_identity,
        claims_identity=claims_identity,
    )


def _transaction_creation_intent_component(transaction_id: str) -> str:
    if _PERSISTENT_TRANSACTION.fullmatch(transaction_id) is None:
        raise ValueError("persistent transaction id")
    return f".{transaction_id}.create.json"


def _transaction_creation_reservation_component(transaction_id: str) -> str:
    if _PERSISTENT_TRANSACTION.fullmatch(transaction_id) is None:
        raise ValueError("persistent transaction id")
    return f".{transaction_id}.reserve.json"


def _transaction_cleanup_component(transaction_id: str, *, complete: bool) -> str:
    if _PERSISTENT_TRANSACTION.fullmatch(transaction_id) is None:
        raise ValueError("persistent transaction id")
    suffix = "removed" if complete else "removing"
    return f"{transaction_id}.{suffix}.json"


def _recovery_cleanup_component(
    transaction_id: str,
    phase: _RecoveryCleanupPhase,
) -> str:
    if (
        _PERSISTENT_TRANSACTION.fullmatch(transaction_id) is None
        or type(phase) is not _RecoveryCleanupPhase
    ):
        raise ValueError("recovery cleanup phase")
    suffix = {
        _RecoveryCleanupPhase.AUTHORIZED: "auth",
        _RecoveryCleanupPhase.FINALIZING: "final",
        _RecoveryCleanupPhase.COMPLETE: "done",
    }[phase]
    return f"{transaction_id}.rc-{suffix}.json"


def _persistent_cleanup_reference_is_valid(
    binding: PersistentTransactionBinding,
    reference: str,
) -> bool:
    if reference == binding.quarantine_relative:
        return True
    parent, separator, component = reference.rpartition("/")
    quarantine_parent, _, _quarantine_component = (
        binding.quarantine_relative.rpartition("/")
    )
    return (
        separator == "/"
        and parent == quarantine_parent
        and _TRANSACTION_DELETE.fullmatch(component) is not None
    )


def _persistent_delete_reference(
    binding: PersistentTransactionBinding,
    delete_component: str,
) -> str:
    if _TRANSACTION_DELETE.fullmatch(delete_component) is None:
        raise ValueError("persistent transaction delete component")
    parent, separator, _component = binding.quarantine_relative.rpartition("/")
    if separator != "/":
        raise ValueError("persistent transaction quarantine reference")
    return f"{parent}/{delete_component}"


@dataclass(frozen=True, slots=True)
class _TransactionCreationReservation:
    component: str
    identity: tuple[int, int]
    raw: bytes
    stage_component: str
    pending_claim_component: str


@dataclass(frozen=True, slots=True)
class _TransactionCreationIntent:
    component: str
    identity: tuple[int, int]
    raw: bytes
    stage_component: str
    pending_claim_component: str
    reservation_identity: tuple[int, int]
    reservation_digest: str
    binding: PersistentTransactionBinding


def _transaction_creation_reservation_bytes(
    store: _TransactionStore,
    *,
    transaction_id: str,
    stage_component: str,
    pending_claim_component: str,
) -> bytes:
    if (
        _PERSISTENT_TRANSACTION.fullmatch(transaction_id) is None
        or _TRANSACTION_STAGE.fullmatch(stage_component) is None
        or _TRANSACTION_PENDING_CLAIM.fullmatch(pending_claim_component) is None
    ):
        raise ValueError("persistent transaction creation reservation")
    body: dict[str, object] = {
        "authority": "zagrosi-forge-transaction-authority-v1",
        "claims_identity": store.claims_identity,
        "control_identity": store.control_identity,
        "minimum_reader_version": _WRITER_VERSION,
        "pending_claim_component": pending_claim_component,
        "plugins_identity": store.plugins_identity,
        "record_kind": "persistent-transaction-create-reservation",
        "schema_digest": _TRANSACTION_CREATE_RESERVATION_SCHEMA_DIGEST,
        "schema_version": "1.0",
        "stage_component": stage_component,
        "store_identity": store.store_identity,
        "transaction_id": transaction_id,
        "writer_version": _WRITER_VERSION,
    }
    body["record_digest"] = hashlib.sha256(canonical_json_bytes(body)).hexdigest()
    return canonical_json_bytes(body, final_newline=True)


def _transaction_creation_reservation_from_record(
    store: _TransactionStore,
    *,
    transaction_id: str,
    raw: bytes,
    identity: tuple[int, int],
) -> _TransactionCreationReservation:
    record = decode_persistent_record(
        raw,
        supported_major=1,
        reader_version=_WRITER_VERSION,
    )
    stage_component = record.get("stage_component")
    pending_claim_component = record.get("pending_claim_component")
    expected_fixed: Mapping[str, object] = {
        "authority": "zagrosi-forge-transaction-authority-v1",
        "claims_identity": store.claims_identity,
        "control_identity": store.control_identity,
        "minimum_reader_version": _WRITER_VERSION,
        "plugins_identity": store.plugins_identity,
        "record_kind": "persistent-transaction-create-reservation",
        "schema_digest": _TRANSACTION_CREATE_RESERVATION_SCHEMA_DIGEST,
        "schema_version": "1.0",
        "store_identity": store.store_identity,
        "transaction_id": transaction_id,
        "writer_version": _WRITER_VERSION,
    }
    expected_keys = {
        *expected_fixed,
        "pending_claim_component",
        "record_digest",
        "stage_component",
    }
    if (
        set(record) != expected_keys
        or any(record.get(key) != value for key, value in expected_fixed.items())
        or not isinstance(stage_component, str)
        or _TRANSACTION_STAGE.fullmatch(stage_component) is None
        or not isinstance(pending_claim_component, str)
        or _TRANSACTION_PENDING_CLAIM.fullmatch(pending_claim_component) is None
        or raw
        != _transaction_creation_reservation_bytes(
            store,
            transaction_id=transaction_id,
            stage_component=stage_component,
            pending_claim_component=pending_claim_component,
        )
    ):
        raise OSError(errno.ESTALE, "transaction creation reservation changed")
    component = _transaction_creation_reservation_component(transaction_id)
    if not _private_record_name_binds(
        store.claims,
        component,
        identity,
        expected_raw=raw,
        windows=store.windows,
    ):
        raise OSError(errno.ESTALE, "transaction creation reservation changed")
    return _TransactionCreationReservation(
        component=component,
        identity=identity,
        raw=raw,
        stage_component=stage_component,
        pending_claim_component=pending_claim_component,
    )


def _transaction_creation_intent_bytes(
    store: _TransactionStore,
    *,
    transaction_id: str,
    reservation: _TransactionCreationReservation,
    binding: PersistentTransactionBinding,
) -> bytes:
    if (
        not isinstance(reservation, _TransactionCreationReservation)
        or not _persistent_binding_invariants(binding)
        or binding.transaction_id != transaction_id
        or binding.plugins_identity != store.plugins_identity
        or binding.control_identity != store.control_identity
        or binding.store_identity != store.store_identity
        or binding.claims_identity != store.claims_identity
    ):
        raise ValueError("persistent transaction creation intent")
    body: dict[str, object] = {
        "authority": "zagrosi-forge-transaction-authority-v1",
        "claim_digest": binding.claim_digest,
        "claim_identity": binding.claim_identity,
        "claims_identity": store.claims_identity,
        "control_identity": store.control_identity,
        "minimum_reader_version": _WRITER_VERSION,
        "pending_claim_component": reservation.pending_claim_component,
        "plugins_identity": store.plugins_identity,
        "record_kind": "persistent-transaction-create-bound",
        "reservation_digest": hashlib.sha256(reservation.raw).hexdigest(),
        "reservation_identity": reservation.identity,
        "schema_digest": _TRANSACTION_CREATE_INTENT_SCHEMA_DIGEST,
        "schema_version": "1.0",
        "stage_component": reservation.stage_component,
        "store_identity": store.store_identity,
        "transaction_id": transaction_id,
        "transaction_identity": binding.transaction_identity,
        "writer_version": _WRITER_VERSION,
    }
    body["record_digest"] = hashlib.sha256(canonical_json_bytes(body)).hexdigest()
    return canonical_json_bytes(body, final_newline=True)


def _transaction_creation_intent_from_record(
    store: _TransactionStore,
    *,
    transaction_id: str,
    raw: bytes,
    identity: tuple[int, int],
) -> _TransactionCreationIntent:
    record = decode_persistent_record(
        raw, supported_major=1, reader_version=_WRITER_VERSION
    )
    stage_component = record.get("stage_component")
    pending_claim_component = record.get("pending_claim_component")
    transaction_identity = record.get("transaction_identity")
    claim_identity = record.get("claim_identity")
    reservation_identity = record.get("reservation_identity")
    reservation_digest = record.get("reservation_digest")
    expected_fixed: Mapping[str, object] = {
        "authority": "zagrosi-forge-transaction-authority-v1",
        "claims_identity": store.claims_identity,
        "control_identity": store.control_identity,
        "minimum_reader_version": _WRITER_VERSION,
        "plugins_identity": store.plugins_identity,
        "record_kind": "persistent-transaction-create-bound",
        "schema_digest": _TRANSACTION_CREATE_INTENT_SCHEMA_DIGEST,
        "schema_version": "1.0",
        "store_identity": store.store_identity,
        "transaction_id": transaction_id,
        "writer_version": _WRITER_VERSION,
    }
    expected_keys = {
        *expected_fixed,
        "claim_digest",
        "claim_identity",
        "pending_claim_component",
        "record_digest",
        "reservation_digest",
        "reservation_identity",
        "stage_component",
        "transaction_identity",
    }
    if (
        set(record) != expected_keys
        or any(record.get(key) != value for key, value in expected_fixed.items())
        or not isinstance(stage_component, str)
        or _TRANSACTION_STAGE.fullmatch(stage_component) is None
        or not isinstance(pending_claim_component, str)
        or _TRANSACTION_PENDING_CLAIM.fullmatch(pending_claim_component) is None
        or not _file_identity_invariants(transaction_identity)
        or not _file_identity_invariants(claim_identity)
        or not _file_identity_invariants(reservation_identity)
        or not isinstance(reservation_digest, str)
        or _DIGEST.fullmatch(reservation_digest) is None
        or cast(tuple[int, int], transaction_identity)[0] != store.plugins_identity[0]
        or cast(tuple[int, int], claim_identity)[0] != store.plugins_identity[0]
        or cast(tuple[int, int], reservation_identity)[0] != store.plugins_identity[0]
    ):
        raise OSError(errno.ESTALE, "transaction creation intent changed")
    reservation_component = _transaction_creation_reservation_component(transaction_id)
    reservation_raw = _transaction_creation_reservation_bytes(
        store,
        transaction_id=transaction_id,
        stage_component=stage_component,
        pending_claim_component=pending_claim_component,
    )
    if hashlib.sha256(reservation_raw).hexdigest() != reservation_digest:
        raise OSError(errno.ESTALE, "transaction creation intent changed")
    reservation = _TransactionCreationReservation(
        component=reservation_component,
        identity=cast(tuple[int, int], reservation_identity),
        raw=reservation_raw,
        stage_component=stage_component,
        pending_claim_component=pending_claim_component,
    )
    if not _transaction_record_is_active_or_retired_exact(
        store,
        reservation_component,
        reservation.identity,
        reservation.raw,
    ):
        raise OSError(errno.ESTALE, "transaction creation reservation changed")
    binding, _claim_raw = _binding_from_fields(
        transaction_id=transaction_id,
        plugins_identity=store.plugins_identity,
        control_identity=store.control_identity,
        store_identity=store.store_identity,
        claims_identity=store.claims_identity,
        transaction_identity=cast(tuple[int, int], transaction_identity),
        claim_identity=cast(tuple[int, int], claim_identity),
    )
    if record.get(
        "claim_digest"
    ) != binding.claim_digest or raw != _transaction_creation_intent_bytes(
        store,
        transaction_id=transaction_id,
        reservation=reservation,
        binding=binding,
    ):
        raise OSError(errno.ESTALE, "transaction creation intent changed")
    component = _transaction_creation_intent_component(transaction_id)
    if not _private_record_name_binds(
        store.claims,
        component,
        identity,
        expected_raw=raw,
        windows=store.windows,
    ):
        raise OSError(errno.ESTALE, "transaction creation intent changed")
    return _TransactionCreationIntent(
        component=component,
        identity=identity,
        raw=raw,
        stage_component=stage_component,
        pending_claim_component=pending_claim_component,
        reservation_identity=reservation.identity,
        reservation_digest=reservation_digest,
        binding=binding,
    )


@dataclass(frozen=True, slots=True)
class _TransactionCleanupRecord:
    component: str
    identity: tuple[int, int]
    raw: bytes
    delete_component: str
    complete: bool


@dataclass(frozen=True, slots=True)
class _RecoveryCleanupPhaseRecord:
    component: str
    identity: tuple[int, int]
    raw: bytes
    phase: _RecoveryCleanupPhase
    previous_phase_digest: str
    record_digest: str
    retired: bool


def _transaction_cleanup_record_bytes(
    binding: PersistentTransactionBinding,
    *,
    delete_component: str,
    complete: bool,
) -> bytes:
    if (
        not _persistent_binding_invariants(binding)
        or _TRANSACTION_DELETE.fullmatch(delete_component) is None
    ):
        raise ValueError("persistent transaction cleanup record")
    body: dict[str, object] = {
        "authority": "zagrosi-forge-transaction-authority-v1",
        "binding": binding.canonical_projection(),
        "delete_component": delete_component,
        "minimum_reader_version": _WRITER_VERSION,
        "record_kind": (
            "persistent-transaction-cleanup-complete"
            if complete
            else "persistent-transaction-cleanup-intent"
        ),
        "schema_digest": _TRANSACTION_CLEANUP_SCHEMA_DIGEST,
        "schema_version": "1.0",
        "transaction_id": binding.transaction_id,
        "writer_version": _WRITER_VERSION,
    }
    body["record_digest"] = hashlib.sha256(canonical_json_bytes(body)).hexdigest()
    return canonical_json_bytes(body, final_newline=True)


def _recovery_cleanup_binding_digest(
    binding: PersistentTransactionBinding,
) -> str:
    if not _persistent_binding_invariants(binding):
        raise ValueError("recovery cleanup binding")
    return hashlib.sha256(
        canonical_json_bytes(binding.canonical_projection())
    ).hexdigest()


def _transaction_journal_access_digest(
    binding: PersistentTransactionBinding,
    *,
    location: TransactionLocation,
    journal_relative: str,
) -> str:
    valid_relative = (
        location is TransactionLocation.LIVE
        and journal_relative == binding.root_relative
    ) or (
        location is TransactionLocation.QUARANTINED
        and _persistent_cleanup_reference_is_valid(binding, journal_relative)
    )
    if (
        not _persistent_binding_invariants(binding)
        or type(location) is not TransactionLocation
        or not valid_relative
    ):
        raise ValueError("transaction journal access")
    return hashlib.sha256(
        canonical_json_bytes(
            {
                "binding_digest": _recovery_cleanup_binding_digest(binding),
                "journal_relative": journal_relative,
                "location": location.value,
                "transaction_id": binding.transaction_id,
            }
        )
    ).hexdigest()


def _recovery_cleanup_access_digest(
    binding: PersistentTransactionBinding,
    *,
    location: TransactionLocation,
    journal_relative: str,
) -> str:
    if (
        location is not TransactionLocation.QUARANTINED
        or not _persistent_cleanup_reference_is_valid(binding, journal_relative)
    ):
        raise ValueError("recovery cleanup observation")
    return _transaction_journal_access_digest(
        binding,
        location=location,
        journal_relative=journal_relative,
    )


def _recovery_cleanup_record_bytes(
    binding: PersistentTransactionBinding,
    *,
    location: TransactionLocation,
    journal_relative: str,
    journal_access_digest: str,
    journal_evidence_digest: str,
    journal_head_sequence: int,
    journal_head_record_digest: str,
    transaction_binding_digest: str,
    delete_component: str,
    phase: _RecoveryCleanupPhase,
    previous_phase_digest: str,
) -> bytes:
    binding_digest = _recovery_cleanup_binding_digest(binding)
    if (
        location is not TransactionLocation.QUARANTINED
        or not _persistent_cleanup_reference_is_valid(binding, journal_relative)
        or _DIGEST.fullmatch(journal_access_digest) is None
        or journal_access_digest
        != _recovery_cleanup_access_digest(
            binding,
            location=location,
            journal_relative=journal_relative,
        )
        or _DIGEST.fullmatch(journal_evidence_digest) is None
        or type(journal_head_sequence) is not int
        or journal_head_sequence < 0
        or _DIGEST.fullmatch(journal_head_record_digest) is None
        or transaction_binding_digest != binding_digest
        or _TRANSACTION_DELETE.fullmatch(delete_component) is None
        or type(phase) is not _RecoveryCleanupPhase
        or _DIGEST.fullmatch(previous_phase_digest) is None
        or (
            phase is _RecoveryCleanupPhase.AUTHORIZED
            and previous_phase_digest != _ZERO_DIGEST
        )
        or (
            phase is not _RecoveryCleanupPhase.AUTHORIZED
            and previous_phase_digest == _ZERO_DIGEST
        )
    ):
        raise ValueError("recovery cleanup record")
    body: dict[str, object] = {
        "authority": "zagrosi-forge-transaction-authority-v1",
        "binding": binding.canonical_projection(),
        "binding_digest": binding_digest,
        "delete_component": delete_component,
        "journal_access_digest": journal_access_digest,
        "journal_evidence_digest": journal_evidence_digest,
        "journal_head_record_digest": journal_head_record_digest,
        "journal_head_sequence": journal_head_sequence,
        "journal_head_state": "ROLLED_BACK",
        "journal_location": location.value,
        "journal_relative": journal_relative,
        "minimum_reader_version": _WRITER_VERSION,
        "policy_version": _POLICY_VERSION,
        "previous_recovery_cleanup_digest": previous_phase_digest,
        "record_kind": "persistent-transaction-recovery-cleanup",
        "recovery_cleanup_phase": phase.value,
        "root_identity": binding.transaction_identity,
        "schema_digest": _TRANSACTION_RECOVERY_CLEANUP_SCHEMA_DIGEST,
        "schema_version": "1.0",
        "transaction_binding_digest": transaction_binding_digest,
        "transaction_id": binding.transaction_id,
        "writer_version": _WRITER_VERSION,
    }
    body["record_digest"] = hashlib.sha256(canonical_json_bytes(body)).hexdigest()
    return canonical_json_bytes(body, final_newline=True)


def _recovery_cleanup_phase_bytes(
    authorization: RecoveryCleanupAuthorization,
    *,
    phase: _RecoveryCleanupPhase,
    previous_phase_digest: str,
) -> bytes:
    authorization._require_valid()
    return _recovery_cleanup_record_bytes(
        authorization.binding,
        location=authorization.location,
        journal_relative=authorization.journal_relative,
        journal_access_digest=authorization.journal_access_digest,
        journal_evidence_digest=authorization.journal_evidence_digest,
        journal_head_sequence=authorization.journal_head_sequence,
        journal_head_record_digest=authorization.journal_head_record_digest,
        transaction_binding_digest=authorization.transaction_binding_digest,
        delete_component=authorization.delete_component,
        phase=phase,
        previous_phase_digest=previous_phase_digest,
    )


def _recovery_cleanup_authorization_invariants(value: object) -> bool:
    if type(value) is not RecoveryCleanupAuthorization:
        return False
    authorization = value
    try:
        expected_component = _recovery_cleanup_component(
            authorization.binding.transaction_id,
            _RecoveryCleanupPhase.AUTHORIZED,
        )
        expected_raw = _recovery_cleanup_record_bytes(
            authorization.binding,
            location=authorization.location,
            journal_relative=authorization.journal_relative,
            journal_access_digest=authorization.journal_access_digest,
            journal_evidence_digest=authorization.journal_evidence_digest,
            journal_head_sequence=authorization.journal_head_sequence,
            journal_head_record_digest=authorization.journal_head_record_digest,
            transaction_binding_digest=authorization.transaction_binding_digest,
            delete_component=authorization.delete_component,
            phase=_RecoveryCleanupPhase.AUTHORIZED,
            previous_phase_digest=_ZERO_DIGEST,
        )
        decoded = decode_persistent_record(
            expected_raw,
            supported_major=1,
            reader_version=_WRITER_VERSION,
        )
        return (
            authorization._seal is _RECOVERY_CLEANUP_AUTHORIZATION_TOKEN
            and authorization._component == expected_component
            and _file_identity_invariants(authorization._identity)
            and authorization._raw == expected_raw
            and authorization.authorization_digest == decoded["record_digest"]
        )
    except (AttributeError, ForgeError, TypeError, ValueError):
        return False


def _recovery_cleanup_observation_projection(
    authorization: RecoveryCleanupAuthorization,
    capture: _RecoveryCleanupObservationCapture,
) -> Mapping[str, object]:
    return {
        "authorization_active": capture.authorization_active,
        "authorization_digest": authorization.authorization_digest,
        "authorization_identity": capture.authorization_identity,
        "cleanup_complete_digest": capture.cleanup_complete_digest,
        "cleanup_complete_identity": capture.cleanup_complete_identity,
        "cleanup_intent_digest": capture.cleanup_intent_digest,
        "cleanup_intent_identity": capture.cleanup_intent_identity,
        "complete_identity": capture.complete_identity,
        "complete_record_digest": capture.complete_record_digest,
        "complete_retired": capture.complete_retired,
        "current_reference": capture.current_reference,
        "finalizing_identity": capture.finalizing_identity,
        "finalizing_record_digest": capture.finalizing_record_digest,
        "finalizing_retired": capture.finalizing_retired,
        "phase": capture.phase,
        "transaction_id": capture.transaction_id,
    }


def _recovery_cleanup_observation_digest(
    authorization: RecoveryCleanupAuthorization,
    capture: _RecoveryCleanupObservationCapture,
) -> str:
    return hashlib.sha256(
        canonical_json_bytes(
            _recovery_cleanup_observation_projection(authorization, capture)
        )
    ).hexdigest()


def _optional_recovery_cleanup_record_invariants(
    digest: object,
    identity: object,
) -> bool:
    return (digest is None and identity is None) or (
        isinstance(digest, str)
        and _DIGEST.fullmatch(digest) is not None
        and _file_identity_invariants(identity)
    )


def _optional_recovery_cleanup_phase_invariants(
    digest: object,
    identity: object,
    retired: object,
) -> bool:
    return (digest is None and identity is None and retired is None) or (
        isinstance(digest, str)
        and _DIGEST.fullmatch(digest) is not None
        and _file_identity_invariants(identity)
        and type(retired) is bool
    )


def _recovery_cleanup_observation_capture_invariants(
    authorization: object,
    capture: object,
) -> bool:
    if (
        type(authorization) is not RecoveryCleanupAuthorization
        or not _recovery_cleanup_authorization_invariants(authorization)
        or type(capture) is not _RecoveryCleanupObservationCapture
        or capture.transaction_id != authorization.transaction_id
        or capture.authorization_identity != authorization._identity
        or capture.authorization_active is not True
        or capture.phase
        not in {
            _RecoveryCleanupPhase.AUTHORIZED.value,
            _RecoveryCleanupPhase.FINALIZING.value,
            _RecoveryCleanupPhase.COMPLETE.value,
        }
        or not _optional_recovery_cleanup_phase_invariants(
            capture.finalizing_record_digest,
            capture.finalizing_identity,
            capture.finalizing_retired,
        )
        or not _optional_recovery_cleanup_phase_invariants(
            capture.complete_record_digest,
            capture.complete_identity,
            capture.complete_retired,
        )
        or not _optional_recovery_cleanup_record_invariants(
            capture.cleanup_intent_digest,
            capture.cleanup_intent_identity,
        )
        or not _optional_recovery_cleanup_record_invariants(
            capture.cleanup_complete_digest,
            capture.cleanup_complete_identity,
        )
    ):
        return False
    current_reference = capture.current_reference
    valid_current_reference = current_reference is None or (
        isinstance(current_reference, str)
        and _persistent_cleanup_reference_is_valid(
            authorization.binding,
            current_reference,
        )
    )
    if not valid_current_reference:
        return False
    if capture.phase == _RecoveryCleanupPhase.AUTHORIZED.value:
        return (
            current_reference is not None
            and capture.finalizing_record_digest is None
            and capture.complete_record_digest is None
            and capture.cleanup_complete_digest is None
        )
    if capture.phase == _RecoveryCleanupPhase.FINALIZING.value:
        return (
            capture.finalizing_record_digest is not None
            and capture.finalizing_retired is False
            and capture.complete_record_digest is None
            and (
                capture.cleanup_intent_digest is not None
                or capture.cleanup_complete_digest is not None
            )
            and not (
                capture.cleanup_complete_digest is not None
                and current_reference is not None
            )
        )
    return (
        current_reference is None
        and capture.finalizing_record_digest is not None
        and capture.complete_record_digest is not None
        and capture.cleanup_complete_digest is not None
        and not (
            capture.finalizing_retired is True and capture.complete_retired is not True
        )
    )


def _read_posix_private_record(
    parent: int,
    component: str,
    *,
    device: int,
) -> tuple[bytes, tuple[int, int]]:
    descriptor = os.open(component, _paths._posix_file_flags(), dir_fd=parent)
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_gid != os.getegid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_nlink != 1
            or before.st_dev != device
            or before.st_size > _TRANSACTION_RECORD_LIMIT
            or not _paths._posix_security_metadata_supported(descriptor, before)
        ):
            raise OSError(errno.EPERM, "persistent transaction record is unsafe")
        raw = os.pread(descriptor, _TRANSACTION_RECORD_LIMIT + 1, 0)
        after = os.fstat(descriptor)
        if (
            len(raw) != before.st_size
            or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)
            or _paths._posix_status_fingerprint(before)
            != _paths._posix_status_fingerprint(after)
            or not _paths._posix_security_metadata_supported(descriptor, after)
        ):
            raise OSError(errno.ESTALE, "persistent transaction record changed")
        return raw, (before.st_dev, before.st_ino)
    finally:
        os.close(descriptor)


def _read_windows_private_record(
    parent: int,
    component: str,
    *,
    volume: int,
) -> tuple[bytes, tuple[int, int]]:
    descriptor = _paths._windows_open_child(
        parent, component, directory=False, read_data=True
    )
    try:
        before = _paths._windows_handle_status(descriptor)
        if (
            before.is_directory
            or before.is_reparse
            or before.link_count != 1
            or before.identity[0] != volume
            or before.size > _TRANSACTION_RECORD_LIMIT
            or not _paths._windows_private_authorization(descriptor, exact=True)
        ):
            raise OSError(errno.EPERM, "persistent transaction record is unsafe")
        raw = _paths._windows_read(descriptor, limit=_TRANSACTION_RECORD_LIMIT)
        after = _paths._windows_handle_status(descriptor)
        if (
            len(raw) != before.size
            or after.identity != before.identity
            or after.fingerprint != before.fingerprint
            or not _paths._windows_private_authorization(descriptor, exact=True)
        ):
            raise OSError(errno.ESTALE, "persistent transaction record changed")
        return raw, before.identity
    finally:
        _paths._windows_close(descriptor)


def _publish_posix_transaction_record(
    store: _TransactionStore,
    component: str,
    raw: bytes,
) -> tuple[int, int]:
    temporary = f".record-{secrets.token_hex(16)}.tmp"
    descriptor = -1
    identity: tuple[int, int] | None = None
    try:
        try:
            observed_raw, observed_identity = _read_posix_private_record(
                store.claims,
                component,
                device=store.plugins_identity[0],
            )
        except FileNotFoundError:
            pass
        else:
            if observed_raw != raw or not _private_record_name_binds(
                store.claims,
                component,
                observed_identity,
                expected_raw=raw,
                windows=False,
            ):
                raise OSError(errno.EEXIST, "transaction record conflicts")
            return observed_identity
        descriptor = os.open(
            temporary,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o600,
            dir_fd=store.claims,
        )
        os.fchmod(descriptor, 0o600)
        identity = _identity(descriptor)
        _paths._write_all(descriptor, raw)
        os.fsync(descriptor)
        status = os.fstat(descriptor)
        if (
            (status.st_dev, status.st_ino) != identity
            or not stat.S_ISREG(status.st_mode)
            or status.st_uid != os.geteuid()
            or status.st_gid != os.getegid()
            or stat.S_IMODE(status.st_mode) != 0o600
            or status.st_nlink != 1
            or status.st_dev != store.plugins_identity[0]
            or not _paths._posix_security_metadata_supported(descriptor, status)
        ):
            raise OSError(errno.EPERM, "transaction record is unsafe")
        try:
            _paths._exclusive_posix_rename(store.claims, temporary, component)
        except FileExistsError:
            observed_raw, observed_identity = _read_posix_private_record(
                store.claims,
                component,
                device=store.plugins_identity[0],
            )
            if observed_raw != raw or not _private_record_name_binds(
                store.claims,
                component,
                observed_identity,
                expected_raw=raw,
                windows=False,
            ):
                raise OSError(errno.EEXIST, "transaction record conflicts") from None
            return observed_identity
        os.fsync(store.claims)
        if not _private_record_name_binds(
            store.claims,
            component,
            identity,
            expected_raw=raw,
            windows=False,
        ):
            raise OSError(errno.ESTALE, "transaction record changed")
        return identity
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if identity is not None:
            try:
                named = os.stat(temporary, dir_fd=store.claims, follow_symlinks=False)
                if (named.st_dev, named.st_ino) == identity:
                    os.unlink(temporary, dir_fd=store.claims)
                    os.fsync(store.claims)
            except OSError:
                pass


def _publish_windows_transaction_record(
    store: _TransactionStore,
    component: str,
    raw: bytes,
) -> tuple[int, int]:
    temporary = f".record-{secrets.token_hex(16)}.tmp"
    descriptor = 0
    identity: tuple[int, int] | None = None
    try:
        try:
            observed_raw, observed_identity = _read_windows_private_record(
                store.claims,
                component,
                volume=store.plugins_identity[0],
            )
        except OSError as exc:
            if not isinstance(exc, FileNotFoundError) and getattr(
                exc, "winerror", None
            ) not in {2, 3}:
                raise
        else:
            if observed_raw != raw or not _private_record_name_binds(
                store.claims,
                component,
                observed_identity,
                expected_raw=raw,
                windows=True,
            ):
                raise OSError(errno.EEXIST, "transaction record conflicts")
            return observed_identity
        descriptor = _paths._windows_create_private_file(store.claims, temporary)
        identity = _paths._windows_handle_status(descriptor).identity
        _paths._windows_write(descriptor, raw)
        status = _paths._windows_handle_status(descriptor)
        if (
            status.identity != identity
            or status.is_directory
            or status.is_reparse
            or status.link_count != 1
            or status.identity[0] != store.plugins_identity[0]
            or not _paths._windows_private_authorization(descriptor, exact=True)
        ):
            raise OSError(errno.EPERM, "transaction record is unsafe")
        try:
            _durable_windows_file_rename(
                descriptor,
                store.claims,
                component,
            )
        except FileExistsError:
            observed_raw, observed_identity = _read_windows_private_record(
                store.claims,
                component,
                volume=store.plugins_identity[0],
            )
            if observed_raw != raw or not _private_record_name_binds(
                store.claims,
                component,
                observed_identity,
                expected_raw=raw,
                windows=True,
            ):
                raise OSError(errno.EEXIST, "transaction record conflicts") from None
            return observed_identity
        _windows_flush_directory_binding(
            store.store,
            _TRANSACTION_CLAIMS_COMPONENT,
            store.claims_identity,
        )
        if not _private_record_name_binds(
            store.claims,
            component,
            identity,
            expected_raw=raw,
            windows=True,
        ):
            raise OSError(errno.ESTALE, "transaction record changed")
        return identity
    finally:
        if descriptor:
            _paths._windows_close(descriptor)
        if identity is not None:
            temporary_handle = 0
            try:
                temporary_handle = _paths._windows_open_child(
                    store.claims,
                    temporary,
                    directory=False,
                    read_data=True,
                    delete_access=True,
                )
                status = _paths._windows_handle_status(temporary_handle)
                if status.identity == identity:
                    _windows_delete_handle(temporary_handle)
            except (ForgeError, OSError):
                pass
            finally:
                if temporary_handle:
                    _paths._windows_close(temporary_handle)


def _publish_transaction_record(
    store: _TransactionStore,
    component: str,
    raw: bytes,
) -> tuple[int, int]:
    if store.windows:
        return _publish_windows_transaction_record(store, component, raw)
    return _publish_posix_transaction_record(store, component, raw)


def _transaction_record_retirement_component(component: str) -> str:
    if (
        not isinstance(component, str)
        or not component
        or len(component.encode("utf-8")) > LIMIT_POLICY.value("path_component_bytes")
        or "/" in component
        or "\x00" in component
    ):
        raise ValueError("transaction record component")
    digest = hashlib.sha256(component.encode("utf-8")).hexdigest()
    return f".retired-{digest}.json"


def _transaction_record_is_active_or_retired_exact(
    store: _TransactionStore,
    component: str,
    expected_identity: tuple[int, int],
    expected_raw: bytes,
) -> bool:
    retired = _transaction_record_retirement_component(component)
    active_is_exact = _private_record_name_binds(
        store.claims,
        component,
        expected_identity,
        expected_raw=expected_raw,
        windows=store.windows,
    )
    retired_is_exact = _private_record_name_binds(
        store.claims,
        retired,
        expected_identity,
        expected_raw=expected_raw,
        windows=store.windows,
    )
    active_exists = active_is_exact or _transaction_name_exists(
        store.claims,
        component,
        directory=False,
        windows=store.windows,
    )
    retired_exists = retired_is_exact or _transaction_name_exists(
        store.claims,
        retired,
        directory=False,
        windows=store.windows,
    )
    return (active_is_exact and not retired_exists) or (
        retired_is_exact and not active_exists
    )


def _remove_exact_transaction_record(
    store: _TransactionStore,
    component: str,
    expected_identity: tuple[int, int],
    expected_raw: bytes,
) -> None:
    retired = _transaction_record_retirement_component(component)
    source_is_exact = _private_record_name_binds(
        store.claims,
        component,
        expected_identity,
        expected_raw=expected_raw,
        windows=store.windows,
    )
    retired_is_exact = _private_record_name_binds(
        store.claims,
        retired,
        expected_identity,
        expected_raw=expected_raw,
        windows=store.windows,
    )
    if not source_is_exact:
        source_exists = _transaction_name_exists(
            store.claims,
            component,
            directory=False,
            windows=store.windows,
        )
        if not source_exists and retired_is_exact:
            return
        raise OSError(errno.ESTALE, "transaction record changed")
    if retired_is_exact or _transaction_name_exists(
        store.claims,
        retired,
        directory=False,
        windows=store.windows,
    ):
        raise OSError(errno.EEXIST, "transaction retirement slot conflicts")
    if store.windows:
        descriptor = _paths._windows_open_child(
            store.claims,
            component,
            directory=False,
            read_data=True,
            write_data=True,
            delete_access=True,
        )
        try:
            status = _paths._windows_handle_status(descriptor)
            if (
                status.identity != expected_identity
                or status.is_directory
                or status.is_reparse
                or status.link_count != 1
                or not _paths._windows_private_authorization(descriptor, exact=True)
            ):
                raise OSError(errno.ESTALE, "transaction record changed")
            _durable_windows_file_rename(
                descriptor,
                store.claims,
                retired,
            )
        finally:
            _paths._windows_close(descriptor)
        _windows_flush_directory_binding(
            store.store,
            _TRANSACTION_CLAIMS_COMPONENT,
            store.claims_identity,
        )
    else:
        _paths._exclusive_posix_rename(store.claims, component, retired)
        os.fsync(store.claims)
    if not _private_record_name_binds(
        store.claims,
        retired,
        expected_identity,
        expected_raw=expected_raw,
        windows=store.windows,
    ) or _transaction_name_exists(
        store.claims,
        component,
        directory=False,
        windows=store.windows,
    ):
        raise OSError(errno.ESTALE, "retired transaction record changed")


def _create_posix_store_record(store: int, raw: bytes) -> None:
    descriptor = os.open(
        _TRANSACTION_STORE_CONTROL,
        os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
        0o600,
        dir_fd=store,
    )
    try:
        os.fchmod(descriptor, 0o600)
        _paths._write_all(descriptor, raw)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.fsync(store)


def _create_windows_store_record(store: int, raw: bytes) -> None:
    descriptor = _paths._windows_create_private_file(store, _TRANSACTION_STORE_CONTROL)
    try:
        _paths._windows_write(descriptor, raw)
        _windows_flush(descriptor)
    finally:
        _paths._windows_close(descriptor)


def _discard_posix_losing_transaction_store(
    control: int,
    stage_name: str,
    staged: int,
    store_identity: tuple[int, int],
    claims: int,
    claims_identity: tuple[int, int],
    expected_record: bytes,
) -> None:
    """Remove only the exact, unopened first-publication loser."""

    raw, record_identity = _read_posix_private_record(
        staged,
        _TRANSACTION_STORE_CONTROL,
        device=store_identity[0],
    )
    if (
        raw != expected_record
        or tuple(os.listdir(claims))
        or set(os.listdir(staged))
        != {_TRANSACTION_CLAIMS_COMPONENT, _TRANSACTION_STORE_CONTROL}
        or not _paths._posix_namespace_binds(control, stage_name, store_identity)
        or not _paths._posix_namespace_binds(
            staged, _TRANSACTION_CLAIMS_COMPONENT, claims_identity
        )
        or not _private_record_name_binds(
            staged,
            _TRANSACTION_STORE_CONTROL,
            record_identity,
            expected_raw=expected_record,
            windows=False,
        )
    ):
        raise OSError(errno.ESTALE, "losing transaction store changed")
    os.unlink(_TRANSACTION_STORE_CONTROL, dir_fd=staged)
    os.fsync(staged)
    os.rmdir(_TRANSACTION_CLAIMS_COMPONENT, dir_fd=staged)
    os.fsync(staged)
    if not _paths._posix_namespace_binds(control, stage_name, store_identity):
        raise OSError(errno.ESTALE, "losing transaction store changed")
    os.rmdir(stage_name, dir_fd=control)
    os.fsync(control)


def _open_windows_losing_store_record(
    control: int,
    stage_name: str,
    staged: int,
    store_identity: tuple[int, int],
    claims: int,
    claims_identity: tuple[int, int],
    expected_record: bytes,
) -> int:
    """Validate a first-publication loser and hold its exact control record."""

    raw, record_identity = _read_windows_private_record(
        staged,
        _TRANSACTION_STORE_CONTROL,
        volume=store_identity[0],
    )
    if (
        raw != expected_record
        or _windows_list_names(claims, limit=1)
        or set(_windows_list_names(staged, limit=2))
        != {_TRANSACTION_CLAIMS_COMPONENT, _TRANSACTION_STORE_CONTROL}
        or not _paths._windows_namespace_binds(control, stage_name, store_identity)
        or not _paths._windows_namespace_binds(
            staged, _TRANSACTION_CLAIMS_COMPONENT, claims_identity
        )
    ):
        raise OSError(errno.ESTALE, "losing transaction store changed")
    record = _paths._windows_open_child(
        staged,
        _TRANSACTION_STORE_CONTROL,
        directory=False,
        read_data=True,
        delete_access=True,
    )
    status = _paths._windows_handle_status(record)
    if (
        status.identity != record_identity
        or status.is_directory
        or status.is_reparse
        or status.link_count != 1
        or not _paths._windows_private_authorization(record, exact=True)
    ):
        _paths._windows_close(record)
        raise OSError(errno.ESTALE, "losing transaction store changed")
    return record


def _open_posix_transaction_store(
    owned_root: OwnedRoot, *, create: bool
) -> _TransactionStore:
    control = owned_root._duplicate_control_descriptor()
    store = claims = -1
    try:
        if not owned_root._validate_control_descriptor(control):
            raise _error("ownership.unowned", "The transaction store is not trusted.")
        plugins_identity = owned_root.identity
        control_identity = owned_root.control_identity
        try:
            store = _paths._open_directory_component(
                control,
                _TRANSACTION_STORE_COMPONENT,
                linked_code="path.linked_leaf",
            )
        except ForgeError as exc:
            if not create or exc.code != "path.outside_root":
                raise _error(
                    "ownership.unowned", "The transaction store is not trusted."
                ) from None
            stage_name, staged, store_identity = _paths._stage_posix_directory(
                control, prefix="transactions"
            )
            try:
                os.mkdir(_TRANSACTION_CLAIMS_COMPONENT, 0o700, dir_fd=staged)
                claims = _paths._open_directory_component(
                    staged,
                    _TRANSACTION_CLAIMS_COMPONENT,
                    linked_code="path.linked_leaf",
                )
                claims_status = os.fstat(claims)
                claims_identity = (claims_status.st_dev, claims_status.st_ino)
                raw = _transaction_store_record_bytes(
                    plugins_identity=plugins_identity,
                    control_identity=control_identity,
                    store_identity=store_identity,
                    claims_identity=claims_identity,
                )
                _create_posix_store_record(staged, raw)
                try:
                    _paths._exclusive_posix_rename(
                        control, stage_name, _TRANSACTION_STORE_COMPONENT
                    )
                except FileExistsError:
                    _discard_posix_losing_transaction_store(
                        control,
                        stage_name,
                        staged,
                        store_identity,
                        claims,
                        claims_identity,
                        raw,
                    )
                    os.close(claims)
                    claims = -1
                    os.close(staged)
                    staged = -1
                    store = _paths._open_directory_component(
                        control,
                        _TRANSACTION_STORE_COMPONENT,
                        linked_code="path.linked_leaf",
                    )
                else:
                    os.fsync(control)
                    store = staged
                    staged = -1
            finally:
                if staged >= 0:
                    os.close(staged)
        store_status = os.fstat(store)
        store_identity = (store_status.st_dev, store_status.st_ino)
        if claims < 0:
            claims = _paths._open_directory_component(
                store,
                _TRANSACTION_CLAIMS_COMPONENT,
                linked_code="path.linked_ancestor",
            )
        claims_status = os.fstat(claims)
        claims_identity = (claims_status.st_dev, claims_status.st_ino)
        if (
            store_status.st_dev != plugins_identity[0]
            or claims_status.st_dev != plugins_identity[0]
            or not _paths._private_directory(store, store_status, exact=True)
            or not _paths._private_directory(claims, claims_status, exact=True)
            or not owned_root._filesystem_guard(store)
            or not owned_root._filesystem_guard(claims)
        ):
            raise _error("ownership.unowned", "The transaction store is not trusted.")
        raw, _record_identity = _read_posix_private_record(
            store,
            _TRANSACTION_STORE_CONTROL,
            device=plugins_identity[0],
        )
        if not _valid_transaction_store_record(
            raw,
            plugins_identity=plugins_identity,
            control_identity=control_identity,
            store_identity=store_identity,
            claims_identity=claims_identity,
        ) or not _private_record_name_binds(
            store,
            _TRANSACTION_STORE_CONTROL,
            _record_identity,
            expected_raw=raw,
            windows=False,
        ):
            raise _error("ownership.unowned", "The transaction store is not trusted.")
        if not owned_root._validate_control_descriptor(control):
            raise _error("ownership.unowned", "The transaction store is not trusted.")
        opened = _TransactionStore(
            control,
            store,
            claims,
            plugins_identity,
            control_identity,
            store_identity,
            claims_identity,
            False,
        )
        if not _transaction_store_namespace_is_valid(opened):
            raise _error("ownership.unowned", "The transaction store is not trusted.")
        control = store = claims = -1
        return opened
    finally:
        for descriptor in (claims, store, control):
            if descriptor >= 0:
                os.close(descriptor)


def _open_windows_transaction_store(
    owned_root: OwnedRoot, *, create: bool
) -> _TransactionStore:
    control = owned_root._duplicate_control_descriptor()
    store = claims = 0
    try:
        if not owned_root._validate_control_descriptor(control):
            raise _error("ownership.unowned", "The transaction store is not trusted.")
        plugins_identity = owned_root.identity
        control_identity = owned_root.control_identity
        try:
            store = _paths._windows_open_child(
                control,
                _TRANSACTION_STORE_COMPONENT,
                directory=True,
                read_data=True,
            )
        except OSError as exc:
            missing = isinstance(exc, FileNotFoundError) or getattr(
                exc, "winerror", None
            ) in {2, 3}
            if not create or not missing:
                raise _error(
                    "ownership.unowned", "The transaction store is not trusted."
                ) from None
            for _attempt in range(16):
                stage_name = f".transactions-{secrets.token_hex(16)}.tmp"
                try:
                    staged = _paths._windows_create_private_directory(
                        control, stage_name
                    )
                except FileExistsError:
                    continue
                break
            else:
                raise _error(
                    "ownership.unowned", "The transaction store cannot be created."
                )
            try:
                store_status = _paths._windows_handle_status(staged)
                claims = _paths._windows_create_private_directory(
                    staged, _TRANSACTION_CLAIMS_COMPONENT
                )
                claims_identity = _paths._windows_handle_status(claims).identity
                raw = _transaction_store_record_bytes(
                    plugins_identity=plugins_identity,
                    control_identity=control_identity,
                    store_identity=store_status.identity,
                    claims_identity=claims_identity,
                )
                _create_windows_store_record(staged, raw)
                _paths._windows_close(claims)
                claims = 0
                _windows_flush_directory_binding(
                    control,
                    stage_name,
                    store_status.identity,
                )
                try:
                    _durable_windows_directory_rename(
                        staged,
                        control,
                        _TRANSACTION_STORE_COMPONENT,
                        store_status.identity,
                    )
                except FileExistsError:
                    reopened_staged = _paths._windows_open_child(
                        control,
                        stage_name,
                        directory=True,
                        read_data=True,
                        delete_access=True,
                    )
                    try:
                        reopened_status = _paths._windows_handle_status(reopened_staged)
                        if (
                            reopened_status.identity != store_status.identity
                            or reopened_status.is_reparse
                            or not _paths._windows_private_directory(
                                reopened_staged,
                                exact=True,
                            )
                        ):
                            raise OSError(
                                errno.ESTALE,
                                "losing transaction store changed",
                            )
                        _paths._windows_close(staged)
                    except BaseException:
                        _paths._windows_close(reopened_staged)
                        raise
                    staged = reopened_staged
                    claims = _paths._windows_open_child(
                        staged,
                        _TRANSACTION_CLAIMS_COMPONENT,
                        directory=True,
                        read_data=True,
                        delete_access=True,
                    )
                    claims_status = _paths._windows_handle_status(claims)
                    if (
                        claims_status.identity != claims_identity
                        or claims_status.is_reparse
                        or not _paths._windows_private_directory(claims, exact=True)
                    ):
                        raise OSError(
                            errno.ESTALE,
                            "losing transaction claims changed",
                        )
                    record = _open_windows_losing_store_record(
                        control,
                        stage_name,
                        staged,
                        store_status.identity,
                        claims,
                        claims_identity,
                        raw,
                    )
                    try:
                        _windows_delete_handle(record)
                    finally:
                        _paths._windows_close(record)
                    _windows_delete_handle(claims)
                    _paths._windows_close(claims)
                    claims = 0
                    if not _paths._windows_namespace_binds(
                        control, stage_name, store_status.identity
                    ):
                        raise OSError(errno.ESTALE, "losing transaction store changed")
                    _windows_delete_handle(staged)
                    _paths._windows_close(staged)
                    staged = 0
                    store = _paths._windows_open_child(
                        control,
                        _TRANSACTION_STORE_COMPONENT,
                        directory=True,
                        read_data=True,
                    )
                else:
                    store = staged
                    staged = 0
            finally:
                if staged:
                    _paths._windows_close(staged)
        store_status = _paths._windows_handle_status(store)
        store_identity = store_status.identity
        if not claims:
            claims = _paths._windows_open_child(
                store,
                _TRANSACTION_CLAIMS_COMPONENT,
                directory=True,
                read_data=True,
            )
        claims_status = _paths._windows_handle_status(claims)
        claims_identity = claims_status.identity
        if (
            store_identity[0] != plugins_identity[0]
            or claims_identity[0] != plugins_identity[0]
            or not _paths._windows_private_directory(store, exact=True)
            or not _paths._windows_private_directory(claims, exact=True)
            or not owned_root._filesystem_guard(store)
            or not owned_root._filesystem_guard(claims)
        ):
            raise _error("ownership.unowned", "The transaction store is not trusted.")
        raw, _record_identity = _read_windows_private_record(
            store,
            _TRANSACTION_STORE_CONTROL,
            volume=plugins_identity[0],
        )
        if not _valid_transaction_store_record(
            raw,
            plugins_identity=plugins_identity,
            control_identity=control_identity,
            store_identity=store_identity,
            claims_identity=claims_identity,
        ) or not _private_record_name_binds(
            store,
            _TRANSACTION_STORE_CONTROL,
            _record_identity,
            expected_raw=raw,
            windows=True,
        ):
            raise _error("ownership.unowned", "The transaction store is not trusted.")
        if not owned_root._validate_control_descriptor(control):
            raise _error("ownership.unowned", "The transaction store is not trusted.")
        opened = _TransactionStore(
            control,
            store,
            claims,
            plugins_identity,
            control_identity,
            store_identity,
            claims_identity,
            True,
        )
        if not _transaction_store_namespace_is_valid(opened):
            raise _error("ownership.unowned", "The transaction store is not trusted.")
        control = store = claims = 0
        return opened
    finally:
        for descriptor in (claims, store, control):
            if descriptor:
                _paths._windows_close(descriptor)


def _open_transaction_store(
    owned_root: OwnedRoot, *, create: bool
) -> _TransactionStore:
    if not isinstance(owned_root, OwnedRoot):
        raise _error("ownership.unowned", "The transaction store is not trusted.")
    if os.name == "nt":
        return _open_windows_transaction_store(owned_root, create=create)
    return _open_posix_transaction_store(owned_root, create=create)


def _transaction_claim_body(
    *,
    transaction_id: str,
    root_relative: str,
    quarantine_relative: str,
    claim_relative: str,
    plugins_identity: tuple[int, int],
    control_identity: tuple[int, int],
    store_identity: tuple[int, int],
    claims_identity: tuple[int, int],
    transaction_identity: tuple[int, int],
    claim_identity: tuple[int, int],
) -> dict[str, object]:
    return {
        "authority": "zagrosi-forge-transaction-authority-v1",
        "claim_identity": claim_identity,
        "claim_relative": claim_relative,
        "claims_identity": claims_identity,
        "control_identity": control_identity,
        "minimum_reader_version": _WRITER_VERSION,
        "plugins_identity": plugins_identity,
        "quarantine_relative": quarantine_relative,
        "record_kind": "persistent-transaction-root",
        "root_relative": root_relative,
        "schema_digest": _TRANSACTION_BINDING_SCHEMA_DIGEST,
        "schema_version": "1.0",
        "store_identity": store_identity,
        "transaction_id": transaction_id,
        "transaction_identity": transaction_identity,
        "writer_version": _WRITER_VERSION,
    }


def _binding_from_fields(
    *,
    transaction_id: str,
    plugins_identity: tuple[int, int],
    control_identity: tuple[int, int],
    store_identity: tuple[int, int],
    claims_identity: tuple[int, int],
    transaction_identity: tuple[int, int],
    claim_identity: tuple[int, int],
) -> tuple[PersistentTransactionBinding, bytes]:
    root_relative, quarantine_relative, claim_relative = (
        _persistent_transaction_references(transaction_id)
    )
    body = _transaction_claim_body(
        transaction_id=transaction_id,
        root_relative=root_relative,
        quarantine_relative=quarantine_relative,
        claim_relative=claim_relative,
        plugins_identity=plugins_identity,
        control_identity=control_identity,
        store_identity=store_identity,
        claims_identity=claims_identity,
        transaction_identity=transaction_identity,
        claim_identity=claim_identity,
    )
    claim_digest = hashlib.sha256(canonical_json_bytes(body)).hexdigest()
    body["record_digest"] = claim_digest
    binding = PersistentTransactionBinding(
        transaction_id=transaction_id,
        root_relative=root_relative,
        quarantine_relative=quarantine_relative,
        claim_relative=claim_relative,
        plugins_identity=plugins_identity,
        control_identity=control_identity,
        store_identity=store_identity,
        claims_identity=claims_identity,
        transaction_identity=transaction_identity,
        claim_identity=claim_identity,
        claim_digest=claim_digest,
        _token=_PERSISTENT_BINDING_TOKEN,
    )
    return binding, canonical_json_bytes(body, final_newline=True)


def _persistent_binding_invariants(value: object) -> bool:
    if type(value) is not PersistentTransactionBinding:
        return False
    binding = value
    try:
        if binding._seal is not _PERSISTENT_BINDING_TOKEN:
            return False
        root, quarantine, claim = _persistent_transaction_references(
            binding.transaction_id
        )
        if (
            binding.root_relative != root
            or binding.quarantine_relative != quarantine
            or binding.claim_relative != claim
            or not all(
                _file_identity_invariants(identity)
                for identity in (
                    binding.plugins_identity,
                    binding.control_identity,
                    binding.store_identity,
                    binding.claims_identity,
                    binding.transaction_identity,
                    binding.claim_identity,
                )
            )
            or binding.transaction_identity[0] != binding.plugins_identity[0]
        ):
            return False
        body = _transaction_claim_body(
            transaction_id=binding.transaction_id,
            root_relative=binding.root_relative,
            quarantine_relative=binding.quarantine_relative,
            claim_relative=binding.claim_relative,
            plugins_identity=binding.plugins_identity,
            control_identity=binding.control_identity,
            store_identity=binding.store_identity,
            claims_identity=binding.claims_identity,
            transaction_identity=binding.transaction_identity,
            claim_identity=binding.claim_identity,
        )
        return (
            hashlib.sha256(canonical_json_bytes(body)).hexdigest()
            == binding.claim_digest
        )
    except (AttributeError, TypeError, ValueError):
        return False


def _transaction_journal_identity_error(
    binding: PersistentTransactionBinding,
) -> ForgeError:
    return _error(
        "ownership.identity_mismatch",
        "The persistent transaction journal authority changed.",
        recovery=(binding.quarantine_relative,),
    )


def _transaction_journal_binding_bytes(
    binding: PersistentTransactionBinding,
) -> bytes:
    expected, raw = _binding_from_fields(
        transaction_id=binding.transaction_id,
        plugins_identity=binding.plugins_identity,
        control_identity=binding.control_identity,
        store_identity=binding.store_identity,
        claims_identity=binding.claims_identity,
        transaction_identity=binding.transaction_identity,
        claim_identity=binding.claim_identity,
    )
    if expected != binding:
        raise _transaction_journal_identity_error(binding)
    return raw


def _transaction_journal_store_is_valid(
    store: _TransactionStore,
    binding: PersistentTransactionBinding,
    filesystem_guard: _paths.FilesystemGuard,
) -> bool:
    if (
        store.windows != (os.name == "nt")
        or store.plugins_identity != binding.plugins_identity
        or store.control_identity != binding.control_identity
        or store.store_identity != binding.store_identity
        or store.claims_identity != binding.claims_identity
        or not _transaction_store_namespace_is_valid(store)
    ):
        return False
    expected = (
        binding.control_identity,
        binding.store_identity,
        binding.claims_identity,
    )
    descriptors = (store.control, store.store, store.claims)
    try:
        if store.windows:
            for descriptor, identity in zip(descriptors, expected, strict=True):
                windows_status = _paths._windows_handle_status(descriptor)
                if (
                    windows_status.identity != identity
                    or windows_status.is_reparse
                    or not _paths._windows_private_directory(descriptor, exact=True)
                    or not filesystem_guard(descriptor)
                ):
                    return False
            return True
        for descriptor, identity in zip(descriptors, expected, strict=True):
            posix_status = os.fstat(descriptor)
            if (
                (posix_status.st_dev, posix_status.st_ino) != identity
                or not _paths._private_directory(descriptor, posix_status, exact=True)
                or not filesystem_guard(descriptor)
            ):
                return False
        return True
    except (ForgeError, OSError):
        return False


def _transaction_journal_location_is_valid(
    store: _TransactionStore,
    descriptor: int,
    binding: PersistentTransactionBinding,
    journal_relative: str,
    filesystem_guard: _paths.FilesystemGuard,
) -> bool:
    component = journal_relative.rsplit("/", 1)[-1]
    try:
        if store.windows:
            windows_status = _paths._windows_handle_status(descriptor)
            return (
                windows_status.identity == binding.transaction_identity
                and not windows_status.is_reparse
                and _paths._windows_private_directory(descriptor, exact=True)
                and filesystem_guard(descriptor)
                and _paths._windows_namespace_binds(
                    store.store,
                    component,
                    binding.transaction_identity,
                )
            )
        posix_status = os.fstat(descriptor)
        return (
            (posix_status.st_dev, posix_status.st_ino) == binding.transaction_identity
            and _paths._private_directory(descriptor, posix_status, exact=True)
            and filesystem_guard(descriptor)
            and _paths._posix_namespace_binds(
                store.store,
                component,
                binding.transaction_identity,
            )
        )
    except (ForgeError, OSError):
        return False


def _transaction_journal_records_are_valid(
    store: _TransactionStore,
    binding: PersistentTransactionBinding,
) -> bool:
    try:
        if store.windows:
            store_raw, store_record_identity = _read_windows_private_record(
                store.store,
                _TRANSACTION_STORE_CONTROL,
                volume=binding.plugins_identity[0],
            )
            claim_raw, claim_identity = _read_windows_private_record(
                store.claims,
                f"{binding.transaction_id}.json",
                volume=binding.plugins_identity[0],
            )
        else:
            store_raw, store_record_identity = _read_posix_private_record(
                store.store,
                _TRANSACTION_STORE_CONTROL,
                device=binding.plugins_identity[0],
            )
            claim_raw, claim_identity = _read_posix_private_record(
                store.claims,
                f"{binding.transaction_id}.json",
                device=binding.plugins_identity[0],
            )
        return (
            _valid_transaction_store_record(
                store_raw,
                plugins_identity=binding.plugins_identity,
                control_identity=binding.control_identity,
                store_identity=binding.store_identity,
                claims_identity=binding.claims_identity,
            )
            and _private_record_name_binds(
                store.store,
                _TRANSACTION_STORE_CONTROL,
                store_record_identity,
                expected_raw=store_raw,
                windows=store.windows,
            )
            and claim_identity == binding.claim_identity
            and claim_raw == _transaction_journal_binding_bytes(binding)
            and _private_record_name_binds(
                store.claims,
                f"{binding.transaction_id}.json",
                binding.claim_identity,
                expected_raw=claim_raw,
                windows=store.windows,
            )
        )
    except (ForgeError, OSError, TypeError, ValueError):
        return False


def _try_windows_transaction_state_lock(
    handle: int,
) -> ctypes.Structure | None:
    """Lock one byte beyond every allowed anchor read without extending the file."""

    from ctypes import wintypes

    overlapped = _kernel_lock._new_overlapped()
    setattr(overlapped, "Offset", _TRANSACTION_STATE_LOCK_OFFSET & 0xFFFFFFFF)
    setattr(
        overlapped,
        "OffsetHigh",
        (_TRANSACTION_STATE_LOCK_OFFSET >> 32) & 0xFFFFFFFF,
    )
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
    if number == 33:
        return None
    raise OSError(number, "transaction state LockFileEx failed")


def _unlock_windows_transaction_state_lock(
    handle: int,
    overlapped: ctypes.Structure,
) -> None:
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
        raise OSError(
            _paths._windows_last_error(),
            "transaction state UnlockFileEx failed",
        )


@dataclass(slots=True)
class _TransactionStateLock:
    descriptor: int
    windows: bool
    overlapped: ctypes.Structure | None

    def close(self) -> None:
        empty = 0 if self.windows else -1
        descriptor = self.descriptor
        if descriptor == empty:
            return
        overlapped = self.overlapped
        self.descriptor = empty
        self.overlapped = None
        try:
            if self.windows:
                if overlapped is not None:
                    _unlock_windows_transaction_state_lock(
                        descriptor,
                        overlapped,
                    )
            else:
                _kernel_lock._unlock_posix(descriptor)
        finally:
            if self.windows:
                _paths._windows_close(descriptor)
            else:
                os.close(descriptor)


def _transaction_state_lock_descriptor_is_valid(
    store: _TransactionStore,
    binding: PersistentTransactionBinding,
    descriptor: int,
) -> bool:
    component = f"{binding.transaction_id}.json"
    try:
        expected_raw = _transaction_journal_binding_bytes(binding)
        if store.windows:
            windows_before = _paths._windows_handle_status(descriptor)
            raw = _paths._windows_read(
                descriptor,
                limit=_TRANSACTION_RECORD_LIMIT,
            )
            windows_after = _paths._windows_handle_status(descriptor)
            descriptor_is_valid = (
                windows_before.identity == binding.claim_identity
                and not windows_before.is_directory
                and not windows_before.is_reparse
                and windows_before.link_count == 1
                and windows_before.identity[0] == binding.plugins_identity[0]
                and windows_before.size == len(expected_raw)
                and raw == expected_raw
                and windows_after.identity == windows_before.identity
                and windows_after.fingerprint == windows_before.fingerprint
                and _paths._windows_private_authorization(
                    descriptor,
                    exact=True,
                )
            )
        else:
            posix_before = os.fstat(descriptor)
            raw = os.pread(descriptor, _TRANSACTION_RECORD_LIMIT + 1, 0)
            posix_after = os.fstat(descriptor)
            descriptor_is_valid = (
                (posix_before.st_dev, posix_before.st_ino) == binding.claim_identity
                and stat.S_ISREG(posix_before.st_mode)
                and posix_before.st_uid == os.geteuid()
                and posix_before.st_gid == os.getegid()
                and stat.S_IMODE(posix_before.st_mode) == 0o600
                and posix_before.st_nlink == 1
                and posix_before.st_dev == binding.plugins_identity[0]
                and posix_before.st_size == len(expected_raw)
                and raw == expected_raw
                and (posix_after.st_dev, posix_after.st_ino) == binding.claim_identity
                and _paths._posix_status_fingerprint(posix_before)
                == _paths._posix_status_fingerprint(posix_after)
                and _paths._posix_security_metadata_supported(
                    descriptor,
                    posix_after,
                )
            )
        return (
            descriptor_is_valid
            and _transaction_store_namespace_is_valid(store)
            and _transaction_journal_records_are_valid(store, binding)
            and _private_record_name_binds(
                store.claims,
                component,
                binding.claim_identity,
                expected_raw=expected_raw,
                windows=store.windows,
            )
        )
    except (AttributeError, ForgeError, OSError, TypeError, ValueError):
        return False


def _acquire_transaction_state_lock(
    store: _TransactionStore,
    binding: PersistentTransactionBinding,
) -> _TransactionStateLock:
    """Serialize exact transaction cleanup state across threads and processes."""

    descriptor = 0 if store.windows else -1
    acquired = False
    overlapped: ctypes.Structure | None = None
    try:
        component = f"{binding.transaction_id}.json"
        if store.windows:
            descriptor = _windows_open_raw_child(
                store.claims,
                component,
                directory=False,
                read_data=True,
                write_data=True,
            )
        else:
            descriptor = os.open(
                component,
                _paths._posix_file_flags() | os.O_RDWR,
                dir_fd=store.claims,
            )
        if not _transaction_state_lock_descriptor_is_valid(
            store,
            binding,
            descriptor,
        ):
            raise OSError(errno.ESTALE, "transaction state lock authority changed")
        deadline = time.monotonic() + _TRANSACTION_STATE_LOCK_TIMEOUT_SECONDS
        while True:
            if store.windows:
                overlapped = _try_windows_transaction_state_lock(descriptor)
                acquired = overlapped is not None
            else:
                acquired = _kernel_lock._try_posix_lock(descriptor)
            if acquired:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise OSError(
                    errno.ETIMEDOUT,
                    "transaction state lock acquisition timed out",
                )
            time.sleep(min(_TRANSACTION_STATE_LOCK_POLL_SECONDS, remaining))
        if not _transaction_state_lock_descriptor_is_valid(
            store,
            binding,
            descriptor,
        ):
            raise OSError(errno.ESTALE, "transaction state lock authority changed")
        selected = _TransactionStateLock(
            descriptor,
            store.windows,
            overlapped,
        )
        descriptor = 0 if store.windows else -1
        acquired = False
        overlapped = None
        return selected
    finally:
        if acquired:
            try:
                if store.windows:
                    if overlapped is not None:
                        _unlock_windows_transaction_state_lock(
                            descriptor,
                            overlapped,
                        )
                else:
                    _kernel_lock._unlock_posix(descriptor)
            finally:
                if store.windows:
                    if descriptor:
                        _paths._windows_close(descriptor)
                        descriptor = 0
                elif descriptor >= 0:
                    os.close(descriptor)
                    descriptor = -1
        if store.windows:
            if descriptor:
                _paths._windows_close(descriptor)
        elif descriptor >= 0:
            os.close(descriptor)


class TransactionJournalAccess:
    """Sealed authority for one exact live or quarantined transaction journal."""

    _binding: PersistentTransactionBinding
    _closed: bool
    _descriptor: int
    _filesystem_guard: _paths.FilesystemGuard
    _journal_relative: str
    _location: TransactionLocation
    _lock: LockType
    _namespace: _paths._NamespaceCapability | None
    _seal: object
    _store: _TransactionStore | None
    _writable: bool

    __slots__ = (
        "_binding",
        "_closed",
        "_descriptor",
        "_filesystem_guard",
        "_journal_relative",
        "_location",
        "_lock",
        "_namespace",
        "_seal",
        "_store",
        "_writable",
    )

    def __init__(
        self,
        *,
        binding: PersistentTransactionBinding,
        location: TransactionLocation,
        journal_relative: str,
        writable: bool,
        descriptor: int,
        store: _TransactionStore,
        namespace: _paths._NamespaceCapability,
        filesystem_guard: _paths.FilesystemGuard,
        _token: object,
    ) -> None:
        valid_relative = (
            location is TransactionLocation.LIVE
            and journal_relative == binding.root_relative
        ) or (
            location is TransactionLocation.QUARANTINED
            and _persistent_cleanup_reference_is_valid(binding, journal_relative)
        )
        if (
            _token is not _TRANSACTION_JOURNAL_ACCESS_TOKEN
            or not _persistent_binding_invariants(binding)
            or location
            not in (TransactionLocation.LIVE, TransactionLocation.QUARANTINED)
            or type(writable) is not bool
            or (writable and location is not TransactionLocation.LIVE)
            or not valid_relative
        ):
            raise TypeError(
                "transaction journal access is created only by ownership authority"
            )
        object.__setattr__(self, "_binding", binding)
        object.__setattr__(self, "_location", location)
        object.__setattr__(self, "_journal_relative", journal_relative)
        object.__setattr__(self, "_writable", writable)
        object.__setattr__(self, "_descriptor", descriptor)
        object.__setattr__(self, "_store", store)
        object.__setattr__(self, "_namespace", namespace)
        object.__setattr__(self, "_filesystem_guard", filesystem_guard)
        object.__setattr__(self, "_closed", False)
        object.__setattr__(self, "_lock", Lock())
        object.__setattr__(self, "_seal", _TRANSACTION_JOURNAL_ACCESS_TOKEN)

    @property
    def binding(self) -> PersistentTransactionBinding:
        return self._binding

    @property
    def location(self) -> TransactionLocation:
        return self._location

    @property
    def journal_relative(self) -> str:
        return self._journal_relative

    @property
    def read_only(self) -> bool:
        return not self._writable

    def _require_journal_access(self, *, write: bool) -> None:
        with self._lock:
            self._validate_locked(write=write)

    def _validate_locked(self, *, write: bool) -> None:
        binding = self._binding
        if (
            type(write) is not bool
            or self._closed
            or self._seal is not _TRANSACTION_JOURNAL_ACCESS_TOKEN
            or not _persistent_binding_invariants(binding)
            or self._location
            not in (TransactionLocation.LIVE, TransactionLocation.QUARANTINED)
            or type(self._writable) is not bool
        ):
            raise _transaction_journal_identity_error(binding)
        if write and not self._writable:
            raise _error(
                "ownership.unowned",
                "This transaction journal capability is read-only.",
                recovery=(binding.quarantine_relative,),
            )
        namespace = self._namespace
        store = self._store
        try:
            if (
                namespace is None
                or store is None
                or not namespace._validate_namespace_binding()
                or _native_identity(namespace._plugins_descriptor)
                != binding.plugins_identity
                or _native_identity(namespace._control_descriptor)
                != binding.control_identity
                or not _transaction_journal_store_is_valid(
                    store, binding, self._filesystem_guard
                )
                or not _transaction_journal_records_are_valid(store, binding)
                or not _transaction_journal_location_is_valid(
                    store,
                    self._descriptor,
                    binding,
                    self._journal_relative,
                    self._filesystem_guard,
                )
                or not namespace._validate_namespace_binding()
                or not _transaction_store_namespace_is_valid(store)
            ):
                raise _transaction_journal_identity_error(binding)
        except (AttributeError, ForgeError, OSError, TypeError, ValueError) as exc:
            if (
                isinstance(exc, ForgeError)
                and exc.code == "ownership.identity_mismatch"
            ):
                raise
            raise _transaction_journal_identity_error(binding) from None

    def _duplicate_journal_descriptor(self, *, write: bool) -> int:
        with self._lock:
            self._validate_locked(write=write)
            duplicate = (
                _paths._windows_duplicate(self._descriptor)
                if os.name == "nt"
                else os.dup(self._descriptor)
            )
            try:
                self._validate_locked(write=write)
                if _native_identity(duplicate) != self._binding.transaction_identity:
                    raise _transaction_journal_identity_error(self._binding)
                return duplicate
            except BaseException:
                _close_native(duplicate)
                raise

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            descriptor = self._descriptor
            store = self._store
            namespace = self._namespace
            object.__setattr__(self, "_descriptor", 0 if os.name == "nt" else -1)
            object.__setattr__(self, "_store", None)
            object.__setattr__(self, "_namespace", None)
            object.__setattr__(self, "_closed", True)
            _close_native(descriptor)
            if store is not None:
                store.close()
            if namespace is not None:
                namespace.close()

    def __enter__(self) -> TransactionJournalAccess:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def __setattr__(self, _name: str, _value: object) -> Never:
        raise AttributeError("transaction journal access is read-only")

    def __reduce__(self) -> Never:
        raise TypeError("ownership capabilities are not serializable")


def _quarantined_recovery_ticket_is_live(
    ticket: object,
    binding: PersistentTransactionBinding,
    journal_relative: str,
) -> bool:
    if (
        type(ticket) is not QuarantineTicket
        or not _persistent_binding_invariants(binding)
        or journal_relative != binding.quarantine_relative
    ):
        return False
    selected = ticket
    try:
        with selected._lock:
            namespace = selected._namespace
            root = selected._root
            return (
                not selected._closed
                and not selected._used
                and selected._binding == binding
                and selected._recovery_reference == journal_relative
                and selected._identity == binding.transaction_identity
                and selected._root_identity == binding.plugins_identity
                and namespace is not None
                and root != (0 if os.name == "nt" else -1)
                and _native_identity(root) == binding.plugins_identity
                and namespace._validate_namespace_binding()
                and _native_identity(namespace._plugins_descriptor)
                == binding.plugins_identity
                and _native_identity(namespace._control_descriptor)
                == binding.control_identity
            )
    except (AttributeError, ForgeError, OSError, TypeError, ValueError):
        return False


def _quarantined_recovery_authority_is_valid(
    root: int,
    namespace: _paths._NamespaceCapability | None,
    binding: PersistentTransactionBinding,
) -> bool:
    try:
        return (
            namespace is not None
            and root != (0 if os.name == "nt" else -1)
            and _native_identity(root) == binding.plugins_identity
            and namespace._validate_namespace_binding()
            and _native_identity(namespace._plugins_descriptor)
            == binding.plugins_identity
            and _native_identity(namespace._control_descriptor)
            == binding.control_identity
        )
    except (AttributeError, ForgeError, OSError, TypeError, ValueError):
        return False


def _quarantined_recovery_cleanup_is_absent(
    store: _TransactionStore,
    binding: PersistentTransactionBinding,
) -> bool:
    try:
        return (
            _load_transaction_cleanup_record(store, binding, complete=False) is None
            and _load_transaction_cleanup_record(store, binding, complete=True) is None
            and _transaction_journal_records_are_valid(store, binding)
            and _transaction_store_namespace_is_valid(store)
        )
    except (AttributeError, ForgeError, OSError, TypeError, ValueError):
        return False


class _QuarantinedRecoveryJournalWriter:
    """Transferred writer authority for one exact quarantined recovery journal."""

    _binding: PersistentTransactionBinding
    _closed: bool
    _descriptor: int
    _directory_writer_taken: bool
    _filesystem_guard: _paths.FilesystemGuard
    _journal_relative: str
    _lock: LockType
    _namespace: _paths._NamespaceCapability | None
    _origin: _paths._AuthorityOrigin
    _seal: object
    _store: _TransactionStore | None
    _ticket_namespace: _paths._NamespaceCapability | None
    _ticket_root: int

    __slots__ = (
        "_binding",
        "_closed",
        "_descriptor",
        "_directory_writer_taken",
        "_filesystem_guard",
        "_journal_relative",
        "_lock",
        "_namespace",
        "_origin",
        "_seal",
        "_store",
        "_ticket_namespace",
        "_ticket_root",
    )

    def __init__(
        self,
        *,
        binding: PersistentTransactionBinding,
        journal_relative: str,
        descriptor: int,
        store: _TransactionStore,
        namespace: _paths._NamespaceCapability,
        filesystem_guard: _paths.FilesystemGuard,
        origin: _paths._AuthorityOrigin,
        ticket_root: int,
        ticket_namespace: _paths._NamespaceCapability,
        _token: object,
    ) -> None:
        if (
            _token is not _QUARANTINED_RECOVERY_JOURNAL_WRITER_TOKEN
            or not _persistent_binding_invariants(binding)
            or journal_relative != binding.quarantine_relative
            or type(store) is not _TransactionStore
            or type(namespace) is not _paths._NamespaceCapability
            or type(origin) is not _paths._AuthorityOrigin
            or type(ticket_namespace) is not _paths._NamespaceCapability
            or not _quarantined_recovery_authority_is_valid(
                ticket_root,
                ticket_namespace,
                binding,
            )
        ):
            raise TypeError(
                "quarantined recovery journal writers are transferred internally"
            )
        object.__setattr__(self, "_binding", binding)
        object.__setattr__(self, "_journal_relative", journal_relative)
        object.__setattr__(self, "_descriptor", descriptor)
        object.__setattr__(self, "_store", store)
        object.__setattr__(self, "_namespace", namespace)
        object.__setattr__(self, "_filesystem_guard", filesystem_guard)
        object.__setattr__(self, "_origin", origin)
        object.__setattr__(self, "_ticket_root", ticket_root)
        object.__setattr__(self, "_ticket_namespace", ticket_namespace)
        object.__setattr__(self, "_directory_writer_taken", False)
        object.__setattr__(self, "_closed", False)
        object.__setattr__(self, "_lock", Lock())
        object.__setattr__(self, "_seal", _QUARANTINED_RECOVERY_JOURNAL_WRITER_TOKEN)

    @property
    def binding(self) -> PersistentTransactionBinding:
        return self._binding

    @property
    def location(self) -> TransactionLocation:
        return TransactionLocation.QUARANTINED

    @property
    def journal_relative(self) -> str:
        return self._journal_relative

    def _validate_locked(self) -> None:
        binding = self._binding
        namespace = self._namespace
        store = self._store
        try:
            if (
                self._closed
                or self._seal is not _QUARANTINED_RECOVERY_JOURNAL_WRITER_TOKEN
                or not _persistent_binding_invariants(binding)
                or self._journal_relative != binding.quarantine_relative
                or type(self._directory_writer_taken) is not bool
                or not _quarantined_recovery_authority_is_valid(
                    self._ticket_root,
                    self._ticket_namespace,
                    binding,
                )
                or namespace is None
                or store is None
                or not namespace._validate_namespace_binding()
                or _native_identity(namespace._plugins_descriptor)
                != binding.plugins_identity
                or _native_identity(namespace._control_descriptor)
                != binding.control_identity
                or not _transaction_journal_store_is_valid(
                    store,
                    binding,
                    self._filesystem_guard,
                )
                or not _transaction_journal_records_are_valid(store, binding)
                or not _quarantined_recovery_cleanup_is_absent(store, binding)
                or not _transaction_journal_location_is_valid(
                    store,
                    self._descriptor,
                    binding,
                    self._journal_relative,
                    self._filesystem_guard,
                )
                or not namespace._validate_namespace_binding()
                or not _transaction_store_namespace_is_valid(store)
            ):
                raise _transaction_journal_identity_error(binding)
        except (AttributeError, ForgeError, OSError, TypeError, ValueError) as exc:
            if (
                isinstance(exc, ForgeError)
                and exc.code == "ownership.identity_mismatch"
            ):
                raise
            raise _transaction_journal_identity_error(binding) from None

    def _require_recovery_journal_access(self) -> None:
        with self._lock:
            self._validate_locked()

    def _require_journal_access(self, *, write: bool) -> None:
        if type(write) is not bool:
            raise _transaction_journal_identity_error(self._binding)
        self._require_recovery_journal_access()

    def _duplicate_journal_descriptor(self, *, write: bool) -> int:
        if type(write) is not bool:
            raise _transaction_journal_identity_error(self._binding)
        with self._lock:
            self._validate_locked()
            duplicate = (
                _paths._windows_duplicate(self._descriptor)
                if os.name == "nt"
                else os.dup(self._descriptor)
            )
            try:
                self._validate_locked()
                if _native_identity(duplicate) != self._binding.transaction_identity:
                    raise _transaction_journal_identity_error(self._binding)
                return duplicate
            except BaseException:
                _close_native(duplicate)
                raise

    def _take_directory_writer(self) -> _paths.OwnedDirectoryWriter:
        with self._lock:
            self._validate_locked()
            if self._directory_writer_taken:
                raise _error(
                    "ownership.unowned",
                    "The quarantined recovery journal writer was already transferred.",
                    recovery=(self._binding.quarantine_relative,),
                )
            namespace = self._namespace
            if namespace is None:
                raise _transaction_journal_identity_error(self._binding)
            descriptor = 0 if os.name == "nt" else -1
            writer_namespace: _paths._NamespaceCapability | None = None
            writer: _paths.OwnedDirectoryWriter | None = None
            try:
                descriptor = (
                    _paths._windows_duplicate(self._descriptor)
                    if os.name == "nt"
                    else os.dup(self._descriptor)
                )
                writer_namespace = _paths._duplicate_namespace_capability(
                    namespace._home_descriptor,
                    namespace._plugins_descriptor,
                    namespace._control_descriptor,
                    home_identity=namespace._home_identity,
                    plugins_identity=namespace._plugins_identity,
                    control_identity=namespace._control_identity,
                    filesystem_guard=self._filesystem_guard,
                    windows=os.name == "nt",
                )
                writer = _paths.OwnedDirectoryWriter(
                    descriptor,
                    self._binding.transaction_identity,
                    writer_namespace,
                    self._filesystem_guard,
                    self._origin,
                    windows=os.name == "nt",
                    _token=_paths._CAPABILITY_TOKEN,
                )
                descriptor = 0 if os.name == "nt" else -1
                writer_namespace = None
                writer._require_open()
                self._validate_locked()
                object.__setattr__(self, "_directory_writer_taken", True)
                return writer
            except BaseException:
                if writer is not None:
                    writer.close()
                elif writer_namespace is not None:
                    writer_namespace.close()
                if os.name == "nt" and descriptor:
                    _paths._windows_close(descriptor)
                elif os.name != "nt" and descriptor >= 0:
                    os.close(descriptor)
                raise

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            descriptor = self._descriptor
            store = self._store
            namespace = self._namespace
            ticket_root = self._ticket_root
            ticket_namespace = self._ticket_namespace
            object.__setattr__(self, "_descriptor", 0 if os.name == "nt" else -1)
            object.__setattr__(self, "_store", None)
            object.__setattr__(self, "_namespace", None)
            object.__setattr__(self, "_ticket_root", 0 if os.name == "nt" else -1)
            object.__setattr__(self, "_ticket_namespace", None)
            object.__setattr__(self, "_closed", True)
            _close_native(descriptor)
            if store is not None:
                store.close()
            if namespace is not None:
                namespace.close()
            _close_native(ticket_root)
            if ticket_namespace is not None:
                ticket_namespace.close()

    def __enter__(self) -> _QuarantinedRecoveryJournalWriter:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def __setattr__(self, _name: str, _value: object) -> Never:
        raise AttributeError("quarantined recovery journal writers are read-only")

    def __reduce__(self) -> Never:
        raise TypeError("ownership capabilities are not serializable")


class QuarantinedRecoveryJournalAccess:
    """One-shot authority for journal completion after exact root quarantine."""

    _binding: PersistentTransactionBinding
    _closed: bool
    _descriptor: int
    _filesystem_guard: _paths.FilesystemGuard
    _journal_relative: str
    _lock: LockType
    _namespace: _paths._NamespaceCapability | None
    _origin: _paths._AuthorityOrigin
    _seal: object
    _store: _TransactionStore | None
    _ticket_namespace: _paths._NamespaceCapability | None
    _ticket_root: int
    _transferred: bool

    __slots__ = (
        "_binding",
        "_closed",
        "_descriptor",
        "_filesystem_guard",
        "_journal_relative",
        "_lock",
        "_namespace",
        "_origin",
        "_seal",
        "_store",
        "_ticket_namespace",
        "_ticket_root",
        "_transferred",
    )

    def __init__(
        self,
        *,
        binding: PersistentTransactionBinding,
        journal_relative: str,
        descriptor: int,
        store: _TransactionStore,
        namespace: _paths._NamespaceCapability,
        filesystem_guard: _paths.FilesystemGuard,
        origin: _paths._AuthorityOrigin,
        ticket_root: int,
        ticket_namespace: _paths._NamespaceCapability,
        _token: object,
    ) -> None:
        if (
            _token is not _QUARANTINED_RECOVERY_JOURNAL_ACCESS_TOKEN
            or not _persistent_binding_invariants(binding)
            or journal_relative != binding.quarantine_relative
            or type(store) is not _TransactionStore
            or type(namespace) is not _paths._NamespaceCapability
            or type(origin) is not _paths._AuthorityOrigin
            or type(ticket_namespace) is not _paths._NamespaceCapability
            or not _quarantined_recovery_authority_is_valid(
                ticket_root,
                ticket_namespace,
                binding,
            )
        ):
            raise TypeError(
                "quarantined recovery journal access is created only by "
                "ownership authority"
            )
        object.__setattr__(self, "_binding", binding)
        object.__setattr__(self, "_journal_relative", journal_relative)
        object.__setattr__(self, "_descriptor", descriptor)
        object.__setattr__(self, "_store", store)
        object.__setattr__(self, "_namespace", namespace)
        object.__setattr__(self, "_filesystem_guard", filesystem_guard)
        object.__setattr__(self, "_origin", origin)
        object.__setattr__(self, "_ticket_root", ticket_root)
        object.__setattr__(self, "_ticket_namespace", ticket_namespace)
        object.__setattr__(self, "_transferred", False)
        object.__setattr__(self, "_closed", False)
        object.__setattr__(self, "_lock", Lock())
        object.__setattr__(self, "_seal", _QUARANTINED_RECOVERY_JOURNAL_ACCESS_TOKEN)

    @property
    def binding(self) -> PersistentTransactionBinding:
        return self._binding

    @property
    def location(self) -> TransactionLocation:
        return TransactionLocation.QUARANTINED

    @property
    def journal_relative(self) -> str:
        return self._journal_relative

    def _validate_locked(self) -> None:
        binding = self._binding
        namespace = self._namespace
        store = self._store
        try:
            if (
                self._closed
                or self._transferred
                or self._seal is not _QUARANTINED_RECOVERY_JOURNAL_ACCESS_TOKEN
                or not _persistent_binding_invariants(binding)
                or self._journal_relative != binding.quarantine_relative
                or not _quarantined_recovery_authority_is_valid(
                    self._ticket_root,
                    self._ticket_namespace,
                    binding,
                )
                or namespace is None
                or store is None
                or not namespace._validate_namespace_binding()
                or _native_identity(namespace._plugins_descriptor)
                != binding.plugins_identity
                or _native_identity(namespace._control_descriptor)
                != binding.control_identity
                or not _transaction_journal_store_is_valid(
                    store,
                    binding,
                    self._filesystem_guard,
                )
                or not _transaction_journal_records_are_valid(store, binding)
                or not _quarantined_recovery_cleanup_is_absent(store, binding)
                or not _transaction_journal_location_is_valid(
                    store,
                    self._descriptor,
                    binding,
                    self._journal_relative,
                    self._filesystem_guard,
                )
                or not namespace._validate_namespace_binding()
                or not _transaction_store_namespace_is_valid(store)
            ):
                raise _transaction_journal_identity_error(binding)
        except (AttributeError, ForgeError, OSError, TypeError, ValueError) as exc:
            if (
                isinstance(exc, ForgeError)
                and exc.code == "ownership.identity_mismatch"
            ):
                raise
            raise _transaction_journal_identity_error(binding) from None

    def _require_recovery_journal_access(self) -> None:
        with self._lock:
            self._validate_locked()

    def _take_recovery_writer(self) -> _QuarantinedRecoveryJournalWriter:
        with self._lock:
            self._validate_locked()
            store = self._store
            namespace = self._namespace
            ticket_namespace = self._ticket_namespace
            if store is None or namespace is None:
                raise _transaction_journal_identity_error(self._binding)
            if ticket_namespace is None:
                raise _transaction_journal_identity_error(self._binding)
            writer = _QuarantinedRecoveryJournalWriter(
                binding=self._binding,
                journal_relative=self._journal_relative,
                descriptor=self._descriptor,
                store=store,
                namespace=namespace,
                filesystem_guard=self._filesystem_guard,
                origin=self._origin,
                ticket_root=self._ticket_root,
                ticket_namespace=ticket_namespace,
                _token=_QUARANTINED_RECOVERY_JOURNAL_WRITER_TOKEN,
            )
            writer._require_recovery_journal_access()
            object.__setattr__(self, "_descriptor", 0 if os.name == "nt" else -1)
            object.__setattr__(self, "_store", None)
            object.__setattr__(self, "_namespace", None)
            object.__setattr__(self, "_ticket_root", 0 if os.name == "nt" else -1)
            object.__setattr__(self, "_ticket_namespace", None)
            object.__setattr__(self, "_transferred", True)
            object.__setattr__(self, "_closed", True)
            return writer

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            descriptor = self._descriptor
            store = self._store
            namespace = self._namespace
            ticket_root = self._ticket_root
            ticket_namespace = self._ticket_namespace
            object.__setattr__(self, "_descriptor", 0 if os.name == "nt" else -1)
            object.__setattr__(self, "_store", None)
            object.__setattr__(self, "_namespace", None)
            object.__setattr__(self, "_ticket_root", 0 if os.name == "nt" else -1)
            object.__setattr__(self, "_ticket_namespace", None)
            object.__setattr__(self, "_closed", True)
            _close_native(descriptor)
            if store is not None:
                store.close()
            if namespace is not None:
                namespace.close()
            _close_native(ticket_root)
            if ticket_namespace is not None:
                ticket_namespace.close()

    def __enter__(self) -> QuarantinedRecoveryJournalAccess:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def __setattr__(self, _name: str, _value: object) -> Never:
        raise AttributeError("quarantined recovery journal access is read-only")

    def __reduce__(self) -> Never:
        raise TypeError("ownership capabilities are not serializable")


def _read_transaction_record_if_present(
    store: _TransactionStore, component: str
) -> tuple[bytes, tuple[int, int]] | None:
    try:
        if store.windows:
            return _read_windows_private_record(
                store.claims,
                component,
                volume=store.plugins_identity[0],
            )
        return _read_posix_private_record(
            store.claims,
            component,
            device=store.plugins_identity[0],
        )
    except OSError as exc:
        if isinstance(exc, FileNotFoundError) or (
            store.windows and getattr(exc, "winerror", None) in {2, 3}
        ):
            return None
        raise


def _transaction_name_exists(
    parent: int, component: str, *, directory: bool, windows: bool
) -> bool:
    descriptor = 0 if windows else -1
    try:
        if windows:
            descriptor = _paths._windows_open_child(
                parent,
                component,
                directory=directory,
            )
        else:
            os.stat(component, dir_fd=parent, follow_symlinks=False)
        return True
    except OSError as exc:
        if isinstance(exc, FileNotFoundError) or (
            windows and getattr(exc, "winerror", None) in {2, 3}
        ):
            return False
        raise
    finally:
        if windows and descriptor:
            _paths._windows_close(descriptor)


def _load_transaction_creation_intent(
    store: _TransactionStore, transaction_id: str
) -> _TransactionCreationIntent | None:
    component = _transaction_creation_intent_component(transaction_id)
    observed = _read_transaction_record_if_present(store, component)
    if observed is None:
        return None
    raw, identity = observed
    return _transaction_creation_intent_from_record(
        store,
        transaction_id=transaction_id,
        raw=raw,
        identity=identity,
    )


def _load_transaction_creation_reservation(
    store: _TransactionStore,
    transaction_id: str,
) -> _TransactionCreationReservation | None:
    component = _transaction_creation_reservation_component(transaction_id)
    observed = _read_transaction_record_if_present(store, component)
    if observed is None:
        return None
    raw, identity = observed
    return _transaction_creation_reservation_from_record(
        store,
        transaction_id=transaction_id,
        raw=raw,
        identity=identity,
    )


def _publish_transaction_creation_reservation(
    store: _TransactionStore,
    *,
    transaction_id: str,
) -> _TransactionCreationReservation:
    component = _transaction_creation_reservation_component(transaction_id)
    stage_component = f".root-{secrets.token_hex(16)}.tmp"
    pending_claim_component = f".claim-{secrets.token_hex(16)}.tmp"
    raw = _transaction_creation_reservation_bytes(
        store,
        transaction_id=transaction_id,
        stage_component=stage_component,
        pending_claim_component=pending_claim_component,
    )
    identity = _publish_transaction_record(store, component, raw)
    return _transaction_creation_reservation_from_record(
        store,
        transaction_id=transaction_id,
        raw=raw,
        identity=identity,
    )


def _publish_transaction_creation_intent(
    store: _TransactionStore,
    *,
    transaction_id: str,
    reservation: _TransactionCreationReservation,
    binding: PersistentTransactionBinding,
) -> _TransactionCreationIntent:
    component = _transaction_creation_intent_component(transaction_id)
    raw = _transaction_creation_intent_bytes(
        store,
        transaction_id=transaction_id,
        reservation=reservation,
        binding=binding,
    )
    identity = _publish_transaction_record(store, component, raw)
    return _transaction_creation_intent_from_record(
        store,
        transaction_id=transaction_id,
        raw=raw,
        identity=identity,
    )


def _require_new_transaction_namespace(
    store: _TransactionStore, transaction_id: str
) -> None:
    final_claim = f"{transaction_id}.json"
    if _transaction_name_exists(
        store.store,
        transaction_id,
        directory=True,
        windows=store.windows,
    ) or _transaction_name_exists(
        store.claims,
        final_claim,
        directory=False,
        windows=store.windows,
    ):
        raise OSError(errno.EEXIST, "transaction state has no creation intent")


def _transaction_directory_is_exact(
    store: _TransactionStore,
    component: str,
    descriptor: int,
    expected_identity: tuple[int, int],
    *,
    filesystem_guard: _paths.FilesystemGuard,
) -> bool:
    try:
        if store.windows:
            before = _paths._windows_handle_status(descriptor)
            if (
                before.identity != expected_identity
                or before.is_reparse
                or not _paths._windows_private_directory(descriptor, exact=True)
                or not filesystem_guard(descriptor)
                or not _paths._windows_namespace_binds(
                    store.store, component, expected_identity
                )
            ):
                return False
            after = _paths._windows_handle_status(descriptor)
            return (
                after.identity == before.identity
                and after.fingerprint == before.fingerprint
                and _paths._windows_private_directory(descriptor, exact=True)
                and _paths._windows_namespace_binds(
                    store.store, component, expected_identity
                )
                and _transaction_store_namespace_is_valid(store)
            )
        posix_before = os.fstat(descriptor)
        if (
            (posix_before.st_dev, posix_before.st_ino) != expected_identity
            or not _paths._private_directory(descriptor, posix_before, exact=True)
            or not filesystem_guard(descriptor)
            or not _paths._posix_namespace_binds(
                store.store, component, expected_identity
            )
        ):
            return False
        posix_after = os.fstat(descriptor)
        return (
            (posix_after.st_dev, posix_after.st_ino) == expected_identity
            and _paths._posix_status_fingerprint(posix_after)
            == _paths._posix_status_fingerprint(posix_before)
            and _paths._private_directory(descriptor, posix_after, exact=True)
            and _paths._posix_namespace_binds(store.store, component, expected_identity)
            and _transaction_store_namespace_is_valid(store)
        )
    except (ForgeError, OSError):
        return False


def _pending_transaction_claim_is_exact(
    store: _TransactionStore,
    component: str,
    descriptor: int,
    expected_identity: tuple[int, int],
) -> bool:
    named = 0
    try:
        if store.windows:
            windows_before = _paths._windows_handle_status(descriptor)
            named = _paths._windows_open_child(
                store.claims,
                component,
                directory=False,
                read_data=True,
            )
            windows_named = _paths._windows_handle_status(named)
            windows_after = _paths._windows_handle_status(descriptor)
            return (
                windows_before.identity == expected_identity
                and windows_named.identity == expected_identity
                and windows_after.identity == expected_identity
                and windows_after.fingerprint == windows_before.fingerprint
                and not windows_before.is_directory
                and not windows_before.is_reparse
                and windows_before.link_count == 1
                and windows_before.identity[0] == store.plugins_identity[0]
                and windows_before.size <= _TRANSACTION_RECORD_LIMIT
                and _paths._windows_private_authorization(descriptor, exact=True)
                and _transaction_store_namespace_is_valid(store)
            )
        posix_before = os.fstat(descriptor)
        posix_named = os.stat(component, dir_fd=store.claims, follow_symlinks=False)
        posix_after = os.fstat(descriptor)
        return (
            (posix_before.st_dev, posix_before.st_ino) == expected_identity
            and (posix_named.st_dev, posix_named.st_ino) == expected_identity
            and (posix_after.st_dev, posix_after.st_ino) == expected_identity
            and _paths._posix_status_fingerprint(posix_after)
            == _paths._posix_status_fingerprint(posix_before)
            and stat.S_ISREG(posix_before.st_mode)
            and posix_before.st_uid == os.geteuid()
            and posix_before.st_gid == os.getegid()
            and stat.S_IMODE(posix_before.st_mode) == 0o600
            and posix_before.st_nlink == 1
            and posix_before.st_dev == store.plugins_identity[0]
            and posix_before.st_size <= _TRANSACTION_RECORD_LIMIT
            and _paths._posix_security_metadata_supported(descriptor, posix_before)
            and _paths._posix_security_metadata_supported(
                descriptor,
                posix_after,
            )
            and _transaction_store_namespace_is_valid(store)
        )
    except (ForgeError, OSError):
        return False
    finally:
        if named:
            _paths._windows_close(named)


def _persistent_transaction_root_capability(
    store: _TransactionStore,
    binding: PersistentTransactionBinding,
) -> PersistentTransactionRoot:
    reference = _paths.validate_reference(
        binding.root_relative,
        role="persistent-transaction-root",
        limits=LIMIT_POLICY,
    ).unwrap()
    claim = TransactionPathClaim(
        binding.transaction_id,
        reference,
        store.plugins_identity,
        binding.transaction_identity,
        persistent_binding=binding,
        _token=_CAPABILITY_TOKEN,
    )
    return PersistentTransactionRoot(binding, claim, _token=_PERSISTENT_ROOT_TOKEN)


def _create_posix_persistent_transaction(
    owned_root: OwnedRoot,
    store: _TransactionStore,
    *,
    transaction_id: str,
) -> PersistentTransactionRoot:
    leaf = anchor = -1
    final_claim = f"{transaction_id}.json"
    try:
        intent = _load_transaction_creation_intent(store, transaction_id)
        if intent is None:
            _require_new_transaction_namespace(store, transaction_id)
            reservation = _load_transaction_creation_reservation(
                store,
                transaction_id,
            )
            if reservation is None:
                reservation = _publish_transaction_creation_reservation(
                    store,
                    transaction_id=transaction_id,
                )
            _require_new_transaction_namespace(store, transaction_id)
            if _transaction_name_exists(
                store.store,
                reservation.stage_component,
                directory=True,
                windows=False,
            ) or _transaction_name_exists(
                store.claims,
                reservation.pending_claim_component,
                directory=False,
                windows=False,
            ):
                raise OSError(
                    errno.ESTALE,
                    "unbound reserved transaction object requires recovery",
                )
            os.mkdir(reservation.stage_component, 0o700, dir_fd=store.store)
            leaf = os.open(
                reservation.stage_component,
                _directory_flags(),
                dir_fd=store.store,
            )
            leaf_status = os.fstat(leaf)
            transaction_identity = _identity(leaf)
            if (
                not _paths._private_directory(leaf, leaf_status, exact=True)
                or transaction_identity[0] != store.plugins_identity[0]
                or not owned_root._filesystem_guard(leaf)
                or not _transaction_directory_is_exact(
                    store,
                    reservation.stage_component,
                    leaf,
                    transaction_identity,
                    filesystem_guard=owned_root._filesystem_guard,
                )
            ):
                raise OSError(errno.EPERM, "staged transaction root is unsafe")
            os.fsync(leaf)
            os.fsync(store.store)
            anchor = os.open(
                reservation.pending_claim_component,
                os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
                0o600,
                dir_fd=store.claims,
            )
            os.fchmod(anchor, 0o600)
            claim_identity = _identity(anchor)
            if not _pending_transaction_claim_is_exact(
                store,
                reservation.pending_claim_component,
                anchor,
                claim_identity,
            ):
                raise OSError(errno.EPERM, "staged transaction claim is unsafe")
            binding, claim_raw = _binding_from_fields(
                transaction_id=transaction_id,
                plugins_identity=store.plugins_identity,
                control_identity=store.control_identity,
                store_identity=store.store_identity,
                claims_identity=store.claims_identity,
                transaction_identity=transaction_identity,
                claim_identity=claim_identity,
            )
            if (
                _load_transaction_creation_reservation(store, transaction_id)
                != reservation
                or not _transaction_directory_is_exact(
                    store,
                    reservation.stage_component,
                    leaf,
                    transaction_identity,
                    filesystem_guard=owned_root._filesystem_guard,
                )
                or not _pending_transaction_claim_is_exact(
                    store,
                    reservation.pending_claim_component,
                    anchor,
                    claim_identity,
                )
            ):
                raise OSError(
                    errno.ESTALE,
                    "transaction creation reservation changed",
                )
            intent = _publish_transaction_creation_intent(
                store,
                transaction_id=transaction_id,
                reservation=reservation,
                binding=binding,
            )
        else:
            binding = intent.binding
            claim_raw = _transaction_journal_binding_bytes(binding)
            stage_leaf = _open_posix_transaction_location(
                store,
                intent.stage_component,
                expected_identity=binding.transaction_identity,
                filesystem_guard=owned_root._filesystem_guard,
            )
            live_leaf = _open_posix_transaction_location(
                store,
                transaction_id,
                expected_identity=binding.transaction_identity,
                filesystem_guard=owned_root._filesystem_guard,
            )
            if (stage_leaf >= 0) == (live_leaf >= 0):
                for descriptor in (stage_leaf, live_leaf):
                    if descriptor >= 0:
                        os.close(descriptor)
                raise OSError(errno.ESTALE, "transaction root publication is ambiguous")
            leaf = stage_leaf if stage_leaf >= 0 else live_leaf
            published = _read_transaction_record_if_present(store, final_claim)
            if published is None:
                anchor = os.open(
                    intent.pending_claim_component,
                    os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW,
                    dir_fd=store.claims,
                )
                if not _pending_transaction_claim_is_exact(
                    store,
                    intent.pending_claim_component,
                    anchor,
                    binding.claim_identity,
                ):
                    raise OSError(errno.ESTALE, "pending transaction claim changed")
            elif published != (claim_raw, binding.claim_identity):
                raise OSError(errno.ESTALE, "published transaction claim changed")

        root_is_live = _paths._posix_namespace_binds(
            store.store,
            transaction_id,
            binding.transaction_identity,
        )
        if not root_is_live and not _transaction_directory_is_exact(
            store,
            intent.stage_component,
            leaf,
            binding.transaction_identity,
            filesystem_guard=owned_root._filesystem_guard,
        ):
            raise OSError(errno.ESTALE, "staged transaction root changed")
        if anchor >= 0:
            if not _pending_transaction_claim_is_exact(
                store,
                intent.pending_claim_component,
                anchor,
                binding.claim_identity,
            ):
                raise OSError(errno.ESTALE, "pending transaction claim changed")
            observed = os.pread(anchor, _TRANSACTION_RECORD_LIMIT + 1, 0)
            if observed != claim_raw:
                os.ftruncate(anchor, 0)
                os.lseek(anchor, 0, os.SEEK_SET)
                _paths._write_all(anchor, claim_raw)
            os.fsync(anchor)
            if not _private_record_name_binds(
                store.claims,
                intent.pending_claim_component,
                binding.claim_identity,
                expected_raw=claim_raw,
                windows=False,
            ):
                raise OSError(errno.ESTALE, "pending transaction claim changed")
        if not root_is_live:
            _paths._exclusive_posix_rename(
                store.store,
                intent.stage_component,
                transaction_id,
            )
            os.fsync(store.store)
        if not _transaction_directory_is_exact(
            store,
            transaction_id,
            leaf,
            binding.transaction_identity,
            filesystem_guard=owned_root._filesystem_guard,
        ):
            raise OSError(errno.ESTALE, "persistent transaction root moved")
        if anchor >= 0:
            _exclusive_rename(
                store.claims,
                intent.pending_claim_component,
                final_claim,
            )
            os.fsync(store.claims)
        if not _transaction_directory_is_exact(
            store,
            transaction_id,
            leaf,
            binding.transaction_identity,
            filesystem_guard=owned_root._filesystem_guard,
        ) or not _private_record_name_binds(
            store.claims,
            final_claim,
            binding.claim_identity,
            expected_raw=claim_raw,
            windows=False,
        ):
            raise OSError(errno.ESTALE, "persistent transaction publication changed")
        _finish_transaction_creation_intent(store, binding)
        return _persistent_transaction_root_capability(store, binding)
    finally:
        for descriptor in (anchor, leaf):
            if descriptor >= 0:
                os.close(descriptor)


def _revalidate_windows_claim_publication(
    store: _TransactionStore,
    *,
    final_claim: str,
    claim_identity: tuple[int, int],
    raw: bytes,
) -> bool | None:
    """Return exact publication, proven absence, or preserved ambiguity."""

    try:
        observed_raw, observed_identity = _read_windows_private_record(
            store.claims,
            final_claim,
            volume=store.plugins_identity[0],
        )
    except OSError as exc:
        missing = isinstance(exc, FileNotFoundError) or getattr(
            exc, "winerror", None
        ) in {2, 3}
        return False if missing else None
    except (ForgeError, ValueError):
        return None
    if (
        observed_identity != claim_identity
        or observed_raw != raw
        or not _private_record_name_binds(
            store.claims,
            final_claim,
            claim_identity,
            expected_raw=raw,
            windows=True,
        )
        or not _transaction_store_namespace_is_valid(store)
    ):
        return None
    return True


def _create_windows_persistent_transaction(
    owned_root: OwnedRoot,
    store: _TransactionStore,
    *,
    transaction_id: str,
) -> PersistentTransactionRoot:
    leaf = anchor = 0
    final_claim = f"{transaction_id}.json"
    try:
        intent = _load_transaction_creation_intent(store, transaction_id)
        if intent is None:
            _require_new_transaction_namespace(store, transaction_id)
            reservation = _load_transaction_creation_reservation(
                store,
                transaction_id,
            )
            if reservation is None:
                reservation = _publish_transaction_creation_reservation(
                    store,
                    transaction_id=transaction_id,
                )
            _require_new_transaction_namespace(store, transaction_id)
            if _transaction_name_exists(
                store.store,
                reservation.stage_component,
                directory=True,
                windows=True,
            ) or _transaction_name_exists(
                store.claims,
                reservation.pending_claim_component,
                directory=False,
                windows=True,
            ):
                raise OSError(
                    errno.ESTALE,
                    "unbound reserved transaction object requires recovery",
                )
            leaf = _paths._windows_create_private_directory(
                store.store,
                reservation.stage_component,
            )
            transaction_identity = _paths._windows_handle_status(leaf).identity
            if (
                transaction_identity[0] != store.plugins_identity[0]
                or not _paths._windows_private_directory(leaf, exact=True)
                or not owned_root._filesystem_guard(leaf)
                or not _transaction_directory_is_exact(
                    store,
                    reservation.stage_component,
                    leaf,
                    transaction_identity,
                    filesystem_guard=owned_root._filesystem_guard,
                )
            ):
                raise OSError(errno.EPERM, "staged transaction root is unsafe")
            _windows_flush_directory_binding(
                store.store,
                reservation.stage_component,
                transaction_identity,
            )
            anchor = _paths._windows_create_private_file(
                store.claims,
                reservation.pending_claim_component,
            )
            claim_identity = _paths._windows_handle_status(anchor).identity
            if not _pending_transaction_claim_is_exact(
                store,
                reservation.pending_claim_component,
                anchor,
                claim_identity,
            ):
                raise OSError(errno.EPERM, "staged transaction claim is unsafe")
            binding, claim_raw = _binding_from_fields(
                transaction_id=transaction_id,
                plugins_identity=store.plugins_identity,
                control_identity=store.control_identity,
                store_identity=store.store_identity,
                claims_identity=store.claims_identity,
                transaction_identity=transaction_identity,
                claim_identity=claim_identity,
            )
            if (
                _load_transaction_creation_reservation(store, transaction_id)
                != reservation
                or not _transaction_directory_is_exact(
                    store,
                    reservation.stage_component,
                    leaf,
                    transaction_identity,
                    filesystem_guard=owned_root._filesystem_guard,
                )
                or not _pending_transaction_claim_is_exact(
                    store,
                    reservation.pending_claim_component,
                    anchor,
                    claim_identity,
                )
            ):
                raise OSError(
                    errno.ESTALE,
                    "transaction creation reservation changed",
                )
            intent = _publish_transaction_creation_intent(
                store,
                transaction_id=transaction_id,
                reservation=reservation,
                binding=binding,
            )
        else:
            binding = intent.binding
            claim_raw = _transaction_journal_binding_bytes(binding)
            stage_leaf = _open_windows_transaction_location(
                store,
                intent.stage_component,
                expected_identity=binding.transaction_identity,
                filesystem_guard=owned_root._filesystem_guard,
            )
            live_leaf = _open_windows_transaction_location(
                store,
                transaction_id,
                expected_identity=binding.transaction_identity,
                filesystem_guard=owned_root._filesystem_guard,
            )
            if bool(stage_leaf) == bool(live_leaf):
                for descriptor in (stage_leaf, live_leaf):
                    if descriptor:
                        _paths._windows_close(descriptor)
                raise OSError(errno.ESTALE, "transaction root publication is ambiguous")
            leaf = stage_leaf or live_leaf
            published = _read_transaction_record_if_present(store, final_claim)
            if published is None:
                anchor = _windows_open_raw_child(
                    store.claims,
                    intent.pending_claim_component,
                    directory=False,
                    read_data=True,
                    write_data=True,
                    delete_access=True,
                )
                if not _pending_transaction_claim_is_exact(
                    store,
                    intent.pending_claim_component,
                    anchor,
                    binding.claim_identity,
                ):
                    raise OSError(errno.ESTALE, "pending transaction claim changed")
            elif published != (claim_raw, binding.claim_identity):
                raise OSError(errno.ESTALE, "published transaction claim changed")

        root_is_live = _paths._windows_namespace_binds(
            store.store,
            transaction_id,
            binding.transaction_identity,
        )
        if not root_is_live and not _transaction_directory_is_exact(
            store,
            intent.stage_component,
            leaf,
            binding.transaction_identity,
            filesystem_guard=owned_root._filesystem_guard,
        ):
            raise OSError(errno.ESTALE, "staged transaction root changed")
        if anchor:
            if not _pending_transaction_claim_is_exact(
                store,
                intent.pending_claim_component,
                anchor,
                binding.claim_identity,
            ):
                raise OSError(errno.ESTALE, "pending transaction claim changed")
            observed = _paths._windows_read(anchor, limit=_TRANSACTION_RECORD_LIMIT)
            if observed != claim_raw:
                _windows_replace_bytes(anchor, claim_raw)
            _windows_flush(anchor)
            if not _private_record_name_binds(
                store.claims,
                intent.pending_claim_component,
                binding.claim_identity,
                expected_raw=claim_raw,
                windows=True,
            ):
                raise OSError(errno.ESTALE, "pending transaction claim changed")
        if not root_is_live:
            _durable_windows_directory_rename(
                leaf,
                store.store,
                transaction_id,
                binding.transaction_identity,
            )
        if not _transaction_directory_is_exact(
            store,
            transaction_id,
            leaf,
            binding.transaction_identity,
            filesystem_guard=owned_root._filesystem_guard,
        ):
            raise OSError(errno.ESTALE, "persistent transaction root moved")
        if anchor:
            _durable_windows_file_rename(
                anchor,
                store.claims,
                final_claim,
            )
            _windows_flush_directory_binding(
                store.store,
                _TRANSACTION_CLAIMS_COMPONENT,
                store.claims_identity,
            )
        if not _transaction_directory_is_exact(
            store,
            transaction_id,
            leaf,
            binding.transaction_identity,
            filesystem_guard=owned_root._filesystem_guard,
        ) or not _private_record_name_binds(
            store.claims,
            final_claim,
            binding.claim_identity,
            expected_raw=claim_raw,
            windows=True,
        ):
            raise OSError(errno.ESTALE, "persistent transaction publication changed")
        _finish_transaction_creation_intent(store, binding)
        return _persistent_transaction_root_capability(store, binding)
    finally:
        for descriptor in (anchor, leaf):
            if descriptor:
                _paths._windows_close(descriptor)


def create_persistent_transaction_root(
    owned_root: OwnedRoot, *, transaction_id: str
) -> Result[PersistentTransactionRoot]:
    """Create one fixed transaction root plus an immutable sibling anchor."""

    if (
        not isinstance(owned_root, OwnedRoot)
        or not isinstance(transaction_id, str)
        or _PERSISTENT_TRANSACTION.fullmatch(transaction_id) is None
    ):
        return Result.failure(
            _error("ownership.unowned", "Persistent transaction ownership is invalid.")
        )
    store: _TransactionStore | None = None
    try:
        store = _open_transaction_store(owned_root, create=True)
        if store.windows:
            created = _create_windows_persistent_transaction(
                owned_root, store, transaction_id=transaction_id
            )
        else:
            created = _create_posix_persistent_transaction(
                owned_root, store, transaction_id=transaction_id
            )
        return Result.success(created)
    except (ForgeError, OSError, ValueError):
        return Result.failure(
            _error(
                "ownership.unowned",
                "The persistent transaction root cannot be created safely.",
            )
        )
    finally:
        if store is not None:
            store.close()


def _binding_from_record(
    raw: bytes,
    *,
    transaction_id: str,
    store: _TransactionStore,
    claim_identity: tuple[int, int],
) -> PersistentTransactionBinding:
    record = decode_persistent_record(
        raw, supported_major=1, reader_version=_WRITER_VERSION
    )
    expected_root, expected_quarantine, expected_claim = (
        _persistent_transaction_references(transaction_id)
    )
    expected_fixed: Mapping[str, object] = {
        "authority": "zagrosi-forge-transaction-authority-v1",
        "claim_identity": claim_identity,
        "claim_relative": expected_claim,
        "claims_identity": store.claims_identity,
        "control_identity": store.control_identity,
        "minimum_reader_version": _WRITER_VERSION,
        "plugins_identity": store.plugins_identity,
        "quarantine_relative": expected_quarantine,
        "record_kind": "persistent-transaction-root",
        "root_relative": expected_root,
        "schema_digest": _TRANSACTION_BINDING_SCHEMA_DIGEST,
        "schema_version": "1.0",
        "store_identity": store.store_identity,
        "transaction_id": transaction_id,
        "writer_version": _WRITER_VERSION,
    }
    expected_keys = {*expected_fixed, "transaction_identity", "record_digest"}
    transaction_identity = record.get("transaction_identity")
    if (
        set(record) != expected_keys
        or any(record.get(key) != value for key, value in expected_fixed.items())
        or not _file_identity_invariants(transaction_identity)
        or cast(tuple[int, int], transaction_identity)[0] != store.plugins_identity[0]
        or not isinstance(record.get("record_digest"), str)
    ):
        raise _error("ownership.unowned", "The transaction anchor is not trusted.")
    binding = PersistentTransactionBinding(
        transaction_id=transaction_id,
        root_relative=expected_root,
        quarantine_relative=expected_quarantine,
        claim_relative=expected_claim,
        plugins_identity=store.plugins_identity,
        control_identity=store.control_identity,
        store_identity=store.store_identity,
        claims_identity=store.claims_identity,
        transaction_identity=cast(tuple[int, int], transaction_identity),
        claim_identity=claim_identity,
        claim_digest=cast(str, record["record_digest"]),
        _token=_PERSISTENT_BINDING_TOKEN,
    )
    if not _persistent_binding_invariants(binding):
        raise _error("ownership.unowned", "The transaction anchor is not trusted.")
    return binding


def load_persistent_transaction_binding(
    owned_root: OwnedRoot, *, transaction_id: str
) -> Result[PersistentTransactionBinding]:
    """Load one immutable data binding without minting cleanup authority."""

    if (
        not isinstance(owned_root, OwnedRoot)
        or not isinstance(transaction_id, str)
        or _PERSISTENT_TRANSACTION.fullmatch(transaction_id) is None
    ):
        return Result.failure(
            _error("ownership.unowned", "The transaction anchor is not trusted.")
        )
    store: _TransactionStore | None = None
    try:
        store = _open_transaction_store(owned_root, create=False)
        component = f"{transaction_id}.json"
        if store.windows:
            raw, identity = _read_windows_private_record(
                store.claims, component, volume=store.plugins_identity[0]
            )
        else:
            raw, identity = _read_posix_private_record(
                store.claims, component, device=store.plugins_identity[0]
            )
        binding = _binding_from_record(
            raw,
            transaction_id=transaction_id,
            store=store,
            claim_identity=identity,
        )
        if (
            not _private_record_name_binds(
                store.claims,
                component,
                identity,
                expected_raw=raw,
                windows=store.windows,
            )
            or not _transaction_store_namespace_is_valid(store)
            or not owned_root._validate_control_descriptor(store.control)
        ):
            raise _error("ownership.unowned", "The transaction anchor is not trusted.")
        return Result.success(binding)
    except (ForgeError, OSError, ValueError):
        return Result.failure(
            _error("ownership.unowned", "The transaction anchor is not trusted.")
        )
    finally:
        if store is not None:
            store.close()


def _open_posix_transaction_location(
    store: _TransactionStore,
    component: str,
    *,
    expected_identity: tuple[int, int],
    filesystem_guard: _paths.FilesystemGuard,
) -> int:
    try:
        descriptor = os.open(component, _directory_flags(), dir_fd=store.store)
    except FileNotFoundError:
        return -1
    try:
        status = os.fstat(descriptor)
        if (
            (status.st_dev, status.st_ino) != expected_identity
            or not _paths._private_directory(descriptor, status, exact=True)
            or not filesystem_guard(descriptor)
        ):
            raise OSError(errno.ESTALE, "transaction location identity changed")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _open_windows_transaction_location(
    store: _TransactionStore,
    component: str,
    *,
    expected_identity: tuple[int, int],
    filesystem_guard: _paths.FilesystemGuard,
    write: bool = False,
) -> int:
    try:
        descriptor = _paths._windows_open_child(
            store.store,
            component,
            directory=True,
            read_data=True,
            write_data=write,
        )
    except OSError as exc:
        if isinstance(exc, FileNotFoundError) or getattr(exc, "winerror", None) in {
            2,
            3,
        }:
            return 0
        raise
    try:
        status = _paths._windows_handle_status(descriptor)
        if (
            status.identity != expected_identity
            or status.is_reparse
            or not _paths._windows_private_directory(descriptor, exact=True)
            or not filesystem_guard(descriptor)
        ):
            raise OSError(errno.ESTALE, "transaction location identity changed")
        return descriptor
    except BaseException:
        _paths._windows_close(descriptor)
        raise


def _recovery_cleanup_authorization_from_record(
    store: _TransactionStore,
    binding: PersistentTransactionBinding,
    *,
    raw: bytes,
    identity: tuple[int, int],
    retired: bool = False,
) -> RecoveryCleanupAuthorization:
    record = decode_persistent_record(
        raw,
        supported_major=1,
        reader_version=_WRITER_VERSION,
    )
    location_value = record.get("journal_location")
    journal_relative = record.get("journal_relative")
    journal_access_digest = record.get("journal_access_digest")
    journal_evidence_digest = record.get("journal_evidence_digest")
    journal_head_sequence = record.get("journal_head_sequence")
    journal_head_record_digest = record.get("journal_head_record_digest")
    transaction_binding_digest = record.get("transaction_binding_digest")
    delete_component = record.get("delete_component")
    expected_fixed: Mapping[str, object] = {
        "authority": "zagrosi-forge-transaction-authority-v1",
        "binding": binding.canonical_projection(),
        "binding_digest": _recovery_cleanup_binding_digest(binding),
        "journal_head_state": "ROLLED_BACK",
        "journal_location": TransactionLocation.QUARANTINED.value,
        "minimum_reader_version": _WRITER_VERSION,
        "policy_version": _POLICY_VERSION,
        "previous_recovery_cleanup_digest": _ZERO_DIGEST,
        "record_kind": "persistent-transaction-recovery-cleanup",
        "recovery_cleanup_phase": _RecoveryCleanupPhase.AUTHORIZED.value,
        "root_identity": binding.transaction_identity,
        "schema_digest": _TRANSACTION_RECOVERY_CLEANUP_SCHEMA_DIGEST,
        "schema_version": "1.0",
        "transaction_id": binding.transaction_id,
        "writer_version": _WRITER_VERSION,
    }
    expected_keys = {
        *expected_fixed,
        "delete_component",
        "journal_access_digest",
        "journal_evidence_digest",
        "journal_head_record_digest",
        "journal_head_sequence",
        "journal_relative",
        "record_digest",
        "transaction_binding_digest",
    }
    if (
        set(record) != expected_keys
        or any(record.get(key) != value for key, value in expected_fixed.items())
        or location_value != TransactionLocation.QUARANTINED.value
        or not isinstance(journal_relative, str)
        or not isinstance(journal_access_digest, str)
        or not isinstance(journal_evidence_digest, str)
        or type(journal_head_sequence) is not int
        or not isinstance(journal_head_record_digest, str)
        or not isinstance(transaction_binding_digest, str)
        or not isinstance(delete_component, str)
    ):
        raise OSError(errno.ESTALE, "recovery cleanup authorization changed")
    expected_raw = _recovery_cleanup_record_bytes(
        binding,
        location=TransactionLocation.QUARANTINED,
        journal_relative=journal_relative,
        journal_access_digest=journal_access_digest,
        journal_evidence_digest=journal_evidence_digest,
        journal_head_sequence=journal_head_sequence,
        journal_head_record_digest=journal_head_record_digest,
        transaction_binding_digest=transaction_binding_digest,
        delete_component=delete_component,
        phase=_RecoveryCleanupPhase.AUTHORIZED,
        previous_phase_digest=_ZERO_DIGEST,
    )
    component = _recovery_cleanup_component(
        binding.transaction_id,
        _RecoveryCleanupPhase.AUTHORIZED,
    )
    selected_component = (
        _transaction_record_retirement_component(component) if retired else component
    )
    if raw != expected_raw or not _private_record_name_binds(
        store.claims,
        selected_component,
        identity,
        expected_raw=expected_raw,
        windows=store.windows,
    ):
        raise OSError(errno.ESTALE, "recovery cleanup authorization changed")
    record_digest = record.get("record_digest")
    if not isinstance(record_digest, str):
        raise OSError(errno.ESTALE, "recovery cleanup authorization changed")
    return RecoveryCleanupAuthorization(
        binding=binding,
        location=TransactionLocation.QUARANTINED,
        journal_relative=journal_relative,
        journal_access_digest=journal_access_digest,
        journal_evidence_digest=journal_evidence_digest,
        journal_head_sequence=journal_head_sequence,
        journal_head_record_digest=journal_head_record_digest,
        transaction_binding_digest=transaction_binding_digest,
        delete_component=delete_component,
        authorization_digest=record_digest,
        _component=component,
        _identity=identity,
        _raw=raw,
        _token=_RECOVERY_CLEANUP_AUTHORIZATION_TOKEN,
    )


def _load_recovery_cleanup_authorization(
    store: _TransactionStore,
    binding: PersistentTransactionBinding,
) -> RecoveryCleanupAuthorization | None:
    component = _recovery_cleanup_component(
        binding.transaction_id,
        _RecoveryCleanupPhase.AUTHORIZED,
    )
    observed = _read_transaction_record_if_present(store, component)
    if observed is None:
        return None
    raw, identity = observed
    return _recovery_cleanup_authorization_from_record(
        store,
        binding,
        raw=raw,
        identity=identity,
    )


def _load_recovery_cleanup_authorization_any(
    store: _TransactionStore,
    binding: PersistentTransactionBinding,
) -> tuple[RecoveryCleanupAuthorization | None, bool]:
    component = _recovery_cleanup_component(
        binding.transaction_id,
        _RecoveryCleanupPhase.AUTHORIZED,
    )
    retired_component = _transaction_record_retirement_component(component)
    active = _read_transaction_record_if_present(store, component)
    retired = _read_transaction_record_if_present(store, retired_component)
    if active is not None and retired is not None:
        raise OSError(errno.ESTALE, "recovery cleanup authorization is ambiguous")
    selected = active if active is not None else retired
    if selected is None:
        return None, False
    raw, identity = selected
    return (
        _recovery_cleanup_authorization_from_record(
            store,
            binding,
            raw=raw,
            identity=identity,
            retired=active is None,
        ),
        active is not None,
    )


def _recovery_cleanup_phase_digest(raw: bytes) -> str:
    record = decode_persistent_record(
        raw,
        supported_major=1,
        reader_version=_WRITER_VERSION,
    )
    digest = record.get("record_digest")
    if not isinstance(digest, str) or _DIGEST.fullmatch(digest) is None:
        raise OSError(errno.ESTALE, "recovery cleanup WAL changed")
    return digest


def _load_recovery_cleanup_phase(
    store: _TransactionStore,
    authorization: RecoveryCleanupAuthorization,
    *,
    phase: _RecoveryCleanupPhase,
    previous_phase_digest: str,
    allow_retired: bool,
) -> _RecoveryCleanupPhaseRecord | None:
    if phase is _RecoveryCleanupPhase.AUTHORIZED:
        raise ValueError("authorized phase has dedicated evidence")
    expected_raw = _recovery_cleanup_phase_bytes(
        authorization,
        phase=phase,
        previous_phase_digest=previous_phase_digest,
    )
    component = _recovery_cleanup_component(authorization.transaction_id, phase)
    retired_component = _transaction_record_retirement_component(component)
    active = _read_transaction_record_if_present(store, component)
    retired = (
        _read_transaction_record_if_present(store, retired_component)
        if allow_retired
        else None
    )
    if active is not None and retired is not None:
        raise OSError(errno.ESTALE, "recovery cleanup WAL is ambiguous")
    observed = active if active is not None else retired
    if observed is None:
        return None
    raw, identity = observed
    selected_component = component if active is not None else retired_component
    if raw != expected_raw or not _private_record_name_binds(
        store.claims,
        selected_component,
        identity,
        expected_raw=expected_raw,
        windows=store.windows,
    ):
        raise OSError(errno.ESTALE, "recovery cleanup WAL changed")
    return _RecoveryCleanupPhaseRecord(
        component=component,
        identity=identity,
        raw=raw,
        phase=phase,
        previous_phase_digest=previous_phase_digest,
        record_digest=_recovery_cleanup_phase_digest(raw),
        retired=active is None,
    )


def _publish_recovery_cleanup_phase(
    store: _TransactionStore,
    authorization: RecoveryCleanupAuthorization,
    *,
    phase: _RecoveryCleanupPhase,
    previous_phase_digest: str,
) -> _RecoveryCleanupPhaseRecord:
    existing = _load_recovery_cleanup_phase(
        store,
        authorization,
        phase=phase,
        previous_phase_digest=previous_phase_digest,
        allow_retired=True,
    )
    if existing is not None:
        return existing
    component = _recovery_cleanup_component(authorization.transaction_id, phase)
    raw = _recovery_cleanup_phase_bytes(
        authorization,
        phase=phase,
        previous_phase_digest=previous_phase_digest,
    )
    _publish_transaction_record(store, component, raw)
    created = _load_recovery_cleanup_phase(
        store,
        authorization,
        phase=phase,
        previous_phase_digest=previous_phase_digest,
        allow_retired=False,
    )
    if created is None:
        raise OSError(errno.ESTALE, "recovery cleanup WAL publication changed")
    return created


def _retire_recovery_cleanup_phase(
    store: _TransactionStore,
    record: _RecoveryCleanupPhaseRecord,
) -> None:
    if record.retired:
        if not _transaction_record_is_active_or_retired_exact(
            store,
            record.component,
            record.identity,
            record.raw,
        ):
            raise OSError(errno.ESTALE, "recovery cleanup WAL retirement changed")
        return
    _remove_exact_transaction_record(
        store,
        record.component,
        record.identity,
        record.raw,
    )


def _load_transaction_cleanup_record(
    store: _TransactionStore,
    binding: PersistentTransactionBinding,
    *,
    complete: bool,
) -> _TransactionCleanupRecord | None:
    component = _transaction_cleanup_component(
        binding.transaction_id, complete=complete
    )
    observed = _read_transaction_record_if_present(store, component)
    if observed is None:
        return None
    observed_raw, observed_identity = observed
    decoded = decode_persistent_record(
        observed_raw,
        supported_major=1,
        reader_version=_WRITER_VERSION,
    )
    delete_component = decoded.get("delete_component")
    if (
        not isinstance(delete_component, str)
        or _TRANSACTION_DELETE.fullmatch(delete_component) is None
    ):
        raise OSError(errno.ESTALE, "transaction cleanup record changed")
    expected_raw = _transaction_cleanup_record_bytes(
        binding,
        delete_component=delete_component,
        complete=complete,
    )
    if observed_raw != expected_raw or not _private_record_name_binds(
        store.claims,
        component,
        observed_identity,
        expected_raw=expected_raw,
        windows=store.windows,
    ):
        raise OSError(errno.ESTALE, "transaction cleanup record changed")
    return _TransactionCleanupRecord(
        component=component,
        identity=observed_identity,
        raw=observed_raw,
        delete_component=delete_component,
        complete=complete,
    )


def _transaction_cleanup_record_is_valid(
    store: _TransactionStore,
    binding: PersistentTransactionBinding,
    *,
    complete: bool,
    delete_component: str | None = None,
) -> bool:
    try:
        record = _load_transaction_cleanup_record(
            store,
            binding,
            complete=complete,
        )
        return record is not None and (
            delete_component is None or record.delete_component == delete_component
        )
    except (ForgeError, OSError, TypeError, ValueError):
        return False


def _publish_transaction_cleanup_intent(
    store: _TransactionStore,
    binding: PersistentTransactionBinding,
    *,
    delete_component: str | None = None,
) -> _TransactionCleanupRecord:
    if not _transaction_journal_records_are_valid(store, binding) or (
        delete_component is not None
        and _TRANSACTION_DELETE.fullmatch(delete_component) is None
    ):
        raise OSError(errno.ESTALE, "transaction cleanup authority changed")
    existing = _load_transaction_cleanup_record(
        store,
        binding,
        complete=False,
    )
    if existing is not None:
        if (
            delete_component is not None
            and existing.delete_component != delete_component
        ):
            raise OSError(errno.ESTALE, "transaction cleanup intent changed")
        return existing
    component = _transaction_cleanup_component(binding.transaction_id, complete=False)
    selected_delete_component = (
        f".delete-{secrets.token_hex(16)}.tmp"
        if delete_component is None
        else delete_component
    )
    raw = _transaction_cleanup_record_bytes(
        binding,
        delete_component=selected_delete_component,
        complete=False,
    )
    _publish_transaction_record(store, component, raw)
    created = _load_transaction_cleanup_record(
        store,
        binding,
        complete=False,
    )
    if created is None or created.delete_component != selected_delete_component:
        raise OSError(errno.ESTALE, "transaction cleanup intent changed")
    return created


def _publish_transaction_cleanup_complete(
    store: _TransactionStore,
    binding: PersistentTransactionBinding,
    *,
    delete_component: str,
) -> None:
    intent = _load_transaction_cleanup_record(
        store,
        binding,
        complete=False,
    )
    if intent is None or intent.delete_component != delete_component:
        raise OSError(errno.ESTALE, "transaction cleanup intent is missing")
    component = _transaction_cleanup_component(binding.transaction_id, complete=True)
    raw = _transaction_cleanup_record_bytes(
        binding,
        delete_component=delete_component,
        complete=True,
    )
    _publish_transaction_record(store, component, raw)
    complete_record = _load_transaction_cleanup_record(
        store,
        binding,
        complete=True,
    )
    if complete_record is None or complete_record.delete_component != delete_component:
        raise OSError(errno.ESTALE, "transaction cleanup completion changed")
    _remove_exact_transaction_record(
        store,
        intent.component,
        intent.identity,
        intent.raw,
    )


@dataclass(frozen=True, slots=True)
class _RecoveryCleanupState:
    authorization_active: bool
    finalizing: _RecoveryCleanupPhaseRecord | None
    complete: _RecoveryCleanupPhaseRecord | None
    cleanup_intent: _TransactionCleanupRecord | None
    cleanup_complete: _TransactionCleanupRecord | None
    journal_relative: str | None


def _publish_recovery_cleanup_finalizing(
    store: _TransactionStore,
    authorization: RecoveryCleanupAuthorization,
    cleanup_record: _TransactionCleanupRecord,
) -> _RecoveryCleanupPhaseRecord:
    authorization._require_valid()
    if (
        not _recovery_cleanup_authorization_is_active(store, authorization)
        or cleanup_record.complete
        or cleanup_record.delete_component != authorization.delete_component
        or _load_transaction_cleanup_record(
            store,
            authorization.binding,
            complete=False,
        )
        != cleanup_record
        or _load_transaction_cleanup_record(
            store,
            authorization.binding,
            complete=True,
        )
        is not None
        or _recovery_cleanup_phase_name_exists(
            store,
            authorization.transaction_id,
            _RecoveryCleanupPhase.COMPLETE,
        )
    ):
        raise OSError(errno.ESTALE, "recovery cleanup finalization changed")
    finalizing = _publish_recovery_cleanup_phase(
        store,
        authorization,
        phase=_RecoveryCleanupPhase.FINALIZING,
        previous_phase_digest=authorization.authorization_digest,
    )
    if finalizing.retired:
        raise OSError(errno.ESTALE, "recovery cleanup finalization is retired")
    return finalizing


def _recovery_cleanup_phase_name_exists(
    store: _TransactionStore,
    transaction_id: str,
    phase: _RecoveryCleanupPhase,
) -> bool:
    component = _recovery_cleanup_component(transaction_id, phase)
    return _transaction_name_exists(
        store.claims,
        component,
        directory=False,
        windows=store.windows,
    ) or _transaction_name_exists(
        store.claims,
        _transaction_record_retirement_component(component),
        directory=False,
        windows=store.windows,
    )


def _recovery_cleanup_wal_residue_exists(
    store: _TransactionStore,
    transaction_id: str,
) -> bool:
    return any(
        _recovery_cleanup_phase_name_exists(store, transaction_id, phase)
        for phase in _RecoveryCleanupPhase
    )


def _recovery_cleanup_authorization_is_active(
    store: _TransactionStore,
    authorization: RecoveryCleanupAuthorization,
) -> bool:
    authorization._require_valid()
    active = _private_record_name_binds(
        store.claims,
        authorization._component,
        authorization._identity,
        expected_raw=authorization._raw,
        windows=store.windows,
    )
    retired_component = _transaction_record_retirement_component(
        authorization._component
    )
    retired = _private_record_name_binds(
        store.claims,
        retired_component,
        authorization._identity,
        expected_raw=authorization._raw,
        windows=store.windows,
    )
    active_exists = active or _transaction_name_exists(
        store.claims,
        authorization._component,
        directory=False,
        windows=store.windows,
    )
    retired_exists = retired or _transaction_name_exists(
        store.claims,
        retired_component,
        directory=False,
        windows=store.windows,
    )
    if (active and not retired_exists) or (retired and not active_exists):
        return active
    raise OSError(errno.ESTALE, "recovery cleanup authorization changed")


def _recovery_cleanup_identity_is_elsewhere(
    store: _TransactionStore,
    *,
    binding: PersistentTransactionBinding,
    expected_components: frozenset[str],
) -> bool:
    names = _bounded_transaction_names(store.store, windows=store.windows)
    for name in names:
        if (
            name
            in {
                _TRANSACTION_STORE_CONTROL,
                _TRANSACTION_CLAIMS_COMPONENT,
            }
            or name in expected_components
        ):
            continue
        descriptor = 0 if store.windows else -1
        try:
            if store.windows:
                try:
                    descriptor = _paths._windows_open_child(
                        store.store,
                        name,
                        directory=True,
                    )
                except OSError:
                    continue
            else:
                try:
                    descriptor = os.open(name, _directory_flags(), dir_fd=store.store)
                except OSError:
                    continue
            if _native_identity(descriptor) == binding.transaction_identity:
                return True
        finally:
            if store.windows:
                if descriptor:
                    _paths._windows_close(descriptor)
            elif descriptor >= 0:
                os.close(descriptor)
    return False


def _inspect_recovery_cleanup_state(
    owned_root: OwnedRoot,
    store: _TransactionStore,
    authorization: RecoveryCleanupAuthorization,
) -> _RecoveryCleanupState:
    authorization._require_valid()
    binding = authorization.binding
    if (
        _load_transaction_binding_from_store(store, binding.transaction_id) != binding
        or not _transaction_store_namespace_is_valid(store)
        or not owned_root._validate_control_descriptor(store.control)
    ):
        raise OSError(errno.ESTALE, "recovery cleanup authority changed")
    authorization_active = _recovery_cleanup_authorization_is_active(
        store,
        authorization,
    )
    finalizing = _load_recovery_cleanup_phase(
        store,
        authorization,
        phase=_RecoveryCleanupPhase.FINALIZING,
        previous_phase_digest=authorization.authorization_digest,
        allow_retired=True,
    )
    if finalizing is None:
        if _recovery_cleanup_phase_name_exists(
            store,
            binding.transaction_id,
            _RecoveryCleanupPhase.COMPLETE,
        ):
            raise OSError(errno.ESTALE, "recovery cleanup WAL is incomplete")
        complete = None
    else:
        complete = _load_recovery_cleanup_phase(
            store,
            authorization,
            phase=_RecoveryCleanupPhase.COMPLETE,
            previous_phase_digest=finalizing.record_digest,
            allow_retired=True,
        )
    cleanup_intent = _load_transaction_cleanup_record(
        store,
        binding,
        complete=False,
    )
    cleanup_complete = _load_transaction_cleanup_record(
        store,
        binding,
        complete=True,
    )
    if (
        cleanup_intent is not None
        and cleanup_intent.delete_component != authorization.delete_component
    ) or (
        cleanup_complete is not None
        and cleanup_complete.delete_component != authorization.delete_component
    ):
        raise OSError(errno.ESTALE, "recovery cleanup evidence conflicts")

    live_component = binding.root_relative.rsplit("/", 1)[-1]
    quarantine_component = binding.quarantine_relative.rsplit("/", 1)[-1]
    delete_component = authorization.delete_component
    live = quarantine = deleting = 0 if store.windows else -1
    try:
        if store.windows:
            live = _open_windows_transaction_location(
                store,
                live_component,
                expected_identity=binding.transaction_identity,
                filesystem_guard=owned_root._filesystem_guard,
            )
            quarantine = _open_windows_transaction_location(
                store,
                quarantine_component,
                expected_identity=binding.transaction_identity,
                filesystem_guard=owned_root._filesystem_guard,
            )
            deleting = _open_windows_transaction_location(
                store,
                delete_component,
                expected_identity=binding.transaction_identity,
                filesystem_guard=owned_root._filesystem_guard,
            )
            locations = (bool(live), bool(quarantine), bool(deleting))
        else:
            live = _open_posix_transaction_location(
                store,
                live_component,
                expected_identity=binding.transaction_identity,
                filesystem_guard=owned_root._filesystem_guard,
            )
            quarantine = _open_posix_transaction_location(
                store,
                quarantine_component,
                expected_identity=binding.transaction_identity,
                filesystem_guard=owned_root._filesystem_guard,
            )
            deleting = _open_posix_transaction_location(
                store,
                delete_component,
                expected_identity=binding.transaction_identity,
                filesystem_guard=owned_root._filesystem_guard,
            )
            locations = (live >= 0, quarantine >= 0, deleting >= 0)
        if sum(locations) > 1 or locations[0]:
            raise OSError(errno.ESTALE, "recovery cleanup location is ambiguous")
        journal_relative = (
            binding.quarantine_relative
            if locations[1]
            else (
                _persistent_delete_reference(binding, delete_component)
                if locations[2]
                else None
            )
        )
        if _recovery_cleanup_identity_is_elsewhere(
            store,
            binding=binding,
            expected_components=frozenset(
                {
                    live_component,
                    quarantine_component,
                    delete_component,
                }
            ),
        ):
            raise OSError(errno.ESTALE, "recovery cleanup identity was displaced")
    finally:
        if store.windows:
            for descriptor in (deleting, quarantine, live):
                if descriptor:
                    _paths._windows_close(descriptor)
        else:
            for descriptor in (deleting, quarantine, live):
                if descriptor >= 0:
                    os.close(descriptor)

    if finalizing is None:
        if (
            complete is not None
            or cleanup_complete is not None
            or journal_relative is None
            or not authorization_active
        ):
            raise OSError(errno.ESTALE, "recovery cleanup authorization is incomplete")
    else:
        if cleanup_intent is None and cleanup_complete is None:
            raise OSError(errno.ESTALE, "recovery cleanup intent is missing")
        if complete is not None and (
            cleanup_complete is None or journal_relative is not None
        ):
            raise OSError(errno.ESTALE, "recovery cleanup completion conflicts")
        if cleanup_complete is not None and journal_relative is not None:
            raise OSError(errno.ESTALE, "recovery cleanup completion conflicts")
    if not authorization_active and (
        finalizing is None
        or complete is None
        or not finalizing.retired
        or not complete.retired
        or cleanup_complete is None
        or journal_relative is not None
    ):
        raise OSError(errno.ESTALE, "retired recovery cleanup is incomplete")
    if (
        finalizing is not None
        and finalizing.retired
        and (complete is None or not complete.retired)
    ):
        raise OSError(errno.ESTALE, "recovery cleanup retirement order changed")
    if not _transaction_store_namespace_is_valid(
        store
    ) or not owned_root._validate_control_descriptor(store.control):
        raise OSError(errno.ESTALE, "recovery cleanup namespace changed")
    return _RecoveryCleanupState(
        authorization_active=authorization_active,
        finalizing=finalizing,
        complete=complete,
        cleanup_intent=cleanup_intent,
        cleanup_complete=cleanup_complete,
        journal_relative=journal_relative,
    )


def _generic_cleanup_record_digest(
    record: _TransactionCleanupRecord | None,
) -> str | None:
    return None if record is None else hashlib.sha256(record.raw).hexdigest()


def _capture_recovery_cleanup_observation(
    authorization: RecoveryCleanupAuthorization,
    state: _RecoveryCleanupState,
) -> RecoveryCleanupObservation:
    authorization._require_valid()
    if not state.authorization_active:
        raise OSError(errno.ESTALE, "recovery cleanup authorization is retired")
    phase = (
        _RecoveryCleanupPhase.COMPLETE
        if state.complete is not None
        else (
            _RecoveryCleanupPhase.FINALIZING
            if state.finalizing is not None
            else _RecoveryCleanupPhase.AUTHORIZED
        )
    )
    capture = _RecoveryCleanupObservationCapture(
        transaction_id=authorization.transaction_id,
        authorization_identity=authorization._identity,
        authorization_active=state.authorization_active,
        phase=phase.value,
        current_reference=state.journal_relative,
        finalizing_record_digest=(
            None if state.finalizing is None else state.finalizing.record_digest
        ),
        finalizing_identity=(
            None if state.finalizing is None else state.finalizing.identity
        ),
        finalizing_retired=(
            None if state.finalizing is None else state.finalizing.retired
        ),
        complete_record_digest=(
            None if state.complete is None else state.complete.record_digest
        ),
        complete_identity=None if state.complete is None else state.complete.identity,
        complete_retired=None if state.complete is None else state.complete.retired,
        cleanup_intent_digest=_generic_cleanup_record_digest(state.cleanup_intent),
        cleanup_intent_identity=(
            None if state.cleanup_intent is None else state.cleanup_intent.identity
        ),
        cleanup_complete_digest=_generic_cleanup_record_digest(state.cleanup_complete),
        cleanup_complete_identity=(
            None if state.cleanup_complete is None else state.cleanup_complete.identity
        ),
    )
    observed = RecoveryCleanupObservation(
        authorization=authorization,
        capture=capture,
        _token=_RECOVERY_CLEANUP_OBSERVATION_TOKEN,
    )
    observed._require_valid()
    authorization._require_valid()
    return observed


def _finish_transaction_creation_intent(
    store: _TransactionStore, binding: PersistentTransactionBinding
) -> None:
    component = _transaction_creation_intent_component(binding.transaction_id)
    observed = _read_transaction_record_if_present(store, component)
    if observed is None:
        return
    observed_raw, observed_identity = observed
    intent = _transaction_creation_intent_from_record(
        store,
        transaction_id=binding.transaction_id,
        raw=observed_raw,
        identity=observed_identity,
    )
    if intent.binding != binding:
        raise OSError(errno.ESTALE, "transaction creation intent changed")
    reservation_component = _transaction_creation_reservation_component(
        binding.transaction_id
    )
    reservation_raw = _transaction_creation_reservation_bytes(
        store,
        transaction_id=binding.transaction_id,
        stage_component=intent.stage_component,
        pending_claim_component=intent.pending_claim_component,
    )
    if hashlib.sha256(reservation_raw).hexdigest() != intent.reservation_digest:
        raise OSError(errno.ESTALE, "transaction creation reservation changed")
    reservation = _read_transaction_record_if_present(store, reservation_component)
    if reservation is not None:
        reservation_observed_raw, reservation_observed_identity = reservation
        if (
            reservation_observed_raw != reservation_raw
            or reservation_observed_identity != intent.reservation_identity
        ):
            raise OSError(errno.ESTALE, "transaction creation reservation changed")
        _remove_exact_transaction_record(
            store,
            reservation_component,
            reservation_observed_identity,
            reservation_observed_raw,
        )
    _remove_exact_transaction_record(
        store,
        component,
        observed_identity,
        observed_raw,
    )


def _transaction_control_names(owned_root: OwnedRoot) -> tuple[str, ...]:
    namespace: _paths._NamespaceCapability | None = None
    control = 0 if os.name == "nt" else -1
    try:
        if os.name == "nt":
            namespace = owned_root._duplicate_namespace_capability()
            if not namespace._validate_namespace_binding():
                raise OSError(errno.ESTALE, "transaction namespace changed")
            control = _paths._windows_open_child(
                namespace._plugins_descriptor,
                ".zagrosi",
                directory=True,
                read_data=True,
            )
        else:
            control = owned_root._duplicate_control_descriptor()
        if not owned_root._validate_control_descriptor(control):
            raise OSError(errno.ESTALE, "transaction control identity changed")
        names = _bounded_transaction_names(
            control,
            windows=os.name == "nt",
        )
        if not owned_root._validate_control_descriptor(control) or (
            namespace is not None and not namespace._validate_namespace_binding()
        ):
            raise OSError(errno.ESTALE, "transaction control identity changed")
        return names
    finally:
        if namespace is not None:
            namespace.close()
        if os.name == "nt":
            if control:
                _paths._windows_close(control)
        else:
            if control >= 0:
                os.close(control)


def _transaction_control_inventory_is_exact(
    names: tuple[str, ...],
    *,
    store_present: bool,
) -> bool:
    reserved = tuple(
        name
        for name in names
        if (key := _inventory_name_key(name)) == _TRANSACTION_STORE_COMPONENT
        or key.startswith(f".{_TRANSACTION_STORE_COMPONENT}-")
    )
    return (
        reserved == (_TRANSACTION_STORE_COMPONENT,) if store_present else not reserved
    )


def _transaction_store_is_exactly_absent(owned_root: OwnedRoot) -> bool:
    initial = _transaction_control_names(owned_root)
    if not _transaction_control_inventory_is_exact(
        initial,
        store_present=False,
    ):
        return False
    return _transaction_control_names(
        owned_root
    ) == initial and _transaction_control_inventory_is_exact(
        initial,
        store_present=False,
    )


def _bounded_transaction_names(
    descriptor: int,
    *,
    windows: bool,
) -> tuple[str, ...]:
    limit = LIMIT_POLICY.value("bundle_files")
    if windows:
        return _windows_list_names(descriptor, limit=limit)
    names: list[str] = []
    with os.scandir(descriptor) as entries:
        for entry in entries:
            names.append(entry.name)
            if len(names) > limit:
                raise OSError(errno.E2BIG, "transaction inventory limit exceeded")
    return tuple(sorted(names))


def _inventory_name_key(name: str) -> str:
    return unicodedata.normalize("NFKC", name).casefold()


def _reject_inventory_name_collisions(names: tuple[str, ...]) -> None:
    observed: dict[str, str] = {}
    for name in names:
        key = _inventory_name_key(name)
        previous = observed.setdefault(key, name)
        if previous != name:
            raise OSError(errno.EEXIST, "transaction inventory name collision")


def _claim_transaction_ids(names: tuple[str, ...]) -> set[str]:
    transaction_ids: set[str] = set()
    reserved_prefixes = ("tx-", ".tx-", ".claim-", ".record-", ".retired-")
    for name in names:
        match = _TRANSACTION_ANCHOR.fullmatch(name)
        if match is not None:
            transaction_ids.add(match.group(1))
            continue
        match = _TRANSACTION_CREATION_RECORD.fullmatch(name)
        if match is not None:
            transaction_ids.add(match.group(1))
            continue
        match = _TRANSACTION_CLEANUP_RECORD.fullmatch(name)
        if match is not None:
            transaction_ids.add(match.group(1))
            continue
        match = _TRANSACTION_RECOVERY_CLEANUP_RECORD.fullmatch(name)
        if match is not None:
            transaction_ids.add(match.group(1))
            continue
        if _TRANSACTION_RETIRED_RECORD.fullmatch(name) is not None:
            continue
        if (
            _TRANSACTION_PENDING_CLAIM.fullmatch(name) is not None
            or _TRANSACTION_RECORD_STAGE.fullmatch(name) is not None
        ):
            raise OSError(errno.ESTALE, "unbound transaction record remains")
        key = _inventory_name_key(name)
        if any(key.startswith(prefix) for prefix in reserved_prefixes):
            raise OSError(errno.EINVAL, "transaction claim name is ambiguous")
    return transaction_ids


def _store_transaction_ids(names: tuple[str, ...]) -> set[str]:
    transaction_ids: set[str] = set()
    reserved_prefixes = (
        "tx-",
        ".root-",
        ".delete-",
        ".zagrosi-quarantine-",
        "control",
        "claims",
    )
    for name in names:
        if name in {_TRANSACTION_STORE_CONTROL, _TRANSACTION_CLAIMS_COMPONENT}:
            continue
        if _PERSISTENT_TRANSACTION.fullmatch(name) is not None:
            transaction_ids.add(name)
            continue
        if (
            _TRANSACTION_STAGE.fullmatch(name) is not None
            or _TRANSACTION_DELETE.fullmatch(name) is not None
            or _TRANSACTION_QUARANTINE.fullmatch(name) is not None
        ):
            continue
        key = _inventory_name_key(name)
        if any(key.startswith(prefix) for prefix in reserved_prefixes):
            raise OSError(errno.EINVAL, "transaction store name is ambiguous")
    return transaction_ids


def _load_transaction_binding_from_store(
    store: _TransactionStore,
    transaction_id: str,
) -> PersistentTransactionBinding:
    component = f"{transaction_id}.json"
    if store.windows:
        raw, identity = _read_windows_private_record(
            store.claims,
            component,
            volume=store.plugins_identity[0],
        )
    else:
        raw, identity = _read_posix_private_record(
            store.claims,
            component,
            device=store.plugins_identity[0],
        )
    binding = _binding_from_record(
        raw,
        transaction_id=transaction_id,
        store=store,
        claim_identity=identity,
    )
    if not _private_record_name_binds(
        store.claims,
        component,
        identity,
        expected_raw=raw,
        windows=store.windows,
    ):
        raise OSError(errno.ESTALE, "transaction anchor changed")
    return binding


def _observe_pending_transaction(
    owned_root: OwnedRoot,
    store: _TransactionStore,
    binding: PersistentTransactionBinding,
) -> PendingTransactionObservation | None:
    live = quarantine = deleting = 0 if store.windows else -1
    try:
        creation_intent = _load_transaction_creation_intent(
            store,
            binding.transaction_id,
        )
        creation_reservation = _load_transaction_creation_reservation(
            store,
            binding.transaction_id,
        )
        if creation_intent is None:
            if creation_reservation is not None:
                raise OSError(
                    errno.ESTALE,
                    "transaction reservation has no bound successor",
                )
        elif creation_intent.binding != binding or (
            creation_reservation is not None
            and (
                creation_reservation.identity != creation_intent.reservation_identity
                or hashlib.sha256(creation_reservation.raw).hexdigest()
                != creation_intent.reservation_digest
            )
        ):
            raise OSError(errno.ESTALE, "transaction creation evidence changed")

        cleanup_intent = _load_transaction_cleanup_record(
            store,
            binding,
            complete=False,
        )
        cleanup_complete = _load_transaction_cleanup_record(
            store,
            binding,
            complete=True,
        )
        if (
            cleanup_intent is not None
            and cleanup_complete is not None
            and cleanup_intent.delete_component != cleanup_complete.delete_component
        ):
            raise OSError(errno.ESTALE, "transaction cleanup evidence conflicts")
        delete_component = (
            cleanup_intent.delete_component
            if cleanup_intent is not None
            else (
                cleanup_complete.delete_component
                if cleanup_complete is not None
                else None
            )
        )
        live_component = binding.root_relative.rsplit("/", 1)[-1]
        quarantine_component = binding.quarantine_relative.rsplit("/", 1)[-1]
        if store.windows:
            live = _open_windows_transaction_location(
                store,
                live_component,
                expected_identity=binding.transaction_identity,
                filesystem_guard=owned_root._filesystem_guard,
            )
            quarantine = _open_windows_transaction_location(
                store,
                quarantine_component,
                expected_identity=binding.transaction_identity,
                filesystem_guard=owned_root._filesystem_guard,
            )
            if delete_component is not None:
                deleting = _open_windows_transaction_location(
                    store,
                    delete_component,
                    expected_identity=binding.transaction_identity,
                    filesystem_guard=owned_root._filesystem_guard,
                )
            locations = (bool(live), bool(quarantine), bool(deleting))
        else:
            live = _open_posix_transaction_location(
                store,
                live_component,
                expected_identity=binding.transaction_identity,
                filesystem_guard=owned_root._filesystem_guard,
            )
            quarantine = _open_posix_transaction_location(
                store,
                quarantine_component,
                expected_identity=binding.transaction_identity,
                filesystem_guard=owned_root._filesystem_guard,
            )
            if delete_component is not None:
                deleting = _open_posix_transaction_location(
                    store,
                    delete_component,
                    expected_identity=binding.transaction_identity,
                    filesystem_guard=owned_root._filesystem_guard,
                )
            locations = (live >= 0, quarantine >= 0, deleting >= 0)
        if sum(locations) > 1:
            raise OSError(errno.ESTALE, "transaction locations are ambiguous")
        if (
            _load_transaction_binding_from_store(store, binding.transaction_id)
            != binding
        ):
            raise OSError(errno.ESTALE, "transaction anchor changed")
        if not _transaction_store_namespace_is_valid(
            store
        ) or not owned_root._validate_control_descriptor(store.control):
            raise OSError(errno.ESTALE, "transaction namespace changed")
        if locations[0]:
            if cleanup_intent is not None or cleanup_complete is not None:
                raise OSError(
                    errno.ESTALE,
                    "live transaction has cleanup evidence",
                )
            return PendingTransactionObservation(
                binding=binding,
                location=TransactionLocation.LIVE,
                journal_relative=binding.root_relative,
                _token=_PENDING_TRANSACTION_OBSERVATION_TOKEN,
            )
        if locations[1]:
            if cleanup_complete is not None:
                raise OSError(
                    errno.ESTALE,
                    "quarantine conflicts with cleanup completion",
                )
            return PendingTransactionObservation(
                binding=binding,
                location=TransactionLocation.QUARANTINED,
                journal_relative=binding.quarantine_relative,
                _token=_PENDING_TRANSACTION_OBSERVATION_TOKEN,
            )
        if locations[2]:
            if (
                cleanup_intent is None
                or cleanup_complete is not None
                or delete_component is None
            ):
                raise OSError(
                    errno.ESTALE,
                    "delete-token transaction lacks exact cleanup intent",
                )
            return PendingTransactionObservation(
                binding=binding,
                location=TransactionLocation.QUARANTINED,
                journal_relative=_persistent_delete_reference(
                    binding,
                    delete_component,
                ),
                _token=_PENDING_TRANSACTION_OBSERVATION_TOKEN,
            )
        if cleanup_complete is None:
            raise OSError(
                errno.ESTALE,
                "transaction root is missing without cleanup completion",
            )
        return None
    finally:
        if store.windows:
            for descriptor in (deleting, quarantine, live):
                if descriptor:
                    _paths._windows_close(descriptor)
        else:
            for descriptor in (deleting, quarantine, live):
                if descriptor >= 0:
                    os.close(descriptor)


def _active_recovery_cleanup_transaction_ids(
    claim_names: tuple[str, ...],
) -> tuple[str, ...]:
    phase_ids = {
        match.group(1)
        for name in claim_names
        if (match := _TRANSACTION_RECOVERY_CLEANUP_RECORD.fullmatch(name)) is not None
    }
    authorized_suffix = ".rc-auth.json"
    authorized_ids = {
        name[: -len(authorized_suffix)]
        for name in claim_names
        if name.endswith(authorized_suffix)
        and _PERSISTENT_TRANSACTION.fullmatch(name[: -len(authorized_suffix)])
        is not None
    }
    if phase_ids - authorized_ids:
        raise OSError(errno.ESTALE, "recovery cleanup WAL lacks authorization")
    return tuple(sorted(authorized_ids))


def _inspect_active_recovery_cleanup_authorizations(
    owned_root: OwnedRoot,
    store: _TransactionStore,
    claim_names: tuple[str, ...],
) -> tuple[tuple[RecoveryCleanupAuthorization, _RecoveryCleanupState], ...]:
    inspected: list[tuple[RecoveryCleanupAuthorization, _RecoveryCleanupState]] = []
    for transaction_id in _active_recovery_cleanup_transaction_ids(claim_names):
        binding = _load_transaction_binding_from_store(store, transaction_id)
        authorization = _load_recovery_cleanup_authorization(store, binding)
        if authorization is None:
            raise OSError(errno.ESTALE, "recovery cleanup authorization is missing")
        state = _inspect_recovery_cleanup_state(
            owned_root,
            store,
            authorization,
        )
        if not state.authorization_active:
            raise OSError(errno.ESTALE, "recovery cleanup authorization is retired")
        inspected.append((authorization, state))
    return tuple(inspected)


def discover_pending_transactions(
    owned_root: OwnedRoot,
) -> Result[tuple[PendingTransactionObservation, ...]]:
    """Discover exact persistent journal roots without creating or retiring state."""

    if not isinstance(owned_root, OwnedRoot):
        return Result.failure(
            _error(
                "ownership.cleanup_incomplete",
                "Pending transaction discovery requires an owned root.",
            )
        )
    store: _TransactionStore | None = None
    try:
        if _transaction_store_is_exactly_absent(owned_root):
            return Result.success(())
        control_names = _transaction_control_names(owned_root)
        if not _transaction_control_inventory_is_exact(
            control_names,
            store_present=True,
        ):
            raise OSError(errno.ESTALE, "transaction store bootstrap is ambiguous")
        store = _open_transaction_store(owned_root, create=False)
        store_names = _bounded_transaction_names(
            store.store,
            windows=store.windows,
        )
        claim_names = _bounded_transaction_names(
            store.claims,
            windows=store.windows,
        )
        _reject_inventory_name_collisions(store_names)
        _reject_inventory_name_collisions(claim_names)
        store_ids = _store_transaction_ids(store_names)
        claim_ids = _claim_transaction_ids(claim_names)
        anchor_ids = {
            match.group(1)
            for name in claim_names
            if (match := _TRANSACTION_ANCHOR.fullmatch(name)) is not None
        }
        if (store_ids | claim_ids) - anchor_ids:
            raise OSError(errno.ESTALE, "unbound transaction state remains")
        recovery_cleanup = _inspect_active_recovery_cleanup_authorizations(
            owned_root,
            store,
            claim_names,
        )
        recovery_cleanup_ids = {
            authorization.transaction_id for authorization, _state in recovery_cleanup
        }

        observations: list[PendingTransactionObservation] = []
        expected_store_names = {
            _TRANSACTION_STORE_CONTROL,
            _TRANSACTION_CLAIMS_COMPONENT,
        }
        expected_store_names.update(
            state.journal_relative.rsplit("/", 1)[-1]
            for _authorization, state in recovery_cleanup
            if state.journal_relative is not None
        )
        maximum = LIMIT_POLICY.value("journal_records") + 1
        for transaction_id in sorted(anchor_ids - recovery_cleanup_ids):
            binding = _load_transaction_binding_from_store(store, transaction_id)
            observation = _observe_pending_transaction(owned_root, store, binding)
            if observation is not None:
                observations.append(observation)
                expected_store_names.add(
                    observation.journal_relative.rsplit("/", 1)[-1]
                )
                if len(observations) >= maximum:
                    break
        if len(observations) < maximum:
            reserved_locations = {
                name
                for name in store_names
                if (
                    _PERSISTENT_TRANSACTION.fullmatch(name) is not None
                    or _TRANSACTION_STAGE.fullmatch(name) is not None
                    or _TRANSACTION_DELETE.fullmatch(name) is not None
                    or _TRANSACTION_QUARANTINE.fullmatch(name) is not None
                )
            }
            if reserved_locations - expected_store_names:
                raise OSError(errno.ESTALE, "unbound transaction location remains")
        if (
            _bounded_transaction_names(store.store, windows=store.windows)
            != store_names
            or _bounded_transaction_names(store.claims, windows=store.windows)
            != claim_names
            or _transaction_control_names(owned_root) != control_names
            or not _transaction_store_namespace_is_valid(store)
            or not owned_root._validate_control_descriptor(store.control)
        ):
            raise OSError(errno.ESTALE, "transaction inventory namespace changed")
        return Result.success(tuple(observations))
    except (ForgeError, OSError, TypeError, ValueError):
        return Result.failure(
            _error(
                "ownership.cleanup_incomplete",
                "Pending transaction discovery cannot trust the durable inventory.",
            )
        )
    finally:
        if store is not None:
            store.close()


@dataclass(slots=True)
class _OpenRecoveryGeneration:
    capture: _RecoveryGenerationCapture
    parent: int
    leaf: int
    manifest: OpenedRegularFile | None

    def close(self) -> None:
        first_failure: BaseException | None = None
        manifest = self.manifest
        self.manifest = None
        if manifest is not None:
            try:
                manifest.close()
            except BaseException as exc:
                first_failure = exc
        empty = 0 if os.name == "nt" else -1
        for attribute in ("leaf", "parent"):
            descriptor = cast(int, getattr(self, attribute))
            if descriptor == empty:
                continue
            setattr(self, attribute, empty)
            try:
                _close_native(descriptor)
            except BaseException as exc:
                if first_failure is None:
                    first_failure = exc
        if first_failure is not None:
            raise first_failure


@dataclass(slots=True)
class _OpenRecoveryReceipt:
    status: str
    control_identity: tuple[int, int]
    existing_depth: int
    ancestor_identity: tuple[int, int]
    parent_identity: tuple[int, int] | None
    identity: tuple[int, int] | None
    digest: str | None
    opened: OpenedRegularFile | None
    ancestor: int

    def close(self) -> None:
        first_failure: BaseException | None = None
        opened = self.opened
        self.opened = None
        if opened is not None:
            try:
                opened.close()
            except BaseException as exc:
                first_failure = exc
        empty = 0 if os.name == "nt" else -1
        ancestor = self.ancestor
        self.ancestor = empty
        if ancestor != empty:
            try:
                _close_native(ancestor)
            except BaseException as exc:
                if first_failure is None:
                    first_failure = exc
        if first_failure is not None:
            raise first_failure


def _recovery_source_root(owned_root: OwnedRoot) -> _paths.SourceRoot:
    descriptor = owned_root._duplicate_descriptor()
    selected: _paths.SourceRoot | None = None
    try:
        source_type = (
            _paths._WindowsSourceRoot if os.name == "nt" else _paths.SourceRoot
        )
        selected = source_type(
            descriptor,
            owned_root.absolute_ancestry,
            owned_root._filesystem_guard,
            owned_root._origin,
            _token=_paths._CAPABILITY_TOKEN,
        )
        descriptor = 0 if os.name == "nt" else -1
        selected._require_open()
        return selected
    except BaseException:
        if selected is not None:
            selected.close()
        elif (os.name == "nt" and descriptor) or (os.name != "nt" and descriptor >= 0):
            _close_native(descriptor)
        raise


def _open_recovery_generation(
    owned_root: OwnedRoot,
    source_root: _paths.SourceRoot,
    *,
    relative_path: str,
    expected_parent_identity: tuple[int, int],
    expected_leaf_identity: tuple[int, int],
    expected_manifest_digest: str,
    manifest_relative: str,
) -> _OpenRecoveryGeneration:
    reference = _paths.validate_reference(
        relative_path,
        role="recovery-generation",
        limits=LIMIT_POLICY,
    ).unwrap()
    manifest_reference = _paths.validate_reference(
        manifest_relative,
        role="recovery-manifest",
        limits=LIMIT_POLICY,
    ).unwrap()
    parent = leaf = 0 if os.name == "nt" else -1
    manifest: OpenedRegularFile | None = None
    try:
        root = owned_root._duplicate_descriptor()
        try:
            parent = (
                _windows_open_parent(root, reference.components[:-1])
                if os.name == "nt"
                else _open_parent(root, reference.components[:-1])
            )
        finally:
            _close_native(root)
        leaf = (
            _windows_open_raw_child(
                parent,
                reference.components[-1],
                directory=True,
            )
            if os.name == "nt"
            else os.open(reference.components[-1], _directory_flags(), dir_fd=parent)
        )
        parent_identity = _native_identity(parent)
        leaf_identity = _native_identity(leaf)
        if os.name == "nt":
            windows_leaf_status = _paths._windows_handle_status(leaf)
            valid_leaf = (
                windows_leaf_status.is_directory
                and not windows_leaf_status.is_reparse
                and windows_leaf_status.identity[0] == owned_root.identity[0]
                and owned_root._filesystem_guard(leaf)
                and _paths._windows_namespace_binds(
                    parent,
                    reference.components[-1],
                    leaf_identity,
                )
            )
        else:
            posix_leaf_status = os.fstat(leaf)
            valid_leaf = (
                stat.S_ISDIR(posix_leaf_status.st_mode)
                and posix_leaf_status.st_dev == owned_root.identity[0]
                and posix_leaf_status.st_uid == os.geteuid()
                and owned_root._filesystem_guard(leaf)
                and _paths._posix_namespace_binds(
                    parent,
                    reference.components[-1],
                    leaf_identity,
                )
            )
        if (
            not valid_leaf
            or parent_identity != expected_parent_identity
            or leaf_identity != expected_leaf_identity
            or not owned_root._validate_live_descriptor()
        ):
            raise OSError(errno.ESTALE, "published generation identity changed")
        manifest = source_root.open_regular_file(manifest_reference)
        raw = manifest.read_bytes(limit=256 * 1024)
        if (
            manifest.root_identity != owned_root.identity
            or hashlib.sha256(raw).hexdigest() != expected_manifest_digest
        ):
            raise OSError(errno.ESTALE, "published manifest identity changed")
        capture = _RecoveryGenerationCapture(
            relative_path=relative_path,
            parent_identity=parent_identity,
            leaf_identity=leaf_identity,
            manifest_relative=manifest_relative,
            manifest_parent_identity=manifest.parent_identity,
            manifest_identity=manifest.identity,
            manifest_digest=hashlib.sha256(raw).hexdigest(),
        )
        if not _recovery_generation_capture_invariants(capture):
            raise OSError(errno.ESTALE, "published generation evidence changed")
        opened = _OpenRecoveryGeneration(
            capture=capture,
            parent=parent,
            leaf=leaf,
            manifest=manifest,
        )
        parent = leaf = 0 if os.name == "nt" else -1
        manifest = None
        return opened
    finally:
        first_failure: BaseException | None = None
        closing_manifest = manifest
        manifest = None
        if closing_manifest is not None:
            try:
                closing_manifest.close()
            except BaseException as exc:
                first_failure = exc
        try:
            _close_all_native_descriptors(leaf, parent)
        except BaseException as exc:
            if first_failure is None:
                first_failure = exc
        if first_failure is not None:
            raise first_failure


def _recovery_generation_is_current(
    owned_root: OwnedRoot,
    source_root: _paths.SourceRoot,
    opened: _OpenRecoveryGeneration,
) -> bool:
    capture = opened.capture
    manifest = opened.manifest
    canonical_manifest: OpenedRegularFile | None = None
    try:
        if manifest is None:
            return False
        parent_identity = _native_identity(opened.parent)
        leaf_identity = _native_identity(opened.leaf)
        if os.name == "nt":
            windows_parent_status = _paths._windows_handle_status(opened.parent)
            windows_leaf_status = _paths._windows_handle_status(opened.leaf)
            descriptors_valid = (
                windows_parent_status.is_directory
                and not windows_parent_status.is_reparse
                and windows_parent_status.identity[0] == owned_root.identity[0]
                and windows_leaf_status.is_directory
                and not windows_leaf_status.is_reparse
                and windows_leaf_status.identity[0] == owned_root.identity[0]
                and owned_root._filesystem_guard(opened.parent)
                and owned_root._filesystem_guard(opened.leaf)
            )
        else:
            posix_parent_status = os.fstat(opened.parent)
            posix_leaf_status = os.fstat(opened.leaf)
            descriptors_valid = (
                stat.S_ISDIR(posix_parent_status.st_mode)
                and posix_parent_status.st_dev == owned_root.identity[0]
                and posix_parent_status.st_uid == os.geteuid()
                and stat.S_ISDIR(posix_leaf_status.st_mode)
                and posix_leaf_status.st_dev == owned_root.identity[0]
                and posix_leaf_status.st_uid == os.geteuid()
                and owned_root._filesystem_guard(opened.parent)
                and owned_root._filesystem_guard(opened.leaf)
            )
        if (
            not descriptors_valid
            or parent_identity != capture.parent_identity
            or leaf_identity != capture.leaf_identity
            or manifest.identity != capture.manifest_identity
            or manifest.parent_identity != capture.manifest_parent_identity
            or hashlib.sha256(manifest.read_bytes(limit=256 * 1024)).hexdigest()
            != capture.manifest_digest
            or not owned_root._validate_live_descriptor()
        ):
            return False
        component = capture.relative_path.rsplit("/", 1)[-1]
        leaf_bound = (
            _paths._windows_namespace_binds(
                opened.parent,
                component,
                capture.leaf_identity,
            )
            if os.name == "nt"
            else _paths._posix_namespace_binds(
                opened.parent,
                component,
                capture.leaf_identity,
            )
        )
        if not leaf_bound:
            return False
        manifest_reference = _paths.validate_reference(
            capture.manifest_relative,
            role="recovery-manifest",
            limits=LIMIT_POLICY,
        ).unwrap()
        canonical_manifest = source_root.open_regular_file(manifest_reference)
        canonical_raw = canonical_manifest.read_bytes(limit=256 * 1024)
        return (
            canonical_manifest.root_identity == owned_root.identity
            and canonical_manifest.parent_identity == capture.manifest_parent_identity
            and canonical_manifest.identity == capture.manifest_identity
            and hashlib.sha256(canonical_raw).hexdigest() == capture.manifest_digest
            and owned_root._validate_live_descriptor()
        )
    except (ForgeError, OSError, TypeError, ValueError):
        return False
    finally:
        if canonical_manifest is not None:
            canonical_manifest.close()


def _native_path_is_missing(exc: OSError) -> bool:
    return isinstance(exc, FileNotFoundError) or (
        os.name == "nt" and getattr(exc, "winerror", None) in {2, 3}
    )


def _open_recovery_receipt_absence(
    owned_root: OwnedRoot,
    reference: _SafeReferenceSnapshot,
) -> _OpenRecoveryReceipt:
    empty = 0 if os.name == "nt" else -1
    current = child = empty
    parent_components = reference.components[1:-1]
    if (
        not reference.components
        or reference.components[0] != ".zagrosi"
        or not parent_components
    ):
        raise ValueError("committed receipt reference")
    try:
        current = owned_root._duplicate_control_descriptor()
        if _native_identity(
            current
        ) != owned_root.control_identity or not owned_root._validate_control_descriptor(
            current
        ):
            raise OSError(errno.ESTALE, "receipt control changed")
        for depth, component in enumerate(parent_components):
            try:
                child = (
                    _windows_open_raw_child(
                        current,
                        component,
                        directory=True,
                    )
                    if os.name == "nt"
                    else os.open(component, _directory_flags(), dir_fd=current)
                )
            except OSError as exc:
                if not _native_path_is_missing(exc):
                    raise
                if not owned_root._validate_control_binding():
                    raise OSError(errno.ESTALE, "receipt control changed") from None
                selected = _OpenRecoveryReceipt(
                    status="absent",
                    control_identity=owned_root.control_identity,
                    existing_depth=depth,
                    ancestor_identity=_native_identity(current),
                    parent_identity=None,
                    identity=None,
                    digest=None,
                    opened=None,
                    ancestor=current,
                )
                current = empty
                return selected
            child_identity = _native_identity(child)
            if os.name == "nt":
                status = _paths._windows_handle_status(child)
                private = (
                    status.identity[0] == owned_root.identity[0]
                    and status.is_directory
                    and not status.is_reparse
                    and _paths._windows_private_directory(child, exact=True)
                    and owned_root._filesystem_guard(child)
                    and _paths._windows_namespace_binds(
                        current,
                        component,
                        child_identity,
                    )
                )
            else:
                private = (
                    _private_posix_receipt_parent(
                        child,
                        device=owned_root.identity[0],
                    )
                    and owned_root._filesystem_guard(child)
                    and _paths._posix_namespace_binds(
                        current,
                        component,
                        child_identity,
                    )
                )
            if not private:
                raise OSError(errno.EPERM, "receipt parent is not private")
            previous = current
            current = empty
            _close_native(previous)
            current = child
            child = empty
        leaf = reference.components[-1]
        try:
            if os.name == "nt":
                child = _windows_open_raw_child(
                    current,
                    leaf,
                    directory=None,
                    read_data=True,
                )
            else:
                os.stat(leaf, dir_fd=current, follow_symlinks=False)
        except OSError as exc:
            if not _native_path_is_missing(exc):
                raise
        else:
            raise OSError(errno.EEXIST, "committed receipt appeared")
        if not owned_root._validate_control_binding():
            raise OSError(errno.ESTALE, "receipt control changed")
        selected = _OpenRecoveryReceipt(
            status="absent",
            control_identity=owned_root.control_identity,
            existing_depth=len(parent_components),
            ancestor_identity=_native_identity(current),
            parent_identity=None,
            identity=None,
            digest=None,
            opened=None,
            ancestor=current,
        )
        current = empty
        return selected
    finally:
        _close_all_native_descriptors(child, current)


def _recovery_receipt_absence_is_current(
    owned_root: OwnedRoot,
    opened: _OpenRecoveryReceipt,
    reference: _SafeReferenceSnapshot,
) -> bool:
    empty = 0 if os.name == "nt" else -1
    control = parent = probe = empty
    parent_components = reference.components[1:-1]
    try:
        if (
            opened.status != "absent"
            or opened.opened is not None
            or opened.ancestor == empty
            or opened.control_identity != owned_root.control_identity
            or opened.existing_depth < 0
            or opened.existing_depth > len(parent_components)
            or _native_identity(opened.ancestor) != opened.ancestor_identity
        ):
            return False
        if os.name == "nt":
            ancestor_status = _paths._windows_handle_status(opened.ancestor)
            ancestor_private = (
                ancestor_status.identity[0] == owned_root.identity[0]
                and ancestor_status.is_directory
                and not ancestor_status.is_reparse
                and _paths._windows_private_directory(
                    opened.ancestor,
                    exact=True,
                )
                and owned_root._filesystem_guard(opened.ancestor)
            )
        else:
            ancestor_private = _private_posix_receipt_parent(
                opened.ancestor,
                device=owned_root.identity[0],
            ) and owned_root._filesystem_guard(opened.ancestor)
        if not ancestor_private:
            return False
        control = owned_root._duplicate_control_descriptor()
        if _native_identity(
            control
        ) != opened.control_identity or not owned_root._validate_control_descriptor(
            control
        ):
            return False
        if opened.existing_depth:
            parent = (
                _windows_open_private_directory_chain(
                    control,
                    parent_components[: opened.existing_depth],
                    volume=owned_root.identity[0],
                    create_missing=False,
                )
                if os.name == "nt"
                else _open_private_directory_chain(
                    control,
                    parent_components[: opened.existing_depth],
                    device=owned_root.identity[0],
                    create_missing=False,
                )
            )
        else:
            parent = (
                _paths._windows_duplicate(control)
                if os.name == "nt"
                else os.dup(control)
            )
        if _native_identity(parent) != opened.ancestor_identity:
            return False
        component = (
            parent_components[opened.existing_depth]
            if opened.existing_depth < len(parent_components)
            else reference.components[-1]
        )
        try:
            if os.name == "nt":
                probe = _windows_open_raw_child(
                    parent,
                    component,
                    directory=None,
                    read_data=True,
                )
            else:
                os.stat(component, dir_fd=parent, follow_symlinks=False)
        except OSError as exc:
            missing = _native_path_is_missing(exc)
        else:
            missing = False
        return missing and owned_root._validate_control_binding()
    except (ForgeError, OSError, TypeError, ValueError):
        return False
    finally:
        _close_all_native_descriptors(probe, parent, control)


def _recovery_receipt_is_current(
    owned_root: OwnedRoot,
    opened: _OpenRecoveryReceipt,
    reference: _SafeReferenceSnapshot,
    *,
    expected_raw: bytes,
) -> bool:
    if opened.status == "absent":
        return _recovery_receipt_absence_is_current(
            owned_root,
            opened,
            reference,
        )
    duplicate = 0 if os.name == "nt" else -1
    try:
        if (
            opened.status != "matching"
            or opened.opened is None
            or opened.control_identity != owned_root.control_identity
            or opened.existing_depth != len(reference.components[1:-1])
            or opened.ancestor_identity != opened.parent_identity
        ):
            return False
        raw = opened.opened.read_bytes(limit=256 * 1024)
        duplicate = opened.opened._duplicate_descriptor()
        binding = _receipt_binding_state(
            owned_root,
            reference,
            expected_parent_identity=opened.parent_identity,
            retained_leaf=duplicate,
        )
        return (
            raw == expected_raw
            and opened.opened.root_identity == owned_root.identity
            and opened.opened.parent_identity == opened.parent_identity
            and opened.opened.identity == opened.identity
            and hashlib.sha256(raw).hexdigest() == opened.digest
            and _receipt_parent_chain_is_private(owned_root, reference)
            and binding.valid
        )
    except (ForgeError, OSError, TypeError, ValueError):
        return False
    finally:
        if (os.name == "nt" and duplicate) or (os.name != "nt" and duplicate >= 0):
            _close_native(duplicate)


def _open_recovery_receipt(
    owned_root: OwnedRoot,
    source_root: _paths.SourceRoot,
    *,
    reference: SafeRelativePath,
    expected_raw: bytes,
) -> _OpenRecoveryReceipt:
    snapshot = _snapshot_safe_reference(reference)
    if snapshot is None:
        raise ValueError("committed receipt reference")
    opened: OpenedRegularFile | None = None
    try:
        opened = source_root.open_regular_file(reference)
    except ForgeError as exc:
        if exc.code == "path.missing":
            return _open_recovery_receipt_absence(owned_root, snapshot)
        raise
    duplicate = 0 if os.name == "nt" else -1
    try:
        raw = opened.read_bytes(limit=256 * 1024)
        duplicate = opened._duplicate_descriptor()
        binding = _receipt_binding_state(
            owned_root,
            snapshot,
            expected_parent_identity=opened.parent_identity,
            retained_leaf=duplicate,
        )
        if (
            raw != expected_raw
            or opened.root_identity != owned_root.identity
            or not _private_receipt_descriptor(
                duplicate,
                device=owned_root.identity[0],
            )
            or not _receipt_parent_chain_is_private(owned_root, snapshot)
            or not binding.valid
        ):
            raise OSError(errno.ESTALE, "committed receipt changed")
        return _OpenRecoveryReceipt(
            status="matching",
            control_identity=owned_root.control_identity,
            existing_depth=len(snapshot.components[1:-1]),
            ancestor_identity=opened.parent_identity,
            parent_identity=opened.parent_identity,
            identity=opened.identity,
            digest=hashlib.sha256(raw).hexdigest(),
            opened=opened,
            ancestor=0 if os.name == "nt" else -1,
        )
    except BaseException:
        opened.close()
        raise
    finally:
        if (os.name == "nt" and duplicate) or (os.name != "nt" and duplicate >= 0):
            _close_native(duplicate)


def _recovery_finalization_evidence(
    observation: PendingTransactionObservation,
    journal: LoadedJournal,
) -> tuple[
    PersistentTransactionBinding,
    str,
    str,
    int,
    str,
    str,
    JournalPathIdentity,
    JournalPathIdentity,
    bytes,
    SafeRelativePath,
]:
    from .journal import JournalState, LoadedJournal as RuntimeLoadedJournal

    if (
        type(observation) is not PendingTransactionObservation
        or observation._seal is not _PENDING_TRANSACTION_OBSERVATION_TOKEN
        or type(journal) is not RuntimeLoadedJournal
    ):
        raise TypeError("recovery finalization evidence")
    journal._require_valid()
    binding = observation.binding
    binding_digest = _recovery_cleanup_binding_digest(binding)
    head = journal.records[-1]
    if (
        observation.location is not TransactionLocation.LIVE
        or observation.journal_relative != binding.root_relative
        or head.state
        not in {
            JournalState.COMMIT_INTENT,
            JournalState.CONFIG_COMMITTED,
            JournalState.RECEIPT_COMMITTED,
        }
        or head.transaction_id != binding.transaction_id
        or journal.head.transaction_binding_digest != binding_digest
        or journal.access_digest
        != _transaction_journal_access_digest(
            binding,
            location=observation.location,
            journal_relative=observation.journal_relative,
        )
        or _DIGEST.fullmatch(journal._binding_digest) is None
        or head.verification_evidence_digest is None
        or head.config_recovery is None
    ):
        raise ValueError("recovery finalization evidence")
    prepared = head.prepared
    source = tuple(item for item in head.identities if item.role == "source-generation")
    cache = tuple(item for item in head.identities if item.role == "cache-generation")
    receipt = tuple(
        item for item in head.identities if item.role == "committed-receipt"
    )
    if (
        len(source) != 1
        or source[0].leaf_identity is None
        or source[0].content_digest is None
        or len(cache) != 1
        or cache[0].leaf_identity is None
        or cache[0].content_digest is None
        or (
            head.state is JournalState.RECEIPT_COMMITTED
            and (
                len(receipt) != 1
                or receipt[0].leaf_identity is None
                or receipt[0].content_digest is None
            )
        )
        or (head.state is not JournalState.RECEIPT_COMMITTED and receipt)
    ):
        raise ValueError("recovery finalization evidence")
    prepared_raw = canonical_json_bytes(
        prepared.prepared_receipt,
        final_newline=True,
    )
    decoded = _decode_committed_receipt(prepared_raw)
    receipt_reference = committed_receipt_reference(
        prepared.effective_marketplace_id,
        prepared.install_identity,
    )
    captured = (
        binding,
        journal.access_digest,
        journal._binding_digest,
        journal.head.sequence,
        journal.head.record_digest,
        journal.head.state.value,
        source[0],
        cache[0],
        prepared_raw,
        receipt_reference,
    )
    journal._require_valid()
    if (
        decoded.source.relative_path != source[0].relative_path
        or decoded.source.manifest_digest != source[0].content_digest
        or decoded.cache.relative_path != cache[0].relative_path
        or decoded.cache.manifest_digest != cache[0].content_digest
        or captured[:6]
        != (
            observation.binding,
            journal.access_digest,
            journal._binding_digest,
            journal.head.sequence,
            journal.head.record_digest,
            journal.head.state.value,
        )
    ):
        raise ValueError("recovery finalization evidence")
    return captured


def observe_recovery_finalization(
    owned_root: OwnedRoot,
    *,
    observation: PendingTransactionObservation,
    journal: LoadedJournal,
) -> Result[RecoveryFinalizationObservation]:
    """Observe fresh committed generations and receipt state without effects."""

    source_root: _paths.SourceRoot | None = None
    first_source = first_cache = second_source = second_cache = None
    first_receipt = second_receipt = None
    try:
        if not isinstance(owned_root, OwnedRoot):
            raise TypeError("owned root")
        (
            _binding,
            journal_access_digest,
            journal_evidence_digest,
            journal_head_sequence,
            journal_head_record_digest,
            journal_state,
            source_identity,
            cache_identity,
            prepared_raw,
            receipt_reference,
        ) = _recovery_finalization_evidence(observation, journal)
        if (
            source_identity.leaf_identity is None
            or source_identity.content_digest is None
            or cache_identity.leaf_identity is None
            or cache_identity.content_digest is None
        ):
            raise ValueError("recovery generation evidence")
        prepared = journal.records[-1].prepared
        source_manifest_relative = (
            f"{source_identity.relative_path}/plugins/"
            f"{prepared.install_identity.plugin_id}/"
            ".codex-plugin/bundle-manifest.json"
        )
        cache_manifest_relative = (
            f"{cache_identity.relative_path}/.codex-plugin/bundle-manifest.json"
        )
        receipt_snapshot = _snapshot_safe_reference(receipt_reference)
        if receipt_snapshot is None:
            raise ValueError("committed receipt reference")
        source_root = _recovery_source_root(owned_root)
        first_source = _open_recovery_generation(
            owned_root,
            source_root,
            relative_path=source_identity.relative_path,
            expected_parent_identity=source_identity.parent_identity,
            expected_leaf_identity=source_identity.leaf_identity,
            expected_manifest_digest=source_identity.content_digest,
            manifest_relative=source_manifest_relative,
        )
        first_cache = _open_recovery_generation(
            owned_root,
            source_root,
            relative_path=cache_identity.relative_path,
            expected_parent_identity=cache_identity.parent_identity,
            expected_leaf_identity=cache_identity.leaf_identity,
            expected_manifest_digest=cache_identity.content_digest,
            manifest_relative=cache_manifest_relative,
        )
        first_receipt = _open_recovery_receipt(
            owned_root,
            source_root,
            reference=receipt_reference,
            expected_raw=prepared_raw,
        )
        second_source = _open_recovery_generation(
            owned_root,
            source_root,
            relative_path=source_identity.relative_path,
            expected_parent_identity=source_identity.parent_identity,
            expected_leaf_identity=source_identity.leaf_identity,
            expected_manifest_digest=source_identity.content_digest,
            manifest_relative=source_manifest_relative,
        )
        second_cache = _open_recovery_generation(
            owned_root,
            source_root,
            relative_path=cache_identity.relative_path,
            expected_parent_identity=cache_identity.parent_identity,
            expected_leaf_identity=cache_identity.leaf_identity,
            expected_manifest_digest=cache_identity.content_digest,
            manifest_relative=cache_manifest_relative,
        )
        second_receipt = _open_recovery_receipt(
            owned_root,
            source_root,
            reference=receipt_reference,
            expected_raw=prepared_raw,
        )
        if (
            first_source.capture != second_source.capture
            or first_cache.capture != second_cache.capture
            or not _recovery_generation_is_current(
                owned_root,
                source_root,
                first_source,
            )
            or not _recovery_generation_is_current(
                owned_root,
                source_root,
                first_cache,
            )
            or not _recovery_generation_is_current(
                owned_root,
                source_root,
                second_source,
            )
            or not _recovery_generation_is_current(
                owned_root,
                source_root,
                second_cache,
            )
            or not _recovery_receipt_is_current(
                owned_root,
                first_receipt,
                receipt_snapshot,
                expected_raw=prepared_raw,
            )
            or not _recovery_receipt_is_current(
                owned_root,
                second_receipt,
                receipt_snapshot,
                expected_raw=prepared_raw,
            )
            or (
                first_receipt.status,
                first_receipt.control_identity,
                first_receipt.existing_depth,
                first_receipt.ancestor_identity,
                first_receipt.parent_identity,
                first_receipt.identity,
                first_receipt.digest,
            )
            != (
                second_receipt.status,
                second_receipt.control_identity,
                second_receipt.existing_depth,
                second_receipt.ancestor_identity,
                second_receipt.parent_identity,
                second_receipt.identity,
                second_receipt.digest,
            )
        ):
            raise OSError(errno.ESTALE, "recovery finalization evidence changed")
        receipt_status = second_receipt.status
        head = journal.records[-1]
        receipt_items = tuple(
            item for item in head.identities if item.role == "committed-receipt"
        )
        if (journal_state == "COMMIT_INTENT" and receipt_status != "absent") or (
            journal_state == "RECEIPT_COMMITTED"
            and (
                receipt_status != "matching"
                or len(receipt_items) != 1
                or receipt_items[0].relative_path != receipt_reference.value
                or receipt_items[0].parent_identity != second_receipt.parent_identity
                or receipt_items[0].leaf_identity != second_receipt.identity
                or receipt_items[0].content_digest != second_receipt.digest
            )
        ):
            raise OSError(errno.ESTALE, "committed receipt state changed")
        journal._require_valid()
        if (
            journal.records[-1] is not head
            or journal.head.sequence != journal_head_sequence
            or journal.head.record_digest != journal_head_record_digest
            or not _recovery_receipt_is_current(
                owned_root,
                first_receipt,
                receipt_snapshot,
                expected_raw=prepared_raw,
            )
            or not _recovery_receipt_is_current(
                owned_root,
                second_receipt,
                receipt_snapshot,
                expected_raw=prepared_raw,
            )
        ):
            raise OSError(errno.ESTALE, "recovery finalization evidence changed")
        capture = _RecoveryFinalizationCapture(
            transaction_id=head.transaction_id,
            journal_state=journal_state,
            journal_access_digest=journal_access_digest,
            journal_evidence_digest=journal_evidence_digest,
            journal_head_sequence=journal_head_sequence,
            journal_head_record_digest=journal_head_record_digest,
            source=second_source.capture,
            cache=second_cache.capture,
            receipt_status=receipt_status,
            receipt_relative=receipt_reference.value,
            receipt_control_identity=second_receipt.control_identity,
            receipt_existing_depth=second_receipt.existing_depth,
            receipt_ancestor_identity=second_receipt.ancestor_identity,
            receipt_parent_identity=second_receipt.parent_identity,
            receipt_identity=second_receipt.identity,
            receipt_digest=second_receipt.digest,
            prepared_receipt_digest=hashlib.sha256(prepared_raw).hexdigest(),
        )
        selected = RecoveryFinalizationObservation(
            capture=capture,
            _token=_RECOVERY_FINALIZATION_OBSERVATION_TOKEN,
        )
        selected._require_valid()
        journal._require_valid()
        return Result.success(selected)
    except (ForgeError, OSError, TypeError, ValueError):
        return Result.failure(
            _error(
                "ownership.identity_mismatch",
                "Committed recovery evidence cannot be observed safely.",
            )
        )
    finally:
        resources = (
            second_receipt,
            second_cache,
            second_source,
            first_receipt,
            first_cache,
            first_source,
            source_root,
        )
        first_failure: BaseException | None = None
        for resource in resources:
            if resource is None:
                continue
            try:
                resource.close()
            except BaseException as exc:
                if first_failure is None:
                    first_failure = exc
        if first_failure is not None:
            raise first_failure


def _terminal_recovery_cleanup_evidence(
    observation: PendingTransactionObservation,
    journal: LoadedJournal,
) -> tuple[
    PersistentTransactionBinding,
    str,
    str,
    int,
    str,
    str,
]:
    from .journal import JournalState, LoadedJournal as RuntimeLoadedJournal

    if (
        type(observation) is not PendingTransactionObservation
        or observation._seal is not _PENDING_TRANSACTION_OBSERVATION_TOKEN
        or type(journal) is not RuntimeLoadedJournal
    ):
        raise TypeError("terminal recovery cleanup evidence")
    journal._require_valid()
    binding = observation.binding
    binding_digest = _recovery_cleanup_binding_digest(binding)
    if (
        observation.location is not TransactionLocation.QUARANTINED
        or not _persistent_cleanup_reference_is_valid(
            binding,
            observation.journal_relative,
        )
        or journal.head.state is not JournalState.ROLLED_BACK
        or journal.records[-1].state is not JournalState.ROLLED_BACK
        or journal.records[-1].transaction_id != binding.transaction_id
        or journal.head.transaction_binding_digest != binding_digest
        or journal.access_digest
        != _recovery_cleanup_access_digest(
            binding,
            location=observation.location,
            journal_relative=observation.journal_relative,
        )
        or _DIGEST.fullmatch(journal._binding_digest) is None
    ):
        raise ValueError("terminal recovery cleanup evidence")
    captured = (
        binding,
        observation.journal_relative,
        journal.access_digest,
        journal.head.sequence,
        journal.head.record_digest,
        journal._binding_digest,
    )
    journal._require_valid()
    confirmed = (
        observation.binding,
        observation.journal_relative,
        journal.access_digest,
        journal.head.sequence,
        journal.head.record_digest,
        journal._binding_digest,
    )
    if captured != confirmed:
        raise ValueError("terminal recovery cleanup evidence")
    return captured


def _authorization_matches_terminal_evidence(
    authorization: RecoveryCleanupAuthorization,
    *,
    binding: PersistentTransactionBinding,
    journal_relative: str,
    journal_access_digest: str,
    journal_head_sequence: int,
    journal_head_record_digest: str,
    journal_evidence_digest: str,
) -> bool:
    try:
        authorization._require_valid()
        return (
            authorization.binding == binding
            and authorization.location is TransactionLocation.QUARANTINED
            and authorization.journal_relative == journal_relative
            and authorization.journal_access_digest == journal_access_digest
            and authorization.journal_head_sequence == journal_head_sequence
            and authorization.journal_head_record_digest == journal_head_record_digest
            and authorization.journal_evidence_digest == journal_evidence_digest
            and authorization.transaction_binding_digest
            == _recovery_cleanup_binding_digest(binding)
        )
    except (AttributeError, TypeError, ValueError):
        return False


def authorize_recovery_cleanup(
    owned_root: OwnedRoot,
    *,
    observation: PendingTransactionObservation,
    journal: LoadedJournal,
) -> Result[RecoveryCleanupAuthorization]:
    """Durably bind one exact quarantined ROLLED_BACK journal to cleanup."""

    store: _TransactionStore | None = None
    state_lock: _TransactionStateLock | None = None
    try:
        if not isinstance(owned_root, OwnedRoot):
            raise TypeError("owned root")
        (
            binding,
            journal_relative,
            journal_access_digest,
            journal_head_sequence,
            journal_head_record_digest,
            journal_evidence_digest,
        ) = _terminal_recovery_cleanup_evidence(observation, journal)
        store = _open_transaction_store(owned_root, create=False)
        state_lock = _acquire_transaction_state_lock(store, binding)
        existing = _load_recovery_cleanup_authorization(store, binding)
        if existing is not None:
            if not _authorization_matches_terminal_evidence(
                existing,
                binding=binding,
                journal_relative=journal_relative,
                journal_access_digest=journal_access_digest,
                journal_head_sequence=journal_head_sequence,
                journal_head_record_digest=journal_head_record_digest,
                journal_evidence_digest=journal_evidence_digest,
            ):
                raise OSError(
                    errno.ESTALE,
                    "recovery cleanup authorization conflicts",
                )
            _inspect_recovery_cleanup_state(owned_root, store, existing)
            return Result.success(existing)
        if any(
            _recovery_cleanup_phase_name_exists(
                store,
                binding.transaction_id,
                phase,
            )
            for phase in (
                _RecoveryCleanupPhase.FINALIZING,
                _RecoveryCleanupPhase.COMPLETE,
            )
        ):
            raise OSError(errno.ESTALE, "recovery cleanup WAL is incomplete")
        store.close()
        store = None

        from .journal import load_pending

        observations = discover_pending_transactions(owned_root)
        if not observations.is_ok or observation not in observations.unwrap():
            raise OSError(errno.ESTALE, "recovery cleanup observation changed")
        loaded = load_pending(owned_root)
        matches = tuple(
            candidate
            for candidate in loaded
            if candidate.records[-1].transaction_id == binding.transaction_id
        )
        if len(matches) != 1 or matches[0] != journal:
            raise OSError(errno.ESTALE, "recovery cleanup journal changed")

        store = _open_transaction_store(owned_root, create=False)
        if (
            _load_transaction_binding_from_store(store, binding.transaction_id)
            != binding
            or _observe_pending_transaction(owned_root, store, binding) != observation
        ):
            raise OSError(errno.ESTALE, "recovery cleanup authority changed")
        cleanup_intent = _load_transaction_cleanup_record(
            store,
            binding,
            complete=False,
        )
        if _load_transaction_cleanup_record(store, binding, complete=True) is not None:
            raise OSError(errno.ESTALE, "recovery cleanup is already complete")
        observed_delete_component = (
            journal_relative.rsplit("/", 1)[-1]
            if journal_relative != binding.quarantine_relative
            else None
        )
        if cleanup_intent is not None:
            if (
                observed_delete_component is not None
                and cleanup_intent.delete_component != observed_delete_component
            ):
                raise OSError(errno.ESTALE, "recovery cleanup intent changed")
            delete_component = cleanup_intent.delete_component
        elif observed_delete_component is not None:
            raise OSError(errno.ESTALE, "recovery cleanup intent is missing")
        else:
            delete_component = f".delete-{secrets.token_hex(16)}.tmp"
        raw = _recovery_cleanup_record_bytes(
            binding,
            location=TransactionLocation.QUARANTINED,
            journal_relative=journal_relative,
            journal_access_digest=journal_access_digest,
            journal_evidence_digest=journal_evidence_digest,
            journal_head_sequence=journal_head_sequence,
            journal_head_record_digest=journal_head_record_digest,
            transaction_binding_digest=_recovery_cleanup_binding_digest(binding),
            delete_component=delete_component,
            phase=_RecoveryCleanupPhase.AUTHORIZED,
            previous_phase_digest=_ZERO_DIGEST,
        )
        component = _recovery_cleanup_component(
            binding.transaction_id,
            _RecoveryCleanupPhase.AUTHORIZED,
        )
        _publish_transaction_record(store, component, raw)
        created = _load_recovery_cleanup_authorization(store, binding)
        if created is None or not _authorization_matches_terminal_evidence(
            created,
            binding=binding,
            journal_relative=journal_relative,
            journal_access_digest=journal_access_digest,
            journal_head_sequence=journal_head_sequence,
            journal_head_record_digest=journal_head_record_digest,
            journal_evidence_digest=journal_evidence_digest,
        ):
            raise OSError(errno.ESTALE, "recovery cleanup publication changed")
        _inspect_recovery_cleanup_state(owned_root, store, created)
        return Result.success(created)
    except (AttributeError, ForgeError, OSError, TypeError, ValueError):
        recovery = (
            (observation.binding.quarantine_relative,)
            if type(observation) is PendingTransactionObservation
            and _persistent_binding_invariants(observation.binding)
            else ()
        )
        return Result.failure(
            _error(
                "ownership.cleanup_incomplete",
                "Recovery cleanup cannot be authorized.",
                recovery=recovery,
            )
        )
    finally:
        try:
            if store is not None:
                store.close()
        finally:
            if state_lock is not None:
                state_lock.close()


def _discover_recovery_cleanup_observations_once(
    owned_root: OwnedRoot,
) -> tuple[RecoveryCleanupObservation, ...]:
    store: _TransactionStore | None = None
    try:
        if _transaction_store_is_exactly_absent(owned_root):
            return ()
        control_names = _transaction_control_names(owned_root)
        if not _transaction_control_inventory_is_exact(
            control_names,
            store_present=True,
        ):
            raise OSError(errno.ESTALE, "transaction store bootstrap is ambiguous")
        store = _open_transaction_store(owned_root, create=False)
        store_names = _bounded_transaction_names(store.store, windows=store.windows)
        claim_names = _bounded_transaction_names(store.claims, windows=store.windows)
        _reject_inventory_name_collisions(store_names)
        _reject_inventory_name_collisions(claim_names)
        store_ids = _store_transaction_ids(store_names)
        claim_ids = _claim_transaction_ids(claim_names)
        anchor_ids = {
            match.group(1)
            for name in claim_names
            if (match := _TRANSACTION_ANCHOR.fullmatch(name)) is not None
        }
        if (store_ids | claim_ids) - anchor_ids:
            raise OSError(errno.ESTALE, "unbound transaction state remains")
        inspected = _inspect_active_recovery_cleanup_authorizations(
            owned_root,
            store,
            claim_names,
        )
        observations = tuple(
            _capture_recovery_cleanup_observation(authorization, state)
            for authorization, state in inspected
        )
        if (
            _bounded_transaction_names(store.store, windows=store.windows)
            != store_names
            or _bounded_transaction_names(store.claims, windows=store.windows)
            != claim_names
            or _transaction_control_names(owned_root) != control_names
            or not _transaction_store_namespace_is_valid(store)
            or not owned_root._validate_control_descriptor(store.control)
        ):
            raise OSError(errno.ESTALE, "recovery cleanup inventory changed")
        for observation in observations:
            observation._require_valid()
        return observations
    finally:
        if store is not None:
            store.close()


def discover_recovery_cleanup_observations(
    owned_root: OwnedRoot,
) -> Result[tuple[RecoveryCleanupObservation, ...]]:
    """Twice observe exact active cleanup WAL state without minting authority."""

    if not isinstance(owned_root, OwnedRoot):
        return Result.failure(
            _error(
                "ownership.cleanup_incomplete",
                "Recovery cleanup discovery requires an owned root.",
            )
        )
    try:
        first = _discover_recovery_cleanup_observations_once(owned_root)
        second = _discover_recovery_cleanup_observations_once(owned_root)
        if first != second:
            raise OSError(errno.ESTALE, "recovery cleanup inventory changed")
        for observation in second:
            observation._require_valid()
        return Result.success(second)
    except (AttributeError, ForgeError, OSError, TypeError, ValueError):
        return Result.failure(
            _error(
                "ownership.cleanup_incomplete",
                "Recovery cleanup discovery cannot trust the durable inventory.",
            )
        )


def discover_recovery_cleanup_authorizations(
    owned_root: OwnedRoot,
) -> Result[tuple[RecoveryCleanupAuthorization, ...]]:
    """Discover compatible base authorizations from sealed current observations."""

    observed = discover_recovery_cleanup_observations(owned_root)
    if not observed.is_ok:
        error = observed.error
        if error is None:
            return Result.failure(
                _error(
                    "ownership.cleanup_incomplete",
                    "Recovery cleanup discovery cannot trust the durable inventory.",
                )
            )
        return Result.failure(error)
    return Result.success(
        tuple(observation.authorization for observation in observed.unwrap())
    )


def _rebind_persistent_transaction(
    owned_root: OwnedRoot,
    *,
    binding: PersistentTransactionBinding,
    perform_maintenance: bool,
    recovery_authorization: RecoveryCleanupAuthorization | None,
) -> Result[ReboundTransaction]:
    if (
        not isinstance(owned_root, OwnedRoot)
        or not _persistent_binding_invariants(binding)
        or type(perform_maintenance) is not bool
        or (
            recovery_authorization is not None
            and (
                type(recovery_authorization) is not RecoveryCleanupAuthorization
                or not _recovery_cleanup_authorization_invariants(
                    recovery_authorization
                )
                or recovery_authorization.binding != binding
            )
        )
    ):
        return Result.failure(
            _error(
                "ownership.cleanup_incomplete",
                "Persistent transaction cleanup cannot be rebound.",
            )
        )
    loaded_result = load_persistent_transaction_binding(
        owned_root, transaction_id=binding.transaction_id
    )
    if not loaded_result.is_ok or loaded_result.unwrap() != binding:
        return Result.failure(
            _error(
                "ownership.cleanup_incomplete",
                "Persistent transaction cleanup cannot be rebound.",
                recovery=(binding.quarantine_relative,),
            )
        )
    store: _TransactionStore | None = None
    live = quarantine = deleting = 0 if os.name == "nt" else -1
    root = 0 if os.name == "nt" else -1
    namespace: _paths._NamespaceCapability | None = None
    try:
        store = _open_transaction_store(owned_root, create=False)
        recovery_record, recovery_record_active = (
            _load_recovery_cleanup_authorization_any(store, binding)
        )
        if recovery_record is not None:
            recovery_state = _inspect_recovery_cleanup_state(
                owned_root,
                store,
                recovery_record,
            )
            if recovery_record_active and (
                recovery_authorization is None
                or recovery_record != recovery_authorization
            ):
                raise OSError(
                    errno.ESTALE,
                    "persistent transaction is recovery-cleanup authorized",
                )
            if not recovery_record_active and (
                recovery_authorization is not None
                or recovery_state.authorization_active
                or recovery_state.finalizing is None
                or recovery_state.complete is None
                or not recovery_state.finalizing.retired
                or not recovery_state.complete.retired
                or recovery_state.cleanup_complete is None
                or recovery_state.journal_relative is not None
            ):
                raise OSError(
                    errno.ESTALE,
                    "retired recovery cleanup WAL is incomplete",
                )
        elif recovery_authorization is not None or _recovery_cleanup_wal_residue_exists(
            store,
            binding.transaction_id,
        ):
            raise OSError(errno.ESTALE, "recovery cleanup authorization changed")
        live_component = binding.root_relative.rsplit("/", 1)[-1]
        quarantine_component = binding.quarantine_relative.rsplit("/", 1)[-1]
        cleanup_intent = _load_transaction_cleanup_record(
            store,
            binding,
            complete=False,
        )
        cleanup_complete = _load_transaction_cleanup_record(
            store,
            binding,
            complete=True,
        )
        if (
            cleanup_intent is not None
            and cleanup_complete is not None
            and cleanup_intent.delete_component != cleanup_complete.delete_component
        ):
            raise _error(
                "ownership.cleanup_incomplete",
                "Persistent transaction cleanup evidence conflicts.",
                recovery=(binding.quarantine_relative,),
            )
        delete_component = (
            cleanup_intent.delete_component
            if cleanup_intent is not None
            else (
                cleanup_complete.delete_component
                if cleanup_complete is not None
                else None
            )
        )
        delete_reference = (
            _persistent_delete_reference(binding, delete_component)
            if delete_component is not None
            else None
        )
        if store.windows:
            live = _open_windows_transaction_location(
                store,
                live_component,
                expected_identity=binding.transaction_identity,
                filesystem_guard=owned_root._filesystem_guard,
            )
            quarantine = _open_windows_transaction_location(
                store,
                quarantine_component,
                expected_identity=binding.transaction_identity,
                filesystem_guard=owned_root._filesystem_guard,
            )
            if delete_component is not None:
                deleting = _open_windows_transaction_location(
                    store,
                    delete_component,
                    expected_identity=binding.transaction_identity,
                    filesystem_guard=owned_root._filesystem_guard,
                )
            live_exists = bool(live)
            quarantine_exists = bool(quarantine)
            deleting_exists = bool(deleting)
        else:
            live = _open_posix_transaction_location(
                store,
                live_component,
                expected_identity=binding.transaction_identity,
                filesystem_guard=owned_root._filesystem_guard,
            )
            quarantine = _open_posix_transaction_location(
                store,
                quarantine_component,
                expected_identity=binding.transaction_identity,
                filesystem_guard=owned_root._filesystem_guard,
            )
            if delete_component is not None:
                deleting = _open_posix_transaction_location(
                    store,
                    delete_component,
                    expected_identity=binding.transaction_identity,
                    filesystem_guard=owned_root._filesystem_guard,
                )
            live_exists = live >= 0
            quarantine_exists = quarantine >= 0
            deleting_exists = deleting >= 0
        if sum((live_exists, quarantine_exists, deleting_exists)) > 1:
            raise _error(
                "ownership.cleanup_incomplete",
                "Persistent transaction cleanup is ambiguous.",
                recovery=(binding.quarantine_relative,),
            )
        final_binding = load_persistent_transaction_binding(
            owned_root, transaction_id=binding.transaction_id
        )
        if not final_binding.is_ok or final_binding.unwrap() != binding:
            raise _error(
                "ownership.cleanup_incomplete",
                "Persistent transaction cleanup authority changed.",
                recovery=(binding.quarantine_relative,),
            )
        if not _transaction_store_namespace_is_valid(
            store
        ) or not owned_root._validate_control_descriptor(store.control):
            raise _error(
                "ownership.cleanup_incomplete",
                "Persistent transaction cleanup containment changed.",
            )
        if live_exists:
            if cleanup_intent is not None or cleanup_complete is not None:
                raise _error(
                    "ownership.cleanup_incomplete",
                    "Persistent transaction cleanup evidence conflicts with a live root.",
                    recovery=(binding.quarantine_relative,),
                )
            if perform_maintenance:
                _finish_transaction_creation_intent(store, binding)
            reference = _paths.validate_reference(
                binding.root_relative,
                role="persistent-transaction-root",
                limits=LIMIT_POLICY,
            ).unwrap()
            claim = TransactionPathClaim(
                binding.transaction_id,
                reference,
                binding.plugins_identity,
                binding.transaction_identity,
                persistent_binding=binding,
                _token=_CAPABILITY_TOKEN,
            )
            return Result.success(
                ReboundTransaction(
                    location=TransactionLocation.LIVE,
                    binding=binding,
                    claim=claim,
                    ticket=None,
                    _token=_REBOUND_TRANSACTION_TOKEN,
                )
            )
        if quarantine_exists:
            if cleanup_complete is not None:
                raise _error(
                    "ownership.cleanup_incomplete",
                    "Persistent transaction cleanup completion conflicts.",
                    recovery=(binding.quarantine_relative,),
                )
            root = owned_root._duplicate_root_descriptor()
            namespace = owned_root._duplicate_namespace_capability()
            if (
                _native_identity(root) != binding.plugins_identity
                or not namespace._validate_namespace_binding()
            ):
                raise _error(
                    "ownership.cleanup_incomplete",
                    "Persistent transaction cleanup containment changed.",
                )
            ticket = QuarantineTicket(
                root,
                namespace,
                binding.quarantine_relative,
                binding.transaction_identity,
                binding.plugins_identity,
                binding=binding,
                _token=_CAPABILITY_TOKEN,
            )
            root = 0 if store.windows else -1
            namespace = None
            return Result.success(
                ReboundTransaction(
                    location=TransactionLocation.QUARANTINED,
                    binding=binding,
                    claim=None,
                    ticket=ticket,
                    _token=_REBOUND_TRANSACTION_TOKEN,
                )
            )
        if deleting_exists:
            if (
                cleanup_intent is None
                or cleanup_complete is not None
                or delete_reference is None
            ):
                raise _error(
                    "ownership.cleanup_incomplete",
                    "Persistent transaction deletion is not authorized.",
                    recovery=(binding.quarantine_relative,),
                )
            root = owned_root._duplicate_root_descriptor()
            namespace = owned_root._duplicate_namespace_capability()
            if (
                _native_identity(root) != binding.plugins_identity
                or not namespace._validate_namespace_binding()
            ):
                raise _error(
                    "ownership.cleanup_incomplete",
                    "Persistent transaction cleanup containment changed.",
                )
            ticket = QuarantineTicket(
                root,
                namespace,
                delete_reference,
                binding.transaction_identity,
                binding.plugins_identity,
                binding=binding,
                _token=_CAPABILITY_TOKEN,
            )
            root = 0 if store.windows else -1
            namespace = None
            return Result.success(
                ReboundTransaction(
                    location=TransactionLocation.QUARANTINED,
                    binding=binding,
                    claim=None,
                    ticket=ticket,
                    _token=_REBOUND_TRANSACTION_TOKEN,
                )
            )
        if (
            _transaction_name_exists(
                store.store,
                live_component,
                directory=True,
                windows=store.windows,
            )
            or _transaction_name_exists(
                store.store,
                quarantine_component,
                directory=True,
                windows=store.windows,
            )
            or (
                delete_component is not None
                and _transaction_name_exists(
                    store.store,
                    delete_component,
                    directory=True,
                    windows=store.windows,
                )
            )
        ):
            raise _error(
                "ownership.cleanup_incomplete",
                "Persistent transaction cleanup remains ambiguous.",
                recovery=(binding.quarantine_relative,),
            )
        if cleanup_complete is None:
            raise _error(
                "ownership.cleanup_incomplete",
                "Persistent transaction cleanup completion is missing.",
                recovery=(binding.quarantine_relative,),
            )
        if perform_maintenance and cleanup_intent is not None:
            _remove_exact_transaction_record(
                store,
                cleanup_intent.component,
                cleanup_intent.identity,
                cleanup_intent.raw,
            )
        final_complete = _load_transaction_cleanup_record(
            store,
            binding,
            complete=True,
        )
        if (
            final_complete is None
            or final_complete.delete_component != cleanup_complete.delete_component
            or _transaction_name_exists(
                store.store,
                live_component,
                directory=True,
                windows=store.windows,
            )
            or _transaction_name_exists(
                store.store,
                quarantine_component,
                directory=True,
                windows=store.windows,
            )
            or _transaction_name_exists(
                store.store,
                cleanup_complete.delete_component,
                directory=True,
                windows=store.windows,
            )
        ):
            raise _error(
                "ownership.cleanup_incomplete",
                "Persistent transaction cleanup completion changed.",
                recovery=(binding.quarantine_relative,),
            )
        return Result.success(
            ReboundTransaction(
                location=TransactionLocation.REMOVED,
                binding=binding,
                claim=None,
                ticket=None,
                _token=_REBOUND_TRANSACTION_TOKEN,
            )
        )
    except (ForgeError, OSError, ValueError):
        return Result.failure(
            _error(
                "ownership.cleanup_incomplete",
                "Persistent transaction cleanup cannot be rebound.",
                recovery=(binding.quarantine_relative,),
            )
        )
    finally:
        if namespace is not None:
            namespace.close()
        if os.name == "nt":
            for descriptor in (deleting, quarantine, live, root):
                if descriptor:
                    _paths._windows_close(descriptor)
        else:
            for descriptor in (deleting, quarantine, live, root):
                if descriptor >= 0:
                    os.close(descriptor)
        if store is not None:
            store.close()


def rebind_persistent_transaction(
    owned_root: OwnedRoot, *, binding: PersistentTransactionBinding
) -> Result[ReboundTransaction]:
    """Rebind one transaction and finish safe generic maintenance records."""

    return _rebind_persistent_transaction(
        owned_root,
        binding=binding,
        perform_maintenance=True,
        recovery_authorization=None,
    )


def rebind_persistent_transaction_for_recovery(
    owned_root: OwnedRoot,
    *,
    binding: PersistentTransactionBinding,
    _recovery_authorization: RecoveryCleanupAuthorization | None = None,
) -> Result[ReboundTransaction]:
    """Effect-free rebind for locked recovery validation and exact WAL cleanup."""

    return _rebind_persistent_transaction(
        owned_root,
        binding=binding,
        perform_maintenance=False,
        recovery_authorization=_recovery_authorization,
    )


def _transaction_path_claim_matches_binding(
    claim: object,
    binding: PersistentTransactionBinding,
) -> bool:
    if type(claim) is not TransactionPathClaim:
        return False
    selected = claim
    try:
        with selected._lock:
            return (
                not selected._consumed
                and selected.transaction_id == binding.transaction_id
                and selected.relative.value == binding.root_relative
                and selected.root_identity == binding.plugins_identity
                and selected.identity == binding.transaction_identity
                and selected._persistent_binding == binding
            )
    except (AttributeError, ForgeError, TypeError, ValueError):
        return False


def _transaction_journal_source(
    transaction: object,
) -> tuple[PersistentTransactionBinding, TransactionLocation, str]:
    if type(transaction) is PersistentTransactionRoot:
        created = transaction
        if _persistent_binding_invariants(
            created.binding
        ) and _transaction_path_claim_matches_binding(
            created.claim,
            created.binding,
        ):
            return (
                created.binding,
                TransactionLocation.LIVE,
                created.binding.root_relative,
            )
    elif type(transaction) is ReboundTransaction:
        rebound = transaction
        binding = rebound.binding
        if not _persistent_binding_invariants(binding):
            raise _error(
                "ownership.unowned",
                "Persistent transaction journal authority is invalid.",
            )
        if (
            rebound.location is TransactionLocation.LIVE
            and rebound.ticket is None
            and _transaction_path_claim_matches_binding(rebound.claim, binding)
        ):
            return binding, TransactionLocation.LIVE, binding.root_relative
        ticket = rebound.ticket
        if (
            rebound.location is TransactionLocation.QUARANTINED
            and rebound.claim is None
            and type(ticket) is QuarantineTicket
            and _persistent_cleanup_reference_is_valid(
                binding,
                ticket.recovery_reference,
            )
            and ticket._identity == binding.transaction_identity
            and ticket._root_identity == binding.plugins_identity
            and ticket._binding == binding
        ):
            return (
                binding,
                TransactionLocation.QUARANTINED,
                ticket.recovery_reference,
            )
    raise _error(
        "ownership.unowned",
        "Persistent transaction journal authority is invalid.",
    )


def open_quarantined_recovery_journal_access(
    owned_root: OwnedRoot,
    transaction: ReboundTransaction,
) -> Result[QuarantinedRecoveryJournalAccess]:
    """Retain one-shot write authority for an exactly quarantined journal."""

    store: _TransactionStore | None = None
    namespace: _paths._NamespaceCapability | None = None
    descriptor = 0 if os.name == "nt" else -1
    ticket_root = 0 if os.name == "nt" else -1
    ticket_namespace: _paths._NamespaceCapability | None = None
    access: QuarantinedRecoveryJournalAccess | None = None
    binding: PersistentTransactionBinding | None = None
    try:
        if (
            not isinstance(owned_root, OwnedRoot)
            or type(transaction) is not ReboundTransaction
        ):
            raise _error(
                "ownership.unowned",
                "Quarantined recovery journal authority is invalid.",
            )
        rebound = transaction
        binding = rebound.binding
        ticket = rebound.ticket
        if (
            rebound.location is not TransactionLocation.QUARANTINED
            or rebound.claim is not None
            or type(ticket) is not QuarantineTicket
            or not _quarantined_recovery_ticket_is_live(
                ticket,
                binding,
                binding.quarantine_relative,
            )
        ):
            raise _error(
                "ownership.unowned",
                "Quarantined recovery journal authority is invalid.",
                recovery=(binding.quarantine_relative,),
            )
        store = _open_transaction_store(owned_root, create=False)
        namespace = owned_root._duplicate_namespace_capability()
        component = binding.quarantine_relative.rsplit("/", 1)[-1]
        if store.windows:
            descriptor = _open_windows_transaction_location(
                store,
                component,
                expected_identity=binding.transaction_identity,
                filesystem_guard=owned_root._filesystem_guard,
                write=True,
            )
            if not descriptor:
                raise _transaction_journal_identity_error(binding)
        else:
            descriptor = _open_posix_transaction_location(
                store,
                component,
                expected_identity=binding.transaction_identity,
                filesystem_guard=owned_root._filesystem_guard,
            )
            if descriptor < 0:
                raise _transaction_journal_identity_error(binding)
        if (
            not _quarantined_recovery_cleanup_is_absent(store, binding)
            or not _transaction_journal_location_is_valid(
                store,
                descriptor,
                binding,
                binding.quarantine_relative,
                owned_root._filesystem_guard,
            )
            or not _quarantined_recovery_ticket_is_live(
                ticket,
                binding,
                binding.quarantine_relative,
            )
        ):
            raise _transaction_journal_identity_error(binding)
        ticket_root, ticket_namespace = ticket._take_authority()
        if (
            not _quarantined_recovery_authority_is_valid(
                ticket_root,
                ticket_namespace,
                binding,
            )
            or not _quarantined_recovery_cleanup_is_absent(store, binding)
            or not _transaction_journal_location_is_valid(
                store,
                descriptor,
                binding,
                binding.quarantine_relative,
                owned_root._filesystem_guard,
            )
        ):
            raise _transaction_journal_identity_error(binding)
        if ticket_namespace is None:
            raise _transaction_journal_identity_error(binding)
        access = QuarantinedRecoveryJournalAccess(
            binding=binding,
            journal_relative=binding.quarantine_relative,
            descriptor=descriptor,
            store=store,
            namespace=namespace,
            filesystem_guard=owned_root._filesystem_guard,
            origin=owned_root._origin,
            ticket_root=ticket_root,
            ticket_namespace=ticket_namespace,
            _token=_QUARANTINED_RECOVERY_JOURNAL_ACCESS_TOKEN,
        )
        ticket_root = 0 if store.windows else -1
        ticket_namespace = None
        descriptor = 0 if store.windows else -1
        store = None
        namespace = None
        access._require_recovery_journal_access()
        return Result.success(access)
    except (AttributeError, ForgeError, OSError, TypeError, ValueError):
        if access is not None:
            access.close()
        recovery = (
            (binding.quarantine_relative,)
            if binding is not None and _persistent_binding_invariants(binding)
            else ()
        )
        return Result.failure(
            _error(
                "ownership.unowned",
                "Quarantined recovery journal authority cannot be retained.",
                recovery=recovery,
            )
        )
    finally:
        if ticket_namespace is not None:
            ticket_namespace.close()
        if os.name == "nt" and ticket_root:
            _close_native(ticket_root)
        elif os.name != "nt" and ticket_root >= 0:
            _close_native(ticket_root)
        if namespace is not None:
            namespace.close()
        if os.name == "nt" and descriptor:
            _close_native(descriptor)
        elif os.name != "nt" and descriptor >= 0:
            _close_native(descriptor)
        if store is not None:
            store.close()


def open_transaction_journal_access(
    owned_root: OwnedRoot,
    transaction: PersistentTransactionRoot | ReboundTransaction,
) -> Result[TransactionJournalAccess]:
    """Retain revalidated access to one exact persistent transaction journal."""

    store: _TransactionStore | None = None
    namespace: _paths._NamespaceCapability | None = None
    descriptor = 0 if os.name == "nt" else -1
    access: TransactionJournalAccess | None = None
    binding: PersistentTransactionBinding | None = None
    try:
        if not isinstance(owned_root, OwnedRoot):
            raise _error(
                "ownership.unowned",
                "Persistent transaction journal authority is invalid.",
            )
        binding, location, relative = _transaction_journal_source(transaction)
        store = _open_transaction_store(owned_root, create=False)
        namespace = owned_root._duplicate_namespace_capability()
        component = relative.rsplit("/", 1)[-1]
        if store.windows:
            descriptor = _open_windows_transaction_location(
                store,
                component,
                expected_identity=binding.transaction_identity,
                filesystem_guard=owned_root._filesystem_guard,
                write=location is TransactionLocation.LIVE,
            )
            if not descriptor:
                raise _transaction_journal_identity_error(binding)
        else:
            descriptor = _open_posix_transaction_location(
                store,
                component,
                expected_identity=binding.transaction_identity,
                filesystem_guard=owned_root._filesystem_guard,
            )
            if descriptor < 0:
                raise _transaction_journal_identity_error(binding)
        access = TransactionJournalAccess(
            binding=binding,
            location=location,
            journal_relative=relative,
            writable=location is TransactionLocation.LIVE,
            descriptor=descriptor,
            store=store,
            namespace=namespace,
            filesystem_guard=owned_root._filesystem_guard,
            _token=_TRANSACTION_JOURNAL_ACCESS_TOKEN,
        )
        descriptor = 0 if store.windows else -1
        store = None
        namespace = None
        access._require_journal_access(write=False)
        return Result.success(access)
    except (AttributeError, ForgeError, OSError, TypeError, ValueError):
        if access is not None:
            access.close()
        recovery = (
            (binding.quarantine_relative,)
            if binding is not None and _persistent_binding_invariants(binding)
            else ()
        )
        return Result.failure(
            _error(
                "ownership.unowned",
                "Persistent transaction journal authority cannot be retained.",
                recovery=recovery,
            )
        )
    finally:
        if namespace is not None:
            namespace.close()
        if os.name == "nt" and descriptor:
            _close_native(descriptor)
        elif os.name != "nt" and descriptor >= 0:
            _close_native(descriptor)
        if store is not None:
            store.close()


def open_pending_transaction_journal_access(
    owned_root: OwnedRoot,
    observation: PendingTransactionObservation,
) -> Result[TransactionJournalAccess]:
    """Open one exact observed journal without retaining write authority."""

    store: _TransactionStore | None = None
    namespace: _paths._NamespaceCapability | None = None
    descriptor = 0 if os.name == "nt" else -1
    access: TransactionJournalAccess | None = None
    binding: PersistentTransactionBinding | None = None
    try:
        if (
            not isinstance(owned_root, OwnedRoot)
            or type(observation) is not PendingTransactionObservation
            or observation._seal is not _PENDING_TRANSACTION_OBSERVATION_TOKEN
        ):
            raise _error(
                "ownership.unowned",
                "Pending transaction journal authority is invalid.",
            )
        discovered = discover_pending_transactions(owned_root)
        if not discovered.is_ok or observation not in discovered.unwrap():
            raise _error(
                "ownership.unowned",
                "Pending transaction journal authority changed.",
            )
        binding = observation.binding
        store = _open_transaction_store(owned_root, create=False)
        namespace = owned_root._duplicate_namespace_capability()
        component = observation.journal_relative.rsplit("/", 1)[-1]
        if store.windows:
            descriptor = _open_windows_transaction_location(
                store,
                component,
                expected_identity=binding.transaction_identity,
                filesystem_guard=owned_root._filesystem_guard,
                write=False,
            )
            if not descriptor:
                raise _transaction_journal_identity_error(binding)
        else:
            descriptor = _open_posix_transaction_location(
                store,
                component,
                expected_identity=binding.transaction_identity,
                filesystem_guard=owned_root._filesystem_guard,
            )
            if descriptor < 0:
                raise _transaction_journal_identity_error(binding)
        access = TransactionJournalAccess(
            binding=binding,
            location=observation.location,
            journal_relative=observation.journal_relative,
            writable=False,
            descriptor=descriptor,
            store=store,
            namespace=namespace,
            filesystem_guard=owned_root._filesystem_guard,
            _token=_TRANSACTION_JOURNAL_ACCESS_TOKEN,
        )
        descriptor = 0 if store.windows else -1
        store = None
        namespace = None
        access._require_journal_access(write=False)
        return Result.success(access)
    except (AttributeError, ForgeError, OSError, TypeError, ValueError):
        if access is not None:
            access.close()
        recovery = (
            (binding.quarantine_relative,)
            if binding is not None and _persistent_binding_invariants(binding)
            else ()
        )
        return Result.failure(
            _error(
                "ownership.unowned",
                "Pending transaction journal authority cannot be retained.",
                recovery=recovery,
            )
        )
    finally:
        if namespace is not None:
            namespace.close()
        if os.name == "nt" and descriptor:
            _close_native(descriptor)
        elif os.name != "nt" and descriptor >= 0:
            _close_native(descriptor)
        if store is not None:
            store.close()


def _create_windows_transaction_path(
    owned_root: OwnedRoot,
    relative: SafeRelativePath,
    reference: _SafeReferenceSnapshot,
    transaction_id: str,
) -> Result[TransactionPathClaim]:
    root = parent = leaf = 0
    created_identity: tuple[int, int] | None = None
    try:
        root = owned_root._duplicate_root_descriptor()
        if not owned_root._validate_live_descriptor(root):
            raise _error("ownership.unowned", "The transaction root is not trusted.")
        root_identity = _native_identity(root)
        parent = _windows_open_parent(root, reference.components[:-1])
        try:
            leaf = _paths._windows_create_private_directory(
                parent, reference.components[-1]
            )
        except FileExistsError:
            return Result.failure(
                _error("ownership.unowned", "The transaction path already exists.")
            )
        status = _paths._windows_handle_status(leaf)
        created_identity = status.identity
        if status.identity[0] != root_identity[
            0
        ] or not _paths._windows_private_directory(leaf, exact=True):
            raise _error(
                "ownership.unowned", "The transaction path is not privately owned."
            )
        return Result.success(
            TransactionPathClaim(
                transaction_id,
                relative,
                root_identity,
                status.identity,
                _token=_CAPABILITY_TOKEN,
            )
        )
    except (ForgeError, OSError):
        if leaf and parent and created_identity is not None:
            _rollback_windows_transaction(
                parent,
                reference.components[-1],
                leaf,
                created_identity,
            )
        return Result.failure(
            _error(
                "ownership.unowned", "The transaction path cannot be created safely."
            )
        )
    finally:
        for handle in (leaf, parent, root):
            if handle:
                _paths._windows_close(handle)


def create_transaction_path(
    owned_root: OwnedRoot,
    relative: SafeRelativePath,
    *,
    transaction_id: str,
) -> Result[TransactionPathClaim]:
    """Exclusively create one transaction directory and mint its identity claim."""

    reference = _snapshot_safe_reference(relative)
    if (
        not isinstance(owned_root, OwnedRoot)
        or reference is None
        or _TRANSACTION.fullmatch(transaction_id) is None
    ):
        return cast(
            Result[TransactionPathClaim],
            _failure("ownership.unowned", "Transaction ownership is invalid."),
        )
    if os.name == "nt":
        return _create_windows_transaction_path(
            owned_root,
            relative,
            reference,
            transaction_id,
        )
    root = parent = leaf = -1
    created = False
    created_identity: tuple[int, int] | None = None
    try:
        root = owned_root._duplicate_root_descriptor()
        if not owned_root._validate_live_descriptor(root):
            raise _error("ownership.unowned", "The transaction root is not trusted.")
        root_identity = _identity(root)
        parent = _open_parent(root, reference.components[:-1])
        os.mkdir(reference.components[-1], 0o700, dir_fd=parent)
        created = True
        leaf = os.open(reference.components[-1], _directory_flags(), dir_fd=parent)
        status = os.fstat(leaf)
        created_identity = _identity(leaf)
        if not stat.S_ISDIR(status.st_mode) or stat.S_IMODE(status.st_mode) != 0o700:
            raise OSError(errno.EPERM, "created directory is not private")
        claim = TransactionPathClaim(
            transaction_id,
            relative,
            root_identity,
            created_identity,
            _token=_CAPABILITY_TOKEN,
        )
        return Result.success(claim)
    except FileExistsError:
        return Result.failure(
            _error("ownership.unowned", "The transaction path already exists.")
        )
    except (ForgeError, OSError):
        if created and parent >= 0 and leaf >= 0 and created_identity is not None:
            _rollback_posix_transaction(
                parent,
                reference.components[-1],
                leaf,
                created_identity,
            )
        return Result.failure(
            _error(
                "ownership.unowned", "The transaction path cannot be created safely."
            )
        )
    finally:
        for descriptor in (leaf, parent, root):
            if descriptor >= 0:
                os.close(descriptor)


def prove_transaction_owned(
    path: PathProof, *, claim: TransactionPathClaim | object
) -> Result[OwnershipProof]:
    """Mint deletion authority only from the exclusive-creation identity."""

    if not isinstance(path, PathProof) or not isinstance(claim, TransactionPathClaim):
        return Result.failure(
            _error("ownership.unowned", "No transaction ownership claim exists.")
        )
    if claim._consumed:
        return Result.failure(
            _error("ownership.unowned", "The transaction claim was already used.")
        )
    path_reference = _snapshot_safe_reference(path.relative)
    claim_reference = _snapshot_safe_reference(claim.relative)
    if path_reference is None or claim_reference is None:
        return Result.failure(
            _error(
                "ownership.identity_mismatch",
                "The transaction path identity does not match.",
            )
        )
    root = -1
    namespace: _paths._NamespaceCapability | None = None
    try:
        namespace = path._duplicate_namespace_capability()
        root = path._duplicate_root_descriptor()
    except (ForgeError, OSError):
        if namespace is not None:
            namespace.close()
        return Result.failure(
            _error(
                "ownership.identity_mismatch", "The transaction path identity changed."
            )
        )
    try:
        if (
            namespace is None
            or not namespace._validate_namespace_binding()
            or not path.leaf_exists
            or path.leaf_identity != claim.identity
            or path.owned_ancestor_identity != claim.root_identity
            or path_reference != claim_reference
            or _native_identity(root) != claim.root_identity
        ):
            return Result.failure(
                _error(
                    "ownership.identity_mismatch",
                    "The transaction path identity does not match.",
                )
            )
        if not claim._consume():
            return Result.failure(
                _error("ownership.unowned", "The transaction claim was already used.")
            )
        proof = OwnershipProof(
            root,
            namespace,
            path.relative,
            claim.identity,
            _TransactionObservation(
                path,
                claim.transaction_id,
                claim._persistent_binding,
            ),
            _token=_CAPABILITY_TOKEN,
        )
        root = -1
        namespace = None
        return Result.success(proof)
    except (ForgeError, OSError):
        return Result.failure(
            _error(
                "ownership.identity_mismatch", "The transaction path identity changed."
            )
        )
    finally:
        if root >= 0:
            _close_native(root)
        if namespace is not None:
            namespace.close()


def _exact_mapping(
    value: object, expected: frozenset[str], *, field: str
) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != expected:
        raise ValueError(field)
    return cast(Mapping[str, object], value)


def _string(
    value: object, *, field: str, pattern: re.Pattern[str] | None = None
) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > 240
        or (pattern is not None and pattern.fullmatch(value) is None)
    ):
        raise ValueError(field)
    return value


def _digest(value: object, *, field: str) -> str:
    return _string(value, field=field, pattern=_DIGEST)


def _receipt_identity(value: object) -> InstallIdentity:
    record = _exact_mapping(
        value,
        frozenset(
            {
                "marketplace_id",
                "plugin_id",
                "base_version",
                "install_version",
                "base_payload_digest",
                "rendered_payload_digest",
                "policy_digest",
                "transformation_profile",
                "contract_versions",
            }
        ),
        field="identity",
    )
    versions = record["contract_versions"]
    if (
        not isinstance(versions, tuple)
        or not versions
        or any(not isinstance(item, str) or not item for item in versions)
        or len(set(versions)) != len(versions)
    ):
        raise ValueError("identity.contract_versions")
    identity = InstallIdentity(
        marketplace_id=_string(
            record["marketplace_id"],
            field="identity.marketplace_id",
            pattern=_IDENTIFIER,
        ),
        plugin_id=_string(
            record["plugin_id"], field="identity.plugin_id", pattern=_IDENTIFIER
        ),
        base_version=_string(
            record["base_version"],
            field="identity.base_version",
            pattern=_RELEASE_VERSION,
        ),
        install_version=_string(
            record["install_version"], field="identity.install_version"
        ),
        base_payload_digest=_digest(
            record["base_payload_digest"], field="identity.base_payload_digest"
        ),
        rendered_payload_digest=_digest(
            record["rendered_payload_digest"], field="identity.rendered_payload_digest"
        ),
        policy_digest=_digest(record["policy_digest"], field="identity.policy_digest"),
        transformation_profile=_string(
            record["transformation_profile"], field="identity.transformation_profile"
        ),
        contract_versions=cast(tuple[str, ...], versions),
    )
    if identity.plugin_id != _PLUGIN_ID:
        raise ValueError("identity.plugin_id")
    return identity


@dataclass(frozen=True, slots=True)
class _GenerationRecord:
    relative_path: str
    manifest_digest: str


@dataclass(frozen=True, slots=True)
class _ConfigRecord:
    before_digest: str
    after_digest: str


@dataclass(frozen=True, slots=True)
class _DecodedReceipt:
    record: Mapping[str, object]
    identity: InstallIdentity
    source: _GenerationRecord
    cache: _GenerationRecord
    config: _ConfigRecord
    effective_marketplace_id: str


def _generation(value: object, *, field: str) -> _GenerationRecord:
    record = _exact_mapping(
        value,
        frozenset({"relative_path", "manifest_digest"}),
        field=field,
    )
    return _GenerationRecord(
        relative_path=_string(record["relative_path"], field=f"{field}.relative_path"),
        manifest_digest=_digest(
            record["manifest_digest"], field=f"{field}.manifest_digest"
        ),
    )


def _validate_transaction(value: object) -> None:
    record = _exact_mapping(value, frozenset({"id", "lineage"}), field="transaction")
    transaction_id = _string(record["id"], field="transaction.id", pattern=_TRANSACTION)
    lineage = record["lineage"]
    if (
        not isinstance(lineage, tuple)
        or not lineage
        or any(
            not isinstance(item, str) or _TRANSACTION.fullmatch(item) is None
            for item in lineage
        )
        or len(set(lineage)) != len(lineage)
        or lineage[-1] != transaction_id
    ):
        raise ValueError("transaction.lineage")


def _validate_config(value: object) -> _ConfigRecord:
    record = _exact_mapping(
        value,
        frozenset({"path_id", "before_digest", "after_digest"}),
        field="config",
    )
    if record["path_id"] != "codex-config":
        raise ValueError("config.path_id")
    return _ConfigRecord(
        _digest(record["before_digest"], field="config.before_digest"),
        _digest(record["after_digest"], field="config.after_digest"),
    )


def _validate_tools(value: object) -> None:
    record = _exact_mapping(
        value,
        frozenset(
            {
                "installer_version",
                "python_version",
                "codex_version",
                "platform",
                "verifier_version",
            }
        ),
        field="tools",
    )
    if record["installer_version"] != _WRITER_VERSION:
        raise _error(
            "ownership.receipt_unsupported", "The receipt writer is unsupported."
        )
    for field in ("python_version", "codex_version", "verifier_version"):
        _string(record[field], field=f"tools.{field}", pattern=_RELEASE_VERSION)
    if record["platform"] not in {"linux", "macos", "windows"}:
        raise ValueError("tools.platform")


def _decode_committed_receipt(raw: bytes) -> _DecodedReceipt:
    record = decode_persistent_record(raw, reader_version=_WRITER_VERSION)
    expected = frozenset(
        {
            "record_kind",
            "schema_version",
            "schema_digest",
            "writer_version",
            "minimum_reader_version",
            "record_digest",
            "state_machine_version",
            "policy_version",
            "transformation_version",
            "effective_marketplace_id",
            "identity",
            "transaction",
            "source",
            "cache",
            "config",
            "tools",
            "created_at",
        }
    )
    _exact_mapping(record, expected, field="receipt")
    if record["schema_digest"] != RECEIPT_SCHEMA_DIGEST:
        raise _error(
            "ownership.receipt_unsupported", "The receipt schema is unsupported."
        )
    supported = {
        "record_kind": "committed",
        "schema_version": _RECEIPT_SCHEMA_VERSION,
        "writer_version": _WRITER_VERSION,
        "minimum_reader_version": _WRITER_VERSION,
        "state_machine_version": _STATE_MACHINE_VERSION,
        "policy_version": _POLICY_VERSION,
        "transformation_version": _TRANSFORMATION_VERSION,
    }
    for field, expected_value in supported.items():
        if record[field] != expected_value:
            raise _error(
                "ownership.receipt_unsupported",
                "The receipt authority version is unsupported.",
            )
    effective = _string(
        record["effective_marketplace_id"],
        field="effective_marketplace_id",
        pattern=_IDENTIFIER,
    )
    identity = _receipt_identity(record["identity"])
    if identity.transformation_profile != _TRANSFORMATION_VERSION:
        raise _error(
            "ownership.receipt_unsupported",
            "The receipt transformation authority is unsupported.",
        )
    source = _generation(record["source"], field="source")
    cache = _generation(record["cache"], field="cache")
    expected_source = (
        f"sources/{effective}/{_PLUGIN_ID}/{identity.install_version}/marketplace"
    )
    expected_cache = f"cache/{effective}/{_PLUGIN_ID}/{identity.install_version}"
    if source.relative_path != expected_source or cache.relative_path != expected_cache:
        raise ValueError("generation.relative_path")
    _validate_transaction(record["transaction"])
    config = _validate_config(record["config"])
    _validate_tools(record["tools"])
    created_at = _string(record["created_at"], field="created_at")
    try:
        datetime.strptime(created_at, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise ValueError("created_at") from exc
    return _DecodedReceipt(record, identity, source, cache, config, effective)


def _private_posix_receipt_parent(descriptor: int, *, device: int) -> bool:
    status = os.fstat(descriptor)
    return status.st_dev == device and _paths._private_directory(
        descriptor,
        status,
        exact=True,
    )


def _private_posix_receipt(
    descriptor: int,
    status: os.stat_result,
    *,
    device: int,
    size: int | None = None,
) -> bool:
    try:
        return (
            stat.S_ISREG(status.st_mode)
            and status.st_uid == os.geteuid()
            and stat.S_IMODE(status.st_mode) == 0o600
            and status.st_nlink == 1
            and status.st_dev == device
            and (size is None or status.st_size == size)
            and _paths._posix_security_metadata_supported(descriptor, status)
        )
    except OSError:
        return False


def _private_receipt_descriptor(descriptor: int, *, device: int) -> bool:
    if os.name == "posix":
        return _private_posix_receipt(
            descriptor,
            os.fstat(descriptor),
            device=device,
        )
    receipt_status = _paths._windows_handle_status(descriptor)
    return receipt_status.identity[0] == device and _windows_private_file(descriptor)


def _open_private_directory_chain(
    root: int,
    components: tuple[str, ...],
    *,
    device: int,
    create_missing: bool = True,
) -> int:
    empty = -1
    current = os.dup(root)
    child = empty
    try:
        for component in components:
            created = False
            if create_missing:
                try:
                    os.mkdir(component, 0o700, dir_fd=current)
                    created = True
                except FileExistsError:
                    pass
            try:
                child = os.open(component, _directory_flags(), dir_fd=current)
                if not _private_posix_receipt_parent(child, device=device):
                    raise _error(
                        "ownership.receipt_conflict",
                        "The receipt parent is not privately owned.",
                    )
                if created:
                    os.fsync(child)
                    os.fsync(current)
            except BaseException:
                raise
            previous = current
            current = empty
            _close_native(previous)
            current = child
            child = empty
        return current
    except BaseException:
        try:
            _close_all_native_descriptors(child, current)
        except BaseException:
            pass
        raise


def _receipt_parent_chain_is_private(
    owned_root: OwnedRoot,
    reference: _SafeReferenceSnapshot,
) -> bool:
    control = parent = 0 if os.name == "nt" else -1
    try:
        control = owned_root._duplicate_control_descriptor()
        if not owned_root._validate_control_descriptor(control):
            return False
        if os.name == "nt":
            volume = _paths._windows_handle_status(control).identity[0]
            parent = _windows_open_private_directory_chain(
                control,
                reference.components[1:-1],
                volume=volume,
                create_missing=False,
            )
            return (
                _paths._windows_handle_status(parent).identity[0] == volume
                and _paths._windows_private_directory(parent, exact=True)
                and owned_root._validate_control_binding()
            )
        device = os.fstat(control).st_dev
        parent = _open_private_directory_chain(
            control,
            reference.components[1:-1],
            device=device,
            create_missing=False,
        )
        return (
            _private_posix_receipt_parent(
                parent,
                device=device,
            )
            and owned_root._validate_control_binding()
        )
    except (ForgeError, OSError):
        return False
    finally:
        _close_all_native_descriptors(parent, control)


@dataclass(frozen=True, slots=True)
class _ReceiptBindingState:
    control_live: bool
    parent_bound: bool
    leaf_bound: bool
    leaf_private: bool

    @property
    def valid(self) -> bool:
        return (
            self.control_live
            and self.parent_bound
            and self.leaf_bound
            and self.leaf_private
        )


def _receipt_binding_state(
    owned_root: OwnedRoot,
    reference: _SafeReferenceSnapshot,
    *,
    expected_parent_identity: tuple[int, int],
    retained_leaf: int | None = None,
    expected_leaf_identity: tuple[int, int] | None = None,
) -> _ReceiptBindingState:
    empty = 0 if os.name == "nt" else -1
    control = parent = canonical_leaf = empty
    control_live = parent_bound = leaf_bound = leaf_private = False
    if (retained_leaf is None) == (expected_leaf_identity is None):
        return _ReceiptBindingState(False, False, False, False)
    try:
        control = owned_root._duplicate_control_descriptor()
        if not owned_root._validate_control_descriptor(control):
            return _ReceiptBindingState(False, False, False, False)
        control_live = True
        device = _native_identity(control)[0]
        if device != owned_root.identity[0]:
            return _ReceiptBindingState(True, False, False, False)
        if os.name == "nt":
            parent = _windows_open_private_directory_chain(
                control,
                reference.components[1:-1],
                volume=device,
                create_missing=False,
            )
        else:
            parent = _open_private_directory_chain(
                control,
                reference.components[1:-1],
                device=device,
                create_missing=False,
            )
        parent_bound = _native_identity(parent) == expected_parent_identity
        if not parent_bound:
            return _ReceiptBindingState(True, False, False, False)
        if os.name == "nt":
            canonical_leaf = _windows_open_raw_child(
                parent,
                reference.components[-1],
                directory=False,
                read_data=True,
            )
        else:
            canonical_leaf = os.open(
                reference.components[-1],
                os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
                dir_fd=parent,
            )
        retained_identity = (
            expected_leaf_identity
            if retained_leaf is None
            else _native_identity(retained_leaf)
        )
        leaf_bound = _native_identity(canonical_leaf) == retained_identity
        if leaf_bound:
            leaf_private = _private_receipt_descriptor(
                canonical_leaf,
                device=device,
            )
            if retained_leaf is not None:
                leaf_private = leaf_private and _private_receipt_descriptor(
                    retained_leaf,
                    device=device,
                )
    except (ForgeError, OSError):
        pass
    finally:
        _close_all_native_descriptors(canonical_leaf, parent, control)
    try:
        control_live = control_live and owned_root._validate_control_binding()
    except (ForgeError, OSError):
        control_live = False
    return _ReceiptBindingState(
        control_live,
        parent_bound,
        leaf_bound,
        leaf_private,
    )


def _read_posix_descriptor(descriptor: int, *, limit: int) -> bytes:
    status = os.fstat(descriptor)
    if status.st_size > limit:
        raise _error("ownership.receipt_conflict", "The existing receipt is too large.")
    chunks: list[bytes] = []
    offset = 0
    while offset < status.st_size:
        chunk = os.pread(descriptor, min(64 * 1024, status.st_size - offset), offset)
        if not chunk:
            break
        chunks.append(chunk)
        offset += len(chunk)
    rendered = b"".join(chunks)
    if len(rendered) != status.st_size:
        raise _error("ownership.receipt_conflict", "The existing receipt changed.")
    return rendered


def _receipt_temp_leaf(leaf: str) -> str:
    identity_prefix = leaf[:16]
    return f".receipt-{identity_prefix}-{secrets.token_hex(8)}.tmp"


def _open_matching_posix_receipt(
    parent: int, leaf: str, raw: bytes, *, device: int
) -> int | None:
    descriptor = -1
    try:
        descriptor = os.open(
            leaf,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
            dir_fd=parent,
        )
        before = os.fstat(descriptor)
        if not _private_posix_receipt(descriptor, before, device=device):
            return None
        rendered = _read_posix_descriptor(descriptor, limit=256 * 1024)
        after = os.fstat(descriptor)
        matches = (
            _identity(descriptor) == (before.st_dev, before.st_ino)
            and _paths._posix_status_fingerprint(before)
            == _paths._posix_status_fingerprint(after)
            and _private_posix_receipt(descriptor, after, device=device)
            and rendered == raw
        )
        if not matches:
            return None
        retained = descriptor
        descriptor = -1
        return retained
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _publish_posix_receipt(
    owned_root: OwnedRoot,
    reference: SafeRelativePath,
    raw: bytes,
) -> Result[ReceiptPublication]:
    safe_reference = _snapshot_safe_reference(reference)
    if safe_reference is None:
        return Result.failure(
            _error("ownership.receipt_invalid", "Committed receipt path is invalid.")
        )
    control = parent = descriptor = -1
    temp_leaf = ""
    staged_identity: tuple[int, int] | None = None
    renamed = False
    leaf = safe_reference.components[-1]
    try:
        control = owned_root._duplicate_control_descriptor()
        if not owned_root._validate_control_descriptor(control):
            raise _error("ownership.unowned", "The receipt root is not trusted.")
        device = os.fstat(control).st_dev
        parent = _open_private_directory_chain(
            control, safe_reference.components[1:-1], device=device
        )
        expected_parent_identity = _identity(parent)
        existing = -1
        try:
            matched = _open_matching_posix_receipt(parent, leaf, raw, device=device)
            if matched is not None:
                existing = matched
                binding = _receipt_binding_state(
                    owned_root,
                    safe_reference,
                    expected_parent_identity=expected_parent_identity,
                    retained_leaf=existing,
                )
                if not binding.control_live:
                    raise _error(
                        "ownership.unowned", "The receipt root is not trusted."
                    )
                if not binding.valid:
                    return Result.failure(
                        _error(
                            "ownership.receipt_conflict",
                            "The canonical receipt binding changed.",
                        )
                    )
                return Result.success(ReceiptPublication(reference, False))
            return Result.failure(
                _error(
                    "ownership.receipt_conflict",
                    "Committed receipt bytes already differ.",
                )
            )
        except FileNotFoundError:
            pass
        finally:
            if existing >= 0:
                os.close(existing)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW
        for _attempt in range(8):
            temp_leaf = _receipt_temp_leaf(leaf)
            try:
                descriptor = os.open(temp_leaf, flags, 0o600, dir_fd=parent)
                break
            except FileExistsError:
                continue
        if descriptor < 0:
            raise OSError(errno.EEXIST, "receipt staging names are occupied")
        staged_identity = _identity(descriptor)
        view = memoryview(raw)
        written = 0
        while written < len(view):
            amount = os.write(descriptor, view[written:])
            if amount < 1:
                raise OSError(errno.EIO, "short receipt write")
            written += amount
        os.fsync(descriptor)
        status = os.fstat(descriptor)
        if (
            status.st_dev,
            status.st_ino,
        ) != staged_identity or not _private_posix_receipt(
            descriptor,
            status,
            device=device,
            size=len(raw),
        ):
            raise OSError(errno.ESTALE, "staged receipt identity changed")
        try:
            _exclusive_rename(parent, temp_leaf, leaf)
        except FileExistsError:
            existing = -1
            try:
                matched = _open_matching_posix_receipt(
                    parent,
                    leaf,
                    raw,
                    device=device,
                )
                if matched is not None:
                    existing = matched
                    binding = _receipt_binding_state(
                        owned_root,
                        safe_reference,
                        expected_parent_identity=expected_parent_identity,
                        retained_leaf=existing,
                    )
                    if not binding.control_live:
                        raise _error(
                            "ownership.unowned", "The receipt root is not trusted."
                        )
                    if binding.valid:
                        return Result.success(ReceiptPublication(reference, False))
                return Result.failure(
                    _error(
                        "ownership.receipt_conflict",
                        "Committed receipt bytes already differ.",
                    )
                )
            finally:
                if existing >= 0:
                    os.close(existing)
        renamed = True
        current = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
        published = os.fstat(descriptor)
        if (
            (current.st_dev, current.st_ino) != staged_identity
            or (published.st_dev, published.st_ino) != staged_identity
            or not _private_posix_receipt(
                descriptor,
                published,
                device=device,
                size=len(raw),
            )
        ):
            raise OSError(errno.ESTALE, "published receipt identity changed")
        os.fsync(parent)
        binding = _receipt_binding_state(
            owned_root,
            safe_reference,
            expected_parent_identity=expected_parent_identity,
            retained_leaf=descriptor,
        )
        if not binding.control_live:
            raise _error("ownership.unowned", "The receipt root is not trusted.")
        if not binding.valid:
            return Result.failure(
                _error(
                    "ownership.receipt_conflict",
                    "The canonical receipt binding changed.",
                )
            )
        return Result.success(ReceiptPublication(reference, True))
    except ForgeError as exc:
        if exc.code.startswith("ownership."):
            return Result.failure(exc)
        return Result.failure(
            _error("ownership.unowned", "The receipt root is not trusted.")
        )
    except OSError:
        return Result.failure(
            _error(
                "ownership.receipt_conflict", "The receipt cannot be published safely."
            )
        )
    finally:
        if not renamed and parent >= 0 and staged_identity is not None and temp_leaf:
            try:
                current = os.stat(temp_leaf, dir_fd=parent, follow_symlinks=False)
                if (current.st_dev, current.st_ino) == staged_identity:
                    os.unlink(temp_leaf, dir_fd=parent)
            except OSError:
                pass
        for item in (descriptor, parent, control):
            if item >= 0:
                os.close(item)


def _publish_windows_receipt(
    owned_root: OwnedRoot,
    reference: SafeRelativePath,
    raw: bytes,
) -> Result[ReceiptPublication]:
    safe_reference = _snapshot_safe_reference(reference)
    if safe_reference is None:
        return Result.failure(
            _error("ownership.receipt_invalid", "Committed receipt path is invalid.")
        )
    control = parent = handle = 0
    security_descriptor = 0
    temp_leaf = ""
    renamed = False
    try:
        control = owned_root._duplicate_control_descriptor()
        if not owned_root._validate_control_descriptor(control):
            raise _error("ownership.unowned", "The receipt root is not trusted.")
        volume = _paths._windows_handle_status(control).identity[0]
        parent = _windows_open_private_directory_chain(
            control, safe_reference.components[1:-1], volume=volume
        )
        expected_parent_identity = _native_identity(parent)
        leaf = safe_reference.components[-1]
        existing = 0
        try:
            existing = _windows_open_raw_child(
                parent, leaf, directory=False, read_data=True
            )
        except OSError as exc:
            if not isinstance(exc, FileNotFoundError) and getattr(
                exc, "winerror", None
            ) not in {2, 3}:
                raise
        if existing:
            try:
                status = _paths._windows_handle_status(existing)
                if (
                    status.identity[0] == volume
                    and _windows_private_file(existing)
                    and status.size <= 256 * 1024
                    and _paths._windows_read(existing, limit=256 * 1024) == raw
                ):
                    binding = _receipt_binding_state(
                        owned_root,
                        safe_reference,
                        expected_parent_identity=expected_parent_identity,
                        retained_leaf=existing,
                    )
                    if not binding.control_live:
                        raise _error(
                            "ownership.unowned", "The receipt root is not trusted."
                        )
                    if not binding.valid:
                        return Result.failure(
                            _error(
                                "ownership.receipt_conflict",
                                "The canonical receipt binding changed.",
                            )
                        )
                    return Result.success(ReceiptPublication(reference, False))
                return Result.failure(
                    _error(
                        "ownership.receipt_conflict",
                        "Committed receipt bytes already differ.",
                    )
                )
            finally:
                _paths._windows_close(existing)
        security_descriptor = _paths._windows_private_security_descriptor()
        for _attempt in range(8):
            temp_leaf = _receipt_temp_leaf(leaf)
            try:
                handle = _windows_open_raw_child(
                    parent,
                    temp_leaf,
                    directory=False,
                    write_data=True,
                    delete_access=True,
                    create=True,
                    security_descriptor=security_descriptor,
                )
                break
            except FileExistsError:
                continue
        if not handle:
            raise OSError(errno.EEXIST, "receipt staging names are occupied")
        _windows_write_all(handle, raw)
        _windows_flush(handle)
        status = _paths._windows_handle_status(handle)
        if (
            status.identity[0] != volume
            or status.size != len(raw)
            or not _windows_private_file(handle)
        ):
            raise OSError(errno.ESTALE, "staged receipt identity changed")
        staged_identity = status.identity
        try:
            _windows_rename_handle(handle, parent, leaf)
        except FileExistsError:
            existing = _windows_open_raw_child(
                parent, leaf, directory=False, read_data=True
            )
            try:
                current = _paths._windows_handle_status(existing)
                if (
                    current.identity[0] == volume
                    and _windows_private_file(existing)
                    and current.size <= 256 * 1024
                    and _paths._windows_read(existing, limit=256 * 1024) == raw
                ):
                    binding = _receipt_binding_state(
                        owned_root,
                        safe_reference,
                        expected_parent_identity=expected_parent_identity,
                        retained_leaf=existing,
                    )
                    if not binding.control_live:
                        raise _error(
                            "ownership.unowned", "The receipt root is not trusted."
                        )
                    if not binding.valid:
                        return Result.failure(
                            _error(
                                "ownership.receipt_conflict",
                                "The canonical receipt binding changed.",
                            )
                        )
                    return Result.success(ReceiptPublication(reference, False))
                return Result.failure(
                    _error(
                        "ownership.receipt_conflict",
                        "Committed receipt bytes already differ.",
                    )
                )
            finally:
                _paths._windows_close(existing)
        renamed = True
        _windows_flush(handle)
        current_handle = _windows_open_raw_child(
            parent, leaf, directory=False, read_data=True
        )
        try:
            current = _paths._windows_handle_status(current_handle)
            if (
                current.identity != staged_identity
                or current.identity[0] != volume
                or current.size != len(raw)
                or not _windows_private_file(current_handle)
                or _paths._windows_read(current_handle, limit=256 * 1024) != raw
            ):
                raise OSError(errno.ESTALE, "published receipt identity changed")
        finally:
            _paths._windows_close(current_handle)
        binding = _receipt_binding_state(
            owned_root,
            safe_reference,
            expected_parent_identity=expected_parent_identity,
            retained_leaf=handle,
        )
        if not binding.control_live:
            raise _error("ownership.unowned", "The receipt root is not trusted.")
        if not binding.valid:
            return Result.failure(
                _error(
                    "ownership.receipt_conflict",
                    "The canonical receipt binding changed.",
                )
            )
        return Result.success(ReceiptPublication(reference, True))
    except ForgeError as exc:
        if exc.code.startswith("ownership."):
            return Result.failure(exc)
        return Result.failure(
            _error("ownership.unowned", "The receipt root is not trusted.")
        )
    except OSError:
        return Result.failure(
            _error(
                "ownership.receipt_conflict", "The receipt cannot be published safely."
            )
        )
    finally:
        if handle and not renamed:
            try:
                _windows_delete_handle(handle)
            except OSError:
                pass
        for item in (handle, parent, control):
            if item:
                _paths._windows_close(item)
        if security_descriptor:
            _paths._windows_local_free(security_descriptor)


def publish_committed_receipt(
    owned_root: OwnedRoot, *, raw: bytes
) -> Result[ReceiptPublication]:
    """Publish canonical committed bytes exclusively; identical bytes are a no-op."""

    if not isinstance(owned_root, OwnedRoot) or not isinstance(raw, bytes):
        return Result.failure(
            _error("ownership.receipt_invalid", "Committed receipt input is invalid.")
        )
    try:
        decoded = _decode_committed_receipt(raw)
        reference = committed_receipt_reference(
            decoded.effective_marketplace_id, decoded.identity
        )
        if (
            not owned_root._validate_live_descriptor()
            or not owned_root._validate_control_descriptor()
        ):
            return Result.failure(
                _error("ownership.unowned", "The receipt root is not trusted.")
            )
        if os.name == "nt":
            return _publish_windows_receipt(owned_root, reference, raw)
        return _publish_posix_receipt(owned_root, reference, raw)
    except ForgeError as exc:
        return Result.failure(_receipt_failure_from(exc))
    except (OSError, TypeError, ValueError, KeyError):
        return Result.failure(
            _error("ownership.receipt_invalid", "The ownership receipt is invalid.")
        )


def validate_committed_receipt(
    receipt: OpenedRegularFile,
    *,
    owned_root: OwnedRoot,
    observed: ObservedGenerationIdentity,
) -> Result[OwnershipProof]:
    """Validate receipt bytes and live generation identity before minting authority."""

    if (
        not isinstance(receipt, OpenedRegularFile)
        or not isinstance(owned_root, OwnedRoot)
        or not _observed_generation_invariants(observed)
    ):
        return Result.failure(
            _error(
                "ownership.receipt_invalid",
                "The ownership receipt capability is invalid.",
            )
        )
    receipt_reference = _snapshot_safe_reference(receipt.relative)
    observed_reference = _snapshot_safe_reference(observed.path.relative)
    if receipt_reference is None or observed_reference is None:
        return Result.failure(
            _error(
                "ownership.receipt_invalid",
                "The ownership receipt capability is invalid.",
            )
        )
    descriptor = -1
    root = -1
    namespace: _paths._NamespaceCapability | None = None
    try:
        expected_receipt = _snapshot_safe_reference(
            committed_receipt_reference(
                observed.effective_marketplace_id,
                observed.identity,
            )
        )
        try:
            raw = receipt.read_bytes(limit=256 * 1024)
        except ForgeError as exc:
            if exc.code.startswith("path."):
                binding = (
                    _receipt_binding_state(
                        owned_root,
                        expected_receipt,
                        expected_parent_identity=receipt.parent_identity,
                        expected_leaf_identity=receipt.identity,
                    )
                    if expected_receipt is not None
                    and receipt_reference.value == expected_receipt.value
                    else _ReceiptBindingState(False, False, False, False)
                )
                if not (
                    binding.control_live and binding.parent_bound and binding.leaf_bound
                ):
                    return Result.failure(
                        _error(
                            "ownership.identity_mismatch",
                            "The ownership receipt containment does not match.",
                        )
                    )
                return Result.failure(
                    _error(
                        "ownership.receipt_invalid",
                        "The ownership receipt permissions are invalid.",
                    )
                )
            raise
        decoded = _decode_committed_receipt(raw)
        selected = decoded.source if observed.root_role == "source" else decoded.cache
        if (
            decoded.identity != observed.identity
            or decoded.effective_marketplace_id != observed.effective_marketplace_id
        ):
            return Result.failure(
                _error(
                    "ownership.identity_mismatch",
                    "The ownership receipt identity does not match.",
                )
            )
        if selected.relative_path != observed_reference.value:
            return Result.failure(
                _error(
                    "ownership.identity_mismatch",
                    "The ownership receipt path does not match.",
                )
            )
        if selected.manifest_digest != observed.manifest_digest:
            return Result.failure(
                _error(
                    "ownership.manifest_mismatch",
                    "The observed manifest does not match.",
                )
            )
        if (
            expected_receipt is None
            or receipt_reference.value != expected_receipt.value
            or receipt.root_identity != owned_root.identity
            or observed.path.owned_ancestor_identity != owned_root.identity
            or not owned_root._validate_live_descriptor()
            or not owned_root._validate_control_descriptor()
            or not _receipt_parent_chain_is_private(owned_root, expected_receipt)
        ):
            return Result.failure(
                _error(
                    "ownership.identity_mismatch",
                    "The ownership receipt containment does not match.",
                )
            )
        descriptor = receipt._duplicate_descriptor()
        if not _private_receipt_descriptor(
            descriptor,
            device=owned_root.identity[0],
        ):
            return Result.failure(
                _error(
                    "ownership.receipt_invalid",
                    "The ownership receipt permissions are invalid.",
                )
            )
        leaf = observed.path._duplicate_descriptor()
        try:
            leaf_identity = (
                _identity(leaf)
                if os.name == "posix"
                else _paths._windows_handle_status(leaf).identity
            )
            if observed.path.leaf_identity != leaf_identity:
                return Result.failure(
                    _error(
                        "ownership.identity_mismatch",
                        "The observed path identity changed.",
                    )
                )
        finally:
            if os.name == "posix":
                os.close(leaf)
            else:
                _paths._windows_close(leaf)
        namespace = observed.path._duplicate_namespace_capability()
        root = observed.path._duplicate_root_descriptor()
        if not namespace._validate_namespace_binding():
            return Result.failure(
                _error(
                    "ownership.identity_mismatch",
                    "The ownership receipt containment changed.",
                )
            )
        binding = _receipt_binding_state(
            owned_root,
            expected_receipt,
            expected_parent_identity=receipt.parent_identity,
            retained_leaf=descriptor,
        )
        if not (binding.control_live and binding.parent_bound and binding.leaf_bound):
            return Result.failure(
                _error(
                    "ownership.identity_mismatch",
                    "The ownership receipt containment does not match.",
                )
            )
        if not binding.leaf_private:
            return Result.failure(
                _error(
                    "ownership.receipt_invalid",
                    "The ownership receipt permissions are invalid.",
                )
            )
        proof = OwnershipProof(
            root,
            namespace,
            observed.path.relative,
            observed.path.leaf_identity,
            observed,
            _token=_CAPABILITY_TOKEN,
        )
        root = -1
        namespace = None
        return Result.success(proof)
    except ForgeError as exc:
        return Result.failure(_receipt_failure_from(exc))
    except (OSError, TypeError, ValueError, KeyError):
        return Result.failure(
            _error("ownership.receipt_invalid", "The ownership receipt is invalid.")
        )
    finally:
        if descriptor >= 0:
            if os.name == "posix":
                os.close(descriptor)
            else:
                _paths._windows_close(descriptor)
        if root >= 0:
            _close_native(root)
        if namespace is not None:
            namespace.close()


def validate_active_install_relation(
    receipt: OpenedRegularFile,
    *,
    owned_root: OwnedRoot,
    source: ObservedGenerationIdentity,
    cache: ObservedGenerationIdentity,
) -> Result[ValidatedInstallRelation]:
    """Validate one receipt against both live generations and seal its relation."""

    if (
        not isinstance(receipt, OpenedRegularFile)
        or not isinstance(owned_root, OwnedRoot)
        or not _observed_generation_invariants(source)
        or not _observed_generation_invariants(cache)
    ):
        return Result.failure(
            _error(
                "ownership.receipt_invalid",
                "The ownership receipt relation input is invalid.",
            )
        )
    if (
        source.root_role != "source"
        or cache.root_role != "cache"
        or source.effective_marketplace_id != cache.effective_marketplace_id
        or source.identity != cache.identity
    ):
        return Result.failure(
            _error(
                "ownership.identity_mismatch",
                "The ownership receipt relation does not match.",
            )
        )
    source_proof: OwnershipProof | None = None
    cache_proof: OwnershipProof | None = None
    final_source_proof: OwnershipProof | None = None
    final_cache_proof: OwnershipProof | None = None
    try:
        source_result = validate_committed_receipt(
            receipt,
            owned_root=owned_root,
            observed=source,
        )
        if source_result.error is not None:
            return Result.failure(source_result.error)
        source_proof = source_result.unwrap()
        cache_result = validate_committed_receipt(
            receipt,
            owned_root=owned_root,
            observed=cache,
        )
        if cache_result.error is not None:
            return Result.failure(cache_result.error)
        cache_proof = cache_result.unwrap()
        raw = receipt.read_bytes(limit=256 * 1024)
        decoded = _decode_committed_receipt(raw)
        final_source_result = validate_committed_receipt(
            receipt,
            owned_root=owned_root,
            observed=source,
        )
        if final_source_result.error is not None:
            return Result.failure(final_source_result.error)
        final_source_proof = final_source_result.unwrap()
        final_cache_result = validate_committed_receipt(
            receipt,
            owned_root=owned_root,
            observed=cache,
        )
        if final_cache_result.error is not None:
            return Result.failure(final_cache_result.error)
        final_cache_proof = final_cache_result.unwrap()
        if not (
            _observed_generation_invariants(source)
            and _observed_generation_invariants(cache)
        ):
            return Result.failure(
                _error(
                    "ownership.identity_mismatch",
                    "The ownership receipt relation changed.",
                )
            )
        source_identity = source.path.leaf_identity
        cache_identity = cache.path.leaf_identity
        if source_identity is None or cache_identity is None:
            return Result.failure(
                _error(
                    "ownership.identity_mismatch",
                    "The ownership receipt generation identity does not match.",
                )
            )
        source_generation = decoded.source.relative_path
        active = ActiveInstallRelation(
            effective_marketplace_id=decoded.effective_marketplace_id,
            identity=decoded.identity,
            managed_config_projection=ManagedConfigProjection.v1(
                effective_marketplace_id=decoded.effective_marketplace_id,
                plugin_id=decoded.identity.plugin_id,
                source_generation=source_generation,
            ),
            source_generation=source_generation,
            cache_generation=decoded.cache.relative_path,
            committed_receipt_ref=committed_receipt_reference(
                decoded.effective_marketplace_id,
                decoded.identity,
            ).value,
        )
        relation = ValidatedInstallRelation(
            active=active,
            config_before_snapshot_digest=decoded.config.before_digest,
            config_after_snapshot_digest=decoded.config.after_digest,
            source_manifest_digest=decoded.source.manifest_digest,
            cache_manifest_digest=decoded.cache.manifest_digest,
            source_identity=source_identity,
            cache_identity=cache_identity,
            receipt_identity=receipt.identity,
            source_observation=source,
            cache_observation=cache,
            _token=_RELATION_TOKEN,
        )
        relation._require_valid()
        return Result.success(relation)
    except ForgeError as exc:
        return Result.failure(_receipt_failure_from(exc))
    except (OSError, TypeError, ValueError, KeyError):
        return Result.failure(
            _error(
                "ownership.receipt_invalid",
                "The ownership receipt relation is invalid.",
            )
        )
    finally:
        if source_proof is not None:
            source_proof.close()
        if cache_proof is not None:
            cache_proof.close()
        if final_source_proof is not None:
            final_source_proof.close()
        if final_cache_proof is not None:
            final_cache_proof.close()


def _quarantine_target(
    reference: _SafeReferenceSnapshot, transaction_id: str
) -> tuple[str, str]:
    domain = f"{transaction_id}\0{reference.value}".encode()
    destination = f".zagrosi-quarantine-{hashlib.sha256(domain).hexdigest()[:24]}"
    components = (*reference.components[:-1], destination)
    recovery = "/".join(components)
    if (
        len(components) > LIMIT_POLICY.value("path_components")
        or len(recovery.encode("utf-8")) > LIMIT_POLICY.value("path_bytes")
        or len(destination.encode("utf-8")) > LIMIT_POLICY.value("path_component_bytes")
    ):
        raise _error(
            "ownership.quarantine_conflict",
            "The quarantine recovery reference exceeds trusted limits.",
        )
    return destination, recovery


def _persistent_cleanup_binding(
    proof: OwnershipProof,
    *,
    transaction_id: str,
    recovery: str,
) -> PersistentTransactionBinding | None:
    observed = proof._observed
    if not isinstance(observed, _TransactionObservation):
        return None
    binding = observed.persistent_binding
    if binding is None:
        return None
    if (
        not _persistent_binding_invariants(binding)
        or observed.transaction_id != binding.transaction_id
        or transaction_id != binding.transaction_id
        or proof.relative.value != binding.root_relative
        or proof.identity != binding.transaction_identity
        or proof._root_identity != binding.plugins_identity
        or recovery != binding.quarantine_relative
    ):
        raise _error(
            "ownership.identity_mismatch",
            "Persistent transaction cleanup authority changed.",
        )
    return binding


def _quarantine_windows(
    proof: OwnershipProof,
    reference: _SafeReferenceSnapshot,
    *,
    transaction_id: str,
) -> Result[QuarantineTicket]:
    root = 0
    namespace: _paths._NamespaceCapability | None = None
    try:
        destination, recovery = _quarantine_target(reference, transaction_id)
        binding = _persistent_cleanup_binding(
            proof,
            transaction_id=transaction_id,
            recovery=recovery,
        )
        root, namespace = proof._take_authority()
    except ForgeError as exc:
        return Result.failure(exc)
    parent = source = moved = 0
    renamed = False
    try:
        root_status = _paths._windows_handle_status(root)
        if (
            root_status.identity != proof._root_identity
            or not _paths._windows_private_directory(root, exact=False)
        ):
            raise _error("ownership.identity_mismatch", "The owned root changed.")
        root_identity = root_status.identity
        parent = _windows_open_parent(root, reference.components[:-1])
        try:
            source = _paths._windows_open_child(
                parent,
                reference.components[-1],
                directory=True,
                delete_access=True,
            )
        except (ForgeError, OSError):
            return Result.failure(
                _error(
                    "ownership.identity_mismatch", "The owned path identity changed."
                )
            )
        source_status = _paths._windows_handle_status(source)
        if (
            source_status.identity != proof.identity
            or source_status.identity[0] != root_identity[0]
        ):
            return Result.failure(
                _error(
                    "ownership.identity_mismatch", "The owned path identity changed."
                )
            )
        if not namespace._validate_namespace_binding():
            return Result.failure(
                _error(
                    "ownership.identity_mismatch",
                    "The ownership proof containment changed.",
                )
            )

        def mark_renamed() -> None:
            nonlocal renamed
            renamed = True

        _durable_windows_directory_rename(
            source,
            parent,
            destination,
            proof.identity,
            after_rename=mark_renamed,
        )
        moved = _paths._windows_open_child(
            parent, destination, directory=True, delete_access=True
        )
        if _paths._windows_handle_status(moved).identity != proof.identity:
            raise OSError(errno.ESTALE, "quarantined identity changed")
        if not namespace._validate_namespace_binding():
            raise OSError(errno.ESTALE, "quarantine containment changed")
        ticket_root = root
        ticket_namespace = namespace
        root = 0
        namespace = None
        return Result.success(
            QuarantineTicket(
                ticket_root,
                ticket_namespace,
                recovery,
                proof.identity,
                proof._root_identity,
                binding=binding,
                _token=_CAPABILITY_TOKEN,
            )
        )
    except FileExistsError:
        return Result.failure(
            _error(
                "ownership.quarantine_conflict",
                "The quarantine destination already exists.",
            )
        )
    except (ForgeError, OSError):
        return Result.failure(
            _error(
                "ownership.quarantine_conflict",
                "The owned path could not be quarantined safely.",
                recovery=(recovery,) if renamed else (),
            )
        )
    finally:
        for handle in (moved, source, parent, root):
            if handle:
                _paths._windows_close(handle)
        if namespace is not None:
            namespace.close()


def quarantine_owned(
    proof: OwnershipProof, *, transaction_id: str
) -> Result[QuarantineTicket]:
    """Move one proven leaf to a no-replace sibling quarantine."""

    if not isinstance(proof, OwnershipProof):
        return Result.failure(
            _error("ownership.unowned", "Ownership proof is required.")
        )
    reference = _snapshot_safe_reference(proof.relative)
    if reference is None or _TRANSACTION.fullmatch(transaction_id) is None:
        return Result.failure(
            _error("ownership.unowned", "Ownership proof is required.")
        )
    if os.name == "nt":
        return _quarantine_windows(
            proof,
            reference,
            transaction_id=transaction_id,
        )
    root = -1
    namespace: _paths._NamespaceCapability | None = None
    try:
        destination, recovery = _quarantine_target(reference, transaction_id)
        binding = _persistent_cleanup_binding(
            proof,
            transaction_id=transaction_id,
            recovery=recovery,
        )
        root, namespace = proof._take_authority()
    except ForgeError as exc:
        return Result.failure(exc)
    parent = source = moved = -1
    renamed = False
    try:
        root_status = os.fstat(root)
        if _identity(root) != proof._root_identity or not _paths._private_directory(
            root, root_status, exact=False
        ):
            raise _error("ownership.identity_mismatch", "The owned root changed.")
        parent = _open_parent(root, reference.components[:-1])
        try:
            source = os.open(
                reference.components[-1], _directory_flags(), dir_fd=parent
            )
        except OSError:
            return Result.failure(
                _error(
                    "ownership.identity_mismatch", "The owned path identity changed."
                )
            )
        if _identity(source) != proof.identity:
            return Result.failure(
                _error(
                    "ownership.identity_mismatch", "The owned path identity changed."
                )
            )
        if not namespace._validate_namespace_binding():
            return Result.failure(
                _error(
                    "ownership.identity_mismatch",
                    "The ownership proof containment changed.",
                )
            )
        _exclusive_rename(parent, reference.components[-1], destination)
        renamed = True
        os.fsync(parent)
        moved = os.open(destination, _directory_flags(), dir_fd=parent)
        if _identity(moved) != proof.identity:
            raise OSError(errno.ESTALE, "quarantined identity changed")
        if not namespace._validate_namespace_binding():
            raise OSError(errno.ESTALE, "quarantine containment changed")
        ticket_root = root
        ticket_namespace = namespace
        root = -1
        namespace = None
        return Result.success(
            QuarantineTicket(
                ticket_root,
                ticket_namespace,
                recovery,
                proof.identity,
                proof._root_identity,
                binding=binding,
                _token=_CAPABILITY_TOKEN,
            )
        )
    except FileExistsError:
        return Result.failure(
            _error(
                "ownership.quarantine_conflict",
                "The quarantine destination already exists.",
            )
        )
    except (ForgeError, OSError):
        return Result.failure(
            _error(
                "ownership.quarantine_conflict",
                "The owned path could not be quarantined safely.",
                recovery=(recovery,) if renamed else (),
            )
        )
    finally:
        for descriptor in (moved, source, parent, root):
            if descriptor >= 0:
                os.close(descriptor)
        if namespace is not None:
            namespace.close()


def _consume_cleanup_entry(entries: list[int]) -> None:
    entries[0] += 1
    if entries[0] > _CLEANUP_MAX_ENTRIES:
        raise OSError(errno.E2BIG, "cleanup entry limit exceeded")


def _require_cleanup_namespace(namespace: _paths._NamespaceCapability) -> None:
    if not namespace._validate_namespace_binding():
        raise OSError(errno.ESTALE, "quarantine containment changed")


_DARWIN_FSGETPATH_LIMIT = 8192


def _darwin_held_directory_path(handle: int) -> bytes | None:
    """Return a path to one held inode, or None only for proven ENOENT."""

    if sys.platform != "darwin":
        raise OSError(errno.ENOTSUP, "fsgetpath is Darwin-only")

    class Fsid(ctypes.Structure):
        _fields_ = [("value", ctypes.c_int32 * 2)]

    class StatFs(ctypes.Structure):
        _fields_ = [
            ("block_size", ctypes.c_uint32),
            ("io_size", ctypes.c_int32),
            ("blocks", ctypes.c_uint64),
            ("blocks_free", ctypes.c_uint64),
            ("blocks_available", ctypes.c_uint64),
            ("files", ctypes.c_uint64),
            ("files_free", ctypes.c_uint64),
            ("filesystem_id", Fsid),
            ("owner", ctypes.c_uint32),
            ("type", ctypes.c_uint32),
            ("flags", ctypes.c_uint32),
            ("subtype", ctypes.c_uint32),
            ("type_name", ctypes.c_char * 16),
            ("mounted_on", ctypes.c_char * 1024),
            ("mounted_from", ctypes.c_char * 1024),
            ("reserved", ctypes.c_uint32 * 8),
        ]

    before = os.fstat(handle)
    if not stat.S_ISDIR(before.st_mode):
        raise OSError(errno.ENOTDIR, "unlink proof requires a directory")
    libc = ctypes.CDLL(None, use_errno=True)
    fstatfs = libc.fstatfs
    fstatfs.argtypes = [ctypes.c_int, ctypes.POINTER(StatFs)]
    fstatfs.restype = ctypes.c_int
    information = StatFs()
    ctypes.set_errno(0)
    if fstatfs(handle, ctypes.byref(information)) != 0:
        error = ctypes.get_errno() or errno.EIO
        raise OSError(error, os.strerror(error))
    try:
        fsgetpath = libc.fsgetpath
    except AttributeError as exc:
        raise OSError(errno.ENOTSUP, "fsgetpath is unavailable") from exc
    fsgetpath.argtypes = [
        ctypes.POINTER(ctypes.c_char),
        ctypes.c_size_t,
        ctypes.POINTER(Fsid),
        ctypes.c_uint64,
    ]
    fsgetpath.restype = ctypes.c_ssize_t
    buffer = ctypes.create_string_buffer(_DARWIN_FSGETPATH_LIMIT)
    ctypes.set_errno(0)
    length = int(
        fsgetpath(
            buffer,
            len(buffer),
            ctypes.byref(information.filesystem_id),
            before.st_ino,
        )
    )
    error = ctypes.get_errno()
    after = os.fstat(handle)
    if (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino):
        raise OSError(errno.ESTALE, "held directory identity changed")
    if length == -1:
        if error == errno.ENOENT:
            return None
        error = error or errno.EIO
        raise OSError(error, os.strerror(error))
    if length < 1 or length > len(buffer) or buffer.raw[length - 1] != 0:
        raise OSError(errno.EIO, "fsgetpath returned an invalid path")
    return buffer.raw[: length - 1]


def _preflight_posix_directory_unlink_proof(
    descriptor: int,
    expected_identity: tuple[int, int],
) -> None:
    status = os.fstat(descriptor)
    if (status.st_dev, status.st_ino) != expected_identity or not stat.S_ISDIR(
        status.st_mode
    ):
        raise OSError(errno.ESTALE, "quarantine identity changed")
    if sys.platform == "darwin":
        if _darwin_held_directory_path(descriptor) is None:
            raise OSError(errno.ESTALE, "quarantine is already unlinked")
        return
    if sys.platform.startswith("linux"):
        if status.st_nlink <= 0:
            raise OSError(errno.ESTALE, "quarantine is already unlinked")
        return
    raise OSError(errno.ENOTSUP, "directory unlink proof is unavailable")


def _posix_directory_is_empty(descriptor: int) -> bool:
    with os.scandir(descriptor) as entries:
        return next(entries, None) is None


def _require_posix_directory_unlinked(
    descriptor: int,
    expected_identity: tuple[int, int],
) -> None:
    status = os.fstat(descriptor)
    if (status.st_dev, status.st_ino) != expected_identity:
        raise OSError(errno.ESTALE, "quarantine identity changed")
    if sys.platform == "darwin":
        if _darwin_held_directory_path(descriptor) is not None:
            raise OSError(errno.ESTALE, "held quarantine survived deletion")
        return
    if sys.platform.startswith("linux") and status.st_nlink == 0:
        return
    raise OSError(errno.ESTALE, "held quarantine unlink is unproven")


def _clean_directory(
    descriptor: int,
    *,
    device: int,
    depth: int,
    entries: list[int],
    namespace: _paths._NamespaceCapability,
) -> None:
    if depth > _CLEANUP_MAX_DEPTH:
        raise OSError(errno.ELOOP, "cleanup depth limit exceeded")
    directory_status = os.fstat(descriptor)
    if not stat.S_ISDIR(directory_status.st_mode) or directory_status.st_dev != device:
        raise OSError(errno.EXDEV, "cleanup crossed a filesystem boundary")
    names: list[str] = []
    with os.scandir(descriptor) as iterator:
        for entry in iterator:
            _consume_cleanup_entry(entries)
            names.append(entry.name)
    for name in sorted(names):
        status = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        expected_identity = (status.st_dev, status.st_ino)
        if status.st_dev != device:
            raise OSError(errno.EXDEV, "cleanup crossed a filesystem boundary")
        if stat.S_ISDIR(status.st_mode) and not stat.S_ISLNK(status.st_mode):
            child = os.open(name, _directory_flags(), dir_fd=descriptor)
            try:
                child_status = os.fstat(child)
                if (child_status.st_dev, child_status.st_ino) != expected_identity:
                    raise OSError(errno.ESTALE, "cleanup child identity changed")
                _clean_directory(
                    child,
                    device=device,
                    depth=depth + 1,
                    entries=entries,
                    namespace=namespace,
                )
            finally:
                os.close(child)
            current = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            if (current.st_dev, current.st_ino) != expected_identity:
                raise OSError(errno.ESTALE, "cleanup child identity changed")
            _require_cleanup_namespace(namespace)
            os.rmdir(name, dir_fd=descriptor)
        else:
            _require_cleanup_namespace(namespace)
            os.unlink(name, dir_fd=descriptor)


def _clean_windows_directory(
    handle: int,
    *,
    volume: int,
    depth: int,
    entries: list[int],
    namespace: _paths._NamespaceCapability,
) -> None:
    if depth > _CLEANUP_MAX_DEPTH:
        raise OSError(errno.ELOOP, "cleanup depth limit exceeded")
    status = _paths._windows_handle_status(handle)
    if not status.is_directory or status.is_reparse or status.identity[0] != volume:
        raise OSError(errno.EXDEV, "cleanup crossed a filesystem boundary")
    remaining = _CLEANUP_MAX_ENTRIES - entries[0]
    if remaining < 0:
        raise OSError(errno.E2BIG, "cleanup entry limit exceeded")
    for name in _windows_list_names(handle, limit=remaining):
        _consume_cleanup_entry(entries)
        child = current = 0
        try:
            child = _windows_open_raw_child(
                handle,
                name,
                directory=None,
                read_data=True,
                delete_access=True,
            )
            child_status = _paths._windows_handle_status(child)
            if child_status.identity[0] != volume:
                raise OSError(errno.EXDEV, "cleanup crossed a filesystem boundary")
            if child_status.is_directory and not child_status.is_reparse:
                _clean_windows_directory(
                    child,
                    volume=volume,
                    depth=depth + 1,
                    entries=entries,
                    namespace=namespace,
                )
            current = _windows_open_raw_child(
                handle, name, directory=None, delete_access=True
            )
            if _paths._windows_handle_status(current).identity != child_status.identity:
                raise OSError(errno.ESTALE, "cleanup child identity changed")
            _require_cleanup_namespace(namespace)
            _windows_delete_handle(current)
        finally:
            for item in (current, child):
                if item:
                    _paths._windows_close(item)


def _open_cleanup_transaction_store(
    root: int,
    namespace: _paths._NamespaceCapability,
    binding: PersistentTransactionBinding,
) -> _TransactionStore:
    if (
        not _persistent_binding_invariants(binding)
        or not namespace._validate_namespace_binding()
        or _native_identity(root) != binding.plugins_identity
        or _native_identity(namespace._plugins_descriptor) != binding.plugins_identity
        or _native_identity(namespace._control_descriptor) != binding.control_identity
    ):
        raise OSError(errno.ESTALE, "transaction cleanup authority changed")
    duplicate = _paths._windows_duplicate if os.name == "nt" else os.dup
    control = duplicate(namespace._control_descriptor)
    store = claims = 0 if os.name == "nt" else -1
    opened: _TransactionStore | None = None
    try:
        if os.name == "nt":
            store = _paths._windows_open_child(
                control, _TRANSACTION_STORE_COMPONENT, directory=True
            )
            claims = _paths._windows_open_child(
                store, _TRANSACTION_CLAIMS_COMPONENT, directory=True
            )
        else:
            store = _paths._open_directory_component(
                control,
                _TRANSACTION_STORE_COMPONENT,
                linked_code="path.linked_leaf",
            )
            claims = _paths._open_directory_component(
                store,
                _TRANSACTION_CLAIMS_COMPONENT,
                linked_code="path.linked_ancestor",
            )
        opened = _TransactionStore(
            control,
            store,
            claims,
            binding.plugins_identity,
            binding.control_identity,
            binding.store_identity,
            binding.claims_identity,
            os.name == "nt",
        )
        control = store = claims = 0 if os.name == "nt" else -1
        if (
            not _transaction_journal_store_is_valid(
                opened,
                binding,
                namespace._filesystem_guard,
            )
            or not _transaction_journal_records_are_valid(opened, binding)
            or not namespace._validate_namespace_binding()
        ):
            raise OSError(errno.ESTALE, "transaction cleanup authority changed")
        selected = opened
        opened = None
        return selected
    finally:
        if opened is not None:
            opened.close()
        if os.name == "nt":
            for descriptor in (claims, store, control):
                if descriptor:
                    _paths._windows_close(descriptor)
        else:
            for descriptor in (claims, store, control):
                if descriptor >= 0:
                    os.close(descriptor)


def _require_cleanup_recovery_mode(
    store: _TransactionStore,
    binding: PersistentTransactionBinding,
    authorization: RecoveryCleanupAuthorization | None,
) -> None:
    observed, observed_active = _load_recovery_cleanup_authorization_any(
        store,
        binding,
    )
    if observed is None:
        if authorization is not None or _recovery_cleanup_wal_residue_exists(
            store,
            binding.transaction_id,
        ):
            raise OSError(errno.ESTALE, "recovery cleanup authorization changed")
        return
    if (
        not observed_active
        or authorization is None
        or observed != authorization
        or not _recovery_cleanup_authorization_is_active(store, observed)
    ):
        raise OSError(
            errno.ESTALE,
            "generic cleanup cannot bypass recovery cleanup authorization",
        )


def _require_windows_cleanup_name_absent(parent: int, component: str) -> None:
    if _transaction_name_exists(
        parent,
        component,
        directory=True,
        windows=True,
    ):
        raise OSError(errno.ESTALE, "transaction cleanup name was replaced")


def _remove_windows_quarantine(
    ticket: QuarantineTicket,
    root: int,
    namespace: _paths._NamespaceCapability,
    *,
    recovery_authorization: RecoveryCleanupAuthorization | None = None,
) -> Result[CleanupResult]:
    parent = leaf = 0
    transaction_store: _TransactionStore | None = None
    state_lock: _TransactionStateLock | None = None
    cleanup_record: _TransactionCleanupRecord | None = None
    components = tuple(ticket.recovery_reference.split("/"))
    try:
        root_status = _paths._windows_handle_status(root)
        if (
            root_status.identity != ticket._root_identity
            or not _paths._windows_private_directory(root, exact=False)
        ):
            raise OSError(errno.ESTALE, "quarantine root identity changed")
        if ticket._binding is not None:
            transaction_store = _open_cleanup_transaction_store(
                root,
                namespace,
                ticket._binding,
            )
            state_lock = _acquire_transaction_state_lock(
                transaction_store,
                ticket._binding,
            )
            _require_cleanup_recovery_mode(
                transaction_store,
                ticket._binding,
                recovery_authorization,
            )
            cleanup_record = _publish_transaction_cleanup_intent(
                transaction_store,
                ticket._binding,
                delete_component=(
                    None
                    if recovery_authorization is None
                    else recovery_authorization.delete_component
                ),
            )
        parent = _windows_open_parent(root, components[:-1])
        leaf = _windows_open_raw_child(
            parent,
            components[-1],
            directory=True,
            read_data=True,
            delete_access=True,
        )
        leaf_status = _paths._windows_handle_status(leaf)
        if (
            leaf_status.identity != ticket._identity
            or leaf_status.is_reparse
            or leaf_status.identity[0] != ticket._root_identity[0]
        ):
            raise OSError(errno.ESTALE, "quarantine identity changed")
        _clean_windows_directory(
            leaf,
            volume=ticket._identity[0],
            depth=0,
            entries=[0],
            namespace=namespace,
        )
        current = _windows_open_raw_child(
            parent, components[-1], directory=True, delete_access=True
        )
        try:
            if _paths._windows_handle_status(
                current
            ).identity != ticket._identity or _windows_list_names(leaf, limit=1):
                raise OSError(errno.ESTALE, "quarantine identity changed")
            _require_cleanup_namespace(namespace)
            if recovery_authorization is not None:
                if (
                    ticket._binding != recovery_authorization.binding
                    or transaction_store is None
                    or cleanup_record is None
                ):
                    raise OSError(
                        errno.ESTALE,
                        "recovery cleanup authority changed",
                    )
                _publish_recovery_cleanup_finalizing(
                    transaction_store,
                    recovery_authorization,
                    cleanup_record,
                )
                if _paths._windows_handle_status(
                    current
                ).identity != ticket._identity or _windows_list_names(leaf, limit=1):
                    raise OSError(
                        errno.ESTALE,
                        "recovery cleanup finalization changed",
                    )
                _require_cleanup_namespace(namespace)
            _paths._windows_close(leaf)
            leaf = 0
            _windows_delete_handle(current)
        finally:
            _paths._windows_close(current)
        _require_windows_cleanup_name_absent(parent, components[-1])
        if ticket._binding is not None:
            if transaction_store is None or cleanup_record is None:
                raise OSError(errno.ESTALE, "transaction cleanup authority changed")
            _windows_flush_directory_binding(
                transaction_store.control,
                _TRANSACTION_STORE_COMPONENT,
                transaction_store.store_identity,
            )
            _require_windows_cleanup_name_absent(parent, components[-1])
            _publish_transaction_cleanup_complete(
                transaction_store,
                ticket._binding,
                delete_component=cleanup_record.delete_component,
            )
        return Result.success(CleanupResult(True, ticket.recovery_reference))
    except (ForgeError, OSError):
        return Result.failure(
            _error(
                "ownership.cleanup_incomplete",
                "Quarantine cleanup is incomplete.",
                recovery=(ticket.recovery_reference,),
            )
        )
    finally:
        try:
            if transaction_store is not None:
                transaction_store.close()
        finally:
            try:
                if state_lock is not None:
                    state_lock.close()
            finally:
                for item in (leaf, parent, root):
                    if item:
                        _paths._windows_close(item)
                namespace.close()


def remove_quarantine(
    ticket: QuarantineTicket,
    *,
    _recovery_authorization: RecoveryCleanupAuthorization | None = None,
) -> Result[CleanupResult]:
    """Remove one quarantined tree without following any link."""

    if not isinstance(ticket, QuarantineTicket) or (
        _recovery_authorization is not None
        and (
            type(_recovery_authorization) is not RecoveryCleanupAuthorization
            or not _recovery_cleanup_authorization_invariants(_recovery_authorization)
            or ticket._binding != _recovery_authorization.binding
        )
    ):
        return Result.failure(
            _error("ownership.cleanup_incomplete", "A quarantine ticket is required.")
        )
    try:
        root, namespace = ticket._take_authority()
    except ForgeError as exc:
        return Result.failure(exc)
    if os.name == "nt":
        return _remove_windows_quarantine(
            ticket,
            root,
            namespace,
            recovery_authorization=_recovery_authorization,
        )
    parent = leaf = -1
    transaction_store: _TransactionStore | None = None
    state_lock: _TransactionStateLock | None = None
    cleanup_record: _TransactionCleanupRecord | None = None
    components = tuple(ticket.recovery_reference.split("/"))
    try:
        root_status = os.fstat(root)
        if _identity(root) != ticket._root_identity or not _paths._private_directory(
            root, root_status, exact=False
        ):
            raise OSError(errno.ESTALE, "quarantine root identity changed")
        if ticket._binding is not None:
            transaction_store = _open_cleanup_transaction_store(
                root,
                namespace,
                ticket._binding,
            )
            state_lock = _acquire_transaction_state_lock(
                transaction_store,
                ticket._binding,
            )
            _require_cleanup_recovery_mode(
                transaction_store,
                ticket._binding,
                _recovery_authorization,
            )
            cleanup_record = _publish_transaction_cleanup_intent(
                transaction_store,
                ticket._binding,
                delete_component=(
                    None
                    if _recovery_authorization is None
                    else _recovery_authorization.delete_component
                ),
            )
        parent = _open_parent(root, components[:-1])
        current_component = components[-1]
        leaf = os.open(current_component, _directory_flags(), dir_fd=parent)
        if _identity(leaf) != ticket._identity:
            raise OSError(errno.ESTALE, "quarantine identity changed")
        if ticket._binding is not None:
            if cleanup_record is None:
                raise OSError(errno.ESTALE, "transaction cleanup authority changed")
            quarantine_component = ticket._binding.quarantine_relative.rsplit("/", 1)[
                -1
            ]
            if current_component == quarantine_component:
                if _transaction_name_exists(
                    parent,
                    cleanup_record.delete_component,
                    directory=True,
                    windows=False,
                ):
                    raise OSError(
                        errno.EEXIST,
                        "transaction delete token conflicts",
                    )
                _paths._exclusive_posix_rename(
                    parent,
                    current_component,
                    cleanup_record.delete_component,
                )
                os.fsync(parent)
                current_component = cleanup_record.delete_component
            elif current_component != cleanup_record.delete_component:
                raise OSError(errno.ESTALE, "transaction delete token changed")
            if (
                not _paths._posix_namespace_binds(
                    parent,
                    current_component,
                    ticket._identity,
                )
                or _identity(leaf) != ticket._identity
            ):
                raise OSError(errno.ESTALE, "transaction delete token changed")
        _preflight_posix_directory_unlink_proof(leaf, ticket._identity)
        _clean_directory(
            leaf,
            device=ticket._identity[0],
            depth=0,
            entries=[0],
            namespace=namespace,
        )
        if not _paths._posix_namespace_binds(
            parent,
            current_component,
            ticket._identity,
        ):
            raise OSError(errno.ESTALE, "quarantine identity changed")
        _preflight_posix_directory_unlink_proof(leaf, ticket._identity)
        _require_cleanup_namespace(namespace)
        if not _posix_directory_is_empty(leaf):
            raise OSError(errno.ENOTEMPTY, "quarantine changed after cleanup")
        if _recovery_authorization is not None:
            if (
                ticket._binding != _recovery_authorization.binding
                or transaction_store is None
                or cleanup_record is None
            ):
                raise OSError(errno.ESTALE, "recovery cleanup authority changed")
            _publish_recovery_cleanup_finalizing(
                transaction_store,
                _recovery_authorization,
                cleanup_record,
            )
            if not _paths._posix_namespace_binds(
                parent,
                current_component,
                ticket._identity,
            ) or not _posix_directory_is_empty(leaf):
                raise OSError(
                    errno.ESTALE,
                    "recovery cleanup finalization changed",
                )
            _preflight_posix_directory_unlink_proof(leaf, ticket._identity)
            _require_cleanup_namespace(namespace)
        os.rmdir(current_component, dir_fd=parent)
        os.fsync(parent)
        if _transaction_name_exists(
            parent,
            current_component,
            directory=True,
            windows=False,
        ):
            raise OSError(errno.ESTALE, "transaction delete token was replaced")
        _require_posix_directory_unlinked(leaf, ticket._identity)
        if ticket._binding is not None:
            if transaction_store is None or cleanup_record is None:
                raise OSError(errno.ESTALE, "transaction cleanup authority changed")
            _publish_transaction_cleanup_complete(
                transaction_store,
                ticket._binding,
                delete_component=cleanup_record.delete_component,
            )
        return Result.success(CleanupResult(True, ticket.recovery_reference))
    except (ForgeError, OSError):
        return Result.failure(
            _error(
                "ownership.cleanup_incomplete",
                "Quarantine cleanup is incomplete.",
                recovery=(ticket.recovery_reference,),
            )
        )
    finally:
        try:
            if transaction_store is not None:
                transaction_store.close()
        finally:
            try:
                if state_lock is not None:
                    state_lock.close()
            finally:
                for descriptor in (leaf, parent, root):
                    if descriptor >= 0:
                        os.close(descriptor)
                namespace.close()


def _complete_recovery_cleanup(
    owned_root: OwnedRoot,
    authorization: RecoveryCleanupAuthorization,
) -> CleanupResult:
    store = _open_transaction_store(owned_root, create=False)
    try:
        state = _inspect_recovery_cleanup_state(
            owned_root,
            store,
            authorization,
        )
        if not state.authorization_active:
            return CleanupResult(True, authorization.journal_relative)
        if state.finalizing is None or state.journal_relative is not None:
            raise OSError(errno.ESTALE, "recovery cleanup is not finalizing")
        if state.cleanup_complete is None:
            if state.cleanup_intent is None:
                raise OSError(errno.ESTALE, "recovery cleanup intent is missing")
            _publish_transaction_cleanup_complete(
                store,
                authorization.binding,
                delete_component=authorization.delete_component,
            )
        elif state.cleanup_intent is not None:
            _remove_exact_transaction_record(
                store,
                state.cleanup_intent.component,
                state.cleanup_intent.identity,
                state.cleanup_intent.raw,
            )

        state = _inspect_recovery_cleanup_state(
            owned_root,
            store,
            authorization,
        )
        if (
            state.finalizing is None
            or state.cleanup_complete is None
            or state.journal_relative is not None
        ):
            raise OSError(errno.ESTALE, "recovery cleanup completion changed")
        complete = state.complete
        if complete is None:
            complete = _publish_recovery_cleanup_phase(
                store,
                authorization,
                phase=_RecoveryCleanupPhase.COMPLETE,
                previous_phase_digest=state.finalizing.record_digest,
            )
        _retire_recovery_cleanup_phase(store, complete)
        _retire_recovery_cleanup_phase(store, state.finalizing)
        authorization_record = _RecoveryCleanupPhaseRecord(
            component=authorization._component,
            identity=authorization._identity,
            raw=authorization._raw,
            phase=_RecoveryCleanupPhase.AUTHORIZED,
            previous_phase_digest=_ZERO_DIGEST,
            record_digest=authorization.authorization_digest,
            retired=False,
        )
        _retire_recovery_cleanup_phase(store, authorization_record)
        final_state = _inspect_recovery_cleanup_state(
            owned_root,
            store,
            authorization,
        )
        if final_state.authorization_active:
            raise OSError(errno.ESTALE, "recovery cleanup retirement changed")
        return CleanupResult(True, authorization.journal_relative)
    finally:
        store.close()


def resume_recovery_cleanup(
    owned_root: OwnedRoot,
    authorization: RecoveryCleanupAuthorization,
) -> Result[CleanupResult]:
    """Resume one exact terminal cleanup from its durable three-phase WAL."""

    store: _TransactionStore | None = None
    rebound: ReboundTransaction | None = None
    try:
        if (
            not isinstance(owned_root, OwnedRoot)
            or type(authorization) is not RecoveryCleanupAuthorization
            or not _recovery_cleanup_authorization_invariants(authorization)
        ):
            raise TypeError("recovery cleanup authorization")
        store = _open_transaction_store(owned_root, create=False)
        state = _inspect_recovery_cleanup_state(
            owned_root,
            store,
            authorization,
        )
        if not state.authorization_active:
            return Result.success(CleanupResult(True, authorization.journal_relative))
        if state.journal_relative is None:
            store.close()
            store = None
            return Result.success(_complete_recovery_cleanup(owned_root, authorization))
        if state.cleanup_complete is not None:
            raise OSError(errno.ESTALE, "recovery cleanup completion conflicts")
        cleanup_intent = _publish_transaction_cleanup_intent(
            store,
            authorization.binding,
            delete_component=authorization.delete_component,
        )
        if cleanup_intent.delete_component != authorization.delete_component:
            raise OSError(errno.ESTALE, "recovery cleanup intent changed")
        confirmed = _inspect_recovery_cleanup_state(
            owned_root,
            store,
            authorization,
        )
        if confirmed.journal_relative != state.journal_relative:
            raise OSError(errno.ESTALE, "recovery cleanup location changed")
        store.close()
        store = None

        rebound_result = rebind_persistent_transaction_for_recovery(
            owned_root,
            binding=authorization.binding,
            _recovery_authorization=authorization,
        )
        if not rebound_result.is_ok:
            raise OSError(errno.ESTALE, "recovery cleanup cannot be rebound")
        rebound = rebound_result.unwrap()
        if (
            rebound.location is not TransactionLocation.QUARANTINED
            or rebound.ticket is None
            or rebound.ticket.recovery_reference != state.journal_relative
        ):
            raise OSError(errno.ESTALE, "recovery cleanup rebound changed")
        ticket = rebound.ticket
        removed = remove_quarantine(
            ticket,
            _recovery_authorization=authorization,
        )
        rebound = None
        if not removed.is_ok:
            return removed
        return Result.success(_complete_recovery_cleanup(owned_root, authorization))
    except (AttributeError, ForgeError, OSError, TypeError, ValueError):
        recovery = (
            (authorization.binding.quarantine_relative,)
            if type(authorization) is RecoveryCleanupAuthorization
            and _recovery_cleanup_authorization_invariants(authorization)
            else ()
        )
        return Result.failure(
            _error(
                "ownership.cleanup_incomplete",
                "Recovery cleanup is incomplete.",
                recovery=recovery,
            )
        )
    finally:
        if rebound is not None:
            rebound.close()
        if store is not None:
            store.close()


def load_legacy_install_catalog() -> Result[LegacyInstallCatalog]:
    """Load and seal the one installed recognition-only legacy authority."""

    try:
        raw = (
            resources.files("zagrosi_forge.install")
            .joinpath("legacy-install-catalog.json")
            .read_bytes()
        )
        if hashlib.sha256(raw).hexdigest() != _LEGACY_CATALOG_RESOURCE_DIGEST:
            raise ValueError("legacy catalog resource digest")
        decoded = decode_persistent_record(raw, reader_version=_WRITER_VERSION)
        expected = {
            "record_kind": "legacy-install-catalog",
            "schema_version": "1.0",
            "schema_digest": _LEGACY_CATALOG_SCHEMA_DIGEST,
            "writer_version": _WRITER_VERSION,
            "minimum_reader_version": _WRITER_VERSION,
            "marketplace_id": "zagrosi",
            "source_type": "local",
            "source_leaf": "zagrosi-forge",
            "plugin_key": "zagrosi-forge@zagrosi",
            "cache_pattern": "cache/zagrosi/zagrosi-forge/<base-version>",
            "authority": "recognition-only",
        }
        if set(decoded) != {*expected, "record_digest"} or any(
            decoded.get(key) != value for key, value in expected.items()
        ):
            raise ValueError("legacy catalog contract")
        record_digest = decoded.get("record_digest")
        if not isinstance(record_digest, str):
            raise ValueError("legacy catalog record digest")
        return Result.success(
            LegacyInstallCatalog(
                marketplace_id=cast(str, decoded["marketplace_id"]),
                source_type=cast(str, decoded["source_type"]),
                source_leaf=cast(str, decoded["source_leaf"]),
                plugin_key=cast(str, decoded["plugin_key"]),
                cache_pattern=cast(str, decoded["cache_pattern"]),
                catalog_digest=record_digest,
                _token=_LEGACY_TOKEN,
            )
        )
    except (ForgeError, OSError, TypeError, ValueError, KeyError):
        return Result.failure(
            _error(
                "ownership.receipt_invalid",
                "The installed legacy recognition catalog is invalid.",
            )
        )


def _legacy_checkout_source(source: object, *, expected_leaf: str) -> bool:
    if (
        type(source) is not str
        or not source
        or len(source.encode("utf-8")) > 4_096
        or "\0" in source
    ):
        return False
    posix_source = PurePosixPath(source)
    posix_parts = posix_source.parts
    posix_match = (
        source.startswith("/")
        and not source.startswith("//")
        and posix_source.is_absolute()
        and posix_source.name == expected_leaf
        and posix_source.as_posix() == source
        and all(part not in {".", ".."} for part in posix_parts)
    )
    windows_source = PureWindowsPath(source)
    windows_parts = windows_source.parts
    windows_match = (
        windows_source.is_absolute()
        and windows_source.name == expected_leaf
        and str(windows_source) == source
        and not source.startswith(("\\\\", "//", "\\?\\", "\\.\\"))
        and all(part not in {".", ".."} for part in windows_parts)
    )
    return posix_match or windows_match


def match_legacy_install(
    catalog: LegacyInstallCatalog,
    *,
    marketplace_id: str,
    marketplace_table: Mapping[str, object],
    plugin_key: str,
    plugin_table: Mapping[str, object],
    cache_relative: SafeRelativePath,
) -> Result[LegacyRecognition | None]:
    """Match complete relevant tables; near misses remain unmanaged data."""

    if not _legacy_catalog_invariants(catalog):
        return Result.failure(
            _error(
                "ownership.receipt_invalid",
                "The installed legacy recognition catalog is invalid.",
            )
        )
    cache_reference = _snapshot_safe_reference(cache_relative)
    if (
        not isinstance(marketplace_table, Mapping)
        or not isinstance(plugin_table, Mapping)
        or set(marketplace_table) != {"source_type", "source"}
        or set(plugin_table) != {"enabled"}
        or cache_reference is None
    ):
        return Result.success(None)
    source_type = marketplace_table["source_type"]
    source = marketplace_table["source"]
    enabled = plugin_table["enabled"]
    cache = re.fullmatch(
        r"cache/zagrosi/zagrosi-forge/((?:0|[1-9][0-9]*)\."
        r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*))",
        cache_reference.value,
    )
    if (
        type(marketplace_id) is not str
        or marketplace_id != catalog.marketplace_id
        or type(source_type) is not str
        or source_type != catalog.source_type
        or type(plugin_key) is not str
        or plugin_key != catalog.plugin_key
        or type(enabled) is not bool
        or enabled is not True
        or not _legacy_checkout_source(source, expected_leaf=catalog.source_leaf)
        or cache is None
    ):
        return Result.success(None)
    projection = {
        "cache_relative": cache_reference.value,
        "marketplaces": {
            marketplace_id: {"source": source, "source_type": source_type}
        },
        "plugins": {plugin_key: {"enabled": enabled}},
    }
    return Result.success(
        LegacyRecognition(
            marketplace_id=marketplace_id,
            cache_relative=cache_reference.value,
            base_version=cache[1],
            catalog_digest=catalog.catalog_digest,
            projection_digest=hashlib.sha256(
                canonical_json_bytes(projection)
            ).hexdigest(),
            _token=_LEGACY_TOKEN,
        )
    )


def recognize_legacy_install(
    catalog: LegacyInstallCatalog,
    *,
    marketplace_id: str,
    marketplace_table: Mapping[str, object],
    plugin_key: str,
    plugin_table: Mapping[str, object],
    cache_relative: SafeRelativePath,
) -> Result[LegacyRecognition | None]:
    """Compatibility spelling for the exact complete-table matcher."""

    return match_legacy_install(
        catalog,
        marketplace_id=marketplace_id,
        marketplace_table=marketplace_table,
        plugin_key=plugin_key,
        plugin_table=plugin_table,
        cache_relative=cache_relative,
    )
