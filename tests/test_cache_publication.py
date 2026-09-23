"""Installed caches stay clean and retain a working copy on failed updates."""

from pathlib import Path

import pytest

from forge_test_helpers import SCRIPT
from runtime_support import load_runtime


@pytest.fixture
def installer():
    return load_runtime(SCRIPT).plugin_cache


def trees(tmp_path):
    source, cache = tmp_path / "source", tmp_path / "cache"
    for root in (source, cache):
        root.mkdir()
        (root / "runtime.py").write_text("old\n", encoding="utf-8")
    return source, cache


def test_matching_runtime_still_repairs_cache_junk(installer, tmp_path):
    source, cache = trees(tmp_path)
    junk = cache / ".venv" / "lib"
    junk.mkdir(parents=True)
    (junk / "payload").write_bytes(b"unused")
    assert installer.plugin_tree_fingerprint(source) == installer.plugin_tree_fingerprint(cache)
    preview = installer.materialize_plugin_cache(source, cache, True)
    assert preview["changed"]
    assert junk.exists()
    assert installer.materialize_plugin_cache(source, cache, False)["changed"]
    assert not junk.exists()
    assert (cache / "runtime.py").read_text() == "old\n"
    assert not installer.materialize_plugin_cache(source, cache, False)["changed"]


def test_fingerprint_prunes_ignored_directories_before_scanning(installer, tmp_path, monkeypatch):
    source, _ = trees(tmp_path)
    ignored = source / ".venv"
    ignored.mkdir()
    original = installer.os.scandir

    def guarded(path):
        assert Path(path) != ignored, "excluded directory was traversed"
        return original(path)

    monkeypatch.setattr(installer.os, "scandir", guarded)
    assert installer.plugin_tree_fingerprint(source)


def test_failed_publication_restores_previous_cache(installer, tmp_path, monkeypatch):
    source, cache = trees(tmp_path)
    (source / "runtime.py").write_text("new\n", encoding="utf-8")
    replace = installer.os.replace

    def fail_publication(src, dst):
        if Path(src).name.startswith(".cache.tmp-") and Path(dst) == cache:
            raise PermissionError("publication refused")
        return replace(src, dst)

    monkeypatch.setattr(installer.os, "replace", fail_publication)
    with pytest.raises(PermissionError, match="publication refused"):
        installer.materialize_plugin_cache(source, cache, False)
    assert (cache / "runtime.py").read_text() == "old\n"
    assert not list(tmp_path.glob(".cache.tmp-*"))


def test_interrupted_publication_recovers_backup(installer, tmp_path):
    source, cache = trees(tmp_path)
    cache.rename(tmp_path / ".cache.previous")
    abandoned = tmp_path / ".cache.tmp-interrupted"
    abandoned.mkdir()
    (abandoned / "partial.py").write_text("partial", encoding="utf-8")
    assert installer.materialize_plugin_cache(source, cache, False)["changed"] is False
    assert (cache / "runtime.py").read_text() == "old\n"
    assert not (tmp_path / ".cache.previous").exists()
    assert not abandoned.exists()


def test_source_change_during_copy_preserves_working_cache(installer, tmp_path, monkeypatch):
    source, cache = trees(tmp_path)
    (source / "runtime.py").write_text("new\n", encoding="utf-8")
    copytree = installer.shutil.copytree

    def changed_copy(*args, **kwargs):
        result = copytree(*args, **kwargs)
        (Path(result) / "runtime.py").write_text("inconsistent\n", encoding="utf-8")
        return result

    monkeypatch.setattr(installer.shutil, "copytree", changed_copy)
    with pytest.raises(ValueError, match="changed"):
        installer.materialize_plugin_cache(source, cache, False)
    assert (cache / "runtime.py").read_text() == "old\n"


def test_cache_must_not_overlap_source(installer, tmp_path):
    source, _ = trees(tmp_path)
    with pytest.raises(ValueError, match="overlap"):
        installer.materialize_plugin_cache(source, source, False)
    assert (source / "runtime.py").read_text() == "old\n"


def test_failed_rollback_keeps_recoverable_copy(installer, tmp_path, monkeypatch):
    source, cache = trees(tmp_path)
    (source / "runtime.py").write_text("new\n", encoding="utf-8")
    replace = installer.os.replace

    def refuse_destination(src, dst):
        if Path(dst) == cache:
            raise PermissionError("destination unavailable")
        return replace(src, dst)

    with monkeypatch.context() as patch:
        patch.setattr(installer.os, "replace", refuse_destination)
        with pytest.raises(PermissionError):
            installer.materialize_plugin_cache(source, cache, False)
    assert (tmp_path / ".cache.previous/runtime.py").read_text() == "old\n"
    assert installer.materialize_plugin_cache(source, cache, False)["changed"]
    assert (cache / "runtime.py").read_text() == "new\n"


def test_update_status_flags_known_development_artifacts(installer, tmp_path):
    source, cache = trees(tmp_path)
    (cache / "runtime.pyc").write_bytes(b"stale")
    (cache / "node_modules").mkdir()
    status = installer.plugin_cache_status(source, cache)
    assert status["changed"]
    assert status["excluded_paths"] == ["node_modules", "runtime.pyc"]
    installer.materialize_plugin_cache(source, cache, False)
    assert not installer.plugin_cache_status(source, cache)["changed"]


@pytest.mark.parametrize("reserved", [".cache.previous", ".cache.tmp-source"])
@pytest.mark.parametrize("nested", [False, True])
def test_source_inside_recovery_paths_is_never_removed(installer, tmp_path, reserved, nested):
    source, cache = trees(tmp_path)
    reserved_source = tmp_path / reserved / "source" if nested else tmp_path / reserved
    reserved_source.parent.mkdir(parents=True, exist_ok=True)
    source.rename(reserved_source)
    with pytest.raises(ValueError, match="overlap"):
        installer.materialize_plugin_cache(reserved_source, cache, False)
    assert (reserved_source / "runtime.py").read_text() == "old\n"
    assert (cache / "runtime.py").read_text() == "old\n"
