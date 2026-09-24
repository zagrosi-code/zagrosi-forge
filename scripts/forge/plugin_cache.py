"""Package inventory and serialized, recoverable installed-cache replacement."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any
import json
import os
import shutil
import tempfile

from . import policy as _policy
from . import storage as _storage

PACKAGE_MANIFEST = ".codex-plugin/package-files.json"


def package_members(root: Path) -> set[str]:
    path = root / PACKAGE_MANIFEST
    if root.is_symlink() or path.parent.is_symlink() or path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
        raise ValueError("Plugin package needs a regular package-files.json manifest")
    members = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(members, list) or not members or PACKAGE_MANIFEST not in members
            or any(not isinstance(name, str) or not name or "\\" in name or ":" in name
                   or PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts
                   or PurePosixPath(name).as_posix() != name or should_skip_cache_path(Path(name)) for name in members)
            or len(set(members)) != len(members)):
        raise ValueError("Plugin package manifest must list unique, safe relative file paths")
    return set(members)


def package_paths(members: set[str]) -> set[str]:
    return members | {parent.as_posix() for name in members for parent in Path(name).parents}


def should_skip_cache_path(path: Path) -> bool:
    return (any(part in _policy.PLUGIN_CACHE_IGNORE_DIRS for part in path.parts)
            or path.name in _policy.PLUGIN_CACHE_IGNORE_FILES or path.suffix in {".pyc", ".pyo"}
            or any(part == ".env" or part.startswith(".env.") for part in path.parts))


def plugin_tree_inventory(root: Path, members: set[str] | None = None) -> tuple[str, list[str]]:
    """Hash declared members without traversing undeclared directories."""
    import hashlib

    digest = hashlib.sha256()
    excluded: list[str] = []
    files: list[Path] = []
    if root.is_symlink():
        raise ValueError(f"Plugin tree must not be a symbolic link: {root}")
    if not root.exists():
        return "", []
    if not root.is_dir():
        raise ValueError(f"Plugin tree must be a directory: {root}")
    allowed = package_paths(package_members(root) if members is None else members)

    def fail(error: OSError) -> None:
        raise error

    for directory, dirs, names in os.walk(root, onerror=fail):
        parent = Path(directory)
        for name in [*dirs, *names]:
            path = parent / name
            relative = path.relative_to(root)
            if relative.as_posix() not in allowed or should_skip_cache_path(relative):
                excluded.append(relative.as_posix())
                if name in dirs:
                    dirs.remove(name)
            elif path.is_symlink():
                raise ValueError(f"Plugin package contains a symbolic link: {relative}")
            elif name in names:
                if not path.is_file() or path.stat().st_nlink != 1:
                    raise ValueError(f"Plugin package contains a non-regular file: {relative}")
                files.append(path)
    for path in sorted(files):
        relative = path.relative_to(root)
        digest.update(str(relative).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest(), sorted(excluded)


def plugin_tree_fingerprint(root: Path) -> str:
    return plugin_tree_inventory(root)[0]


def plugin_cache_status(plugin_root: Path, cache_path: Path) -> dict[str, Any]:
    members = package_members(plugin_root)
    for name in members:
        path = plugin_root / name
        if not path.is_file() or path.is_symlink() or path.stat().st_nlink != 1:
            raise ValueError(f"Plugin package member is missing or not a regular file: {name}")
    source_fingerprint = plugin_tree_inventory(plugin_root, members)[0]
    cached_fingerprint, excluded = plugin_tree_inventory(cache_path, members)
    return {
        "path": str(cache_path),
        "changed": source_fingerprint != cached_fingerprint or bool(excluded),
        "source_fingerprint": source_fingerprint,
        "cached_fingerprint": cached_fingerprint or None,
        "excluded_paths": excluded,
    }


def materialize_plugin_cache(plugin_root: Path, cache_path: Path, dry_run: bool) -> dict[str, Any]:
    source, destination = plugin_root.resolve(), cache_path.resolve()
    if source.is_relative_to(destination) or destination.is_relative_to(source):
        raise ValueError("Plugin source and installed cache must not overlap.")
    if dry_run:
        return {**plugin_cache_status(plugin_root, cache_path), "dry_run": True}
    with _storage.file_lock(cache_path):
        previous = cache_path.with_name(f".{cache_path.name}.previous")
        abandoned = [path for path in cache_path.parent.iterdir()
                     if path.name.startswith(f".{cache_path.name}.tmp-")]
        if any(source.is_relative_to(path.resolve()) or path.resolve().is_relative_to(source)
               for path in [previous, *abandoned]):
            raise ValueError("Plugin source must not overlap cache recovery paths.")
        if any(path.is_symlink() or (path.exists() and not path.is_dir()) for path in (cache_path, previous)):
            raise ValueError("Installed cache and recovery copy must be regular directories.")
        if previous.exists():
            if cache_path.exists():
                shutil.rmtree(previous)
            else:
                os.replace(previous, cache_path)
        for path in abandoned:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
        payload = {**plugin_cache_status(plugin_root, cache_path), "dry_run": False}
        if not payload["changed"]:
            return payload
        with tempfile.TemporaryDirectory(prefix=f".{cache_path.name}.tmp-", dir=cache_path.parent) as directory:
            temporary = Path(directory)
            allowed = package_paths(package_members(plugin_root))

            def ignore(directory: str, names: list[str]) -> set[str]:
                parent = Path(directory).relative_to(plugin_root)
                return {name for name in names if (parent / name).as_posix() not in allowed
                        or should_skip_cache_path(parent / name)}

            shutil.copytree(plugin_root, temporary, ignore=ignore, dirs_exist_ok=True, symlinks=True)
            if plugin_tree_fingerprint(temporary) != payload["source_fingerprint"]:
                raise ValueError("Plugin source changed while preparing the cache; retry the update.")
            if cache_path.exists():
                os.replace(cache_path, previous)
            try:
                os.replace(temporary, cache_path)
            except BaseException:
                if previous.exists():
                    os.replace(previous, cache_path)
                raise
        if previous.exists():
            shutil.rmtree(previous)
        return payload
