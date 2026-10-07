"""Bounded collaboration records and cooperative, portable path reservations."""
from __future__ import annotations

from bisect import bisect_left
from pathlib import Path
import json
import math
import re
import unicodedata

VERSION = 1
MAX_SESSIONS = 128
MAX_PATHS = 256
MAX_BOARD_BYTES = 1024 * 1024
_HOSTS = {"codex", "claude", "other"}
_STATES = {"planning", "working", "blocked", "review", "handoff"}
_FIELDS = {"participant_id", "checkout_id", "generation", "name", "host", "task", "state",
           "paths", "branch", "head", "updated_at", "note"}
_UNSET = object()


class TeamError(Exception):
    def __init__(self, code: str, message: str, **details):
        super().__init__(message)
        self.code, self.details = code, details


def _invalid(message: str):
    raise TeamError("team-invalid-board", message)


def is_plain_text(value, maximum: int, empty: bool = False) -> bool:
    """Accept bounded, single-line Unicode text without hidden control characters."""
    return (isinstance(value, str) and (empty or bool(value.strip())) and len(value) <= maximum
            and not any(unicodedata.category(c).startswith("C") or unicodedata.category(c) in {"Zl", "Zp"} for c in value))


def _token(value) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{32}", value) is not None


def _portable(path: str) -> str:
    return unicodedata.normalize("NFC", path).casefold()


def _path(value) -> str:
    if not is_plain_text(value, 1024) or value.startswith(("/", "\\")):
        raise TeamError("team-invalid-path", "Paths must be bounded repository-relative literals.")
    parts = [part for part in value.replace("\\", "/").split("/") if part not in ("", ".")]
    for part in parts:
        folded = _portable(part)
        if (part == ".." or folded == ".git" or part.endswith((".", " "))
                or any(c in part for c in ':*?[]<>|"')
                or re.fullmatch(r"(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", folded)):
            raise TeamError("team-invalid-path", "Path has an unsafe or nonportable component.")
    return "/".join(parts) or "."


def _resolve(path: Path) -> Path:
    try:
        return path.resolve(strict=True)
    except FileNotFoundError:
        # A missing child can mask an invalid ancestor on Windows. Only missing
        # paths are allowed; strict resolution must still surface loops/errors.
        for parent in path.parents:
            try:
                parent.resolve(strict=True)
                break
            except FileNotFoundError:
                continue
        return path.resolve()


def normalize_paths(paths, root: Path | None = None) -> list[str]:
    """Keep declared paths and their local symlink targets; never leave the repository."""
    if not isinstance(paths, (list, tuple)) or len(paths) > MAX_PATHS:
        raise TeamError("team-invalid-path", "At most 256 literal paths may be reserved.")
    result = set()
    try:
        root = _resolve(Path(root)) if root is not None else None
        for value in paths:
            path = _path(value)
            result.add(path)
            if root is not None:
                resolved = _resolve(root / path)
                result.add(_path(resolved.relative_to(root).as_posix()))
    except (OSError, RuntimeError, ValueError):
        raise TeamError("team-invalid-path", "Path cannot be resolved safely inside the repository.") from None
    if len(result) > MAX_PATHS:
        raise TeamError("team-invalid-path", "Resolved aliases exceed the 256-path limit.")
    return sorted(result)


def _covers(owned: str, required: str) -> bool:
    return owned == "." or owned == required or required.startswith(owned + "/")


def _inode(root: Path | None, path: str):
    if root is None:
        return None
    try:
        base = Path(root).resolve()
        resolved = (base / path).resolve()
        resolved.relative_to(base)
        stat = resolved.stat()
        return stat.st_dev, stat.st_ino
    except (OSError, RuntimeError, ValueError):
        return None


def paths_overlap(left: str, right: str, root: Path | None = None) -> bool:
    first, second = normalize_paths([left], root), normalize_paths([right], root)
    if any(_covers(_portable(a), _portable(b)) or _covers(_portable(b), _portable(a))
           for a in first for b in second):
        return True
    inode = _inode(root, left)
    return inode is not None and inode == _inode(root, right)


def paths_cover(owned, required, root: Path | None = None) -> bool:
    """Check current targets against stored reservations without widening old aliases."""
    stored = normalize_paths(owned)
    wanted = normalize_paths(required, root)
    inodes = set()
    for path in stored:
        if root is not None:
            try:
                target = (Path(root) / path).resolve().relative_to(Path(root).resolve()).as_posix()
            except (OSError, RuntimeError, ValueError):
                continue
            if not any(_covers(_portable(scope), _portable(target)) for scope in stored):
                continue
        if (inode := _inode(root, path)) is not None:
            inodes.add(inode)
    return all(any(_covers(_portable(path), _portable(target)) for path in stored)
               or (_inode(root, target) in inodes) for target in wanted)


def _branch(value) -> bool:
    if value is None:
        return True
    return (is_plain_text(value, 255) and value != "@" and not value.endswith(("/", "."))
            and not any(c in value for c in " ~^:?*[\\") and ".." not in value and "@{" not in value
            and all(part and not part.startswith(".") and not part.endswith(".lock")
                    for part in value.split("/")))


