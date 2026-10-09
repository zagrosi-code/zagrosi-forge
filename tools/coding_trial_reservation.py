"""Private, creation-bound native attempt ownership and interior restoration."""
from __future__ import annotations

import configparser
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import stat
import uuid

from coding_trial_inventory import copy_snapshot, fingerprint, inventory, relative_path
from coding_trial_qualification import fields, integer, parse_json, read_bytes, require, sha

MUTABLE = ("workspace", "home", "codex", "generated")


def _identity(info):
    return info.st_dev, info.st_ino, info.st_mode


def _anchor(path, expected):
    fields(expected, "path device inode mode parent_device parent_inode", "Creation anchor")
    require(expected["path"] == str(path) and path.resolve(strict=True) == path, "Anchor path changed")
    for key in ("device", "inode", "mode", "parent_device", "parent_inode"):
        integer(expected[key], "Anchor " + key, minimum=0)
    info, parent = path.lstat(), path.parent.lstat()
    require(stat.S_ISDIR(info.st_mode) and stat.S_ISDIR(parent.st_mode), "Anchor is not a directory")
    require(_identity(info) == (expected["device"], expected["inode"], expected["mode"])
            and (parent.st_dev, parent.st_ino) == (expected["parent_device"], expected["parent_inode"]),
            "Creation anchor or parent changed")
    return info


def _regular(path):
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "Bound record is not a single-link regular file")
    return read_bytes(path.parent, path.name)


def _git_entries(path):
    entries = inventory(path)
    require(not {"commondir", "gitdir", "objects/info/alternates", "objects/info/http-alternates"} & entries.keys(),
            "Git metadata contains external indirection")
    for name in ("config", "config.worktree"):
        if name not in entries:
            continue
        parser = configparser.RawConfigParser(strict=False, allow_no_value=True)
        try:
            parser.read_string(_regular(path / name).decode("utf-8"))
        except (configparser.Error, UnicodeError) as exc:
            raise ValueError("Unsupported initial Git configuration") from exc
        for section in parser.sections():
            base = section.split()[0].split(".")[0].casefold()
            require(base not in {"include", "includeif"}, "Git configuration includes are unsupported")
            require(base != "core" or not parser.has_option(section, "worktree"), "Git worktree indirection is unsupported")
    return entries


def _mutable_inventories(state):
    paths = state["_paths"]
    result = {name: inventory(paths[name], excluded=(".git",) if name == "workspace" else ()) for name in MUTABLE}
    git = paths["workspace"] / ".git"
    result["workspace_git"] = _git_entries(git) if os.path.lexists(git) else None
    return result


def _auth_identity(value):
    if value is None:
        return None
    path = Path(value)
    require(path.is_absolute() and path.resolve(strict=True) == path, "Credential path changed")
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "Credential identity is not a regular file")
    return {"path": str(path), "device": info.st_dev, "inode": info.st_ino, "mode": info.st_mode}


def _check_reservation(state):
    """Check fixed identities, permitting expected changes inside mutable roots."""
    try:
        if "_lock" in state:
            handle, expected = state["_lock"]
            current = os.stat("execution.lock", dir_fd=state["_fds"]["private"], follow_symlinks=False)
            require(stat.S_ISREG(current.st_mode) and current.st_nlink == 1
                    and _identity(current) == _identity(os.fstat(handle)) == expected,
                    "Active reservation lock changed")
        for name, (path, expected) in state["_anchors"].items():
            info = _anchor(path, expected)
            if name in state["_fds"]:
                require(_identity(os.fstat(state["_fds"][name])) == _identity(info), "Pinned directory changed")
        for path, (digest, identity) in state["_files"].items():
            require(hashlib.sha256(_regular(path)).hexdigest() == digest
                    and _identity(path.lstat()) == identity, "Bound record changed")
        for name, (path, expected) in state["_snapshots"].items():
            current = _git_entries(path) if name == "workspace_git" else inventory(path)
            require(current == expected, "Initial snapshot changed")
        require(_auth_identity(state["_auth_file"]) == state["record"]["auth"], "Credential identity changed")
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"loading-unqualified: Reservation identity unavailable: {exc}") from exc


