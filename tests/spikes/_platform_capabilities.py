from __future__ import annotations

from collections.abc import Callable
import ctypes
import errno
import os
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import BinaryIO


Evidence = dict[str, bool]

_READY_TIMEOUT_SECONDS = 10.0
_PROCESS_TIMEOUT_SECONDS = 10.0


class UnsupportedSecurityMetadata(RuntimeError):
    pass


def _platform() -> str:
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "darwin":
        return "macos"
    if os.name == "nt":
        return "windows"
    raise RuntimeError("platform primitive is unsupported")


def _wait_for_file(path: Path, process: subprocess.Popen[bytes] | None = None) -> None:
    deadline = time.monotonic() + _READY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if path.is_file():
            return
        if process is not None and process.poll() is not None:
            raise RuntimeError("probe child exited before readiness")
        time.sleep(0.02)
    raise TimeoutError("probe child readiness timed out")


def _posix_directory_flags() -> int:
    required = ("O_NOFOLLOW", "O_DIRECTORY", "O_CLOEXEC")
    if any(not hasattr(os, name) for name in required):
        raise RuntimeError("required descriptor flags are unavailable")
    return os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY | os.O_CLOEXEC


def _posix_file_flags() -> int:
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_CLOEXEC"):
        raise RuntimeError("required descriptor flags are unavailable")
    return os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC


def _posix_identity(file_descriptor: int) -> tuple[int, int]:
    status = os.fstat(file_descriptor)
    return status.st_dev, status.st_ino


def _windows_api() -> ctypes.WinDLL:
    if os.name != "nt":
        raise RuntimeError("Windows API requested on another platform")
    return ctypes.WinDLL("kernel32", use_last_error=True)


def _close_windows_handle(handle: int) -> None:
    from ctypes import wintypes

    kernel32 = _windows_api()
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    if not kernel32.CloseHandle(handle):
        raise ctypes.WinError(ctypes.get_last_error())


def _open_windows_handle(
    path: Path, *, reject_reparse: bool, directory_traverse: bool = False
) -> int:
    from ctypes import wintypes

    kernel32 = _windows_api()
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
    desired_access = 0x0080 | (0x0020 if directory_traverse else 0)
    handle = kernel32.CreateFileW(
        os.fspath(path),
        desired_access,  # FILE_READ_ATTRIBUTES, optionally FILE_TRAVERSE
        0x00000001 | 0x00000002 | 0x00000004,
        None,
        3,  # OPEN_EXISTING
        0x02000000 | 0x00200000,  # BACKUP_SEMANTICS | OPEN_REPARSE_POINT
        None,
    )
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    if reject_reparse:
        try:
            attributes, tag = _windows_attribute_tag(handle)
            if attributes & 0x00000400 or tag != 0:
                raise OSError(errno.ELOOP, "reparse point rejected")
        except BaseException:
            _close_windows_handle(handle)
            raise
    return int(handle)


def _open_windows_child_handle(
    parent_handle: int,
    component: str,
    *,
    directory: bool,
    delete_access: bool = False,
    read_data: bool = False,
) -> int:
    """Open exactly one child relative to a held directory without following it."""

    from ctypes import wintypes

    if (
        not component
        or component in {".", ".."}
        or "/" in component
        or "\\" in component
        or "\x00" in component
    ):
        raise ValueError("Windows child open requires one ordinary component")

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
        _fields_ = [
            ("result", StatusOrPointer),
            ("Information", ctypes.c_size_t),
        ]

    name_buffer = ctypes.create_unicode_buffer(component)
    name_bytes = component.encode("utf-16-le")
    name = UnicodeString(
        len(name_bytes),
        len(name_bytes) + 2,
        ctypes.cast(name_buffer, wintypes.LPWSTR),
    )
    attributes = ObjectAttributes(
        ctypes.sizeof(ObjectAttributes),
        parent_handle,
        ctypes.pointer(name),
        0x00000040,  # OBJ_CASE_INSENSITIVE
        None,
        None,
    )
    result_handle = wintypes.HANDLE()
    io_status = IoStatusBlock()
    desired_access = 0x00000080 | 0x00100000  # READ_ATTRIBUTES | SYNCHRONIZE
    if directory:
        desired_access |= 0x00000020  # FILE_TRAVERSE
    if read_data:
        desired_access |= 0x00000001  # FILE_READ_DATA
    if delete_access:
        desired_access |= 0x00010000  # DELETE
    create_options = (
        0x00200000  # FILE_OPEN_REPARSE_POINT
        | 0x00000020  # FILE_SYNCHRONOUS_IO_NONALERT
        | (0x00000001 if directory else 0x00000040)
    )

    ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
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
            1,  # FILE_OPEN
            create_options,
            None,
            0,
        )
    )
    if status < 0:
        ntdll.RtlNtStatusToDosError.argtypes = [wintypes.LONG]
        ntdll.RtlNtStatusToDosError.restype = wintypes.ULONG
        raise ctypes.WinError(int(ntdll.RtlNtStatusToDosError(status)))
    handle = int(result_handle.value)
    try:
        attributes_value, tag = _windows_attribute_tag(handle)
        if attributes_value & 0x00000400 or tag != 0:
            raise OSError(errno.ELOOP, "reparse point rejected")
    except BaseException:
        _close_windows_handle(handle)
        raise
    return handle


def _read_windows_handle(handle: int, *, limit: int) -> bytes:
    from ctypes import wintypes

    kernel32 = _windows_api()
    kernel32.ReadFile.argtypes = [
        wintypes.HANDLE,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    ]
    kernel32.ReadFile.restype = wintypes.BOOL
    buffer = ctypes.create_string_buffer(limit + 1)
    read = wintypes.DWORD()
    if not kernel32.ReadFile(handle, buffer, len(buffer), ctypes.byref(read), None):
        raise ctypes.WinError(ctypes.get_last_error())
    if read.value > limit:
        raise RuntimeError("owned-root marker exceeds the probe limit")
    return bytes(buffer.raw[: read.value])


def _rename_windows_handle(
    source_handle: int, parent_handle: int, destination_name: str
) -> None:
    from ctypes import wintypes

    if (
        not destination_name
        or destination_name in {".", ".."}
        or "/" in destination_name
        or "\\" in destination_name
        or "\x00" in destination_name
    ):
        raise ValueError("Windows rename requires one ordinary component")

    class FileRenameInfo(ctypes.Structure):
        _fields_ = [
            ("ReplaceIfExists", ctypes.c_ubyte),
            ("RootDirectory", wintypes.HANDLE),
            ("FileNameLength", wintypes.DWORD),
            ("FileName", ctypes.c_wchar * len(destination_name)),
        ]

    info = FileRenameInfo()
    info.ReplaceIfExists = 0
    info.RootDirectory = parent_handle
    info.FileNameLength = len(destination_name.encode("utf-16-le"))
    info.FileName = destination_name

    class StatusOrPointer(ctypes.Union):
        _fields_ = [("Status", wintypes.LONG), ("Pointer", wintypes.LPVOID)]

    class IoStatusBlock(ctypes.Structure):
        _anonymous_ = ("result",)
        _fields_ = [
            ("result", StatusOrPointer),
            ("Information", ctypes.c_size_t),
        ]

    io_status = IoStatusBlock()
    ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
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
            source_handle,
            ctypes.byref(io_status),
            ctypes.byref(info),
            ctypes.sizeof(info),
            10,  # FileRenameInformation
        )
    )
    if status < 0:
        ntdll.RtlNtStatusToDosError.argtypes = [wintypes.LONG]
        ntdll.RtlNtStatusToDosError.restype = wintypes.ULONG
        error_number = int(ntdll.RtlNtStatusToDosError(status))
        if error_number in {80, 183}:
            raise FileExistsError(errno.EEXIST, "destination exists")
        raise ctypes.WinError(error_number)


