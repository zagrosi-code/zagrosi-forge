"""Explicit local team opt-in, durable pending work and per-checkout identity."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import tempfile
import time
import uuid

from .team_git import TEAM_REF, decode_json
from .team_state import TeamError, validate_board

_LIMIT = 8 * 1024 * 1024


def _read(path, limit=_LIMIT):
    try:
        observed = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(observed.st_mode) or observed.st_nlink != 1 or observed.st_size > limit:
        raise TeamError("team-local-state", "Team metadata must be a bounded regular file without links.")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(descriptor, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if ((observed.st_dev, observed.st_ino) != (opened.st_dev, opened.st_ino) or
                not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1):
            raise TeamError("team-local-state", "Team metadata changed while opening it; retry after inspection.")
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise TeamError("team-local-state", "Team metadata exceeds the local size limit.")
    return decode_json(raw)


def _hex(value, length=32):
    return isinstance(value, str) and re.fullmatch(rf"[a-f0-9]{{{length}}}", value)


def _oid(value):
    return _hex(value, 40) or _hex(value, 64)


def _receipt(value):
    return (isinstance(value, dict) and _oid(value.get("revision")) and _hex(value.get("board_id"))
            and "expected" in value and (value["expected"] is None or _oid(value["expected"])))


def _checkout_valid(value):
    if (not isinstance(value, dict) or set(value) != {"checkout_id", "sessions", "bindings", "pending"}
            or not _hex(value["checkout_id"]) or not isinstance(value["sessions"], dict)
            or len(value["sessions"]) > 128 or not isinstance(value["bindings"], dict) or len(value["bindings"]) > 512):
        return False
    if any(not _hex(key) or not _hex(generation) for key, generation in value["sessions"].items()):
        return False
    for key, binding in value["bindings"].items():
        if (not _hex(key, 64) or not isinstance(binding, dict) or set(binding) != {"session_id", "generation"}
                or not _hex(binding["session_id"]) or not _hex(binding["generation"])):
            return False
    pending = value["pending"]
    if pending is not None and (not _receipt(pending) or not isinstance(pending.get("action"), str)
            or pending.get("action") not in {"start", "update", "finish", "recover"}
            or not _hex(pending.get("session_id")) or not _hex(pending.get("generation"))
            or "binding" not in pending or (pending["binding"] is not None and not _hex(pending["binding"], 64))):
        return False
    return True


def _cache_valid(cache):
    if cache is not None:
        if (not isinstance(cache, dict) or set(cache) != {"revision", "board", "observed_at"}
                or not _oid(cache["revision"]) or type(cache["observed_at"]) not in (int, float)
                or not 0 <= cache["observed_at"] <= 1e12 or not math.isfinite(cache["observed_at"])):
            return False
        validate_board(cache["board"])
    return True


def _state_valid(value):
    if (not isinstance(value, dict) or set(value) != {"version", "participant_id", "connection", "pending_init", "checkouts", "cache"}
            or type(value["version"]) is not int or value["version"] != 1 or not _hex(value["participant_id"])
            or not isinstance(value["checkouts"], dict) or len(value["checkouts"]) > 128 or not _cache_valid(value["cache"])):
        return False
    connection = value["connection"]
    if connection is not None and (not isinstance(connection, dict)
            or set(connection) != {"remote", "endpoint_digest", "board_id", "marker_digest", "name"}
            or not _hex(connection["board_id"]) or not _hex(connection["endpoint_digest"], 64)
            or not _hex(connection["marker_digest"], 64)
            or any(not isinstance(connection[key], str) or not connection[key] for key in ("remote", "name"))):
        return False
    pending = value["pending_init"]
    if pending is not None and (not isinstance(pending, dict) or not _hex(pending.get("board_id"))
            or not isinstance(pending.get("pin"), dict)
            or set(pending["pin"]) != {"remote", "endpoint_digest"}
            or not isinstance(pending["pin"]["remote"], str) or not _hex(pending["pin"]["endpoint_digest"], 64)
            or not isinstance(pending.get("name"), str)
            or ("receipt" in pending and not _receipt(pending["receipt"]))):
        return False
    return all(_hex(key, 64) and _checkout_valid(checkout) for key, checkout in value["checkouts"].items())


def _directory(path):
    if path.is_symlink():
        raise TeamError("team-local-state", "Team metadata directories cannot be symbolic links.")
    path.mkdir(mode=0o700, exist_ok=True)
    if not path.is_dir():
        raise TeamError("team-local-state", "Team metadata requires a directory.")


def _write(path, value):
    raw = (json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if len(raw) > _LIMIT:
        raise TeamError("team-local-state", "Team metadata exceeds the local size limit.")
    _read(path)
    descriptor, name = tempfile.mkstemp(prefix=".team-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        if os.name == "posix":
            parent = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(parent)
            finally:
                os.close(parent)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class LocalTeam:
    def __init__(self, repo):
        self.repo = repo
        self.directory = repo.common_dir / "forge-team"
        self.path = self.directory / "state.json"
        self.key = hashlib.sha256(os.fsencode(str(repo.git_dir))).hexdigest()
        self._held = False

    @contextmanager
    def locked(self, timeout=5):
        _directory(self.directory)
        lock_path = self.directory / "lock"
        if lock_path.is_symlink():
            raise TeamError("team-local-state", "The team state lock cannot be a symbolic link.")
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        acquired = False
        try:
            observed = os.fstat(descriptor)
            if not stat.S_ISREG(observed.st_mode) or observed.st_nlink != 1:
                raise TeamError("team-local-state", "The team lock must be a regular single-link file.")
            if observed.st_size == 0:
                os.write(descriptor, b"\0")
            deadline = time.monotonic() + timeout
            while not acquired:
                try:
                    if os.name == "nt":
                        import msvcrt
                        os.lseek(descriptor, 0, os.SEEK_SET)
                        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TeamError("team-local-busy", "Another Forge process is updating local team state; retry shortly.")
                    time.sleep(.02)
            self._held = True
            yield self
        finally:
            self._held = False
            if acquired:
                if os.name == "nt":
                    import msvcrt
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def load(self):
        if self.directory.is_symlink():
            raise TeamError("team-local-state", "Team state directory cannot be a symbolic link.")
        value = _read(self.path)
        if value is None:
            return {"version": 1, "participant_id": uuid.uuid4().hex, "connection": None,
                    "pending_init": None, "checkouts": {}, "cache": None}
        if not _state_valid(value):
            raise TeamError("team-local-state", "Local team state is invalid; preserve it before recovery.")
        return value

    def save(self, state):
        if not self._held:
            raise TeamError("team-local-state", "Local team state must be saved while holding its lock.")
        if not _state_valid(state):
            raise TeamError("team-local-state", "Refusing to save invalid local team state.")
        _write(self.path, state)

    def checkout(self, state):
        value = state["checkouts"].setdefault(self.key, {"checkout_id": uuid.uuid4().hex,
                    "sessions": {}, "bindings": {}, "pending": None})
        if not _checkout_valid(value):
            raise TeamError("team-local-state", "The checkout's team state is invalid; preserve it before recovery.")
        return value

    def marker(self):
        parent = self.repo.root / ".forge"
        if parent.is_symlink():
            raise TeamError("team-marker", "Team discovery directory cannot be a symbolic link.")
        marker = _read(parent / "team.json", limit=4096)
        if marker is None:
            return None
        if (not isinstance(marker, dict) or set(marker) != {"version", "board_id", "ref"} or
                type(marker["version"]) is not int or marker["version"] != 1 or
                not isinstance(marker["board_id"], str) or not re.fullmatch(r"[a-f0-9]{32}", marker["board_id"]) or
                marker["ref"] != TEAM_REF):
            raise TeamError("team-marker", "Team discovery marker is invalid or unsupported.")
        return marker

    @staticmethod
    def marker_digest(marker):
        return hashlib.sha256(json.dumps(marker, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def write_marker(self, board_id):
        if not isinstance(board_id, str) or not re.fullmatch(r"[a-f0-9]{32}", board_id):
            raise TeamError("team-marker", "Team board identity is invalid.")
        wanted = {"version": 1, "board_id": board_id, "ref": TEAM_REF}
        existing = self.marker()
        if existing is not None:
            if existing != wanted:
                raise TeamError("team-marker", "An unrelated team marker exists; it will not be replaced.")
            return
        parent = self.repo.root / ".forge"
        _directory(parent)
        descriptor, temporary = tempfile.mkstemp(prefix=".team-", dir=parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(json.dumps(wanted, sort_keys=True, indent=2) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, parent / "team.json")
            except FileExistsError:
                if self.marker() != wanted:
                    raise TeamError("team-marker", "A team marker appeared during setup; it will not be replaced.")
        finally:
            os.unlink(temporary)
