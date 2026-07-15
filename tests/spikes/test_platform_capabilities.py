from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

import _platform_capabilities as platform_capabilities
from _platform_capabilities import (
    atomic_supported_metadata_replacement,
    exclusive_absent_directory_publication,
    no_follow_component_opening,
    owned_root_quarantine_rename,
    process_death_lock_release,
    process_tree_termination,
    stable_parent_and_leaf_identity,
    unsupported_security_metadata_rejection,
)


_ISOLATED_ENVIRONMENT = (
    "HOME",
    "CODEX_HOME",
    "XDG_CONFIG_HOME",
    "XDG_CACHE_HOME",
    "XDG_DATA_HOME",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
    "TMPDIR",
    "TEMP",
    "TMP",
)


@pytest.fixture
def isolated_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    sentinel = tmp_path / "outside-sentinel.bin"
    sentinel_bytes = b"zagrosi-platform-spike-outside-sentinel\x00\xff"
    sentinel.write_bytes(sentinel_bytes)
    before = sentinel.stat()

    isolated = tmp_path / "isolated"
    for variable in _ISOLATED_ENVIRONMENT:
        value = isolated / variable.lower()
        value.mkdir(parents=True)
        monkeypatch.setenv(variable, os.fspath(value))

    work = isolated / "work"
    work.mkdir()
    yield work

    after = sentinel.stat()
    assert sentinel.read_bytes() == sentinel_bytes
    assert (after.st_dev, after.st_ino, after.st_size) == (
        before.st_dev,
        before.st_ino,
        before.st_size,
    )


def test_platform_opens_each_component_without_following_links(
    isolated_root: Path,
) -> None:
    evidence = no_follow_component_opening(isolated_root / "no-follow")
    assert evidence == {
        "intermediate_reparse_rejected": True,
        "nested_component_opened": True,
        "ordinary_component_opened": True,
        "reparse_component_rejected": True,
    }


def test_platform_reports_stable_parent_and_leaf_identity(isolated_root: Path) -> None:
    evidence = stable_parent_and_leaf_identity(isolated_root / "identity")
    assert evidence == {
        "leaf_handle_stable": True,
        "parent_handle_stable": True,
        "replacement_distinct": True,
    }


def test_platform_exclusively_publishes_absent_directory(isolated_root: Path) -> None:
    evidence = exclusive_absent_directory_publication(isolated_root / "publish")
    assert evidence == {
        "absent_destination_published": True,
        "existing_destination_rejected": True,
        "rejected_source_preserved": True,
    }


def test_platform_quarantine_rename_stays_in_owned_root(isolated_root: Path) -> None:
    evidence = owned_root_quarantine_rename(isolated_root / "quarantine")
    assert evidence == {
        "identity_preserved": True,
        "marker_link_rejected": True,
        "owner_mismatch_rejected": True,
        "pre_rename_identity_swap_rejected": True,
        "private_parent_writer_boundary": True,
        "root_moved_exclusively": True,
        "source_link_rejected": True,
    }


def test_platform_kernel_lock_releases_after_process_death(
    isolated_root: Path,
) -> None:
    evidence = process_death_lock_release(isolated_root / "lock")
    assert evidence == {
        "child_acquired_kernel_lock": True,
        "contender_blocked_while_alive": True,
        "lock_released_after_death": True,
    }


def test_platform_atomically_replaces_supported_config_metadata(
    isolated_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence = atomic_supported_metadata_replacement(isolated_root / "replace")
    expected = {
        "bytes_replaced": True,
        "replacement_data_flushed": True,
        "supported_metadata_preserved": True,
    }
    if os.name == "nt":
        expected["security_authorization_preserved"] = True
    elif os.uname().sysname == "Darwin":
        expected["provenance_preserved"] = True
    assert evidence == expected

    probe = isolated_root / "optional-xattr-probe"
    probe.touch()
    name = b"com.apple.provenance"
    monkeypatch.setattr(platform_capabilities, "_list_xattrs", lambda _path: ())
    monkeypatch.setattr(
        platform_capabilities,
        "_get_xattr",
        lambda _path, _name: pytest.fail("absent xattr must not be read"),
    )
    assert platform_capabilities._optional_xattr(probe, name) is None
    monkeypatch.setattr(platform_capabilities, "_list_xattrs", lambda _path: (name,))
    monkeypatch.setattr(platform_capabilities, "_get_xattr", lambda _path, _name: b"")
    assert platform_capabilities._optional_xattr(probe, name) == b""


def test_platform_rejects_unsupported_acl_xattr_dacl_or_flags(
    isolated_root: Path,
) -> None:
    evidence = unsupported_security_metadata_rejection(isolated_root / "metadata")
    expected = {
        "bytes_preserved": True,
        "effects_rejected": True,
        "security_metadata_detected": True,
    }
    assert evidence == expected


def test_platform_terminates_native_process_tree(isolated_root: Path) -> None:
    evidence = process_tree_termination(isolated_root / "process-tree")
    assert evidence == {
        "descendant_started": True,
        "tree_terminated": True,
        "tree_owned_resource_released": True,
    }
