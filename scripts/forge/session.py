"""Forge session."""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any
import io

PRETTY_OUTPUT = False


_CLI_CONTEXT: ContextVar[dict[str, Any] | None] = ContextVar("forge_cli_context", default=None)


_GATE_STREAMS: ContextVar[tuple[io.StringIO, io.StringIO] | None] = ContextVar("forge_gate_streams", default=None)


_QUALITY_CAPTURE: ContextVar[dict[str, Any] | None] = ContextVar("forge_quality_capture", default=None)


def cached_analysis(name, key, analyze):
    """Reuse parsed results only within a read-cached command and unchanged inputs."""
    from copy import deepcopy

    context = _CLI_CONTEXT.get()
    if context is None or context.get("texts") is None:
        return analyze(lambda path: path)
    cache = context.setdefault("analyses", {}).setdefault(name, {})
    parent = context.get("analysis_observations")
    cached = cache.get(key)
    if cached and all(_path_signature(path) == signature for path, signature in cached[0].items()):
        if parent is not None:
            for path, signature in cached[0].items():
                parent.setdefault(path, signature)
        return deepcopy(cached[1])
    observations = {}

    def observe(path):
        observations.setdefault(path, _path_signature(path))
        return path

    context["analysis_observations"] = observations
    try:
        result = analyze(observe)
    finally:
        context["analysis_observations"] = parent
        if parent is not None:
            for path, signature in observations.items():
                parent.setdefault(path, signature)
    if all(_path_signature(path) == signature for path, signature in observations.items()):
        if len(cache) >= 64:
            cache.pop(next(iter(cache)))
        cache[key] = observations, deepcopy(result)
    return result


def _path_signature(path):
    """Include link identity and resolution, as well as target file contents."""
    from . import storage

    try:
        target, entry = path.stat(), path.lstat()
        return (str(path.resolve()), *storage.file_signature(path, target), entry.st_mode,
                entry.st_ino, entry.st_mtime_ns, entry.st_ctime_ns)
    except (OSError, RuntimeError):
        return None