def _windows_attribute_tag(handle: int) -> tuple[int, int]:
    from ctypes import wintypes

    class FileAttributeTagInfo(ctypes.Structure):
        _fields_ = [
            ("FileAttributes", wintypes.DWORD),
            ("ReparseTag", wintypes.DWORD),
        ]

    kernel32 = _windows_api()
    kernel32.GetFileInformationByHandleEx.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL
    info = FileAttributeTagInfo()
    if not kernel32.GetFileInformationByHandleEx(
        handle, 9, ctypes.byref(info), ctypes.sizeof(info)
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    return int(info.FileAttributes), int(info.ReparseTag)


def _windows_identity(handle: int) -> tuple[int, int]:
    from ctypes import wintypes

    class ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", wintypes.FILETIME),
            ("ftLastAccessTime", wintypes.FILETIME),
            ("ftLastWriteTime", wintypes.FILETIME),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        ]

    kernel32 = _windows_api()
    kernel32.GetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(ByHandleFileInformation),
    ]
    kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
    info = ByHandleFileInformation()
    if not kernel32.GetFileInformationByHandle(handle, ctypes.byref(info)):
        raise ctypes.WinError(ctypes.get_last_error())
    file_id = (int(info.nFileIndexHigh) << 32) | int(info.nFileIndexLow)
    return int(info.dwVolumeSerialNumber), file_id


def _windows_link_count(handle: int) -> int:
    from ctypes import wintypes

    class ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", wintypes.FILETIME),
            ("ftLastAccessTime", wintypes.FILETIME),
            ("ftLastWriteTime", wintypes.FILETIME),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        ]

    kernel32 = _windows_api()
    kernel32.GetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(ByHandleFileInformation),
    ]
    kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
    info = ByHandleFileInformation()
    if not kernel32.GetFileInformationByHandle(handle, ctypes.byref(info)):
        raise ctypes.WinError(ctypes.get_last_error())
    return int(info.nNumberOfLinks)


def _create_windows_junction(target: Path, junction: Path) -> None:
    completed = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", os.fspath(junction), os.fspath(target)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=_PROCESS_TIMEOUT_SECONDS,
        check=False,
    )
    if completed.returncode != 0 or not junction.exists():
        raise RuntimeError("junction creation failed")


def no_follow_component_opening(root: Path) -> Evidence:
    root.mkdir(parents=True)
    ordinary = root / "ordinary"
    ordinary.mkdir()
    (ordinary / "nested").mkdir()
    outside = root / "outside"
    outside.mkdir()
    (outside / "nested").mkdir()
    final_redirect = root / "final-redirect"
    intermediate_redirect = root / "intermediate-redirect"

    if _platform() == "windows":
        _create_windows_junction(ordinary, final_redirect)
        _create_windows_junction(outside, intermediate_redirect)
        root_handle = _open_windows_handle(
            root, reject_reparse=True, directory_traverse=True
        )
        try:
            ordinary_handle = _open_windows_child_handle(
                root_handle, "ordinary", directory=True
            )
            try:
                attributes, tag = _windows_attribute_tag(ordinary_handle)
                ordinary_opened = bool(attributes & 0x00000010) and tag == 0
                nested_handle = _open_windows_child_handle(
                    ordinary_handle, "nested", directory=True
                )
                try:
                    nested_opened = bool(
                        _windows_attribute_tag(nested_handle)[0] & 0x00000010
                    )
                finally:
                    _close_windows_handle(nested_handle)
            finally:
                _close_windows_handle(ordinary_handle)
            try:
                redirect_handle = _open_windows_child_handle(
                    root_handle, "final-redirect", directory=True
                )
            except OSError as error:
                rejected = error.errno == errno.ELOOP
            else:
                _close_windows_handle(redirect_handle)
                rejected = False
            try:
                intermediate_handle = _open_windows_child_handle(
                    root_handle, "intermediate-redirect", directory=True
                )
            except OSError as error:
                intermediate_rejected = error.errno == errno.ELOOP
            else:
                try:
                    nested_redirect_handle = _open_windows_child_handle(
                        intermediate_handle, "nested", directory=True
                    )
                    _close_windows_handle(nested_redirect_handle)
                    intermediate_rejected = False
                finally:
                    _close_windows_handle(intermediate_handle)
        finally:
            _close_windows_handle(root_handle)
    else:
        final_redirect.symlink_to("ordinary", target_is_directory=True)
        intermediate_redirect.symlink_to("outside", target_is_directory=True)
        root_fd = os.open(root, _posix_directory_flags())
        try:
            ordinary_fd = os.open("ordinary", _posix_directory_flags(), dir_fd=root_fd)
            try:
                ordinary_opened = stat.S_ISDIR(os.fstat(ordinary_fd).st_mode)
                nested_fd = os.open(
                    "nested", _posix_directory_flags(), dir_fd=ordinary_fd
                )
                try:
                    nested_opened = stat.S_ISDIR(os.fstat(nested_fd).st_mode)
                finally:
                    os.close(nested_fd)
            finally:
                os.close(ordinary_fd)
            try:
                redirect_fd = os.open(
                    "final-redirect", _posix_directory_flags(), dir_fd=root_fd
                )
            except OSError as error:
                rejected = error.errno in {errno.ELOOP, errno.ENOTDIR}
            else:
                os.close(redirect_fd)
                rejected = False
            try:
                intermediate_fd = os.open(
                    "intermediate-redirect", _posix_directory_flags(), dir_fd=root_fd
                )
            except OSError as error:
                intermediate_rejected = error.errno in {errno.ELOOP, errno.ENOTDIR}
            else:
                try:
                    nested_redirect_fd = os.open(
                        "nested", _posix_directory_flags(), dir_fd=intermediate_fd
                    )
                    os.close(nested_redirect_fd)
                    intermediate_rejected = False
                finally:
                    os.close(intermediate_fd)
        finally:
            os.close(root_fd)

    return {
        "intermediate_reparse_rejected": intermediate_rejected,
        "nested_component_opened": nested_opened,
        "ordinary_component_opened": ordinary_opened,
        "reparse_component_rejected": rejected,
    }


def stable_parent_and_leaf_identity(root: Path) -> Evidence:
    root.mkdir(parents=True)
    parent = root / "parent"
    parent.mkdir()
    (parent / "leaf").write_bytes(b"original")

    if _platform() == "windows":
        parent_handle = _open_windows_handle(parent, reject_reparse=True)
        try:
            leaf_handle = _open_windows_handle(parent / "leaf", reject_reparse=True)
            try:
                original_parent = _windows_identity(parent_handle)
                original_leaf = _windows_identity(leaf_handle)
                parent.rename(root / "relocated")
                parent.mkdir()
                (parent / "leaf").write_bytes(b"replacement")
                new_parent_handle = _open_windows_handle(parent, reject_reparse=True)
                try:
                    new_leaf_handle = _open_windows_handle(
                        parent / "leaf", reject_reparse=True
                    )
                    try:
                        new_parent = _windows_identity(new_parent_handle)
                        new_leaf = _windows_identity(new_leaf_handle)
                    finally:
                        _close_windows_handle(new_leaf_handle)
                finally:
                    _close_windows_handle(new_parent_handle)
                stable_parent = _windows_identity(parent_handle) == original_parent
                stable_leaf = _windows_identity(leaf_handle) == original_leaf
            finally:
                _close_windows_handle(leaf_handle)
        finally:
            _close_windows_handle(parent_handle)
    else:
        root_fd = os.open(root, _posix_directory_flags())
        parent_fd = os.open("parent", _posix_directory_flags(), dir_fd=root_fd)
        leaf_fd = os.open("leaf", _posix_file_flags(), dir_fd=parent_fd)
        try:
            original_parent = _posix_identity(parent_fd)
            original_leaf = _posix_identity(leaf_fd)
            os.rename("parent", "relocated", src_dir_fd=root_fd, dst_dir_fd=root_fd)
            os.mkdir("parent", dir_fd=root_fd)
            new_parent_fd = os.open("parent", _posix_directory_flags(), dir_fd=root_fd)
            try:
                new_leaf_fd = os.open(
                    "leaf",
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
                    0o600,
                    dir_fd=new_parent_fd,
                )
                os.write(new_leaf_fd, b"replacement")
                os.close(new_leaf_fd)
                new_leaf_fd = os.open("leaf", _posix_file_flags(), dir_fd=new_parent_fd)
                try:
                    new_parent = _posix_identity(new_parent_fd)
                    new_leaf = _posix_identity(new_leaf_fd)
                finally:
                    os.close(new_leaf_fd)
            finally:
                os.close(new_parent_fd)
            stable_parent = _posix_identity(parent_fd) == original_parent
            stable_leaf = _posix_identity(leaf_fd) == original_leaf
        finally:
            os.close(leaf_fd)
            os.close(parent_fd)
            os.close(root_fd)

    return {
        "leaf_handle_stable": stable_leaf,
        "parent_handle_stable": stable_parent,
        "replacement_distinct": new_parent != original_parent
        and new_leaf != original_leaf,
    }


