"""The small CLI must bind every module used by detached implementation."""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import py_compile
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from runtime_support import load_entrypoint, load_runtime
from test_zagrosi_skills import (
    ROOT,
    copy_implementation_plugin,
    make_detached_record_fixture,
    planning_tree_snapshot,
    run_script_raw,
)


@pytest.fixture
def bundle(tmp_path):
    return copy_implementation_plugin(tmp_path / "bundle")


def update_manifest(script):
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools/update_runtime_manifest.py"), "--script", str(script)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


def alias_script(bundle, tmp_path, kind):
    if kind == "file":
        script = tmp_path / "forge.py"
        script.symlink_to(bundle / "scripts/zagrosi_skills.py")
        return script
    directory = tmp_path / "linked-plugin"
    directory.symlink_to(bundle, target_is_directory=True)
    return directory / "scripts/zagrosi_skills.py"


@pytest.mark.parametrize("kind", ["file", "directory"])
def test_ordinary_cli_accepts_symlink_aliases_and_preserves_lexical_identity(bundle, tmp_path, capsys, kind):
    script = alias_script(bundle, tmp_path, kind)
    entrypoint = load_entrypoint(script)
    assert entrypoint.main(["status", "--path", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["success"]
    package = entrypoint.load_runtime()
    assert package.CLI_PATH == script.absolute()
    assert Path(package.__file__) == bundle / "scripts/forge/__init__.py"


@pytest.mark.parametrize("kind", ["file", "directory"])
def test_detached_sources_still_reject_symlink_aliases(bundle, tmp_path, kind):
    package = load_entrypoint(alias_script(bundle, tmp_path, kind)).load_runtime()
    sources = importlib.import_module(package.MODULE_NAMES["forge/sources.py"])
    models = importlib.import_module(package.MODULE_NAMES["forge/models.py"])
    with pytest.raises(models.DetachedImplementationError) as caught:
        sources.reopen_implementation_sources()
    assert caught.value.code == "unsafe-implement-source"


@pytest.mark.skipif(Path("/tmp").resolve() == Path("/tmp"), reason="The /tmp alias is platform-specific")
def test_ordinary_cli_accepts_macos_tmp_directory_alias(capsys):
    with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
        bundle = copy_implementation_plugin(Path(temporary))
        script = bundle / "scripts/zagrosi_skills.py"
        assert script != script.resolve()
        entrypoint = load_entrypoint(script)
        assert entrypoint.main(["status", "--path", temporary]) == 0
        assert json.loads(capsys.readouterr().out)["success"]
        assert entrypoint.load_runtime().CLI_PATH == script


def test_disappeared_entrypoint_has_structured_integrity_failure(bundle, capsys):
    script = bundle / "scripts/zagrosi_skills.py"
    entrypoint = load_entrypoint(script)
    script.unlink()
    assert entrypoint.main(["status", "--path", str(bundle)]) == 1
    assert json.loads(capsys.readouterr().out)["error_code"] == "implement-source-drift"


def test_every_source_is_verified_before_package_initializer_runs(bundle, tmp_path, capsys):
    marker = tmp_path / "initialized"
    (bundle / "scripts/forge/__init__.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).touch()\n"
    )
    script = bundle / "scripts/zagrosi_skills.py"
    update_manifest(script)
    entrypoint = load_entrypoint(script)
    (bundle / "scripts/forge/status.py").write_text("# unbound replacement\n")
    assert entrypoint.main(["status", "--path", str(tmp_path)]) == 1
    assert json.loads(capsys.readouterr().out)["error_code"] == "implement-source-drift"
    assert not marker.exists()


@pytest.mark.parametrize("change", ["bytes", "missing", "symlink", "hardlink", "oversize"])
def test_untrusted_module_is_rejected_before_execution(bundle, tmp_path, capsys, change):
    entrypoint = load_entrypoint(bundle / "scripts/zagrosi_skills.py")
    source = bundle / "scripts/forge/status.py"
    marker = tmp_path / "executed"
    if change == "bytes":
        source.write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
    elif change == "missing":
        source.unlink()
    elif change == "symlink":
        target = tmp_path / "source.py"
        source.rename(target)
        source.symlink_to(target)
    elif change == "hardlink":
        os.link(source, tmp_path / "alias.py")
    else:
        with source.open("wb") as handle:
            handle.truncate(entrypoint._SOURCE_LIMIT + 1)
    assert entrypoint.main(["status", "--path", str(tmp_path)]) == 1
    assert json.loads(capsys.readouterr().out)["error_code"] == "implement-source-drift"
    assert not marker.exists()
    assert entrypoint._runtime is None


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO paths require POSIX")
def test_fifo_module_is_rejected_without_waiting_for_a_writer(bundle, tmp_path):
    source = bundle / "scripts/forge/status.py"
    source.unlink()
    os.mkfifo(source)
    result = subprocess.run(
        [sys.executable, str(bundle / "scripts/zagrosi_skills.py"), "status", "--path", str(tmp_path)],
        capture_output=True, text=True, timeout=5,
    )
    assert result.returncode == 1
    assert json.loads(result.stdout)["error_code"] == "implement-source-drift"


def test_lazy_import_executes_verified_snapshot_and_recheck_detects_drift(bundle, tmp_path, capsys):
    entrypoint = load_entrypoint(bundle / "scripts/zagrosi_skills.py")
    package = entrypoint.load_runtime()
    source = bundle / "scripts/forge/status.py"
    source.write_text("raise AssertionError('unverified replacement executed')\n")
    status = importlib.import_module(package.MODULE_NAMES["forge/status.py"])
    assert status.status(SimpleNamespace(path=str(tmp_path))) == 0
    assert json.loads(capsys.readouterr().out)["success"]
    with pytest.raises(ImportError, match="Source digest mismatch"):
        package.verify_sources()


def test_unbound_module_cannot_enter_private_package(bundle):
    package = load_entrypoint(bundle / "scripts/zagrosi_skills.py").load_runtime()
    (bundle / "scripts/forge/injected.py").write_text("VALUE = 'injected'\n")
    with pytest.raises(ModuleNotFoundError, match="Unbound module"):
        importlib.import_module(package.__name__ + ".injected")


def test_cached_bytecode_cannot_replace_verified_source(bundle, tmp_path):
    source = bundle / "scripts/forge/status.py"
    original, metadata = source.read_bytes(), source.stat()
    marker = tmp_path / "executed"
    replacement = f"from pathlib import Path\nPath({str(marker)!r}).touch()\n#".encode()
    source.write_bytes(replacement + b" " * (len(original) - len(replacement)))
    os.utime(source, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
    py_compile.compile(str(source), doraise=True, invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP)
    source.write_bytes(original)
    os.utime(source, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
    package = load_entrypoint(bundle / "scripts/zagrosi_skills.py").load_runtime()
    module = importlib.import_module(package.MODULE_NAMES["forge/status.py"])
    assert callable(module.status)
    assert not marker.exists()
    assert module.__cached__ is None


def test_independent_entrypoints_keep_module_and_session_identity_separate(bundle, tmp_path):
    second = copy_implementation_plugin(tmp_path / "other")
    first_package = load_entrypoint(bundle / "scripts/zagrosi_skills.py").load_runtime()
    second_package = load_entrypoint(second / "scripts/zagrosi_skills.py").load_runtime()
    first_session = importlib.import_module(first_package.MODULE_NAMES["forge/session.py"])
    second_session = importlib.import_module(second_package.MODULE_NAMES["forge/session.py"])
    assert first_package.CLI_PATH != second_package.CLI_PATH
    assert first_session._CLI_CONTEXT is not second_session._CLI_CONTEXT
    assert first_package.__name__ != second_package.__name__


def test_portable_source_reader_preserves_binding_and_rejects_links(bundle, tmp_path, monkeypatch):
    entrypoint = load_entrypoint(bundle / "scripts/zagrosi_skills.py")
    monkeypatch.setattr(os, "supports_dir_fd", set())
    package = entrypoint.load_runtime()
    package.verify_sources()
    source = bundle / "scripts/forge/status.py"
    target = tmp_path / "source.py"
    source.rename(target)
    source.symlink_to(target)
    with pytest.raises(ImportError, match="Linked source path"):
        package.verify_sources()


@pytest.mark.parametrize("refresh_manifest", [False, True])
def test_module_change_after_admission_cannot_mutate_detached_state(bundle, tmp_path, refresh_manifest):
    fixture = make_detached_record_fixture(tmp_path, plugin_root=bundle)
    state_before = planning_tree_snapshot(fixture.implementation_root)
    planning_before = planning_tree_snapshot(fixture.planning)
    source = bundle / "scripts/forge/status.py"
    source.write_bytes(source.read_bytes() + b"\n# changed after admission\n")
    if refresh_manifest:
        update_manifest(fixture.script)
    result = run_script_raw(
        fixture.script, "next-section", "--planning-dir", str(fixture.planning),
        "--implementation-root", str(fixture.implementation_root),
    )
    assert result.returncode == 1
    assert json.loads(result.stdout)["error_code"] == "implement-source-drift"
    assert planning_tree_snapshot(fixture.implementation_root) == state_before
    assert planning_tree_snapshot(fixture.planning) == planning_before


def test_module_change_during_detached_command_is_rechecked_before_success(bundle, tmp_path, monkeypatch, capsys):
    fixture = make_detached_record_fixture(tmp_path, plugin_root=bundle)
    forge = load_runtime(fixture.script)
    original = forge.detached_completed_records
    state_before = planning_tree_snapshot(fixture.implementation_root)
    source = bundle / "scripts/forge/status.py"
    changed = False

    def after_initial_checks(*args, **kwargs):
        nonlocal changed
        result = original(*args, **kwargs)
        if not changed:
            source.write_bytes(source.read_bytes() + b"\n# changed during command\n")
            changed = True
        return result

    monkeypatch.setattr(forge.owner("detached_completed_records"), "detached_completed_records", after_initial_checks)
    assert forge.main([
        "next-section", "--planning-dir", str(fixture.planning),
        "--implementation-root", str(fixture.implementation_root),
    ]) == 1
    assert changed
    payload = json.loads(capsys.readouterr().out)
    assert payload["error_code"] == "implement-source-drift"
    assert payload["implement_source"] == "tool"
    assert planning_tree_snapshot(fixture.implementation_root) == state_before


def test_detached_reopen_converts_runtime_io_error_to_closed_source_failure(bundle, monkeypatch):
    forge = load_runtime(bundle / "scripts/zagrosi_skills.py")

    def unreadable(_path):
        raise PermissionError("source cannot be reopened")

    monkeypatch.setattr(forge.entrypoint, "_read_source", unreadable)
    with pytest.raises(forge.DetachedImplementationError) as caught:
        forge.reopen_implementation_sources()
    assert caught.value.code == "implement-source-drift"


def test_imported_runtime_does_not_accept_an_existing_namespace(bundle):
    entrypoint = load_entrypoint(bundle / "scripts/zagrosi_skills.py")
    namespace = entrypoint.__name__ + "_runtime"
    foreign = importlib.util.module_from_spec(importlib.machinery.ModuleSpec(namespace, loader=None))
    sys.modules[namespace] = foreign
    try:
        with pytest.raises(ImportError, match="already exists"):
            entrypoint.load_runtime()
        assert sys.modules[namespace] is foreign
    finally:
        del sys.modules[namespace]
