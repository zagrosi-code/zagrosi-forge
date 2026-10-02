"""Complete native package provenance blocks changed or unsafe loading inputs."""
import hashlib
import json
import os
from pathlib import Path
import shutil

import pytest

from test_native_workflow_trials import trials
from coding_trial_evidence import evaluator_files
from forge_test_helpers import ROOT
from native_workflow_checks import runtime_module

MANIFEST = ".codex-plugin/package-files.json"
LOADING = (".codex-plugin/plugin.json", ".claude-plugin/plugin.json",
           ".agents/plugins/marketplace.json", "skills/example/agents/openai.yaml")


def package(root):
    content = {MANIFEST: "", **{name: "{}\n" for name in LOADING},
               "scripts/zagrosi_skills.py": "# launcher\n", "skills/example/SKILL.md": "# Skill\n"}
    for name, text in content.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    (root / MANIFEST).write_text(json.dumps(sorted(content)))
    return root


def identity(root):
    # Independent expected schema uses the existing package inventory contract.
    cache = runtime_module(ROOT, "plugin_cache")
    return {"version": 1, "sha256": cache.plugin_tree_inventory(root)[0]}


@pytest.fixture
def native_matrix(tmp_path, monkeypatch):
    source = package(tmp_path / "source")
    codex = tmp_path / "codex"
    claude = tmp_path / "claude"
    shutil.copytree(source, codex)
    shutil.copytree(source, claude)
    directory = tmp_path / "matrix"
    trial = directory / "codex-unsupported"
    (trial / "workspace").mkdir(parents=True)
    (trial / "workspace/user.txt").write_text("user bytes")
    record = {"rows": [{"id": trial.name, "host": "codex", "trigger": "unsupported"}],
              "models": {"codex": "pinned", "claude": "pinned"},
              "efforts": {"codex": "medium", "claude": "medium"},
              "plugin_root": str(source), "plugin_sha256": trials.plugin_files(source),
              "package_identity": identity(source), "evaluator_sha256": trials.evaluator_identity(),
              "installation": {"codex_plugin": str(codex), "claude_plugin": str(claude),
                               "marketplace_name": "fixture", "marketplace": str(source)},
              "total_timeout": 10, "timeout": 5}
    trials.write(directory / "matrix.json", record)
    trials.write(trial / "baseline.json", trials.files(trial / "workspace"))
    calls = []
    monkeypatch.setattr(trials, "authenticated", lambda host: True)

    def session(*args, **kwargs):
        calls.append(kwargs["host"])
        return {"success": True, "final_text": "Bonjour", "plugin_actions": [], "registered_skills": []}

    monkeypatch.setattr(trials, "run_session", session)
    return directory, record, calls


@pytest.mark.parametrize("location", ["plugin_root", "codex_plugin"])
@pytest.mark.parametrize("name", LOADING)
@pytest.mark.parametrize("operation", ["edit", "delete"])
def test_loading_metadata_drift_prevents_any_model_call(native_matrix, location, name, operation):
    directory, record, calls = native_matrix
    root = Path(record[location] if location == "plugin_root" else record["installation"][location])
    path = root / name
    if operation == "edit":
        path.write_text("changed loading bytes")
    else:
        path.unlink()
    result = trials.run(directory, "codex-unsupported")
    assert not result["success"] and not calls, result


@pytest.mark.parametrize("name", [".claude-plugin/new.json", "skills/extra/agents/openai.yaml",
                                  "skills/planning/SKILL.md", "agents/new.md", "hooks/hooks.json", ".mcp.json",
                                  "skills/__pycache__/SKILL.md", "skills/.pytest_cache/SKILL.md",
                                  "scripts/forge/__pycache__/helper.py", "scripts/forge/.pytest_cache/helper.py"])
def test_undeclared_loading_additions_prevent_model_calls(native_matrix, name):
    directory, record, calls = native_matrix
    path = Path(record["installation"]["codex_plugin"]) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("new loader input")
    result = trials.run(directory, "codex-unsupported")
    assert not result["success"] and not calls, result


def test_copied_packages_match_and_generated_bytecode_does_not_change_identity(tmp_path):
    source = package(tmp_path / "source")
    copied = tmp_path / "copied"
    shutil.copytree(source, copied)
    cache = copied / "scripts/__pycache__"
    cache.mkdir()
    (cache / "helper.pyc").write_bytes(b"generated")
    assert trials.package_identity(source) == trials.package_identity(copied) == identity(source)
    extra = copied / "assets/new.svg"
    extra.parent.mkdir()
    extra.write_text("<svg/>")
    members = json.loads((copied / MANIFEST).read_text())
    (copied / MANIFEST).write_text(json.dumps([*members, "assets/new.svg"]))
    assert trials.package_identity(copied) != identity(source)


