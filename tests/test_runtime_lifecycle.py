"""Test-owned runtime lifetimes release verified snapshots without sharing state."""

import gc
import sys
import weakref
from pathlib import Path
from types import ModuleType

import pytest

import runtime_support


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/zagrosi_skills.py"


def test_runtime_scope_releases_modules_finders_and_source_snapshots():
    before = tuple(sys.meta_path)
    references = []
    for _ in range(12):
        with runtime_support.runtime_scope():
            runtime = runtime_support.load_runtime(SCRIPT)
            assert runtime.markdown.requirement_ids("REQ-1") == ["REQ-1"]
            entrypoint, package = runtime.entrypoint.__name__, runtime.package.__name__
            references.append(weakref.ref(runtime.package.__loader__))
        assert entrypoint not in sys.modules
        assert not any(name == package or name.startswith(package + ".") for name in sys.modules)
        assert tuple(sys.meta_path) == before
        del runtime
    gc.collect()
    assert all(reference() is None for reference in references)


def test_nested_scope_preserves_live_runtime_and_unrelated_imports(monkeypatch):
    unrelated = ModuleType("forge_unrelated_lifecycle_test")
    finder = object()
    monkeypatch.setitem(sys.modules, unrelated.__name__, unrelated)
    try:
        with runtime_support.runtime_scope():
            surviving = runtime_support.load_runtime(SCRIPT)
            original = surviving.markdown.requirement_ids
            with runtime_support.runtime_scope():
                disposed = runtime_support.load_runtime(SCRIPT)
                disposed.markdown.requirement_ids = lambda _text: ["patched"]
                # An import registration created by another owner must survive teardown.
                sys.meta_path.append(finder)
                assert surviving.markdown.requirement_ids("REQ-1") == ["REQ-1"]
                assert disposed.markdown.requirement_ids("REQ-1") == ["patched"]
            assert finder in sys.meta_path and sys.modules[unrelated.__name__] is unrelated
            assert surviving.markdown.requirement_ids is original
            assert surviving.storage.load_json  # Load a new module after sibling disposal.
        assert finder in sys.meta_path and sys.modules[unrelated.__name__] is unrelated
    finally:
        if finder in sys.meta_path:
            sys.meta_path.remove(finder)


def test_scope_cleans_direct_entrypoints_when_body_fails():
    before = tuple(sys.meta_path)
    with pytest.raises(RuntimeError, match="test body failed"):
        with runtime_support.runtime_scope():
            entrypoint = runtime_support.load_entrypoint(SCRIPT)
            package = entrypoint.load_runtime()
            namespace = package.__name__
            raise RuntimeError("test body failed")
    assert entrypoint.__name__ not in sys.modules
    assert namespace not in sys.modules
    assert tuple(sys.meta_path) == before


def test_runtime_created_before_scope_keeps_lazy_imports():
    surviving = runtime_support.load_runtime(SCRIPT)
    with runtime_support.runtime_scope():
        disposed = runtime_support.load_runtime(SCRIPT)
        assert disposed.markdown.requirement_ids("REQ-1") == ["REQ-1"]
    assert surviving.scoring.FlightScoreInputs
    assert surviving.package.__name__ in sys.modules


def test_cached_process_executor_works_after_runtime_scope(tmp_path):
    from forge_test_helpers import _command_executor

    with runtime_support.runtime_scope():
        execute = _command_executor()
    result = execute([sys.executable, "-c", "print('retained executor')"], tmp_path)
    assert result["returncode"] == 0 and result["stdout"].strip() == "retained executor"
