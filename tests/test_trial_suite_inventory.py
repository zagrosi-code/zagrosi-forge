"""Independent suite inventory contract regressions."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import os
from pathlib import Path
import stat
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from coding_trial_inventory import copy_snapshot, fingerprint, inventory, project
from trial_suite_fixtures import link, regular_entries, write_files


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "task"
    write_files(root, {"backend/app/service.py": "VALUE = 1\n",
                       "backend/application/other.py": "VALUE = 2\n",
                       "specs/test_service.py": "assert True\n",
                       "settings.toml": 'mode = "strict"\n'})
    return root


def test_inventory_has_every_file_directory_mode_and_root_configuration(tree):
    expected = regular_entries(tree, ("backend/app/service.py", "backend/application/other.py",
                                      "specs/test_service.py", "settings.toml"))
    result = inventory(tree)
    assert result == expected
    assert "." not in result
    assert list(result) == sorted(result)


def test_projection_uses_path_components_and_keeps_structural_ancestors(tree):
    entries = inventory(tree)
    before = deepcopy(entries)
    selected = project(entries, ("backend/app", "settings.toml"))
    assert set(selected) == {"backend", "backend/app", "backend/app/service.py", "settings.toml"}
    assert selected == {name: before[name] for name in selected}
    assert entries == before


def test_fingerprint_is_canonical_and_does_not_modify_entries():
    entries = {"b": {"mode": 420, "type": "file", "sha256": "b" * 64},
               "a": {"type": "directory", "mode": 493}}
    before = deepcopy(entries)
    canonical = ('{"a":{"mode":493,"type":"directory"},'
                 '"b":{"mode":420,"sha256":"' + "b" * 64 + '","type":"file"}}')
    assert fingerprint(entries) == hashlib.sha256(canonical.encode()).hexdigest()
    assert fingerprint(dict(reversed(list(entries.items())))) == fingerprint(entries)
    assert entries == before


@pytest.mark.parametrize("change", ["content", "root-config", "new-file", "rename", "delete", "type", "mode"])
def test_material_changes_invalidate_candidate_identity(tree, change):
    source = tree / "backend/app/service.py"
    before = fingerprint(inventory(tree))
    if change == "content":
        source.write_text("VALUE = 3\n")
    elif change == "root-config":
        (tree / "settings.toml").write_text('mode = "relaxed"\n')
    elif change == "new-file":
        (tree / "undeclared.txt").write_text("new root file\n")
    elif change == "rename":
        source.rename(source.with_name("renamed.py"))
    elif change == "delete":
        source.unlink()
    elif change == "type":
        source.unlink()
        source.mkdir()
    else:
        if os.name != "posix":
            pytest.skip("POSIX permission-mode contract")
        source.chmod(stat.S_IMODE(source.stat().st_mode) ^ stat.S_IXUSR)
    assert fingerprint(inventory(tree)) != before


def test_declared_generated_bytes_are_pruned_but_new_root_files_remain_visible(tree):
    baseline = inventory(tree)
    (tree / ".cache").mkdir()
    (tree / ".cache/output.bin").write_bytes(b"first")
    before = inventory(tree, excluded=(".cache",), baseline=baseline)
    (tree / ".cache/output.bin").write_bytes(b"changed")
    assert inventory(tree, excluded=(".cache",), baseline=baseline) == before == baseline
    (tree / "outside.txt").write_text("visible")
    assert "outside.txt" in inventory(tree, excluded=(".cache",), baseline=baseline)


@pytest.mark.parametrize("excluded", [("backend",), ("backend/app/service.py",), ("settings.toml",)])
def test_generated_exclusions_cannot_hide_baseline_material(tree, excluded):
    with pytest.raises(ValueError):
        inventory(tree, excluded=excluded, baseline=inventory(tree))


@pytest.mark.parametrize("path", ["../outside", "/outside", "backend/../specs", "./backend", "backend//app"])
def test_inventory_rejects_unnormalized_exclusions(tree, path):
    with pytest.raises(ValueError):
        inventory(tree, excluded=(path,))


@pytest.mark.parametrize("metadata", ["nested/.git/config", "nested/.git"])
def test_inventory_rejects_nested_repositories(tree, metadata):
    write_files(tree, {metadata: "git metadata"})
    with pytest.raises(ValueError):
        inventory(tree)


def test_special_file_is_rejected_without_blocking(tmp_path):
    if not hasattr(os, "mkfifo"):
        pytest.skip("Named pipes are unavailable")
    root = tmp_path / "task"
    root.mkdir()
    os.mkfifo(root / "pipe")
    # Bound the regression even if an implementation mistakenly reads the pipe.
    script = ("from pathlib import Path\n"
              "from coding_trial_inventory import inventory\n"
              "import sys\n"
              "try: inventory(Path(sys.argv[1]))\n"
              "except ValueError: raise SystemExit(0)\n"
              "raise SystemExit(1)\n")
    result = subprocess.run([sys.executable, "-B", "-c", script, str(root)],
                            env={**os.environ, "PYTHONPATH": str(ROOT / "tools")},
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr


def test_generated_root_is_pruned_before_inspecting_unsupported_entries(tree):
    baseline = inventory(tree)
    write_files(tree, {".cache/nested/.git/config": "not part of the task"})
    assert inventory(tree, excluded=(".cache",), baseline=baseline) == baseline


def test_regular_hardlinks_are_rejected(tree):
    try:
        os.link(tree / "settings.toml", tree / "settings-alias.toml")
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"Hardlinks are unavailable: {exc}")
    with pytest.raises(ValueError):
        inventory(tree)


@pytest.mark.parametrize("target", ["../private.txt", "missing", "loop"])
def test_inventory_rejects_escaping_dangling_and_cyclic_links(tree, target):
    (tree.parent / "private.txt").write_text("not part of task")
    link(tree / "loop", target)
    with pytest.raises(ValueError):
        inventory(tree)


@pytest.mark.parametrize("spelling", ["absolute-in-tree", "backslash-in-tree"])
def test_link_targets_remain_portable_after_snapshot_relocation(tree, spelling):
    target = str(tree / "backend/app/service.py")
    if spelling == "backslash-in-tree":
        target = r"backend\app\service.py"
        (tree / target).write_text("VALUE = 1\n")
    link(tree / "alias.py", target)
    with pytest.raises(ValueError):
        inventory(tree)


@pytest.mark.parametrize("cycle", ["ancestor", "mutual-directories"])
def test_directory_traversal_cycles_are_rejected_without_following_links(tree, cycle):
    if cycle == "ancestor":
        link(tree / "backend/app/up", "..", directory=True)
    else:
        (tree / "left").mkdir()
        (tree / "right").mkdir()
        link(tree / "left/next", "../right", directory=True)
        link(tree / "right/next", "../left", directory=True)
    with pytest.raises(ValueError):
        inventory(tree)


def test_relative_acyclic_directory_alias_is_retained_and_copies_as_a_link(tree, tmp_path):
    link(tree / "current", "backend/app", directory=True)
    expected = project(inventory(tree), ("backend/app", "current"))
    assert expected["current"]["target"] == "backend/app"
    assert "current/service.py" not in expected
    destination = tmp_path / "copy"
    copy_snapshot(tree, destination, expected)
    assert inventory(destination) == expected
    assert (destination / "current").is_symlink()
    assert os.readlink(destination / "current") == "backend/app"
    assert (destination / "backend/app/service.py").read_bytes() == (tree / "backend/app/service.py").read_bytes()
    if os.name == "posix":
        # Keep literal POSIX link identity on Windows; native traversal is supplementary.
        assert (destination / "current/service.py").read_bytes() == (tree / "backend/app/service.py").read_bytes()


def test_links_preserve_literal_targets_and_target_changes_affect_identity(tree):
    alias = tree / "backend/app/current.py"
    link(alias, "service.py")
    before = inventory(tree)
    assert before["backend/app/current.py"] == {
        "type": "symlink", "mode": stat.S_IMODE(alias.lstat().st_mode), "target": "service.py"}
    (tree / "backend/app/alternate.py").write_text("VALUE = 1\n")
    original = fingerprint(inventory(tree))
    alias.unlink()
    link(alias, "alternate.py")
    assert fingerprint(inventory(tree)) != original


@pytest.mark.parametrize("target", ["../../settings.toml", "../../.workflow/notes.py", "../../.cache/generated.py"])
def test_projection_rejects_links_to_unassessed_material(tree, target):
    write_files(tree, {".workflow/notes.py": "VALUE = 1\n", ".cache/generated.py": "VALUE = 1\n"})
    link(tree / "backend/app/alias.py", target)
    with pytest.raises(ValueError):
        project(inventory(tree), ("backend/app",))


def test_copy_preserves_an_in_tree_link_and_exact_selected_material(tree, tmp_path):
    link(tree / "backend/app/current.py", "service.py")
    if os.name == "posix":
        (tree / "backend/app").chmod(0o750)
        (tree / "backend/app/service.py").chmod(0o640)
    expected = project(inventory(tree), ("backend/app", "settings.toml"))
    destination = tmp_path / "copy"
    copy_snapshot(tree, destination, expected)
    assert inventory(destination) == expected
    assert (destination / "backend/app/current.py").is_symlink()
    assert os.readlink(destination / "backend/app/current.py") == "service.py"
    assert not (destination / "specs").exists()
    assert not (destination / "backend/application").exists()
    assert expected == project(inventory(tree), ("backend/app", "settings.toml"))


def test_copy_never_overwrites_existing_user_destination(tree, tmp_path):
    destination = tmp_path / "existing"
    write_files(destination, {"user.txt": "keep me"})
    with pytest.raises(FileExistsError):
        copy_snapshot(tree, destination, inventory(tree))
    assert (destination / "user.txt").read_text() == "keep me"
    assert list(destination.iterdir()) == [destination / "user.txt"]


@pytest.mark.parametrize("through_alias", [False, True])
def test_copy_rejects_destination_inside_source_before_mutating_it(tree, tmp_path, through_alias):
    expected = inventory(tree)
    parent = tree
    if through_alias:
        parent = tmp_path / "task-alias"
        link(parent, str(tree), directory=True)
    destination = parent / "snapshot"
    with pytest.raises(ValueError):
        copy_snapshot(tree, destination, expected)
    assert not destination.exists()
    assert inventory(tree) == expected


@pytest.mark.parametrize("change", ["content", "deleted", "type", "mode", "link-target", "file-symlink", "hardlink"])
def test_copy_rejects_source_changed_since_inventory(tree, tmp_path, change):
    source = tree / "backend/app/service.py"
    link(tree / "backend/app/current.py", "service.py")
    expected = inventory(tree)
    if change == "content":
        source.write_text("VALUE = 9\n")
    elif change == "deleted":
        source.unlink()
    elif change == "type":
        source.unlink()
        source.mkdir()
    elif change == "mode":
        if os.name != "posix":
            pytest.skip("POSIX permission-mode contract")
        source.chmod(stat.S_IMODE(source.stat().st_mode) ^ stat.S_IXUSR)
    elif change == "hardlink":
        try:
            os.link(source, tmp_path / "source-alias.py")
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f"Hardlinks are unavailable: {exc}")
    elif change == "file-symlink":
        outside = tmp_path / "outside.py"
        outside.write_bytes(source.read_bytes())
        source.unlink()
        link(source, str(outside))
    else:
        alias = tree / "backend/app/current.py"
        alias.unlink()
        link(alias, "../../settings.toml")
    with pytest.raises(ValueError):
        copy_snapshot(tree, tmp_path / "copy", expected)


def test_long_cyclic_link_input_has_a_controlled_validation_error(tmp_path):
    for index in range(1100):
        link(tmp_path / f"link{index:04}", f"link{(index + 1) % 1100:04}")
    with pytest.raises(ValueError):
        inventory(tmp_path)


def test_reusing_the_same_directory_alias_in_one_target_is_not_a_cycle(tmp_path):
    (tmp_path / "directory").mkdir()
    (tmp_path / "directory/value.py").write_text("VALUE = 1\n")
    link(tmp_path / "alias", "directory", directory=True)
    link(tmp_path / "twice.py", "alias/../alias/value.py")
    result = inventory(tmp_path)
    assert result["twice.py"]["target"] == "alias/../alias/value.py"
    copied = tmp_path.with_name(tmp_path.name + "-copy")
    copy_snapshot(tmp_path, copied, result)
    assert inventory(copied) == result
    assert os.readlink(copied / "twice.py") == "alias/../alias/value.py"
    if os.name == "posix":
        # Native Windows traversal rejects this literal target; virtual validation is portable.
        assert (tmp_path / "twice.py").read_text() == "VALUE = 1\n"


def test_project_requires_a_string_content_digest():
    entry = {"type": "file", "mode": 0o644, "sha256": int("1" * 64)}
    with pytest.raises(ValueError):
        project({"value.py": entry}, ("value.py",))


def test_directory_link_cannot_expand_a_partial_structural_ancestor(tmp_path):
    (tmp_path / "backend/app").mkdir(parents=True)
    (tmp_path / "backend/app/value.py").write_text("VALUE = 1\n")
    (tmp_path / "backend/private.py").write_text("PRIVATE = 2\n")
    link(tmp_path / "alias", "backend", directory=True)
    with pytest.raises(ValueError):
        project(inventory(tmp_path), ("backend/app", "alias"))


def test_file_replaced_by_link_at_open_is_never_read_or_copied(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    victim = source / "value.py"
    victim.write_text("VALUE = 1\n")
    expected = inventory(source)
    private = tmp_path / "private.py"
    private.write_text("PRIVATE = 2\n")
    original_open = os.open
    replaced = False

    def replace_before_open(path, flags, *args, **kwargs):
        nonlocal replaced
        if Path(path).name == "value.py" and not replaced:
            replaced = True
            victim.unlink()
            link(victim, str(private))
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", replace_before_open)
    output = tmp_path / "copy"
    with pytest.raises(ValueError):
        copy_snapshot(source, output, expected)
    assert replaced
    assert not (output / "value.py").exists()


@pytest.mark.parametrize("copy", [False, True])
def test_directory_mode_change_during_initial_stat_does_not_accept_stale_identity(tmp_path, monkeypatch, copy):
    if os.name != "posix":
        pytest.skip("POSIX directory descriptor and mode contract")
    source = tmp_path / "source"
    child = source / "child"
    child.mkdir(parents=True)
    (child / "value.py").write_text("VALUE = 1\n")
    child.chmod(0o755)
    expected = inventory(source)
    original_stat = os.stat
    changed = False

    def chmod_after_stat(path, *args, **kwargs):
        nonlocal changed
        result = original_stat(path, *args, **kwargs)
        if path == "child" and kwargs.get("dir_fd") is not None and not changed:
            changed = True
            child.chmod(0o700)
        return result

    monkeypatch.setattr(os, "stat", chmod_after_stat)
    with pytest.raises(ValueError):
        if copy:
            copy_snapshot(source, tmp_path / "copy", expected)
        else:
            inventory(source)
    assert changed


def test_included_resources_prune_unreferenced_material_before_inspection(tmp_path):
    write_files(tmp_path, {"oracle.py": "# Private controller\n", "checks/helper.py": "VALUE = 1\n",
                           ".git/config": "Unrelated curator metadata\n",
                           "export/nested/.git/config": "Other task material\n"})
    if hasattr(os, "mkfifo"):
        os.mkfifo(tmp_path / "unrelated-pipe")
    names = ("oracle.py", "checks/helper.py")
    assert inventory(tmp_path, included=names) == regular_entries(tmp_path, names)
    assert inventory(tmp_path, included=()) == {}


def test_included_directory_contains_its_complete_subtree_and_ancestors(tree):
    assert inventory(tree, included=("backend/app",)) == regular_entries(tree, ("backend/app/service.py",))


@pytest.mark.parametrize("names", [("missing.py",), ("../settings.toml",)])
def test_included_resources_reject_missing_or_escaping_selectors(tree, names):
    with pytest.raises(ValueError):
        inventory(tree, included=names)


def test_included_link_cannot_read_an_unselected_target(tree):
    link(tree / "alias.py", "backend/app/service.py")
    with pytest.raises(ValueError):
        inventory(tree, included=("alias.py",))


@pytest.mark.parametrize("selected", ["backend/app", "backend"])
def test_included_directory_link_needs_its_complete_target_selected(tmp_path, selected):
    source = tmp_path / "source"
    (source / "backend/app").mkdir(parents=True)
    (source / "backend/app/value.py").write_text("VALUE = 1\n")
    (source / "backend/private.py").write_text("PRIVATE = 2\n")
    try:
        (source / "alias").symlink_to("backend", target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"Symlinks are unavailable: {exc}")
    if selected == "backend/app":
        with pytest.raises(ValueError):
            inventory(source, included=(selected, "alias"))
        return
    entries = inventory(source, included=(selected, "alias"))
    destination = tmp_path / "copy"
    copy_snapshot(source, destination, entries)
    assert inventory(destination) == entries
    assert (destination / "alias").is_symlink()
    assert (destination / "alias/private.py").read_text() == "PRIVATE = 2\n"
