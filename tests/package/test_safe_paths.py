from __future__ import annotations

import errno
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest


ROOT = Path(__file__).parents[2]
CORPUS = json.loads(
    (ROOT / "tests/fixtures/package/paths/lexical-corpus.json").read_text(
        encoding="utf-8"
    )
)


def _code(result: Any) -> str:
    assert not result.is_ok
    assert result.error is not None
    assert result.error.exit_category == 11
    return result.error.code


def _reference(raw: str):
    from zagrosi_forge.install.paths import validate_reference
    from zagrosi_forge.install.policies import LIMIT_POLICY

    return validate_reference(raw, role="test-reference", limits=LIMIT_POLICY).unwrap()


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


def _identity(path: Path) -> tuple[int, int]:
    if os.name == "nt":
        import zagrosi_forge.install.paths as paths

        parent = paths._windows_open_path(os.fspath(path.parent))
        child = 0
        try:
            child = paths._windows_open_child(parent, path.name, directory=None)
            return paths._windows_handle_status(child).identity
        finally:
            if child:
                paths._windows_close(child)
            paths._windows_close(parent)
    metadata = path.stat(follow_symlinks=False)
    return metadata.st_dev, metadata.st_ino


def _private_test_directory(path: Path) -> Path:
    """Create one exact-private test directory without weakening production."""

    if os.name != "nt":
        path.mkdir(mode=0o700)
        return path

    import zagrosi_forge.install.paths as paths

    parent = paths._windows_open_path(os.fspath(path.parent))
    child = 0
    try:
        child = paths._windows_create_private_directory(parent, path.name)
        assert paths._windows_private_directory(child, exact=True)
    finally:
        if child:
            paths._windows_close(child)
        paths._windows_close(parent)
    return path


def _directory_link(target: Path, link: Path) -> None:
    if os.name != "nt":
        link.symlink_to(target, target_is_directory=True)
        return
    completed = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
        check=False,
        capture_output=True,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(case["value"], case["code"]) for case in CORPUS["invalid_components"]],
)
def test_component_rejects_absolute_traversal_separator_and_controls(
    raw: str, expected: str
) -> None:
    from zagrosi_forge.install.paths import validate_component
    from zagrosi_forge.install.policies import LIMIT_POLICY

    result = validate_component(raw, role="plugin-id", limits=LIMIT_POLICY)
    assert _code(result) == expected
    assert (
        validate_component("zagrosi-forge", role="plugin-id", limits=LIMIT_POLICY)
        .unwrap()
        .value
        == "zagrosi-forge"
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(case["value"], case["code"]) for case in CORPUS["invalid_windows_components"]],
)
def test_component_rejects_windows_drive_unc_device_and_reserved_names(
    raw: str, expected: str
) -> None:
    from zagrosi_forge.install.paths import validate_component
    from zagrosi_forge.install.policies import LIMIT_POLICY

    assert (
        _code(validate_component(raw, role="plugin-id", limits=LIMIT_POLICY))
        == expected
    )


@pytest.mark.parametrize(("first", "second"), CORPUS["reference_collisions"])
def test_reference_rejects_casefold_and_unicode_normalization_collision(
    first: str, second: str
) -> None:
    from zagrosi_forge.install.paths import validate_reference_set
    from zagrosi_forge.install.policies import LIMIT_POLICY

    result = validate_reference_set(
        (first, second), role="package-reference", limits=LIMIT_POLICY
    )
    assert _code(result) == "path.normalization_collision"