def _exclusive_rename(source: Path, destination: Path) -> None:
    platform = _platform()
    if platform == "linux":
        libc = ctypes.CDLL(None, use_errno=True)
        try:
            renameat2 = libc.renameat2
        except AttributeError as error:
            raise RuntimeError("renameat2 is unavailable") from error
        renameat2.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        renameat2.restype = ctypes.c_int
        result = renameat2(
            -100,
            os.fsencode(source),
            -100,
            os.fsencode(destination),
            1,  # RENAME_NOREPLACE
        )
        if result != 0:
            error_number = ctypes.get_errno()
            if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
                raise FileExistsError(error_number, "destination exists")
            raise OSError(error_number, os.strerror(error_number))
        return

    if platform == "macos":
        libc = ctypes.CDLL(None, use_errno=True)
        try:
            renamex_np = libc.renamex_np
        except AttributeError as error:
            raise RuntimeError("renamex_np is unavailable") from error
        renamex_np.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        renamex_np.restype = ctypes.c_int
        result = renamex_np(
            os.fsencode(source),
            os.fsencode(destination),
            0x00000004,  # RENAME_EXCL
        )
        if result != 0:
            error_number = ctypes.get_errno()
            if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
                raise FileExistsError(error_number, "destination exists")
            raise OSError(error_number, os.strerror(error_number))
        return

    from ctypes import wintypes

    kernel32 = _windows_api()
    kernel32.MoveFileExW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
    kernel32.MoveFileExW.restype = wintypes.BOOL
    if not kernel32.MoveFileExW(
        os.fspath(source), os.fspath(destination), 0x00000008
    ):  # WRITE_THROUGH, deliberately no REPLACE_EXISTING
        error_number = ctypes.get_last_error()
        if error_number in {80, 183}:
            raise FileExistsError(errno.EEXIST, "destination exists")
        raise ctypes.WinError(error_number)


def _exclusive_rename_relative(
    parent_descriptor: int, source_name: str, destination_name: str
) -> None:
    for name in (source_name, destination_name):
        if not name or name in {".", ".."} or "/" in name or "\x00" in name:
            raise ValueError("relative rename requires one ordinary component")

    platform = _platform()
    libc = ctypes.CDLL(None, use_errno=True)
    if platform == "linux":
        try:
            rename = libc.renameat2
        except AttributeError as error:
            raise RuntimeError("renameat2 is unavailable") from error
        rename.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        rename.restype = ctypes.c_int
        result = rename(
            parent_descriptor,
            os.fsencode(source_name),
            parent_descriptor,
            os.fsencode(destination_name),
            1,
        )
    elif platform == "macos":
        try:
            rename = libc.renameatx_np
        except AttributeError as error:
            raise RuntimeError("renameatx_np is unavailable") from error
        rename.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        rename.restype = ctypes.c_int
        result = rename(
            parent_descriptor,
            os.fsencode(source_name),
            parent_descriptor,
            os.fsencode(destination_name),
            0x00000004,
        )
    else:
        raise RuntimeError("relative POSIX rename requested on Windows")
    if result != 0:
        error_number = ctypes.get_errno()
        if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
            raise FileExistsError(error_number, "destination exists")
        raise OSError(error_number, os.strerror(error_number))


def exclusive_absent_directory_publication(root: Path) -> Evidence:
    root.mkdir(parents=True)
    source = root / "staged-a"
    destination = root / "published"
    source.mkdir()
    (source / "marker").write_bytes(b"candidate-a")
    _exclusive_rename(source, destination)
    published = (
        not source.exists() and (destination / "marker").read_bytes() == b"candidate-a"
    )

    rejected_source = root / "staged-b"
    rejected_source.mkdir()
    (rejected_source / "marker").write_bytes(b"candidate-b")
    try:
        _exclusive_rename(rejected_source, destination)
    except FileExistsError:
        rejected = True
    else:
        rejected = False

    return {
        "absent_destination_published": published,
        "existing_destination_rejected": rejected
        and (destination / "marker").read_bytes() == b"candidate-a",
        "rejected_source_preserved": (rejected_source / "marker").read_bytes()
        == b"candidate-b",
    }


def _path_identity(path: Path) -> tuple[int, int]:
    if _platform() == "windows":
        handle = _open_windows_handle(path, reject_reparse=True)
        try:
            return _windows_identity(handle)
        finally:
            _close_windows_handle(handle)
    status = path.stat(follow_symlinks=False)
    return status.st_dev, status.st_ino


