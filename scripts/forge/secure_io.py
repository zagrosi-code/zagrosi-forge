"""Forge secure io."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import os
import stat
import time

from . import detached_contract as _detached_contract
from . import handoff_wire as _handoff_wire
from . import models as _models
from . import processes as _processes
from . import storage as _storage

def open_directory_chain_no_follow(path: Path, *, create: bool = False) -> int:
    absolute = _storage.absolute_path_no_follow(path)
    current_fd = os.open(os.sep, _processes._directory_open_flags())
    traversed = Path(os.sep)
    try:
        for component in absolute.parts[1:]:
            traversed /= component
            try:
                next_fd = os.open(component, _processes._directory_open_flags(), dir_fd=current_fd)
            except FileNotFoundError:
                if not create:
                    raise _models.DetachedImplementationError(
                        "unsafe-detached-path",
                        f"Required no-follow directory component is missing: {traversed}",
                        path=str(traversed),
                    )
                os.mkdir(component, mode=0o700, dir_fd=current_fd)
                next_fd = os.open(component, _processes._directory_open_flags(), dir_fd=current_fd)
            except OSError as exc:
                raise _models.DetachedImplementationError(
                    "unsafe-detached-path",
                    f"Unsafe directory component (including any symbolic link) is refused: {traversed}",
                    path=str(traversed),
                ) from exc
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except Exception:
        os.close(current_fd)
        raise


def _relative_parts(relative: str) -> tuple[str, ...]:
    candidate = Path(relative)
    if candidate.is_absolute() or not candidate.parts or any(part in {"", ".", ".."} for part in candidate.parts):
        raise _models.DetachedImplementationError(
            "unsafe-detached-path",
            f"Detached path must be a non-empty relative path without traversal: {relative}",
            path=relative,
        )
    return candidate.parts


def open_relative_directory(root_fd: int, relative: str, *, create: bool = False) -> int:
    parts = _relative_parts(relative)
    current_fd = os.dup(root_fd)
    traversed: list[str] = []
    try:
        for component in parts:
            traversed.append(component)
            try:
                next_fd = os.open(component, _processes._directory_open_flags(), dir_fd=current_fd)
            except FileNotFoundError:
                if not create:
                    raise _models.DetachedImplementationError(
                        "unsafe-detached-path",
                        f"Detached directory is missing: {'/'.join(traversed)}",
                        path="/".join(traversed),
                    )
                os.mkdir(component, mode=0o700, dir_fd=current_fd)
                next_fd = os.open(component, _processes._directory_open_flags(), dir_fd=current_fd)
            except OSError as exc:
                raise _models.DetachedImplementationError(
                    "unsafe-detached-path",
                    f"Detached directory contains a symbolic link or non-directory component: {'/'.join(traversed)}",
                    path="/".join(traversed),
                ) from exc
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except Exception:
        os.close(current_fd)
        raise


def open_relative_parent(root_fd: int, relative: str, *, create: bool = False) -> tuple[int, str]:
    parts = _relative_parts(relative)
    if len(parts) == 1:
        return os.dup(root_fd), parts[0]
    parent_fd = open_relative_directory(root_fd, "/".join(parts[:-1]), create=create)
    return parent_fd, parts[-1]


def read_single_link_regular_at(
    root_fd: int,
    relative: str,
    *,
    cap: int,
    require_mode: int | None = None,
) -> bytes:
    parent_fd, name = open_relative_parent(root_fd, relative)
    file_fd: int | None = None
    try:
        try:
            file_fd = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent_fd)
        except OSError as exc:
            raise _models.DetachedImplementationError(
                "unsafe-detached-file",
                f"Detached file is missing, replaced, or a symbolic link: {relative}",
                path=relative,
            ) from exc
        file_stat = os.fstat(file_fd)
        if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_nlink != 1:
            raise _models.DetachedImplementationError(
                "unsafe-detached-file",
                f"Detached file must be a regular single-link file: {relative}",
                path=relative,
                link_count=file_stat.st_nlink,
            )
        if require_mode is not None and stat.S_IMODE(file_stat.st_mode) != require_mode:
            raise _models.DetachedImplementationError(
                "unsafe-detached-file",
                f"Detached canonical JSON must have mode {require_mode:04o}: {relative}",
                path=relative,
                mode=stat.S_IMODE(file_stat.st_mode),
            )
        if file_stat.st_size > cap:
            raise _models.DetachedImplementationError(
                "detached-file-too-large",
                f"Detached file exceeds its {cap}-byte cap: {relative}",
                path=relative,
                size=file_stat.st_size,
            )
        chunks: list[bytes] = []
        remaining = cap + 1
        while remaining:
            chunk = os.read(file_fd, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if len(raw) > cap:
            raise _models.DetachedImplementationError(
                "detached-file-too-large",
                f"Detached file exceeds its {cap}-byte cap: {relative}",
                path=relative,
                size=len(raw),
            )
        after_read = os.fstat(file_fd)
        def stable_fields(value):
            return (
                    value.st_dev,
                    value.st_ino,
                    value.st_mode,
                    value.st_nlink,
                    value.st_size,
                    value.st_mtime_ns,
                    value.st_ctime_ns,
                )
        if stable_fields(file_stat) != stable_fields(after_read) or len(raw) != file_stat.st_size:
            raise _models.DetachedImplementationError(
                "detached-file-changed",
                f"Detached file changed while its bytes were read: {relative}",
                path=relative,
            )
        return raw
    finally:
        if file_fd is not None:
            os.close(file_fd)
        os.close(parent_fd)


def load_canonical_json_at(root_fd: int, relative: str) -> tuple[dict[str, Any], bytes]:
    raw = read_single_link_regular_at(root_fd, relative, cap=_detached_contract.DETACHED_JSON_CAP, require_mode=0o600)
    try:
        payload = json.loads(raw.decode("utf-8"), parse_constant=_handoff_wire.reject_json_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise _models.DetachedImplementationError(
            "invalid-detached-json",
            f"Detached JSON is not canonical UTF-8 JSON: {relative}",
            path=relative,
        ) from exc
    if not isinstance(payload, dict) or _handoff_wire.canonical_json_bytes(payload) != raw:
        raise _models.DetachedImplementationError(
            "invalid-detached-json",
            f"Detached JSON must be a canonical object with one terminal LF: {relative}",
            path=relative,
        )
    return payload, raw


def load_canonical_json_bytes(raw: bytes, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw.decode("utf-8"), parse_constant=_handoff_wire.reject_json_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise _models.DetachedImplementationError(
            "invalid-section-record-transaction",
            f"{label} is not canonical UTF-8 JSON.",
        ) from exc
    if not isinstance(payload, dict) or _handoff_wire.canonical_json_bytes(payload) != raw:
        raise _models.DetachedImplementationError(
            "invalid-section-record-transaction",
            f"{label} must be a canonical JSON object with one terminal LF.",
        )
    return payload


def _write_all(file_fd: int, raw: bytes) -> None:
    offset = 0
    while offset < len(raw):
        written = os.write(file_fd, raw[offset:])
        if written <= 0:
            raise OSError("short write")
        offset += written


def write_regular_bytes_at(root_fd: int, relative: str, raw: bytes, *, cap: int) -> tuple[str, int]:
    if len(raw) > cap:
        raise _models.DetachedImplementationError(
            "detached-file-too-large",
            f"Detached output exceeds its {cap}-byte cap: {relative}",
            path=relative,
            size=len(raw),
        )
    parent_fd, name = open_relative_parent(root_fd, relative, create=True)
    temporary = f".{name}.{os.getpid()}.{time.time_ns()}.tmp"
    temporary_fd: int | None = None
    try:
        try:
            read_single_link_regular_at(root_fd, relative, cap=cap, require_mode=0o600)
        except _models.DetachedImplementationError as exc:
            if exc.code != "unsafe-detached-file":
                raise
            try:
                os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise
        temporary_fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=parent_fd,
        )
        _write_all(temporary_fd, raw)
        os.fsync(temporary_fd)
        os.close(temporary_fd)
        temporary_fd = None
        os.replace(temporary, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        os.fsync(parent_fd)
        reopened = read_single_link_regular_at(root_fd, relative, cap=cap, require_mode=0o600)
        if reopened != raw:
            raise _models.DetachedImplementationError(
                "detached-write-mismatch",
                f"Detached output changed after write: {relative}",
                path=relative,
            )
        return _handoff_wire.sha256_digest(raw), len(raw)
    finally:
        if temporary_fd is not None:
            os.close(temporary_fd)
        try:
            os.unlink(temporary, dir_fd=parent_fd)
        except FileNotFoundError:
            pass
        os.close(parent_fd)


def write_canonical_json_at(
    root_fd: int,
    relative: str,
    payload: dict[str, Any],
    *,
    immutable: bool = False,
    report_created: bool = False,
) -> tuple[str, int] | tuple[str, int, bool]:
    raw = _handoff_wire.canonical_json_bytes(payload)
    if len(raw) > _detached_contract.DETACHED_JSON_CAP:
        raise _models.DetachedImplementationError(
            "detached-file-too-large",
            f"Detached canonical JSON exceeds its {_detached_contract.DETACHED_JSON_CAP}-byte cap: {relative}",
            path=relative,
            size=len(raw),
        )
    parent_fd, name = open_relative_parent(root_fd, relative, create=True)
    created = False
    try:
        if immutable:
            try:
                file_fd = os.open(
                    name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                    dir_fd=parent_fd,
                )
            except FileExistsError:
                existing = read_single_link_regular_at(root_fd, relative, cap=_detached_contract.DETACHED_JSON_CAP, require_mode=0o600)
                if existing != raw:
                    raise _models.DetachedImplementationError(
                        "pinner-conflict",
                        f"Immutable detached receipt already exists with different bytes: {relative}",
                        path=relative,
                    )
                result: tuple[str, int] | tuple[str, int, bool] = (
                    (_handoff_wire.sha256_digest(existing), len(existing), False)
                    if report_created
                    else (_handoff_wire.sha256_digest(existing), len(existing))
                )
                return result
            created = True
            try:
                _write_all(file_fd, raw)
                os.fsync(file_fd)
            finally:
                os.close(file_fd)
            os.fsync(parent_fd)
        else:
            try:
                read_single_link_regular_at(root_fd, relative, cap=_detached_contract.DETACHED_JSON_CAP, require_mode=0o600)
            except _models.DetachedImplementationError as exc:
                if exc.code != "unsafe-detached-file":
                    raise
                try:
                    os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    raise
            temporary = f".{name}.tmp"
            temporary_fd: int | None = None
            try:
                temporary_fd = os.open(
                    temporary,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                    dir_fd=parent_fd,
                )
                _write_all(temporary_fd, raw)
                os.fsync(temporary_fd)
                os.close(temporary_fd)
                temporary_fd = None
                os.replace(temporary, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
                os.fsync(parent_fd)
            finally:
                if temporary_fd is not None:
                    os.close(temporary_fd)
                try:
                    os.unlink(temporary, dir_fd=parent_fd)
                except FileNotFoundError:
                    pass
        reopened = read_single_link_regular_at(root_fd, relative, cap=_detached_contract.DETACHED_JSON_CAP, require_mode=0o600)
        if reopened != raw:
            raise _models.DetachedImplementationError(
                "detached-write-mismatch",
                f"Detached canonical JSON changed after write: {relative}",
                path=relative,
            )
        return (
            (_handoff_wire.sha256_digest(raw), len(raw), created)
            if report_created
            else (_handoff_wire.sha256_digest(raw), len(raw))
        )
    finally:
        os.close(parent_fd)


def unlink_immutable_json_if_exact_at(root_fd: int, relative: str, expected_raw: bytes) -> None:
    parent_fd, name = open_relative_parent(root_fd, relative)
    try:
        actual = read_single_link_regular_at(root_fd, relative, cap=_detached_contract.DETACHED_JSON_CAP, require_mode=0o600)
        if actual != expected_raw:
            raise _models.DetachedImplementationError(
                "pinner-cleanup-conflict",
                f"Refusing to remove an immutable pinner whose bytes changed: {relative}",
                path=relative,
            )
        os.unlink(name, dir_fd=parent_fd)
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)


def _fd_identity_from_stat(observed: os.stat_result) -> tuple[int, int]:
    return observed.st_dev, observed.st_ino


def mutate_canonical_json_at(root_fd: int, relative: str, default_factory, mutator, timeout_seconds: float = 5.0) -> dict[str, Any]:
    parent_fd, name = open_relative_parent(root_fd, relative, create=True)
    lock_name = f".{name}.lock"
    start = time.monotonic()
    try:
        while True:
            try:
                lock_fd = os.open(
                    lock_name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                    dir_fd=parent_fd,
                )
                try:
                    _write_all(lock_fd, f"{os.getpid()} {_storage.now_iso()}\n".encode())
                    os.fsync(lock_fd)
                finally:
                    os.close(lock_fd)
                break
            except FileExistsError:
                if time.monotonic() - start >= timeout_seconds:
                    raise _models.DetachedImplementationError(
                        "detached-lock-timeout",
                        f"Timed out waiting for detached state lock: {relative}",
                        path=relative,
                    )
                time.sleep(0.01)
        try:
            try:
                state, _ = load_canonical_json_at(root_fd, relative)
            except _models.DetachedImplementationError as exc:
                if exc.code != "unsafe-detached-file":
                    raise
                state = default_factory()
            mutator(state)
            write_canonical_json_at(root_fd, relative, state)
            return state
        finally:
            os.unlink(lock_name, dir_fd=parent_fd)
            os.fsync(parent_fd)
    finally:
        os.close(parent_fd)


def _fd_identity(file_fd: int) -> tuple[int, int]:
    observed = os.fstat(file_fd)
    return observed.st_dev, observed.st_ino