def validate_session(value) -> dict:
    if not isinstance(value, dict) or set(value) != _FIELDS:
        _invalid("Session fields do not match the collaboration schema.")
    if not all(_token(value[key]) for key in ("participant_id", "checkout_id", "generation")):
        _invalid("Session identities must be opaque 32-digit hexadecimal values.")
    for key, limit in (("name", 80), ("task", 500), ("note", 2000)):
        if not is_plain_text(value[key], limit, empty=key == "note"):
            _invalid(f"Session {key} must be bounded plain text without control characters.")
    if (not isinstance(value["host"], str) or value["host"] not in _HOSTS
            or not isinstance(value["state"], str) or value["state"] not in _STATES):
        _invalid("Unknown collaboration host or session state.")
    head = value["head"]
    if not _branch(value["branch"]) or not isinstance(head, str) or not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", head):
        _invalid("Session branch or commit identity is invalid.")
    stamp = value["updated_at"]
    if type(stamp) not in (int, float) or not 0 <= stamp <= 253402300799 or not math.isfinite(stamp):
        _invalid("Session timestamp must be a finite nonnegative UTC epoch value.")
    result = dict(value)
    result["paths"] = normalize_paths(value["paths"])
    return result


def _assert_claims(sessions: dict, root: Path | None = None):
    """Index prior claims once; scope conflicts don't require a quadratic path scan."""
    claimed, keys, checkouts, inodes = {}, [], {}, {}
    for session_id, entry in sessions.items():
        if not entry["paths"]:
            continue
        checkout = entry["checkout_id"]
        if checkout in checkouts:
            raise TeamError("team-conflict", "Another task owns this checkout; use a separate worktree.",
                            session_id=checkouts[checkout], conflict="checkout")
        scopes = normalize_paths(entry["paths"], root)
        for path in scopes:
            key = _portable(path)
            parents = ["."] + ["/".join(key.split("/")[:i]) for i in range(1, len(key.split("/")) + 1)]
            conflict = next((claimed[parent] for parent in parents if parent in claimed), None)
            position = bisect_left(keys, key + "/")
            if conflict is None and keys and key == ".":
                conflict = claimed[keys[0]]
            if conflict is None and position < len(keys) and keys[position].startswith(key + "/"):
                conflict = claimed[keys[position]]
            inode = _inode(root, path)
            if conflict is None and inode is not None:
                conflict = inodes.get(inode)
            if conflict is not None:
                raise TeamError("team-conflict", "Another task reserves an overlapping path.",
                                session_id=conflict, path=path, conflict="path")
        checkouts[checkout] = session_id
        for path in scopes:
            claimed[_portable(path)] = session_id
            inode = _inode(root, path)
            if inode is not None:
                inodes[inode] = session_id
        keys = sorted(claimed)


def validate_board(value) -> dict:
    if not isinstance(value, dict) or set(value) != {"version", "board_id", "sessions"}:
        _invalid("Board fields do not match the collaboration schema.")
    if type(value["version"]) is not int or value["version"] != VERSION or not _token(value["board_id"]):
        _invalid("Unsupported collaboration schema or invalid board identity.")
    sessions = value["sessions"]
    if not isinstance(sessions, dict) or len(sessions) > MAX_SESSIONS or not all(_token(key) for key in sessions):
        _invalid("Board must contain at most 128 sessions with valid identities.")
    result = {"version": VERSION, "board_id": value["board_id"],
              "sessions": {key: validate_session(entry) for key, entry in sessions.items()}}
    if len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > MAX_BOARD_BYTES:
        _invalid("Collaboration board exceeds its 1 MiB size limit.")
    _assert_claims(result["sessions"])
    return result


def new_board(board_id: str) -> dict:
    return validate_board({"version": VERSION, "board_id": board_id, "sessions": {}})


def require_session(board: dict, session_id: str, *, participant_id: str, checkout_id: str,
                    generation: str, branch=_UNSET, head=_UNSET) -> dict:
    current = validate_board(board)["sessions"].get(session_id)
    if current is None:
        raise TeamError("team-session-missing", "Task is no longer present on the shared board.")
    if any(current[key] != value for key, value in (("participant_id", participant_id),
            ("checkout_id", checkout_id), ("generation", generation))):
        raise TeamError("team-ownership-lost", "Task ownership changed; inspect the shared board before continuing.")
    if branch is not _UNSET and (current["branch"] != branch
            or branch is None and current["head"] != head):
        raise TeamError("team-checkout-changed", "Checkout changed; explicitly update the task before editing.")
    return current


def with_session(board: dict, session_id: str, session: dict, root: Path | None = None) -> dict:
    result = validate_board(board)
    if not _token(session_id):
        _invalid("Invalid collaboration session identity.")
    entry = validate_session(session)
    entry["paths"] = normalize_paths(entry["paths"], root)
    result["sessions"].pop(session_id, None)
    result["sessions"][session_id] = entry
    _assert_claims(result["sessions"], root)
    return validate_board(result)


def without_session(board: dict, session_id: str) -> dict:
    result = validate_board(board)
    if session_id not in result["sessions"]:
        raise TeamError("team-session-missing", "Task is no longer present on the shared board.")
    del result["sessions"][session_id]
    return result
