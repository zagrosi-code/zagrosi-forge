"""Preserve repository path data instead of selecting a sibling."""
import os

import pytest

from forge_test_helpers import load_zagrosi_module
from test_team_git import git


_POSIX_NAME = pytest.mark.skipif(os.name == "nt", reason="POSIX whitespace filename fixture")


def _init(root, branch, *, git_dir=None):
    options = ["init", f"--initial-branch={branch}"]
    if git_dir is not None:
        options.append(f"--separate-git-dir={git_dir}")
    git(root.parent, *options, str(root))


def _commit(root, content):
    (root / "source.txt").write_text(content, encoding="utf-8")
    git(root, "add", "source.txt")
    git(root, "commit", "-m", content.strip())
    return git(root, "rev-parse", "--verify", "HEAD").stdout.strip()


def _snapshot(*roots):
    return {(str(root), path.relative_to(root)): path.read_bytes()
            for root in roots for path in root.rglob("*") if path.is_file()}


@pytest.mark.parametrize("suffix", [
    pytest.param(" ", marks=_POSIX_NAME, id="space"),
    pytest.param("\t", marks=_POSIX_NAME, id="tab"),
    pytest.param("\n", marks=_POSIX_NAME, id="lf"),
    pytest.param("\r", marks=_POSIX_NAME, id="cr"),
    pytest.param("\u00a0", id="nbsp"),
])
def test_discovery_keeps_selected_checkout_instead_of_trimmed_sibling(tmp_path, suffix):
    neighbor, selected = tmp_path / "checkout", tmp_path / f"checkout{suffix}"
    _init(neighbor, "neighbor")
    neighbor_head = _commit(neighbor, "Neighbor only\n")
    _init(selected, "selected")
    selected_head = _commit(selected, "Selected only\n")
    assert selected_head != neighbor_head
    before = _snapshot(neighbor, selected)
    forge = load_zagrosi_module()

    actual = forge.team_git.Repository.discover(selected)

    assert actual.root == selected.resolve()
    assert actual.git_dir == (selected / ".git").resolve()
    assert actual.common_dir == (selected / ".git").resolve()
    assert actual.head == selected_head
    assert actual.branch == "selected"
    assert _snapshot(neighbor, selected) == before


@_POSIX_NAME
def test_unborn_selected_checkout_cannot_borrow_trimmed_sibling_head(tmp_path):
    neighbor, selected = tmp_path / "checkout", tmp_path / "checkout "
    _init(neighbor, "neighbor")
    _commit(neighbor, "Neighbor only\n")
    _init(selected, "selected")
    assert git(selected, "rev-parse", "--verify", "HEAD", check=False).returncode != 0
    before = _snapshot(neighbor, selected)
    forge = load_zagrosi_module()

    with pytest.raises(forge.team_state.TeamError) as failed:
        forge.team_git.Repository.discover(selected)

    assert failed.value.code == "team-repository"
    assert str(failed.value) == "Team collaboration requires a Git working tree with an initial commit."
    assert _snapshot(neighbor, selected) == before


def test_discovery_preserves_separate_git_directory_and_linked_common_directory(tmp_path):
    root, admin = tmp_path / "worktree", tmp_path / "admin\u00a0"
    trimmed = tmp_path / "admin"
    trimmed.mkdir()
    (trimmed / "sentinel").write_text("Unrelated directory\n", encoding="utf-8")
    _init(root, "main", git_dir=admin)
    head = _commit(root, "Selected only\n")
    linked = tmp_path / "linked"
    git(root, "worktree", "add", "--detach", str(linked))
    expected_linked_admin = admin / "worktrees" / "linked"
    assert (expected_linked_admin / "HEAD").is_file()
    before = _snapshot(root, admin, trimmed, linked)
    forge = load_zagrosi_module()

    actual = forge.team_git.Repository.discover(root)
    assert actual.root == root.resolve()
    assert actual.git_dir == admin.resolve()
    assert actual.common_dir == admin.resolve()
    assert actual.head == head and actual.branch == "main"
    other = forge.team_git.Repository.discover(linked)
    assert other.root == linked.resolve()
    assert other.git_dir == expected_linked_admin.resolve()
    assert other.common_dir == admin.resolve()
    assert other.head == head and other.branch is None
    assert _snapshot(root, admin, trimmed, linked) == before


def test_whitespace_git_directory_rejects_missing_managed_marker(tmp_path):
    root, admin = tmp_path / "worktree", tmp_path / "admin\u00a0"
    _init(root, "main", git_dir=admin)
    _commit(root, "Selected only\n")
    # The missing-marker branch recognizes this managed shape before reading
    # a descriptor; no fabricated descriptor or alternate runtime is needed.
    workspace = admin / "forge-plans" / ("a" * 64)
    workspace.mkdir(parents=True)
    ordinary = tmp_path / "ordinary" / "forge-plans" / ("b" * 64)
    ordinary.mkdir(parents=True)
    before = _snapshot(root, admin, ordinary)
    forge = load_zagrosi_module()

    with pytest.raises(forge.team_state.TeamError, match="prepared plan marker is missing") as failed:
        forge.team_plans.validate(workspace, root)

    assert failed.value.code == "team-plan-invalid"
    assert forge.team_plans.validate(ordinary, root) is None
    assert _snapshot(root, admin, ordinary) == before
