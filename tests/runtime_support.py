"""Read legacy test symbols from their real modules; patches name an owner."""

from __future__ import annotations

import ast
import importlib
import importlib.util
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import Any


def load_entrypoint(script: Path) -> ModuleType:
    name = f"forge_entrypoint_test_{time.time_ns()}"
    spec = importlib.util.spec_from_file_location(name, script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class RuntimeView:
    """Compatibility reads for tests, without proxying writes into production."""

    def __init__(self, entrypoint: ModuleType):
        self.entrypoint = entrypoint
        self.package = entrypoint.load_runtime()
        self.modules = [importlib.import_module(name) for name in self.package.MODULE_NAMES.values()]
        self._owners: dict[str, ModuleType] = {}
        for module in self.modules:
            for name in vars(module):
                self._owners.setdefault(name, module)
        for module in self.modules:
            for node in ast.parse(Path(module.__file__).read_text()).body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    self._owners[node.name] = module
                elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for target in targets:
                        for child in ast.walk(target):
                            if isinstance(child, ast.Name):
                                self._owners[child.id] = module

    def owner(self, name: str) -> ModuleType:
        if name in self._owners:
            return self._owners[name]
        if hasattr(self.entrypoint, name):
            return self.entrypoint
        if hasattr(self.package, name):
            return self.package
        raise AttributeError(name)

    def __getattr__(self, name: str) -> Any:
        if hasattr(self.entrypoint, name):
            return getattr(self.entrypoint, name)
        return getattr(self.owner(name), name)


def load_runtime(script: Path) -> RuntimeView:
    return RuntimeView(load_entrypoint(script))
