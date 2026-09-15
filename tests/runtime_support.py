"""Fresh verified runtimes with explicit, lazy module access for tests."""

from __future__ import annotations

import importlib
import importlib.util
import sys
import time
from pathlib import Path
from types import ModuleType


def load_entrypoint(script: Path) -> ModuleType:
    name = f"forge_entrypoint_test_{time.time_ns()}"
    spec = importlib.util.spec_from_file_location(name, script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
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
