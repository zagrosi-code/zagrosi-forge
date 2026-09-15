"""Test fixtures keep fresh verified modules without importing the whole runtime."""
import json
import sys
from pathlib import Path, PureWindowsPath

from runtime_support import load_entrypoint, load_runtime

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/zagrosi_skills.py"


def test_runtime_manifest_keys_and_cli_dispatch_ignore_native_path_separators(tmp_path, monkeypatch, capsys):
    class WindowsRelativePath(type(Path())):
        def relative_to(self, *other, **kwargs):
            return PureWindowsPath(super().relative_to(*other, **kwargs))

    entrypoint = load_entrypoint(SCRIPT)
    monkeypatch.setattr(entrypoint, "Path", WindowsRelativePath)
    assert str(WindowsRelativePath(SCRIPT.parent / "forge/cli.py").relative_to(SCRIPT.parent)) == "forge\\cli.py"
    assert entrypoint.main(["status", "--path", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["success"]
    assert set(entrypoint.load_runtime().MODULE_NAMES) == set(entrypoint.RUNTIME_MANIFEST)


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
