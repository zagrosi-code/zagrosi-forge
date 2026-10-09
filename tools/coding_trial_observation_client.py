"""Private oracle mailbox client. Stdlib only; never launches candidate code.

The outer assessor owns the oracle deadline, worker execution and cleanup.
Loading this frozen file with runpy needs no candidate or evaluator import path.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import stat
import time

SCHEMA = "coding-trial-observation/v1"


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _same_file(path_info, descriptor_info):
    if os.name != "nt":
        return _identity(path_info) == _identity(descriptor_info)
    # This frozen stdlib-only client cannot import the evaluator's comparison.
    def comparable(info):
        return (info.st_dev, info.st_ino, info.st_mode & ~0o111, info.st_nlink,
                info.st_size, info.st_mtime_ns, getattr(info, "st_birthtime_ns", info.st_ctime_ns))
    return comparable(path_info) == comparable(descriptor_info)


def _directory(path, expected=None):
    _require(path.is_absolute() and path.resolve(strict=True) == path,
             "Mailbox directory is not a regular absolute path")
    info = path.lstat()
    identity = (info.st_dev, info.st_ino, info.st_mode)
    _require(stat.S_ISDIR(info.st_mode) and (expected is None or identity == expected),
             "Mailbox directory changed")
    return identity


def _read(path, limit=None, *, parent_identity=None):
    """Read one stable single-link file, bounding allocation before content I/O."""
    parent_identity = _directory(path.parent, parent_identity)
    before = path.lstat()
    _require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1, "Mailbox file is aliased or not regular")
    bound = before.st_size if limit is None else limit
    _require(before.st_size <= bound, "Mailbox file exceeds its byte bound")
    handle = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(handle, "rb") as stream:
        observed = os.fstat(stream.fileno())
        _require(_same_file(before, observed), "Mailbox file changed before read")
        raw = stream.read(bound + 1)
        _require(len(raw) <= bound and _identity(os.fstat(stream.fileno())) == _identity(observed)
                 and _identity(path.lstat()) == _identity(before), "Mailbox file changed during read")
    _directory(path.parent, parent_identity)
    return raw


def _json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result, "Duplicate JSON field")
            result[key] = value
        return result
    def constant(value):
        raise ValueError("Nonfinite JSON number")
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def _encoded(value):
    raw = (json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n").encode("utf-8")
    _require(_json(raw) == value, "Observation must contain only JSON data")
    return raw


def _request(value):
    _require(type(value) is dict and set(value) == {"schema", "id", "input"}, "Invalid observation request fields")
    _require(value["schema"] == SCHEMA and isinstance(value["id"], str) and value["id"], "Invalid observation identity")
    return _encoded(value)


def _response(value, identifier):
    _require(type(value) is dict and set(value) == {"schema", "id", "result", "error"}, "Invalid observation response fields")
    _require(value["schema"] == SCHEMA and value["id"] == identifier, "Observation response identity differs")
    error = value["error"]
    _require(error is None or (value["result"] is None and type(error) is dict
             and set(error) == {"type", "message"} and all(isinstance(item, str) for item in error.values())),
             "Invalid observation error")
    _encoded(value)
    return value


@contextmanager
def _exclusive(mailbox):
    path = mailbox / "client.lock"
    handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        original = os.fstat(handle)
        before = path.lstat()
        _require(_same_file(before, original), "Observation client lock changed")
    except BaseException:
        os.close(handle)
        raise
    try:
        yield
    finally:
        try:
            unchanged = _identity(os.fstat(handle)) == _identity(original)
        finally:
            os.close(handle)
        _directory(mailbox)
        after = path.lstat()
        _require(unchanged and _identity(after) == _identity(before) and _same_file(after, original),
                 "Observation client lock changed")
        path.unlink()


def _next_directory(mailbox):
    previous = sorted(path for path in mailbox.iterdir() if path.name != "client.lock")
    for number, path in enumerate(previous, 1):
        _require(path.name == f"{number:06d}", "Unexpected observation sequence")
        _directory(path)
        _require(_read(path / "request.ready", 0) == _read(path / "response.ready", 0) == b"",
                 "Prior observation is incomplete")
    path = mailbox / f"{len(previous) + 1:06d}"
    path.mkdir()
    return path, _directory(path)


def observe(assessment_path: Path, request: dict) -> dict:
    """Exchange one bounded request; the parent owns the waiting oracle's lifetime."""
    try:
        raw = _request(request)
        descriptor = _json(_read(Path(assessment_path).absolute()))
        _require(descriptor["schema"] == "coding-trial-assessment-input/v1"
                 and descriptor["worker"]["protocol"] == SCHEMA, "Unsupported observation descriptor")
        limit = descriptor["worker"]["command"]["output_bytes"]
        _require(type(limit) is int and 0 < limit <= 8388608 and len(raw) <= limit,
                 "Observation request exceeds its byte bound")
        mailbox = Path(descriptor["evidence_dir"]) / "oracle-ipc"
        _directory(mailbox)
        with _exclusive(mailbox):
            directory, identity = _next_directory(mailbox)
            with (directory / "request.json").open("xb") as stream:
                stream.write(raw)
            _directory(directory, identity)
            (directory / "request.ready").touch(exist_ok=False)
            while not os.path.lexists(directory / "response.ready"):
                _directory(directory, identity)
                time.sleep(.01)
            _read(directory / "response.ready", 0, parent_identity=identity)
            return _response(_json(_read(directory / "response.json", limit, parent_identity=identity)), request["id"])
    except (OSError, KeyError, TypeError, UnicodeError, RuntimeError) as exc:
        raise ValueError(f"Invalid observation mailbox: {exc}") from exc
