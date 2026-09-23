"""Package inventory and serialized, recoverable installed-cache replacement."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import os
import shutil
import tempfile

from . import policy as _policy
from . import storage as _storage

def should_skip_cache_path(path: Path) -> bool:
    return (any(part in _policy.PLUGIN_CACHE_IGNORE_DIRS for part in path.parts)
            or path.name in _policy.PLUGIN_CACHE_IGNORE_FILES or path.suffix in {".pyc", ".pyo"})


def plugin_tree_inventory(root: Path) -> tuple[str, list[str]]:
    """Hash package files without traversing development directories."""
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

    def fail(error: OSError) -> None:
        raise error

    for directory, dirs, names in os.walk(root, onerror=fail):
        parent = Path(directory)
        for name in [*dirs, *names]:
            path = parent / name
            relative = path.relative_to(root)
            if should_skip_cache_path(relative):
                excluded.append(relative.as_posix())
                if name in dirs:
                    dirs.remove(name)
            elif path.is_symlink():
                raise ValueError(f"Plugin package contains a symbolic link: {relative}")
            elif name in names:
                if not path.is_file():
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


def copy_ignore(_: str, names: list[str]) -> set[str]:
    return {name for name in names if should_skip_cache_path(Path(name))}


def plugin_cache_status(plugin_root: Path, cache_path: Path) -> dict[str, Any]:
    source_fingerprint = plugin_tree_fingerprint(plugin_root)
    cached_fingerprint, excluded = plugin_tree_inventory(cache_path)
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
            shutil.copytree(plugin_root, temporary, ignore=copy_ignore, dirs_exist_ok=True, symlinks=True)
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