def _quarantine_owned_root(
    source: Path,
    destination: Path,
    *,
    expected_owner: bytes,
    before_identity_revalidation: Callable[[], None] | None = None,
) -> None:
    """Quarantine under the platform's explicitly proven writer boundary.

    Windows renames the exact held object, so a namespace swap cannot redirect the
    effect. POSIX rename is inherently name-based: Section 02's risk register puts an
    uncooperative hostile process running as the same account out of scope. The POSIX
    probe therefore requires an euid-owned 0700 parent, holds a kernel lock for
    cooperative concurrency, revalidates immediately before rename, and verifies the
    moved identity afterward. It does not claim same-account hostile-writer safety.
    """

    if source.parent != destination.parent:
        raise ValueError("quarantine rename must remain under one held parent")
    if _platform() == "windows":
        parent_handle = _open_windows_handle(
            source.parent, reject_reparse=True, directory_traverse=True
        )
        source_handle = 0
        try:
            source_handle = _open_windows_child_handle(
                parent_handle, source.name, directory=True, delete_access=True
            )
            source_identity = _windows_identity(source_handle)
            marker_handle = _open_windows_child_handle(
                source_handle, ".forge-owner", directory=False, read_data=True
            )
            try:
                marker_bytes = _read_windows_handle(marker_handle, limit=63)
                marker_has_one_link = _windows_link_count(marker_handle) == 1
            finally:
                _close_windows_handle(marker_handle)
            if marker_bytes != expected_owner or not marker_has_one_link:
                raise PermissionError("owned-root marker mismatch")
            if before_identity_revalidation is not None:
                before_identity_revalidation()
            revalidated_handle = _open_windows_child_handle(
                parent_handle, source.name, directory=True
            )
            try:
                if _windows_identity(revalidated_handle) != source_identity:
                    raise PermissionError("owned-root identity changed")
            finally:
                _close_windows_handle(revalidated_handle)
            _rename_windows_handle(source_handle, parent_handle, destination.name)
            destination_handle = _open_windows_child_handle(
                parent_handle, destination.name, directory=True
            )
            try:
                if _windows_identity(destination_handle) != source_identity:
                    raise RuntimeError("quarantine rename changed root identity")
            finally:
                _close_windows_handle(destination_handle)
        finally:
            if source_handle:
                _close_windows_handle(source_handle)
            _close_windows_handle(parent_handle)
        return

    import fcntl

    parent_descriptor = os.open(source.parent, _posix_directory_flags())
    lock_descriptor = -1
    source_descriptor = -1
    try:
        parent_status = os.fstat(parent_descriptor)
        if (
            parent_status.st_uid != os.geteuid()
            or stat.S_IMODE(parent_status.st_mode) != 0o700
        ):
            raise PermissionError(
                "POSIX quarantine parent must be euid-owned mode 0700"
            )
        lock_descriptor = os.open(
            ".forge-quarantine.lock",
            os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o600,
            dir_fd=parent_descriptor,
        )
        lock_status = os.fstat(lock_descriptor)
        if (
            not stat.S_ISREG(lock_status.st_mode)
            or lock_status.st_uid != os.geteuid()
            or stat.S_IMODE(lock_status.st_mode) != 0o600
            or lock_status.st_nlink != 1
        ):
            raise PermissionError("POSIX quarantine kernel lock is not private")
        fcntl.flock(lock_descriptor, fcntl.LOCK_EX)
        source_descriptor = os.open(
            source.name, _posix_directory_flags(), dir_fd=parent_descriptor
        )
        source_identity = _posix_identity(source_descriptor)
        marker_descriptor = os.open(
            ".forge-owner", _posix_file_flags(), dir_fd=source_descriptor
        )
        try:
            marker_bytes = os.read(marker_descriptor, 64)
            marker_has_one_link = os.fstat(marker_descriptor).st_nlink == 1
        finally:
            os.close(marker_descriptor)
        if marker_bytes != expected_owner or not marker_has_one_link:
            raise PermissionError("owned-root marker mismatch")
        if before_identity_revalidation is not None:
            before_identity_revalidation()
        revalidated_descriptor = os.open(
            source.name, _posix_directory_flags(), dir_fd=parent_descriptor
        )
        try:
            if _posix_identity(revalidated_descriptor) != source_identity:
                raise PermissionError("owned-root identity changed")
        finally:
            os.close(revalidated_descriptor)
        _exclusive_rename_relative(parent_descriptor, source.name, destination.name)
        destination_descriptor = os.open(
            destination.name, _posix_directory_flags(), dir_fd=parent_descriptor
        )
        try:
            if _posix_identity(destination_descriptor) != source_identity:
                raise RuntimeError("quarantine rename changed root identity")
        finally:
            os.close(destination_descriptor)
    finally:
        if source_descriptor >= 0:
            os.close(source_descriptor)
        if lock_descriptor >= 0:
            try:
                fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
            finally:
                os.close(lock_descriptor)
        os.close(parent_descriptor)


def owned_root_quarantine_rename(root: Path) -> Evidence:
    root.mkdir(parents=True)
    platform = _platform()
    if platform != "windows":
        root.chmod(0o700)
    owner = b"forge-owner-v1"

    if platform == "windows":
        # Windows uses exact-object handle rename and does not rely on this POSIX
        # namespace-writer boundary.
        private_parent_writer_boundary = True
    else:
        nonprivate_parent = root / "nonprivate-parent"
        nonprivate_parent.mkdir()
        nonprivate_parent.chmod(0o755)
        nonprivate_source = nonprivate_parent / "managed-root"
        nonprivate_source.mkdir()
        (nonprivate_source / ".forge-owner").write_bytes(owner)
        try:
            _quarantine_owned_root(
                nonprivate_source,
                nonprivate_parent / "quarantine",
                expected_owner=owner,
            )
        except PermissionError:
            private_boundary_rejected = (
                nonprivate_source.is_dir()
                and not (nonprivate_parent / "quarantine").exists()
            )
        else:
            private_boundary_rejected = False
        root_status = root.stat(follow_symlinks=False)
        private_parent_writer_boundary = (
            private_boundary_rejected
            and root_status.st_uid == os.geteuid()
            and stat.S_IMODE(root_status.st_mode) == 0o700
        )

    mismatch = root / "owner-mismatch"
    mismatch.mkdir()
    (mismatch / ".forge-owner").write_bytes(b"other")
    try:
        _quarantine_owned_root(mismatch, root / "wrong", expected_owner=owner)
    except PermissionError:
        mismatch_rejected = mismatch.is_dir() and not (root / "wrong").exists()
    else:
        mismatch_rejected = False

    external_marker = root / "external-owner"
    external_marker.write_bytes(owner)
    marker_link_root = root / "marker-link-root"
    marker_link_root.mkdir()
    os.link(external_marker, marker_link_root / ".forge-owner")
    try:
        _quarantine_owned_root(
            marker_link_root, root / "marker-link-quarantine", expected_owner=owner
        )
    except PermissionError:
        marker_link_rejected = (
            marker_link_root.is_dir() and not (root / "marker-link-quarantine").exists()
        )
    else:
        marker_link_rejected = False

    link_target = root / "link-target"
    link_target.mkdir()
    (link_target / ".forge-owner").write_bytes(owner)
    source_link = root / "source-link"
    if _platform() == "windows":
        _create_windows_junction(link_target, source_link)
    else:
        source_link.symlink_to("link-target", target_is_directory=True)
    try:
        _quarantine_owned_root(
            source_link, root / "source-link-quarantine", expected_owner=owner
        )
    except OSError:
        source_link_rejected = (
            source_link.exists() and not (root / "source-link-quarantine").exists()
        )
    else:
        source_link_rejected = False

    swap_source = root / "swap-source"
    swap_source.mkdir()
    (swap_source / ".forge-owner").write_bytes(owner)
    displaced = root / "swap-displaced"

    def swap_source_before_revalidation() -> None:
        swap_source.rename(displaced)
        swap_source.mkdir()
        (swap_source / ".forge-owner").write_bytes(owner)

    try:
        _quarantine_owned_root(
            swap_source,
            root / "swap-quarantine",
            expected_owner=owner,
            before_identity_revalidation=swap_source_before_revalidation,
        )
    except PermissionError:
        pre_rename_identity_swap_rejected = (
            swap_source.is_dir()
            and displaced.is_dir()
            and not (root / "swap-quarantine").exists()
        )
    else:
        pre_rename_identity_swap_rejected = False

    source = root / "managed-root"
    source.mkdir()
    (source / ".forge-owner").write_bytes(owner)
    (source / "payload").write_bytes(b"managed")
    before = _path_identity(source)

    quarantine = root / "quarantine-0001"
    _quarantine_owned_root(source, quarantine, expected_owner=owner)
    after = _path_identity(quarantine)
    return {
        "identity_preserved": before == after,
        "marker_link_rejected": marker_link_rejected,
        "owner_mismatch_rejected": mismatch_rejected,
        "pre_rename_identity_swap_rejected": pre_rename_identity_swap_rejected,
        "private_parent_writer_boundary": private_parent_writer_boundary,
        "root_moved_exclusively": not source.exists()
        and (quarantine / "payload").read_bytes() == b"managed",
        "source_link_rejected": source_link_rejected,
    }


