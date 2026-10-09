"""Task-local reservation inputs; fixture creation never establishes real admission.

The caller must supply reviewed initial trial bytes. This helper does not invent
trial states, adopt an existing attempt, execute Git, or accept real credentials.
Snapshot paths/modes follow the parent's approved exact clarifications. This
ignored fixture has not been imported or executed.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil

from test_trial_suite_native_runtime import byte_hash, make_native
from trial_suite_fixtures import digest, regular_entries


BASELINE_FILES = ("backend/app/__init__.py", "backend/app/service.py", "specs/test_service.py",
                  "pyproject.toml", "deps.lock", "settings.toml", "AGENTS.md")


def creation_anchor(path):
    path = path.resolve()
    info, parent = path.stat(), path.parent.stat()
    return {"path": str(path), "device": info.st_dev, "inode": info.st_ino, "mode": info.st_mode,
            "parent_device": parent.st_dev, "parent_inode": parent.st_ino}


def make_reserved_native(tmp_path, *, initial_attempt_bytes: bytes, plain=False, subagents=False,
                         scheduled_position=0, with_admission=False):
    """Create one fresh fixture, then bind its original records, roots and snapshots."""
    for name in ("assessor", "attempt"):
        path = tmp_path / name
        assert not path.exists() and not path.is_symlink(), "Reservation fixture requires fresh task-local roots"
    fixture = make_native(tmp_path, plain=plain, subagents=subagents)
    if with_admission:
        from native_admission_fixtures import attach_static_admission
        attach_static_admission(fixture)
    native, roots = fixture["native"], fixture["roots"]
    attempt = native.parent
    control = attempt / "private"
    initial = control / "initial"
    initial.mkdir()
    trial = attempt / "trial.json"
    initial_record = control / "initial-attempt.json"
    for path in (trial, initial_record):
        with path.open("xb") as stream:
            stream.write(initial_attempt_bytes)

    mutable = {"workspace": Path(roots["workspace"]), "home": native / "home",
               "codex": native / "codex", "generated": Path(roots["generated"])}
    assert not (mutable["workspace"] / ".git").exists()
    declared_files = {"workspace": BASELINE_FILES, "home": (), "codex": ("plugins",), "generated": ()}
    snapshots = {"workspace_git": None}
    for role, source in mutable.items():
        destination = initial / role
        shutil.copytree(source, destination, symlinks=True)
        expected = regular_entries(source, declared_files[role])
        # Fail visibly if the shared fixture grows new material; never silently
        # snapshot bytes that the independently declared inventory omits.
        assert {path.relative_to(source).as_posix() for path in source.rglob("*")} == set(expected)
        assert {path.relative_to(destination).as_posix() for path in destination.rglob("*")} == set(expected)
        assert regular_entries(destination, declared_files[role]) == expected
        snapshots[role] = {"path": "initial/" + role, "inventory_sha256": digest(expected),
                           "anchor": creation_anchor(destination)}
    assert snapshots["workspace"]["inventory_sha256"] == fixture["suite"]["tasks"]["normalize"]["source"]["baseline_sha256"]

    loading_configuration = deepcopy(fixture["suite"])
    for arm in loading_configuration["arms"].values():
        arm["loading"]["receipt"] = None
    anchored = {"attempt": attempt, "private": control, "initial": initial, **mutable}
    reservation = {
        "schema": "coding-trial-native-reservation/v1",
        "loading_configuration_sha256": digest(loading_configuration),
        "suite_source_sha256": byte_hash(fixture["suite_root"] / "suite.json"),
        "attempt_record": {"path": "../trial.json", "initial_path": "initial-attempt.json",
                           "sha256": hashlib.sha256(initial_attempt_bytes).hexdigest()},
        "task": "normalize", "arm": fixture["arm"], "scheduled_position": scheduled_position,
        "roots": deepcopy(roots), "runtime_sha256": byte_hash(native / "runtime.json"), "auth": None,
        "anchors": {name: creation_anchor(path) for name, path in anchored.items()}, "snapshots": snapshots,
    }
    reservation_path = control / "reservation.json"
    with reservation_path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(reservation, indent=2) + "\n")
    loading, writer = control / "loading", control / "writer"
    assert not loading.exists() and not writer.exists() and not (control / "execution.lock").exists()
    fixture.update(reservation=reservation, reservation_path=reservation_path, trial_path=trial,
                   initial_record_path=initial_record, initial_attempt_bytes=initial_attempt_bytes,
                   loading_evidence=loading, writer_evidence=writer)
    return fixture
