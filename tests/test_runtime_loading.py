"""Test fixtures keep fresh verified modules without importing the whole runtime."""
import sys
from pathlib import Path

from runtime_support import load_runtime

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/zagrosi_skills.py"


def test_small_unit_fixture_loads_only_requested_modules():
    runtime = load_runtime(SCRIPT)
    prefix = runtime.package.__name__ + "."
    assert not any(name.startswith(prefix) for name in sys.modules)
    assert runtime.markdown.requirement_ids("REQ-1 REQ-10") == ["REQ-1", "REQ-10"]
    loaded = {name.removeprefix(prefix) for name in sys.modules if name.startswith(prefix)}
    assert "markdown" in loaded
    assert not loaded & {"detached_context", "recovery", "installation", "cli"}


def test_fixture_patches_cannot_leak_between_runtimes(monkeypatch):
    first, second = load_runtime(SCRIPT), load_runtime(SCRIPT)
    monkeypatch.setattr(first.markdown, "requirement_ids", lambda text: ["patched"])
    assert second.markdown.requirement_ids("REQ-1") == ["REQ-1"]