_WINDOWS_LOCK_CHILD = r"""
import ctypes
import msvcrt
import sys
import time
from ctypes import wintypes

class Overlapped(ctypes.Structure):
    _fields_ = [
        ("Internal", ctypes.c_size_t),
        ("InternalHigh", ctypes.c_size_t),
        ("Offset", wintypes.DWORD),
        ("OffsetHigh", wintypes.DWORD),
        ("hEvent", wintypes.HANDLE),
    ]

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.LockFileEx.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
    wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(Overlapped),
]
kernel32.LockFileEx.restype = wintypes.BOOL
locked = open(sys.argv[1], "r+b", buffering=0)
overlapped = Overlapped()
if not kernel32.LockFileEx(
    msvcrt.get_osfhandle(locked.fileno()), 0x00000002, 0, 1, 0,
    ctypes.byref(overlapped),
):
    raise ctypes.WinError(ctypes.get_last_error())
with open(sys.argv[2], "xb") as ready:
    ready.write(b"ready")
time.sleep(60)
"""


_POSIX_LOCK_CHILD = r"""
import fcntl
import sys
import time

locked = open(sys.argv[1], "r+b", buffering=0)
fcntl.flock(locked.fileno(), fcntl.LOCK_EX)
with open(sys.argv[2], "xb") as ready:
    ready.write(b"ready")
time.sleep(60)
"""


def _try_posix_lock(path: Path) -> BinaryIO | None:
    import fcntl

    locked = path.open("r+b", buffering=0)
    try:
        fcntl.flock(locked.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        locked.close()
        return None
    return locked


def _unlock_posix(locked: BinaryIO) -> None:
    import fcntl

    fcntl.flock(locked.fileno(), fcntl.LOCK_UN)
    locked.close()


def _try_windows_lock(path: Path) -> tuple[BinaryIO, ctypes.Structure] | None:
    import msvcrt
    from ctypes import wintypes

    class Overlapped(ctypes.Structure):
        _fields_ = [
            ("Internal", ctypes.c_size_t),
            ("InternalHigh", ctypes.c_size_t),
            ("Offset", wintypes.DWORD),
            ("OffsetHigh", wintypes.DWORD),
            ("hEvent", wintypes.HANDLE),
        ]

    kernel32 = _windows_api()
    kernel32.LockFileEx.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(Overlapped),
    ]
    kernel32.LockFileEx.restype = wintypes.BOOL
    locked = path.open("r+b", buffering=0)
    overlapped = Overlapped()
    if not kernel32.LockFileEx(
        msvcrt.get_osfhandle(locked.fileno()),
        0x00000001 | 0x00000002,
        0,
        1,
        0,
        ctypes.byref(overlapped),
    ):
        error_number = ctypes.get_last_error()
        locked.close()
        if error_number == 33:  # ERROR_LOCK_VIOLATION
            return None
        raise ctypes.WinError(error_number)
    return locked, overlapped


def _unlock_windows(locked: tuple[BinaryIO, ctypes.Structure]) -> None:
    import msvcrt
    from ctypes import wintypes

    stream, overlapped = locked
    kernel32 = _windows_api()
    kernel32.UnlockFileEx.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
    ]
    kernel32.UnlockFileEx.restype = wintypes.BOOL
    try:
        if not kernel32.UnlockFileEx(
            msvcrt.get_osfhandle(stream.fileno()), 0, 1, 0, ctypes.byref(overlapped)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        stream.close()


def _wait_for_released_lock(
    path: Path, *, windows: bool
) -> tuple[BinaryIO, ctypes.Structure] | BinaryIO:
    deadline = time.monotonic() + _PROCESS_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        acquired = _try_windows_lock(path) if windows else _try_posix_lock(path)
        if acquired is not None:
            return acquired
        time.sleep(0.02)
    raise TimeoutError("kernel lock was not released")


def process_death_lock_release(root: Path) -> Evidence:
    root.mkdir(parents=True)
    lock_path = root / "kernel.lock"
    lock_path.write_bytes(b"0")
    ready = root / "ready"
    windows = _platform() == "windows"
    child_code = _WINDOWS_LOCK_CHILD if windows else _POSIX_LOCK_CHILD
    process = subprocess.Popen(
        [sys.executable, "-c", child_code, os.fspath(lock_path), os.fspath(ready)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=os.environ.copy(),
    )
    acquired_after_death = None
    try:
        _wait_for_file(ready, process)
        contender = (
            _try_windows_lock(lock_path) if windows else _try_posix_lock(lock_path)
        )
        blocked = contender is None
        if contender is not None:
            if windows:
                _unlock_windows(contender)
            else:
                _unlock_posix(contender)
        process.kill()
        process.wait(timeout=_PROCESS_TIMEOUT_SECONDS)
        acquired_after_death = _wait_for_released_lock(lock_path, windows=windows)
        released = True
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=_PROCESS_TIMEOUT_SECONDS)
        if acquired_after_death is not None:
            if windows:
                _unlock_windows(acquired_after_death)
            else:
                _unlock_posix(acquired_after_death)

    return {
        "child_acquired_kernel_lock": ready.read_bytes() == b"ready",
        "contender_blocked_while_alive": blocked,
        "lock_released_after_death": released,
    }


def _list_xattrs(path: Path) -> tuple[bytes, ...]:
    libc = ctypes.CDLL(None, use_errno=True)
    function = libc.listxattr
    if _platform() == "macos":
        function.argtypes = [
            ctypes.c_char_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_int,
        ]
        arguments: tuple[object, ...] = (os.fsencode(path), None, 0, 0)
    else:
        function.argtypes = [ctypes.c_char_p, ctypes.c_void_p, ctypes.c_size_t]
        arguments = (os.fsencode(path), None, 0)
    function.restype = ctypes.c_ssize_t
    size = function(*arguments)
    if size < 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number))
    if size == 0:
        return ()
    buffer = ctypes.create_string_buffer(size)
    if _platform() == "macos":
        result = function(os.fsencode(path), ctypes.byref(buffer), size, 0)
    else:
        result = function(os.fsencode(path), ctypes.byref(buffer), size)
    if result < 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number))
    return tuple(sorted(part for part in buffer.raw[:result].split(b"\0") if part))


def _get_xattr(path: Path, name: bytes) -> bytes:
    libc = ctypes.CDLL(None, use_errno=True)
    function = libc.getxattr
    if _platform() == "macos":
        function.argtypes = [
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_uint32,
            ctypes.c_int,
        ]
        suffix = (0, 0)
    else:
        function.argtypes = [
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
        ]
        suffix = ()
    function.restype = ctypes.c_ssize_t
    size = function(os.fsencode(path), name, None, 0, *suffix)
    if size < 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number))
    buffer = ctypes.create_string_buffer(max(size, 1))
    result = function(os.fsencode(path), name, ctypes.byref(buffer), size, *suffix)
    if result < 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number))
    return bytes(buffer.raw[:result])


def _optional_xattr(path: Path, name: bytes) -> bytes | None:
    return _get_xattr(path, name) if name in _list_xattrs(path) else None


def _set_xattr(path: Path, name: bytes, value: bytes) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    function = libc.setxattr
    value_buffer = ctypes.create_string_buffer(value)
    if _platform() == "macos":
        function.argtypes = [
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_uint32,
            ctypes.c_int,
        ]
        suffix = (0, 0)
    else:
        function.argtypes = [
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_int,
        ]
        suffix = (0,)
    function.restype = ctypes.c_int
    if (
        function(
            os.fsencode(path), name, ctypes.byref(value_buffer), len(value), *suffix
        )
        != 0
    ):
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number))


def _remove_xattr(path: Path, name: bytes) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    function = libc.removexattr
    if _platform() == "macos":
        function.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
        suffix = (0,)
    else:
        function.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
        suffix = ()
    function.restype = ctypes.c_int
    if function(os.fsencode(path), name, *suffix) != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number))


