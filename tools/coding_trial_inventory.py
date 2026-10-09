"""Complete, relocatable file identities shared by suite preparation and review."""
from __future__ import annotations

from collections import deque
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import stat


def relative_path(value: str) -> str:
    """Validate a literal, portable path; scope roots are never glob patterns."""
    if (not isinstance(value, str) or not value or "\x00" in value or "\\" in value
            or value.startswith("/") or PureWindowsPath(value).drive
            or any(part in {"", ".", ".."} for part in value.split("/"))):
        raise ValueError(f"Invalid relative path: {value!r}")
    return value


def contains_path(root: str, name: str) -> bool:
    return name == root or name.startswith(root + "/")


def fingerprint(entries: dict) -> str:
    return hashlib.sha256(json.dumps(entries, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode("utf-8")).hexdigest()


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def _same_file(path_info, descriptor_info):
    if os.name != "nt":
        return _identity(path_info) == _identity(descriptor_info)
    # Windows path stat adds extension-based execute bits and reports birthtime
    # as ctime; fstat reports ChangeTime. Keep full same-API mutation checks.
    def comparable(info):
        return (info.st_dev, info.st_ino, info.st_mode & ~0o111, info.st_nlink,
                info.st_size, info.st_mtime_ns, getattr(info, "st_birthtime_ns", info.st_ctime_ns))
    return comparable(path_info) == comparable(descriptor_info)


def _stat(parent, name):
    return (os.stat(name, dir_fd=parent, follow_symlinks=False)
            if isinstance(parent, int) else (parent / name).lstat())


@contextmanager
def _directory(parent, name, before=None):
    """Keep directory traversal on pinned descriptors where the OS supports it."""
    if before is None:
        before = _stat(parent, name)
    if not stat.S_ISDIR(before.st_mode):
        raise ValueError(f"Expected a regular directory: {name}")
    handle = parent / name if not isinstance(parent, int) else None
    if os.name == "posix":
        handle = os.open(name if isinstance(parent, int) else handle,
                         os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                         dir_fd=parent if isinstance(parent, int) else None)
    try:
        if isinstance(handle, int) and _identity(os.fstat(handle)) != _identity(before):
            raise ValueError(f"Directory changed before traversal: {name}")
        yield handle
        if _identity(_stat(parent, name)) != _identity(before):
            raise ValueError(f"Directory changed during traversal: {name}")
    finally:
        if isinstance(handle, int):
            os.close(handle)


def _file(parent, name, before, destination):
    if before.st_nlink != 1:
        raise ValueError(f"Hard-linked files are unsupported: {name}")
    flags = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
             | getattr(os, "O_BINARY", 0))
    handle = os.open(name if isinstance(parent, int) else parent / name, flags,
                     dir_fd=parent if isinstance(parent, int) else None)
    digest = hashlib.sha256()
    output = None
    try:
        observed = os.fstat(handle)
        if not _same_file(before, observed):
            raise ValueError(f"File changed before reading: {name}")
        if destination is not None:
            output = destination.open("xb")
        while chunk := os.read(handle, 1024 * 1024):
            digest.update(chunk)
            if output is not None:
                output.write(chunk)
        if (_identity(os.fstat(handle)) != _identity(observed)
                or _identity(_stat(parent, name)) != _identity(before)):
            raise ValueError(f"File changed during reading: {name}")
    finally:
        if output is not None:
            output.close()
        os.close(handle)
    if destination is not None:
        destination.chmod(stat.S_IMODE(before.st_mode))
    return digest.hexdigest()


def _scan(root, excluded=(), expected=None, destination=None, included=None):
    entries = {}
    children = {}
    if expected is not None:
        for name in expected:
            parent, _, leaf = name.rpartition("/")
            children.setdefault(parent, []).append(leaf)

    def visit(parent, prefix):
        if expected is None:
            with os.scandir(parent) as listing:
                names = sorted(entry.name for entry in listing)
        else:
            names = sorted(children.get(prefix, []))
        for leaf in names:
            name = f"{prefix}/{leaf}" if prefix else leaf
            if included is not None and not any(contains_path(selected, name) or contains_path(name, selected)
                                                for selected in included):
                continue
            if any(contains_path(exclusion, name) for exclusion in excluded):
                continue
            relative_path(name)
            if leaf == ".git":
                raise ValueError(f"Git metadata is not task material: {name}")
            before = _stat(parent, leaf)
            mode = stat.S_IMODE(before.st_mode)
            output = destination / name if destination is not None else None
            if stat.S_ISREG(before.st_mode):
                entry = {"type": "file", "mode": mode,
                         "sha256": _file(parent, leaf, before, output)}
            elif stat.S_ISDIR(before.st_mode):
                entry = {"type": "directory", "mode": mode}
                if output is not None:
                    output.mkdir(mode=0o700)
                with _directory(parent, leaf, before) as child:
                    visit(child, name)
            elif stat.S_ISLNK(before.st_mode):
                target = (os.readlink(leaf, dir_fd=parent) if isinstance(parent, int)
                          else os.readlink(parent / leaf))
                if _identity(_stat(parent, leaf)) != _identity(before):
                    raise ValueError(f"Link changed during reading: {name}")
                entry = {"type": "symlink", "mode": mode, "target": target}
            else:
                raise ValueError(f"Unsupported file type: {name}")
            if expected is not None and entry != expected[name]:
                raise ValueError(f"Source no longer matches its inventory: {name}")
            entries[name] = entry

    with _directory(root.parent, root.name) as handle:
        visit(handle, "")
    return dict(sorted(entries.items()))


