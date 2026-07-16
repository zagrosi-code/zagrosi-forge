"""Receipt-bound ownership and the sole recursive removal capability."""

from __future__ import annotations

import ctypes
from _thread import LockType
from dataclasses import dataclass
from datetime import datetime
import errno
import hashlib
from importlib import resources
import os
from pathlib import PurePosixPath, PureWindowsPath
import re
import secrets
import stat
import sys
from threading import Lock
from typing import Mapping, Never, cast

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


def _windows_flush(handle: int) -> None:
    from ctypes import wintypes

    kernel32 = _paths._windows_dll("kernel32")
    kernel32.FlushFileBuffers.argtypes = [wintypes.HANDLE]
    kernel32.FlushFileBuffers.restype = wintypes.BOOL
    if not kernel32.FlushFileBuffers(handle):
        raise _paths._windows_error(_paths._windows_last_error())


def _windows_private_file(handle: int) -> bool:
    status = _paths._windows_handle_status(handle)
    if status.is_directory or status.is_reparse or status.link_count != 1:
        return False
    return _paths._windows_private_authorization(handle, exact=True)


def _windows_open_parent(root: int, components: tuple[str, ...]) -> int:
    current = _paths._windows_duplicate(root)
    try:
        for component in components:
            child = _paths._windows_open_child(current, component, directory=True)
            _paths._windows_close(current)
            current = child
        return current
    except BaseException:
        _paths._windows_close(current)
        raise


def _windows_open_private_directory_chain(
    root: int,
    components: tuple[str, ...],
    *,
    volume: int,
    create_missing: bool = True,
) -> int:
    current = _paths._windows_duplicate(root)
    try:
        for component in components:
            child = 0
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
                if child:
                    _paths._windows_close(child)
                raise
            _paths._windows_close(current)
            current = child
        return current
    except BaseException:
        _paths._windows_close(current)
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
    current = os.dup(root)
    try:
        for component in components:
            child = os.open(component, _directory_flags(), dir_fd=current)
            os.close(current)
            current = child
        return current
    except BaseException:
        os.close(current)
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


class TransactionPathClaim:
    _consumed: bool
    _identity: tuple[int, int]
    _lock: LockType
    _relative: SafeRelativePath
    _root_identity: tuple[int, int]
    _transaction_id: str

    __slots__ = (
        "_consumed",
        "_identity",
        "_lock",
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
        _token: object,
    ) -> None:
        if _token is not _CAPABILITY_TOKEN:
            raise TypeError(
                "TransactionPathClaim is created only by exclusive creation"
            )
        object.__setattr__(self, "_transaction_id", transaction_id)
        object.__setattr__(self, "_relative", relative)
        object.__setattr__(self, "_root_identity", root_identity)
        object.__setattr__(self, "_identity", identity)
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
    _closed: bool
    _identity: tuple[int, int]
    _lock: LockType
    _namespace: _paths._NamespaceCapability | None
    _recovery_reference: str
    _root: int
    _root_identity: tuple[int, int]
    _used: bool

    __slots__ = (
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
        _token: object,
    ) -> None:
        if _token is not _CAPABILITY_TOKEN:
            raise TypeError("QuarantineTicket is created only by quarantine_owned")
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


@dataclass(frozen=True, slots=True)
class CleanupResult:
    removed: bool
    recovery_reference: str


@dataclass(frozen=True, slots=True)
class ReceiptPublication:
    reference: SafeRelativePath
    created: bool


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
            _TransactionObservation(path, claim.transaction_id),
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
    current = os.dup(root)
    try:
        for component in components:
            created = False
            child = -1
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
                if child >= 0:
                    os.close(child)
                raise
            os.close(current)
            current = child
        return current
    except BaseException:
        os.close(current)
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
        if os.name == "nt":
            for handle in (parent, control):
                if handle:
                    _paths._windows_close(handle)
        else:
            for descriptor in (parent, control):
                if descriptor >= 0:
                    os.close(descriptor)


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
    control = parent = canonical_leaf = -1
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
        for descriptor in (canonical_leaf, parent, control):
            if descriptor >= 0:
                _close_native(descriptor)
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
        _windows_rename_handle(source, parent, destination)
        renamed = True
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


def _remove_windows_quarantine(
    ticket: QuarantineTicket,
    root: int,
    namespace: _paths._NamespaceCapability,
) -> Result[CleanupResult]:
    parent = leaf = 0
    components = tuple(ticket.recovery_reference.split("/"))
    try:
        root_status = _paths._windows_handle_status(root)
        if (
            root_status.identity != ticket._root_identity
            or not _paths._windows_private_directory(root, exact=False)
        ):
            raise OSError(errno.ESTALE, "quarantine root identity changed")
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
            if _paths._windows_handle_status(current).identity != ticket._identity:
                raise OSError(errno.ESTALE, "quarantine identity changed")
            _require_cleanup_namespace(namespace)
            _windows_delete_handle(current)
        finally:
            _paths._windows_close(current)
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
        for item in (leaf, parent, root):
            if item:
                _paths._windows_close(item)
        namespace.close()


def remove_quarantine(ticket: QuarantineTicket) -> Result[CleanupResult]:
    """Remove one quarantined tree without following any link."""

    if not isinstance(ticket, QuarantineTicket):
        return Result.failure(
            _error("ownership.cleanup_incomplete", "A quarantine ticket is required.")
        )
    try:
        root, namespace = ticket._take_authority()
    except ForgeError as exc:
        return Result.failure(exc)
    if os.name == "nt":
        return _remove_windows_quarantine(ticket, root, namespace)
    parent = leaf = -1
    components = tuple(ticket.recovery_reference.split("/"))
    try:
        root_status = os.fstat(root)
        if _identity(root) != ticket._root_identity or not _paths._private_directory(
            root, root_status, exact=False
        ):
            raise OSError(errno.ESTALE, "quarantine root identity changed")
        parent = _open_parent(root, components[:-1])
        leaf = os.open(components[-1], _directory_flags(), dir_fd=parent)
        if _identity(leaf) != ticket._identity:
            raise OSError(errno.ESTALE, "quarantine identity changed")
        _clean_directory(
            leaf,
            device=ticket._identity[0],
            depth=0,
            entries=[0],
            namespace=namespace,
        )
        current = os.stat(components[-1], dir_fd=parent, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != ticket._identity:
            raise OSError(errno.ESTALE, "quarantine identity changed")
        _require_cleanup_namespace(namespace)
        os.rmdir(components[-1], dir_fd=parent)
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
        for descriptor in (leaf, parent, root):
            if descriptor >= 0:
                os.close(descriptor)
        namespace.close()


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
