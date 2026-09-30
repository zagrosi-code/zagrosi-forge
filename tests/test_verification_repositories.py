"""Verification follows real repository boundaries and observes their source changes."""

from pathlib import Path
import subprocess

import pytest

from test_verification_receipts import workspace
from test_verification_symlinks import symlink, verify


def git(path, *args):
    return subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, timeout=10)


@pytest.fixture(params=["submodule", "embedded"])
def repository(workspace, tmp_path, request):
    _, _, target = workspace
    git(target, "init", "-q")
    source = tmp_path / "vendor-source" if request.param == "submodule" else target / "services/vendor"
    source.mkdir(parents=True)
    git(source, "init", "-q")
    (source / ".gitignore").write_text("cache/\n*.local.py\n")
    (source / "module.py").write_text("value = 1\n")
    (source / "removed.py").write_text("value = 2\n")
    git(source, "add", ".")
    git(source, "-c", "user.name=Forge", "-c", "user.email=forge@example.invalid",
        "-c", "commit.gpgsign=false", "commit", "-qm", "fixture")
    if request.param == "submodule":
        git(target, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(source), "services/vendor")
    return workspace, target / "services/vendor"


@pytest.mark.parametrize("change", ["edit", "add", "delete"])
def test_nested_source_changes_invalidate_receipt(repository, capsys, change):
    workspace, nested = repository
    forge, planning, target = workspace
    verify(workspace, capsys)
    assert forge.verification.integration_report(planning, target)["success"]
    if change == "edit":
        (nested / "module.py").write_text("value = 3\n")
    elif change == "add":
        (nested / "new.py").write_text("value = 4\n")
    else:
        (nested / "removed.py").unlink()
    assert not forge.verification.integration_report(planning, target)["success"]


def test_nested_ignore_rules_preserve_receipt(repository, capsys):
    workspace, nested = repository
    forge, planning, target = workspace
    verify(workspace, capsys)
    (nested / "cache").mkdir()
    (nested / "cache/derived.py").write_text("cache = True\n")
    (nested / "private.local.py").write_text("local = True\n")
    assert forge.verification.integration_report(planning, target)["success"]


def test_deleted_tracked_nested_files_remain_observed(repository, capsys):
    workspace, nested = repository
    forge, planning, target = workspace
    deleted = nested / "removed.py"
    deleted.unlink()
    verify(workspace, capsys)
    assert forge.verification.integration_report(planning, target)["success"]
    git(nested, "rm", "removed.py")
    assert not forge.verification.integration_report(planning, target)["success"]


@pytest.mark.parametrize("repository", ["submodule"], indirect=True)
def test_deinitialized_missing_and_restored_submodule_are_distinct(repository, capsys):
    workspace, nested = repository
    forge, planning, target = workspace
    verify(workspace, capsys)
    git(target, "submodule", "deinit", "-f", "--", "services/vendor")
    assert not forge.verification.integration_report(planning, target)["success"]
    verify(workspace, capsys)
    assert forge.verification.integration_report(planning, target)["success"]
    nested.rmdir()
    assert not forge.verification.integration_report(planning, target)["success"]
    verify(workspace, capsys)
    assert forge.verification.integration_report(planning, target)["success"]
    git(target, "-c", "protocol.file.allow=always", "submodule", "update", "--init", "--", "services/vendor")
    assert not forge.verification.integration_report(planning, target)["success"]


@pytest.mark.parametrize("repository", ["submodule"], indirect=True)
def test_deinitialized_submodule_still_binds_ordinary_source(repository, capsys):
    workspace, nested = repository
    forge, planning, target = workspace
    git(target, "submodule", "deinit", "-f", "--", "services/vendor")
    source = nested / "source.py"
    source.write_text("value = 1\n")
    verify(workspace, capsys)
    assert forge.verification.integration_report(planning, target)["success"]
    source.write_text("value = 2\n")
    assert not forge.verification.integration_report(planning, target)["success"]


def test_nested_directory_links_bind_identity_without_traversing(repository, capsys, tmp_path, monkeypatch):
    workspace, nested = repository
    forge, planning, target = workspace
    external = tmp_path / "external"
    external.mkdir()
    (external / "private.py").write_text("outside = True\n")
    symlink(nested / "external", external)
    symlink(nested / "self", nested)
    execute = forge.mutable_inputs.subprocess.run
    open_file = forge.mutable_inputs.os.open

    def bounded_inventory(argv, *args, **kwargs):
        assert Path(kwargs["cwd"]).resolve().is_relative_to(target)
        return execute(argv, *args, **kwargs)

    def contained_open(path, *args, **kwargs):
        assert not Path(path).resolve().is_relative_to(external)
        return open_file(path, *args, **kwargs)

    monkeypatch.setattr(forge.mutable_inputs.subprocess, "run", bounded_inventory)
    monkeypatch.setattr(forge.mutable_inputs.os, "open", contained_open)
    verify(workspace, capsys)
    (external / "private.py").write_text("outside = False\n")
    assert forge.verification.integration_report(planning, target)["success"]
    (nested / "external").unlink()
    symlink(nested / "external", target)
    assert not forge.verification.integration_report(planning, target)["success"]


@pytest.mark.parametrize("entry", [".", "..", "../outside", "absolute", "linked/nested"])
def test_git_boundaries_cannot_escape_or_recurse_without_progress(workspace, tmp_path, monkeypatch, entry):
    forge, planning, target = workspace
    external = tmp_path / "outside"
    (external / "nested").mkdir(parents=True)
    if entry == "linked/nested":
        symlink(target / "linked", external)
    name = str(external) if entry == "absolute" else entry
    calls = []

    def inventory(argv, *, cwd, **kwargs):
        calls.append(cwd)
        assert len(calls) == 1, "Invalid boundaries must be rejected before traversal"
        return subprocess.CompletedProcess(argv, 0, name.encode() + b"\0")

    (target / ".git").mkdir()
    monkeypatch.setattr(forge.mutable_inputs.subprocess, "run", inventory)
    with pytest.raises(ValueError, match="target directory|repository boundary"):
        forge.mutable_inputs.verification_snapshot(planning, target)
    assert calls == [target]


@pytest.mark.parametrize("destination", ["root", "outside"])
def test_resolved_directory_boundaries_cannot_escape_or_recurse_to_root(workspace, tmp_path, monkeypatch, destination):
    forge, planning, target = workspace
    boundary = target / "boundary"
    boundary.mkdir()
    (target / ".git").mkdir()
    resolved = target if destination == "root" else tmp_path
    resolve = Path.resolve
    calls = []

    def inventory(argv, *, cwd, **kwargs):
        calls.append(cwd)
        assert len(calls) == 1, "Resolved boundaries must be checked before recursion"
        return subprocess.CompletedProcess(argv, 0, b"boundary\0")

    monkeypatch.setattr(Path, "resolve", lambda path, *args, **kwargs:
                        resolved if path == boundary else resolve(path, *args, **kwargs))
    monkeypatch.setattr(forge.mutable_inputs.subprocess, "run", inventory)
    with pytest.raises(ValueError, match="repository boundary"):
        forge.mutable_inputs.verification_snapshot(planning, target)
    assert calls == [target]
