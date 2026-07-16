"""Lexical path validation and live, no-follow filesystem authority."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
import ctypes
from dataclasses import dataclass
import errno
import hashlib
import os
from pathlib import PurePosixPath, PureWindowsPath
import re
import secrets
import stat
import sys
from types import MappingProxyType
from typing import Any, Never, cast
import unicodedata

from .contracts import (
    ForgeError,
    Result,
    RunnerOperation,
    RunnerProvenance,
    canonical_json_bytes,
    decode_persistent_record,
    require_runner_authority,
)
from .policies import LIMIT_POLICY, LimitPolicy


FileIdentity = tuple[int, int]
FilesystemGuard = Callable[[int], bool]

_CAPABILITY_TOKEN = object()
_CONTROL_FILE = "control-v1.json"
_CONTROL_RECORD_LIMIT = 8 * 1024
_CONTROL_SCHEMA_DIGEST = (
    "e5114f0feab36ae80c47241a87de6d2f0077cd346c9becf0cf40c52a711157c4"
)
_CONTROL_SCHEMA_VERSION = "1.0"
_CONTROL_WRITER_VERSION = "0.2.0"
_XATTR_LIST_LIMIT = 64 * 1024
_LOWER_COMPONENT = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\Z")
_WINDOWS_PREFIX = re.compile(r"(?i)^[a-z]:")
_INTERNAL_RECEIPT_REFERENCE = re.compile(
    r"\.zagrosi/ownership/"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?/"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?/"
    r"[0-9a-f]{64}\.json\Z"
)
_WINDOWS_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL", "CLOCK$"}
    | {f"COM{number}" for number in range(1, 10)}
    | {f"LPT{number}" for number in range(1, 10)}
)


class _AuthorityOrigin:
    """Unserializable per-facade origin used to bind minted capabilities."""

    __slots__ = ()

    def __init__(self, *, _token: object) -> None:
        if _token is not _CAPABILITY_TOKEN:
            raise TypeError("authority origins are minted internally")

    def __reduce__(self) -> Never:
        raise TypeError("authority origins are not serializable")


class _ReferenceMintOrigin:
    """Opaque origin separating candidate and trusted internal references."""

    __slots__ = ()

    def __init__(self, *, _token: object) -> None:
        if _token is not _CAPABILITY_TOKEN:
            raise TypeError("reference origins are minted internally")

    def __reduce__(self) -> Never:
        raise TypeError("reference origins are not serializable")


_CANDIDATE_REFERENCE_ORIGIN = _ReferenceMintOrigin(_token=_CAPABILITY_TOKEN)
_INTERNAL_REFERENCE_ORIGIN = _ReferenceMintOrigin(_token=_CAPABILITY_TOKEN)


_LINUX_NETWORK_FILESYSTEMS = frozenset(
    {
        0x517B,  # SMB
        0x6969,  # NFS
        0x65735546,  # FUSE (support must be explicitly added by policy)
        0xFE534D42,  # SMB2
        0xFF534D42,  # CIFS
    }
)
_LINUX_LOCAL_FILESYSTEMS = frozenset(
    {
        0x01021994,  # tmpfs
        0x2FC12FC1,  # zfs
        0x58465342,  # xfs
        0x61756673,  # aufs
        0x794C7630,  # overlayfs
        0x9123683E,  # btrfs
        0xEF53,  # ext2/3/4
    }
)


def _error(code: str, message: str) -> ForgeError:
    return ForgeError(code, 11, message)


@dataclass(frozen=True, slots=True, init=False)
class SafeComponent:
    value: str

    def __init__(self, value: str, *, _token: object) -> None:
        if _token is not _CAPABILITY_TOKEN:
            raise TypeError("SafeComponent is created only by validate_component")
        object.__setattr__(self, "value", value)

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True, init=False)
class SafeRelativePath:
    value: str
    components: tuple[str, ...]
    collision_key: str
    _mint: object

    def __init__(
        self,
        value: str,
        components: tuple[str, ...],
        collision_key: str,
        *,
        _mint: _ReferenceMintOrigin,
        _token: object,
    ) -> None:
        if _token is not _CAPABILITY_TOKEN or _mint not in {
            _CANDIDATE_REFERENCE_ORIGIN,
            _INTERNAL_REFERENCE_ORIGIN,
        }:
            raise TypeError("SafeRelativePath is created only by validate_reference")
        object.__setattr__(self, "value", value)
        object.__setattr__(self, "components", components)
        object.__setattr__(self, "collision_key", collision_key)
        object.__setattr__(self, "_mint", _mint)

    def __str__(self) -> str:
        return self.value


def _safe_reference_invariants(reference: object) -> bool:
    """Revalidate every projection before crossing a native path boundary."""

    if type(reference) is not SafeRelativePath or getattr(
        reference, "_mint", None
    ) not in {_CANDIDATE_REFERENCE_ORIGIN, _INTERNAL_REFERENCE_ORIGIN}:
        return False
    candidate = reference
    if (
        not isinstance(candidate.value, str)
        or not isinstance(candidate.components, tuple)
        or any(not isinstance(component, str) for component in candidate.components)
        or not isinstance(candidate.collision_key, str)
    ):
        return False
    if candidate._mint is _CANDIDATE_REFERENCE_ORIGIN:
        validated = validate_reference(
            candidate.value, role="native-boundary", limits=LIMIT_POLICY
        )
    else:
        validated = _validate_internal_reference(
            candidate.value, role="native-boundary", limits=LIMIT_POLICY
        )
    if not validated.is_ok:
        return False
    canonical = validated.unwrap()
    return (
        candidate._mint is canonical._mint
        and candidate.value == canonical.value
        and candidate.components == canonical.components
        and candidate.collision_key == canonical.collision_key
    )


def _utf8_length(raw: str) -> int | None:
    try:
        return len(raw.encode("utf-8"))
    except UnicodeEncodeError:
        return None


def _has_control(raw: str) -> bool:
    return any(unicodedata.category(character).startswith("C") for character in raw)


def _has_windows_prefix(raw: str) -> bool:
    windows = PureWindowsPath(raw)
    return bool(
        _WINDOWS_PREFIX.match(raw)
        or raw.startswith(("\\\\", "//", "\\?\\", "\\.\\"))
        or windows.drive
    )


def _is_windows_reserved(component: str) -> bool:
    component = unicodedata.normalize("NFKC", component)
    if component.endswith((".", " ")) or ":" in component:
        return True
    basename = component.split(".", 1)[0].upper()
    return basename in _WINDOWS_RESERVED


def validate_component(
    raw: str, *, role: str, limits: LimitPolicy
) -> Result[SafeComponent]:
    """Validate one lowercase ASCII identifier without host-dependent rules."""

    del role
    if not isinstance(raw, str):
        return Result.rejected(
            _error("path.component_invalid", "The path component is invalid.")
        )
    if raw.startswith("//") or _has_windows_prefix(raw):
        return Result.rejected(
            _error("path.windows_prefix", "Windows path prefixes are not allowed.")
        )
    if raw.startswith("/"):
        return Result.rejected(
            _error("path.absolute", "Absolute paths are not allowed.")
        )
    if raw in {".", ".."}:
        return Result.rejected(
            _error("path.traversal", "Traversal components are not allowed.")
        )
    if _is_windows_reserved(raw):
        return Result.rejected(
            _error("path.reserved", "The path component is reserved.")
        )
    size = _utf8_length(raw)
    if (
        not raw
        or size is None
        or size > limits.value("identifier_bytes")
        or size > limits.value("path_component_bytes")
        or "/" in raw
        or "\\" in raw
        or _has_control(raw)
        or _LOWER_COMPONENT.fullmatch(raw) is None
    ):
        return Result.rejected(
            _error("path.component_invalid", "The path component is invalid.")
        )
    return Result.accepted(SafeComponent(raw, _token=_CAPABILITY_TOKEN))


def _validate_reference_component(
    component: str, *, limits: LimitPolicy, max_component_bytes: int
) -> ForgeError | None:
    if component in {"", ".", ".."}:
        return _error(
            "path.traversal", "Traversal or empty components are not allowed."
        )
    if component.startswith("~") or _has_control(component):
        return _error("path.component_invalid", "A reference component is invalid.")
    size = _utf8_length(component)
    if size is None or size > max_component_bytes:
        return _error("path.component_invalid", "A reference component is invalid.")
    if _is_windows_reserved(component):
        return _error("path.reserved", "A reference component is reserved.")
    return None


def _validate_reference(
    raw: str,
    *,
    role: str,
    limits: LimitPolicy,
    max_component_bytes: int,
    mint: _ReferenceMintOrigin,
) -> Result[SafeRelativePath]:
    """Validate one portable POSIX-form relative reference before native access."""

    del role
    if not isinstance(raw, str):
        return Result.rejected(
            _error("path.component_invalid", "The relative reference is invalid.")
        )
    if raw.startswith("//") or _has_windows_prefix(raw):
        return Result.rejected(
            _error("path.windows_prefix", "Windows path prefixes are not allowed.")
        )
    posix = PurePosixPath(raw)
    if raw.startswith("/") or posix.is_absolute():
        return Result.rejected(
            _error("path.absolute", "Absolute paths are not allowed.")
        )
    size = _utf8_length(raw)
    if (
        not raw
        or size is None
        or size > limits.value("path_bytes")
        or "\\" in raw
        or raw.endswith("/")
        or "//" in raw
    ):
        return Result.rejected(
            _error("path.component_invalid", "The relative reference is invalid.")
        )
    components = tuple(raw.split("/"))
    if len(components) > limits.value("path_components"):
        return Result.rejected(
            _error("path.component_invalid", "The relative reference is invalid.")
        )
    for component in components:
        if problem := _validate_reference_component(
            component, limits=limits, max_component_bytes=max_component_bytes
        ):
            return Result.rejected(problem)
    if tuple(posix.parts) != components or PureWindowsPath(raw).is_absolute():
        return Result.rejected(
            _error("path.component_invalid", "The relative reference is invalid.")
        )
    collision_key = "/".join(
        unicodedata.normalize("NFKC", component).casefold() for component in components
    )
    return Result.accepted(
        SafeRelativePath(
            raw,
            components,
            collision_key,
            _mint=mint,
            _token=_CAPABILITY_TOKEN,
        )
    )


def validate_reference(
    raw: str, *, role: str, limits: LimitPolicy
) -> Result[SafeRelativePath]:
    """Validate one candidate-controlled portable relative reference."""

    return _validate_reference(
        raw,
        role=role,
        limits=limits,
        max_component_bytes=limits.value("path_component_bytes"),
        mint=_CANDIDATE_REFERENCE_ORIGIN,
    )


def _validate_internal_reference(
    raw: str,
    *,
    role: str,
    limits: LimitPolicy,
) -> Result[SafeRelativePath]:
    """Mint only the fixed trusted committed-receipt reference shape."""

    if not isinstance(raw, str) or _INTERNAL_RECEIPT_REFERENCE.fullmatch(raw) is None:
        return Result.rejected(
            _error("path.component_invalid", "The internal reference is invalid.")
        )
    return _validate_reference(
        raw,
        role=role,
        limits=limits,
        max_component_bytes=len(("0" * 64 + ".json").encode("ascii")),
        mint=_INTERNAL_REFERENCE_ORIGIN,
    )


def validate_reference_set(
    raw_references: Iterable[str], *, role: str, limits: LimitPolicy
) -> Result[tuple[SafeRelativePath, ...]]:
    """Validate references and reject cross-platform spelling ambiguity."""

    accepted: list[SafeRelativePath] = []
    seen: set[str] = set()
    for raw in raw_references:
        result = validate_reference(raw, role=role, limits=limits)
        if not result.is_ok:
            return Result.rejected(cast(ForgeError, result.error))
        reference = result.unwrap()
        if reference.collision_key in seen:
            return Result.rejected(
                _error(
                    "path.normalization_collision",
                    "References collide under portable normalization.",
                )
            )
        seen.add(reference.collision_key)
        accepted.append(reference)
    return Result.accepted(tuple(accepted))


def _posix_directory_flags() -> int:
    if any(
        not hasattr(os, name) for name in ("O_CLOEXEC", "O_DIRECTORY", "O_NOFOLLOW")
    ):
        raise _error(
            "path.unsupported_filesystem",
            "Required no-follow primitives are unavailable.",
        )
    return os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW


def _posix_file_flags() -> int:
    if any(not hasattr(os, name) for name in ("O_CLOEXEC", "O_NOFOLLOW")):
        raise _error(
            "path.unsupported_filesystem",
            "Required no-follow primitives are unavailable.",
        )
    return os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0)


def _identity(status: os.stat_result) -> FileIdentity:
    return status.st_dev, status.st_ino


@dataclass(frozen=True, slots=True)
class _WindowsHandleStatus:
    identity: FileIdentity
    attributes: int
    size: int
    link_count: int
    creation_time: int
    last_write_time: int
    change_time: int

    @property
    def is_directory(self) -> bool:
        return bool(self.attributes & 0x00000010)

    @property
    def is_reparse(self) -> bool:
        return bool(self.attributes & 0x00000400)

    @property
    def fingerprint(self) -> tuple[int, ...]:
        return (
            self.attributes,
            self.size,
            self.link_count,
            self.creation_time,
            self.last_write_time,
            self.change_time,
        )


def _windows_dll(name: str) -> Any:
    loader = getattr(ctypes, "WinDLL", None)
    if loader is None:
        raise _error(
            "path.unsupported_filesystem", "Windows native APIs are unavailable."
        )
    return loader(name, use_last_error=True)


def _windows_error(number: int) -> OSError:
    factory = getattr(ctypes, "WinError", None)
    if factory is None:
        return OSError(number, "Windows filesystem operation failed")
    return cast(OSError, factory(number))


def _windows_last_error() -> int:
    reader = getattr(ctypes, "get_last_error", None)
    return int(reader()) if reader is not None else errno.EIO


def _windows_close(handle: int) -> None:
    from ctypes import wintypes

    kernel32 = _windows_dll("kernel32")
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    if not kernel32.CloseHandle(handle):
        raise _windows_error(_windows_last_error())


def _windows_local_free(pointer: Any) -> None:
    from ctypes import wintypes

    kernel32 = _windows_dll("kernel32")
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL
    argument = (
        wintypes.HLOCAL(pointer)
        if isinstance(pointer, int)
        else ctypes.cast(pointer, wintypes.HLOCAL)
    )
    if kernel32.LocalFree(argument):
        raise _windows_error(_windows_last_error())


def _windows_duplicate(handle: int) -> int:
    from ctypes import wintypes

    kernel32 = _windows_dll("kernel32")
    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.DuplicateHandle.argtypes = [
        wintypes.HANDLE,
        wintypes.HANDLE,
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    ]
    kernel32.DuplicateHandle.restype = wintypes.BOOL
    process = kernel32.GetCurrentProcess()
    duplicate = wintypes.HANDLE()
    if not kernel32.DuplicateHandle(
        process,
        handle,
        process,
        ctypes.byref(duplicate),
        0,
        False,
        0x00000002,  # DUPLICATE_SAME_ACCESS
    ):
        raise _windows_error(_windows_last_error())
    if not duplicate.value:
        raise _error(
            "path.identity_changed", "A Windows handle could not be duplicated."
        )
    return int(duplicate.value)


def _windows_handle_status(handle: int) -> _WindowsHandleStatus:
    from ctypes import wintypes

    class ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("attributes", wintypes.DWORD),
            ("creation_time", wintypes.FILETIME),
            ("last_access_time", wintypes.FILETIME),
            ("last_write_time", wintypes.FILETIME),
            ("volume_serial", wintypes.DWORD),
            ("size_high", wintypes.DWORD),
            ("size_low", wintypes.DWORD),
            ("link_count", wintypes.DWORD),
            ("file_index_high", wintypes.DWORD),
            ("file_index_low", wintypes.DWORD),
        ]

    kernel32 = _windows_dll("kernel32")
    kernel32.GetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(ByHandleFileInformation),
    ]
    kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
    information = ByHandleFileInformation()
    if not kernel32.GetFileInformationByHandle(handle, ctypes.byref(information)):
        raise _windows_error(_windows_last_error())

    class FileBasicInfo(ctypes.Structure):
        _fields_ = [
            ("creation_time", ctypes.c_longlong),
            ("last_access_time", ctypes.c_longlong),
            ("last_write_time", ctypes.c_longlong),
            ("change_time", ctypes.c_longlong),
            ("attributes", wintypes.DWORD),
        ]

    kernel32.GetFileInformationByHandleEx.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL
    basic = FileBasicInfo()
    if not kernel32.GetFileInformationByHandleEx(
        handle, 0, ctypes.byref(basic), ctypes.sizeof(basic)
    ):
        raise _windows_error(_windows_last_error())

    return _WindowsHandleStatus(
        identity=(
            int(information.volume_serial),
            (int(information.file_index_high) << 32) | int(information.file_index_low),
        ),
        attributes=int(basic.attributes),
        size=(int(information.size_high) << 32) | int(information.size_low),
        link_count=int(information.link_count),
        creation_time=int(basic.creation_time),
        last_write_time=int(basic.last_write_time),
        change_time=int(basic.change_time),
    )


def _windows_attribute_tag(handle: int) -> tuple[int, int]:
    from ctypes import wintypes

    class FileAttributeTagInfo(ctypes.Structure):
        _fields_ = [
            ("attributes", wintypes.DWORD),
            ("reparse_tag", wintypes.DWORD),
        ]

    kernel32 = _windows_dll("kernel32")
    kernel32.GetFileInformationByHandleEx.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL
    information = FileAttributeTagInfo()
    if not kernel32.GetFileInformationByHandleEx(
        handle, 9, ctypes.byref(information), ctypes.sizeof(information)
    ):
        raise _windows_error(_windows_last_error())
    return int(information.attributes), int(information.reparse_tag)


def _windows_reject_reparse(handle: int) -> None:
    attributes, tag = _windows_attribute_tag(handle)
    if attributes & 0x00000400 or tag:
        raise _error("path.reparse_point", "Windows reparse points are not allowed.")


def _windows_open_path(path: str) -> int:
    from ctypes import wintypes

    kernel32 = _windows_dll("kernel32")
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    handle = kernel32.CreateFileW(
        path,
        0x00000020 | 0x00000080 | 0x00020000 | 0x00100000,
        0x00000001 | 0x00000002 | 0x00000004,
        None,
        3,  # OPEN_EXISTING
        0x02000000 | 0x00200000,  # BACKUP_SEMANTICS | OPEN_REPARSE_POINT
        None,
    )
    invalid = ctypes.c_void_p(-1).value
    if handle == invalid:
        raise _windows_error(_windows_last_error())
    opened = int(handle)
    try:
        _windows_reject_reparse(opened)
        if not _windows_handle_status(opened).is_directory:
            raise _error("path.outside_root", "The Windows root is not a directory.")
    except BaseException:
        _windows_close(opened)
        raise
    return opened


def _windows_open_child(
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
    from ctypes import wintypes

    if (
        not component
        or component in {".", ".."}
        or "/" in component
        or "\\" in component
        or "\0" in component
    ):
        raise ValueError("Windows child open requires one safe component")

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
        0x00000040,  # OBJ_CASE_INSENSITIVE
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

    ntdll = _windows_dll("ntdll")
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
            2 if create else 1,  # FILE_CREATE or FILE_OPEN
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
        raise _windows_error(number)
    if not result_handle.value:
        raise _error("path.outside_root", "Windows returned no child handle.")
    handle = int(result_handle.value)
    try:
        _windows_reject_reparse(handle)
    except BaseException:
        _windows_close(handle)
        raise
    return handle


def _windows_read(handle: int, *, limit: int) -> bytes:
    from ctypes import wintypes

    kernel32 = _windows_dll("kernel32")
    kernel32.SetFilePointerEx.argtypes = [
        wintypes.HANDLE,
        ctypes.c_longlong,
        ctypes.POINTER(ctypes.c_longlong),
        wintypes.DWORD,
    ]
    kernel32.SetFilePointerEx.restype = wintypes.BOOL
    if not kernel32.SetFilePointerEx(handle, 0, None, 0):
        raise _windows_error(_windows_last_error())
    kernel32.ReadFile.argtypes = [
        wintypes.HANDLE,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    ]
    kernel32.ReadFile.restype = wintypes.BOOL
    chunks: list[bytes] = []
    total = 0
    while total <= limit:
        amount = min(64 * 1024, limit + 1 - total)
        buffer = ctypes.create_string_buffer(amount)
        read = wintypes.DWORD()
        if not kernel32.ReadFile(handle, buffer, amount, ctypes.byref(read), None):
            raise _windows_error(_windows_last_error())
        if read.value == 0:
            break
        chunks.append(bytes(buffer.raw[: read.value]))
        total += int(read.value)
    return b"".join(chunks)


def _windows_write(handle: int, raw: bytes) -> None:
    from ctypes import wintypes

    kernel32 = _windows_dll("kernel32")
    kernel32.WriteFile.argtypes = [
        wintypes.HANDLE,
        wintypes.LPCVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    ]
    kernel32.WriteFile.restype = wintypes.BOOL
    buffer = ctypes.create_string_buffer(raw)
    written = wintypes.DWORD()
    if not kernel32.WriteFile(
        handle, buffer, len(raw), ctypes.byref(written), None
    ) or int(written.value) != len(raw):
        raise _windows_error(_windows_last_error())
    kernel32.FlushFileBuffers.argtypes = [wintypes.HANDLE]
    kernel32.FlushFileBuffers.restype = wintypes.BOOL
    if not kernel32.FlushFileBuffers(handle):
        raise _windows_error(_windows_last_error())


def _windows_local_filesystem(handle: int) -> bool:
    from ctypes import wintypes

    kernel32 = _windows_dll("kernel32")
    kernel32.GetFinalPathNameByHandleW.argtypes = [
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    kernel32.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    needed = int(kernel32.GetFinalPathNameByHandleW(handle, None, 0, 0))
    if needed <= 0:
        return False
    buffer = ctypes.create_unicode_buffer(needed + 1)
    written = int(kernel32.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0))
    if written <= 0 or written >= len(buffer):
        return False
    final_path = buffer.value
    match = re.match(r"^\\\\\?\\([A-Za-z]:)\\", final_path)
    if match is None:
        return False
    volume_root = match.group(1) + "\\"
    kernel32.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetDriveTypeW.restype = wintypes.UINT
    if int(kernel32.GetDriveTypeW(volume_root)) != 3:  # DRIVE_FIXED
        return False
    filesystem = ctypes.create_unicode_buffer(32)
    kernel32.GetVolumeInformationW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPWSTR,
        wintypes.DWORD,
    ]
    kernel32.GetVolumeInformationW.restype = wintypes.BOOL
    if not kernel32.GetVolumeInformationW(
        volume_root,
        None,
        0,
        None,
        None,
        None,
        filesystem,
        len(filesystem),
    ):
        return False
    return _windows_supported_filesystem(
        filesystem.value, drive_type=int(kernel32.GetDriveTypeW(volume_root))
    )


def _windows_supported_filesystem(name: str, *, drive_type: int) -> bool:
    """Accept only the hosted-evidence-backed local Windows filesystem."""

    return drive_type == 3 and name.upper() == "NTFS"


def _open_windows_directory_path(
    raw_path: os.PathLike[str],
) -> tuple[int, tuple[FileIdentity, ...]]:
    raw = os.fspath(raw_path)
    if not isinstance(raw, str) or not raw or "\0" in raw:
        raise _error("path.outside_root", "The Windows root path is invalid.")
    supplied = PureWindowsPath(raw)
    if supplied.drive.startswith("\\") or raw.startswith(("\\\\?\\", "\\\\.\\")):
        raise _error(
            "path.unsupported_filesystem",
            "Windows network/device roots are unsupported.",
        )
    if not supplied.is_absolute() and (supplied.drive or supplied.root):
        raise _error("path.windows_prefix", "The Windows root prefix is invalid.")
    lexical = tuple(part for part in re.split(r"[\\/]", raw) if part)
    if any(component in {".", ".."} for component in lexical):
        raise _error("path.traversal", "Root traversal components are not allowed.")
    absolute = PureWindowsPath(os.path.abspath(raw))
    if (
        not absolute.is_absolute()
        or re.fullmatch(r"(?i)[a-z]:\\", absolute.anchor) is None
    ):
        raise _error("path.windows_prefix", "The Windows root prefix is invalid.")
    initial = absolute.anchor
    components = tuple(absolute.parts[1:])
    current = _windows_open_path(initial)
    ancestry = [_windows_handle_status(current).identity]
    try:
        for component in components:
            child = _windows_open_child(current, component, directory=True)
            _windows_close(current)
            current = child
            ancestry.append(_windows_handle_status(current).identity)
        return current, tuple(ancestry)
    except BaseException:
        _windows_close(current)
        raise


def _windows_current_user_sid() -> str:
    from ctypes import wintypes

    kernel32 = _windows_dll("kernel32")
    advapi32 = _windows_dll("advapi32")
    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(), 0x0008, ctypes.byref(token)
    ):
        raise _windows_error(_windows_last_error())
    try:
        advapi32.GetTokenInformation.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        advapi32.GetTokenInformation.restype = wintypes.BOOL
        needed = wintypes.DWORD()
        advapi32.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
        if needed.value == 0:
            raise _windows_error(_windows_last_error())
        information = ctypes.create_string_buffer(needed.value)
        if not advapi32.GetTokenInformation(
            token, 1, information, needed.value, ctypes.byref(needed)
        ):
            raise _windows_error(_windows_last_error())
        sid = ctypes.c_void_p.from_buffer(information).value
        if not sid:
            raise _error("path.root_unowned", "The Windows user SID is unavailable.")
        converted = wintypes.LPWSTR()
        advapi32.ConvertSidToStringSidW.argtypes = [
            wintypes.LPVOID,
            ctypes.POINTER(wintypes.LPWSTR),
        ]
        advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
        if not advapi32.ConvertSidToStringSidW(sid, ctypes.byref(converted)):
            raise _windows_error(_windows_last_error())
        try:
            value = converted.value
            if value is None:
                raise _error(
                    "path.root_unowned", "The Windows user SID is unavailable."
                )
            return value
        finally:
            _windows_local_free(converted)
    finally:
        if token.value:
            _windows_close(int(token.value))


def _windows_security_sddl(handle: int) -> str:
    from ctypes import wintypes

    advapi32 = _windows_dll("advapi32")
    descriptor = wintypes.LPVOID()
    owner = wintypes.LPVOID()
    group = wintypes.LPVOID()
    dacl = wintypes.LPVOID()
    advapi32.GetSecurityInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.LPVOID),
    ]
    advapi32.GetSecurityInfo.restype = wintypes.DWORD
    number = int(
        advapi32.GetSecurityInfo(
            handle,
            1,
            0x00000001 | 0x00000002 | 0x00000004,
            ctypes.byref(owner),
            ctypes.byref(group),
            ctypes.byref(dacl),
            None,
            ctypes.byref(descriptor),
        )
    )
    if number:
        raise _windows_error(number)
    rendered = wintypes.LPWSTR()
    length = wintypes.ULONG()
    advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(wintypes.ULONG),
    ]
    advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW.restype = (
        wintypes.BOOL
    )
    try:
        if not advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW(
            descriptor,
            1,
            0x00000001 | 0x00000002 | 0x00000004,
            ctypes.byref(rendered),
            ctypes.byref(length),
        ):
            raise _windows_error(_windows_last_error())
        value = rendered.value
        if value is None:
            raise _error("path.root_unowned", "The Windows ACL is unavailable.")
        return value
    finally:
        if rendered:
            _windows_local_free(rendered)
        if descriptor:
            _windows_local_free(descriptor)


WindowsDaclAce = tuple[str, str, str, str, str, str]
WindowsAuthorization = tuple[str, str, str, frozenset[WindowsDaclAce]]


def _windows_canonical_sddl_sid(value: str) -> str:
    """Resolve numeric and abbreviated SDDL principals to one SID spelling."""

    from ctypes import wintypes

    advapi32 = _windows_dll("advapi32")
    descriptor = wintypes.LPVOID()
    size = wintypes.ULONG()
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.ULONG),
    ]
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = (
        wintypes.BOOL
    )
    owner = wintypes.LPVOID()
    defaulted = wintypes.BOOL()
    rendered = wintypes.LPWSTR()
    advapi32.GetSecurityDescriptorOwner.argtypes = [
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.BOOL),
    ]
    advapi32.GetSecurityDescriptorOwner.restype = wintypes.BOOL
    advapi32.ConvertSidToStringSidW.argtypes = [
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.LPWSTR),
    ]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    try:
        if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            f"O:{value}", 1, ctypes.byref(descriptor), ctypes.byref(size)
        ):
            raise _windows_error(_windows_last_error())
        if not advapi32.GetSecurityDescriptorOwner(
            descriptor, ctypes.byref(owner), ctypes.byref(defaulted)
        ):
            raise _windows_error(_windows_last_error())
        if not owner.value:
            raise _error("path.root_unowned", "The Windows SID is unavailable.")
        if not advapi32.ConvertSidToStringSidW(owner, ctypes.byref(rendered)):
            raise _windows_error(_windows_last_error())
        canonical = rendered.value
        if canonical is None:
            raise _error("path.root_unowned", "The Windows SID is unavailable.")
        return canonical
    finally:
        if rendered:
            _windows_local_free(rendered)
        if descriptor:
            _windows_local_free(descriptor)


def _parse_windows_authorization_sddl(sddl: str) -> WindowsAuthorization:
    """Parse exactly the Section 01 supported effective-authorization subset."""

    dacl_start = sddl.find("D:")
    if dacl_start < 0:
        raise ValueError("Windows security descriptor has no DACL")
    principals = sddl[:dacl_start]
    group_start = principals.find("G:", 2)
    if not principals.startswith("O:") or group_start < 0:
        raise ValueError("Windows security descriptor principals are unsupported")
    owner = principals[2:group_start]
    group = principals[group_start + 2 :]
    if not owner or not group or any(marker in group for marker in ("O:", "G:")):
        raise ValueError("Windows security descriptor principal is invalid")
    dacl = sddl[dacl_start:]
    if "(" not in dacl or not dacl.endswith(")"):
        raise ValueError("Windows DACL shape is unsupported")
    header, remainder = dacl[2:].split("(", 1)
    if header.replace("AI", "").replace("AR", "").replace("P", ""):
        raise ValueError("Windows DACL control flags are unsupported")
    body = "(" + remainder
    normalized: list[WindowsDaclAce] = []
    while body:
        if not body.startswith("("):
            raise ValueError("Windows DACL ACE list is malformed")
        end = body.find(")")
        if end < 0:
            raise ValueError("Windows DACL ACE is unterminated")
        fields = body[1:end].split(";")
        if len(fields) != 6 or len(fields[1]) % 2:
            raise ValueError("Windows DACL ACE shape is unsupported")
        if fields[0] != "A":
            raise ValueError("Windows DACL contains a non-allow ACE")
        fields[1] = "".join(
            fields[1][offset : offset + 2]
            for offset in range(0, len(fields[1]), 2)
            if fields[1][offset : offset + 2] != "ID"
        )
        normalized.append(
            (fields[0], fields[1], fields[2], fields[3], fields[4], fields[5])
        )
        body = body[end + 1 :]
    return owner, group, header, frozenset(normalized)


def _windows_private_authorization(handle: int, *, exact: bool) -> bool:
    try:
        owner, _group, dacl_control, aces = _parse_windows_authorization_sddl(
            _windows_security_sddl(handle)
        )
        current_sid = _windows_canonical_sddl_sid(_windows_current_user_sid())
        owner_sid = _windows_canonical_sddl_sid(owner)
        system_sid = _windows_canonical_sddl_sid("SY")
        administrators_sid = _windows_canonical_sddl_sid("BA")
        owner_rights_sid = _windows_canonical_sddl_sid("OW")
    except (ForgeError, OSError, ValueError):
        return False
    if owner_sid != current_sid or (exact and dacl_control != "P"):
        return False
    allowed = {current_sid, system_sid, administrators_sid, owner_rights_sid}
    current_has_full_access = False
    try:
        for _kind, flags, rights, object_guid, inherited_guid, trustee in aces:
            trustee_sid = _windows_canonical_sddl_sid(trustee)
            flag_tokens = {
                flags[offset : offset + 2] for offset in range(0, len(flags), 2)
            }
            if (
                trustee_sid not in allowed
                or object_guid
                or inherited_guid
                or "IO" in flag_tokens
                or not flag_tokens <= {"OI", "CI", "NP"}
                or (exact and flag_tokens)
            ):
                return False
            normalized_rights = rights.lower()
            full_access = rights == "FA" or normalized_rights in {
                "0x1f01ff",
                "0x001f01ff",
            }
            if not full_access:
                return False
            if trustee_sid in {current_sid, owner_rights_sid}:
                current_has_full_access = True
    except (ForgeError, OSError, ValueError):
        return False
    return current_has_full_access


def _windows_private_directory(handle: int, *, exact: bool) -> bool:
    try:
        if not _windows_handle_status(handle).is_directory:
            return False
    except OSError:
        return False
    return _windows_private_authorization(handle, exact=exact)


def _windows_private_security_descriptor() -> int:
    from ctypes import wintypes

    sid = _windows_current_user_sid()
    sddl = f"O:{sid}D:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;FA;;;{sid})"
    advapi32 = _windows_dll("advapi32")
    descriptor = wintypes.LPVOID()
    size = wintypes.ULONG()
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.ULONG),
    ]
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = (
        wintypes.BOOL
    )
    if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl, 1, ctypes.byref(descriptor), ctypes.byref(size)
    ):
        raise _windows_error(_windows_last_error())
    if not descriptor.value:
        raise _error("path.root_unowned", "A private Windows ACL could not be created.")
    return int(descriptor.value)


def _windows_create_private_directory(parent: int, component: str) -> int:
    descriptor = _windows_private_security_descriptor()
    try:
        return _windows_open_child(
            parent,
            component,
            directory=True,
            delete_access=True,
            create=True,
            security_descriptor=descriptor,
        )
    finally:
        _windows_local_free(descriptor)


def _windows_create_private_file(parent: int, component: str) -> int:
    descriptor = _windows_private_security_descriptor()
    try:
        return _windows_open_child(
            parent,
            component,
            directory=False,
            read_data=True,
            write_data=True,
            delete_access=True,
            create=True,
            security_descriptor=descriptor,
        )
    finally:
        _windows_local_free(descriptor)


def _windows_rename_handle(source: int, parent: int, destination: str) -> None:
    from ctypes import wintypes

    if (
        not destination
        or destination in {".", ".."}
        or "/" in destination
        or "\\" in destination
        or "\0" in destination
    ):
        raise ValueError("Windows rename requires one safe component")

    class FileRenameInformation(ctypes.Structure):
        _fields_ = [
            ("replace_if_exists", ctypes.c_ubyte),
            ("root_directory", wintypes.HANDLE),
            ("file_name_length", wintypes.DWORD),
            ("file_name", ctypes.c_wchar * len(destination)),
        ]

    class StatusOrPointer(ctypes.Union):
        _fields_ = [("status", wintypes.LONG), ("pointer", wintypes.LPVOID)]

    class IoStatusBlock(ctypes.Structure):
        _anonymous_ = ("result",)
        _fields_ = [
            ("result", StatusOrPointer),
            ("information", ctypes.c_size_t),
        ]

    information = FileRenameInformation()
    information.replace_if_exists = 0
    information.root_directory = parent
    information.file_name_length = len(destination.encode("utf-16-le"))
    information.file_name = destination
    io_status = IoStatusBlock()
    ntdll = _windows_dll("ntdll")
    ntdll.NtSetInformationFile.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(IoStatusBlock),
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.c_int,
    ]
    ntdll.NtSetInformationFile.restype = wintypes.LONG
    status = int(
        ntdll.NtSetInformationFile(
            source,
            ctypes.byref(io_status),
            ctypes.byref(information),
            ctypes.sizeof(information),
            10,
        )
    )
    if status < 0:
        ntdll.RtlNtStatusToDosError.argtypes = [wintypes.LONG]
        ntdll.RtlNtStatusToDosError.restype = wintypes.ULONG
        number = int(ntdll.RtlNtStatusToDosError(status))
        if number in {80, 183}:
            raise FileExistsError(number, "Windows destination exists")
        raise _windows_error(number)


def _windows_rollback_created_directory(handle: int) -> None:
    from ctypes import wintypes

    class FileDispositionInfo(ctypes.Structure):
        _fields_ = [("delete_file", wintypes.BOOL)]

    kernel32 = _windows_dll("kernel32")
    kernel32.SetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    kernel32.SetFileInformationByHandle.restype = wintypes.BOOL
    information = FileDispositionInfo(True)
    kernel32.SetFileInformationByHandle(
        handle, 4, ctypes.byref(information), ctypes.sizeof(information)
    )


def _darwin_local_filesystem(handle: int) -> bool:
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

    libc = ctypes.CDLL(None, use_errno=True)
    fstatfs = libc.fstatfs
    fstatfs.argtypes = [ctypes.c_int, ctypes.POINTER(StatFs)]
    fstatfs.restype = ctypes.c_int
    information = StatFs()
    if fstatfs(handle, ctypes.byref(information)) != 0:
        return False
    filesystem = bytes(information.type_name).split(b"\0", 1)[0]
    return bool(information.flags & 0x00001000) and filesystem in {b"apfs", b"hfs"}


def _linux_local_filesystem(handle: int) -> bool:
    class Fsid(ctypes.Structure):
        _fields_ = [("value", ctypes.c_int * 2)]

    class StatFs(ctypes.Structure):
        _fields_ = [
            ("type", ctypes.c_long),
            ("block_size", ctypes.c_long),
            ("blocks", ctypes.c_ulong),
            ("blocks_free", ctypes.c_ulong),
            ("blocks_available", ctypes.c_ulong),
            ("files", ctypes.c_ulong),
            ("files_free", ctypes.c_ulong),
            ("filesystem_id", Fsid),
            ("name_length", ctypes.c_long),
            ("fragment_size", ctypes.c_long),
            ("flags", ctypes.c_long),
            ("spare", ctypes.c_long * 4),
        ]

    libc = ctypes.CDLL(None, use_errno=True)
    fstatfs = libc.fstatfs
    fstatfs.argtypes = [ctypes.c_int, ctypes.POINTER(StatFs)]
    fstatfs.restype = ctypes.c_int
    information = StatFs()
    if fstatfs(handle, ctypes.byref(information)) != 0:
        return False
    filesystem = int(information.type) & 0xFFFFFFFF
    if filesystem in _LINUX_NETWORK_FILESYSTEMS:
        return False
    return filesystem in _LINUX_LOCAL_FILESYSTEMS


def _default_filesystem_guard(handle: int) -> bool:
    if os.name == "nt":
        return _windows_local_filesystem(handle)
    if sys.platform == "darwin":
        return _darwin_local_filesystem(handle)
    if sys.platform.startswith("linux"):
        return _linux_local_filesystem(handle)
    return False


def _classify_component(parent: int, component: str, *, linked_code: str) -> ForgeError:
    try:
        metadata = os.stat(component, dir_fd=parent, follow_symlinks=False)
    except OSError:
        return _error("path.outside_root", "A path component cannot be opened safely.")
    if stat.S_ISLNK(metadata.st_mode):
        return _error(linked_code, "A linked path component is not allowed.")
    return _error("path.outside_root", "A path component has an unsupported type.")


def _open_directory_component(
    parent: int,
    component: str,
    *,
    linked_code: str,
    missing_code: str | None = None,
) -> int:
    try:
        return os.open(component, _posix_directory_flags(), dir_fd=parent)
    except OSError as exc:
        if exc.errno == errno.ENOENT and missing_code is not None:
            raise _error(
                missing_code, "A required path component is missing."
            ) from None
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise _classify_component(
                parent, component, linked_code=linked_code
            ) from None
        raise _error(
            "path.outside_root", "A path component cannot be opened safely."
        ) from None


def _path_components(raw_path: os.PathLike[str]) -> tuple[bool, tuple[str, ...]]:
    raw = os.fspath(raw_path)
    if not isinstance(raw, str) or not raw or "\0" in raw:
        raise _error("path.outside_root", "The root path is invalid.")
    supplied = tuple(component for component in raw.split(os.sep) if component)
    if any(component in {".", ".."} for component in supplied):
        raise _error("path.traversal", "Root traversal components are not allowed.")
    absolute_raw = raw if os.path.isabs(raw) else os.path.join(os.getcwd(), raw)
    components = tuple(
        component for component in absolute_raw.split(os.sep) if component
    )
    return True, components


def _open_directory_path(
    raw_path: os.PathLike[str],
) -> tuple[int, tuple[FileIdentity, ...]]:
    if os.name != "posix":
        raise _error(
            "path.unsupported_filesystem",
            "Native path authority is unavailable on this platform.",
        )
    absolute, components = _path_components(raw_path)
    current = os.open("/" if absolute else ".", _posix_directory_flags())
    ancestry = [_identity(os.fstat(current))]
    try:
        for component in components:
            child = _open_directory_component(
                current, component, linked_code="path.linked_ancestor"
            )
            os.close(current)
            current = child
            ancestry.append(_identity(os.fstat(current)))
        return current, tuple(ancestry)
    except BaseException:
        os.close(current)
        raise


def _descriptor_xattr_names(descriptor: int) -> tuple[bytes, ...]:
    """List extended attributes through the already-authorized descriptor."""

    libc = ctypes.CDLL(None, use_errno=True)
    try:
        function = libc.flistxattr
    except AttributeError as exc:
        raise OSError(
            errno.ENOSYS, "descriptor xattr inspection is unavailable"
        ) from exc
    if sys.platform == "darwin":
        function.argtypes = [
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_int,
        ]
        suffix: tuple[object, ...] = (0,)
    elif sys.platform.startswith("linux"):
        function.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t]
        suffix = ()
    else:
        raise OSError(errno.ENOSYS, "descriptor xattr inspection is unavailable")
    function.restype = ctypes.c_ssize_t
    for _attempt in range(3):
        size = int(function(descriptor, None, 0, *suffix))
        if size < 0:
            number = ctypes.get_errno()
            raise OSError(number, os.strerror(number))
        if size == 0:
            return ()
        if size > _XATTR_LIST_LIMIT:
            raise OSError(errno.E2BIG, "extended attribute list exceeds policy")
        buffer = ctypes.create_string_buffer(size)
        result = int(function(descriptor, ctypes.byref(buffer), size, *suffix))
        if result >= 0:
            return tuple(
                sorted(part for part in buffer.raw[:result].split(b"\0") if part)
            )
        number = ctypes.get_errno()
        if number != errno.ERANGE:
            raise OSError(number, os.strerror(number))
    raise OSError(errno.ERANGE, "extended attribute list changed during inspection")


def _macos_descriptor_has_acl(descriptor: int) -> bool:
    """Inspect the macOS extended ACL without resolving a pathname."""

    if sys.platform != "darwin":
        return False
    libc = ctypes.CDLL(None, use_errno=True)
    try:
        get_acl = libc.acl_get_fd_np
    except AttributeError as exc:
        raise OSError(errno.ENOSYS, "descriptor ACL inspection is unavailable") from exc
    get_acl.argtypes = [ctypes.c_int, ctypes.c_int]
    get_acl.restype = ctypes.c_void_p
    ctypes.set_errno(0)
    acl = get_acl(descriptor, 0x00000100)  # ACL_TYPE_EXTENDED
    if not acl:
        number = ctypes.get_errno()
        if number == errno.ENOENT:
            return False
        raise OSError(number or errno.EIO, os.strerror(number or errno.EIO))
    try:
        entry = ctypes.c_void_p()
        libc.acl_get_entry.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        libc.acl_get_entry.restype = ctypes.c_int
        if libc.acl_get_entry(acl, 0, ctypes.byref(entry)) != 0:
            number = ctypes.get_errno()
            raise OSError(number or errno.EIO, os.strerror(number or errno.EIO))
        return entry.value is not None
    finally:
        libc.acl_free.argtypes = [ctypes.c_void_p]
        libc.acl_free.restype = ctypes.c_int
        if libc.acl_free(acl) != 0:
            number = ctypes.get_errno()
            raise OSError(number or errno.EIO, os.strerror(number or errno.EIO))


def _posix_security_metadata_supported(descriptor: int, status: os.stat_result) -> bool:
    names = _descriptor_xattr_names(descriptor)
    if sys.platform.startswith("linux"):
        if any(
            name.startswith((b"security.", b"trusted.", b"system.posix_acl_"))
            for name in names
        ):
            return False
        return all(name == b"user.zagrosi.spike" for name in names)
    if sys.platform == "darwin":
        if any(name in {b"com.apple.macl", b"com.apple.quarantine"} for name in names):
            return False
        if any(
            name not in {b"com.apple.provenance", b"com.zagrosi.spike"}
            for name in names
        ):
            return False
        return not _macos_descriptor_has_acl(descriptor) and not bool(
            getattr(status, "st_flags", 0)
        )
    return False


def _private_directory(descriptor: int, status: os.stat_result, *, exact: bool) -> bool:
    del exact
    try:
        return (
            stat.S_ISDIR(status.st_mode)
            and status.st_uid == os.geteuid()
            and stat.S_IMODE(status.st_mode) == 0o700
            and _posix_security_metadata_supported(descriptor, status)
        )
    except OSError:
        return False


def _posix_status_fingerprint(status: os.stat_result) -> tuple[int, ...]:
    return (
        status.st_mode,
        status.st_uid,
        status.st_gid,
        status.st_size,
        status.st_mtime_ns,
        status.st_ctime_ns,
        status.st_nlink,
        getattr(status, "st_flags", 0),
    )


def _control_record_bytes(
    plugins_identity: FileIdentity, control_identity: FileIdentity
) -> bytes:
    body: dict[str, object] = {
        "authority": "zagrosi-forge-path-authority-v1",
        "control_identity": control_identity,
        "minimum_reader_version": _CONTROL_WRITER_VERSION,
        "plugins_identity": plugins_identity,
        "record_kind": "forge-control-root",
        "schema_digest": _CONTROL_SCHEMA_DIGEST,
        "schema_version": _CONTROL_SCHEMA_VERSION,
        "writer_version": _CONTROL_WRITER_VERSION,
    }
    body["record_digest"] = hashlib.sha256(canonical_json_bytes(body)).hexdigest()
    return canonical_json_bytes(body, final_newline=True)


def _valid_control_record(
    raw: bytes,
    *,
    plugins_identity: FileIdentity,
    control_identity: FileIdentity,
) -> bool:
    try:
        record = decode_persistent_record(
            raw, supported_major=1, reader_version=_CONTROL_WRITER_VERSION
        )
    except ForgeError:
        return False
    expected = {
        "authority": "zagrosi-forge-path-authority-v1",
        "control_identity": control_identity,
        "minimum_reader_version": _CONTROL_WRITER_VERSION,
        "plugins_identity": plugins_identity,
        "record_kind": "forge-control-root",
        "schema_digest": _CONTROL_SCHEMA_DIGEST,
        "schema_version": _CONTROL_SCHEMA_VERSION,
        "writer_version": _CONTROL_WRITER_VERSION,
    }
    return set(record) == {*expected, "record_digest"} and all(
        record.get(key) == value for key, value in expected.items()
    )


def _write_all(descriptor: int, raw: bytes) -> None:
    offset = 0
    while offset < len(raw):
        written = os.write(descriptor, raw[offset:])
        if written <= 0:
            raise OSError(errno.EIO, "short control-record write")
        offset += written


def _create_posix_control_record(
    control: int,
    *,
    plugins_identity: FileIdentity,
    control_identity: FileIdentity,
) -> None:
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW
    descriptor = os.open(_CONTROL_FILE, flags, 0o600, dir_fd=control)
    try:
        raw = _control_record_bytes(plugins_identity, control_identity)
        _write_all(descriptor, raw)
        os.fsync(descriptor)
        status = os.fstat(descriptor)
        if (
            not stat.S_ISREG(status.st_mode)
            or status.st_uid != os.geteuid()
            or stat.S_IMODE(status.st_mode) != 0o600
            or status.st_nlink != 1
            or status.st_dev != control_identity[0]
            or not _posix_security_metadata_supported(descriptor, status)
        ):
            raise _error("path.root_unowned", "The control claim is not private.")
    finally:
        os.close(descriptor)
    os.fsync(control)


def _validate_posix_control_record(
    control: int,
    *,
    plugins_identity: FileIdentity,
    control_identity: FileIdentity,
) -> bool:
    descriptor = -1
    try:
        descriptor = os.open(_CONTROL_FILE, _posix_file_flags(), dir_fd=control)
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_nlink != 1
            or before.st_dev != control_identity[0]
            or before.st_size > _CONTROL_RECORD_LIMIT
            or not _posix_security_metadata_supported(descriptor, before)
        ):
            return False
        raw = os.pread(descriptor, _CONTROL_RECORD_LIMIT + 1, 0)
        after = os.fstat(descriptor)
        return (
            len(raw) <= _CONTROL_RECORD_LIMIT
            and _identity(before) == _identity(after)
            and _posix_status_fingerprint(before) == _posix_status_fingerprint(after)
            and _posix_security_metadata_supported(descriptor, after)
            and len(raw) == before.st_size
            and _valid_control_record(
                raw,
                plugins_identity=plugins_identity,
                control_identity=control_identity,
            )
        )
    except (ForgeError, OSError):
        return False
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _validate_windows_control_record(
    control: int,
    *,
    plugins_identity: FileIdentity,
    control_identity: FileIdentity,
) -> bool:
    descriptor = 0
    try:
        descriptor = _windows_open_child(
            control, _CONTROL_FILE, directory=False, read_data=True
        )
        before = _windows_handle_status(descriptor)
        if (
            before.is_directory
            or before.is_reparse
            or before.link_count != 1
            or before.identity[0] != control_identity[0]
            or before.size > _CONTROL_RECORD_LIMIT
            or not _windows_private_authorization(descriptor, exact=True)
        ):
            return False
        raw = _windows_read(descriptor, limit=_CONTROL_RECORD_LIMIT)
        after = _windows_handle_status(descriptor)
        return (
            len(raw) == before.size
            and before.identity == after.identity
            and before.fingerprint == after.fingerprint
            and _valid_control_record(
                raw,
                plugins_identity=plugins_identity,
                control_identity=control_identity,
            )
        )
    except (ForgeError, OSError):
        return False
    finally:
        if descriptor:
            _windows_close(descriptor)


def _create_windows_control_record(
    control: int,
    *,
    plugins_identity: FileIdentity,
    control_identity: FileIdentity,
) -> None:
    descriptor = _windows_create_private_file(control, _CONTROL_FILE)
    try:
        _windows_write(
            descriptor, _control_record_bytes(plugins_identity, control_identity)
        )
        status = _windows_handle_status(descriptor)
        if (
            status.is_directory
            or status.is_reparse
            or status.link_count != 1
            or status.identity[0] != control_identity[0]
            or not _windows_private_authorization(descriptor, exact=True)
        ):
            raise _error("path.root_unowned", "The control claim is not private.")
    finally:
        _windows_close(descriptor)


def _exclusive_posix_rename(parent: int, source: str, destination: str) -> None:
    for component in (source, destination):
        if (
            not component
            or component in {".", ".."}
            or "/" in component
            or "\0" in component
        ):
            raise ValueError("rename requires one safe component")
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
        flag = 1
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
        flag = 0x00000004
    else:
        raise _error("path.unsupported_filesystem", "Exclusive rename is unavailable.")
    rename.restype = ctypes.c_int
    if (
        rename(
            parent,
            os.fsencode(source),
            parent,
            os.fsencode(destination),
            flag,
        )
        != 0
    ):
        number = ctypes.get_errno()
        if number in {errno.EEXIST, errno.ENOTEMPTY}:
            raise FileExistsError(number, "destination exists")
        raise OSError(number, os.strerror(number))


def _stage_posix_directory(
    parent: int, *, prefix: str
) -> tuple[str, int, FileIdentity]:
    for _attempt in range(16):
        name = f".{prefix}-{secrets.token_hex(16)}.tmp"
        try:
            os.mkdir(name, 0o700, dir_fd=parent)
        except FileExistsError:
            continue
        try:
            named_status = os.stat(name, dir_fd=parent, follow_symlinks=False)
            descriptor = _open_directory_component(
                parent, name, linked_code="path.linked_leaf"
            )
            held_status = os.fstat(descriptor)
            if _identity(named_status) != _identity(
                held_status
            ) or not _private_directory(descriptor, held_status, exact=True):
                os.close(descriptor)
                raise _error(
                    "path.root_unowned", "A staged root changed before publication."
                )
            return name, descriptor, _identity(held_status)
        except BaseException:
            raise
    raise _error("path.outside_root", "A staged root name could not be allocated.")


def _posix_namespace_binds(
    parent: int, component: str, expected_identity: FileIdentity
) -> bool:
    descriptor = -1
    try:
        descriptor = _open_directory_component(
            parent, component, linked_code="path.linked_leaf"
        )
        return _identity(os.fstat(descriptor)) == expected_identity
    except (ForgeError, OSError):
        return False
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _windows_namespace_binds(
    parent: int, component: str, expected_identity: FileIdentity
) -> bool:
    descriptor = 0
    try:
        descriptor = _windows_open_child(parent, component, directory=True)
        return _windows_handle_status(descriptor).identity == expected_identity
    except (ForgeError, OSError):
        return False
    finally:
        if descriptor:
            _windows_close(descriptor)


def _posix_namespace_authority_is_valid(
    home: int,
    plugins: int,
    control: int,
    *,
    home_identity: FileIdentity,
    plugins_identity: FileIdentity,
    control_identity: FileIdentity,
    filesystem_guard: FilesystemGuard,
) -> bool:
    try:
        home_status = os.fstat(home)
        plugins_status = os.fstat(plugins)
        control_status = os.fstat(control)
        return (
            _identity(home_status) == home_identity
            and _identity(plugins_status) == plugins_identity
            and _identity(control_status) == control_identity
            and control_status.st_dev == plugins_status.st_dev
            and _private_directory(home, home_status, exact=False)
            and _private_directory(plugins, plugins_status, exact=False)
            and _private_directory(control, control_status, exact=True)
            and filesystem_guard(home)
            and filesystem_guard(plugins)
            and filesystem_guard(control)
            and _posix_namespace_binds(home, "plugins", plugins_identity)
            and _posix_namespace_binds(plugins, ".zagrosi", control_identity)
            and _validate_posix_control_record(
                control,
                plugins_identity=plugins_identity,
                control_identity=control_identity,
            )
        )
    except (ForgeError, OSError):
        return False


def _windows_namespace_authority_is_valid(
    home: int,
    plugins: int,
    control: int,
    *,
    home_identity: FileIdentity,
    plugins_identity: FileIdentity,
    control_identity: FileIdentity,
    filesystem_guard: FilesystemGuard,
) -> bool:
    try:
        home_status = _windows_handle_status(home)
        plugins_status = _windows_handle_status(plugins)
        control_status = _windows_handle_status(control)
        return (
            home_status.is_directory
            and plugins_status.is_directory
            and control_status.is_directory
            and not home_status.is_reparse
            and not plugins_status.is_reparse
            and not control_status.is_reparse
            and home_status.identity == home_identity
            and plugins_status.identity == plugins_identity
            and control_status.identity == control_identity
            and control_status.identity[0] == plugins_status.identity[0]
            and _windows_private_directory(home, exact=False)
            and _windows_private_directory(plugins, exact=False)
            and _windows_private_directory(control, exact=True)
            and filesystem_guard(home)
            and filesystem_guard(plugins)
            and filesystem_guard(control)
            and _windows_namespace_binds(home, "plugins", plugins_identity)
            and _windows_namespace_binds(plugins, ".zagrosi", control_identity)
            and _validate_windows_control_record(
                control,
                plugins_identity=plugins_identity,
                control_identity=control_identity,
            )
        )
    except (ForgeError, OSError):
        return False


class _NamespaceCapability:
    """Independent retained authority over home/plugins/.zagrosi."""

    __slots__ = (
        "_closed",
        "_control_descriptor",
        "_control_identity",
        "_filesystem_guard",
        "_home_descriptor",
        "_home_identity",
        "_plugins_descriptor",
        "_plugins_identity",
        "_windows",
    )

    def __init__(
        self,
        home_descriptor: int,
        plugins_descriptor: int,
        control_descriptor: int,
        home_identity: FileIdentity,
        plugins_identity: FileIdentity,
        control_identity: FileIdentity,
        filesystem_guard: FilesystemGuard,
        *,
        windows: bool,
        _token: object,
    ) -> None:
        if _token is not _CAPABILITY_TOKEN:
            raise TypeError("namespace capabilities are minted internally")
        self._home_descriptor = home_descriptor
        self._plugins_descriptor = plugins_descriptor
        self._control_descriptor = control_descriptor
        self._home_identity = home_identity
        self._plugins_identity = plugins_identity
        self._control_identity = control_identity
        self._filesystem_guard = filesystem_guard
        self._windows = windows
        self._closed = False

    def _validate_namespace_binding(self) -> bool:
        if self._closed:
            return False
        if self._windows:
            return _windows_namespace_authority_is_valid(
                self._home_descriptor,
                self._plugins_descriptor,
                self._control_descriptor,
                home_identity=self._home_identity,
                plugins_identity=self._plugins_identity,
                control_identity=self._control_identity,
                filesystem_guard=self._filesystem_guard,
            )
        return _posix_namespace_authority_is_valid(
            self._home_descriptor,
            self._plugins_descriptor,
            self._control_descriptor,
            home_identity=self._home_identity,
            plugins_identity=self._plugins_identity,
            control_identity=self._control_identity,
            filesystem_guard=self._filesystem_guard,
        )

    def _require_open(self) -> None:
        if not self._validate_namespace_binding():
            raise _error("path.identity_changed", "The namespace identity changed.")

    def close(self) -> None:
        if self._closed:
            return
        descriptors = (
            self._home_descriptor,
            self._plugins_descriptor,
            self._control_descriptor,
        )
        if self._windows:
            for descriptor in descriptors:
                _windows_close(descriptor)
        else:
            for descriptor in descriptors:
                os.close(descriptor)
        self._closed = True

    def __enter__(self) -> _NamespaceCapability:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def __reduce__(self) -> Never:
        raise TypeError("filesystem capabilities are not serializable")


def _duplicate_namespace_capability(
    home_descriptor: int,
    plugins_descriptor: int,
    control_descriptor: int,
    *,
    home_identity: FileIdentity,
    plugins_identity: FileIdentity,
    control_identity: FileIdentity,
    filesystem_guard: FilesystemGuard,
    windows: bool,
) -> _NamespaceCapability:
    duplicate = _windows_duplicate if windows else os.dup
    close = _windows_close if windows else os.close
    duplicated: list[int] = []
    try:
        for descriptor in (home_descriptor, plugins_descriptor, control_descriptor):
            duplicated.append(duplicate(descriptor))
        capability = _NamespaceCapability(
            duplicated[0],
            duplicated[1],
            duplicated[2],
            home_identity,
            plugins_identity,
            control_identity,
            filesystem_guard,
            windows=windows,
            _token=_CAPABILITY_TOKEN,
        )
        capability._require_open()
        return capability
    except BaseException:
        for descriptor in duplicated:
            close(descriptor)
        raise


def _rollback_created_directory(
    parent: int,
    component: str,
    descriptor: int,
    expected_identity: FileIdentity | None,
) -> None:
    """Preserve POSIX partial state when name-to-handle binding is ambiguous."""

    del parent, component, descriptor, expected_identity


class OpenedRegularFile:
    """An already-opened regular file; reads never consult its former path."""

    __slots__ = (
        "_closed",
        "_descriptor",
        "_fingerprint",
        "_identity",
        "_link_count",
        "_origin",
        "_parent_identity",
        "_reference",
        "_root_identity",
        "_size",
    )

    def __init__(
        self,
        descriptor: int,
        reference: SafeRelativePath,
        status: os.stat_result,
        parent_identity: FileIdentity,
        root_identity: FileIdentity,
        origin: _AuthorityOrigin,
        *,
        _token: object,
    ) -> None:
        if _token is not _CAPABILITY_TOKEN or type(origin) is not _AuthorityOrigin:
            raise TypeError("OpenedRegularFile is created only by SourceRoot")
        self._descriptor = descriptor
        self._reference = reference
        self._identity = _identity(status)
        self._parent_identity = parent_identity
        self._root_identity = root_identity
        self._origin = origin
        self._size = status.st_size
        self._link_count = status.st_nlink
        self._fingerprint = self._status_fingerprint(status)
        self._closed = False

    @staticmethod
    def _status_fingerprint(status: os.stat_result) -> tuple[int, ...]:
        return _posix_status_fingerprint(status)

    @property
    def relative(self) -> SafeRelativePath:
        return self._reference

    @property
    def identity(self) -> FileIdentity:
        return self._identity

    @property
    def parent_identity(self) -> FileIdentity:
        return self._parent_identity

    @property
    def root_identity(self) -> FileIdentity:
        return self._root_identity

    @property
    def size(self) -> int:
        return self._size

    @property
    def link_count(self) -> int:
        return self._link_count

    def _require_unchanged(self) -> os.stat_result:
        if self._closed:
            raise _error(
                "path.identity_changed", "The opened file capability is closed."
            )
        status = os.fstat(self._descriptor)
        if (
            _identity(status) != self._identity
            or not stat.S_ISREG(status.st_mode)
            or status.st_nlink != 1
            or self._status_fingerprint(status) != self._fingerprint
        ):
            raise _error("path.identity_changed", "The opened file identity changed.")
        return status

    def read_bytes(self, *, limit: int) -> bytes:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise ValueError("read limit must be a non-negative integer")
        status = self._require_unchanged()
        if status.st_size > limit:
            raise _error(
                "path.identity_changed",
                "The opened file cannot satisfy the trusted read bound.",
            )
        chunks: list[bytes] = []
        offset = 0
        while offset <= limit:
            chunk = os.pread(
                self._descriptor, min(64 * 1024, limit + 1 - offset), offset
            )
            if not chunk:
                break
            chunks.append(chunk)
            offset += len(chunk)
        rendered = b"".join(chunks)
        if len(rendered) > limit:
            raise _error(
                "path.identity_changed",
                "The opened file cannot satisfy the trusted read bound.",
            )
        self._require_unchanged()
        if len(rendered) != self._size:
            raise _error(
                "path.identity_changed", "The opened file changed during read."
            )
        return rendered

    def _duplicate_descriptor(self) -> int:
        self._require_unchanged()
        return os.dup(self._descriptor)

    def _clone(self) -> OpenedRegularFile:
        descriptor = self._duplicate_descriptor()
        try:
            clone = OpenedRegularFile(
                descriptor,
                self._reference,
                os.fstat(descriptor),
                self._parent_identity,
                self._root_identity,
                self._origin,
                _token=_CAPABILITY_TOKEN,
            )
            clone._identity = self._identity
            clone._size = self._size
            clone._link_count = self._link_count
            clone._fingerprint = self._fingerprint
            clone._require_unchanged()
            return clone
        except BaseException:
            os.close(descriptor)
            raise

    def close(self) -> None:
        if not self._closed:
            os.close(self._descriptor)
            self._closed = True

    def __enter__(self) -> OpenedRegularFile:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def __reduce__(self) -> Never:
        raise TypeError("filesystem capabilities are not serializable")


class SourceRoot:
    __slots__ = (
        "_absolute_ancestry",
        "_closed",
        "_descriptor",
        "_filesystem_guard",
        "_identity",
        "_origin",
    )

    def __init__(
        self,
        descriptor: int,
        ancestry: tuple[FileIdentity, ...],
        filesystem_guard: FilesystemGuard,
        origin: _AuthorityOrigin,
        *,
        _token: object,
    ) -> None:
        if _token is not _CAPABILITY_TOKEN or type(origin) is not _AuthorityOrigin:
            raise TypeError("SourceRoot is created only by PlatformPathAuthority")
        self._descriptor = descriptor
        self._absolute_ancestry = ancestry
        self._filesystem_guard = filesystem_guard
        self._identity = ancestry[-1]
        self._origin = origin
        self._closed = False

    @property
    def identity(self) -> FileIdentity:
        return self._identity

    @property
    def absolute_ancestry(self) -> tuple[FileIdentity, ...]:
        return self._absolute_ancestry

    def _require_open(self) -> None:
        if self._closed or _identity(os.fstat(self._descriptor)) != self._identity:
            raise _error("path.identity_changed", "The source root identity changed.")

    def _require_supported(self, descriptor: int) -> None:
        if not self._filesystem_guard(descriptor):
            raise _error(
                "path.unsupported_filesystem",
                "A source component filesystem is not supported.",
            )

    def open_regular_file(self, reference: SafeRelativePath) -> OpenedRegularFile:
        if not _safe_reference_invariants(reference):
            raise TypeError("open_regular_file requires SafeRelativePath")
        self._require_open()
        parent = os.dup(self._descriptor)
        try:
            for component in reference.components[:-1]:
                child = _open_directory_component(
                    parent,
                    component,
                    linked_code="path.linked_ancestor",
                    missing_code="path.missing",
                )
                try:
                    self._require_supported(child)
                except BaseException:
                    os.close(child)
                    raise
                os.close(parent)
                parent = child
            leaf = reference.components[-1]
            try:
                descriptor = os.open(leaf, _posix_file_flags(), dir_fd=parent)
            except FileNotFoundError:
                raise _error(
                    "path.missing", "The source file does not exist."
                ) from None
            except OSError as exc:
                if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                    raise _classify_component(
                        parent, leaf, linked_code="path.linked_leaf"
                    ) from None
                raise _error(
                    "path.outside_root", "The source file cannot be opened safely."
                ) from None
            status = os.fstat(descriptor)
            try:
                self._require_supported(descriptor)
            except BaseException:
                os.close(descriptor)
                raise
            if not stat.S_ISREG(status.st_mode):
                os.close(descriptor)
                raise _error(
                    "path.outside_root", "The source leaf is not a regular file."
                )
            if status.st_nlink != 1:
                os.close(descriptor)
                raise _error(
                    "path.hardlink", "Hard-linked source files are not allowed."
                )
            return OpenedRegularFile(
                descriptor,
                reference,
                status,
                _identity(os.fstat(parent)),
                self._identity,
                self._origin,
                _token=_CAPABILITY_TOKEN,
            )
        finally:
            os.close(parent)

    def open_snapshot(
        self,
        references: Iterable[SafeRelativePath],
        *,
        already_opened: Iterable[OpenedRegularFile] = (),
    ) -> SourceSnapshot:
        self._require_open()
        supplied = tuple(references)
        if any(not _safe_reference_invariants(reference) for reference in supplied):
            raise TypeError("open_snapshot requires SafeRelativePath values")
        ordered = tuple(sorted(supplied, key=lambda reference: reference.value))
        collision_keys = [reference.collision_key for reference in ordered]
        if len(set(collision_keys)) != len(collision_keys):
            raise _error(
                "path.normalization_collision",
                "References collide under portable normalization.",
            )
        adopted = tuple(already_opened)
        if any(
            not isinstance(item, OpenedRegularFile)
            or item.root_identity != self._identity
            or item._origin is not self._origin
            for item in adopted
        ):
            raise TypeError("already_opened files must belong to this source root")
        adopted_by_reference = {item.relative.value: item for item in adopted}
        if len(adopted_by_reference) != len(adopted) or not set(
            adopted_by_reference
        ) <= {reference.value for reference in ordered}:
            raise _error(
                "path.outside_root", "Adopted files do not match the snapshot."
            )
        opened: dict[str, OpenedRegularFile] = {}
        root_descriptor = os.dup(self._descriptor)
        try:
            for reference in ordered:
                if existing := adopted_by_reference.get(reference.value):
                    opened[reference.value] = existing._clone()
                else:
                    opened[reference.value] = self.open_regular_file(reference)
            return SourceSnapshot(
                opened,
                root_descriptor,
                self._identity,
                self._absolute_ancestry,
                self._origin,
                _token=_CAPABILITY_TOKEN,
            )
        except BaseException:
            os.close(root_descriptor)
            for item in opened.values():
                item.close()
            raise

    def close(self) -> None:
        if not self._closed:
            os.close(self._descriptor)
            self._closed = True

    def __enter__(self) -> SourceRoot:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def __reduce__(self) -> Never:
        raise TypeError("filesystem capabilities are not serializable")


class SourceSnapshot:
    __slots__ = (
        "_absolute_ancestry",
        "_closed",
        "_files",
        "_root_descriptor",
        "_root_identity",
        "_origin",
    )

    def __init__(
        self,
        files: Mapping[str, OpenedRegularFile],
        root_descriptor: int,
        root_identity: FileIdentity,
        absolute_ancestry: tuple[FileIdentity, ...],
        origin: _AuthorityOrigin,
        *,
        _token: object,
    ) -> None:
        if _token is not _CAPABILITY_TOKEN or type(origin) is not _AuthorityOrigin:
            raise TypeError("SourceSnapshot is created only by SourceRoot")
        self._files = MappingProxyType(dict(files))
        self._root_descriptor = root_descriptor
        self._root_identity = root_identity
        self._absolute_ancestry = absolute_ancestry
        self._origin = origin
        self._closed = False

    @property
    def root_identity(self) -> FileIdentity:
        return self._root_identity

    @property
    def absolute_ancestry(self) -> tuple[FileIdentity, ...]:
        return self._absolute_ancestry

    @property
    def references(self) -> tuple[SafeRelativePath, ...]:
        return tuple(item.relative for item in self._files.values())

    def _require_open(self) -> None:
        if (
            self._closed
            or _identity(os.fstat(self._root_descriptor)) != self._root_identity
        ):
            raise _error("path.identity_changed", "The source snapshot is closed.")

    def _duplicate_root_descriptor(self) -> int:
        self._require_open()
        return os.dup(self._root_descriptor)

    def file(self, reference: SafeRelativePath) -> OpenedRegularFile:
        self._require_open()
        if not _safe_reference_invariants(reference):
            raise TypeError("file requires SafeRelativePath")
        try:
            return self._files[reference.value]
        except KeyError as exc:
            raise _error(
                "path.outside_root", "The reference is not in this snapshot."
            ) from exc

    def read_bytes(self, reference: SafeRelativePath, *, limit: int) -> bytes:
        return self.file(reference).read_bytes(limit=limit)

    def close(self) -> None:
        if not self._closed:
            for item in self._files.values():
                item.close()
            os.close(self._root_descriptor)
            self._closed = True

    def __enter__(self) -> SourceSnapshot:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def __reduce__(self) -> Never:
        raise TypeError("filesystem capabilities are not serializable")


class OwnedRoot:
    __slots__ = (
        "_absolute_ancestry",
        "_closed",
        "_control_descriptor",
        "_control_identity",
        "_created",
        "_descriptor",
        "_filesystem_guard",
        "_home_descriptor",
        "_home_identity",
        "_identity",
        "_origin",
    )

    def __init__(
        self,
        descriptor: int,
        control_descriptor: int,
        home_descriptor: int,
        identity: FileIdentity,
        control_identity: FileIdentity,
        home_identity: FileIdentity,
        ancestry: tuple[FileIdentity, ...],
        created: bool,
        filesystem_guard: FilesystemGuard,
        origin: _AuthorityOrigin,
        *,
        _token: object,
    ) -> None:
        if _token is not _CAPABILITY_TOKEN or type(origin) is not _AuthorityOrigin:
            raise TypeError("OwnedRoot is created only by PlatformPathAuthority")
        self._descriptor = descriptor
        self._control_descriptor = control_descriptor
        self._home_descriptor = home_descriptor
        self._identity = identity
        self._control_identity = control_identity
        self._home_identity = home_identity
        self._absolute_ancestry = ancestry
        self._created = created
        self._filesystem_guard = filesystem_guard
        self._origin = origin
        self._closed = False

    @property
    def identity(self) -> FileIdentity:
        return self._identity

    @property
    def absolute_ancestry(self) -> tuple[FileIdentity, ...]:
        return self._absolute_ancestry

    @property
    def control_identity(self) -> FileIdentity:
        return self._control_identity

    @property
    def home_identity(self) -> FileIdentity:
        return self._home_identity

    @property
    def created(self) -> bool:
        return self._created

    def _require_open(self) -> None:
        if not self._namespace_binding_is_valid():
            raise _error("path.identity_changed", "The owned root identity changed.")

    def _namespace_binding_is_valid(self) -> bool:
        if self._closed:
            return False
        return _posix_namespace_authority_is_valid(
            self._home_descriptor,
            self._descriptor,
            self._control_descriptor,
            home_identity=self._home_identity,
            plugins_identity=self._identity,
            control_identity=self._control_identity,
            filesystem_guard=self._filesystem_guard,
        )

    def _validate_namespace_binding(self) -> bool:
        """Validate the live control namespace and its authenticated claim."""

        return self._namespace_binding_is_valid()

    def _validate_control_binding(self) -> bool:
        return self._validate_namespace_binding()

    def _duplicate_descriptor(self) -> int:
        self._require_open()
        return os.dup(self._descriptor)

    def _duplicate_root_descriptor(self) -> int:
        return self._duplicate_descriptor()

    def _duplicate_control_descriptor(self) -> int:
        self._require_open()
        return os.dup(self._control_descriptor)

    def _duplicate_home_descriptor(self) -> int:
        self._require_open()
        return os.dup(self._home_descriptor)

    def _duplicate_namespace_capability(self) -> _NamespaceCapability:
        self._require_open()
        return _duplicate_namespace_capability(
            self._home_descriptor,
            self._descriptor,
            self._control_descriptor,
            home_identity=self._home_identity,
            plugins_identity=self._identity,
            control_identity=self._control_identity,
            filesystem_guard=self._filesystem_guard,
            windows=False,
        )

    def _validate_control_descriptor(self, descriptor: int | None = None) -> bool:
        try:
            if not self._validate_namespace_binding():
                return False
            selected = self._control_descriptor if descriptor is None else descriptor
            status = os.fstat(selected)
            return (
                _identity(status) == self._control_identity
                and status.st_dev == self._identity[0]
                and _private_directory(selected, status, exact=True)
                and self._filesystem_guard(selected)
            )
        except (ForgeError, OSError):
            return False

    def _validate_live_descriptor(self, descriptor: int | None = None) -> bool:
        try:
            if not self._validate_namespace_binding():
                return False
            selected = self._descriptor if descriptor is None else descriptor
            status = os.fstat(selected)
            return (
                stat.S_ISDIR(status.st_mode)
                and _identity(status) == self._identity
                and status.st_dev == self._identity[0]
                and self._filesystem_guard(selected)
                and _private_directory(
                    self._control_descriptor,
                    os.fstat(self._control_descriptor),
                    exact=True,
                )
                and self._filesystem_guard(self._control_descriptor)
                and (
                    descriptor is not None
                    or _private_directory(selected, status, exact=False)
                )
            )
        except (ForgeError, OSError):
            return False

    def close(self) -> None:
        if not self._closed:
            os.close(self._control_descriptor)
            os.close(self._descriptor)
            os.close(self._home_descriptor)
            self._closed = True

    def __enter__(self) -> OwnedRoot:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def __reduce__(self) -> Never:
        raise TypeError("filesystem capabilities are not serializable")


class PathProof:
    __slots__ = (
        "_absolute_ancestry",
        "_closed",
        "_control_descriptor",
        "_control_identity",
        "_descriptor",
        "_existing_depth",
        "_expected_depth",
        "_filesystem_guard",
        "_home_descriptor",
        "_home_identity",
        "_leaf_exists",
        "_leaf_identity",
        "_owned_ancestor_identity",
        "_origin",
        "_relative",
        "_root_descriptor",
    )

    def __init__(
        self,
        descriptor: int,
        root_descriptor: int,
        control_descriptor: int,
        home_descriptor: int,
        control_identity: FileIdentity,
        home_identity: FileIdentity,
        relative: SafeRelativePath,
        expected_depth: int,
        existing_depth: int,
        leaf_exists: bool,
        leaf_identity: FileIdentity | None,
        owned_ancestor_identity: FileIdentity,
        ancestry: tuple[FileIdentity, ...],
        filesystem_guard: FilesystemGuard,
        origin: _AuthorityOrigin,
        *,
        _token: object,
    ) -> None:
        if _token is not _CAPABILITY_TOKEN or type(origin) is not _AuthorityOrigin:
            raise TypeError("PathProof is created only by PlatformPathAuthority")
        self._descriptor = descriptor
        self._root_descriptor = root_descriptor
        self._control_descriptor = control_descriptor
        self._home_descriptor = home_descriptor
        self._control_identity = control_identity
        self._home_identity = home_identity
        self._relative = relative
        self._expected_depth = expected_depth
        self._existing_depth = existing_depth
        self._leaf_exists = leaf_exists
        self._leaf_identity = leaf_identity
        self._owned_ancestor_identity = owned_ancestor_identity
        self._absolute_ancestry = ancestry
        self._filesystem_guard = filesystem_guard
        self._origin = origin
        self._closed = False

    @property
    def relative(self) -> SafeRelativePath:
        return self._relative

    @property
    def expected_depth(self) -> int:
        return self._expected_depth

    @property
    def existing_depth(self) -> int:
        return self._existing_depth

    @property
    def leaf_exists(self) -> bool:
        return self._leaf_exists

    @property
    def leaf_identity(self) -> FileIdentity | None:
        return self._leaf_identity

    @property
    def owned_ancestor_identity(self) -> FileIdentity:
        return self._owned_ancestor_identity

    @property
    def absolute_ancestry(self) -> tuple[FileIdentity, ...]:
        return self._absolute_ancestry

    def _reopen_live_descriptor(self) -> int:
        current = -1
        try:
            if (
                self._closed
                or not _safe_reference_invariants(self._relative)
                or self._existing_depth < 0
                or self._existing_depth > self._expected_depth
                or len(self._relative.components) != self._expected_depth
                or not _posix_namespace_authority_is_valid(
                    self._home_descriptor,
                    self._root_descriptor,
                    self._control_descriptor,
                    home_identity=self._home_identity,
                    plugins_identity=self._owned_ancestor_identity,
                    control_identity=self._control_identity,
                    filesystem_guard=self._filesystem_guard,
                )
            ):
                raise OSError(errno.ESTALE, "path proof authority changed")

            current = _open_directory_component(
                self._home_descriptor,
                "plugins",
                linked_code="path.linked_ancestor",
            )
            root_status = os.fstat(current)
            if _identity(
                root_status
            ) != self._owned_ancestor_identity or not self._filesystem_guard(current):
                raise OSError(errno.ESTALE, "path proof root changed")

            descendant_identities = self._absolute_ancestry[
                len(self._absolute_ancestry) - self._existing_depth :
            ]
            for index, (component, expected_identity) in enumerate(
                zip(
                    self._relative.components[: self._existing_depth],
                    descendant_identities,
                    strict=True,
                ),
                start=1,
            ):
                terminal = index == self._existing_depth
                directory_required = (
                    not terminal or self._existing_depth < self._expected_depth
                )
                child = -1
                try:
                    if directory_required:
                        child = _open_directory_component(
                            current,
                            component,
                            linked_code="path.linked_ancestor",
                        )
                    else:
                        child = os.open(component, _posix_file_flags(), dir_fd=current)
                    status = os.fstat(child)
                    if (
                        _identity(status) != expected_identity
                        or (directory_required and not stat.S_ISDIR(status.st_mode))
                        or not self._filesystem_guard(child)
                    ):
                        raise OSError(errno.ESTALE, "path proof descendant changed")
                except BaseException:
                    if child >= 0:
                        os.close(child)
                    raise
                previous = current
                current = child
                os.close(previous)

            expected_terminal = (
                self._owned_ancestor_identity
                if self._existing_depth == 0
                else descendant_identities[-1]
            )
            if (
                _identity(os.fstat(self._descriptor)) != expected_terminal
                or not self._filesystem_guard(self._descriptor)
                or not _posix_namespace_authority_is_valid(
                    self._home_descriptor,
                    self._root_descriptor,
                    self._control_descriptor,
                    home_identity=self._home_identity,
                    plugins_identity=self._owned_ancestor_identity,
                    control_identity=self._control_identity,
                    filesystem_guard=self._filesystem_guard,
                )
            ):
                raise OSError(errno.ESTALE, "path proof identity changed")
            reopened = current
            current = -1
            return reopened
        except (ForgeError, OSError, ValueError):
            if current >= 0:
                os.close(current)
            raise _error(
                "path.identity_changed", "The path proof identity changed."
            ) from None
        except BaseException:
            if current >= 0:
                os.close(current)
            raise

    def _require_open(self) -> None:
        descriptor = self._reopen_live_descriptor()
        os.close(descriptor)

    def _duplicate_descriptor(self) -> int:
        return self._reopen_live_descriptor()

    def _duplicate_root_descriptor(self) -> int:
        self._require_open()
        return os.dup(self._root_descriptor)

    def _duplicate_home_descriptor(self) -> int:
        self._require_open()
        return os.dup(self._home_descriptor)

    def _duplicate_namespace_capability(self) -> _NamespaceCapability:
        self._require_open()
        return _duplicate_namespace_capability(
            self._home_descriptor,
            self._root_descriptor,
            self._control_descriptor,
            home_identity=self._home_identity,
            plugins_identity=self._owned_ancestor_identity,
            control_identity=self._control_identity,
            filesystem_guard=self._filesystem_guard,
            windows=False,
        )

    def _open_owned_directory_writer(self) -> Result[OwnedDirectoryWriter]:
        """Derive a writer directly from this live path capability."""

        return _mint_owned_directory_writer(self)

    def close(self) -> None:
        if not self._closed:
            os.close(self._descriptor)
            os.close(self._control_descriptor)
            os.close(self._root_descriptor)
            os.close(self._home_descriptor)
            self._closed = True

    def __enter__(self) -> PathProof:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def __reduce__(self) -> Never:
        raise TypeError("filesystem capabilities are not serializable")


class _WindowsOpenedRegularFile(OpenedRegularFile):
    __slots__ = ()

    def __init__(
        self,
        handle: int,
        reference: SafeRelativePath,
        status: _WindowsHandleStatus,
        parent_identity: FileIdentity,
        root_identity: FileIdentity,
        origin: _AuthorityOrigin,
        *,
        _token: object,
    ) -> None:
        if _token is not _CAPABILITY_TOKEN or type(origin) is not _AuthorityOrigin:
            raise TypeError("OpenedRegularFile is created only by SourceRoot")
        self._descriptor = handle
        self._reference = reference
        self._identity = status.identity
        self._parent_identity = parent_identity
        self._root_identity = root_identity
        self._origin = origin
        self._size = status.size
        self._link_count = status.link_count
        self._fingerprint = status.fingerprint
        self._closed = False

    def _require_windows_unchanged(self) -> _WindowsHandleStatus:
        if self._closed:
            raise _error(
                "path.identity_changed", "The opened file capability is closed."
            )
        status = _windows_handle_status(self._descriptor)
        if (
            status.identity != self._identity
            or status.is_directory
            or status.is_reparse
            or status.link_count != 1
            or status.fingerprint != self._fingerprint
        ):
            raise _error("path.identity_changed", "The opened file identity changed.")
        return status

    def read_bytes(self, *, limit: int) -> bytes:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise ValueError("read limit must be a non-negative integer")
        status = self._require_windows_unchanged()
        if status.size > limit:
            raise _error(
                "path.identity_changed",
                "The opened file cannot satisfy the trusted read bound.",
            )
        rendered = _windows_read(self._descriptor, limit=limit)
        if len(rendered) > limit:
            raise _error(
                "path.identity_changed",
                "The opened file cannot satisfy the trusted read bound.",
            )
        self._require_windows_unchanged()
        if len(rendered) != self._size:
            raise _error(
                "path.identity_changed", "The opened file changed during read."
            )
        return rendered

    def _duplicate_descriptor(self) -> int:
        self._require_windows_unchanged()
        return _windows_duplicate(self._descriptor)

    def _clone(self) -> OpenedRegularFile:
        handle = self._duplicate_descriptor()
        try:
            clone = _WindowsOpenedRegularFile(
                handle,
                self._reference,
                _windows_handle_status(handle),
                self._parent_identity,
                self._root_identity,
                self._origin,
                _token=_CAPABILITY_TOKEN,
            )
            clone._identity = self._identity
            clone._size = self._size
            clone._link_count = self._link_count
            clone._fingerprint = self._fingerprint
            clone._require_windows_unchanged()
            return clone
        except BaseException:
            _windows_close(handle)
            raise

    def close(self) -> None:
        if not self._closed:
            _windows_close(self._descriptor)
            self._closed = True


class _WindowsSourceSnapshot(SourceSnapshot):
    __slots__ = ()

    def _require_open(self) -> None:
        if (
            self._closed
            or _windows_handle_status(self._root_descriptor).identity
            != self._root_identity
        ):
            raise _error("path.identity_changed", "The source snapshot is closed.")

    def _duplicate_root_descriptor(self) -> int:
        self._require_open()
        return _windows_duplicate(self._root_descriptor)

    def close(self) -> None:
        if not self._closed:
            for item in self._files.values():
                item.close()
            _windows_close(self._root_descriptor)
            self._closed = True


class _WindowsSourceRoot(SourceRoot):
    __slots__ = ()

    def _require_open(self) -> None:
        if (
            self._closed
            or _windows_handle_status(self._descriptor).identity != self._identity
        ):
            raise _error("path.identity_changed", "The source root identity changed.")

    def open_regular_file(self, reference: SafeRelativePath) -> OpenedRegularFile:
        if not _safe_reference_invariants(reference):
            raise TypeError("open_regular_file requires SafeRelativePath")
        self._require_open()
        parent = _windows_duplicate(self._descriptor)
        try:
            for component in reference.components[:-1]:
                try:
                    child = _windows_open_child(parent, component, directory=True)
                except ForgeError:
                    raise
                except OSError as exc:
                    if isinstance(exc, FileNotFoundError) or getattr(
                        exc, "winerror", None
                    ) in {2, 3}:
                        raise _error(
                            "path.missing", "A required path component is missing."
                        ) from None
                    raise
                if not self._filesystem_guard(child):
                    _windows_close(child)
                    raise _error(
                        "path.unsupported_filesystem",
                        "A source component filesystem is not supported.",
                    )
                _windows_close(parent)
                parent = child
            try:
                handle = _windows_open_child(
                    parent,
                    reference.components[-1],
                    directory=None,
                    read_data=True,
                )
            except ForgeError:
                raise
            except OSError as exc:
                if isinstance(exc, FileNotFoundError) or getattr(
                    exc, "winerror", None
                ) in {2, 3}:
                    raise _error(
                        "path.missing", "The source file does not exist."
                    ) from None
                if getattr(exc, "winerror", None) in {4390, 1920}:
                    raise _error(
                        "path.reparse_point", "Windows reparse points are not allowed."
                    ) from None
                raise _error(
                    "path.outside_root", "The source file cannot be opened safely."
                ) from None
            status = _windows_handle_status(handle)
            if not self._filesystem_guard(handle):
                _windows_close(handle)
                raise _error(
                    "path.unsupported_filesystem",
                    "The source filesystem is not supported.",
                )
            if status.is_reparse:
                _windows_close(handle)
                raise _error(
                    "path.reparse_point", "Windows reparse points are not allowed."
                )
            if status.is_directory:
                _windows_close(handle)
                raise _error(
                    "path.outside_root", "The source leaf is not a regular file."
                )
            if status.link_count != 1:
                _windows_close(handle)
                raise _error(
                    "path.hardlink", "Hard-linked source files are not allowed."
                )
            return _WindowsOpenedRegularFile(
                handle,
                reference,
                status,
                _windows_handle_status(parent).identity,
                self._identity,
                self._origin,
                _token=_CAPABILITY_TOKEN,
            )
        finally:
            _windows_close(parent)

    def open_snapshot(
        self,
        references: Iterable[SafeRelativePath],
        *,
        already_opened: Iterable[OpenedRegularFile] = (),
    ) -> SourceSnapshot:
        self._require_open()
        supplied = tuple(references)
        if any(not _safe_reference_invariants(reference) for reference in supplied):
            raise TypeError("open_snapshot requires SafeRelativePath values")
        ordered = tuple(sorted(supplied, key=lambda reference: reference.value))
        collision_keys = [reference.collision_key for reference in ordered]
        if len(set(collision_keys)) != len(collision_keys):
            raise _error(
                "path.normalization_collision",
                "References collide under portable normalization.",
            )
        adopted = tuple(already_opened)
        if any(
            not isinstance(item, _WindowsOpenedRegularFile)
            or item.root_identity != self._identity
            or item._origin is not self._origin
            for item in adopted
        ):
            raise TypeError("already_opened files must belong to this source root")
        adopted_by_reference = {item.relative.value: item for item in adopted}
        if len(adopted_by_reference) != len(adopted) or not set(
            adopted_by_reference
        ) <= {reference.value for reference in ordered}:
            raise _error(
                "path.outside_root", "Adopted files do not match the snapshot."
            )
        opened: dict[str, OpenedRegularFile] = {}
        root_handle = _windows_duplicate(self._descriptor)
        try:
            for reference in ordered:
                if existing := adopted_by_reference.get(reference.value):
                    opened[reference.value] = existing._clone()
                else:
                    opened[reference.value] = self.open_regular_file(reference)
            return _WindowsSourceSnapshot(
                opened,
                root_handle,
                self._identity,
                self._absolute_ancestry,
                self._origin,
                _token=_CAPABILITY_TOKEN,
            )
        except BaseException:
            _windows_close(root_handle)
            for item in opened.values():
                item.close()
            raise

    def close(self) -> None:
        if not self._closed:
            _windows_close(self._descriptor)
            self._closed = True


class _WindowsOwnedRoot(OwnedRoot):
    __slots__ = ()

    def _require_open(self) -> None:
        if not self._namespace_binding_is_valid():
            raise _error("path.identity_changed", "The owned root identity changed.")

    def _namespace_binding_is_valid(self) -> bool:
        if self._closed:
            return False
        return _windows_namespace_authority_is_valid(
            self._home_descriptor,
            self._descriptor,
            self._control_descriptor,
            home_identity=self._home_identity,
            plugins_identity=self._identity,
            control_identity=self._control_identity,
            filesystem_guard=self._filesystem_guard,
        )

    def _duplicate_descriptor(self) -> int:
        self._require_open()
        return _windows_duplicate(self._descriptor)

    def _duplicate_control_descriptor(self) -> int:
        self._require_open()
        return _windows_duplicate(self._control_descriptor)

    def _duplicate_home_descriptor(self) -> int:
        self._require_open()
        return _windows_duplicate(self._home_descriptor)

    def _duplicate_namespace_capability(self) -> _NamespaceCapability:
        self._require_open()
        return _duplicate_namespace_capability(
            self._home_descriptor,
            self._descriptor,
            self._control_descriptor,
            home_identity=self._home_identity,
            plugins_identity=self._identity,
            control_identity=self._control_identity,
            filesystem_guard=self._filesystem_guard,
            windows=True,
        )

    def _validate_control_descriptor(self, descriptor: int | None = None) -> bool:
        try:
            if not self._validate_namespace_binding():
                return False
            selected = self._control_descriptor if descriptor is None else descriptor
            status = _windows_handle_status(selected)
            return (
                status.identity == self._control_identity
                and status.identity[0] == self._identity[0]
                and _windows_private_directory(selected, exact=True)
                and self._filesystem_guard(selected)
            )
        except (ForgeError, OSError):
            return False

    def _validate_live_descriptor(self, descriptor: int | None = None) -> bool:
        try:
            if not self._validate_namespace_binding():
                return False
            selected = self._descriptor if descriptor is None else descriptor
            status = _windows_handle_status(selected)
            return (
                status.is_directory
                and status.identity == self._identity
                and status.identity[0] == self._identity[0]
                and self._filesystem_guard(selected)
                and _windows_private_directory(self._control_descriptor, exact=True)
                and self._filesystem_guard(self._control_descriptor)
                and (
                    descriptor is not None
                    or _windows_private_directory(selected, exact=False)
                )
            )
        except (ForgeError, OSError):
            return False

    def close(self) -> None:
        if not self._closed:
            _windows_close(self._control_descriptor)
            _windows_close(self._descriptor)
            _windows_close(self._home_descriptor)
            self._closed = True


class _WindowsPathProof(PathProof):
    __slots__ = ()

    def _reopen_live_descriptor(self) -> int:
        current = 0
        try:
            if (
                self._closed
                or not _safe_reference_invariants(self._relative)
                or self._existing_depth < 0
                or self._existing_depth > self._expected_depth
                or len(self._relative.components) != self._expected_depth
                or not _windows_namespace_authority_is_valid(
                    self._home_descriptor,
                    self._root_descriptor,
                    self._control_descriptor,
                    home_identity=self._home_identity,
                    plugins_identity=self._owned_ancestor_identity,
                    control_identity=self._control_identity,
                    filesystem_guard=self._filesystem_guard,
                )
            ):
                raise OSError(errno.ESTALE, "path proof authority changed")

            current = _windows_open_child(
                self._home_descriptor, "plugins", directory=True
            )
            root_status = _windows_handle_status(current)
            if (
                root_status.identity != self._owned_ancestor_identity
                or not self._filesystem_guard(current)
            ):
                raise OSError(errno.ESTALE, "path proof root changed")

            descendant_identities = self._absolute_ancestry[
                len(self._absolute_ancestry) - self._existing_depth :
            ]
            for index, (component, expected_identity) in enumerate(
                zip(
                    self._relative.components[: self._existing_depth],
                    descendant_identities,
                    strict=True,
                ),
                start=1,
            ):
                terminal = index == self._existing_depth
                directory_required = (
                    not terminal or self._existing_depth < self._expected_depth
                )
                child = 0
                try:
                    child = _windows_open_child(
                        current,
                        component,
                        directory=True if directory_required else None,
                    )
                    status = _windows_handle_status(child)
                    if (
                        status.identity != expected_identity
                        or (directory_required and not status.is_directory)
                        or not self._filesystem_guard(child)
                    ):
                        raise OSError(errno.ESTALE, "path proof descendant changed")
                except BaseException:
                    if child:
                        _windows_close(child)
                    raise
                previous = current
                current = child
                _windows_close(previous)

            expected_terminal = (
                self._owned_ancestor_identity
                if self._existing_depth == 0
                else descendant_identities[-1]
            )
            if (
                _windows_handle_status(self._descriptor).identity != expected_terminal
                or not self._filesystem_guard(self._descriptor)
                or not _windows_namespace_authority_is_valid(
                    self._home_descriptor,
                    self._root_descriptor,
                    self._control_descriptor,
                    home_identity=self._home_identity,
                    plugins_identity=self._owned_ancestor_identity,
                    control_identity=self._control_identity,
                    filesystem_guard=self._filesystem_guard,
                )
            ):
                raise OSError(errno.ESTALE, "path proof identity changed")
            reopened = current
            current = 0
            return reopened
        except (ForgeError, OSError, ValueError):
            if current:
                _windows_close(current)
            raise _error(
                "path.identity_changed", "The path proof identity changed."
            ) from None
        except BaseException:
            if current:
                _windows_close(current)
            raise

    def _require_open(self) -> None:
        handle = self._reopen_live_descriptor()
        _windows_close(handle)

    def _duplicate_descriptor(self) -> int:
        return self._reopen_live_descriptor()

    def _duplicate_root_descriptor(self) -> int:
        self._require_open()
        return _windows_duplicate(self._root_descriptor)

    def _duplicate_home_descriptor(self) -> int:
        self._require_open()
        return _windows_duplicate(self._home_descriptor)

    def _duplicate_namespace_capability(self) -> _NamespaceCapability:
        self._require_open()
        return _duplicate_namespace_capability(
            self._home_descriptor,
            self._root_descriptor,
            self._control_descriptor,
            home_identity=self._home_identity,
            plugins_identity=self._owned_ancestor_identity,
            control_identity=self._control_identity,
            filesystem_guard=self._filesystem_guard,
            windows=True,
        )

    def close(self) -> None:
        if not self._closed:
            _windows_close(self._descriptor)
            _windows_close(self._control_descriptor)
            _windows_close(self._root_descriptor)
            _windows_close(self._home_descriptor)
            self._closed = True


class OwnedDirectoryWriter:
    """Sealed handle-relative authority for atomic regular-file publication."""

    __slots__ = (
        "_closed",
        "_descriptor",
        "_filesystem_guard",
        "_identity",
        "_namespace",
        "_origin",
        "_published_collisions",
        "_published_references",
        "_windows",
    )

    def __init__(
        self,
        descriptor: int,
        identity: FileIdentity,
        namespace: _NamespaceCapability,
        filesystem_guard: FilesystemGuard,
        origin: _AuthorityOrigin,
        *,
        windows: bool,
        _token: object,
    ) -> None:
        if (
            _token is not _CAPABILITY_TOKEN
            or type(origin) is not _AuthorityOrigin
            or type(namespace) is not _NamespaceCapability
        ):
            raise TypeError(
                "OwnedDirectoryWriter is created only by filesystem authority"
            )
        self._descriptor = descriptor
        self._identity = identity
        self._namespace = namespace
        self._filesystem_guard = filesystem_guard
        self._origin = origin
        self._windows = windows
        self._published_references: set[str] = set()
        self._published_collisions: set[str] = set()
        self._closed = False

    def _require_open(self) -> None:
        if self._closed:
            raise _error("path.identity_changed", "The destination writer is closed.")
        self._namespace._require_open()
        if self._windows:
            windows_status = _windows_handle_status(self._descriptor)
            valid = (
                windows_status.is_directory
                and not windows_status.is_reparse
                and windows_status.identity == self._identity
                and windows_status.identity[0] == self._identity[0]
                and self._filesystem_guard(self._descriptor)
                and _windows_private_directory(self._descriptor, exact=True)
            )
        else:
            posix_status = os.fstat(self._descriptor)
            valid = (
                _identity(posix_status) == self._identity
                and posix_status.st_dev == self._identity[0]
                and self._filesystem_guard(self._descriptor)
                and _private_directory(self._descriptor, posix_status, exact=True)
            )
        if not valid:
            raise _error("path.identity_changed", "The destination identity changed.")

    def _open_posix_parents(
        self, components: tuple[str, ...]
    ) -> tuple[list[int], list[tuple[int, str, FileIdentity]]]:
        descriptors = [os.dup(self._descriptor)]
        bindings: list[tuple[int, str, FileIdentity]] = []
        try:
            for component in components:
                parent = descriptors[-1]
                created = False
                try:
                    child = _open_directory_component(
                        parent,
                        component,
                        linked_code="path.linked_ancestor",
                        missing_code="path.missing",
                    )
                except ForgeError as exc:
                    if exc.code != "path.missing":
                        raise
                    try:
                        os.mkdir(component, 0o700, dir_fd=parent)
                        created = True
                    except FileExistsError:
                        pass
                    child = _open_directory_component(
                        parent,
                        component,
                        linked_code="path.linked_ancestor",
                    )
                try:
                    if created:
                        os.fchmod(child, 0o700)
                    status = os.fstat(child)
                    identity = _identity(status)
                    if (
                        status.st_dev != self._identity[0]
                        or not self._filesystem_guard(child)
                        or not _private_directory(child, status, exact=True)
                        or not _posix_namespace_binds(parent, component, identity)
                    ):
                        raise _error(
                            "path.outside_root",
                            "A destination ancestor is not privately controlled.",
                        )
                except BaseException:
                    os.close(child)
                    raise
                descriptors.append(child)
                bindings.append((parent, component, identity))
            return descriptors, bindings
        except BaseException:
            for descriptor in reversed(descriptors):
                os.close(descriptor)
            raise

    def _open_windows_parents(
        self, components: tuple[str, ...]
    ) -> tuple[list[int], list[tuple[int, str, FileIdentity]]]:
        handles = [_windows_duplicate(self._descriptor)]
        bindings: list[tuple[int, str, FileIdentity]] = []
        try:
            for component in components:
                parent = handles[-1]
                try:
                    child = _windows_open_child(parent, component, directory=True)
                except OSError as exc:
                    if not isinstance(exc, FileNotFoundError) and getattr(
                        exc, "winerror", None
                    ) not in {2, 3}:
                        raise
                    try:
                        child = _windows_create_private_directory(parent, component)
                    except FileExistsError:
                        child = _windows_open_child(parent, component, directory=True)
                try:
                    status = _windows_handle_status(child)
                    if (
                        not status.is_directory
                        or status.is_reparse
                        or status.identity[0] != self._identity[0]
                        or not self._filesystem_guard(child)
                        or not _windows_private_directory(child, exact=True)
                        or not _windows_namespace_binds(
                            parent, component, status.identity
                        )
                    ):
                        raise _error(
                            "path.outside_root",
                            "A destination ancestor is not privately controlled.",
                        )
                except BaseException:
                    _windows_close(child)
                    raise
                handles.append(child)
                bindings.append((parent, component, status.identity))
            return handles, bindings
        except BaseException:
            for handle in reversed(handles):
                _windows_close(handle)
            raise

    @staticmethod
    def _discard_posix_temporary(parent: int, component: str, descriptor: int) -> None:
        try:
            opened = os.fstat(descriptor)
            named = os.stat(component, dir_fd=parent, follow_symlinks=False)
            if (
                _identity(opened) == _identity(named)
                and stat.S_ISREG(opened.st_mode)
                and stat.S_ISREG(named.st_mode)
            ):
                os.unlink(component, dir_fd=parent)
        except OSError:
            pass

    def _write_posix(self, reference: SafeRelativePath, raw: bytes, mode: int) -> None:
        descriptors, bindings = self._open_posix_parents(reference.components[:-1])
        parent = descriptors[-1]
        temporary = ""
        descriptor = -1
        published = False
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW
            for _attempt in range(16):
                temporary = f".zagrosi-{secrets.token_hex(16)}.tmp"
                try:
                    descriptor = os.open(temporary, flags, 0o600, dir_fd=parent)
                except FileExistsError:
                    continue
                break
            if descriptor < 0:
                raise OSError(errno.EEXIST, "temporary name allocation failed")
            _write_all(descriptor, raw)
            os.fchmod(descriptor, mode)
            os.fsync(descriptor)
            status = os.fstat(descriptor)
            if (
                not stat.S_ISREG(status.st_mode)
                or status.st_uid != os.geteuid()
                or stat.S_IMODE(status.st_mode) != mode
                or status.st_nlink != 1
                or status.st_dev != self._identity[0]
                or not self._filesystem_guard(descriptor)
                or not _posix_security_metadata_supported(descriptor, status)
            ):
                raise OSError(errno.EPERM, "staged file is not privately controlled")
            self._require_open()
            if any(
                not _posix_namespace_binds(parent_handle, component, identity)
                for parent_handle, component, identity in bindings
            ):
                raise _error("path.identity_changed", "A destination ancestor changed.")
            _exclusive_posix_rename(parent, temporary, reference.components[-1])
            published = True
            os.fsync(parent)
        finally:
            if descriptor >= 0:
                if not published:
                    self._discard_posix_temporary(parent, temporary, descriptor)
                os.close(descriptor)
            for parent_descriptor in reversed(descriptors):
                os.close(parent_descriptor)

    def _write_windows(
        self, reference: SafeRelativePath, raw: bytes, mode: int
    ) -> None:
        del mode
        handles, bindings = self._open_windows_parents(reference.components[:-1])
        parent = handles[-1]
        temporary = ""
        handle = 0
        published = False
        try:
            for _attempt in range(16):
                temporary = f".zagrosi-{secrets.token_hex(16)}.tmp"
                try:
                    handle = _windows_create_private_file(parent, temporary)
                except FileExistsError:
                    continue
                break
            if not handle:
                raise OSError(errno.EEXIST, "temporary name allocation failed")
            _windows_write(handle, raw)
            status = _windows_handle_status(handle)
            if (
                status.is_directory
                or status.is_reparse
                or status.link_count != 1
                or status.identity[0] != self._identity[0]
                or not self._filesystem_guard(handle)
                or not _windows_private_authorization(handle, exact=True)
            ):
                raise OSError(errno.EPERM, "staged file is not privately controlled")
            self._require_open()
            if any(
                not _windows_namespace_binds(parent_handle, component, identity)
                for parent_handle, component, identity in bindings
            ):
                raise _error("path.identity_changed", "A destination ancestor changed.")
            _windows_rename_handle(handle, parent, reference.components[-1])
            published = True
        finally:
            if handle:
                if not published:
                    _windows_rollback_created_directory(handle)
                _windows_close(handle)
            for parent_handle in reversed(handles):
                _windows_close(parent_handle)

    def write_regular_file(
        self, reference: SafeRelativePath, raw: bytes, *, mode: int
    ) -> Result[None]:
        """Publish complete bytes beneath this directory without path reopening."""

        if not _safe_reference_invariants(reference):
            raise TypeError("write_regular_file requires SafeRelativePath")
        if type(raw) is not bytes:
            raise TypeError("write_regular_file requires bytes")
        if isinstance(mode, bool) or mode not in {0o644, 0o755}:
            raise ValueError("mode must be 0o644 or 0o755")
        if len(raw) > LIMIT_POLICY.value("bundle_member_bytes"):
            return Result.failure(
                _error("path.write_failed", "The file exceeds the trusted limit.")
            )
        if reference.value in self._published_references:
            return Result.failure(
                _error("path.destination_exists", "The destination already exists.")
            )
        if reference.collision_key in self._published_collisions:
            return Result.failure(
                _error(
                    "path.normalization_collision",
                    "The destination collides under portable normalization.",
                )
            )
        try:
            self._require_open()
            if self._windows:
                self._write_windows(reference, raw, mode)
            else:
                self._write_posix(reference, raw, mode)
        except FileExistsError:
            return Result.failure(
                _error("path.destination_exists", "The destination already exists.")
            )
        except ForgeError as exc:
            return Result.failure(exc)
        except OSError:
            return Result.failure(
                _error("path.write_failed", "The file could not be written safely.")
            )
        self._published_references.add(reference.value)
        self._published_collisions.add(reference.collision_key)
        return Result.success(None)

    def close(self) -> None:
        if self._closed:
            return
        if self._windows:
            _windows_close(self._descriptor)
        else:
            os.close(self._descriptor)
        self._namespace.close()
        self._closed = True

    def __enter__(self) -> OwnedDirectoryWriter:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def __reduce__(self) -> Never:
        raise TypeError("filesystem capabilities are not serializable")


def _mint_owned_directory_writer(proof: PathProof) -> Result[OwnedDirectoryWriter]:
    expected_type = _WindowsPathProof if os.name == "nt" else PathProof
    if type(proof) is not expected_type:
        return Result.failure(
            _error("path.outside_root", "The path-proof platform is invalid.")
        )
    descriptor = 0 if os.name == "nt" else -1
    namespace: _NamespaceCapability | None = None
    try:
        if (
            not proof.leaf_exists
            or proof.existing_depth != proof.expected_depth
            or proof.leaf_identity is None
        ):
            return Result.failure(
                _error("path.outside_root", "The destination directory is absent.")
            )
        descriptor = proof._duplicate_descriptor()
        namespace = proof._duplicate_namespace_capability()
        if os.name == "nt":
            windows_status = _windows_handle_status(descriptor)
            valid = (
                windows_status.is_directory
                and not windows_status.is_reparse
                and windows_status.identity == proof.leaf_identity
                and proof._filesystem_guard(descriptor)
                and _windows_private_directory(descriptor, exact=True)
            )
            identity = windows_status.identity
        else:
            posix_status = os.fstat(descriptor)
            valid = (
                stat.S_ISDIR(posix_status.st_mode)
                and _identity(posix_status) == proof.leaf_identity
                and proof._filesystem_guard(descriptor)
                and _private_directory(descriptor, posix_status, exact=True)
            )
            identity = _identity(posix_status)
        if not valid or not namespace._validate_namespace_binding():
            return Result.failure(
                _error(
                    "path.outside_root",
                    "The destination directory is not privately controlled.",
                )
            )
        writer = OwnedDirectoryWriter(
            descriptor,
            identity,
            namespace,
            proof._filesystem_guard,
            proof._origin,
            windows=os.name == "nt",
            _token=_CAPABILITY_TOKEN,
        )
        descriptor = 0 if os.name == "nt" else -1
        namespace = None
        return Result.success(writer)
    except ForgeError as exc:
        return Result.failure(exc)
    except OSError:
        return Result.failure(
            _error("path.identity_changed", "The destination identity changed.")
        )
    finally:
        if namespace is not None:
            namespace.close()
        if os.name == "nt":
            if descriptor:
                _windows_close(descriptor)
        elif descriptor >= 0:
            os.close(descriptor)


class PlatformPathAuthority:
    """Fail-closed facade over supported local descriptor primitives."""

    __slots__ = ("_filesystem_guard", "_origin")
    _filesystem_guard: FilesystemGuard
    _origin: _AuthorityOrigin

    def __init__(self) -> None:
        self._filesystem_guard = _default_filesystem_guard
        self._origin = _AuthorityOrigin(_token=_CAPABILITY_TOKEN)

    @staticmethod
    def _non_authoritative_for_testing() -> PlatformPathAuthority:
        """Return a negative-only fake that cannot mint filesystem authority."""

        authority = PlatformPathAuthority()
        authority._filesystem_guard = lambda _handle: False
        return authority

    def open_source_root(self, raw_root: os.PathLike[str]) -> SourceRoot:
        if os.name == "nt":
            handle, ancestry = _open_windows_directory_path(raw_root)
            if not self._filesystem_guard(handle):
                _windows_close(handle)
                raise _error(
                    "path.unsupported_filesystem",
                    "The source filesystem is not supported.",
                )
            return _WindowsSourceRoot(
                handle,
                ancestry,
                self._filesystem_guard,
                self._origin,
                _token=_CAPABILITY_TOKEN,
            )
        descriptor, ancestry = _open_directory_path(raw_root)
        if not self._filesystem_guard(descriptor):
            os.close(descriptor)
            raise _error(
                "path.unsupported_filesystem",
                "The source filesystem is not supported.",
            )
        return SourceRoot(
            descriptor,
            ancestry,
            self._filesystem_guard,
            self._origin,
            _token=_CAPABILITY_TOKEN,
        )

    def open_owned_directory_writer(
        self, proof: PathProof
    ) -> Result[OwnedDirectoryWriter]:
        """Mint atomic write authority from a same-origin existing directory."""

        if not isinstance(proof, PathProof) or proof._origin is not self._origin:
            return Result.failure(
                _error(
                    "path.outside_root",
                    "The path proof was minted by another path authority.",
                )
            )
        return proof._open_owned_directory_writer()

    def prove_descendant(
        self,
        root: OwnedRoot,
        relative: SafeRelativePath,
        *,
        expected_depth: int,
        allow_absent_leaf: bool = False,
    ) -> Result[PathProof]:
        if not isinstance(root, OwnedRoot) or root._origin is not self._origin:
            return Result.rejected(
                _error(
                    "path.outside_root",
                    "The owned root was minted by another path authority.",
                )
            )
        try:
            root._require_open()
        except ForgeError as exc:
            return Result.rejected(exc)
        if (
            not _safe_reference_invariants(relative)
            or isinstance(expected_depth, bool)
            or expected_depth < 1
            or len(relative.components) != expected_depth
        ):
            return Result.rejected(
                _error("path.depth", "The descendant depth does not match authority.")
            )
        if os.name == "nt":
            if not isinstance(root, _WindowsOwnedRoot):
                return Result.rejected(
                    _error("path.outside_root", "The owned-root platform is invalid.")
                )
            return self._prove_windows_descendant(
                root,
                relative,
                expected_depth=expected_depth,
                allow_absent_leaf=allow_absent_leaf,
            )
        root_descriptor = os.dup(root._descriptor)
        current = os.dup(root._descriptor)
        control_descriptor = -1
        home_descriptor = -1
        ancestry = list(root.absolute_ancestry)
        existing_depth = 0
        leaf_exists = False
        leaf_identity: FileIdentity | None = None
        absent = False
        try:
            for index, component in enumerate(relative.components, start=1):
                final = index == expected_depth
                if absent:
                    continue
                try:
                    if final:
                        child = os.open(component, _posix_file_flags(), dir_fd=current)
                    else:
                        child = os.open(
                            component, _posix_directory_flags(), dir_fd=current
                        )
                except FileNotFoundError:
                    if not allow_absent_leaf or not final:
                        os.close(current)
                        os.close(root_descriptor)
                        return Result.rejected(
                            _error(
                                "path.outside_root", "The descendant does not exist."
                            )
                        )
                    absent = True
                    continue
                except OSError as exc:
                    if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                        problem = _classify_component(
                            current,
                            component,
                            linked_code="path.linked_ancestor"
                            if not final
                            else "path.linked_leaf",
                        )
                        os.close(current)
                        os.close(root_descriptor)
                        return Result.rejected(problem)
                    os.close(current)
                    os.close(root_descriptor)
                    return Result.rejected(
                        _error(
                            "path.outside_root",
                            "The descendant cannot be opened safely.",
                        )
                    )
                status = os.fstat(child)
                if not self._filesystem_guard(child):
                    os.close(child)
                    os.close(current)
                    os.close(root_descriptor)
                    return Result.rejected(
                        _error(
                            "path.unsupported_filesystem",
                            "A descendant filesystem is not supported.",
                        )
                    )
                if not final and not stat.S_ISDIR(status.st_mode):
                    os.close(child)
                    os.close(current)
                    os.close(root_descriptor)
                    return Result.rejected(
                        _error(
                            "path.outside_root",
                            "A descendant ancestor is not a directory.",
                        )
                    )
                os.close(current)
                current = child
                existing_depth = index
                identity = _identity(status)
                ancestry.append(identity)
                if final:
                    leaf_exists = True
                    leaf_identity = identity
            control_descriptor = root._duplicate_control_descriptor()
            home_descriptor = root._duplicate_home_descriptor()
            return Result.accepted(
                PathProof(
                    current,
                    root_descriptor,
                    control_descriptor,
                    home_descriptor,
                    root.control_identity,
                    root.home_identity,
                    relative,
                    expected_depth,
                    existing_depth,
                    leaf_exists,
                    leaf_identity,
                    root.identity,
                    tuple(ancestry),
                    self._filesystem_guard,
                    self._origin,
                    _token=_CAPABILITY_TOKEN,
                )
            )
        except BaseException:
            os.close(current)
            os.close(root_descriptor)
            if control_descriptor >= 0:
                os.close(control_descriptor)
            if home_descriptor >= 0:
                os.close(home_descriptor)
            raise

    def _prove_windows_descendant(
        self,
        root: _WindowsOwnedRoot,
        relative: SafeRelativePath,
        *,
        expected_depth: int,
        allow_absent_leaf: bool,
    ) -> Result[PathProof]:
        root_handle = _windows_duplicate(root._descriptor)
        current = _windows_duplicate(root._descriptor)
        control_handle = 0
        home_handle = 0
        ancestry = list(root.absolute_ancestry)
        existing_depth = 0
        leaf_exists = False
        leaf_identity: FileIdentity | None = None
        try:
            for index, component in enumerate(relative.components, start=1):
                final = index == expected_depth
                try:
                    child = _windows_open_child(
                        current, component, directory=None if final else True
                    )
                except ForgeError as exc:
                    _windows_close(current)
                    _windows_close(root_handle)
                    return Result.rejected(exc)
                except OSError as exc:
                    missing = isinstance(exc, FileNotFoundError) or getattr(
                        exc, "winerror", None
                    ) in {2, 3}
                    if missing and allow_absent_leaf and final:
                        break
                    _windows_close(current)
                    _windows_close(root_handle)
                    return Result.rejected(
                        _error(
                            "path.outside_root",
                            "The Windows descendant cannot be opened safely.",
                        )
                    )
                status = _windows_handle_status(child)
                if not self._filesystem_guard(child):
                    _windows_close(child)
                    _windows_close(current)
                    _windows_close(root_handle)
                    return Result.rejected(
                        _error(
                            "path.unsupported_filesystem",
                            "A descendant filesystem is not supported.",
                        )
                    )
                if not final and not status.is_directory:
                    _windows_close(child)
                    _windows_close(current)
                    _windows_close(root_handle)
                    return Result.rejected(
                        _error(
                            "path.outside_root",
                            "A descendant ancestor is not a directory.",
                        )
                    )
                _windows_close(current)
                current = child
                existing_depth = index
                ancestry.append(status.identity)
                if final:
                    leaf_exists = True
                    leaf_identity = status.identity
            control_handle = root._duplicate_control_descriptor()
            home_handle = root._duplicate_home_descriptor()
            return Result.accepted(
                _WindowsPathProof(
                    current,
                    root_handle,
                    control_handle,
                    home_handle,
                    root.control_identity,
                    root.home_identity,
                    relative,
                    expected_depth,
                    existing_depth,
                    leaf_exists,
                    leaf_identity,
                    root.identity,
                    tuple(ancestry),
                    self._filesystem_guard,
                    self._origin,
                    _token=_CAPABILITY_TOKEN,
                )
            )
        except BaseException:
            _windows_close(current)
            _windows_close(root_handle)
            if control_handle:
                _windows_close(control_handle)
            if home_handle:
                _windows_close(home_handle)
            raise

    def assert_disjoint(
        self, source: SourceSnapshot, destination: PathProof
    ) -> Result[None]:
        if (
            not isinstance(source, SourceSnapshot)
            or not isinstance(destination, PathProof)
            or source._origin is not self._origin
            or destination._origin is not self._origin
        ):
            return Result.rejected(
                _error(
                    "path.overlap", "Filesystem authority capabilities are required."
                )
            )
        try:
            source._require_open()
            destination._require_open()
        except ForgeError as exc:
            return Result.rejected(exc)
        if (
            source.root_identity in destination.absolute_ancestry
            or destination.owned_ancestor_identity in source.absolute_ancestry
        ):
            return Result.rejected(
                _error("path.overlap", "Source and destination paths overlap.")
            )
        return Result.accepted(None)

    def bootstrap_forge_root(
        self, codex_home: os.PathLike[str], *, runner: RunnerProvenance
    ) -> Result[OwnedRoot]:
        try:
            require_runner_authority(runner, RunnerOperation.MUTATE)
        except ForgeError as exc:
            return Result.rejected(exc)
        if os.name == "nt":
            return self._bootstrap_windows_forge_root(codex_home)
        home = -1
        plugins = -1
        zagrosi = -1
        staged_plugins = -1
        staged_zagrosi = -1
        created_zagrosi = False
        try:
            home, ancestry = _open_directory_path(codex_home)
            home_status = os.fstat(home)
            if not self._filesystem_guard(home):
                return Result.rejected(
                    _error(
                        "path.unsupported_filesystem",
                        "The Codex home filesystem is not supported.",
                    )
                )
            if not _private_directory(home, home_status, exact=False):
                return Result.rejected(
                    _error(
                        "path.root_unowned", "The Codex home is not privately owned."
                    )
                )
            try:
                plugins = _open_directory_component(
                    home, "plugins", linked_code="path.linked_ancestor"
                )
            except ForgeError as exc:
                if exc.code != "path.outside_root":
                    return Result.rejected(exc)
                try:
                    stage_name, staged_plugins, staged_identity = (
                        _stage_posix_directory(home, prefix="plugins")
                    )
                    _exclusive_posix_rename(home, stage_name, "plugins")
                except FileExistsError:
                    if staged_plugins >= 0:
                        os.close(staged_plugins)
                        staged_plugins = -1
                    plugins = _open_directory_component(
                        home, "plugins", linked_code="path.linked_ancestor"
                    )
                else:
                    plugins = staged_plugins
                    staged_plugins = -1
                    if not _posix_namespace_binds(home, "plugins", staged_identity):
                        return Result.rejected(
                            _error(
                                "path.root_unowned",
                                "The plugins publication identity changed.",
                            )
                        )
            plugin_status = os.fstat(plugins)
            plugin_identity = _identity(plugin_status)
            if not self._filesystem_guard(plugins):
                return Result.rejected(
                    _error(
                        "path.unsupported_filesystem",
                        "The plugins filesystem is not supported.",
                    )
                )
            if not _private_directory(plugins, plugin_status, exact=False):
                return Result.rejected(
                    _error(
                        "path.root_unowned", "The plugins root is not privately owned."
                    )
                )
            try:
                zagrosi = _open_directory_component(
                    plugins, ".zagrosi", linked_code="path.linked_leaf"
                )
            except ForgeError as exc:
                if exc.code != "path.outside_root":
                    return Result.rejected(exc)
                stage_name, staged_zagrosi, staged_identity = _stage_posix_directory(
                    plugins, prefix="zagrosi"
                )
                if not self._filesystem_guard(staged_zagrosi):
                    return Result.rejected(
                        _error(
                            "path.unsupported_filesystem",
                            "The staged Forge filesystem is not supported.",
                        )
                    )
                _create_posix_control_record(
                    staged_zagrosi,
                    plugins_identity=plugin_identity,
                    control_identity=staged_identity,
                )
                try:
                    _exclusive_posix_rename(plugins, stage_name, ".zagrosi")
                except FileExistsError:
                    os.close(staged_zagrosi)
                    staged_zagrosi = -1
                    zagrosi = _open_directory_component(
                        plugins, ".zagrosi", linked_code="path.linked_leaf"
                    )
                else:
                    zagrosi = staged_zagrosi
                    staged_zagrosi = -1
                    created_zagrosi = True
                    if not _posix_namespace_binds(plugins, ".zagrosi", staged_identity):
                        return Result.rejected(
                            _error(
                                "path.root_unowned",
                                "The Forge publication identity changed.",
                            )
                        )
            zagrosi_status = os.fstat(zagrosi)
            zagrosi_identity = _identity(zagrosi_status)
            if not self._filesystem_guard(zagrosi):
                return Result.rejected(
                    _error(
                        "path.unsupported_filesystem",
                        "The Forge root filesystem is not supported.",
                    )
                )
            if not _private_directory(zagrosi, zagrosi_status, exact=True):
                return Result.rejected(
                    _error("path.root_unowned", "The Forge root is not restrictive.")
                )
            if not _validate_posix_control_record(
                zagrosi,
                plugins_identity=plugin_identity,
                control_identity=zagrosi_identity,
            ):
                return Result.rejected(
                    _error(
                        "path.root_unowned",
                        "The Forge control claim is invalid or unsupported.",
                    )
                )
            if not _posix_namespace_binds(
                home, "plugins", plugin_identity
            ) or not _posix_namespace_binds(plugins, ".zagrosi", zagrosi_identity):
                return Result.rejected(
                    _error(
                        "path.root_unowned",
                        "The Forge root namespace binding changed.",
                    )
                )
            owned = OwnedRoot(
                plugins,
                zagrosi,
                home,
                plugin_identity,
                zagrosi_identity,
                _identity(home_status),
                (*ancestry, plugin_identity),
                created_zagrosi,
                self._filesystem_guard,
                self._origin,
                _token=_CAPABILITY_TOKEN,
            )
            plugins = -1
            zagrosi = -1
            home = -1
            return Result.accepted(owned)
        except ForgeError as exc:
            return Result.rejected(exc)
        except OSError:
            return Result.rejected(
                _error("path.outside_root", "The Forge root cannot be created safely.")
            )
        finally:
            if staged_zagrosi >= 0:
                os.close(staged_zagrosi)
            if zagrosi >= 0:
                os.close(zagrosi)
            if staged_plugins >= 0:
                os.close(staged_plugins)
            if plugins >= 0:
                os.close(plugins)
            if home >= 0:
                os.close(home)

    def _bootstrap_windows_forge_root(
        self, codex_home: os.PathLike[str]
    ) -> Result[OwnedRoot]:
        home = 0
        plugins = 0
        zagrosi = 0
        staged_zagrosi = 0
        created_plugins = False
        created_zagrosi = False
        completed = False
        try:
            home, ancestry = _open_windows_directory_path(codex_home)
            if not self._filesystem_guard(home):
                return Result.rejected(
                    _error(
                        "path.unsupported_filesystem",
                        "The Codex home filesystem is not supported.",
                    )
                )
            if not _windows_private_directory(home, exact=False):
                return Result.rejected(
                    _error(
                        "path.root_unowned", "The Codex home is not privately owned."
                    )
                )
            try:
                plugins = _windows_open_child(home, "plugins", directory=True)
            except ForgeError as exc:
                return Result.rejected(exc)
            except OSError as exc:
                if not isinstance(exc, FileNotFoundError) and getattr(
                    exc, "winerror", None
                ) not in {2, 3}:
                    return Result.rejected(
                        _error(
                            "path.outside_root",
                            "The plugins root cannot be opened safely.",
                        )
                    )
                try:
                    plugins = _windows_create_private_directory(home, "plugins")
                    created_plugins = True
                except FileExistsError:
                    plugins = _windows_open_child(home, "plugins", directory=True)
            plugin_status = _windows_handle_status(plugins)
            if not self._filesystem_guard(plugins):
                return Result.rejected(
                    _error(
                        "path.unsupported_filesystem",
                        "The plugins filesystem is not supported.",
                    )
                )
            if not _windows_private_directory(plugins, exact=created_plugins):
                return Result.rejected(
                    _error(
                        "path.root_unowned", "The plugins root is not privately owned."
                    )
                )
            try:
                zagrosi = _windows_open_child(plugins, ".zagrosi", directory=True)
            except OSError as exc:
                if not isinstance(exc, FileNotFoundError) and getattr(
                    exc, "winerror", None
                ) not in {2, 3}:
                    return Result.rejected(
                        _error(
                            "path.root_unowned",
                            "The Forge root cannot be opened safely.",
                        )
                    )
                for _attempt in range(16):
                    stage_name = f".zagrosi-{secrets.token_hex(16)}.tmp"
                    try:
                        staged_zagrosi = _windows_create_private_directory(
                            plugins, stage_name
                        )
                    except FileExistsError:
                        continue
                    break
                if not staged_zagrosi:
                    return Result.rejected(
                        _error(
                            "path.outside_root",
                            "A staged Forge root name could not be allocated.",
                        )
                    )
                stage_status = _windows_handle_status(staged_zagrosi)
                _create_windows_control_record(
                    staged_zagrosi,
                    plugins_identity=plugin_status.identity,
                    control_identity=stage_status.identity,
                )
                try:
                    _windows_rename_handle(staged_zagrosi, plugins, ".zagrosi")
                except FileExistsError:
                    _windows_rollback_created_directory(staged_zagrosi)
                    _windows_close(staged_zagrosi)
                    staged_zagrosi = 0
                    zagrosi = _windows_open_child(plugins, ".zagrosi", directory=True)
                else:
                    zagrosi = staged_zagrosi
                    staged_zagrosi = 0
                    created_zagrosi = True
                    if not _windows_namespace_binds(
                        plugins, ".zagrosi", stage_status.identity
                    ):
                        return Result.rejected(
                            _error(
                                "path.root_unowned",
                                "The Forge publication identity changed.",
                            )
                        )
            control_status = _windows_handle_status(zagrosi)
            if not self._filesystem_guard(zagrosi) or not _windows_private_directory(
                zagrosi, exact=True
            ):
                return Result.rejected(
                    _error("path.root_unowned", "The Forge root is not restrictive.")
                )
            if not _validate_windows_control_record(
                zagrosi,
                plugins_identity=plugin_status.identity,
                control_identity=control_status.identity,
            ):
                return Result.rejected(
                    _error(
                        "path.root_unowned",
                        "The Forge control claim is invalid or unsupported.",
                    )
                )
            if not _windows_namespace_binds(
                home, "plugins", plugin_status.identity
            ) or not _windows_namespace_binds(
                plugins, ".zagrosi", control_status.identity
            ):
                return Result.rejected(
                    _error(
                        "path.root_unowned",
                        "The Forge root namespace binding changed.",
                    )
                )
            owned = _WindowsOwnedRoot(
                plugins,
                zagrosi,
                home,
                plugin_status.identity,
                control_status.identity,
                ancestry[-1],
                (*ancestry, plugin_status.identity),
                created_zagrosi,
                self._filesystem_guard,
                self._origin,
                _token=_CAPABILITY_TOKEN,
            )
            plugins = 0
            zagrosi = 0
            home = 0
            completed = True
            return Result.accepted(owned)
        except ForgeError as exc:
            return Result.rejected(exc)
        except OSError:
            return Result.rejected(
                _error("path.outside_root", "The Forge root cannot be created safely.")
            )
        finally:
            if staged_zagrosi:
                _windows_close(staged_zagrosi)
            if zagrosi:
                _windows_close(zagrosi)
            if not completed and created_plugins and plugins:
                _windows_rollback_created_directory(plugins)
            if plugins:
                _windows_close(plugins)
            if home:
                _windows_close(home)
