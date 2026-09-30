"""Release metadata fails explicitly; native checks retain missing-byte evidence."""
import importlib.util
import json
from pathlib import Path
import shutil

import pytest

from forge_test_helpers import ROOT, run_raw


def tool(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def metadata(tmp_path):
    for name in (".codex-plugin/plugin.json", ".agents/plugins/marketplace.json",
                 ".claude-plugin/plugin.json", ".claude-plugin/marketplace.json", "pyproject.toml"):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)
    return tmp_path


@pytest.mark.parametrize("name", [".codex-plugin/plugin.json", ".agents/plugins/marketplace.json"])
@pytest.mark.parametrize("contents", ["{", "[]", '"text"', "7", "null", "{}"])
def test_doctor_reports_bad_codex_metadata_without_traceback(metadata, name, contents):
    (metadata / name).write_text(contents)
    result = run_raw("doctor", "--plugin-root", str(metadata), "--strict")
    assert result.returncode == 1 and "Traceback" not in result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert any(item.get("path") == str(metadata / name) for item in report["findings"])


def test_release_version_check_and_sync_preserve_other_metadata(metadata):
    release = tool("sync_release")
    path = metadata / ".claude-plugin/plugin.json"
    original = json.loads(path.read_text())
    path.write_text(json.dumps({**original, "version": "0.0.1"}))
    before = path.read_bytes()
    assert release.synchronize(metadata, check=True) == [".claude-plugin/plugin.json"]
    assert path.read_bytes() == before
    assert release.synchronize(metadata) == [".claude-plugin/plugin.json"]
    assert json.loads(path.read_text()) == original
    assert release.synchronize(metadata, check=True) == []


def test_doctor_flags_release_drift(metadata):
    path = metadata / ".claude-plugin/plugin.json"
    content = json.loads(path.read_text()); content["version"] = "0.0.1"
    path.write_text(json.dumps(content))
    result = run_raw("doctor", "--plugin-root", str(metadata), "--strict")
    assert "release-version-drift" in {row["code"] for row in json.loads(result.stdout)["findings"]}


def test_native_smoke_detects_same_version_stale_content_and_missing_references(tmp_path):
    native = tool("native_plugin_smoke")
    source, installed = tmp_path / "source", tmp_path / "installed"
    for directory in (source, installed):
        (directory / "skills/example").mkdir(parents=True)
        (directory / "README.md").write_text("old")
        (directory / "skills/example/SKILL.md").write_text("[reference](references/missing.md)")
    (source / "README.md").write_text("new, same release")
    assert native.mismatches(source, installed, ["README.md"]) == ["README.md"]
    assert native.check_references(installed) == ["skills/example/SKILL.md: references/missing.md"]