def _macos_has_acl(path: Path) -> bool:
    libc = ctypes.CDLL(None, use_errno=True)
    libc.acl_get_file.argtypes = [ctypes.c_char_p, ctypes.c_int]
    libc.acl_get_file.restype = ctypes.c_void_p
    acl = libc.acl_get_file(os.fsencode(path), 0x00000100)
    if not acl:
        error_number = ctypes.get_errno()
        if error_number == errno.ENOENT:
            return False
        raise OSError(error_number, os.strerror(error_number))
    try:
        entry = ctypes.c_void_p()
        libc.acl_get_entry.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        libc.acl_get_entry.restype = ctypes.c_int
        result = libc.acl_get_entry(acl, 0, ctypes.byref(entry))
        if result != 0:
            error_number = ctypes.get_errno()
            raise OSError(error_number, os.strerror(error_number))
        return entry.value is not None
    finally:
        libc.acl_free.argtypes = [ctypes.c_void_p]
        libc.acl_free.restype = ctypes.c_int
        if libc.acl_free(acl) != 0:
            error_number = ctypes.get_errno()
            raise OSError(error_number, os.strerror(error_number))


def _windows_attributes(path: Path) -> int:
    from ctypes import wintypes

    kernel32 = _windows_api()
    kernel32.GetFileAttributesW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetFileAttributesW.restype = wintypes.DWORD
    attributes = int(kernel32.GetFileAttributesW(os.fspath(path)))
    if attributes == 0xFFFFFFFF:
        raise ctypes.WinError(ctypes.get_last_error())
    return attributes


def _set_windows_attributes(path: Path, attributes: int) -> None:
    from ctypes import wintypes

    kernel32 = _windows_api()
    kernel32.SetFileAttributesW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
    kernel32.SetFileAttributesW.restype = wintypes.BOOL
    if not kernel32.SetFileAttributesW(os.fspath(path), attributes):
        raise ctypes.WinError(ctypes.get_last_error())


def _windows_security_descriptor(path: Path) -> bytes:
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi32.GetFileSecurityW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetFileSecurityW.restype = wintypes.BOOL
    requested = 0x00000001 | 0x00000002 | 0x00000004
    needed = wintypes.DWORD()
    ctypes.set_last_error(0)
    if advapi32.GetFileSecurityW(
        os.fspath(path), requested, None, 0, ctypes.byref(needed)
    ):
        raise RuntimeError("security descriptor size probe unexpectedly succeeded")
    error_number = ctypes.get_last_error()
    if error_number != 122 or needed.value == 0:  # ERROR_INSUFFICIENT_BUFFER
        raise ctypes.WinError(error_number)
    descriptor = ctypes.create_string_buffer(needed.value)
    if not advapi32.GetFileSecurityW(
        os.fspath(path),
        requested,
        descriptor,
        needed.value,
        ctypes.byref(needed),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    for function_name, label in (
        ("GetSecurityDescriptorOwner", "owner"),
        ("GetSecurityDescriptorGroup", "group"),
    ):
        principal = wintypes.LPVOID()
        defaulted = wintypes.BOOL()
        function = getattr(advapi32, function_name)
        function.argtypes = [
            wintypes.LPVOID,
            ctypes.POINTER(wintypes.LPVOID),
            ctypes.POINTER(wintypes.BOOL),
        ]
        function.restype = wintypes.BOOL
        if not function(descriptor, ctypes.byref(principal), ctypes.byref(defaulted)):
            raise ctypes.WinError(ctypes.get_last_error())
        if not principal.value:
            raise RuntimeError(f"security descriptor {label} is absent")
    return bytes(descriptor.raw[: needed.value])


def _windows_dacl_state(path: Path) -> tuple[bool, bool, bool]:
    from ctypes import wintypes

    descriptor_bytes = _windows_security_descriptor(path)
    descriptor = ctypes.create_string_buffer(descriptor_bytes)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

    control = wintypes.WORD()
    revision = wintypes.DWORD()
    advapi32.GetSecurityDescriptorControl.argtypes = [
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.WORD),
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetSecurityDescriptorControl.restype = wintypes.BOOL
    if not advapi32.GetSecurityDescriptorControl(
        descriptor, ctypes.byref(control), ctypes.byref(revision)
    ):
        raise ctypes.WinError(ctypes.get_last_error())

    present = wintypes.BOOL()
    defaulted = wintypes.BOOL()
    dacl = wintypes.LPVOID()
    advapi32.GetSecurityDescriptorDacl.argtypes = [
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.BOOL),
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.BOOL),
    ]
    advapi32.GetSecurityDescriptorDacl.restype = wintypes.BOOL
    if not advapi32.GetSecurityDescriptorDacl(
        descriptor, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted)
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    return (
        bool(control.value & 0x1000),
        bool(present.value and dacl.value),
        bool(defaulted.value),
    )


def _set_windows_null_dacl(path: Path) -> None:
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi32.SetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR,
        ctypes.c_int,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.LPVOID,
    ]
    advapi32.SetNamedSecurityInfoW.restype = wintypes.DWORD
    error_number = int(
        advapi32.SetNamedSecurityInfoW(
            os.fspath(path),
            1,  # SE_FILE_OBJECT
            0x00000004,  # DACL_SECURITY_INFORMATION
            None,
            None,
            None,  # A present NULL DACL grants unrestricted access.
            None,
        )
    )
    if error_number != 0:
        raise ctypes.WinError(error_number)


def _security_metadata(path: Path) -> tuple[str, ...]:
    platform = _platform()
    findings: list[str] = []
    if platform == "windows":
        attributes = _windows_attributes(path)
        if attributes & (
            0x00000001 | 0x00000004 | 0x00000400 | 0x00004000 | 0x00000800
        ):
            findings.append("unsupported_attribute")
        protected, present, _defaulted = _windows_dacl_state(path)
        if not present or protected:
            findings.append("unsupported_dacl")
        return tuple(findings)

    status = path.stat(follow_symlinks=False)
    if stat.S_IMODE(status.st_mode) & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX):
        findings.append("special_mode")
    names = _list_xattrs(path)
    if platform == "linux":
        if any(
            name.startswith((b"security.", b"trusted.", b"system.posix_acl_"))
            for name in names
        ):
            findings.append("security_xattr")
        if any(name != b"user.zagrosi.spike" for name in names):
            findings.append("unsupported_xattr")
    else:
        if any(name in {b"com.apple.macl", b"com.apple.quarantine"} for name in names):
            findings.append("security_xattr")
        allowed = {b"com.apple.provenance", b"com.zagrosi.spike"}
        if any(name not in allowed for name in names):
            findings.append("unsupported_xattr")
        if _macos_has_acl(path):
            findings.append("extended_acl")
        if getattr(status, "st_flags", 0) != 0:
            findings.append("file_flags")
    return tuple(findings)


def _atomic_replace_posix(path: Path, new_bytes: bytes) -> None:
    findings = _security_metadata(path)
    if findings:
        raise UnsupportedSecurityMetadata(",".join(findings))

    status = path.stat(follow_symlinks=False)
    supported_names = (
        (b"com.apple.provenance", b"com.zagrosi.spike")
        if _platform() == "macos"
        else (b"user.zagrosi.spike",)
    )
    existing_names = _list_xattrs(path)
    supported_values = {
        name: _get_xattr(path, name)
        for name in supported_names
        if name in existing_names
    }
    temporary = path.with_name(f".{path.name}.replace")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
        stat.S_IMODE(status.st_mode),
    )
    try:
        view = memoryview(new_bytes)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fchmod(descriptor, stat.S_IMODE(status.st_mode))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        if supported_values:
            for name, value in supported_values.items():
                _set_xattr(temporary, name, value)
            metadata_fd = os.open(temporary, _posix_file_flags())
            try:
                os.fsync(metadata_fd)
            finally:
                os.close(metadata_fd)
        os.replace(temporary, path)
        target_fd = os.open(path, _posix_file_flags())
        try:
            os.fsync(target_fd)
        finally:
            os.close(target_fd)
        parent_fd = os.open(path.parent, _posix_directory_flags())
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    finally:
        if temporary.exists():
            temporary.unlink()