@pytest.mark.parametrize("name", ["../outside", "/outside", "C:\\outside", "skills/../outside"])
def test_unsafe_manifest_paths_fail_before_staging_or_reading(tmp_path, monkeypatch, name):
    source = package(tmp_path / "source")
    (source / MANIFEST).write_text(json.dumps([MANIFEST, name]))
    monkeypatch.setattr(trials, "prepare_live", lambda *a: pytest.fail("unsafe package must not reach staging"))
    with pytest.raises(ValueError, match="safe relative"):
        trials.prepare(tmp_path / "matrix", source, {}, {})
    assert not (tmp_path / "matrix").exists()


@pytest.mark.parametrize("kind", ["file-link", "directory-link", "root-link", "hardlink"])
def test_linked_package_inputs_fail_without_following_them(tmp_path, kind):
    source = package(tmp_path / "source")
    external = tmp_path / "external"
    external.write_text("outside bytes")
    target = source / LOADING[-1]
    try:
        if kind == "file-link":
            target.unlink()
            target.symlink_to(external)
        elif kind == "directory-link":
            target.parent.rename(tmp_path / "agents")
            target.parent.symlink_to(tmp_path / "agents", target_is_directory=True)
        elif kind == "root-link":
            source.rename(tmp_path / "real")
            source.symlink_to(tmp_path / "real", target_is_directory=True)
        else:
            target.unlink()
            os.link(external, target)
    except OSError as error:
        pytest.skip(f"links unavailable: {error}")
    with pytest.raises(ValueError, match="regular|symbolic"):
        trials.package_identity(source)
    assert external.read_text() == "outside bytes"


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "directory"])
def test_bytecode_cache_does_not_hide_linked_or_nested_inputs(tmp_path, kind):
    source = package(tmp_path / "source")
    cache = source / "scripts/__pycache__"
    cache.mkdir()
    external = tmp_path / "external"
    external.write_text("outside bytes")
    target = cache / "helper.pyc"
    try:
        if kind == "symlink":
            target.symlink_to(external)
        elif kind == "hardlink":
            os.link(external, target)
        else:
            target.mkdir()
    except OSError as error:
        pytest.skip(f"links unavailable: {error}")
    with pytest.raises(ValueError, match="undeclared loading"):
        trials.package_identity(source)
    assert external.read_text() == "outside bytes"


def test_legacy_matrix_is_readable_but_cannot_be_silently_upgraded(native_matrix, monkeypatch):
    directory, record, calls = native_matrix
    record.pop("package_identity")
    path = directory / "matrix.json"
    trials.write(path, record)
    original = path.read_bytes()
    assert trials.report(directory)["package_provenance"]["status"] == "legacy"
    with pytest.raises(ValueError, match="fresh matrix"):
        trials.run(directory, "codex-unsupported")
    monkeypatch.setattr(trials.sys, "argv", ["native_workflow_trials.py", "run", str(directory), "--case", "codex-unsupported", "--claude-model", "new"])
    with pytest.raises(SystemExit):
        trials.main()
    assert path.read_bytes() == original and not calls
    assert not (directory / "codex-unsupported/result.json").exists()


def test_resume_rejects_source_drift_before_moving_previous_completion(native_matrix):
    directory, record, calls = native_matrix
    record["rows"][0].update(trigger="direct", depth="lean", resume_host="claude")
    trials.write(directory / "matrix.json", record)
    trial = directory / "codex-unsupported"
    trials.write(trial / "result.json", {"success": None, "cross_host_pending": True})
    (Path(record["plugin_root"]) / LOADING[0]).write_text("changed")
    before = trials.files(trial)
    with pytest.raises(ValueError, match="package changed"):
        trials.resume(directory, trial.name)
    assert trials.files(trial) == before and not calls
    assert not (trial / "previous-completion").exists()


def test_evaluator_identity_binds_type_checker_consumer_and_compiler(tmp_path):
    names = ["tools/typescript_trial_checks.mjs", "tools/typescript_type_checks.mjs",
             "tools/typescript_public_contract.ts", "tools/package.json", "tools/package-lock.json"]
    for name in [*names, "oracle.py", "cases.json"]:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name)
    before = evaluator_files(tmp_path, tmp_path / "oracle.py", tmp_path / "cases.json")
    for name in names:
        assert before.get(name) == hashlib.sha256(name.encode()).hexdigest()
        (tmp_path / name).write_text("changed")
        assert evaluator_files(tmp_path, tmp_path / "oracle.py", tmp_path / "cases.json")[name] != before[name]
