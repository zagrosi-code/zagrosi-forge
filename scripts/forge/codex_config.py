"""Small, byte-preserving edits to Forge-owned Codex settings."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import json
import math
import os
import re
import stat
import tempfile
import tomllib


CONFIG_ERROR = "Cannot safely edit this Codex configuration; use the native plugin installer."


def _parse(text):
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        raise ValueError(CONFIG_ERROR) from None


def _same(left, right):
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_same(value, right[key]) for key, value in left.items())
    if isinstance(left, list):
        return len(left) == len(right) and all(_same(a, b) for a, b in zip(left, right))
    return left == right or isinstance(left, float) and math.isnan(left) and math.isnan(right)


def _header_path(line):
    if not re.match(r"^\s*\[(?!\[).*\]\s*(?:#.*)?$", line):
        return None
    try:
        table = tomllib.loads(line)
    except tomllib.TOMLDecodeError:
        return None
    path = []
    while isinstance(table, dict) and len(table) == 1:
        key, table = next(iter(table.items()))
        path.append(key)
    return tuple(path)


def _edit_table(text, path, entries, current):
    lines = text.splitlines(keepends=True)
    newline = "\r\n" if "\r\n" in text else "\n"
    header = "[" + ".".join(json.dumps(key) if "@" in key else key for key in path) + "]"
    start = next((i for i, line in enumerate(lines) if _header_path(line) == path), None)
    if start is None:
        if current:
            raise ValueError(CONFIG_ERROR)
        return text + (newline if text and not text.endswith("\n") else "") + newline + header + newline + "".join(
            f"{key} = {json.dumps(value)}{newline}" for key, value in entries.items()
        ), [f"added {header}"]
    end = next((i for i in range(start + 1, len(lines)) if re.match(r"^\s*\[", lines[i])), len(lines))
    changes = []
    for key, value in entries.items():
        if current.get(key) == value and type(current.get(key)) is type(value):
            continue
        index = None
        for i in range(start + 1, end):
            try:
                parsed = tomllib.loads(lines[i])
            except tomllib.TOMLDecodeError:
                continue
            if key in parsed:
                index = i
                break
        replacement = f"{key} = {json.dumps(value)}{newline}"
        if index is None:
            if key in current:
                raise ValueError(CONFIG_ERROR)
            if end and not lines[end - 1].endswith("\n"):
                lines[end - 1] += newline
            lines.insert(end, replacement)
            end += 1
        else:
            lines[index] = replacement
        changes.append(f"{'added' if index is None else 'updated'} {header}.{key}")
    return "".join(lines), changes


def expected_codex_config(existing: str, plugin_root: Path) -> tuple[str, list[str]]:
    original = _parse(existing)
    expected = deepcopy(original)
    tables = (
        (("marketplaces", "zagrosi"), {"source_type": "local", "source": str(plugin_root)}),
        (("plugins", "zagrosi-forge@zagrosi"), {"enabled": True}),
    )
    for path, entries in tables:
        table = expected
        for key in path:
            if not isinstance(table, dict):
                raise ValueError(CONFIG_ERROR)
            table = table.setdefault(key, {})
        if not isinstance(table, dict):
            raise ValueError(CONFIG_ERROR)
        table.update(entries)
    if _same(original, expected):
        return existing, []
    updated, changes = existing, []
    for path, entries in tables:
        current = original.get(path[0], {}).get(path[1], {})
        if any(current.get(key) != value or type(current.get(key)) is not type(value) for key, value in entries.items()):
            updated, added = _edit_table(updated, path, entries, current)
            changes.extend(added)
    if not _same(_parse(updated), expected):
        raise ValueError(CONFIG_ERROR)
    return updated, changes


def _signature(observed):
    if observed is None:
        return None
    return (observed.st_dev, observed.st_ino, observed.st_mode, observed.st_nlink,
            observed.st_size, observed.st_mtime_ns, observed.st_ctime_ns)


def _unchanged(path, original):
    raw, observed = read_config(path)
    return raw == original[0] and _signature(observed) == _signature(original[1])


def read_config(path):
    """Snapshot bytes and identity without following a final symlink."""
    try:
        before = path.lstat()
    except FileNotFoundError:
        return b"", None
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise ValueError(CONFIG_ERROR)
    with os.fdopen(os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)), "rb") as handle:
        opened = os.fstat(handle.fileno())
        raw = handle.read()
        if _signature(opened) != _signature(os.fstat(handle.fileno())):
            raise ValueError(CONFIG_ERROR)
    # Windows lstat/fstat expose different ctime meanings; compare each view to itself.
    if not os.path.samestat(before, opened) or _signature(path.lstat()) != _signature(before):
        raise ValueError(CONFIG_ERROR)
    return raw, before


def config_text(raw):
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError(CONFIG_ERROR) from None


def publish_config(path, original, updated, *, no_backup=False):
    """Atomically replace the observed config; a failed replacement retains its backup."""
    raw, observed = original
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    backup_path = None
    try:
        with os.fdopen(descriptor, "wb") as handle:
            os.chmod(temporary, stat.S_IMODE(observed.st_mode) if observed else 0o600)
            handle.write(updated.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        if not _unchanged(path, original):
            raise ValueError(CONFIG_ERROR)
        if observed is not None and not no_backup:
            backup_fd, backup_name = tempfile.mkstemp(prefix=f"{path.name}.bak-", dir=path.parent)
            backup_path = Path(backup_name)
            with os.fdopen(backup_fd, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
        if not _unchanged(path, original):
            raise ValueError(CONFIG_ERROR)
        os.replace(temporary, path)
        return backup_path
    finally:
        temporary.unlink(missing_ok=True)
