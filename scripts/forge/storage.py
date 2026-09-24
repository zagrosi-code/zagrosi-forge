"""Forge storage."""

from __future__ import annotations

from datetime import datetime, timezone
from contextlib import contextmanager
from pathlib import Path
from typing import Any
import json
import errno
import os
import stat
import subprocess
import tempfile
import time

from . import CLI_PATH
from . import session as _session

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_text(path: Path) -> str:
    context = _session._CLI_CONTEXT.get()
    if context is None or context["texts"] is None:
        return path.read_text(encoding="utf-8")
    path = path.absolute()
    _session.observe_path(path)
    signature = file_signature(path)
    cached = context["texts"].get(path)
    if cached is None or cached[0] != signature:
        cached = (signature, path.read_text(encoding="utf-8"))
        context["texts"][path] = cached
    return cached[1]


def file_signature(path: Path, current=None) -> tuple:
    current = current or path.stat()
    signature = (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns, current.st_ctime_ns)
    if os.name == "nt" and stat.S_ISREG(current.st_mode):
        # Windows ctime is creation time; restored mtime cannot establish freshness.
        import hashlib

        with path.open("rb") as handle:
            return (*signature, hashlib.file_digest(handle, "sha256").digest())
    return signature


def write_json(path: Path, payload: dict[str, Any]) -> None:
    """Publish complete bytes; a failed write leaves the previous state intact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists():
            temporary.chmod(path.stat().st_mode & 0o777)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(read_text(path))


def absolute_path_no_follow(raw: str | os.PathLike[str]) -> Path:
    expanded = Path(raw).expanduser()
    if not expanded.is_absolute():
        expanded = Path.cwd() / expanded
    return Path(os.path.abspath(os.fspath(expanded)))


@contextmanager
def file_lock(path: Path, timeout_seconds: float = 5.0):
    """Lock a stable sibling inode; the OS releases it when its owner exits."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f"{path.name}.lock")
    if os.name == "nt":
        import msvcrt
        def acquire(handle):
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        def release(handle):
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        def acquire(handle):
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        def release(handle):
            fcntl.flock(handle, fcntl.LOCK_UN)
    with lock_path.open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        deadline = time.monotonic() + timeout_seconds
        while True:
            try:
                acquire(handle)
                break
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    raise
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Timed out waiting for state lock: {lock_path}") from exc
                time.sleep(0.01)
        try:
            yield
        finally:
            release(handle)


def update_json_locked(path: Path, default_factory, mutator, timeout_seconds: float = 5.0) -> dict[str, Any]:
    with file_lock(path, timeout_seconds):
        state = load_json(path) if path.exists() else default_factory()
        mutator(state)
        write_json(path, state)
        return state


def resolve_path(raw: str) -> Path:
    return Path(raw).expanduser().resolve()


def git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def git_info(target_dir: Path) -> dict[str, Any]:
    root_result = git(["rev-parse", "--show-toplevel"], target_dir)
    if root_result.returncode != 0:
        return {"available": False, "root": None}

    root = Path(root_result.stdout.strip())
    branch_result = git(["branch", "--show-current"], root)
    branch = branch_result.stdout.strip() if branch_result.returncode == 0 else ""
    status_result = git(["status", "--porcelain"], root)
    dirty = [line for line in status_result.stdout.splitlines() if line.strip()] if status_result.returncode == 0 else []
    protected = branch in {"main", "master"} or branch.startswith(("release/", "release-", "hotfix/", "hotfix-"))

    return {
        "available": True,
        "root": str(root),
        "branch": branch or None,
        "is_protected_branch": protected,
        "working_tree_clean": not dirty,
        "dirty_files": dirty,
    }


def current_plugin_root() -> Path:
    return Path(str(CLI_PATH)).resolve().parents[1]