def _load_reservation(suite, task_id, arm_id, roots, evidence, auth_file):
    require(evidence.is_absolute() and evidence.name in {"loading", "writer"}, "Unexpected native evidence path")
    control, attempt = evidence.parent, evidence.parent.parent
    require(control.name == "private" and str(control) in roots["private"], "Unreserved native control path")
    native = attempt / "native-runtime"
    paths = {name: attempt / name for name in ("workspace", "generated")}
    paths.update(home=native / "home", codex=native / "codex")
    require(all(roots[name] == str(attempt / name) for name in ("workspace", "product", "public", "generated"))
            and roots["native_runtime"] == str(native), "Roots differ from the reserved layout")
    require(control.resolve(strict=True) == control, "Redirected native control path")
    for name in (("loading", "writer", "execution.lock") if evidence.name == "loading" else ("writer", "execution.lock")):
        if os.path.lexists(control / name):
            raise ValueError(f"input-exists: {control / name}")
    raw = _regular(control / "reservation.json")
    record = parse_json(raw)
    fields(record, "schema loading_configuration_sha256 suite_source_sha256 attempt_record task arm scheduled_position "
           "roots runtime_sha256 auth anchors snapshots", "Native reservation")
    require(record["schema"] == "coding-trial-native-reservation/v1", "Unsupported native reservation")
    require(record["task"] == task_id and record["arm"] == arm_id and record["roots"] == roots, "Reserved selection changed")
    integer(record["scheduled_position"], "Scheduled position", minimum=0)
    configuration = deepcopy(suite)
    for arm in configuration["arms"].values():
        arm["loading"]["receipt"] = None
    for name in ("loading_configuration_sha256", "suite_source_sha256", "runtime_sha256"):
        sha(record[name])
    require(record["loading_configuration_sha256"] == fingerprint(configuration), "Reserved configuration changed")
    initial = record["attempt_record"]
    fields(initial, "path initial_path sha256", "Initial attempt record")
    require(initial["path"] == "../trial.json" and initial["initial_path"] == "initial-attempt.json", "Unexpected initial record path")
    sha(initial["sha256"])
    auth = record["auth"]
    if auth is not None:
        fields(auth, "path device inode mode", "Credential identity")
        for key in ("device", "inode", "mode"):
            integer(auth[key], "Credential " + key, minimum=0)
    require(auth == _auth_identity(auth_file), "Reserved credential identity changed")
    anchored = {"attempt": attempt, "private": control, "initial": control / "initial", **paths}
    fields(record["anchors"], " ".join(anchored), "Reservation anchors")
    anchors = {name: (path, record["anchors"][name]) for name, path in anchored.items()}
    fields(record["snapshots"], "workspace workspace_git home codex generated", "Initial snapshots")
    snapshots, expected = {}, {}
    for name, snapshot in record["snapshots"].items():
        if name == "workspace_git" and snapshot is None:
            expected[name] = None
            continue
        fields(snapshot, "path inventory_sha256 anchor", "Initial snapshot")
        require(snapshot["path"] == "initial/" + name, "Unexpected snapshot path")
        sha(snapshot["inventory_sha256"])
        path = control / snapshot["path"]
        _anchor(path, snapshot["anchor"])
        entries = _git_entries(path) if name == "workspace_git" else inventory(path)
        require(name != "workspace" or ".git" not in entries, "Workspace snapshot must separate Git metadata")
        require(fingerprint(entries) == snapshot["inventory_sha256"], "Initial snapshot digest changed")
        anchors["snapshot:" + name] = path, snapshot["anchor"]
        snapshots[name], expected[name] = (path, entries), entries
    require(record["snapshots"]["workspace"]["inventory_sha256"] == suite["tasks"][task_id]["source"]["baseline_sha256"],
            "Workspace snapshot differs from the admitted baseline")
    files = {control / "reservation.json": hashlib.sha256(raw).hexdigest(),
             attempt / "trial.json": initial["sha256"], control / "initial-attempt.json": initial["sha256"],
             native / "runtime.json": record["runtime_sha256"]}
    state = {"record": record, "reservation_sha256": hashlib.sha256(raw).hexdigest(), "control": control,
             "evidence": evidence, "_paths": paths, "_anchors": anchors, "_fds": {}, "_snapshots": snapshots,
             "_expected": expected, "_auth_file": auth_file,
             "_files": {path: (digest, _identity(path.lstat())) for path, digest in files.items()}}
    _check_reservation(state)
    require(_mutable_inventories(state) == expected, "Mutable roots differ from their original snapshots")
    return state


def _pin(state, name):
    path, expected = state["_anchors"][name]
    info = _anchor(path, expected)
    handle = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    state["_fds"][name] = handle
    require(_identity(os.fstat(handle)) == _identity(info), "Directory changed before pinning")