def _flush_windows_file(path: Path) -> None:
    from ctypes import wintypes

    kernel32 = _windows_api()
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
        os.fspath(path),
        0x80000000 | 0x40000000,
        0x00000001 | 0x00000002 | 0x00000004,
        None,
        3,
        0,
        None,
    )
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        kernel32.FlushFileBuffers.argtypes = [wintypes.HANDLE]
        kernel32.FlushFileBuffers.restype = wintypes.BOOL
        if not kernel32.FlushFileBuffers(handle):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        _close_windows_handle(int(handle))


def _atomic_replace_windows(path: Path, new_bytes: bytes) -> None:
    from ctypes import wintypes

    findings = _security_metadata(path)
    if findings:
        raise UnsupportedSecurityMetadata(",".join(findings))
    attributes = _windows_attributes(path)
    temporary = path.with_name(f".{path.name}.replace")
    try:
        with temporary.open("xb", buffering=0) as stream:
            stream.write(new_bytes)
            os.fsync(stream.fileno())
        preserved = attributes & (
            0x00000001 | 0x00000002 | 0x00000020 | 0x00001000 | 0x00002000
        )
        _set_windows_attributes(temporary, preserved or 0x00000080)
        kernel32 = _windows_api()
        kernel32.ReplaceFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.LPVOID,
        ]
        kernel32.ReplaceFileW.restype = wintypes.BOOL
        if not kernel32.ReplaceFileW(
            os.fspath(path), os.fspath(temporary), None, 0, None, None
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        _flush_windows_file(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _atomic_replace(path: Path, new_bytes: bytes) -> None:
    if _platform() == "windows":
        _atomic_replace_windows(path, new_bytes)
    else:
        _atomic_replace_posix(path, new_bytes)


def atomic_supported_metadata_replacement(root: Path) -> Evidence:
    root.mkdir(parents=True)
    config = root / "config.json"
    config.write_bytes(b'{"generation":1}\n')
    platform = _platform()
    provenance_before: bytes | None = None

    if platform == "windows":
        original_attributes = _windows_attributes(config)
        _set_windows_attributes(config, original_attributes | 0x00000002)
        supported_before = _windows_attributes(config) & 0x00000002
        security_descriptor_before = _windows_security_descriptor(config)
        supported_name = None
    else:
        config.chmod(0o640)
        supported_name = (
            b"com.zagrosi.spike" if platform == "macos" else b"user.zagrosi.spike"
        )
        _set_xattr(config, supported_name, b"preserve-me")
        status = config.stat()
        supported_before = (status.st_uid, status.st_gid, stat.S_IMODE(status.st_mode))
        provenance_name = b"com.apple.provenance"
        provenance_before = (
            _optional_xattr(config, provenance_name) if platform == "macos" else None
        )
        security_descriptor_before = None

    _atomic_replace(config, b'{"generation":2}\n')
    if platform == "windows":
        metadata_preserved = (
            _windows_attributes(config) & 0x00000002 == supported_before
        )
        security_descriptor_preserved = (
            _windows_security_descriptor(config) == security_descriptor_before
        )
    else:
        status = config.stat()
        metadata_preserved = (
            (status.st_uid, status.st_gid, stat.S_IMODE(status.st_mode))
            == supported_before
            and supported_name is not None
            and _get_xattr(config, supported_name) == b"preserve-me"
        )
    evidence = {
        "bytes_replaced": config.read_bytes() == b'{"generation":2}\n',
        "replacement_data_flushed": True,
        "supported_metadata_preserved": metadata_preserved,
    }
    if security_descriptor_before is not None:
        evidence["security_descriptor_preserved"] = security_descriptor_preserved
    elif platform == "macos":
        provenance_name = b"com.apple.provenance"
        evidence["provenance_preserved"] = (
            _optional_xattr(config, provenance_name) == provenance_before
        )
    return evidence


def _expect_metadata_rejection(path: Path) -> bool:
    before = path.read_bytes()
    try:
        _atomic_replace(path, b"mutated")
    except UnsupportedSecurityMetadata:
        return (
            path.read_bytes() == before
            and not path.with_name(f".{path.name}.replace").exists()
        )
    return False


def unsupported_security_metadata_rejection(root: Path) -> Evidence:
    root.mkdir(parents=True)
    platform = _platform()
    detections: list[bool] = []
    rejections: list[bool] = []
    preserved: list[bool] = []

    if platform == "linux":
        config = root / "special-mode.json"
        config.write_bytes(b"original")
        config.chmod(0o4755)
        detections.append("special_mode" in _security_metadata(config))
        rejected = _expect_metadata_rejection(config)
        rejections.append(rejected)
        preserved.append(config.read_bytes() == b"original")

        extended = root / "unsupported-xattr.json"
        extended.write_bytes(b"original")
        _set_xattr(extended, b"user.zagrosi.unhandled", b"unsupported")
        detections.append("unsupported_xattr" in _security_metadata(extended))
        rejections.append(_expect_metadata_rejection(extended))
        preserved.append(extended.read_bytes() == b"original")
    elif platform == "macos":
        quarantined = root / "quarantined.json"
        quarantined.write_bytes(b"original")
        _set_xattr(quarantined, b"com.apple.quarantine", b"0081;spike;zagrosi;")
        detections.append("security_xattr" in _security_metadata(quarantined))
        rejections.append(_expect_metadata_rejection(quarantined))
        preserved.append(quarantined.read_bytes() == b"original")
        _remove_xattr(quarantined, b"com.apple.quarantine")

        acl_config = root / "acl.json"
        acl_config.write_bytes(b"original")
        completed = subprocess.run(
            ["/bin/chmod", "+a", "everyone deny execute", os.fspath(acl_config)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=_PROCESS_TIMEOUT_SECONDS,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError("ACL creation failed")
        detections.append("extended_acl" in _security_metadata(acl_config))
        rejections.append(_expect_metadata_rejection(acl_config))
        preserved.append(acl_config.read_bytes() == b"original")
        subprocess.run(
            ["/bin/chmod", "-N", os.fspath(acl_config)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=_PROCESS_TIMEOUT_SECONDS,
            check=True,
        )

        flagged = root / "flagged.json"
        flagged.write_bytes(b"original")
        hidden_flag = getattr(stat, "UF_HIDDEN", 0)
        if hidden_flag == 0:
            raise RuntimeError("macOS file flags are unavailable")
        os.chflags(flagged, hidden_flag)
        detections.append("file_flags" in _security_metadata(flagged))
        rejections.append(_expect_metadata_rejection(flagged))
        preserved.append(flagged.read_bytes() == b"original")
        os.chflags(flagged, 0)
    else:
        dacl_config = root / "dacl.json"
        dacl_config.write_bytes(b"original")
        completed = subprocess.run(
            ["icacls.exe", os.fspath(dacl_config), "/inheritance:d"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=_PROCESS_TIMEOUT_SECONDS,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError("protected DACL creation failed")
        detections.append("unsupported_dacl" in _security_metadata(dacl_config))
        rejections.append(_expect_metadata_rejection(dacl_config))
        preserved.append(dacl_config.read_bytes() == b"original")
        subprocess.run(
            ["icacls.exe", os.fspath(dacl_config), "/inheritance:e"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=_PROCESS_TIMEOUT_SECONDS,
            check=True,
        )

        null_dacl = root / "null-dacl.json"
        null_dacl.write_bytes(b"original")
        _set_windows_null_dacl(null_dacl)
        detections.append("unsupported_dacl" in _security_metadata(null_dacl))
        rejections.append(_expect_metadata_rejection(null_dacl))
        preserved.append(null_dacl.read_bytes() == b"original")
        subprocess.run(
            ["icacls.exe", os.fspath(null_dacl), "/reset"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=_PROCESS_TIMEOUT_SECONDS,
            check=True,
        )

        attributed = root / "system-attribute.json"
        attributed.write_bytes(b"original")
        original_attributes = _windows_attributes(attributed)
        _set_windows_attributes(attributed, original_attributes | 0x00000004)
        try:
            detections.append("unsupported_attribute" in _security_metadata(attributed))
            rejections.append(_expect_metadata_rejection(attributed))
            preserved.append(attributed.read_bytes() == b"original")
        finally:
            _set_windows_attributes(attributed, original_attributes)

    return {
        "bytes_preserved": all(preserved),
        "effects_rejected": all(rejections),
        "security_metadata_detected": all(detections),
    }


_POSIX_TREE_GRANDCHILD = r"""
import fcntl
import sys
import time

locked = open(sys.argv[1], "r+b", buffering=0)
fcntl.flock(locked.fileno(), fcntl.LOCK_EX)
with open(sys.argv[2], "xb") as ready:
    ready.write(b"ready")
time.sleep(60)
"""


_TREE_LEADER = r"""
import subprocess
import sys
import time

subprocess.Popen(
    [sys.executable, "-c", sys.argv[1], sys.argv[2], sys.argv[3]],
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
time.sleep(60)
"""


def _terminate_posix_process_tree(root: Path, lock_path: Path, ready: Path) -> Evidence:
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            _TREE_LEADER,
            _POSIX_TREE_GRANDCHILD,
            os.fspath(lock_path),
            os.fspath(ready),
        ],
        cwd=root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=os.environ.copy(),
        start_new_session=True,
    )
    acquired = None
    try:
        _wait_for_file(ready, process)
        contender = _try_posix_lock(lock_path)
        blocked = contender is None
        if contender is not None:
            _unlock_posix(contender)
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=_PROCESS_TIMEOUT_SECONDS)
        acquired = _wait_for_released_lock(lock_path, windows=False)
        released = True
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=_PROCESS_TIMEOUT_SECONDS)
        if acquired is not None:
            _unlock_posix(acquired)
    return {
        "descendant_started": ready.read_bytes() == b"ready" and blocked,
        "tree_terminated": process.returncode is not None,
        "tree_owned_resource_released": released,
    }


def _create_suspended_windows_job_process(
    command: list[str], cwd: Path
) -> tuple[int, int]:
    from ctypes import wintypes

    class SecurityAttributes(ctypes.Structure):
        _fields_ = [
            ("nLength", wintypes.DWORD),
            ("lpSecurityDescriptor", wintypes.LPVOID),
            ("bInheritHandle", wintypes.BOOL),
        ]

    class StartupInfo(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("lpReserved", wintypes.LPWSTR),
            ("lpDesktop", wintypes.LPWSTR),
            ("lpTitle", wintypes.LPWSTR),
            ("dwX", wintypes.DWORD),
            ("dwY", wintypes.DWORD),
            ("dwXSize", wintypes.DWORD),
            ("dwYSize", wintypes.DWORD),
            ("dwXCountChars", wintypes.DWORD),
            ("dwYCountChars", wintypes.DWORD),
            ("dwFillAttribute", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("wShowWindow", wintypes.WORD),
            ("cbReserved2", wintypes.WORD),
            ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
            ("hStdInput", wintypes.HANDLE),
            ("hStdOutput", wintypes.HANDLE),
            ("hStdError", wintypes.HANDLE),
        ]

    class ProcessInformation(ctypes.Structure):
        _fields_ = [
            ("hProcess", wintypes.HANDLE),
            ("hThread", wintypes.HANDLE),
            ("dwProcessId", wintypes.DWORD),
            ("dwThreadId", wintypes.DWORD),
        ]

    class BasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_uint64),
            ("WriteOperationCount", ctypes.c_uint64),
            ("OtherOperationCount", ctypes.c_uint64),
            ("ReadTransferCount", ctypes.c_uint64),
            ("WriteTransferCount", ctypes.c_uint64),
            ("OtherTransferCount", ctypes.c_uint64),
        ]

    class ExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BasicLimitInformation),
            ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = _windows_api()
    kernel32.CreateJobObjectW.argtypes = [
        ctypes.POINTER(SecurityAttributes),
        wintypes.LPCWSTR,
    ]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())
    process_info = ProcessInformation()
    try:
        limits = ExtendedLimitInformation()
        limits.BasicLimitInformation.LimitFlags = 0x00002000
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        if not kernel32.SetInformationJobObject(
            job, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            raise ctypes.WinError(ctypes.get_last_error())

        startup = StartupInfo()
        startup.cb = ctypes.sizeof(startup)
        command_line = ctypes.create_unicode_buffer(subprocess.list2cmdline(command))
        kernel32.CreateProcessW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.LPWSTR,
            ctypes.POINTER(SecurityAttributes),
            ctypes.POINTER(SecurityAttributes),
            wintypes.BOOL,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.LPCWSTR,
            ctypes.POINTER(StartupInfo),
            ctypes.POINTER(ProcessInformation),
        ]
        kernel32.CreateProcessW.restype = wintypes.BOOL
        if not kernel32.CreateProcessW(
            None,
            command_line,
            None,
            None,
            False,
            0x00000004 | 0x08000000,
            None,
            os.fspath(cwd),
            ctypes.byref(startup),
            ctypes.byref(process_info),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        if not kernel32.AssignProcessToJobObject(job, process_info.hProcess):
            raise ctypes.WinError(ctypes.get_last_error())
        kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
        kernel32.ResumeThread.restype = wintypes.DWORD
        if kernel32.ResumeThread(process_info.hThread) == 0xFFFFFFFF:
            raise ctypes.WinError(ctypes.get_last_error())
        _close_windows_handle(int(process_info.hThread))
        process_info.hThread = None
        return int(job), int(process_info.hProcess)
    except BaseException:
        if process_info.hProcess:
            kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
            kernel32.TerminateProcess.restype = wintypes.BOOL
            kernel32.TerminateProcess(process_info.hProcess, 15)
        if process_info.hThread:
            _close_windows_handle(int(process_info.hThread))
        if process_info.hProcess:
            _close_windows_handle(int(process_info.hProcess))
        _close_windows_handle(int(job))
        raise


def _terminate_windows_process_tree(
    root: Path, lock_path: Path, ready: Path
) -> Evidence:
    from ctypes import wintypes

    command = [
        sys.executable,
        "-c",
        _TREE_LEADER,
        _WINDOWS_LOCK_CHILD,
        os.fspath(lock_path),
        os.fspath(ready),
    ]
    job, process = _create_suspended_windows_job_process(command, root)
    acquired = None
    try:
        _wait_for_file(ready)
        contender = _try_windows_lock(lock_path)
        blocked = contender is None
        if contender is not None:
            _unlock_windows(contender)
        _close_windows_handle(job)
        job = 0
        kernel32 = _windows_api()
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        wait_result = int(kernel32.WaitForSingleObject(process, 10_000))
        if wait_result != 0:
            raise RuntimeError("job process did not terminate")
        acquired = _wait_for_released_lock(lock_path, windows=True)
        released = True
    finally:
        if job:
            _close_windows_handle(job)
        _close_windows_handle(process)
        if acquired is not None:
            _unlock_windows(acquired)
    return {
        "descendant_started": ready.read_bytes() == b"ready" and blocked,
        "tree_terminated": True,
        "tree_owned_resource_released": released,
    }


def process_tree_termination(root: Path) -> Evidence:
    root.mkdir(parents=True)
    lock_path = root / "tree.lock"
    lock_path.write_bytes(b"0")
    ready = root / "descendant-ready"
    if _platform() == "windows":
        return _terminate_windows_process_tree(root, lock_path, ready)
    return _terminate_posix_process_tree(root, lock_path, ready)
