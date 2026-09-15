"""Forge storage."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json
import os
import subprocess
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
    signature = file_signature(path)
    cached = context["texts"].get(path)
    if cached is None or cached[0] != signature:
        cached = (signature, path.read_text(encoding="utf-8"))
        context["texts"][path] = cached
    return cached[1]


def file_signature(path: Path) -> tuple[int, int, int, int, int]:
    current = path.stat()
    return (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns, current.st_ctime_ns)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(read_text(path))


def absolute_path_no_follow(raw: str | os.PathLike[str]) -> Path:
    expanded = Path(raw).expanduser()
    if not expanded.is_absolute():
        expanded = Path.cwd() / expanded
    return Path(os.path.abspath(os.fspath(expanded)))


def update_json_locked(path: Path, default_factory, mutator, timeout_seconds: float = 5.0) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f"{path.name}.lock")
    start = time.monotonic()
    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, f"{os.getpid()} {now_iso()}\n".encode("utf-8"))
            finally:
                os.close(fd)
            break
        except FileExistsError:
            if time.monotonic() - start >= timeout_seconds:
                raise TimeoutError(f"Timed out waiting for progress lock: {lock_path}")
            time.sleep(0.01)

    try:
        state = load_json(path) if path.exists() else default_factory()
        mutator(state)
        write_json(path, state)
        return state
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


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