@contextmanager
def _reserved_attempt(suite, task_id, arm_id, roots, evidence_dir, *, auth_file=None):
    """Own a fresh phase, never an existing attempt phase or another caller's lock."""
    if os.name != "posix" or not hasattr(os, "O_NOFOLLOW") or os.open not in os.supports_dir_fd:
        raise ValueError("unsupported-profile: Native restoration requires descriptor-relative no-follow operations")
    state, lock = None, None
    try:
        state = _load_reservation(suite, task_id, arm_id, roots, Path(evidence_dir), auth_file)
        for name in state["_anchors"]:
            _pin(state, name)
        control_fd = state["_fds"]["private"]
        try:
            lock = os.open("execution.lock", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=control_fd)
        except FileExistsError as exc:
            raise ValueError(f"input-exists: {state['control'] / 'execution.lock'}") from exc
        state["_lock"] = lock, _identity(os.fstat(lock))
        _check_reservation(state)
        try:
            os.mkdir(state["evidence"].name, mode=0o700, dir_fd=control_fd)
        except FileExistsError as exc:
            raise ValueError(f"input-exists: {state['evidence']}") from exc
        info, parent = state["evidence"].lstat(), os.fstat(control_fd)
        state["_anchors"]["evidence"] = state["evidence"], {
            "path": str(state["evidence"]), "device": info.st_dev, "inode": info.st_ino, "mode": info.st_mode,
            "parent_device": parent.st_dev, "parent_inode": parent.st_ino}
        _pin(state, "evidence")
        _check_reservation(state)
        yield state
    except (OSError, KeyError, TypeError) as exc:
        raise ValueError(f"loading-unqualified: Invalid native reservation: {exc}") from exc
    finally:
        if lock is not None:
            try:
                current = os.stat("execution.lock", dir_fd=state["_fds"]["private"], follow_symlinks=False)
                if _identity(current) == _identity(os.fstat(lock)):
                    os.unlink("execution.lock", dir_fd=state["_fds"]["private"])
            except FileNotFoundError:
                pass
            finally:
                os.close(lock)
        if state is not None:
            for handle in state["_fds"].values():
                os.close(handle)


def _save_record(state, name, value):
    raw = (json.dumps(value, indent=2, allow_nan=False) + "\n").encode("utf-8")
    _save_bytes(state, name, raw)


def _save_bytes(state, name, raw):
    relative_path(name)
    require("/" not in name, "Record must be a basename")
    _check_reservation(state)
    try:
        handle = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=state["_fds"]["evidence"])
    except FileExistsError as exc:
        raise ValueError(f"input-exists: {state['evidence'] / name}") from exc
    with os.fdopen(handle, "wb") as stream:
        stream.write(raw)
    _check_reservation(state)


def _clear(handle):
    """Remove owned interior entries through directory handles, never links."""
    device = os.fstat(handle).st_dev
    for name in os.listdir(handle):
        info = os.stat(name, dir_fd=handle, follow_symlinks=False)
        if not stat.S_ISDIR(info.st_mode):
            os.unlink(name, dir_fd=handle)
            continue
        require(info.st_dev == device, "Refusing to traverse a nested filesystem during restoration")
        child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=handle)
        try:
            require(_identity(os.fstat(child)) == _identity(info), "Directory changed before clearing")
            os.fchmod(child, stat.S_IMODE(info.st_mode) | 0o700)
            _clear(child)
            current = os.stat(name, dir_fd=handle, follow_symlinks=False)
            require((current.st_dev, current.st_ino) == (info.st_dev, info.st_ino), "Directory changed during clearing")
        finally:
            os.close(child)
        os.rmdir(name, dir_fd=handle)


def _restore_reservation(state):
    """Restore verified snapshots only after the loading caller verifies teardown."""
    require(state["evidence"].name == "loading", "Ordinary writer output cannot be restored")
    _check_reservation(state)
    staging = state["control"] / ("restore-" + uuid.uuid4().hex)
    staging.mkdir(mode=0o700)
    stage_fd = os.open(staging, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for name, (source, expected) in state["_snapshots"].items():
            copy_snapshot(source, staging / name, expected)
        _check_reservation(state)
        for name in MUTABLE:
            target = state["_fds"][name]
            source = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=stage_fd)
            try:
                _clear(target)
                for child in os.listdir(source):
                    os.rename(child, child, src_dir_fd=source, dst_dir_fd=target)
            finally:
                os.close(source)
        if state["_expected"]["workspace_git"] is not None:
            os.rename("workspace_git", ".git", src_dir_fd=stage_fd, dst_dir_fd=state["_fds"]["workspace"])
        _check_reservation(state)
        current = _mutable_inventories(state)
        require(current == state["_expected"], "Restored mutable inventories differ")
        return {"schema": "coding-trial-native-restoration/v1", "status": "passed",
                "inventory_sha256": {name: None if entries is None else fingerprint(entries) for name, entries in current.items()},
                "anchors_unchanged": True, "immutable_unchanged": None,
                "reason": "Owned mutable interiors restored; immutable input recheck belongs to loading"}
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"loading-unqualified: Restoration failed: {exc}") from exc
    finally:
        try:
            _clear(stage_fd)
            current = staging.lstat()
            require((current.st_dev, current.st_ino) == (os.fstat(stage_fd).st_dev, os.fstat(stage_fd).st_ino),
                    "Restoration staging directory changed")
            os.rmdir(staging.name, dir_fd=state["_fds"]["private"])
        finally:
            os.close(stage_fd)
