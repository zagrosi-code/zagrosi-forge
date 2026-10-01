"""Fresh verified runtimes with explicit, lazy module access for tests."""

from __future__ import annotations

import importlib
import importlib.util
from contextlib import contextmanager
from contextvars import ContextVar
import sys
import time
from pathlib import Path
from types import ModuleType


_SCOPED_ENTRYPOINTS: ContextVar[list[ModuleType] | None] = ContextVar("test_runtime_scope", default=None)


@contextmanager
def runtime_scope():
    """Release only imports owned by this scope; outer and unscoped runtimes live on."""
    entrypoints = []
    token = _SCOPED_ENTRYPOINTS.set(entrypoints)
    try:
        yield
    finally:
        _SCOPED_ENTRYPOINTS.reset(token)
        namespaces, loaders = set(), set()
        for entrypoint in entrypoints:
            namespaces.add(entrypoint.__name__)
            package = getattr(entrypoint, "_runtime", None)
            if package is not None:
                namespaces.add(package.__name__)
                loaders.add(id(package.__loader__))
        sys.meta_path[:] = [finder for finder in sys.meta_path if id(finder) not in loaders]
        for name in tuple(sys.modules):
            if name.partition(".")[0] in namespaces:
                del sys.modules[name]


def load_entrypoint(script: Path) -> ModuleType:
    name = f"forge_entrypoint_test_{time.time_ns()}"
    spec = importlib.util.spec_from_file_location(name, script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    if (entrypoints := _SCOPED_ENTRYPOINTS.get()) is not None:
        entrypoints.append(module)
    spec.loader.exec_module(module)
    return module


class Runtime:
    """Load only requested modules; each instance keeps patches isolated."""

    def __init__(self, entrypoint: ModuleType):
        self.entrypoint = entrypoint
        self.package = entrypoint.load_runtime()
    def __getattr__(self, name: str) -> ModuleType:
        module = importlib.import_module(f".{name}", self.package.__name__)
        setattr(self, name, module)
        return module


def load_runtime(script: Path) -> Runtime:
    return Runtime(load_entrypoint(script))
