"""Private reservation behavior; fictional ownership never proves real admission."""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from coding_trial_reservation import _save_bytes, _check_reservation, _reserved_attempt, _restore_reservation, _save_record
from native_reservation_fixtures import creation_anchor, make_reserved_native
from test_trial_suite_isolation import tree_state
from test_trial_suite_native_runtime import byte_hash, forbid_credential_reads, prepare_auth, save
from trial_suite_fixtures import digest, link, regular_entries, write_files


pytestmark = pytest.mark.skipif(os.name != "posix" or not hasattr(os, "O_NOFOLLOW") or os.open not in os.supports_dir_fd,
                                reason="Reservation restoration requires the admitted no-follow controller platform")
INITIAL = b'{"caller_record":"opaque fixture bytes; no invented state"}\n'


@pytest.fixture(autouse=True)
def no_processes(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("The private reservation leaf must not launch processes")
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)


@pytest.fixture
def reserved(tmp_path):
    return make_reserved_native(tmp_path, initial_attempt_bytes=INITIAL)


def enter(fixture, *, evidence=None, auth_file=None):
    return _reserved_attempt(fixture["suite"], "normalize", fixture["arm"], fixture["roots"],
                             evidence or fixture["loading_evidence"], auth_file=auth_file)


def persist_reservation(fixture):
    fixture["reservation_path"].write_text(json.dumps(fixture["reservation"], indent=2) + "\n", encoding="utf-8")


def mutable_roots(fixture):
    return {"workspace": Path(fixture["roots"]["workspace"]), "home": fixture["native"] / "home",
            "codex": fixture["native"] / "codex", "generated": Path(fixture["roots"]["generated"])}


def replace_directory(path):
    old = path.with_name(path.name + "-displaced")
    path.rename(old)
    shutil.copytree(old, path, symlinks=True)
    return old


def add_git_snapshot(fixture, *, forbidden=None):
    """Ordinary metadata bytes only; repository semantics belong to section03."""
    git = Path(fixture["roots"]["workspace"]) / ".git"
    files = {"HEAD": "ref: refs/heads/main\n", "config": "[core]\nrepositoryformatversion = 0\nbare = false\n",
             "refs/heads/main": "a" * 40 + "\n", "objects/info/fixture": "Synthetic metadata; never executed.\n"}
    if forbidden == "include":
        files["config"] += '[include]\npath = "/external/private/config"\n'
    elif forbidden == "alternates":
        files["objects/info/alternates"] = "/external/private/objects\n"
    write_files(git, files)
    snapshot = fixture["reservation_path"].parent / "initial/workspace_git"
    shutil.copytree(git, snapshot, symlinks=True)
    fixture["reservation"]["snapshots"]["workspace_git"] = {
        "path": "initial/workspace_git", "inventory_sha256": digest(regular_entries(git, tuple(files))),
        "anchor": creation_anchor(snapshot)}
    persist_reservation(fixture)
    return git, snapshot


@pytest.mark.parametrize("phase", ["loading", "writer"])
def test_exact_phase_owns_lock_and_exclusive_records_without_changing_inputs(reserved, phase):
    evidence = reserved[phase + "_evidence"]
    if phase == "writer":
        # Its complete loading-bundle validation belongs to the public caller.
        reserved["loading_evidence"].mkdir()
        (reserved["loading_evidence"] / "retained.json").write_text("{}\n", encoding="utf-8")
    before = {name: tree_state(path) for name, path in mutable_roots(reserved).items()}
    original_suite = deepcopy(reserved["suite"])
    lock = reserved["reservation_path"].parent / "execution.lock"
    with enter(reserved, evidence=evidence) as state:
        assert evidence.is_dir() and lock.is_file()
        _check_reservation(state)
        _save_record(state, "observation.json", {"status": "observed"})
        assert json.loads((evidence / "observation.json").read_text()) == {"status": "observed"}
    assert not lock.exists()
    assert reserved["trial_path"].read_bytes() == INITIAL
    assert reserved["suite"] == original_suite
    assert {name: tree_state(path) for name, path in mutable_roots(reserved).items()} == before
    with pytest.raises(ValueError, match="^input-exists:"):
        with enter(reserved, evidence=evidence):
            pytest.fail("An already retained phase was replayed")


