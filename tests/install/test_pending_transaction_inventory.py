from __future__ import annotations

import os
from pathlib import Path
import stat

import pytest


def _runner():
    from zagrosi_forge.install.contracts import RunnerProvenance, RunnerState

    return RunnerProvenance(
        state=RunnerState.VERIFIED_INSTALLED_DISTRIBUTION,
        origin="installed-wheel",
        artifact_digest="a" * 64,
        runner_version="0.2.0",
        verification_authority="wheel-sha256",
        policy_digest="b" * 64,
    )


def _private_directory(path: Path) -> None:
    if os.name != "nt":
        path.mkdir(mode=0o700)
        return
    import zagrosi_forge.install.paths as paths

    parent = paths._windows_open_path(os.fspath(path.parent))
    child = 0
    try:
        child = paths._windows_create_private_directory(parent, path.name)
    finally:
        if child:
            paths._windows_close(child)
        paths._windows_close(parent)


def _owned_root(tmp_path: Path):
    from zagrosi_forge.install.paths import PlatformPathAuthority

    home = tmp_path / "codex-home"
    _private_directory(home)
    authority = PlatformPathAuthority()
    owned = authority.bootstrap_forge_root(home, runner=_runner()).unwrap()
    return home, owned


def _tree_snapshot(root: Path) -> tuple[tuple[object, ...], ...]:
    paths = (root, *sorted(root.rglob("*")))
    captured: list[tuple[object, ...]] = []
    for path in paths:
        status = path.lstat()
        captured.append(
            (
                path.relative_to(root).as_posix(),
                stat.S_IFMT(status.st_mode),
                stat.S_IMODE(status.st_mode),
                status.st_dev,
                status.st_ino,
                status.st_nlink,
                status.st_size,
                path.read_bytes() if stat.S_ISREG(status.st_mode) else None,
            )
        )
    return tuple(captured)


def _create_transactions(owned, transaction_ids: tuple[str, ...]):
    from zagrosi_forge.install.ownership import create_persistent_transaction_root

    return tuple(
        create_persistent_transaction_root(
            owned,
            transaction_id=transaction_id,
        ).unwrap()
        for transaction_id in transaction_ids
    )


def test_pending_inventory_absent_store_is_empty_and_effect_free(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.ownership import discover_pending_transactions

    home, owned = _owned_root(tmp_path)
    plugins = home / "plugins"
    transaction_store = plugins / ".zagrosi/transactions"
    before = _tree_snapshot(plugins)
    assert not transaction_store.exists()

    try:
        discovered = discover_pending_transactions(owned).unwrap()
        assert discovered == ()
        assert not transaction_store.exists()
        assert _tree_snapshot(plugins) == before
    finally:
        owned.close()


@pytest.mark.parametrize("store_present", (False, True))
def test_pending_inventory_preserves_bootstrap_stage(
    tmp_path: Path,
    store_present: bool,
) -> None:
    from zagrosi_forge.install.ownership import discover_pending_transactions

    home, owned = _owned_root(tmp_path)
    if store_present:
        _create_transactions(owned, (f"tx-{'e' * 32}",))
    stage = home / f"plugins/.zagrosi/.transactions-{'f' * 32}.tmp"
    _private_directory(stage)
    before = _tree_snapshot(home / "plugins")
    try:
        discovered = discover_pending_transactions(owned)
        assert not discovered.is_ok
        assert discovered.error is not None
        assert discovered.error.code == "ownership.cleanup_incomplete"
        assert stage.is_dir()
        assert _tree_snapshot(home / "plugins") == before
    finally:
        owned.close()


def test_pending_inventory_discovers_distinct_live_roots_sorted_without_path_scans(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from zagrosi_forge.install.ownership import (
        TransactionLocation,
        discover_pending_transactions,
    )

    home, owned = _owned_root(tmp_path)
    transaction_ids = (
        f"tx-{'b' * 32}",
        f"tx-{'a' * 32}",
    )
    _create_transactions(owned, transaction_ids)
    plugins = home / "plugins"
    before = _tree_snapshot(plugins)

    def reject_path_scan(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("pending transaction discovery used a Path scan")

    try:
        with monkeypatch.context() as patch:
            patch.setattr(Path, "glob", reject_path_scan)
            patch.setattr(Path, "rglob", reject_path_scan)
            patch.setattr(Path, "iterdir", reject_path_scan)
            discovered = discover_pending_transactions(owned).unwrap()

        assert tuple(item.binding.transaction_id for item in discovered) == tuple(
            sorted(transaction_ids)
        )
        assert all(item.location is TransactionLocation.LIVE for item in discovered)
        assert all(
            item.journal_relative == item.binding.root_relative for item in discovered
        )
        assert len({item.binding.transaction_identity for item in discovered}) == len(
            discovered
        )
        assert _tree_snapshot(plugins) == before
    finally:
        owned.close()


def test_pending_inventory_limit_plus_one_counts_real_roots_before_loading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from zagrosi_forge.install.contracts import ForgeError
    from zagrosi_forge.install.journal import JournalStore, load_pending
    from zagrosi_forge.install.policies import LIMIT_POLICY

    home, owned = _owned_root(tmp_path)
    limit = LIMIT_POLICY.value("journal_records")
    transaction_ids = tuple(f"tx-{index:032x}" for index in range(limit + 1))
    created = _create_transactions(owned, transaction_ids)
    assert len({item.binding.transaction_identity for item in created}) == limit + 1
    assert len({item.binding.claim_identity for item in created}) == limit + 1
    plugins = home / "plugins"
    before = _tree_snapshot(plugins)

    def reject_journal_load(_store: JournalStore):
        raise AssertionError("journal contents loaded before the root-count gate")

    monkeypatch.setattr(
        JournalStore,
        "_load_with_observations",
        reject_journal_load,
    )
    try:
        with pytest.raises(ForgeError) as raised:
            load_pending(owned)
        assert raised.value.code == "journal.limit_exceeded"
        assert raised.value.exit_category == 14
        assert _tree_snapshot(plugins) == before
    finally:
        owned.close()


def test_pending_inventory_rechecks_names_after_enumeration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import zagrosi_forge.install.ownership as ownership

    home, owned = _owned_root(tmp_path)
    _create_transactions(owned, (f"tx-{'c' * 32}",))
    transaction_store = home / "plugins/.zagrosi/transactions"
    injected = transaction_store / f".root-{'d' * 32}.tmp"
    original = ownership._bounded_transaction_names
    calls = 0

    def inject_after_initial_scan(
        descriptor: int,
        *,
        windows: bool,
    ) -> tuple[str, ...]:
        nonlocal calls
        calls += 1
        names = original(descriptor, windows=windows)
        if calls == 2:
            _private_directory(injected)
        return names

    monkeypatch.setattr(
        ownership,
        "_bounded_transaction_names",
        inject_after_initial_scan,
    )
    try:
        observed = ownership.discover_pending_transactions(owned)
        assert not observed.is_ok
        assert observed.error is not None
        assert observed.error.code == "ownership.cleanup_incomplete"
        assert injected.is_dir()
    finally:
        owned.close()
