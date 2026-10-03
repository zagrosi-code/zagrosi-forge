"""Fresh coding trials own a clean Git baseline without inheriting user settings."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

from test_coding_trials import trials


def git(root, *args):
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    return subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                           "-c", "commit.gpgSign=false", "-c", f"core.hooksPath={os.devnull}", *args],
                          cwd=root, env=env, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def enclosing_repo(tmp_path):
    root = tmp_path / "parent"
    root.mkdir()
    git(root, "init", "--template=", "--initial-branch=main")
    (root / ".gitignore").write_text("trials/\n")
    (root / "user.txt").write_text("committed user bytes\n")
    git(root, "add", "--", ".gitignore", "user.txt")
    git(root, "commit", "-m", "parent baseline")
    (root / "user.txt").write_text("concurrent user work\n")
    return root


@pytest.mark.parametrize("plain", [False, True])
def test_fresh_trial_diff_sees_fixture_edits_inside_ignored_parent(enclosing_repo, plain):
    parent = enclosing_repo
    before = git(parent, "status", "--porcelain"), git(parent, "rev-parse", "HEAD")
    trial = parent / "trials" / ("plain" if plain else "forge")
    trials.prepare(trial, "cleanup", plain_agent=plain)
    workspace = trial / "workspace"
    assert git(workspace, "status", "--porcelain") == ""
    source = workspace / "src/ledger.py"
    source.write_text(source.read_text() + "\n# candidate edit\n")
    observed = {"root": git(workspace, "rev-parse", "--show-toplevel"),
                "diff": git(workspace, "diff", "--name-only")}
    assert observed == {"root": workspace.as_posix(), "diff": "src/ledger.py"}
    assert (git(parent, "status", "--porcelain"), git(parent, "rev-parse", "HEAD")) == before


def test_preparation_ignores_repository_selectors_user_hooks_templates_and_signing(enclosing_repo, tmp_path, monkeypatch):
    parent = enclosing_repo
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    marker = tmp_path / "hook-ran"
    hook = hooks / "pre-commit"
    hook.write_text(f"#!/bin/sh\nprintf executed > {shlex.quote(str(marker))}\nexit 1\n")
    hook.chmod(0o755)
    template = tmp_path / "template"
    template.mkdir()
    (template / "unexpected-template-file").write_text("must not copy")
    config = tmp_path / "hostile.gitconfig"
    config.write_text(f'[core]\n hooksPath = {json.dumps(hooks.as_posix())}\n[commit]\n gpgSign = true\n'
                      '[gpg]\n program = unavailable-signing-program\n[user]\n name = User\n email = user@example.invalid\n')
    objects = tmp_path / "redirected-objects"
    objects.mkdir()
    variables = {"GIT_DIR": str(parent / ".git"), "GIT_COMMON_DIR": str(parent / ".git"),
                 "GIT_WORK_TREE": str(parent), "GIT_INDEX_FILE": str(parent / ".git/index"),
                 "GIT_OBJECT_DIRECTORY": str(objects), "GIT_CONFIG": str(parent / ".git/config"),
                 "GIT_CONFIG_GLOBAL": str(config), "GIT_CONFIG_SYSTEM": str(config),
                 "GIT_TEMPLATE_DIR": str(template), "GIT_CONFIG_COUNT": "1",
                 "GIT_CONFIG_KEY_0": "user.name", "GIT_CONFIG_VALUE_0": "Injected",
                 "GIT_AUTHOR_NAME": "Injected", "GIT_COMMITTER_EMAIL": "injected@example.invalid"}
    before = {path: path.read_bytes() for path in (config, parent / ".git/config", parent / ".git/index")}
    parent_head = git(parent, "rev-parse", "HEAD")
    for key, value in variables.items():
        monkeypatch.setenv(key, value)
    trial = parent / "trials/isolated"
    trials.prepare(trial, "cleanup")
    workspace = trial / "workspace"
    assert git(workspace, "rev-parse", "--show-toplevel") == workspace.as_posix()
    assert git(workspace, "log", "-1", "--format=%an <%ae>|%cn <%ce>") == (
        "Forge Trial <forge-trial@example.invalid>|Forge Trial <forge-trial@example.invalid>")
    assert git(parent, "rev-parse", "HEAD") == parent_head
    assert all(path.read_bytes() == content for path, content in before.items())
    assert not marker.exists() and not list(objects.iterdir())
    assert not (workspace / ".git/unexpected-template-file").exists()


def test_baseline_preserves_fingerprints_and_excludes_only_generated_metadata(tmp_path):
    trial = tmp_path / "trial"
    trials.prepare(trial, "cleanup")
    workspace = trial / "workspace"
    record = json.loads((trial / "trial.json").read_text())
    assert record["baseline_files"] == trials.files(trials.PACK / "fixture") == trials.files(workspace)
    assert set(git(workspace, "ls-files").splitlines()) == set(record["baseline_files"])
    assert not (workspace / ".gitignore").exists()
    for name in (".planning/notes.md", "src/__pycache__/ledger.pyc", ".pytest_cache/cache", "generated.pyc"):
        path = workspace / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("generated")
    assert git(workspace, "status", "--porcelain") == ""
    (workspace / "src/new.py").write_text("public source")
    assert git(workspace, "status", "--porcelain") == "?? src/new.py"
    assert not any(path.startswith(".git/") for path in trials.files(workspace))


def test_hostile_global_attributes_cannot_reencode_fixture_baseline(tmp_path, monkeypatch):
    config = tmp_path / "xdg/git"
    config.mkdir(parents=True)
    attributes = config / "attributes"
    attributes.write_text("*.py working-tree-encoding=UTF-16\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config.parent))
    trial = tmp_path / "trial"
    trials.prepare(trial, "cleanup")
    workspace = trial / "workspace"
    record = json.loads((trial / "trial.json").read_text())
    for name in record["baseline_files"]:
        staged = subprocess.run(["git", "show", f"HEAD:{name}"], cwd=workspace,
                                capture_output=True, check=True).stdout
        assert staged == (workspace / name).read_bytes()
    assert git(workspace, "config", "--local", "--get", "core.attributesFile") == os.devnull
    assert attributes.read_text() == "*.py working-tree-encoding=UTF-16\n"


@pytest.mark.parametrize("depth", ["lean", "standard", "deep"])
def test_resume_keeps_current_red_checkpoint_and_clean_prepared_git_baseline(tmp_path, depth):
    trial = tmp_path / "trial"
    trials.prepare(trial, "resume", depth)
    workspace = trial / "workspace"
    record = json.loads((trial / "trial.json").read_text())
    assert git(workspace, "rev-parse", "--show-toplevel") == workspace.as_posix()
    assert git(workspace, "status", "--porcelain") == ""
    assert not any(name.startswith(".planning/") for name in git(workspace, "ls-files").splitlines())
    result = trials.execute([sys.executable, str(trials.ROOT / "scripts/zagrosi_skills.py"), "status",
                             "--path", str(workspace / ".planning")], workspace)
    assert result["returncode"] == 0, result
    status = json.loads(result["stdout"])
    resume = status.get("resume") or status.get("implementation", {}).get("resume")
    assert resume["stage"] == "red" and resume["evidence_current"]
    assert record["prepared_checkpoint"]["test_process"]["returncode"] != 0
    assert record["baseline_files"] == trials.files(workspace)


def test_missing_git_is_an_explicit_preparation_failure(tmp_path, monkeypatch):
    original = subprocess.run

    def missing(command, *args, **kwargs):
        if command[0] == "git":
            raise FileNotFoundError("git is unavailable")
        return original(command, *args, **kwargs)

    monkeypatch.setattr(trials.subprocess, "run", missing)
    trial = tmp_path / "trial"
    with pytest.raises(ValueError, match="Git"):
        trials.prepare(trial, "cleanup")
    assert not (trial / "trial.json").exists()


def test_existing_trial_is_never_reinitialized(tmp_path):
    trial = tmp_path / "trial"
    trial.mkdir()
    (trial / "retained.json").write_text("previous evidence")
    with pytest.raises(FileExistsError):
        trials.prepare(trial, "cleanup")
    assert trials.files(trial) == {"retained.json": trials.hashlib.sha256(b"previous evidence").hexdigest()}