@pytest.mark.parametrize("collision", ["loading", "writer", "execution.lock"])
def test_loading_refuses_any_existing_phase_or_lock_without_replacing_it(reserved, collision):
    existing = reserved["reservation_path"].parent / collision
    existing.write_bytes(b"original owner\n")
    with pytest.raises(ValueError, match="^input-exists:"):
        with enter(reserved):
            pytest.fail("An existing phase or lock was adopted")
    assert existing.read_bytes() == b"original owner\n"


@pytest.mark.parametrize("location", ["wrong-name", "wrong-parent"])
def test_phase_name_and_parent_are_fixed(reserved, tmp_path, location):
    evidence = (reserved["reservation_path"].parent / "another-loading" if location == "wrong-name"
                else tmp_path / "loading")
    with pytest.raises(ValueError):
        with enter(reserved, evidence=evidence):
            pytest.fail("An arbitrary evidence root was accepted")
    assert not evidence.exists()


def test_nested_call_cannot_acquire_or_release_another_calls_lock(reserved):
    lock = reserved["reservation_path"].parent / "execution.lock"
    with enter(reserved) as state:
        identity = (lock.stat().st_dev, lock.stat().st_ino)
        with pytest.raises(ValueError, match="^input-exists:"):
            with enter(reserved):
                pytest.fail("Concurrent ownership was accepted")
        assert (lock.stat().st_dev, lock.stat().st_ino) == identity
        _save_record(state, "retained.json", {"owner": "first"})
    assert not lock.exists()



def test_context_release_never_removes_a_replacement_lock(reserved):
    lock = reserved["reservation_path"].parent / "execution.lock"
    with enter(reserved):
        lock.rename(lock.with_name("displaced-lock"))
        lock.write_bytes(b"a different owner now holds this pathname\n")
    assert lock.read_bytes() == b"a different owner now holds this pathname\n"


@pytest.mark.parametrize("selector", ["task", "arm"])
def test_requested_task_and_arm_must_match_the_reserved_selection(reserved, selector):
    task, arm = ("other-task", reserved["arm"]) if selector == "task" else ("normalize", "alpha")
    with pytest.raises(ValueError):
        with _reserved_attempt(reserved["suite"], task, arm, reserved["roots"], reserved["loading_evidence"]):
            pytest.fail("A reservation was reassigned to another task or arm")
    assert not reserved["loading_evidence"].exists()


def test_failed_attempt_keeps_evidence_and_cannot_be_replayed(reserved):
    with pytest.raises(RuntimeError, match="controlled failure"):
        with enter(reserved) as state:
            _save_record(state, "failure.json", {"status": "failed"})
            raise RuntimeError("controlled failure")
    assert json.loads((reserved["loading_evidence"] / "failure.json").read_text()) == {"status": "failed"}
    assert not (reserved["reservation_path"].parent / "execution.lock").exists()
    with pytest.raises(ValueError, match="^input-exists:"):
        with enter(reserved):
            pytest.fail("A failed attempt was reused")


@pytest.mark.parametrize("target", ["workspace", "snapshot", "private"])
def test_same_byte_directory_replacement_does_not_inherit_creation_ownership(reserved, target):
    path = {"workspace": Path(reserved["roots"]["workspace"]),
            "snapshot": reserved["reservation_path"].parent / "initial/workspace",
            "private": reserved["reservation_path"].parent}[target]
    before = tree_state(path)
    old = replace_directory(path)
    assert tree_state(path) == before and path.stat().st_ino != old.stat().st_ino
    with pytest.raises(ValueError):
        with enter(reserved):
            pytest.fail("Same-byte replacement acquired the original ownership")
    assert tree_state(path) == before


@pytest.mark.parametrize("target", ["trial", "initial-record", "runtime", "reservation"])
def test_exact_bound_record_bytes_cannot_drift_during_an_operation(reserved, target):
    path = {"trial": reserved["trial_path"], "initial-record": reserved["initial_record_path"],
            "runtime": reserved["native"] / "runtime.json", "reservation": reserved["reservation_path"]}[target]
    with enter(reserved) as state:
        changed = path.read_bytes() + b" \n"
        path.write_bytes(changed)
        with pytest.raises(ValueError):
            _check_reservation(state)
        assert path.read_bytes() == changed