def test_containment_rejects_sibling_prefix_and_source_destination_overlap(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.paths import PlatformPathAuthority

    source = tmp_path / "candidate"
    _private_test_directory(source)
    (source / "payload.txt").write_bytes(b"candidate")
    overlapping_home = source
    sibling_home = tmp_path / "candidate-sibling"
    _private_test_directory(sibling_home)
    authority = PlatformPathAuthority()

    with authority.open_source_root(source) as source_root:
        with source_root.open_snapshot((_reference("payload.txt"),)) as snapshot:
            overlapping = authority.bootstrap_forge_root(
                overlapping_home, runner=_runner()
            ).unwrap()
            with overlapping:
                (overlapping_home / "plugins/stages").mkdir()
                proof = authority.prove_descendant(
                    overlapping,
                    _reference("stages/new"),
                    expected_depth=2,
                    allow_absent_leaf=True,
                ).unwrap()
                assert (
                    _code(authority.assert_disjoint(snapshot, proof)) == "path.overlap"
                )

            sibling = authority.bootstrap_forge_root(
                sibling_home, runner=_runner()
            ).unwrap()
            with sibling:
                (sibling_home / "plugins/stages").mkdir()
                proof = authority.prove_descendant(
                    sibling,
                    _reference("stages/new"),
                    expected_depth=2,
                    allow_absent_leaf=True,
                ).unwrap()
                assert authority.assert_disjoint(snapshot, proof).unwrap() is None


def test_open_snapshot_rejects_linked_or_reparse_ancestor_and_leaf(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.paths import PlatformPathAuthority

    source = tmp_path / "source"
    outside = tmp_path / "outside"
    source.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_bytes(b"external")
    _directory_link(outside, source / "ancestor")
    if os.name == "nt":
        _directory_link(outside, source / "leaf.txt")
    else:
        (source / "leaf.txt").symlink_to(outside / "secret.txt")
    (source / "directory").mkdir()
    authority = PlatformPathAuthority()

    with authority.open_source_root(source) as root:
        with pytest.raises(Exception) as ancestor:
            root.open_snapshot((_reference("ancestor/secret.txt"),))
        assert getattr(ancestor.value, "code", None) in {
            "path.linked_ancestor",
            "path.reparse_point",
        }
        with pytest.raises(Exception) as leaf:
            root.open_snapshot((_reference("leaf.txt"),))
        assert getattr(leaf.value, "code", None) == (
            "path.reparse_point" if os.name == "nt" else "path.linked_leaf"
        )
        with pytest.raises(Exception) as directory:
            root.open_snapshot((_reference("directory"),))
        assert getattr(directory.value, "code", None) == "path.outside_root"


@pytest.mark.parametrize("relative", ("missing.txt", "missing/leaf.txt"))
def test_open_snapshot_reports_missing_reference(tmp_path: Path, relative: str) -> None:
    from zagrosi_forge.install.paths import PlatformPathAuthority

    source = tmp_path / "source"
    source.mkdir()

    with PlatformPathAuthority().open_source_root(source) as root:
        with pytest.raises(Exception) as missing:
            root.open_snapshot((_reference(relative),))
        assert getattr(missing.value, "code", None) == "path.missing"


def test_open_snapshot_rejects_hardlink_before_read(tmp_path: Path) -> None:
    from zagrosi_forge.install.paths import PlatformPathAuthority

    source = tmp_path / "source"
    outside = tmp_path / "outside"
    source.mkdir()
    outside.mkdir()
    canary = outside / "canary.bin"
    canary.write_bytes(b"external-canary")
    before = (_identity(canary), canary.read_bytes())
    os.link(canary, source / "payload.bin")

    with PlatformPathAuthority().open_source_root(source) as root:
        with pytest.raises(Exception) as caught:
            root.open_snapshot((_reference("payload.bin"),))
    assert getattr(caught.value, "code", None) == "path.hardlink"
    assert (_identity(canary), canary.read_bytes()) == before


def test_open_snapshot_never_reads_external_canary_during_rename_race(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.paths import PlatformPathAuthority

    source = tmp_path / "source"
    parent = source / "parent"
    outside = tmp_path / "outside"
    parent.mkdir(parents=True)
    outside.mkdir()
    (parent / "payload.bin").write_bytes(b"approved")
    canary = outside / "payload.bin"
    canary.write_bytes(b"external-secret")
    canary_before = (_identity(canary), canary.read_bytes())

    with PlatformPathAuthority().open_source_root(source) as root:
        with root.open_snapshot((_reference("parent/payload.bin"),)) as snapshot:
            if os.name == "nt":
                payload = parent / "payload.bin"
                payload.rename(parent / "displaced.bin")
                os.link(canary, payload)
                with pytest.raises(Exception) as changed:
                    snapshot.read_bytes(_reference("parent/payload.bin"), limit=64)
                assert getattr(changed.value, "code", None) == "path.identity_changed"
            else:
                parent.rename(source / "displaced")
                _directory_link(outside, source / "parent")
                assert (
                    snapshot.read_bytes(_reference("parent/payload.bin"), limit=64)
                    == b"approved"
                )
    assert (_identity(canary), canary.read_bytes()) == canary_before


def test_snapshot_reads_opened_identity_and_never_reopens_candidate_path(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.paths import PlatformPathAuthority

    source = tmp_path / "source"
    source.mkdir()
    leaf = source / "payload.bin"
    leaf.write_bytes(b"opened-identity")
    reference = _reference("payload.bin")

    with PlatformPathAuthority().open_source_root(source) as root:
        with root.open_snapshot((reference,)) as snapshot:
            opened_identity = snapshot.file(reference).identity
            leaf.rename(source / "old.bin")
            leaf.write_bytes(b"replacement")
            with pytest.raises(Exception) as changed:
                snapshot.read_bytes(reference, limit=64)
            assert getattr(changed.value, "code", None) == "path.identity_changed"
            assert snapshot.file(reference).identity == opened_identity


def test_open_snapshot_duplicates_preopened_identity_without_reopening(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from zagrosi_forge.install.paths import PlatformPathAuthority

    source = tmp_path / "source"
    source.mkdir()
    first_path = source / "first.bin"
    first_path.write_bytes(b"validated-first")
    (source / "second.bin").write_bytes(b"second")
    first = _reference("first.bin")
    second = _reference("second.bin")

    with PlatformPathAuthority().open_source_root(source) as root:
        opened = root.open_regular_file(first)
        root_type = type(root)
        original_open = root_type.open_regular_file

        def guarded_open(self: Any, reference: Any):
            assert reference != first, "an adopted candidate was reopened by name"
            return original_open(self, reference)

        monkeypatch.setattr(root_type, "open_regular_file", guarded_open)
        with root.open_snapshot((second, first), already_opened=(opened,)) as snapshot:
            opened.close()
            assert snapshot.references == (first, second)
            assert snapshot.read_bytes(first, limit=64) == b"validated-first"
            assert snapshot.read_bytes(second, limit=64) == b"second"


def test_opened_file_rejects_same_size_rewrite_with_restored_mtime(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.paths import PlatformPathAuthority

    source = tmp_path / "source"
    source.mkdir()
    candidate = source / "payload.bin"
    candidate.write_bytes(b"approved")
    timestamps = candidate.stat()
    reference = _reference("payload.bin")

    with PlatformPathAuthority().open_source_root(source) as root:
        with root.open_regular_file(reference) as opened:
            candidate.write_bytes(b"attacker")
            os.utime(
                candidate,
                ns=(timestamps.st_atime_ns, timestamps.st_mtime_ns),
            )
            with pytest.raises(Exception) as changed:
                opened.read_bytes(limit=64)
            assert getattr(changed.value, "code", None) == "path.identity_changed"


def test_clone_preserves_original_fingerprint_across_duplication_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from zagrosi_forge.install.paths import PlatformPathAuthority

    source = tmp_path / "source"
    source.mkdir()
    candidate = source / "payload.bin"
    candidate.write_bytes(b"approved")
    timestamps = candidate.stat()
    reference = _reference("payload.bin")

    with PlatformPathAuthority().open_source_root(source) as root:
        with root.open_regular_file(reference) as opened:
            opened_type = type(opened)
            original_duplicate = opened_type._duplicate_descriptor

            def raced_duplicate(self: Any) -> int:
                descriptor = original_duplicate(self)
                candidate.write_bytes(b"attacker")
                os.utime(
                    candidate,
                    ns=(timestamps.st_atime_ns, timestamps.st_mtime_ns),
                )
                return descriptor

            monkeypatch.setattr(opened_type, "_duplicate_descriptor", raced_duplicate)
            with pytest.raises(Exception) as changed:
                root.open_snapshot((reference,), already_opened=(opened,))
            assert getattr(changed.value, "code", None) == "path.identity_changed"


def test_nonexistent_descendant_has_expected_depth_and_owned_ancestor(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.paths import PlatformPathAuthority

    codex_home = tmp_path / "codex-home"
    _private_test_directory(codex_home)
    authority = PlatformPathAuthority()
    owned = authority.bootstrap_forge_root(codex_home, runner=_runner()).unwrap()
    with owned:
        (codex_home / "plugins/.zagrosi/ownership/zagrosi").mkdir(parents=True)
        relative = _reference(".zagrosi/ownership/zagrosi/receipt.json")
        proof = authority.prove_descendant(
            owned, relative, expected_depth=4, allow_absent_leaf=True
        ).unwrap()
        assert proof.expected_depth == 4
        assert proof.existing_depth == 3
        assert not proof.leaf_exists
        assert proof.owned_ancestor_identity == owned.identity
        assert (
            _code(
                authority.prove_descendant(
                    owned, relative, expected_depth=3, allow_absent_leaf=True
                )
            )
            == "path.depth"
        )
        assert (
            _code(
                authority.prove_descendant(
                    owned,
                    _reference("missing/parent/receipt.json"),
                    expected_depth=3,
                    allow_absent_leaf=True,
                )
            )
            == "path.outside_root"
        )


def test_first_root_bootstrap_is_exclusive_restrictive_and_link_safe(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.paths import PlatformPathAuthority

    codex_home = tmp_path / "codex-home"
    external = tmp_path / "external"
    _private_test_directory(codex_home)
    external.mkdir()
    sentinel = external / "sentinel"
    sentinel.write_bytes(b"preserve")
    authority = PlatformPathAuthority()

    owned = authority.bootstrap_forge_root(codex_home, runner=_runner()).unwrap()
    with owned:
        assert owned.created
        assert owned.identity == _identity(codex_home / "plugins")
        assert owned.control_identity == _identity(codex_home / "plugins/.zagrosi")
        assert owned._validate_live_descriptor()
        if os.name == "posix":
            assert (
                stat.S_IMODE((codex_home / "plugins/.zagrosi").stat().st_mode) == 0o700
            )

    linked_home = tmp_path / "linked-home"
    _private_test_directory(linked_home)
    _private_test_directory(linked_home / "plugins")
    _directory_link(external, linked_home / "plugins/.zagrosi")
    assert _code(authority.bootstrap_forge_root(linked_home, runner=_runner())) in {
        "path.linked_leaf",
        "path.reparse_point",
    }
    assert sentinel.read_bytes() == b"preserve"


def test_authenticated_existing_root_reopens_without_granting_deletion(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.paths import PlatformPathAuthority

    codex_home = tmp_path / "codex-home"
    _private_test_directory(codex_home)
    authority = PlatformPathAuthority()
    first = authority.bootstrap_forge_root(codex_home, runner=_runner()).unwrap()
    plugins_identity = first.identity
    control_identity = first.control_identity
    first.close()

    reopened = authority.bootstrap_forge_root(codex_home, runner=_runner()).unwrap()
    with reopened:
        assert not reopened.created
        assert reopened.identity == plugins_identity
        assert reopened.control_identity == control_identity
        assert reopened._validate_live_descriptor()
        assert reopened._validate_control_binding()


def test_concurrent_bootstrap_has_one_publisher_and_one_authenticated_reopen(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.paths import PlatformPathAuthority

    codex_home = tmp_path / "codex-home"
    _private_test_directory(codex_home)

    def bootstrap() -> Any:
        return (
            PlatformPathAuthority()
            .bootstrap_forge_root(codex_home, runner=_runner())
            .unwrap()
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        roots = tuple(pool.map(lambda _index: bootstrap(), range(2)))
    try:
        assert sum(root.created for root in roots) == 1
        assert len({root.identity for root in roots}) == 1
        assert len({root.control_identity for root in roots}) == 1
        assert all(root._validate_control_binding() for root in roots)
    finally:
        for root in roots:
            root.close()


def test_control_claim_tamper_permission_and_namespace_rebind_fail_closed(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.paths import PlatformPathAuthority

    authority = PlatformPathAuthority()

    tampered_home = tmp_path / "tampered-home"
    _private_test_directory(tampered_home)
    authority.bootstrap_forge_root(tampered_home, runner=_runner()).unwrap().close()
    claim = tampered_home / "plugins/.zagrosi/control-v1.json"
    claim.write_bytes(claim.read_bytes().replace(b'"record_kind"', b'"record_k1nd"'))
    assert (
        _code(authority.bootstrap_forge_root(tampered_home, runner=_runner()))
        == "path.root_unowned"
    )

    if os.name == "posix":
        permissive_home = tmp_path / "permissive-home"
        _private_test_directory(permissive_home)
        authority.bootstrap_forge_root(
            permissive_home, runner=_runner()
        ).unwrap().close()
        (permissive_home / "plugins/.zagrosi").chmod(0o755)
        assert (
            _code(authority.bootstrap_forge_root(permissive_home, runner=_runner()))
            == "path.root_unowned"
        )

    rebound_home = tmp_path / "rebound-home"
    _private_test_directory(rebound_home)
    authority.bootstrap_forge_root(rebound_home, runner=_runner()).unwrap().close()
    authentic = rebound_home / "plugins/.zagrosi"
    displaced = rebound_home / "plugins/displaced"
    authentic.rename(displaced)
    authentic.mkdir(mode=0o700)
    (authentic / "control-v1.json").write_bytes(
        (displaced / "control-v1.json").read_bytes()
    )
    if os.name == "posix":
        (authentic / "control-v1.json").chmod(0o600)
    assert (
        _code(authority.bootstrap_forge_root(rebound_home, runner=_runner()))
        == "path.root_unowned"
    )
    assert displaced.is_dir()
    assert authentic.is_dir()


def test_control_claim_schema_and_minimum_reader_are_enforced(tmp_path: Path) -> None:
    from zagrosi_forge.install.contracts import canonical_json_bytes
    from zagrosi_forge.install.paths import PlatformPathAuthority

    for field, value in (
        ("schema_version", "2.0"),
        ("minimum_reader_version", "999.0.0"),
    ):
        codex_home = tmp_path / field
        _private_test_directory(codex_home)
        authority = PlatformPathAuthority()
        authority.bootstrap_forge_root(codex_home, runner=_runner()).unwrap().close()
        claim = codex_home / "plugins/.zagrosi/control-v1.json"
        record = json.loads(claim.read_bytes())
        record[field] = value
        del record["record_digest"]
        record["record_digest"] = hashlib.sha256(
            canonical_json_bytes(record)
        ).hexdigest()
        claim.write_bytes(canonical_json_bytes(record, final_newline=True))
        if os.name == "posix":
            claim.chmod(0o600)
        assert (
            _code(authority.bootstrap_forge_root(codex_home, runner=_runner()))
            == "path.root_unowned"
        )


def test_live_control_namespace_rebind_invalidates_root_and_path_proof(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.paths import PlatformPathAuthority

    codex_home = tmp_path / "codex-home"
    _private_test_directory(codex_home)
    authority = PlatformPathAuthority()
    owned = authority.bootstrap_forge_root(codex_home, runner=_runner()).unwrap()
    with owned:
        (codex_home / "plugins/stages").mkdir()
        proof = authority.prove_descendant(
            owned,
            _reference("stages/new"),
            expected_depth=2,
            allow_absent_leaf=True,
        ).unwrap()
        with proof:
            control = codex_home / "plugins/.zagrosi"
            control.rename(codex_home / "plugins/displaced-control")
            control.mkdir(mode=0o700)
            assert not owned._validate_control_binding()
            with pytest.raises(Exception) as changed:
                proof._require_open()
            assert getattr(changed.value, "code", None) == "path.identity_changed"


def test_live_plugins_namespace_rebind_invalidates_root_and_path_proof(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.paths import PlatformPathAuthority

    codex_home = tmp_path / "codex-home"
    _private_test_directory(codex_home)
    authority = PlatformPathAuthority()
    owned = authority.bootstrap_forge_root(codex_home, runner=_runner()).unwrap()
    with owned:
        (codex_home / "plugins/stages").mkdir()
        proof = authority.prove_descendant(
            owned,
            _reference("stages/new"),
            expected_depth=2,
            allow_absent_leaf=True,
        ).unwrap()
        with proof:
            plugins = codex_home / "plugins"
            displaced = codex_home / "displaced-plugins"
            try:
                plugins.rename(displaced)
            except PermissionError:
                if os.name != "nt":
                    raise
                assert owned._validate_live_descriptor()
                assert owned._validate_control_binding()
                proof._require_open()
                assert plugins.is_dir()
                assert not displaced.exists()
                return
            plugins.mkdir(mode=0o700)

            assert not owned._validate_live_descriptor()
            assert not owned._validate_control_binding()
            with pytest.raises(Exception) as changed:
                proof._require_open()
            assert getattr(changed.value, "code", None) == "path.identity_changed"
            assert displaced.is_dir()
            assert plugins.is_dir()


def test_live_descendant_rebind_invalidates_path_proof(tmp_path: Path) -> None:
    from zagrosi_forge.install.paths import PlatformPathAuthority

    codex_home = tmp_path / "codex-home"
    _private_test_directory(codex_home)
    authority = PlatformPathAuthority()
    owned = authority.bootstrap_forge_root(codex_home, runner=_runner()).unwrap()
    with owned:
        stages = codex_home / "plugins/stages"
        channel = stages / "channel"
        channel.mkdir(parents=True)
        proof = authority.prove_descendant(
            owned,
            _reference("stages/channel/new"),
            expected_depth=3,
            allow_absent_leaf=True,
        ).unwrap()
        with proof:
            displaced = stages / "displaced-channel"
            channel.rename(displaced)
            channel.mkdir(mode=0o700)

            with pytest.raises(Exception) as required:
                proof._require_open()
            assert getattr(required.value, "code", None) == "path.identity_changed"
            with pytest.raises(Exception) as duplicated:
                proof._duplicate_descriptor()
            assert getattr(duplicated.value, "code", None) == "path.identity_changed"
            assert displaced.is_dir()
            assert channel.is_dir()


def test_partial_bootstrap_rollback_never_deletes_a_name_swapped_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import zagrosi_forge.install.paths as paths

    if os.name == "nt":
        parent = paths._windows_open_path(str(tmp_path))
        held = paths._windows_create_private_directory(parent, "created")
        attacker = tmp_path / "attacker"
        attacker.mkdir()
        displaced = tmp_path / "displaced"
        try:
            (tmp_path / "created").rename(displaced)
            attacker.rename(tmp_path / "created")
            attacker_identity = _identity(tmp_path / "created")
            paths._windows_rollback_created_directory(held)
        finally:
            paths._windows_close(held)
            paths._windows_close(parent)
        assert _identity(tmp_path / "created") == attacker_identity
        assert not displaced.exists()
        return

    codex_home = tmp_path / "codex-home"
    _private_test_directory(codex_home)
    attacker = codex_home / "attacker"
    attacker.mkdir()
    attacker_identity = _identity(attacker)
    calls = 0

    def guard(_descriptor: int) -> bool:
        nonlocal calls
        calls += 1
        return calls < 3

    original_stat = paths.os.stat
    swapped = False

    def racing_stat(
        path: Any,
        *,
        dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ):
        nonlocal swapped
        status = original_stat(path, dir_fd=dir_fd, follow_symlinks=follow_symlinks)
        if path == ".zagrosi" and dir_fd is not None and not swapped:
            swapped = True
            created = codex_home / "plugins/.zagrosi"
            created.rename(codex_home / "plugins/displaced")
            attacker.rename(created)
        return status

    monkeypatch.setattr(paths.os, "stat", racing_stat)
    monkeypatch.setattr(paths, "_default_filesystem_guard", guard)
    result = paths.PlatformPathAuthority().bootstrap_forge_root(
        codex_home, runner=_runner()
    )
    assert _code(result) == "path.unsupported_filesystem"
    assert _identity(attacker) == attacker_identity


def test_relative_windows_root_has_complete_drive_ancestry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.name != "nt":
        return

    from zagrosi_forge.install.paths import PlatformPathAuthority

    source = tmp_path / "source"
    source.mkdir()
    monkeypatch.chdir(tmp_path)
    authority = PlatformPathAuthority()
    with authority.open_source_root(Path("source")) as relative_root:
        with authority.open_source_root(source) as absolute_root:
            assert relative_root.absolute_ancestry == absolute_root.absolute_ancestry
            assert relative_root.identity == absolute_root.identity
            assert len(relative_root.absolute_ancestry) > 2


def test_windows_acl_semantics_reject_unknown_ace_types(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import zagrosi_forge.install.paths as paths

    sid = "S-1-5-21-100-200-300-1001"
    valid = f"O:{sid}G:SYD:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;FA;;;{sid})"
    parsed = paths._parse_windows_authorization_sddl(valid)
    inherited = paths._parse_windows_authorization_sddl(
        f"O:{sid}G:SYD:P(A;ID;FA;;;SY)(A;ID;FA;;;BA)(A;ID;FA;;;{sid})"
    )
    assert parsed == inherited
    for ace_type in ("D", "OA", "XA", "AU", "ML"):
        hostile = valid.replace("(A;;FA;;;SY)", f"({ace_type};;FA;;;SY)")
        with pytest.raises(ValueError):
            paths._parse_windows_authorization_sddl(hostile)

    canonical = {
        "LA": sid,
        sid: sid,
        "SY": "S-1-5-18",
        "BA": "S-1-5-32-544",
        "OW": "S-1-3-4",
    }
    rendered = "O:LAG:SYD:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;FA;;;LA)"
    monkeypatch.setattr(paths, "_windows_security_sddl", lambda _handle: rendered)
    monkeypatch.setattr(paths, "_windows_current_user_sid", lambda: sid)
    monkeypatch.setattr(
        paths,
        "_windows_canonical_sddl_sid",
        lambda value: canonical[value],
    )
    assert paths._windows_private_authorization(1, exact=True)

    rendered = "O:LAG:SYD:AI(A;IO;FA;;;LA)"
    assert not paths._windows_private_authorization(1, exact=False)
    rendered = "O:LAG:SYD:PAI(A;;FA;;;LA)"
    assert not paths._windows_private_authorization(1, exact=True)


def test_preexisting_unowned_zagrosi_root_is_preserved(tmp_path: Path) -> None:
    from zagrosi_forge.install.paths import PlatformPathAuthority

    codex_home = tmp_path / "codex-home"
    _private_test_directory(codex_home)
    _private_test_directory(codex_home / "plugins")
    unowned = codex_home / "plugins/.zagrosi"
    unowned.mkdir(mode=0o700)
    sentinel = unowned / "sentinel"
    sentinel.write_bytes(b"unmanaged")
    before = (_identity(unowned), _identity(sentinel), sentinel.read_bytes())

    result = PlatformPathAuthority().bootstrap_forge_root(codex_home, runner=_runner())
    assert _code(result) == "path.root_unowned"
    assert (_identity(unowned), _identity(sentinel), sentinel.read_bytes()) == before


def test_unsupported_or_network_filesystem_stops_without_fallback(
    tmp_path: Path,
) -> None:
    import zagrosi_forge.install.paths as paths

    source = tmp_path / "source"
    source.mkdir()
    canary = source / "canary"
    canary.write_bytes(b"unchanged")
    before = (_identity(canary), canary.read_bytes())
    authority = paths.PlatformPathAuthority._non_authoritative_for_testing()

    with pytest.raises(Exception) as caught:
        authority.open_source_root(source)
    assert getattr(caught.value, "code", None) == "path.unsupported_filesystem"
    assert (_identity(canary), canary.read_bytes()) == before


def test_path_capabilities_are_bound_to_the_minting_authority(tmp_path: Path) -> None:
    from zagrosi_forge.install.paths import PlatformPathAuthority

    codex_home = tmp_path / "codex-home"
    _private_test_directory(codex_home)
    first = PlatformPathAuthority()
    second = PlatformPathAuthority()
    owned = first.bootstrap_forge_root(codex_home, runner=_runner()).unwrap()
    with owned:
        (codex_home / "plugins/stages").mkdir()
        foreign = second.prove_descendant(
            owned,
            _reference("stages/new"),
            expected_depth=2,
            allow_absent_leaf=True,
        )
        assert _code(foreign) == "path.outside_root"
        with first.prove_descendant(
            owned,
            _reference("stages/new"),
            expected_depth=2,
            allow_absent_leaf=True,
        ).unwrap() as proof:
            assert proof.owned_ancestor_identity == owned.identity


def test_path_proof_consumption_revalidates_live_control_claim(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.paths import PlatformPathAuthority

    codex_home = tmp_path / "codex-home"
    _private_test_directory(codex_home)
    authority = PlatformPathAuthority()
    with authority.bootstrap_forge_root(codex_home, runner=_runner()).unwrap() as owned:
        (codex_home / "plugins/stages").mkdir()
        with authority.prove_descendant(
            owned,
            _reference("stages/new"),
            expected_depth=2,
            allow_absent_leaf=True,
        ).unwrap() as proof:
            claim = codex_home / "plugins/.zagrosi/control-v1.json"
            claim.write_bytes(
                claim.read_bytes().replace(b'"record_kind"', b'"record_k1nd"')
            )
            with pytest.raises(Exception) as changed:
                proof._duplicate_descriptor()
            assert getattr(changed.value, "code", None) == "path.identity_changed"


def test_mutated_safe_path_projection_never_reaches_native_access(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.paths import PlatformPathAuthority

    source = tmp_path / "source"
    outside = tmp_path / "outside"
    source.mkdir()
    outside.mkdir()
    canary = outside / "canary"
    canary.write_bytes(b"preserve")
    before = (_identity(canary), canary.read_bytes())
    reference = _reference("payload.bin")
    object.__setattr__(reference, "components", ("..", "outside", "canary"))

    with PlatformPathAuthority().open_source_root(source) as root:
        with pytest.raises(TypeError):
            root.open_snapshot((reference,))
    assert (_identity(canary), canary.read_bytes()) == before


def test_candidate_path_mutated_into_internal_receipt_never_gains_authority(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.paths import PlatformPathAuthority

    source = tmp_path / "source"
    receipt = source / ".zagrosi/ownership/zagrosi/zagrosi-forge" / f"{'a' * 64}.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_bytes(b"internal-canary")
    before = (_identity(receipt), receipt.read_bytes())
    value = receipt.relative_to(source).as_posix()
    reference = _reference("payload.bin")
    object.__setattr__(reference, "value", value)
    object.__setattr__(reference, "components", tuple(value.split("/")))
    object.__setattr__(reference, "collision_key", value.casefold())

    with PlatformPathAuthority().open_source_root(source) as root:
        with pytest.raises(TypeError):
            root.open_snapshot((reference,))
    assert len(receipt.name.encode("utf-8")) == 69
    assert (_identity(receipt), receipt.read_bytes()) == before


def test_reconstructed_safe_path_cannot_cross_native_boundary(tmp_path: Path) -> None:
    from zagrosi_forge.install.paths import PlatformPathAuthority, SafeRelativePath

    source = tmp_path / "source"
    source.mkdir()
    (source / "payload.bin").write_bytes(b"candidate")
    reconstructed = object.__new__(SafeRelativePath)
    object.__setattr__(reconstructed, "value", "payload.bin")
    object.__setattr__(reconstructed, "components", ("payload.bin",))
    object.__setattr__(reconstructed, "collision_key", "payload.bin")

    with PlatformPathAuthority().open_source_root(source) as root:
        with pytest.raises(TypeError):
            root.open_snapshot((reconstructed,))


def test_private_directory_authorization_uses_descriptor_security_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.name != "posix":
        return

    from types import SimpleNamespace

    import zagrosi_forge.install.paths as paths

    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    descriptor = os.open(directory, paths._posix_directory_flags())
    status = os.fstat(descriptor)
    names: tuple[bytes, ...] = ()

    def descriptor_names(selected: int) -> tuple[bytes, ...]:
        assert selected == descriptor
        return names

    monkeypatch.setattr(paths, "_descriptor_xattr_names", descriptor_names)
    monkeypatch.setattr(paths, "_macos_descriptor_has_acl", lambda _descriptor: False)
    try:
        names = (
            (b"com.apple.provenance",)
            if paths.sys.platform == "darwin"
            else (b"user.zagrosi.spike",)
        )
        assert paths._private_directory(descriptor, status, exact=True)

        names = (
            (b"com.apple.quarantine",)
            if paths.sys.platform == "darwin"
            else (b"system.posix_acl_access",)
        )
        assert not paths._private_directory(descriptor, status, exact=True)
        names = (
            (b"com.zagrosi.unhandled",)
            if paths.sys.platform == "darwin"
            else (b"user.zagrosi.unhandled",)
        )
        assert not paths._private_directory(descriptor, status, exact=True)

        names = ()
        monkeypatch.setattr(paths.sys, "platform", "darwin")
        monkeypatch.setattr(
            paths, "_macos_descriptor_has_acl", lambda _descriptor: True
        )
        assert not paths._private_directory(descriptor, status, exact=True)
        monkeypatch.setattr(
            paths, "_macos_descriptor_has_acl", lambda _descriptor: False
        )
        flagged = SimpleNamespace(
            st_mode=status.st_mode,
            st_uid=status.st_uid,
            st_flags=1,
        )
        assert not paths._private_directory(descriptor, flagged, exact=True)
    finally:
        os.close(descriptor)


@pytest.mark.parametrize("target_kind", ("directory", "control-file"))
def test_authenticated_root_rejects_unknown_descriptor_xattrs(
    tmp_path: Path, target_kind: str
) -> None:
    if os.name != "posix":
        return

    from zagrosi_forge.install.paths import PlatformPathAuthority

    codex_home = tmp_path / target_kind
    _private_test_directory(codex_home)
    authority = PlatformPathAuthority()
    authority.bootstrap_forge_root(codex_home, runner=_runner()).unwrap().close()
    control = codex_home / "plugins/.zagrosi"
    target = control if target_kind == "directory" else control / "control-v1.json"
    macos = sys.platform == "darwin"
    name = "com.zagrosi.unhandled" if macos else "user.zagrosi.unhandled"
    unsupported = {errno.ENOTSUP, errno.EPERM}
    if hasattr(errno, "EOPNOTSUPP"):
        unsupported.add(errno.EOPNOTSUPP)
    setter = getattr(os, "setxattr", None)
    wrote_with_api = False
    if callable(setter):
        try:
            setter(target, name, b"hostile")
            wrote_with_api = True
        except OSError as exc:
            if exc.errno in unsupported:
                pytest.skip("test filesystem does not support writable xattrs")
            raise
        except (NotImplementedError, TypeError):
            pass
    if not wrote_with_api:
        xattr = Path("/usr/bin/xattr")
        if not macos or not xattr.is_file():
            pytest.skip("no supported xattr writer is available")
        completed = subprocess.run(
            [str(xattr), "-w", name, "hostile", str(target)],
            check=False,
            capture_output=True,
            timeout=10,
        )
        if completed.returncode != 0:
            pytest.skip("test filesystem does not support writable xattrs")

    assert (
        _code(authority.bootstrap_forge_root(codex_home, runner=_runner()))
        == "path.root_unowned"
    )
    getter = getattr(os, "getxattr", None)
    preserved: bytes | None = None
    if callable(getter):
        try:
            preserved = getter(target, name)
        except (NotImplementedError, TypeError):
            pass
    if preserved is None:
        xattr = Path("/usr/bin/xattr")
        if not macos or not xattr.is_file():
            pytest.fail("the written xattr cannot be read back")
        preserved = subprocess.run(
            [str(xattr), "-p", name, str(target)],
            check=True,
            capture_output=True,
            timeout=10,
        ).stdout.rstrip(b"\n")
    assert preserved == b"hostile"


@pytest.mark.parametrize("metadata_kind", ("extended-acl", "file-flags"))
def test_authenticated_root_rejects_macos_acl_and_flags(
    tmp_path: Path, metadata_kind: str
) -> None:
    if os.name != "posix" or os.uname().sysname != "Darwin":
        return

    from zagrosi_forge.install.paths import PlatformPathAuthority

    codex_home = tmp_path / metadata_kind
    _private_test_directory(codex_home)
    authority = PlatformPathAuthority()
    authority.bootstrap_forge_root(codex_home, runner=_runner()).unwrap().close()
    control = codex_home / "plugins/.zagrosi"
    apply = (
        ["/bin/chmod", "+a", "everyone deny execute", str(control)]
        if metadata_kind == "extended-acl"
        else ["/usr/bin/chflags", "hidden", str(control)]
    )
    clear = (
        ["/bin/chmod", "-N", str(control)]
        if metadata_kind == "extended-acl"
        else ["/usr/bin/chflags", "nohidden", str(control)]
    )
    subprocess.run(apply, check=True, capture_output=True, timeout=10)
    try:
        assert (
            _code(authority.bootstrap_forge_root(codex_home, runner=_runner()))
            == "path.root_unowned"
        )
        assert control.is_dir()
    finally:
        subprocess.run(clear, check=True, capture_output=True, timeout=10)


def test_filesystem_policy_has_no_public_bypass_and_excludes_refs() -> None:
    from zagrosi_forge.install.paths import (
        PlatformPathAuthority,
        _windows_supported_filesystem,
    )

    with pytest.raises(TypeError):
        PlatformPathAuthority(filesystem_guard=lambda _handle: True)  # type: ignore[call-arg]
    assert _windows_supported_filesystem("NTFS", drive_type=3)
    assert not _windows_supported_filesystem("ReFS", drive_type=3)
    assert not _windows_supported_filesystem("NTFS", drive_type=4)
