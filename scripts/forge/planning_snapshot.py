"""Forge planning snapshot."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import hashlib
import os
import stat

from . import detached_contract as _detached_contract
from . import models as _models
from . import processes as _processes
from . import secure_io as _secure_io
from . import storage as _storage

def _read_planning_file(file_fd: int, relative: str, size: int) -> bytes:
    if size > _detached_contract.FROZEN_PLANNING_FILE_CAP:
        raise _models.DetachedImplementationError(
            "planning-file-too-large",
            f"Frozen planning file exceeds its {_detached_contract.FROZEN_PLANNING_FILE_CAP}-byte cap: {relative}",
            path=relative,
            size=size,
        )
    chunks: list[bytes] = []
    remaining = _detached_contract.FROZEN_PLANNING_FILE_CAP + 1
    while remaining:
        chunk = os.read(file_fd, min(1024 * 1024, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    raw = b"".join(chunks)
    if len(raw) > _detached_contract.FROZEN_PLANNING_FILE_CAP:
        raise _models.DetachedImplementationError(
            "planning-file-too-large",
            f"Frozen planning file exceeds its {_detached_contract.FROZEN_PLANNING_FILE_CAP}-byte cap: {relative}",
            path=relative,
            size=len(raw),
        )
    return raw


def planning_tree_fingerprint(root_fd: int) -> tuple[str, int, int]:
    digest = hashlib.sha256()
    digest.update(b"zagrosi-frozen-planning-tree-v1\0")
    file_count = 0
    total_bytes = 0

    def add_entry(relative: str, kind: bytes, observed: os.stat_result, raw: bytes = b"") -> None:
        try:
            path_bytes = relative.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            raise _models.DetachedImplementationError(
                "unsafe-planning-tree",
                f"Frozen planning path is not strict UTF-8: {relative!r}",
                path=relative,
            ) from exc
        if len(path_bytes) > 0xFFFFFFFF:
            raise _models.DetachedImplementationError(
                "unsafe-planning-tree",
                f"Frozen planning path is too long to frame: {relative!r}",
                path=relative,
            )
        digest.update(len(path_bytes).to_bytes(4, "big"))
        digest.update(path_bytes)
        digest.update(kind)
        digest.update(stat.S_IMODE(observed.st_mode).to_bytes(4, "big"))
        digest.update(observed.st_nlink.to_bytes(8, "big"))
        digest.update(len(raw).to_bytes(8, "big"))
        digest.update(raw)

    def walk(directory_fd: int, prefix: str) -> None:
        nonlocal file_count, total_bytes
        try:
            names = sorted(os.listdir(directory_fd), key=lambda value: value.encode("utf-8", errors="strict"))
        except UnicodeEncodeError as exc:
            raise _models.DetachedImplementationError(
                "unsafe-planning-tree",
                "Frozen planning directory contains a name that is not strict UTF-8.",
            ) from exc
        for name in names:
            relative = f"{prefix}/{name}" if prefix else name
            observed = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if stat.S_ISLNK(observed.st_mode):
                raise _models.DetachedImplementationError(
                    "unsafe-planning-tree",
                    f"Frozen planning trees may not contain symbolic links: {relative}",
                    path=relative,
                )
            if stat.S_ISDIR(observed.st_mode):
                add_entry(relative, b"D", observed)
                child_fd = os.open(name, _processes._directory_open_flags(), dir_fd=directory_fd)
                try:
                    reopened = os.fstat(child_fd)
                    if (reopened.st_dev, reopened.st_ino) != (observed.st_dev, observed.st_ino):
                        raise _models.DetachedImplementationError(
                            "planning-tree-changed",
                            f"Frozen planning directory changed while being opened: {relative}",
                            path=relative,
                        )
                    walk(child_fd, relative)
                finally:
                    os.close(child_fd)
                continue
            if not stat.S_ISREG(observed.st_mode):
                raise _models.DetachedImplementationError(
                    "unsafe-planning-tree",
                    f"Frozen planning trees may contain only directories and regular files: {relative}",
                    path=relative,
                )
            file_fd = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=directory_fd)
            try:
                reopened = os.fstat(file_fd)
                if (reopened.st_dev, reopened.st_ino) != (observed.st_dev, observed.st_ino):
                    raise _models.DetachedImplementationError(
                        "planning-tree-changed",
                        f"Frozen planning file changed while being opened: {relative}",
                        path=relative,
                    )
                raw = _read_planning_file(file_fd, relative, reopened.st_size)
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
                if stable_fields(reopened) != stable_fields(after_read) or len(raw) != reopened.st_size:
                    raise _models.DetachedImplementationError(
                        "planning-tree-changed",
                        f"Frozen planning file changed while its bytes were read: {relative}",
                        path=relative,
                    )
            finally:
                os.close(file_fd)
            add_entry(relative, b"F", reopened, raw)
            file_count += 1
            total_bytes += len(raw)
            if total_bytes > _detached_contract.FROZEN_PLANNING_TREE_CAP:
                raise _models.DetachedImplementationError(
                    "planning-tree-too-large",
                    f"Frozen planning tree exceeds its {_detached_contract.FROZEN_PLANNING_TREE_CAP}-byte cap.",
                    total_bytes=total_bytes,
                )
    root_stat = os.fstat(root_fd)
    add_entry(".", b"D", root_stat)
    walk(root_fd, "")
    return "sha256:" + digest.hexdigest(), file_count, total_bytes


@dataclass
class FrozenPlanningTree:
    path: Path
    root_fd: int
    device: int
    inode: int
    digest: str
    file_count: int
    total_bytes: int

    @classmethod
    def open(cls, path: Path, *, expected_digest: str | None = None) -> FrozenPlanningTree:
        absolute = _storage.absolute_path_no_follow(path)
        root_fd = _secure_io.open_directory_chain_no_follow(absolute)
        try:
            root_stat = os.fstat(root_fd)
            digest, file_count, total_bytes = planning_tree_fingerprint(root_fd)
            if expected_digest is not None and digest != expected_digest:
                raise _models.DetachedImplementationError(
                    "planning-tree-drift",
                    "Frozen planning tree does not match the digest recorded by implement-setup.",
                    expected_planning_tree_sha256=expected_digest,
                    actual_planning_tree_sha256=digest,
                )
            return cls(absolute, root_fd, root_stat.st_dev, root_stat.st_ino, digest, file_count, total_bytes)
        except Exception:
            os.close(root_fd)
            raise

    def verify_unchanged(self) -> None:
        current_digest, file_count, total_bytes = planning_tree_fingerprint(self.root_fd)
        path_fd = _secure_io.open_directory_chain_no_follow(self.path)
        try:
            path_stat = os.fstat(path_fd)
        finally:
            os.close(path_fd)
        if (path_stat.st_dev, path_stat.st_ino) != (self.device, self.inode):
            raise _models.DetachedImplementationError(
                "planning-root-replaced",
                "Frozen planning root was replaced while the command was running.",
                planning_dir=str(self.path),
            )
        if (current_digest, file_count, total_bytes) != (self.digest, self.file_count, self.total_bytes):
            raise _models.DetachedImplementationError(
                "planning-tree-changed",
                "Frozen planning tree changed while the command was running.",
                expected_planning_tree_sha256=self.digest,
                actual_planning_tree_sha256=current_digest,
            )

    def close(self) -> None:
        os.close(self.root_fd)