def test_current_root_replacement_prevents_restore_without_touching_replacement(reserved):
    workspace = Path(reserved["roots"]["workspace"])
    with enter(reserved) as state:
        replace_directory(workspace)
        (workspace / "replacement-owned.txt").write_bytes(b"must remain\n")
        before = tree_state(workspace)
        with pytest.raises(ValueError):
            _restore_reservation(state)
        assert tree_state(workspace) == before


def test_restore_keeps_root_inodes_and_removes_links_without_following_them(reserved, tmp_path):
    roots = mutable_roots(reserved)
    identities = {name: creation_anchor(path) for name, path in roots.items()}
    snapshots = {name: tree_state(reserved["reservation_path"].parent / "initial" / name) for name in roots}
    outside = tmp_path / "unrelated-user-files"
    write_files(outside, {"keep.txt": "Do not read through or remove the malicious link.\n"})
    before_outside = tree_state(outside)
    with enter(reserved) as state:
        shutil.rmtree(roots["workspace"] / "backend")
        link(roots["workspace"] / "backend", str(outside), directory=True)
        for name in ("home", "codex", "generated"):
            link(roots[name] / "escape", str(outside), directory=True)
            (roots[name] / "ordinary-dirty.txt").write_bytes(b"expected mutable state\n")
        (roots["workspace"] / ".git").mkdir()
        (roots["workspace"] / ".git/HEAD").write_bytes(b"candidate-created metadata\n")
        _check_reservation(state)  # Dirty interiors are expected; original roots remain owned.
        result = _restore_reservation(state)
        assert result["schema"] == "coding-trial-native-restoration/v1"
        assert result["status"] == "passed" and result["anchors_unchanged"] is True
        assert result["immutable_unchanged"] is None  # Loading owns the immutable-input recheck.
        assert result["inventory_sha256"] == {name: None if record is None else record["inventory_sha256"]
                                                 for name, record in reserved["reservation"]["snapshots"].items()}
        assert {name: tree_state(path) for name, path in roots.items()} == snapshots
        assert {name: creation_anchor(path) for name, path in roots.items()} == identities
        assert tree_state(outside) == before_outside
        _check_reservation(state)


def test_optional_git_snapshot_restores_original_metadata_bytes(reserved):
    git, snapshot = add_git_snapshot(reserved)
    expected = tree_state(snapshot)
    workspace_identity = creation_anchor(git.parent)
    with enter(reserved) as state:
        shutil.rmtree(git)
        git.mkdir()
        (git / "HEAD").write_bytes(b"candidate rewrote provenance\n")
        _restore_reservation(state)
        assert tree_state(git) == expected
        assert creation_anchor(git.parent) == workspace_identity


@pytest.mark.parametrize("kind", ["gitdir", "include", "alternates"])
def test_git_snapshot_cannot_grant_external_metadata_access(reserved, kind):
    if kind == "gitdir":
        (Path(reserved["roots"]["workspace"]) / ".git").write_text("gitdir: /external/private/repo\n", encoding="utf-8")
    else:
        add_git_snapshot(reserved, forbidden=kind)
    with pytest.raises(ValueError):
        with enter(reserved):
            pytest.fail("External Git metadata was admitted as owned snapshot content")


def test_writer_phase_cannot_reset_candidate_work(reserved):
    with enter(reserved, evidence=reserved["writer_evidence"]) as state:
        candidate = Path(reserved["roots"]["workspace"]) / "backend/app/service.py"
        candidate.write_bytes(b"keep ordinary writer output\n")
        with pytest.raises(ValueError):
            _restore_reservation(state)
        assert candidate.read_bytes() == b"keep ordinary writer output\n"


@pytest.mark.parametrize("name", ["../outside.json", "nested/record.json"])
def test_record_names_cannot_escape_the_pinned_evidence_root(reserved, name):
    with enter(reserved) as state:
        with pytest.raises(ValueError):
            _save_record(state, name, {"must": "not write"})
        assert not (reserved["reservation_path"].parent / "outside.json").exists()
        assert not (reserved["loading_evidence"] / "nested").exists()