def _validate_entries(entries):
    for name, entry in entries.items():
        relative_path(name)
        kind = entry.get("type") if isinstance(entry, dict) else None
        fields = {"type", "mode"} | ({"sha256"} if kind == "file" else {"target"} if kind == "symlink" else set())
        if (not isinstance(kind, str) or kind not in {"file", "directory", "symlink"} or set(entry) != fields
                or type(entry["mode"]) is not int or not 0 <= entry["mode"] <= 0o7777):
            raise ValueError(f"Invalid inventory entry: {name}")
        if kind == "file" and (not isinstance(entry["sha256"], str)
                               or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])):
            raise ValueError(f"Invalid content digest: {name}")
        if ".git" in name.split("/"):
            raise ValueError(f"Git metadata is not task material: {name}")
        parent = name.rpartition("/")[0]
        if parent and entries.get(parent, {}).get("type") != "directory":
            raise ValueError(f"Missing structural directory: {name}")


def _link_targets(entries):
    """Resolve a virtual inventory, without reading through a filesystem link."""
    def resolve(parts):
        location, pending, active = [], deque(parts), set()
        while pending:
            part = pending.popleft()
            if isinstance(part, tuple):
                active.remove(part[0])
                continue
            current = "/".join(location)
            if current and entries[current]["type"] != "directory":
                raise ValueError(f"Link traverses a non-directory: {current}")
            if part in {"", "."}:
                continue
            if part == "..":
                if not location:
                    raise ValueError("Link escapes its inventory")
                location.pop()
                continue
            name = "/".join([*location, part])
            entry = entries.get(name)
            if entry is None:
                raise ValueError(f"Link target is absent from its inventory: {name}")
            if entry["type"] == "symlink":
                target = entry["target"]
                if (not isinstance(target, str) or not target or "\x00" in target
                        or "\\" in target or target.startswith("/") or PureWindowsPath(target).drive):
                    raise ValueError(f"Link target is not portable and relative: {name}")
                if name in active:
                    raise ValueError(f"Cyclic link: {name}")
                active.add(name)
                # Exit markers end a link expansion before processing its tail;
                # using the same directory alias twice is not itself a cycle.
                pending.appendleft((name,))
                pending.extendleft(reversed(target.split("/")))
                continue
            location.append(part)
        return "/".join(location)

    targets = {name: resolve(name.split("/")) for name, entry in entries.items()
               if entry["type"] == "symlink"}
    edges = {"": []}
    for name, entry in entries.items():
        if entry["type"] == "directory":
            edges[name] = []
    for name, entry in entries.items():
        target = targets.get(name, name)
        if target in edges:
            edges[name.rpartition("/")[0]].append(target)
    visited, visiting, pending = set(), set(), [("", False)]
    while pending:
        node, exiting = pending.pop()
        if exiting:
            visiting.remove(node)
            visited.add(node)
            continue
        if node in visiting:
            raise ValueError(f"Directory traversal cycle: {node}")
        if node in visited:
            continue
        visiting.add(node)
        pending.append((node, True))
        pending.extend((child, False) for child in reversed(edges[node]))
    return targets


def inventory(root: Path, *, included: tuple[str, ...] | None = None,
              excluded: tuple[str, ...] = (), baseline: dict | None = None) -> dict:
    for name in included or ():
        relative_path(name)
    for name in excluded:
        relative_path(name)
        if baseline is not None and any(contains_path(name, original) for original in baseline):
            raise ValueError(f"Exclusion hides baseline material: {name}")
    try:
        entries = _scan(Path(root).absolute(), excluded, included=included)
        if included is not None and any(name not in entries for name in included):
            raise ValueError("An included resource is missing or excluded")
        if included is not None:
            return project(entries, included)
        _link_targets(entries)
        return entries
    except (OSError, RecursionError) as exc:
        raise ValueError(f"Cannot inventory source: {exc}") from exc


def project(entries: dict, roots: tuple[str, ...]) -> dict:
    _validate_entries(entries)
    for root in roots:
        relative_path(root)
    selected = {name for name in entries if any(contains_path(root, name) for root in roots)}
    structural = {parent.as_posix() for name in selected for parent in Path(name).parents
                  if parent != Path(".")}
    result = {name: dict(entries[name]) for name in sorted(selected | structural)}
    if any(target not in selected for target in _link_targets(result).values()):
        raise ValueError("Link target crosses its assessed role")
    return result


def copy_snapshot(source: Path, destination: Path, expected: dict) -> None:
    source, destination = Path(source).absolute(), Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(destination)
    if destination.resolve().is_relative_to(source.resolve()):
        raise ValueError("Snapshot destination must be outside its source")
    _validate_entries(expected)
    targets = _link_targets(expected)
    destination.mkdir(mode=0o700)
    try:
        _scan(source, expected=expected, destination=destination)
        for name, target in targets.items():
            path = destination / name
            path.symlink_to(expected[name]["target"], target_is_directory=expected[target]["type"] == "directory")
            if hasattr(os, "lchmod"):
                os.lchmod(path, expected[name]["mode"])
        for name in sorted(expected, key=lambda value: value.count("/"), reverse=True):
            if expected[name]["type"] == "directory":
                (destination / name).chmod(expected[name]["mode"])
        if inventory(destination) != expected:
            raise ValueError("Copied snapshot does not match its expected inventory")
    except (OSError, RecursionError) as exc:
        raise ValueError(f"Cannot copy source snapshot: {exc}") from exc