def test_record_collision_never_follows_a_link_or_replaces_an_existing_record(reserved, tmp_path):
    outside = tmp_path / "user-record.json"
    outside.write_bytes(b"original user data\n")
    with enter(reserved) as state:
        link(reserved["loading_evidence"] / "record.json", str(outside))
        with pytest.raises(ValueError, match="^input-exists:"):
            _save_record(state, "record.json", {"bad": True})
        assert outside.read_bytes() == b"original user data\n"
        _save_record(state, "ordinary.json", {"first": True})
        with pytest.raises(ValueError, match="^input-exists:"):
            _save_record(state, "ordinary.json", {"second": True})
        assert json.loads((reserved["loading_evidence"] / "ordinary.json").read_text()) == {"first": True}


def test_record_write_rejects_a_replaced_evidence_directory(reserved):
    with enter(reserved) as state:
        old = replace_directory(reserved["loading_evidence"])
        with pytest.raises(ValueError):
            _save_record(state, "unexpected.json", {"bad": True})
        assert not (old / "unexpected.json").exists()
        assert not (reserved["loading_evidence"] / "unexpected.json").exists()


def test_external_auth_identity_is_bound_without_content_access(reserved, tmp_path, monkeypatch):
    auth = prepare_auth(reserved, tmp_path)
    snapshot = reserved["reservation_path"].parent / "initial/codex"
    (snapshot / "auth.json").touch()
    info = auth.stat()
    reserved["reservation"]["auth"] = {"path": str(auth.resolve()), "device": info.st_dev,
                                          "inode": info.st_ino, "mode": info.st_mode}
    reserved["reservation"]["snapshots"]["codex"]["inventory_sha256"] = digest(
        regular_entries(snapshot, ("plugins", "auth.json")))
    configuration = deepcopy(reserved["suite"])
    for arm in configuration["arms"].values():
        arm["loading"]["receipt"] = None
    reserved["reservation"]["loading_configuration_sha256"] = digest(configuration)
    save(reserved["suite_root"] / "suite.json", reserved["suite"])
    reserved["reservation"]["suite_source_sha256"] = byte_hash(reserved["suite_root"] / "suite.json")
    persist_reservation(reserved)
    original = auth.read_bytes()
    forbid_credential_reads(monkeypatch, auth)
    with enter(reserved, auth_file=auth) as state:
        _check_reservation(state)
        old = auth.with_name("old-auth.json")
        auth.rename(old)
        auth.write_bytes(original)
        with pytest.raises(ValueError):
            _check_reservation(state)
        assert (old.stat().st_dev, old.stat().st_ino) == (info.st_dev, info.st_ino)


@pytest.mark.parametrize("change", ["removed", "replaced"])
@pytest.mark.parametrize("operation", ["check", "save"])
def test_active_lock_loss_prevents_further_reservation_operations(reserved, change, operation):
    lock = reserved["reservation_path"].parent / "execution.lock"
    record = reserved["loading_evidence"] / "after-lock-loss.json"
    replacement = b"a different owner now holds this pathname\n"
    with enter(reserved) as state:
        if change == "removed":
            lock.unlink()
        else:
            lock.rename(lock.with_name("displaced-lock"))
            lock.write_bytes(replacement)
        with pytest.raises(ValueError):
            if operation == "check":
                _check_reservation(state)
            else:
                _save_record(state, record.name, {"must": "not persist"})
        assert not record.exists()
    if change == "replaced":
        assert lock.read_bytes() == replacement
    else:
        assert not lock.exists()


def test_raw_evidence_bytes_are_preserved_and_cannot_be_overwritten(reserved):
    raw = b"\x00non-JSON evidence:\xff\n"
    target = reserved["loading_evidence"] / "private-control.txt"
    with enter(reserved) as state:
        _save_bytes(state, "private-control.txt", raw)
        assert target.read_bytes() == raw
        with pytest.raises(ValueError, match="^input-exists:"):
            _save_bytes(state, "private-control.txt", b"replacement must not persist")
        assert target.read_bytes() == raw
    assert target.read_bytes() == raw
