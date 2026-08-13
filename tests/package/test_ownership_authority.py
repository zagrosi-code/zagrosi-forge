from __future__ import annotations

import ast
import copy
from dataclasses import replace
import errno
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
from types import SimpleNamespace
from typing import Any

import pytest


def _directory_link(path: Path, target: Path) -> None:
    if os.name == "nt":
        subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(path), str(target)],
            check=True,
            capture_output=True,
            text=True,
        )
        return
    path.symlink_to(target, target_is_directory=True)


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


def _install_identity(*, rendered_digest: str = "d" * 64):
    from zagrosi_forge.install.contracts import InstallIdentity
    from zagrosi_forge.install.version import derive_install_version

    return InstallIdentity(
        marketplace_id="zagrosi",
        plugin_id="zagrosi-forge",
        base_version="0.2.0",
        install_version=derive_install_version("0.2.0", "c" * 64),
        base_payload_digest="c" * 64,
        rendered_payload_digest=rendered_digest,
        policy_digest="e" * 64,
        transformation_profile="plugin-v1",
        contract_versions=("finding-v1", "identity-v1"),
    )


def _reference(raw: str):
    from zagrosi_forge.install.paths import validate_reference
    from zagrosi_forge.install.policies import LIMIT_POLICY

    return validate_reference(raw, role="ownership-test", limits=LIMIT_POLICY).unwrap()


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


def _private_test_directory_tree(root: Path, relative: str) -> Path:
    """Create missing fixture ancestors with the production-private Windows ACL."""

    current = root
    for component in Path(relative).parts:
        current /= component
        if not current.exists():
            _private_test_directory(current)
    return current


def _owned(tmp_path: Path):
    from zagrosi_forge.install.paths import PlatformPathAuthority

    home = tmp_path / "codex-home"
    _private_test_directory(home)
    authority = PlatformPathAuthority()
    owned = authority.bootstrap_forge_root(home, runner=_runner()).unwrap()
    return authority, owned, home / "plugins"


def _identity(path: Path) -> tuple[int, int]:
    if os.name != "nt":
        status = path.stat(follow_symlinks=False)
        return status.st_dev, status.st_ino

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


def _code(result: Any) -> str:
    assert not result.is_ok
    assert result.error is not None
    assert result.error.exit_category == 11
    return result.error.code


def _source_relative(identity: Any) -> str:
    return f"sources/zagrosi/zagrosi-forge/{identity.install_version}/marketplace"


def _cache_relative(identity: Any) -> str:
    return f"cache/zagrosi/zagrosi-forge/{identity.install_version}"


def _manifest_relative(identity: Any, role: str) -> str:
    generation = (
        _source_relative(identity) if role == "source" else _cache_relative(identity)
    )
    suffix = (
        "plugins/zagrosi-forge/.codex-plugin/bundle-manifest.json"
        if role == "source"
        else ".codex-plugin/bundle-manifest.json"
    )
    return f"{generation}/{suffix}"


def _receipt(
    identity: Any,
    *,
    relative: str,
    manifest: str,
    cache_manifest: str = "f" * 64,
) -> dict[str, object]:
    from zagrosi_forge.install.ownership import RECEIPT_SCHEMA_DIGEST

    return {
        "record_kind": "committed",
        "schema_version": "1.0",
        "schema_digest": RECEIPT_SCHEMA_DIGEST,
        "writer_version": "0.2.0",
        "minimum_reader_version": "0.2.0",
        "state_machine_version": "1.0",
        "policy_version": "1.0",
        "transformation_version": "plugin-v1",
        "effective_marketplace_id": "zagrosi",
        "identity": {
            "marketplace_id": identity.marketplace_id,
            "plugin_id": identity.plugin_id,
            "base_version": identity.base_version,
            "install_version": identity.install_version,
            "base_payload_digest": identity.base_payload_digest,
            "rendered_payload_digest": identity.rendered_payload_digest,
            "policy_digest": identity.policy_digest,
            "transformation_profile": identity.transformation_profile,
            "contract_versions": list(identity.contract_versions),
        },
        "transaction": {"id": "tx-committed", "lineage": ["tx-committed"]},
        "source": {"relative_path": relative, "manifest_digest": manifest},
        "cache": {
            "relative_path": _cache_relative(identity),
            "manifest_digest": cache_manifest,
        },
        "config": {
            "path_id": "codex-config",
            "before_digest": "0" * 64,
            "after_digest": "1" * 64,
        },
        "tools": {
            "installer_version": "0.2.0",
            "python_version": "3.11.0",
            "codex_version": "0.1.0",
            "platform": "linux",
            "verifier_version": "1.0.0",
        },
        "created_at": "2026-07-15T00:00:00Z",
    }


def _write_record(path: Path, record: dict[str, object]) -> None:
    from zagrosi_forge.install.contracts import canonical_json_bytes

    payload = dict(record)
    payload["record_digest"] = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(payload, final_newline=True))
    path.chmod(0o600)


def _record_bytes(record: dict[str, object]) -> bytes:
    from zagrosi_forge.install.contracts import canonical_json_bytes

    payload = copy.deepcopy(record)
    payload["record_digest"] = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    return canonical_json_bytes(payload, final_newline=True)


def _set_untrusted_xattr(target: Path) -> None:
    if os.name != "posix" or not (
        sys.platform.startswith("linux") or sys.platform == "darwin"
    ):
        pytest.skip("descriptor xattr policy is POSIX-specific")
    name = (
        "com.zagrosi.unhandled"
        if sys.platform == "darwin"
        else "user.zagrosi.unhandled"
    )
    unsupported = {errno.ENOTSUP, errno.EPERM}
    if hasattr(errno, "EOPNOTSUPP"):
        unsupported.add(errno.EOPNOTSUPP)
    setter = getattr(os, "setxattr", None)
    if callable(setter):
        try:
            setter(target, name, b"hostile")
            return
        except OSError as exc:
            if exc.errno not in unsupported:
                raise
        except (NotImplementedError, TypeError):
            pass
    xattr = Path("/usr/bin/xattr")
    if sys.platform != "darwin" or not xattr.is_file():
        pytest.skip("test filesystem has no writable xattr interface")
    completed = subprocess.run(
        [str(xattr), "-w", name, "hostile", str(target)],
        check=False,
        capture_output=True,
        timeout=10,
    )
    if completed.returncode != 0:
        pytest.skip("test filesystem does not support writable xattrs")


def _macos_metadata_commands(
    target: Path, metadata_kind: str
) -> tuple[list[str], list[str]]:
    if os.name != "posix" or sys.platform != "darwin":
        pytest.skip("extended ACL and file-flag policy is macOS-specific")
    if metadata_kind == "extended-acl":
        return (
            ["/bin/chmod", "+a", "everyone deny execute", str(target)],
            ["/bin/chmod", "-N", str(target)],
        )
    return (
        ["/usr/bin/chflags", "hidden", str(target)],
        ["/usr/bin/chflags", "nohidden", str(target)],
    )


def _receipt_proof(tmp_path: Path):
    from zagrosi_forge.install.ownership import (
        committed_receipt_reference,
        install_identity_digest,
        observe_generation_identity,
        publish_committed_receipt,
        validate_committed_receipt,
    )

    authority, owned, root_path = _owned(tmp_path)
    identity = _install_identity()
    relative = _source_relative(identity)
    _private_test_directory_tree(root_path, relative)
    manifest_relative = _manifest_relative(identity, "source")
    manifest_path = root_path / manifest_relative
    _private_test_directory_tree(root_path, str(Path(manifest_relative).parent))
    manifest_path.write_bytes(b"source-generation-manifest\n")
    observed_path = authority.prove_descendant(
        owned,
        _reference(relative),
        expected_depth=len(relative.split("/")),
    ).unwrap()
    with authority.open_source_root(root_path) as manifest_root:
        with manifest_root.open_regular_file(
            _reference(manifest_relative)
        ) as opened_manifest:
            observed = observe_generation_identity(
                effective_marketplace_id="zagrosi",
                root_role="source",
                identity=identity,
                path=observed_path,
                manifest=opened_manifest,
            ).unwrap()
    receipt_relative = (
        f".zagrosi/ownership/zagrosi/zagrosi-forge/"
        f"{install_identity_digest(identity)}.json"
    )
    receipt_path = root_path / receipt_relative
    raw = _record_bytes(
        _receipt(identity, relative=relative, manifest=observed.manifest_digest)
    )
    publish_committed_receipt(owned, raw=raw).unwrap()
    source = authority.open_source_root(root_path)
    opened = source.open_regular_file(committed_receipt_reference("zagrosi", identity))
    result = validate_committed_receipt(opened, owned_root=owned, observed=observed)
    return result, receipt_path, opened, source, observed_path, owned


def _receipt_relation(tmp_path: Path):
    from zagrosi_forge.install.ownership import (
        committed_receipt_reference,
        observe_generation_identity,
        publish_committed_receipt,
        validate_active_install_relation,
    )

    authority, owned, root_path = _owned(tmp_path)
    identity = _install_identity()
    source_relative = _source_relative(identity)
    cache_relative = _cache_relative(identity)
    (root_path / source_relative).mkdir(parents=True, mode=0o700)
    (root_path / cache_relative).mkdir(parents=True, mode=0o700)
    source_manifest_relative = _manifest_relative(identity, "source")
    cache_manifest_relative = _manifest_relative(identity, "cache")
    source_manifest_path = root_path / source_manifest_relative
    cache_manifest_path = root_path / cache_manifest_relative
    source_manifest_path.parent.mkdir(parents=True)
    cache_manifest_path.parent.mkdir(parents=True)
    source_manifest_path.write_bytes(b"source-generation-manifest\n")
    cache_manifest_path.write_bytes(b"cache-generation-manifest\n")
    source_path = authority.prove_descendant(
        owned,
        _reference(source_relative),
        expected_depth=len(source_relative.split("/")),
    ).unwrap()
    cache_path = authority.prove_descendant(
        owned,
        _reference(cache_relative),
        expected_depth=len(cache_relative.split("/")),
    ).unwrap()
    with authority.open_source_root(root_path) as manifest_root:
        with manifest_root.open_regular_file(
            _reference(source_manifest_relative)
        ) as opened_source_manifest:
            source = observe_generation_identity(
                effective_marketplace_id="zagrosi",
                root_role="source",
                identity=identity,
                path=source_path,
                manifest=opened_source_manifest,
            ).unwrap()
        with manifest_root.open_regular_file(
            _reference(cache_manifest_relative)
        ) as opened_cache_manifest:
            cache = observe_generation_identity(
                effective_marketplace_id="zagrosi",
                root_role="cache",
                identity=identity,
                path=cache_path,
                manifest=opened_cache_manifest,
            ).unwrap()
    raw = _record_bytes(
        _receipt(
            identity,
            relative=source_relative,
            manifest=source.manifest_digest,
            cache_manifest=cache.manifest_digest,
        )
    )
    publish_committed_receipt(owned, raw=raw).unwrap()
    receipt_root = authority.open_source_root(root_path)
    opened = receipt_root.open_regular_file(
        committed_receipt_reference("zagrosi", identity)
    )
    result = validate_active_install_relation(
        opened,
        owned_root=owned,
        source=source,
        cache=cache,
    )
    return result, opened, receipt_root, source, cache, source_path, cache_path, owned


def _write_private_receipt_replacement(
    owned: Any,
    receipt_path: Path,
    reference: Any,
    raw: bytes,
    *,
    create_parent: bool,
) -> None:
    if os.name != "nt":
        if create_parent:
            receipt_path.parent.mkdir(mode=0o700)
        receipt_path.write_bytes(raw)
        receipt_path.chmod(0o600)
        return

    import zagrosi_forge.install.ownership as ownership

    control = parent = leaf = 0
    try:
        control = owned._duplicate_control_descriptor()
        volume = ownership._paths._windows_handle_status(control).identity[0]
        if create_parent:
            grandparent = ownership._windows_open_private_directory_chain(
                control,
                reference.components[1:-2],
                volume=volume,
                create_missing=False,
            )
            try:
                parent = ownership._paths._windows_create_private_directory(
                    grandparent,
                    reference.components[-2],
                )
            finally:
                ownership._paths._windows_close(grandparent)
        else:
            parent = ownership._windows_open_private_directory_chain(
                control,
                reference.components[1:-1],
                volume=volume,
                create_missing=False,
            )
        leaf = ownership._paths._windows_create_private_file(
            parent,
            reference.components[-1],
        )
        ownership._windows_write_all(leaf, raw)
        ownership._windows_flush(leaf)
    finally:
        for handle in (leaf, parent, control):
            if handle:
                ownership._paths._windows_close(handle)


def _validate_existing_receipt(
    receipt_path: Path, identity: Any, owned: Any, observed: Any
):
    from zagrosi_forge.install.ownership import (
        committed_receipt_reference,
        validate_committed_receipt,
    )
    from zagrosi_forge.install.paths import PlatformPathAuthority

    authority = PlatformPathAuthority()
    with authority.open_source_root(receipt_path.parents[4]) as receipt_root:
        with receipt_root.open_regular_file(
            committed_receipt_reference("zagrosi", identity)
        ) as opened:
            return validate_committed_receipt(
                opened, owned_root=owned, observed=observed
            )


def test_receipt_key_uses_effective_id_and_full_identity_digest(tmp_path: Path) -> None:
    from zagrosi_forge.install.ownership import (
        install_identity_digest,
        validate_committed_receipt,
    )

    result, receipt_path, opened, source, observed_path, owned = _receipt_proof(
        tmp_path
    )
    proof = result.unwrap()
    expected_digest = install_identity_digest(proof.observed.identity)
    assert receipt_path.parts[-5:] == (
        ".zagrosi",
        "ownership",
        "zagrosi",
        "zagrosi-forge",
        f"{expected_digest}.json",
    )
    assert len(receipt_path.stem) == 64
    with pytest.raises(AttributeError):
        proof.relative = _reference("sources/retargeted")
    with pytest.raises(AttributeError):
        proof.identity = (0, 0)
    observed = proof.observed
    proof.close()
    if os.name == "posix":
        for root_path in (receipt_path.parents[3], receipt_path.parents[4]):
            original_mode = root_path.stat().st_mode & 0o777
            root_path.chmod(0o777)
            assert (
                _code(
                    validate_committed_receipt(
                        opened, owned_root=owned, observed=observed
                    )
                )
                == "ownership.identity_mismatch"
            )
            root_path.chmod(original_mode)
    control = receipt_path.parents[3]
    if os.name == "nt":
        (control / "control-v1.json").write_bytes(b"invalid-control-record")
    else:
        displaced_control = receipt_path.parents[4] / ".zagrosi-displaced"
        control.rename(displaced_control)
        control.mkdir(mode=0o700)
    assert (
        _code(validate_committed_receipt(opened, owned_root=owned, observed=observed))
        == "ownership.identity_mismatch"
    )
    opened.close()
    source.close()
    observed_path.close()
    owned.close()


def test_committed_receipt_is_immutable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import zagrosi_forge.install.ownership as ownership
    from zagrosi_forge.install.ownership import (
        committed_receipt_reference,
        publish_committed_receipt,
    )

    _, owned, root = _owned(tmp_path)
    identity = _install_identity()
    record = _receipt(
        identity,
        relative=_source_relative(identity),
        manifest="9" * 64,
    )
    raw = _record_bytes(record)

    for name, operation in (("write", "write"), ("fsync", "fsync")):
        failure_root = tmp_path / f"{name}-failure"
        failure_root.mkdir()
        _, failure_owned, failure_plugins = _owned(failure_root)
        failure_path = (
            failure_plugins / committed_receipt_reference("zagrosi", identity).value
        )
        with monkeypatch.context() as context:
            if os.name == "nt" and operation == "write":
                original_windows_write = ownership._windows_write_all

                def fail_windows_write(handle: int, data: bytes) -> None:
                    original_windows_write(handle, data[:1])
                    raise OSError("injected write failure")

                context.setattr(ownership, "_windows_write_all", fail_windows_write)
            elif os.name == "nt":
                context.setattr(
                    ownership,
                    "_windows_flush",
                    lambda _handle: (_ for _ in ()).throw(
                        OSError("injected flush failure")
                    ),
                )
            elif operation == "write":
                original_write = ownership.os.write
                calls = 0

                def fail_write(descriptor: int, data: object) -> int:
                    nonlocal calls
                    calls += 1
                    if calls > 1:
                        raise OSError("injected write failure")
                    view = memoryview(data)
                    return original_write(descriptor, view[:1])

                context.setattr(ownership.os, "write", fail_write)
            else:
                context.setattr(
                    ownership.os,
                    "fsync",
                    lambda _descriptor: (_ for _ in ()).throw(
                        OSError("injected fsync failure")
                    ),
                )
            assert not publish_committed_receipt(failure_owned, raw=raw).is_ok
        assert not failure_path.exists()
        failure_owned.close()

    first = publish_committed_receipt(owned, raw=raw).unwrap()
    assert first.created
    reference = committed_receipt_reference("zagrosi", identity)
    receipt_path = root / reference.value
    before = (receipt_path.read_bytes(), receipt_path.stat().st_ino)

    identical = publish_committed_receipt(owned, raw=raw).unwrap()
    assert not identical.created
    changed = copy.deepcopy(record)
    changed["created_at"] = "2026-07-15T00:00:01Z"
    assert (
        _code(publish_committed_receipt(owned, raw=_record_bytes(changed)))
        == "ownership.receipt_conflict"
    )
    assert (receipt_path.read_bytes(), receipt_path.stat().st_ino) == before
    owned.close()


def test_matching_receipt_retry_reestablishes_namespace_durability(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import zagrosi_forge.install.ownership as ownership

    _, owned, root = _owned(tmp_path)
    identity = _install_identity()
    raw = _record_bytes(
        _receipt(
            identity,
            relative=_source_relative(identity),
            manifest="9" * 64,
        )
    )
    reference = ownership.committed_receipt_reference("zagrosi", identity)
    receipt_path = root / reference.value
    renamed = False
    try:
        with monkeypatch.context() as context:
            if os.name == "nt":
                original_rename = ownership._windows_rename_handle

                def rename_then_mark(
                    handle: int,
                    parent: int,
                    leaf: str,
                ) -> None:
                    nonlocal renamed
                    original_rename(handle, parent, leaf)
                    renamed = True

                context.setattr(
                    ownership,
                    "_windows_rename_handle",
                    rename_then_mark,
                )
                original_flush_directory = ownership._windows_flush_directory

                def fail_post_rename_directory_flush(handle: int) -> None:
                    if renamed:
                        raise OSError("injected receipt namespace flush failure")
                    original_flush_directory(handle)

                context.setattr(
                    ownership,
                    "_windows_flush_directory",
                    fail_post_rename_directory_flush,
                )
            else:
                original_rename = ownership._exclusive_rename

                def rename_then_mark(
                    parent: int,
                    source: str,
                    leaf: str,
                ) -> None:
                    nonlocal renamed
                    original_rename(parent, source, leaf)
                    renamed = True

                context.setattr(
                    ownership,
                    "_exclusive_rename",
                    rename_then_mark,
                )
                original_fsync = ownership.os.fsync

                def fail_post_rename_directory_fsync(descriptor: int) -> None:
                    if renamed:
                        raise OSError("injected receipt namespace fsync failure")
                    original_fsync(descriptor)

                context.setattr(
                    ownership.os,
                    "fsync",
                    fail_post_rename_directory_fsync,
                )

            first = ownership.publish_committed_receipt(owned, raw=raw)

        assert not first.is_ok
        assert renamed
        assert receipt_path.read_bytes() == raw

        durability_calls = 0
        with monkeypatch.context() as context:
            if os.name == "nt":
                original_flush_directory = ownership._windows_flush_directory

                def count_directory_flush(handle: int) -> None:
                    nonlocal durability_calls
                    durability_calls += 1
                    original_flush_directory(handle)

                context.setattr(
                    ownership,
                    "_windows_flush_directory",
                    count_directory_flush,
                )
            else:
                original_fsync = ownership.os.fsync

                def count_directory_fsync(descriptor: int) -> None:
                    nonlocal durability_calls
                    durability_calls += 1
                    original_fsync(descriptor)

                context.setattr(
                    ownership.os,
                    "fsync",
                    count_directory_fsync,
                )

            retried = ownership.publish_committed_receipt(owned, raw=raw).unwrap()

        assert not retried.created
        assert durability_calls >= 1
        assert receipt_path.read_bytes() == raw
    finally:
        owned.close()


@pytest.mark.parametrize("preexisting", (False, True))
def test_receipt_publication_rejects_in_place_mutation_during_directory_flush(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    preexisting: bool,
) -> None:
    import zagrosi_forge.install.ownership as ownership

    _, owned, root = _owned(tmp_path)
    identity = _install_identity()
    raw = _record_bytes(
        _receipt(
            identity,
            relative=_source_relative(identity),
            manifest="9" * 64,
        )
    )
    reference = ownership.committed_receipt_reference("zagrosi", identity)
    receipt_path = root / reference.value
    mutated_raw = b"[" + raw[1:]
    mutation_identity: tuple[int, int] | None = None
    try:
        if preexisting:
            ownership.publish_committed_receipt(owned, raw=raw).unwrap()

        with monkeypatch.context() as context:
            if os.name == "nt":
                original_flush_directory = ownership._windows_flush_directory

                def flush_then_mutate(handle: int) -> None:
                    nonlocal mutation_identity
                    original_flush_directory(handle)
                    if receipt_path.exists() and mutation_identity is None:
                        mutation = ownership._windows_open_raw_child(
                            handle,
                            receipt_path.name,
                            directory=False,
                            read_data=True,
                            write_data=True,
                        )
                        try:
                            before = ownership._native_identity(mutation)
                            ownership._windows_replace_bytes(mutation, mutated_raw)
                            ownership._windows_flush(mutation)
                            after = ownership._native_identity(mutation)
                            assert after == before
                            mutation_identity = after
                        finally:
                            ownership._paths._windows_close(mutation)

                context.setattr(
                    ownership,
                    "_windows_flush_directory",
                    flush_then_mutate,
                )
            else:
                original_fsync = ownership.os.fsync

                def fsync_then_mutate(descriptor: int) -> None:
                    nonlocal mutation_identity
                    original_fsync(descriptor)
                    if receipt_path.exists() and mutation_identity is None:
                        before = receipt_path.stat()
                        receipt_path.write_bytes(mutated_raw)
                        after = receipt_path.stat()
                        assert (after.st_dev, after.st_ino) == (
                            before.st_dev,
                            before.st_ino,
                        )
                        mutation_identity = (after.st_dev, after.st_ino)

                context.setattr(ownership.os, "fsync", fsync_then_mutate)

            publication = ownership.publish_committed_receipt(owned, raw=raw)

        assert _code(publication) == "ownership.receipt_conflict"
        assert mutation_identity is not None
        assert receipt_path.read_bytes() == mutated_raw
        status = receipt_path.stat()
        assert (status.st_dev, status.st_ino) == mutation_identity
    finally:
        owned.close()


@pytest.mark.skipif(os.name == "nt", reason="POSIX close-failure injection")
def test_posix_receipt_publication_close_failure_attempts_every_native_close(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import zagrosi_forge.install.ownership as ownership

    _, owned, _ = _owned(tmp_path)
    identity = _install_identity()
    raw = _record_bytes(
        _receipt(
            identity,
            relative=_source_relative(identity),
            manifest="9" * 64,
        )
    )
    original_binding_state = ownership._receipt_binding_state
    original_close = ownership.os.close
    armed = False
    close_attempts: list[int] = []

    def binding_then_arm(*args: Any, **kwargs: Any) -> Any:
        nonlocal armed
        binding = original_binding_state(*args, **kwargs)
        armed = True
        return binding

    def close_and_fail_first(descriptor: int) -> None:
        if not armed:
            original_close(descriptor)
            return
        close_attempts.append(descriptor)
        original_close(descriptor)
        if len(close_attempts) == 1:
            raise OSError("injected receipt close failure")

    try:
        with monkeypatch.context() as context:
            context.setattr(
                ownership,
                "_receipt_binding_state",
                binding_then_arm,
            )
            context.setattr(ownership.os, "close", close_and_fail_first)

            publication = ownership.publish_committed_receipt(owned, raw=raw)

            assert _code(publication) == "ownership.receipt_invalid"
            assert len(close_attempts) == 3
    finally:
        owned.close()


def test_committed_receipt_rejects_mutated_internal_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import zagrosi_forge.install.ownership as ownership

    _, owned, root = _owned(tmp_path)
    identity = _install_identity()
    reference = ownership.committed_receipt_reference("zagrosi", identity)
    object.__setattr__(
        reference,
        "components",
        (".zagrosi", "..", "escaped-receipt.json"),
    )
    monkeypatch.setattr(
        ownership,
        "committed_receipt_reference",
        lambda _marketplace, _identity: reference,
    )
    raw = _record_bytes(
        _receipt(
            identity,
            relative=_source_relative(identity),
            manifest="9" * 64,
        )
    )

    result = ownership.publish_committed_receipt(owned, raw=raw)

    assert _code(result) == "ownership.receipt_invalid"
    assert not (root / "escaped-receipt.json").exists()
    owned.close()


@pytest.mark.parametrize("target_kind", ("receipt", "parent", "control"))
def test_receipt_authority_rejects_untrusted_xattrs(
    tmp_path: Path, target_kind: str
) -> None:
    from zagrosi_forge.install.ownership import (
        publish_committed_receipt,
        validate_committed_receipt,
    )

    result, receipt_path, opened, source, observed_path, owned = _receipt_proof(
        tmp_path
    )
    proof = result.unwrap()
    observed = proof.observed
    raw = receipt_path.read_bytes()
    target = {
        "receipt": receipt_path,
        "parent": receipt_path.parent,
        "control": receipt_path.parents[3],
    }[target_kind]
    proof.close()
    try:
        _set_untrusted_xattr(target)
        publication = publish_committed_receipt(owned, raw=raw)
        validation = validate_committed_receipt(
            opened,
            owned_root=owned,
            observed=observed,
        )
        expected_publication = (
            "ownership.unowned"
            if target_kind == "control"
            else "ownership.receipt_conflict"
        )
        expected_validation = (
            "ownership.receipt_invalid"
            if target_kind == "receipt"
            else "ownership.identity_mismatch"
        )
        assert _code(publication) == expected_publication
        assert _code(validation) == expected_validation
    finally:
        opened.close()
        source.close()
        observed_path.close()
        owned.close()


@pytest.mark.parametrize("metadata_kind", ("extended-acl", "file-flags"))
@pytest.mark.parametrize("target_kind", ("receipt", "parent", "control"))
def test_receipt_authority_rejects_macos_acl_and_flags(
    tmp_path: Path, target_kind: str, metadata_kind: str
) -> None:
    from zagrosi_forge.install.ownership import (
        publish_committed_receipt,
        validate_committed_receipt,
    )

    result, receipt_path, opened, source, observed_path, owned = _receipt_proof(
        tmp_path
    )
    proof = result.unwrap()
    observed = proof.observed
    raw = receipt_path.read_bytes()
    target = {
        "receipt": receipt_path,
        "parent": receipt_path.parent,
        "control": receipt_path.parents[3],
    }[target_kind]
    apply, clear = _macos_metadata_commands(target, metadata_kind)
    proof.close()
    applied = False
    try:
        completed = subprocess.run(
            apply,
            check=False,
            capture_output=True,
            timeout=10,
        )
        if completed.returncode != 0:
            pytest.skip("test filesystem rejected macOS security metadata")
        applied = True
        publication = publish_committed_receipt(owned, raw=raw)
        validation = validate_committed_receipt(
            opened,
            owned_root=owned,
            observed=observed,
        )
        expected_publication = (
            "ownership.unowned"
            if target_kind == "control"
            else "ownership.receipt_conflict"
        )
        expected_validation = (
            "ownership.receipt_invalid"
            if target_kind == "receipt"
            else "ownership.identity_mismatch"
        )
        assert _code(publication) == expected_publication
        assert _code(validation) == expected_validation
    finally:
        if applied:
            subprocess.run(
                clear,
                check=False,
                capture_output=True,
                timeout=10,
            )
        opened.close()
        source.close()
        observed_path.close()
        owned.close()


@pytest.mark.skipif(os.name != "posix", reason="POSIX descriptor policy")
def test_posix_receipt_matcher_rejects_untrusted_metadata(tmp_path: Path) -> None:
    import zagrosi_forge.install.ownership as ownership

    parent_path = tmp_path / "receipts"
    parent_path.mkdir(mode=0o700)
    raw = b"receipt"
    receipt_path = parent_path / "receipt.json"
    receipt_path.write_bytes(raw)
    receipt_path.chmod(0o644)
    parent = os.open(
        parent_path,
        os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW,
    )
    try:
        assert (
            ownership._open_matching_posix_receipt(
                parent,
                "receipt.json",
                raw,
                device=os.fstat(parent).st_dev,
            )
            is None
        )
    finally:
        os.close(parent)


@pytest.mark.parametrize("target_kind", ("parent", "leaf"))
def test_receipt_validation_rejects_canonical_rebind_and_preserves_state(
    tmp_path: Path, target_kind: str
) -> None:
    from zagrosi_forge.install.ownership import (
        committed_receipt_reference,
        validate_committed_receipt,
    )

    result, receipt_path, opened, source, observed_path, owned = _receipt_proof(
        tmp_path
    )
    proof = result.unwrap()
    observed = proof.observed
    raw = receipt_path.read_bytes()
    reference = committed_receipt_reference("zagrosi", observed.identity)
    parent = receipt_path.parent
    original_canary = parent / "original-canary"
    original_canary.write_bytes(b"preserve-original")
    proof.close()

    def file_state(path: Path) -> tuple[int, int, bytes]:
        status = path.stat()
        return status.st_dev, status.st_ino, path.read_bytes()

    if target_kind == "parent":
        displaced_parent = parent.with_name(f"{parent.name}-displaced")
        try:
            parent.rename(displaced_parent)
        except PermissionError:
            if os.name != "nt":
                raise
            before = (file_state(receipt_path), file_state(original_canary))
            validation = validate_committed_receipt(
                opened,
                owned_root=owned,
                observed=observed,
            )
            assert validation.is_ok
            validation.unwrap().close()
            assert (file_state(receipt_path), file_state(original_canary)) == before
            opened.close()
            source.close()
            observed_path.close()
            owned.close()
            return
        displaced_receipt = displaced_parent / receipt_path.name
        displaced_canary = displaced_parent / original_canary.name
        _write_private_receipt_replacement(
            owned,
            receipt_path,
            reference,
            raw,
            create_parent=True,
        )
    else:
        displaced_receipt = receipt_path.with_suffix(".displaced")
        receipt_path.rename(displaced_receipt)
        displaced_canary = original_canary
        _write_private_receipt_replacement(
            owned,
            receipt_path,
            reference,
            raw,
            create_parent=False,
        )
    replacement_canary = receipt_path.parent / "replacement-canary"
    replacement_canary.write_bytes(b"preserve-replacement")

    before = (
        file_state(displaced_receipt),
        file_state(receipt_path),
        file_state(displaced_canary),
        file_state(replacement_canary),
    )

    validation = validate_committed_receipt(
        opened,
        owned_root=owned,
        observed=observed,
    )

    assert _code(validation) == "ownership.identity_mismatch"
    assert (
        file_state(displaced_receipt),
        file_state(receipt_path),
        file_state(displaced_canary),
        file_state(replacement_canary),
    ) == before
    opened.close()
    source.close()
    observed_path.close()
    owned.close()


@pytest.mark.parametrize("target_kind", ("parent", "leaf"))
def test_receipt_publication_rejects_midflight_canonical_rebind_and_preserves_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target_kind: str
) -> None:
    import zagrosi_forge.install.ownership as ownership

    _, owned, root = _owned(tmp_path)
    identity = _install_identity()
    raw = _record_bytes(
        _receipt(
            identity,
            relative=_source_relative(identity),
            manifest="9" * 64,
        )
    )
    reference = ownership.committed_receipt_reference("zagrosi", identity)
    receipt_path = root / reference.value
    ownership.publish_committed_receipt(owned, raw=raw).unwrap()
    parent = receipt_path.parent
    original_canary = parent / "original-canary"
    original_canary.write_bytes(b"preserve-original")
    rebound_paths: tuple[Path, Path, Path, Path] | None = None
    before: tuple[tuple[int, int, bytes], ...] | None = None

    def file_state(path: Path) -> tuple[int, int, bytes]:
        status = path.stat()
        return status.st_dev, status.st_ino, path.read_bytes()

    def rebind() -> None:
        nonlocal before, rebound_paths
        if rebound_paths is not None:
            return
        if target_kind == "parent":
            displaced_parent = parent.with_name(f"{parent.name}-displaced")
            parent.rename(displaced_parent)
            displaced_receipt = displaced_parent / receipt_path.name
            displaced_canary = displaced_parent / original_canary.name
            _write_private_receipt_replacement(
                owned,
                receipt_path,
                reference,
                raw,
                create_parent=True,
            )
        else:
            displaced_receipt = receipt_path.with_suffix(".displaced")
            receipt_path.rename(displaced_receipt)
            displaced_canary = original_canary
            _write_private_receipt_replacement(
                owned,
                receipt_path,
                reference,
                raw,
                create_parent=False,
            )
        replacement_canary = receipt_path.parent / "replacement-canary"
        replacement_canary.write_bytes(b"preserve-replacement")
        rebound_paths = (
            displaced_receipt,
            receipt_path,
            displaced_canary,
            replacement_canary,
        )
        before = tuple(file_state(path) for path in rebound_paths)

    if os.name == "nt" and target_kind == "parent":
        original_open = ownership._windows_open_raw_child

        def rebind_before_receipt_open(
            parent_handle: int,
            component: str,
            *,
            directory: bool | None,
            read_data: bool = False,
            write_data: bool = False,
            delete_access: bool = False,
            create: bool = False,
            security_descriptor: int | None = None,
        ) -> int:
            if (
                component == receipt_path.name
                and read_data
                and not create
                and rebound_paths is None
            ):
                rebind()
            return original_open(
                parent_handle,
                component,
                directory=directory,
                read_data=read_data,
                write_data=write_data,
                delete_access=delete_access,
                create=create,
                security_descriptor=security_descriptor,
            )

        monkeypatch.setattr(
            ownership,
            "_windows_open_raw_child",
            rebind_before_receipt_open,
        )
    elif os.name == "nt":
        original_read = ownership._paths._windows_read

        def rebind_after_read(handle: int, *, limit: int) -> bytes:
            rendered = original_read(handle, limit=limit)
            if limit == 256 * 1024 and rebound_paths is None:
                rebind()
            return rendered

        monkeypatch.setattr(ownership._paths, "_windows_read", rebind_after_read)
    else:
        original_match = ownership._open_matching_posix_receipt

        def rebind_after_match(
            parent_descriptor: int,
            leaf: str,
            candidate: bytes,
            *,
            device: int,
        ) -> int | None:
            matched = original_match(
                parent_descriptor,
                leaf,
                candidate,
                device=device,
            )
            if matched is not None:
                rebind()
            return matched

        monkeypatch.setattr(
            ownership,
            "_open_matching_posix_receipt",
            rebind_after_match,
        )

    publication = ownership.publish_committed_receipt(owned, raw=raw)

    assert _code(publication) == "ownership.receipt_conflict"
    assert rebound_paths is not None
    assert before is not None
    assert tuple(file_state(path) for path in rebound_paths) == before
    owned.close()


def test_committed_receipt_is_hidden_until_atomic_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import zagrosi_forge.install.ownership as ownership
    from zagrosi_forge.install.ownership import (
        committed_receipt_reference,
        publish_committed_receipt,
    )

    _, owned, root = _owned(tmp_path)
    identity = _install_identity()
    raw = _record_bytes(
        _receipt(
            identity,
            relative=_source_relative(identity),
            manifest="9" * 64,
        )
    )
    reference = committed_receipt_reference("zagrosi", identity)
    receipt_path = root / reference.value
    observed_atomic_publish = False

    if os.name == "nt":
        staged_leaf: str | None = None
        staged_handle = 0
        staged_parent = 0
        original_open = ownership._windows_open_raw_child
        original_windows_rename = ownership._windows_rename_handle

        def observe_windows_open(
            parent: int,
            component: str,
            *,
            directory: bool | None,
            read_data: bool = False,
            write_data: bool = False,
            delete_access: bool = False,
            create: bool = False,
            security_descriptor: int | None = None,
        ) -> int:
            nonlocal staged_handle, staged_leaf, staged_parent
            handle = original_open(
                parent,
                component,
                directory=directory,
                read_data=read_data,
                write_data=write_data,
                delete_access=delete_access,
                create=create,
                security_descriptor=security_descriptor,
            )
            if create:
                staged_handle = handle
                staged_leaf = component
                staged_parent = parent
            return handle

        def observe_windows_publish(source: int, parent: int, destination: str) -> None:
            nonlocal observed_atomic_publish
            observed_atomic_publish = True
            assert staged_leaf is not None
            assert staged_leaf.startswith(".receipt-")
            assert staged_leaf.endswith(".tmp")
            assert staged_leaf != destination
            assert source == staged_handle
            assert parent == staged_parent
            assert destination == receipt_path.name
            staged_status = ownership._paths._windows_handle_status(source)
            assert not staged_status.is_directory
            assert staged_status.size == len(raw)
            original_windows_rename(source, parent, destination)

        monkeypatch.setattr(
            ownership,
            "_windows_open_raw_child",
            observe_windows_open,
        )
        monkeypatch.setattr(
            ownership, "_windows_rename_handle", observe_windows_publish
        )
    else:
        original_rename = ownership._exclusive_rename

        def observe_publish(parent: int, source: str, destination: str) -> None:
            nonlocal observed_atomic_publish
            observed_atomic_publish = True
            assert source.startswith(".receipt-") and source.endswith(".tmp")
            assert destination == receipt_path.name
            assert not receipt_path.exists()
            assert (receipt_path.parent / source).read_bytes() == raw
            original_rename(parent, source, destination)

        monkeypatch.setattr(ownership, "_exclusive_rename", observe_publish)
    assert publish_committed_receipt(owned, raw=raw).unwrap().created
    assert observed_atomic_publish
    assert receipt_path.read_bytes() == raw
    assert not tuple(receipt_path.parent.glob(".receipt-*.tmp"))
    owned.close()


def test_committed_receipt_publish_failure_cleans_only_its_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import zagrosi_forge.install.ownership as ownership
    from zagrosi_forge.install.ownership import (
        committed_receipt_reference,
        publish_committed_receipt,
    )

    _, owned, root = _owned(tmp_path)
    identity = _install_identity()
    raw = _record_bytes(
        _receipt(
            identity,
            relative=_source_relative(identity),
            manifest="9" * 64,
        )
    )
    receipt_path = root / committed_receipt_reference("zagrosi", identity).value

    def fail_publish(_parent: int, _source: str, _destination: str) -> None:
        raise OSError("injected pre-publish crash")

    if os.name == "nt":
        monkeypatch.setattr(ownership, "_windows_rename_handle", fail_publish)
    else:
        monkeypatch.setattr(ownership, "_exclusive_rename", fail_publish)
    assert not publish_committed_receipt(owned, raw=raw).is_ok
    assert not receipt_path.exists()
    assert not tuple(receipt_path.parent.glob(".receipt-*.tmp"))
    owned.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows namespace race")
def test_windows_receipt_publish_revalidates_final_namespace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import zagrosi_forge.install.ownership as ownership
    from zagrosi_forge.install.ownership import (
        committed_receipt_reference,
        publish_committed_receipt,
    )

    _, owned, root = _owned(tmp_path)
    identity = _install_identity()
    raw = _record_bytes(
        _receipt(
            identity,
            relative=_source_relative(identity),
            manifest="9" * 64,
        )
    )
    receipt_path = root / committed_receipt_reference("zagrosi", identity).value
    displaced = receipt_path.with_suffix(".displaced")
    original_rename = ownership._windows_rename_handle

    def swap_after_rename(source: int, parent: int, destination: str) -> None:
        original_rename(source, parent, destination)
        receipt_path.rename(displaced)
        receipt_path.write_bytes(b"external")

    monkeypatch.setattr(ownership, "_windows_rename_handle", swap_after_rename)
    assert (
        _code(publish_committed_receipt(owned, raw=raw)) == "ownership.receipt_conflict"
    )
    assert receipt_path.read_bytes() == b"external"
    assert displaced.read_bytes() == raw
    owned.close()


def test_unknown_future_or_corrupt_receipt_preserves_state(tmp_path: Path) -> None:
    result, receipt_path, opened, source, observed_path, owned = _receipt_proof(
        tmp_path
    )
    valid_proof = result.unwrap()
    observed = valid_proof.observed
    valid_proof.close()
    opened.close()
    source.close()
    generation = receipt_path.parents[4] / observed.path.relative.value
    marker = generation / "preserve"
    marker.write_bytes(b"managed")
    before = (generation.stat().st_ino, marker.read_bytes())
    base = _receipt(
        observed.identity,
        relative=_source_relative(observed.identity),
        manifest=observed.manifest_digest,
    )

    invalid_records = []
    unknown = copy.deepcopy(base)
    unknown["unknown"] = True
    invalid_records.append(unknown)
    nested_unknown = copy.deepcopy(base)
    cast_config = nested_unknown["config"]
    assert isinstance(cast_config, dict)
    cast_config["unknown"] = True
    invalid_records.append(nested_unknown)
    bad_lineage = copy.deepcopy(base)
    cast_transaction = bad_lineage["transaction"]
    assert isinstance(cast_transaction, dict)
    cast_transaction["lineage"] = []
    invalid_records.append(bad_lineage)
    control_path = copy.deepcopy(base)
    cast_source = control_path["source"]
    assert isinstance(cast_source, dict)
    cast_source["relative_path"] = "ownership/zagrosi/zagrosi-forge/state"
    invalid_records.append(control_path)
    for invalid in invalid_records:
        _write_record(receipt_path, invalid)
        assert (
            _code(
                _validate_existing_receipt(
                    receipt_path, observed.identity, owned, observed
                )
            )
            == "ownership.receipt_invalid"
        )
        assert (generation.stat().st_ino, marker.read_bytes()) == before

    future = copy.deepcopy(base)
    future["schema_version"] = "2.0"
    _write_record(receipt_path, future)
    assert (
        _code(
            _validate_existing_receipt(receipt_path, observed.identity, owned, observed)
        )
        == "ownership.receipt_unsupported"
    )

    raw = bytearray(_record_bytes(base))
    decoded = json.loads(raw)
    digest = decoded["record_digest"].encode("ascii")
    offset = raw.index(digest)
    raw[offset] = ord("0") if raw[offset] != ord("0") else ord("1")
    receipt_path.write_bytes(bytes(raw))
    assert (
        _code(
            _validate_existing_receipt(receipt_path, observed.identity, owned, observed)
        )
        == "ownership.receipt_corrupt"
    )
    assert (generation.stat().st_ino, marker.read_bytes()) == before
    observed_path.close()
    owned.close()


def test_receipt_schema_digest_and_minimum_reader_are_enforced(tmp_path: Path) -> None:
    from zagrosi_forge.install.ownership import (
        committed_receipt_reference,
        validate_committed_receipt,
    )
    from zagrosi_forge.install.paths import PlatformPathAuthority

    result, receipt_path, opened, source, observed_path, owned = _receipt_proof(
        tmp_path
    )
    assert result.is_ok
    proof = result.unwrap()
    observed = proof.observed
    proof.close()
    opened.close()
    source.close()

    bad_schema = _receipt(
        observed.identity,
        relative=_source_relative(observed.identity),
        manifest=observed.manifest_digest,
    )
    bad_schema["schema_digest"] = "0" * 64
    _write_record(receipt_path, bad_schema)
    authority = PlatformPathAuthority()
    with authority.open_source_root(receipt_path.parents[4]) as receipt_root:
        with receipt_root.open_regular_file(
            committed_receipt_reference("zagrosi", observed.identity)
        ) as bad_receipt:
            assert (
                _code(
                    validate_committed_receipt(
                        bad_receipt, owned_root=owned, observed=observed
                    )
                )
                == "ownership.receipt_unsupported"
            )

    future_reader = _receipt(
        observed.identity,
        relative=_source_relative(observed.identity),
        manifest=observed.manifest_digest,
    )
    future_reader["minimum_reader_version"] = "99.0.0"
    _write_record(receipt_path, future_reader)
    assert (
        _code(
            _validate_existing_receipt(receipt_path, observed.identity, owned, observed)
        )
        == "ownership.receipt_unsupported"
    )

    for field in (
        "state_machine_version",
        "policy_version",
        "transformation_version",
    ):
        wrong_authority = _receipt(
            observed.identity,
            relative=_source_relative(observed.identity),
            manifest=observed.manifest_digest,
        )
        wrong_authority[field] = "2.0"
        _write_record(receipt_path, wrong_authority)
        assert (
            _code(
                _validate_existing_receipt(
                    receipt_path, observed.identity, owned, observed
                )
            )
            == "ownership.receipt_unsupported"
        )
    observed_path.close()
    owned.close()


def test_receipt_identity_or_manifest_mismatch_denies_cleanup(tmp_path: Path) -> None:
    from zagrosi_forge.install.ownership import validate_committed_receipt

    result, _, opened, source, observed_path, owned = _receipt_proof(tmp_path)
    assert result.is_ok
    proof = result.unwrap()
    proof.close()
    observed = proof.observed
    original_manifest = observed.manifest_digest
    object.__setattr__(observed, "manifest_digest", "8" * 64)
    assert (
        _code(validate_committed_receipt(opened, owned_root=owned, observed=observed))
        == "ownership.receipt_invalid"
    )
    object.__setattr__(observed, "manifest_digest", original_manifest)
    original_identity = observed.identity
    object.__setattr__(
        observed, "identity", _install_identity(rendered_digest="7" * 64)
    )
    assert (
        _code(validate_committed_receipt(opened, owned_root=owned, observed=observed))
        == "ownership.receipt_invalid"
    )
    object.__setattr__(observed, "identity", original_identity)
    opened.close()
    source.close()
    observed_path.close()
    owned.close()


def test_observed_generation_identity_cannot_be_forged_from_manifest_digest(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.ownership import (
        ObservedGenerationIdentity,
        validate_committed_receipt,
    )

    result, opened, receipt_root, source, _, source_path, cache_path, owned = (
        _receipt_relation(tmp_path)
    )
    assert result.is_ok
    with pytest.raises(TypeError, match="created only by verified observation"):
        ObservedGenerationIdentity(
            effective_marketplace_id=source.effective_marketplace_id,
            root_role=source.root_role,
            identity=source.identity,
            path=source.path,
            manifest_digest=source.manifest_digest,
            _token=object(),
        )
    object.__setattr__(source, "manifest_digest", "7" * 64)
    assert (
        _code(validate_committed_receipt(opened, owned_root=owned, observed=source))
        == "ownership.receipt_invalid"
    )
    opened.close()
    receipt_root.close()
    source_path.close()
    cache_path.close()
    owned.close()


def test_validated_relation_requires_both_live_generation_roles_and_projects_receipt(
    tmp_path: Path,
) -> None:
    result, opened, receipt_root, source, cache, source_path, cache_path, owned = (
        _receipt_relation(tmp_path)
    )
    validated = result.unwrap()
    active = validated.active
    assert active.identity == source.identity == cache.identity
    assert active.effective_marketplace_id == "zagrosi"
    assert active.source_generation == source.path.relative.value
    assert active.cache_generation == cache.path.relative.value
    assert len(active.managed_config_projection.nodes) == 3
    assert validated.config_before_snapshot_digest == "0" * 64
    assert validated.config_after_snapshot_digest == "1" * 64
    assert validated.source_manifest_digest == source.manifest_digest
    assert validated.cache_manifest_digest == cache.manifest_digest
    assert validated.source_identity == source.path.leaf_identity
    assert validated.cache_identity == cache.path.leaf_identity
    opened.close()
    receipt_root.close()
    source_path.close()
    cache_path.close()
    owned.close()


def test_relation_revalidates_source_after_final_cache_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import zagrosi_forge.install.ownership as ownership

    result, opened, receipt_root, source, cache, source_path, cache_path, owned = (
        _receipt_relation(tmp_path)
    )
    assert result.is_ok
    original_validate = ownership.validate_committed_receipt
    path_type = type(source.path)
    original_reopen = path_type._duplicate_descriptor
    calls = 0
    source_rebound = False

    def rebind_before_final_cache(
        receipt: Any,
        *,
        owned_root: Any,
        observed: Any,
    ):
        nonlocal calls, source_rebound
        calls += 1
        if calls == 4 and observed is cache:
            source_rebound = True
        return original_validate(
            receipt,
            owned_root=owned_root,
            observed=observed,
        )

    def reopen_after_rebind(path: Any) -> int:
        if source_rebound and path is source.path:
            raise OSError("source generation rebound")
        return original_reopen(path)

    monkeypatch.setattr(
        ownership, "validate_committed_receipt", rebind_before_final_cache
    )
    monkeypatch.setattr(path_type, "_duplicate_descriptor", reopen_after_rebind)
    rejected = ownership.validate_active_install_relation(
        opened,
        owned_root=owned,
        source=source,
        cache=cache,
    )
    assert source_rebound
    assert _code(rejected) == "ownership.identity_mismatch"
    opened.close()
    receipt_root.close()
    source_path.close()
    cache_path.close()
    owned.close()


def test_validated_relation_rejects_each_identity_path_manifest_or_role_mismatch(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.ownership import validate_active_install_relation

    result, opened, receipt_root, source, cache, source_path, cache_path, owned = (
        _receipt_relation(tmp_path)
    )
    assert result.is_ok
    cases = (
        (source, "root_role", "cache"),
        (cache, "root_role", "source"),
        (cache, "manifest_digest", "7" * 64),
        (cache, "identity", _install_identity(rendered_digest="7" * 64)),
        (source, "path", cache.path),
    )
    for target, field, value in cases:
        original = getattr(target, field)
        object.__setattr__(target, field, value)
        rejected = validate_active_install_relation(
            opened,
            owned_root=owned,
            source=source,
            cache=cache,
        )
        assert _code(rejected) == "ownership.receipt_invalid"
        object.__setattr__(target, field, original)
    opened.close()
    receipt_root.close()
    source_path.close()
    cache_path.close()
    owned.close()


def test_validated_relation_is_sealed_read_only_and_not_deletion_authority(
    tmp_path: Path,
) -> None:
    import pickle

    from zagrosi_forge.install.contracts import ForgeError
    from zagrosi_forge.install.ownership import (
        ValidatedInstallRelation,
        prove_transaction_owned,
    )

    result, opened, receipt_root, source, cache, source_path, cache_path, owned = (
        _receipt_relation(tmp_path)
    )
    validated = result.unwrap()
    with pytest.raises(TypeError, match="created only by receipt validation"):
        ValidatedInstallRelation(
            active=validated.active,
            config_before_snapshot_digest=validated.config_before_snapshot_digest,
            config_after_snapshot_digest=validated.config_after_snapshot_digest,
            source_manifest_digest=validated.source_manifest_digest,
            cache_manifest_digest=validated.cache_manifest_digest,
            source_identity=validated.source_identity,
            cache_identity=validated.cache_identity,
            receipt_identity=validated.receipt_identity,
            source_observation=source,
            cache_observation=cache,
            _token=object(),
        )
    with pytest.raises((AttributeError, TypeError)):
        validated.active = validated.active  # type: ignore[misc]
    projected = validated.active
    object.__setattr__(projected, "source_generation", "sources/retargeted")
    assert validated.active.source_generation != projected.source_generation
    with pytest.raises(TypeError, match="not serializable"):
        pickle.dumps(validated)
    assert _code(prove_transaction_owned(source_path, claim=validated)) == (
        "ownership.unowned"
    )
    assert _code(prove_transaction_owned(source_path, claim=validated.active)) == (
        "ownership.unowned"
    )
    object.__setattr__(validated, "_config_after_snapshot_digest", "7" * 64)
    with pytest.raises(ForgeError) as caught:
        _ = validated.config_after_snapshot_digest
    assert caught.value.code == "ownership.identity_mismatch"
    opened.close()
    receipt_root.close()
    source_path.close()
    cache_path.close()
    owned.close()


def test_recognized_legacy_never_authorizes_old_cache_deletion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import zagrosi_forge.install.ownership as ownership
    from zagrosi_forge.install.ownership import (
        load_legacy_install_catalog,
        match_legacy_install,
        prove_transaction_owned,
    )

    authority, owned, root = _owned(tmp_path)
    (root / "cache/zagrosi/zagrosi-forge/0.2.0").mkdir(parents=True)
    path = authority.prove_descendant(
        owned, _reference("cache/zagrosi/zagrosi-forge/0.2.0"), expected_depth=4
    ).unwrap()
    catalog = load_legacy_install_catalog().unwrap()
    legacy = match_legacy_install(
        catalog,
        marketplace_id="zagrosi",
        marketplace_table={
            "source_type": "local",
            "source": "/checkout/zagrosi-forge",
        },
        plugin_key="zagrosi-forge@zagrosi",
        plugin_table={"enabled": True},
        cache_relative=_reference("cache/zagrosi/zagrosi-forge/0.2.0"),
    ).unwrap()
    assert legacy is not None
    assert _code(prove_transaction_owned(path, claim=legacy)) == "ownership.unowned"
    monkeypatch.setattr(ownership, "_LEGACY_CATALOG_RESOURCE_DIGEST", "0" * 64)
    rejected = load_legacy_install_catalog()
    assert _code(rejected) == "ownership.receipt_invalid"
    path.close()
    owned.close()


@pytest.mark.parametrize(
    ("marketplace_id", "marketplace_table", "plugin_key", "plugin_table", "cache"),
    (
        (
            "zagrosi-2",
            {"source_type": "local", "source": "/checkout/zagrosi-forge"},
            "zagrosi-forge@zagrosi",
            {"enabled": True},
            "cache/zagrosi/zagrosi-forge/0.2.0",
        ),
        (
            "zagrosi",
            {
                "source_type": "local",
                "source": "/checkout/zagrosi-forge",
                "extra": True,
            },
            "zagrosi-forge@zagrosi",
            {"enabled": True},
            "cache/zagrosi/zagrosi-forge/0.2.0",
        ),
        (
            "zagrosi",
            {"source_type": "git", "source": "/checkout/zagrosi-forge"},
            "zagrosi-forge@zagrosi",
            {"enabled": True},
            "cache/zagrosi/zagrosi-forge/0.2.0",
        ),
        (
            "zagrosi",
            {"source_type": "local", "source": "/checkout/not-forge"},
            "zagrosi-forge@zagrosi",
            {"enabled": True},
            "cache/zagrosi/zagrosi-forge/0.2.0",
        ),
        (
            "zagrosi",
            {"source_type": "local", "source": "/checkout/zagrosi-forge"},
            "zagrosi-forge@other",
            {"enabled": True},
            "cache/zagrosi/zagrosi-forge/0.2.0",
        ),
        (
            "zagrosi",
            {"source_type": "local", "source": "/checkout/zagrosi-forge"},
            "zagrosi-forge@zagrosi",
            {"enabled": 1},
            "cache/zagrosi/zagrosi-forge/0.2.0",
        ),
        (
            "zagrosi",
            {"source_type": "local", "source": "/checkout/zagrosi-forge"},
            "zagrosi-forge@zagrosi",
            {"enabled": True, "extra": False},
            "cache/zagrosi/zagrosi-forge/0.2.0",
        ),
        (
            "zagrosi",
            {"source_type": "local", "source": "/checkout/zagrosi-forge"},
            "zagrosi-forge@zagrosi",
            {"enabled": True},
            "cache/zagrosi/zagrosi-forge/latest",
        ),
    ),
)
def test_legacy_matcher_requires_complete_exact_tables_and_builtin_types(
    marketplace_id: str,
    marketplace_table: dict[str, object],
    plugin_key: str,
    plugin_table: dict[str, object],
    cache: str,
) -> None:
    from zagrosi_forge.install.ownership import (
        load_legacy_install_catalog,
        match_legacy_install,
    )

    catalog = load_legacy_install_catalog().unwrap()
    matched = match_legacy_install(
        catalog,
        marketplace_id=marketplace_id,
        marketplace_table=marketplace_table,
        plugin_key=plugin_key,
        plugin_table=plugin_table,
        cache_relative=_reference(cache),
    )
    assert matched.is_ok
    assert matched.unwrap() is None


def test_legacy_catalog_and_recognition_are_sealed_and_projection_digest_bound() -> (
    None
):
    import pickle

    from zagrosi_forge.install.ownership import (
        LegacyInstallCatalog,
        load_legacy_install_catalog,
        match_legacy_install,
    )

    catalog = load_legacy_install_catalog().unwrap()
    with pytest.raises(TypeError, match="loaded only from installed resources"):
        LegacyInstallCatalog(
            marketplace_id=catalog.marketplace_id,
            source_type=catalog.source_type,
            source_leaf=catalog.source_leaf,
            plugin_key=catalog.plugin_key,
            cache_pattern=catalog.cache_pattern,
            catalog_digest=catalog.catalog_digest,
            _token=object(),
        )
    with pytest.raises(AttributeError):
        catalog.marketplace_id = "other"  # type: ignore[misc]
    with pytest.raises(TypeError, match="not serializable"):
        pickle.dumps(catalog)
    matched = match_legacy_install(
        catalog,
        marketplace_id="zagrosi",
        marketplace_table={
            "source_type": "local",
            "source": "/checkout/zagrosi-forge",
        },
        plugin_key="zagrosi-forge@zagrosi",
        plugin_table={"enabled": True},
        cache_relative=_reference("cache/zagrosi/zagrosi-forge/0.2.0"),
    ).unwrap()
    assert matched is not None
    assert len(matched.catalog_digest) == 64
    assert len(matched.projection_digest) == 64
    assert matched.base_version == "0.2.0"
    assert "/checkout" not in repr(matched)
    with pytest.raises(TypeError, match="not serializable"):
        pickle.dumps(matched)

    tampered = load_legacy_install_catalog().unwrap()
    object.__setattr__(tampered, "source_leaf", "other")
    rejected = match_legacy_install(
        tampered,
        marketplace_id="zagrosi",
        marketplace_table={
            "source_type": "local",
            "source": "/checkout/zagrosi-forge",
        },
        plugin_key="zagrosi-forge@zagrosi",
        plugin_table={"enabled": True},
        cache_relative=_reference("cache/zagrosi/zagrosi-forge/0.2.0"),
    )
    assert _code(rejected) == "ownership.receipt_invalid"


def _transaction(tmp_path: Path, *, transaction_id: str = "tx-one"):
    from zagrosi_forge.install.ownership import (
        create_transaction_path,
        prove_transaction_owned,
    )

    authority, owned, root = _owned(tmp_path)
    (root / "stages").mkdir(mode=0o700)
    relative = _reference("stages/candidate")
    claim = create_transaction_path(
        owned, relative, transaction_id=transaction_id
    ).unwrap()
    path = authority.prove_descendant(owned, relative, expected_depth=2).unwrap()
    proof = prove_transaction_owned(path, claim=claim).unwrap()
    return proof, path, owned, root


def _persistent_transaction(
    tmp_path: Path, *, transaction_id: str = "tx-0123456789abcdef0123456789abcdef"
):
    from zagrosi_forge.install.ownership import create_persistent_transaction_root

    authority, owned, root = _owned(tmp_path)
    created = create_persistent_transaction_root(
        owned, transaction_id=transaction_id
    ).unwrap()
    return authority, owned, root, created


def _recovery_prepared(binding: Any) -> Any:
    from zagrosi_forge.install.contracts import canonical_json_bytes
    from zagrosi_forge.install.journal import (
        JournalConfigIdentity,
        JournalPathIdentity,
        PreparedTransaction,
        RollbackAction,
        TransactionOwnedPath,
    )

    identity = _install_identity()
    before = JournalConfigIdentity(
        parent_identity=(11, 12),
        leaf_identity=(11, 13),
        byte_digest="0" * 64,
        semantic_digest="2" * 64,
        metadata_fingerprint="3" * 64,
        snapshot_digest="4" * 64,
        target_metadata_digest=None,
    )
    candidate = JournalConfigIdentity(
        parent_identity=(11, 12),
        leaf_identity=None,
        byte_digest="1" * 64,
        semantic_digest="6" * 64,
        metadata_fingerprint="3" * 64,
        snapshot_digest="4" * 64,
        target_metadata_digest="5" * 64,
    )
    receipt = _receipt(
        identity,
        relative=_source_relative(identity),
        manifest="8" * 64,
    )
    receipt["transaction"] = {
        "id": binding.transaction_id,
        "lineage": [binding.transaction_id],
    }
    receipt["config"] = {
        "path_id": "codex-config",
        "before_digest": before.byte_digest,
        "after_digest": candidate.byte_digest,
    }
    receipt["record_digest"] = hashlib.sha256(canonical_json_bytes(receipt)).hexdigest()
    root_relative = binding.root_relative
    rollback = RollbackAction(
        action="quarantine-if-owned",
        relative_path=root_relative,
        expected_identity=binding.transaction_identity,
    )
    return PreparedTransaction(
        transaction_id=binding.transaction_id,
        effective_marketplace_id="zagrosi",
        config_transaction_digest=hashlib.sha256(
            binding.transaction_id.encode("utf-8")
        ).hexdigest(),
        plan_digest="9" * 64,
        runner_provenance=_runner(),
        install_identity=identity,
        before_relation_digest="a" * 64,
        candidate_relation_digest="b" * 64,
        before_config=before,
        candidate_config=candidate,
        identities=(
            JournalPathIdentity(
                role="transaction-root",
                relative_path=root_relative,
                parent_identity=binding.store_identity,
                leaf_identity=binding.transaction_identity,
                content_digest=None,
            ),
            JournalPathIdentity(
                role="source-generation",
                relative_path=_source_relative(identity),
                parent_identity=(20, 23),
                leaf_identity=None,
                content_digest="8" * 64,
            ),
            JournalPathIdentity(
                role="cache-generation",
                relative_path=_cache_relative(identity),
                parent_identity=(20, 24),
                leaf_identity=None,
                content_digest="f" * 64,
            ),
        ),
        transaction_owned_paths=(
            TransactionOwnedPath(
                role="transaction-root",
                relative_path=root_relative,
                expected_identity=binding.transaction_identity,
            ),
        ),
        rollback_actions=(rollback,),
        prepared_receipt=receipt,
    )


def _recovery_cleanup_fixture(
    tmp_path: Path,
    *,
    transaction_id: str,
    terminal: bool = True,
) -> tuple[Any, Path, Any, Any]:
    import zagrosi_forge.install.ownership as ownership
    from zagrosi_forge.install.journal import (
        JournalRollbackEvent,
        JournalState,
        JournalStore,
        JournalTransition,
        load_pending,
    )

    tmp_path.mkdir(parents=True, exist_ok=True)
    authority, owned, root, created = _persistent_transaction(
        tmp_path,
        transaction_id=transaction_id,
    )
    live_proof = authority.prove_descendant(
        owned,
        created.claim.relative,
        expected_depth=3,
    ).unwrap()
    store = JournalStore(
        ownership.open_transaction_journal_access(owned, created).unwrap(),
        live_proof,
    )
    prepared = _recovery_prepared(created.binding)
    action = prepared.rollback_actions[0]
    head = store.create_prepared(prepared)
    head = store.append(
        head,
        JournalTransition(
            JournalState.ROLLBACK_ACTION_INTENT,
            rollback_event=JournalRollbackEvent(
                action_index=0,
                action_digest=action.action_digest,
            ),
        ),
    )
    store.close()
    live_proof.close()

    rebound = ownership.rebind_persistent_transaction(
        owned,
        binding=created.binding,
    ).unwrap()
    assert rebound.claim is not None
    path = authority.prove_descendant(
        owned,
        rebound.claim.relative,
        expected_depth=3,
    ).unwrap()
    cleanup = ownership.prove_transaction_owned(
        path,
        claim=rebound.claim,
    ).unwrap()
    ticket = ownership.quarantine_owned(
        cleanup,
        transaction_id=transaction_id,
    ).unwrap()
    rebound.close()
    path.close()

    quarantined = ownership.rebind_persistent_transaction(
        owned,
        binding=created.binding,
    ).unwrap()
    if terminal:
        access = ownership.open_quarantined_recovery_journal_access(
            owned,
            quarantined,
        ).unwrap()
        recovery_store = JournalStore.from_quarantined_recovery(access)
        head = recovery_store.append_recovery(
            head,
            JournalTransition(
                JournalState.ROLLBACK_ACTION_COMPLETED,
                rollback_event=JournalRollbackEvent(
                    action_index=0,
                    action_digest=action.action_digest,
                    outcome="quarantined",
                    observed_identity=created.binding.transaction_identity,
                    recovery_reference=created.binding.quarantine_relative,
                ),
            ),
        )
        recovery_store.append_recovery(
            head,
            JournalTransition(JournalState.ROLLED_BACK),
        )
        recovery_store.close()
    quarantined.close()
    ticket.close()

    observations = ownership.discover_pending_transactions(owned).unwrap()
    journals = load_pending(owned)
    assert len(observations) == len(journals) == 1
    return owned, root, observations[0], journals[0]


def _recovery_finalization_fixture(
    tmp_path: Path,
    *,
    committed_receipt: bool = False,
):
    import zagrosi_forge.install.ownership as ownership
    from zagrosi_forge.install.contracts import canonical_json_bytes
    from zagrosi_forge.install.journal import (
        JournalPathIdentity,
        JournalState,
        JournalStore,
        JournalTransition,
    )
    from tests.install.test_transaction_recovery import (
        _config_recovery_descriptor,
        _config_result,
        _prepared,
    )

    authority, owned, root, created = _persistent_transaction(tmp_path)
    identity = _install_identity()
    source_relative = _source_relative(identity)
    cache_relative = _cache_relative(identity)
    source = root / source_relative
    cache = root / cache_relative
    source.mkdir(parents=True, mode=0o700)
    cache.mkdir(parents=True, mode=0o700)
    source_manifest = root / _manifest_relative(identity, "source")
    cache_manifest = root / _manifest_relative(identity, "cache")
    source_manifest.parent.mkdir(parents=True)
    cache_manifest.parent.mkdir(parents=True)
    source_manifest.write_bytes(b"recovery-source-manifest\n")
    cache_manifest.write_bytes(b"recovery-cache-manifest\n")
    source_digest = hashlib.sha256(source_manifest.read_bytes()).hexdigest()
    cache_digest = hashlib.sha256(cache_manifest.read_bytes()).hexdigest()

    prepared = _prepared(created.binding)
    receipt = json.loads(canonical_json_bytes(prepared.prepared_receipt))
    receipt["source"] = {
        "relative_path": source_relative,
        "manifest_digest": source_digest,
    }
    receipt["cache"] = {
        "relative_path": cache_relative,
        "manifest_digest": cache_digest,
    }
    receipt.pop("record_digest", None)
    receipt["record_digest"] = hashlib.sha256(canonical_json_bytes(receipt)).hexdigest()
    source_initial = replace(
        next(item for item in prepared.identities if item.role == "source-generation"),
        relative_path=source_relative,
        parent_identity=_identity(source.parent),
        leaf_identity=None,
        content_digest=source_digest,
    )
    cache_initial = replace(
        next(item for item in prepared.identities if item.role == "cache-generation"),
        relative_path=cache_relative,
        parent_identity=_identity(cache.parent),
        leaf_identity=None,
        content_digest=cache_digest,
    )
    transaction_root = next(
        item for item in prepared.identities if item.role == "transaction-root"
    )
    prepared = replace(
        prepared,
        identities=(transaction_root, source_initial, cache_initial),
        prepared_receipt=receipt,
    )

    live_proof = authority.prove_descendant(
        owned,
        created.claim.relative,
        expected_depth=3,
    ).unwrap()
    store = JournalStore(
        ownership.open_transaction_journal_access(owned, created).unwrap(),
        live_proof,
    )
    descriptor = _config_recovery_descriptor(prepared.transaction_id)
    head = store.create_prepared(prepared)
    for transition in (
        JournalTransition(JournalState.STAGED, config_recovery=descriptor),
        JournalTransition(
            JournalState.VERIFIED,
            verification_evidence_digest="f" * 64,
        ),
        JournalTransition(
            JournalState.SOURCE_PUBLISHED,
            source_result=replace(
                source_initial,
                leaf_identity=_identity(source),
            ),
        ),
        JournalTransition(
            JournalState.CACHE_PUBLISHED,
            cache_result=replace(
                cache_initial,
                leaf_identity=_identity(cache),
            ),
        ),
        JournalTransition(JournalState.PUBLISHED),
        JournalTransition(JournalState.COMMIT_INTENT),
    ):
        head = store.append(head, transition)
    if committed_receipt:
        head = store.append(
            head,
            JournalTransition(
                JournalState.CONFIG_COMMITTED,
                config_result=_config_result(prepared, descriptor),
            ),
        )
        prepared_raw = canonical_json_bytes(
            prepared.prepared_receipt,
            final_newline=True,
        )
        ownership.publish_committed_receipt(
            owned,
            raw=prepared_raw,
        ).unwrap()
        receipt_reference = ownership.committed_receipt_reference(
            prepared.effective_marketplace_id,
            prepared.install_identity,
        )
        receipt_path = root / receipt_reference.value
        head = store.append(
            head,
            JournalTransition(
                JournalState.RECEIPT_COMMITTED,
                receipt_result=JournalPathIdentity(
                    role="committed-receipt",
                    relative_path=receipt_reference.value,
                    parent_identity=_identity(receipt_path.parent),
                    leaf_identity=_identity(receipt_path),
                    content_digest=hashlib.sha256(prepared_raw).hexdigest(),
                ),
            ),
        )
    observation = ownership.discover_pending_transactions(owned).unwrap()[0]
    return (
        ownership,
        owned,
        root,
        store,
        live_proof,
        observation,
        store.load(),
    )


def test_recovery_finalization_observation_binds_live_generations(
    tmp_path: Path,
) -> None:
    import pickle

    ownership, owned, _root, store, proof, observation, journal = (
        _recovery_finalization_fixture(tmp_path)
    )
    try:
        result = ownership.observe_recovery_finalization(
            owned,
            observation=observation,
            journal=journal,
        )

        observed = result.unwrap()
        assert observed.transaction_id == journal.records[-1].transaction_id
        assert observed.journal_state == "COMMIT_INTENT"
        assert observed.receipt_status == "absent"
        assert len(observed.observation_digest) == 64
        with pytest.raises(AttributeError):
            observed.receipt_status = "matching"  # type: ignore[misc]
        with pytest.raises(TypeError, match="not serializable"):
            pickle.dumps(observed)
        object.__setattr__(observed, "receipt_status", "matching")
        with pytest.raises(TypeError, match="observation changed"):
            observed._require_valid()
        object.__setattr__(observed, "receipt_status", "absent")
        observed._require_valid()
    finally:
        store.close()
        proof.close()
        owned.close()


def test_recovery_finalization_observation_accepts_exact_committed_receipt(
    tmp_path: Path,
) -> None:
    ownership, owned, _root, store, proof, observation, journal = (
        _recovery_finalization_fixture(
            tmp_path,
            committed_receipt=True,
        )
    )
    try:
        observed = ownership.observe_recovery_finalization(
            owned,
            observation=observation,
            journal=journal,
        ).unwrap()

        assert observed.transaction_id == journal.records[-1].transaction_id
        assert observed.journal_state == "RECEIPT_COMMITTED"
        assert observed.receipt_status == "matching"
    finally:
        store.close()
        proof.close()
        owned.close()


def test_recovery_finalization_rejects_receipt_ancestry_appearing_between_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ownership, owned, root, store, proof, observation, journal = (
        _recovery_finalization_fixture(tmp_path)
    )
    original = ownership._open_recovery_receipt
    calls = 0

    def create_private_receipt_ancestry(*args: Any, **kwargs: Any):
        nonlocal calls
        opened = original(*args, **kwargs)
        calls += 1
        if calls == 1:
            parent = root / ".zagrosi"
            for component in (
                "ownership",
                "zagrosi",
                "zagrosi-forge",
            ):
                parent = _private_test_directory(parent / component)
        return opened

    try:
        monkeypatch.setattr(
            ownership,
            "_open_recovery_receipt",
            create_private_receipt_ancestry,
        )

        result = ownership.observe_recovery_finalization(
            owned,
            observation=observation,
            journal=journal,
        )

        assert _code(result) == "ownership.identity_mismatch"
    finally:
        store.close()
        proof.close()
        owned.close()


def test_recovery_finalization_rejects_receipt_appearing_after_second_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from zagrosi_forge.install.contracts import canonical_json_bytes

    ownership, owned, _root, store, proof, observation, journal = (
        _recovery_finalization_fixture(tmp_path)
    )
    original = ownership._recovery_generation_is_current
    published = False
    prepared_raw = canonical_json_bytes(
        journal.records[-1].prepared.prepared_receipt,
        final_newline=True,
    )

    def publish_receipt(*args: Any, **kwargs: Any) -> bool:
        nonlocal published
        if not published:
            published = True
            ownership.publish_committed_receipt(
                owned,
                raw=prepared_raw,
            ).unwrap()
        return original(*args, **kwargs)

    try:
        monkeypatch.setattr(
            ownership,
            "_recovery_generation_is_current",
            publish_receipt,
        )

        result = ownership.observe_recovery_finalization(
            owned,
            observation=observation,
            journal=journal,
        )

        assert _code(result) == "ownership.identity_mismatch"
    finally:
        store.close()
        proof.close()
        owned.close()


@pytest.mark.skipif(os.name != "posix", reason="POSIX namespace replacement")
def test_recovery_finalization_rejects_manifest_replaced_after_second_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ownership, owned, root, store, proof, observation, journal = (
        _recovery_finalization_fixture(tmp_path)
    )
    original = ownership._open_recovery_receipt
    calls = 0
    manifest = root / _manifest_relative(_install_identity(), "source")

    def replace_manifest(*args: Any, **kwargs: Any):
        nonlocal calls
        opened = original(*args, **kwargs)
        calls += 1
        if calls == 2:
            original_parent = manifest.parent
            retained_parent = original_parent.with_name("retained-.codex-plugin")
            raw = manifest.read_bytes()
            original_parent.replace(retained_parent)
            original_parent.mkdir()
            manifest.write_bytes(raw)
        return opened

    try:
        monkeypatch.setattr(
            ownership,
            "_open_recovery_receipt",
            replace_manifest,
        )

        result = ownership.observe_recovery_finalization(
            owned,
            observation=observation,
            journal=journal,
        )

        assert _code(result) == "ownership.identity_mismatch"
    finally:
        store.close()
        proof.close()
        owned.close()


@pytest.mark.skipif(os.name != "posix", reason="POSIX private-mode assertion")
def test_recovery_finalization_rejects_untrusted_missing_receipt_parent(
    tmp_path: Path,
) -> None:
    ownership, owned, root, store, proof, observation, journal = (
        _recovery_finalization_fixture(tmp_path)
    )
    untrusted = root / ".zagrosi" / "ownership"
    untrusted.mkdir(mode=0o755)
    try:
        result = ownership.observe_recovery_finalization(
            owned,
            observation=observation,
            journal=journal,
        )

        assert _code(result) == "ownership.identity_mismatch"
    finally:
        store.close()
        proof.close()
        owned.close()


@pytest.mark.skipif(os.name != "posix", reason="POSIX namespace replacement")
def test_recovery_finalization_rejects_matching_receipt_parent_rebind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ownership, owned, root, store, proof, observation, journal = (
        _recovery_finalization_fixture(
            tmp_path,
            committed_receipt=True,
        )
    )
    identity = _install_identity()
    reference = ownership.committed_receipt_reference("zagrosi", identity)
    receipt = root / reference.value
    original = ownership._recovery_generation_is_current
    swapped = False

    def replace_receipt_parent(*args: Any, **kwargs: Any) -> bool:
        nonlocal swapped
        if not swapped:
            swapped = True
            original_parent = receipt.parent
            retained_parent = original_parent.with_name(
                f"retained-{original_parent.name}"
            )
            raw = receipt.read_bytes()
            original_parent.replace(retained_parent)
            original_parent.mkdir(mode=0o700)
            receipt.write_bytes(raw)
            receipt.chmod(0o600)
        return original(*args, **kwargs)

    try:
        monkeypatch.setattr(
            ownership,
            "_recovery_generation_is_current",
            replace_receipt_parent,
        )

        result = ownership.observe_recovery_finalization(
            owned,
            observation=observation,
            journal=journal,
        )

        assert _code(result) == "ownership.identity_mismatch"
    finally:
        store.close()
        proof.close()
        owned.close()


def test_open_recovery_generation_close_is_idempotent_after_native_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import zagrosi_forge.install.ownership as ownership

    class _Manifest:
        def __init__(self) -> None:
            self.close_calls = 0

        def close(self) -> None:
            self.close_calls += 1

    manifest = _Manifest()
    opened = ownership._OpenRecoveryGeneration(
        capture=ownership._RecoveryGenerationCapture(
            relative_path="cache/zagrosi/zagrosi-forge/1.0.0",
            parent_identity=(1, 2),
            leaf_identity=(1, 3),
            manifest_relative=(
                "cache/zagrosi/zagrosi-forge/1.0.0/.codex-plugin/bundle-manifest.json"
            ),
            manifest_parent_identity=(1, 4),
            manifest_identity=(1, 5),
            manifest_digest="a" * 64,
        ),
        parent=101,
        leaf=102,
        manifest=manifest,  # type: ignore[arg-type]
    )
    closed: list[int] = []

    def fail_first_close(descriptor: int) -> None:
        closed.append(descriptor)
        if descriptor == 102:
            raise OSError("injected close failure")

    monkeypatch.setattr(ownership, "_close_native", fail_first_close)

    with pytest.raises(OSError, match="injected close failure"):
        opened.close()

    assert closed == [102, 101]
    assert opened.leaf == (0 if os.name == "nt" else -1)
    assert opened.parent == (0 if os.name == "nt" else -1)
    opened.close()
    assert closed == [102, 101]
    assert manifest.close_calls == 1


def test_recovery_generation_failure_attempts_every_resource_close(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ownership, owned, _root, store, proof, _observation, journal = (
        _recovery_finalization_fixture(tmp_path)
    )
    source = next(
        item
        for item in journal.records[-1].identities
        if item.role == "source-generation"
    )
    prepared = journal.records[-1].prepared
    assert prepared is not None
    source_root = ownership._recovery_source_root(owned)
    native_closes: list[int] = []
    manifest_closes = 0
    original_native_close = ownership._close_native
    manifest_type = (
        ownership._paths._WindowsOpenedRegularFile
        if os.name == "nt"
        else ownership.OpenedRegularFile
    )
    original_manifest_close = manifest_type.close

    def close_native(descriptor: int) -> None:
        native_closes.append(descriptor)
        original_native_close(descriptor)

    def close_manifest_then_fail(opened: Any) -> None:
        nonlocal manifest_closes
        manifest_closes += 1
        original_manifest_close(opened)
        raise OSError("injected manifest close failure")

    try:
        with monkeypatch.context() as context:
            context.setattr(ownership, "_close_native", close_native)
            context.setattr(
                ownership,
                "_recovery_generation_capture_invariants",
                lambda _capture: False,
            )
            context.setattr(
                manifest_type,
                "close",
                close_manifest_then_fail,
            )

            with pytest.raises(OSError, match="injected manifest close failure"):
                ownership._open_recovery_generation(
                    owned,
                    source_root,
                    relative_path=source.relative_path,
                    expected_parent_identity=source.parent_identity,
                    expected_leaf_identity=source.leaf_identity,
                    expected_manifest_digest=source.content_digest,
                    manifest_relative=(
                        f"{source.relative_path}/plugins/"
                        f"{prepared.install_identity.plugin_id}/"
                        ".codex-plugin/bundle-manifest.json"
                    ),
                )

        assert manifest_closes == 1
        assert len(native_closes) == len(source.relative_path.split("/")) - 1 + 3
    finally:
        source_root.close()
        store.close()
        proof.close()
        owned.close()


def test_recovery_receipt_absence_transfer_closes_each_handle_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import zagrosi_forge.install.ownership as ownership

    _, owned, root = _owned(tmp_path)
    assert (root / ".zagrosi").is_dir()
    _private_test_directory(root / ".zagrosi" / "ownership")
    reference = ownership.committed_receipt_reference(
        "zagrosi",
        _install_identity(),
    )
    snapshot = ownership._snapshot_safe_reference(reference)
    assert snapshot is not None
    original_close = ownership._close_native
    closes: list[int] = []

    def close_then_fail_once(descriptor: int) -> None:
        closes.append(descriptor)
        original_close(descriptor)
        if len(closes) == 1:
            raise OSError("injected ancestry close failure")

    try:
        with monkeypatch.context() as context:
            context.setattr(
                ownership,
                "_close_native",
                close_then_fail_once,
            )
            with pytest.raises(OSError, match="injected ancestry close failure"):
                ownership._open_recovery_receipt_absence(
                    owned,
                    snapshot,
                )

        assert len(closes) == 2
        assert len(set(closes)) == 2
    finally:
        owned.close()


def test_recovery_receipt_absence_probe_attempts_every_handle_close(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import zagrosi_forge.install.ownership as ownership

    _, owned, _root = _owned(tmp_path)
    reference = ownership.committed_receipt_reference(
        "zagrosi",
        _install_identity(),
    )
    snapshot = ownership._snapshot_safe_reference(reference)
    assert snapshot is not None
    opened = ownership._open_recovery_receipt_absence(owned, snapshot)
    original_close = ownership._close_native
    closes: list[int] = []

    def close_then_fail_once(descriptor: int) -> None:
        closes.append(descriptor)
        original_close(descriptor)
        if len(closes) == 1:
            raise OSError("injected absence probe close failure")

    try:
        with monkeypatch.context() as context:
            context.setattr(
                ownership,
                "_close_native",
                close_then_fail_once,
            )
            with pytest.raises(OSError, match="injected absence probe close failure"):
                ownership._recovery_receipt_absence_is_current(
                    owned,
                    opened,
                    snapshot,
                )

        assert len(closes) == 2
        assert len(set(closes)) == 2
    finally:
        opened.close()
        owned.close()


def test_recovery_receipt_absence_existing_depth_releases_transfer_handles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import zagrosi_forge.install.ownership as ownership

    _, owned, root = _owned(tmp_path)
    _private_test_directory(root / ".zagrosi" / "ownership")
    reference = ownership.committed_receipt_reference(
        "zagrosi",
        _install_identity(),
    )
    snapshot = ownership._snapshot_safe_reference(reference)
    assert snapshot is not None
    opened = ownership._open_recovery_receipt_absence(owned, snapshot)
    assert opened.existing_depth == 1
    original_close = ownership._close_native
    closes: list[int] = []

    def close_then_fail_once(descriptor: int) -> None:
        closes.append(descriptor)
        original_close(descriptor)
        if len(closes) == 1:
            raise OSError("injected private-chain transfer close failure")

    try:
        with monkeypatch.context() as context:
            context.setattr(
                ownership,
                "_close_native",
                close_then_fail_once,
            )
            assert not ownership._recovery_receipt_absence_is_current(
                owned,
                opened,
                snapshot,
            )

        assert len(closes) == 3
        assert len(set(closes)) == 3
    finally:
        opened.close()
        owned.close()


@pytest.mark.skipif(os.name != "posix", reason="POSIX descriptor transfer")
def test_open_parent_close_failure_releases_untransferred_child_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import zagrosi_forge.install.ownership as ownership

    (tmp_path / "child").mkdir()
    root = os.open(tmp_path, ownership._directory_flags())
    original_close = ownership._close_native
    closes: list[int] = []

    def close_then_fail_once(descriptor: int) -> None:
        closes.append(descriptor)
        original_close(descriptor)
        if len(closes) == 1:
            raise OSError("injected parent transfer close failure")

    try:
        with monkeypatch.context() as context:
            context.setattr(
                ownership,
                "_close_native",
                close_then_fail_once,
            )
            with pytest.raises(
                OSError,
                match="injected parent transfer close failure",
            ):
                ownership._open_parent(root, ("child",))

        assert len(closes) == 2
        assert len(set(closes)) == 2
    finally:
        os.close(root)


def test_transaction_store_namespace_failure_closes_each_native_handle_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import zagrosi_forge.install.ownership as ownership

    _, owned, _ = _owned(tmp_path)
    watched: set[int] = set()
    close_counts: dict[int, int] = {}
    original_store_close = ownership._TransactionStore.close

    def capture_store_handles(store: Any) -> None:
        empty = 0 if store.windows else -1
        watched.update(
            descriptor
            for descriptor in (store.control, store.store, store.claims)
            if descriptor != empty
        )
        original_store_close(store)

    with monkeypatch.context() as context:
        context.setattr(
            ownership,
            "_transaction_store_namespace_is_valid",
            lambda _store: False,
        )
        context.setattr(
            ownership._TransactionStore,
            "close",
            capture_store_handles,
        )
        if os.name == "nt":
            original_native_close = ownership._paths._windows_close

            def close_once(descriptor: int) -> None:
                if descriptor in watched:
                    close_counts[descriptor] = close_counts.get(descriptor, 0) + 1
                    assert close_counts[descriptor] == 1
                original_native_close(descriptor)

            context.setattr(ownership._paths, "_windows_close", close_once)
        else:
            original_native_close = ownership.os.close

            def close_once(descriptor: int) -> None:
                if descriptor in watched:
                    close_counts[descriptor] = close_counts.get(descriptor, 0) + 1
                    assert close_counts[descriptor] == 1
                original_native_close(descriptor)

            context.setattr(ownership.os, "close", close_once)

        result = ownership.create_persistent_transaction_root(
            owned,
            transaction_id="tx-c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0",
        )

    assert _code(result) == "ownership.unowned"
    owned.close()


def test_windows_publication_adapters_order_namespace_before_barrier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import zagrosi_forge.install.ownership as ownership

    events: list[tuple[str, object]] = []

    monkeypatch.setattr(
        ownership._paths,
        "_windows_rename_handle",
        lambda source, parent, destination: events.append(
            ("rename", (source, parent, destination))
        ),
    )
    monkeypatch.setattr(
        ownership,
        "_windows_flush",
        lambda handle: events.append(("flush-file", handle)),
    )
    monkeypatch.setattr(
        ownership,
        "_windows_flush_directory_binding",
        lambda parent, component, identity: events.append(
            ("flush-directory", (parent, component, identity))
        ),
    )

    published = False

    def mark_published() -> None:
        nonlocal published
        published = True
        events.append(("published", True))

    ownership._durable_windows_file_rename(
        11,
        12,
        "claim.json",
        after_rename=mark_published,
    )
    ownership._durable_windows_directory_rename(
        21,
        22,
        "transactions",
        (23, 24),
    )

    assert published
    assert events == [
        ("rename", (11, 12, "claim.json")),
        ("published", True),
        ("flush-file", 11),
        ("rename", (21, 22, "transactions")),
        ("flush-directory", (22, "transactions", (23, 24))),
    ]


def test_windows_record_retirement_opens_write_capable_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import zagrosi_forge.install.ownership as ownership

    class Status:
        identity = (17, 19)
        is_directory = False
        is_reparse = False
        link_count = 1

    store = ownership._TransactionStore(
        control=11,
        store=12,
        claims=13,
        plugins_identity=(17, 18),
        control_identity=(17, 18),
        store_identity=(17, 18),
        claims_identity=(17, 18),
        windows=True,
    )
    bindings = iter((True, False, True))
    opened: list[dict[str, object]] = []

    monkeypatch.setattr(
        ownership,
        "_private_record_name_binds",
        lambda *_args, **_kwargs: next(bindings),
    )
    monkeypatch.setattr(
        ownership,
        "_transaction_name_exists",
        lambda *_args, **_kwargs: False,
    )

    def open_child(
        _parent: int,
        _component: str,
        **kwargs: object,
    ) -> int:
        opened.append(kwargs)
        return 23

    monkeypatch.setattr(ownership._paths, "_windows_open_child", open_child)
    monkeypatch.setattr(
        ownership._paths,
        "_windows_handle_status",
        lambda _descriptor: Status(),
    )
    monkeypatch.setattr(
        ownership._paths,
        "_windows_private_authorization",
        lambda _descriptor, *, exact: exact,
    )
    monkeypatch.setattr(
        ownership,
        "_durable_windows_file_rename",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        ownership,
        "_windows_flush_directory_binding",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(ownership._paths, "_windows_close", lambda _descriptor: None)

    ownership._remove_exact_transaction_record(
        store,
        ".intent.json",
        (17, 19),
        b"canonical-intent",
    )

    assert opened == [
        {
            "directory": False,
            "read_data": True,
            "write_data": True,
            "delete_access": True,
        }
    ]


def test_windows_directory_flush_converts_failing_ntstatus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import zagrosi_forge.install.ownership as ownership

    class FakeFunction:
        def __init__(self, result: int) -> None:
            self.result = result
            self.calls: list[tuple[object, ...]] = []
            self.argtypes: object = None
            self.restype: object = None

        def __call__(self, *args: object) -> int:
            self.calls.append(args)
            return self.result

    class FakeNtdll:
        NtFlushBuffersFileEx = FakeFunction(-1073741790)
        RtlNtStatusToDosError = FakeFunction(5)

    ntdll = FakeNtdll()
    monkeypatch.setattr(ownership._paths, "_windows_dll", lambda name: ntdll)
    monkeypatch.setattr(
        ownership._paths,
        "_windows_error",
        lambda number: OSError(number, "Windows directory flush failed"),
    )

    with pytest.raises(OSError, match="Windows directory flush failed"):
        ownership._windows_flush_directory(17)

    assert ntdll.NtFlushBuffersFileEx.calls
    assert ntdll.NtFlushBuffersFileEx.calls[0][:4] == (17, 0, None, 0)
    assert ntdll.RtlNtStatusToDosError.calls == [(-1073741790,)]


def test_windows_receipt_parent_requests_write_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import zagrosi_forge.install.ownership as ownership

    class Status:
        identity = (17, 18)

    calls: list[tuple[str, dict[str, object]]] = []
    descriptors = iter((31, 32))

    def open_child(
        _parent: int,
        component: str,
        **kwargs: object,
    ) -> int:
        calls.append((component, kwargs))
        return next(descriptors)

    monkeypatch.setattr(ownership._paths, "_windows_duplicate", lambda _root: 30)
    monkeypatch.setattr(ownership._paths, "_windows_open_child", open_child)
    monkeypatch.setattr(
        ownership._paths,
        "_windows_handle_status",
        lambda _descriptor: Status(),
    )
    monkeypatch.setattr(
        ownership._paths,
        "_windows_private_directory",
        lambda _descriptor, *, exact: exact,
    )
    monkeypatch.setattr(ownership, "_close_native", lambda _descriptor: None)

    handle = ownership._windows_open_private_directory_chain(
        29,
        ("ownership", "zagrosi-forge"),
        volume=17,
        create_missing=False,
        write_final=True,
    )

    assert handle == 32
    assert calls == [
        ("ownership", {"directory": True, "write_data": False}),
        ("zagrosi-forge", {"directory": True, "write_data": True}),
    ]


def test_windows_claim_publication_revalidation_is_tristate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import zagrosi_forge.install.ownership as ownership

    class Store:
        claims = 41
        plugins_identity = (42, 43)

    store = Store()
    raw = b"canonical-claim"
    identity = (42, 44)
    monkeypatch.setattr(
        ownership,
        "_transaction_store_namespace_is_valid",
        lambda _store: True,
    )
    monkeypatch.setattr(
        ownership,
        "_private_record_name_binds",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        ownership,
        "_read_windows_private_record",
        lambda *_args, **_kwargs: (raw, identity),
    )
    assert (
        ownership._revalidate_windows_claim_publication(
            store,
            final_claim="tx.json",
            claim_identity=identity,
            raw=raw,
        )
        is True
    )

    def missing(*_args: object, **_kwargs: object) -> None:
        raise FileNotFoundError

    monkeypatch.setattr(ownership, "_read_windows_private_record", missing)
    assert (
        ownership._revalidate_windows_claim_publication(
            store,
            final_claim="tx.json",
            claim_identity=identity,
            raw=raw,
        )
        is False
    )

    def ambiguous(*_args: object, **_kwargs: object) -> None:
        raise OSError(errno.EPERM, "injected ambiguity")

    monkeypatch.setattr(ownership, "_read_windows_private_record", ambiguous)
    assert (
        ownership._revalidate_windows_claim_publication(
            store,
            final_claim="tx.json",
            claim_identity=identity,
            raw=raw,
        )
        is None
    )


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-death durability")
@pytest.mark.parametrize(
    "barrier",
    (
        "root-durable",
        "wal-durable",
        "temp-anchor-durable",
        "anchor-published",
    ),
)
def test_persistent_creation_recovers_after_process_death_at_durable_barrier(
    tmp_path: Path, barrier: str
) -> None:
    import zagrosi_forge.install.ownership as ownership
    from zagrosi_forge.install.paths import PlatformPathAuthority

    transaction_id = (
        "tx-c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1"
        if barrier == "root-durable"
        else (
            "tx-cbcbcbcbcbcbcbcbcbcbcbcbcbcbcbcb"
            if barrier == "temp-anchor-durable"
            else (
                "tx-c9c9c9c9c9c9c9c9c9c9c9c9c9c9c9c9"
                if barrier == "wal-durable"
                else "tx-c2c2c2c2c2c2c2c2c2c2c2c2c2c2c2c2"
            )
        )
    )
    home = tmp_path / "codex-home"
    _private_test_directory(home)
    child = os.fork()
    if child == 0:
        try:
            original_fsync = ownership.os.fsync
            live = home / "plugins/.zagrosi/transactions" / transaction_id
            claims = home / "plugins/.zagrosi/transactions/claims"
            anchor = claims / f"{transaction_id}.json"
            creation_intent = claims / f".{transaction_id}.create.json"
            store = claims.parent

            def terminate_after_real_fsync(descriptor: int) -> None:
                original_fsync(descriptor)
                descriptor_identity = ownership._identity(descriptor)
                temporary_anchors = tuple(claims.glob(".claim-*.tmp"))
                if (
                    (
                        barrier == "root-durable"
                        and live.is_dir()
                        and not anchor.exists()
                        and descriptor_identity == _identity(store)
                    )
                    or (
                        barrier == "wal-durable"
                        and creation_intent.is_file()
                        and temporary_anchors
                        and all(
                            candidate.stat().st_size == 0
                            for candidate in temporary_anchors
                        )
                        and not live.exists()
                        and not anchor.exists()
                        and descriptor_identity == _identity(claims)
                    )
                    or (
                        barrier == "temp-anchor-durable"
                        and any(
                            descriptor_identity == _identity(candidate)
                            for candidate in temporary_anchors
                        )
                        and not anchor.exists()
                    )
                    or (
                        barrier == "anchor-published"
                        and anchor.is_file()
                        and descriptor_identity == _identity(claims)
                    )
                ):
                    os.kill(os.getpid(), signal.SIGKILL)

            ownership.os.fsync = terminate_after_real_fsync
            owned = (
                PlatformPathAuthority()
                .bootstrap_forge_root(home, runner=_runner())
                .unwrap()
            )
            ownership.create_persistent_transaction_root(
                owned, transaction_id=transaction_id
            )
        finally:
            os._exit(91)

    _waited, status = os.waitpid(child, 0)
    assert os.WIFSIGNALED(status)
    assert os.WTERMSIG(status) == signal.SIGKILL

    owned = (
        PlatformPathAuthority().bootstrap_forge_root(home, runner=_runner()).unwrap()
    )
    try:
        recovered = ownership.create_persistent_transaction_root(
            owned, transaction_id=transaction_id
        ).unwrap()
        loaded = ownership.load_persistent_transaction_binding(
            owned, transaction_id=transaction_id
        ).unwrap()
        rebound = ownership.rebind_persistent_transaction(
            owned, binding=loaded
        ).unwrap()
        assert recovered.binding == loaded
        assert rebound.location is ownership.TransactionLocation.LIVE
        claims = home / "plugins/.zagrosi/transactions/claims"
        assert not tuple(claims.glob(f".{transaction_id}.*"))
        assert not tuple(claims.glob(".record-*.tmp"))
        assert not tuple(claims.glob(".claim-*.tmp"))
    finally:
        owned.close()


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-death durability")
@pytest.mark.parametrize(
    "barrier",
    (
        "staged-root-parent-durable",
        "pending-claim-created",
    ),
)
def test_persistent_creation_prebinding_crash_preserves_unbound_reserved_names(
    tmp_path: Path,
    barrier: str,
) -> None:
    import zagrosi_forge.install.ownership as ownership
    from zagrosi_forge.install.paths import PlatformPathAuthority

    transaction_id = (
        "tx-d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1"
        if barrier == "staged-root-parent-durable"
        else "tx-d2d2d2d2d2d2d2d2d2d2d2d2d2d2d2d2"
    )
    home = tmp_path / "codex-home"
    _private_test_directory(home)
    store = home / "plugins/.zagrosi/transactions"
    claims = store / "claims"
    reservation = claims / f".{transaction_id}.reserve.json"
    successor = claims / f".{transaction_id}.create.json"

    child = os.fork()
    if child == 0:
        try:
            original_fsync = ownership.os.fsync
            original_open = ownership.os.open

            def terminate_after_root_parent_fsync(descriptor: int) -> None:
                original_fsync(descriptor)
                if (
                    barrier == "staged-root-parent-durable"
                    and reservation.is_file()
                    and not successor.exists()
                    and tuple(store.glob(".root-*.tmp"))
                    and not tuple(claims.glob(".claim-*.tmp"))
                    and ownership._identity(descriptor) == _identity(store)
                ):
                    os.kill(os.getpid(), signal.SIGKILL)

            def terminate_after_pending_claim_create(
                component: str | bytes,
                *args: Any,
                **kwargs: Any,
            ) -> int:
                descriptor = original_open(component, *args, **kwargs)
                if (
                    barrier == "pending-claim-created"
                    and isinstance(component, str)
                    and component.startswith(".claim-")
                    and component.endswith(".tmp")
                    and reservation.is_file()
                    and not successor.exists()
                ):
                    os.kill(os.getpid(), signal.SIGKILL)
                return descriptor

            ownership.os.fsync = terminate_after_root_parent_fsync
            ownership.os.open = terminate_after_pending_claim_create
            owned = (
                PlatformPathAuthority()
                .bootstrap_forge_root(home, runner=_runner())
                .unwrap()
            )
            ownership.create_persistent_transaction_root(
                owned,
                transaction_id=transaction_id,
            )
        finally:
            os._exit(91)

    _waited, status = os.waitpid(child, 0)
    assert os.WIFSIGNALED(status)
    assert os.WTERMSIG(status) == signal.SIGKILL
    reserved = json.loads(reservation.read_bytes())
    staged = store / reserved["stage_component"]
    pending = claims / reserved["pending_claim_component"]
    assert staged.is_dir()
    assert pending.exists() is (barrier == "pending-claim-created")
    assert not successor.exists()

    displaced = staged.with_name(f"{staged.name}-displaced")
    if barrier == "staged-root-parent-durable":
        staged.rename(displaced)
        _private_test_directory(staged)
        (staged / "replacement-canary").write_bytes(b"preserve-replacement")
        (displaced / "original-canary").write_bytes(b"preserve-original")
    else:
        (staged / "unbound-canary").write_bytes(b"preserve-stage")
        pending.write_bytes(b"preserve-unbound-claim")
        pending.chmod(0o600)

    owned = (
        PlatformPathAuthority().bootstrap_forge_root(home, runner=_runner()).unwrap()
    )
    try:
        for _attempt in range(2):
            retried = ownership.create_persistent_transaction_root(
                owned,
                transaction_id=transaction_id,
            )
            assert _code(retried) == "ownership.unowned"
        assert tuple(store.glob(".root-*.tmp")) == (staged,)
        assert tuple(claims.glob(".claim-*.tmp")) == (
            (pending,) if pending.exists() else ()
        )
        if barrier == "staged-root-parent-durable":
            assert (
                staged / "replacement-canary"
            ).read_bytes() == b"preserve-replacement"
            assert (displaced / "original-canary").read_bytes() == b"preserve-original"
        else:
            assert (staged / "unbound-canary").read_bytes() == b"preserve-stage"
            assert pending.read_bytes() == b"preserve-unbound-claim"
    finally:
        owned.close()


def test_persistent_creation_retries_when_only_durable_reservation_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import zagrosi_forge.install.ownership as ownership

    class ReservationOnlyInterrupt(BaseException):
        pass

    transaction_id = "tx-d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4"
    _, owned, root = _owned(tmp_path)
    original_publish = ownership._publish_transaction_creation_reservation

    def publish_then_interrupt(*args: Any, **kwargs: Any) -> Any:
        original_publish(*args, **kwargs)
        raise ReservationOnlyInterrupt

    monkeypatch.setattr(
        ownership,
        "_publish_transaction_creation_reservation",
        publish_then_interrupt,
    )
    with pytest.raises(ReservationOnlyInterrupt):
        ownership.create_persistent_transaction_root(
            owned,
            transaction_id=transaction_id,
        )
    store = root / ".zagrosi/transactions"
    claims = store / "claims"
    assert (claims / f".{transaction_id}.reserve.json").is_file()
    assert not tuple(store.glob(".root-*.tmp"))
    assert not tuple(claims.glob(".claim-*.tmp"))
    monkeypatch.undo()

    created = ownership.create_persistent_transaction_root(
        owned,
        transaction_id=transaction_id,
    ).unwrap()

    assert (root / created.binding.root_relative).is_dir()
    assert (root / created.binding.claim_relative).is_file()
    assert not tuple(store.glob(".root-*.tmp"))
    assert not tuple(claims.glob(".claim-*.tmp"))
    owned.close()


@pytest.mark.skipif(os.name == "nt", reason="POSIX publication interruption")
def test_posix_post_rename_interrupt_preserves_exact_root_and_anchor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import zagrosi_forge.install.ownership as ownership

    class InjectedPublicationInterrupt(BaseException):
        pass

    transaction_id = "tx-d0d0d0d0d0d0d0d0d0d0d0d0d0d0d0d0"
    _, owned, root = _owned(tmp_path)
    original_rename = ownership._exclusive_rename

    def publish_then_interrupt(parent: int, source: str, destination: str) -> None:
        original_rename(parent, source, destination)
        raise InjectedPublicationInterrupt

    monkeypatch.setattr(ownership, "_exclusive_rename", publish_then_interrupt)
    with pytest.raises(InjectedPublicationInterrupt):
        ownership.create_persistent_transaction_root(
            owned,
            transaction_id=transaction_id,
        )

    live = root / ".zagrosi/transactions" / transaction_id
    anchor = root / ".zagrosi/transactions/claims" / f"{transaction_id}.json"
    assert live.is_dir()
    assert anchor.is_file()
    binding = ownership.load_persistent_transaction_binding(
        owned,
        transaction_id=transaction_id,
    ).unwrap()
    rebound = ownership.rebind_persistent_transaction(
        owned,
        binding=binding,
    ).unwrap()
    assert rebound.location is ownership.TransactionLocation.LIVE
    assert not (
        root / ".zagrosi/transactions/claims" / f".{transaction_id}.create.json"
    ).exists()
    owned.close()


@pytest.mark.skipif(os.name == "nt", reason="POSIX publication interruption")
def test_recovery_rebind_preserves_durable_creation_intent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import zagrosi_forge.install.ownership as ownership

    class InjectedPublicationInterrupt(BaseException):
        pass

    transaction_id = "tx-c7c7c7c7c7c7c7c7c7c7c7c7c7c7c7c7"
    _, owned, root = _owned(tmp_path)
    original_rename = ownership._exclusive_rename

    def publish_then_interrupt(parent: int, source: str, destination: str) -> None:
        original_rename(parent, source, destination)
        raise InjectedPublicationInterrupt

    monkeypatch.setattr(ownership, "_exclusive_rename", publish_then_interrupt)
    with pytest.raises(InjectedPublicationInterrupt):
        ownership.create_persistent_transaction_root(
            owned,
            transaction_id=transaction_id,
        )
    monkeypatch.undo()

    binding = ownership.load_persistent_transaction_binding(
        owned,
        transaction_id=transaction_id,
    ).unwrap()
    claims = root / ".zagrosi/transactions/claims"
    creation_intent = claims / f".{transaction_id}.create.json"
    before = (
        creation_intent.stat().st_dev,
        creation_intent.stat().st_ino,
        creation_intent.read_bytes(),
    )
    rebound = ownership.rebind_persistent_transaction_for_recovery(
        owned,
        binding=binding,
    ).unwrap()
    try:
        assert rebound.location is ownership.TransactionLocation.LIVE
        assert (
            creation_intent.stat().st_dev,
            creation_intent.stat().st_ino,
            creation_intent.read_bytes(),
        ) == before
    finally:
        rebound.close()

    maintained = ownership.rebind_persistent_transaction(
        owned,
        binding=binding,
    ).unwrap()
    maintained.close()
    assert not creation_intent.exists()
    owned.close()


@pytest.mark.skipif(os.name == "nt", reason="POSIX bound-successor interruption")
@pytest.mark.parametrize("tamper", ("missing", "substituted"))
def test_bound_creation_successor_requires_exact_reservation_predecessor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tamper: str,
) -> None:
    import zagrosi_forge.install.ownership as ownership

    class BoundSuccessorInterrupt(BaseException):
        pass

    transaction_id = (
        "tx-d5d5d5d5d5d5d5d5d5d5d5d5d5d5d5d5"
        if tamper == "missing"
        else "tx-d6d6d6d6d6d6d6d6d6d6d6d6d6d6d6d6"
    )
    _, owned, root = _owned(tmp_path)
    final_claim = f"{transaction_id}.json"
    original_rename = ownership._exclusive_rename

    def publish_anchor_then_interrupt(
        parent: int,
        source: str,
        destination: str,
    ) -> None:
        original_rename(parent, source, destination)
        if destination == final_claim:
            raise BoundSuccessorInterrupt

    monkeypatch.setattr(
        ownership,
        "_exclusive_rename",
        publish_anchor_then_interrupt,
    )
    with pytest.raises(BoundSuccessorInterrupt):
        ownership.create_persistent_transaction_root(
            owned,
            transaction_id=transaction_id,
        )
    monkeypatch.undo()

    claims = root / ".zagrosi/transactions/claims"
    reservation = claims / f".{transaction_id}.reserve.json"
    if tamper == "missing":
        reservation.unlink()
    else:
        reservation.rename(claims / "displaced-reservation.json")
        reservation.write_bytes(b"preserve-substituted-reservation")
        reservation.chmod(0o600)

    retried = ownership.create_persistent_transaction_root(
        owned,
        transaction_id=transaction_id,
    )

    assert _code(retried) == "ownership.unowned"
    assert (root / ".zagrosi/transactions" / transaction_id).is_dir()
    assert (claims / final_claim).is_file()
    if tamper == "substituted":
        assert reservation.read_bytes() == b"preserve-substituted-reservation"
    owned.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows publication interruption")
def test_windows_post_rename_interrupt_preserves_exact_root_and_anchor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import zagrosi_forge.install.ownership as ownership

    class InjectedPublicationInterrupt(BaseException):
        pass

    transaction_id = "tx-f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0"
    final_claim = f"{transaction_id}.json"
    _, owned, root = _owned(tmp_path)
    original_rename = ownership._paths._windows_rename_handle

    def publish_then_interrupt(source: int, parent: int, destination: str) -> None:
        original_rename(source, parent, destination)
        if destination == final_claim:
            raise InjectedPublicationInterrupt

    monkeypatch.setattr(
        ownership._paths,
        "_windows_rename_handle",
        publish_then_interrupt,
    )
    with pytest.raises(InjectedPublicationInterrupt):
        ownership.create_persistent_transaction_root(
            owned,
            transaction_id=transaction_id,
        )

    live = root / ".zagrosi/transactions" / transaction_id
    anchor = root / ".zagrosi/transactions/claims" / final_claim
    assert live.is_dir()
    assert anchor.is_file()
    binding = ownership.load_persistent_transaction_binding(
        owned,
        transaction_id=transaction_id,
    ).unwrap()
    rebound = ownership.rebind_persistent_transaction(
        owned,
        binding=binding,
    ).unwrap()
    assert rebound.location is ownership.TransactionLocation.LIVE
    assert not (
        root / ".zagrosi/transactions/claims" / f".{transaction_id}.create.json"
    ).exists()
    owned.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows durability barriers")
def test_windows_persistent_publication_flushes_intent_then_root_before_anchor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import zagrosi_forge.install.ownership as ownership

    barriers: list[str] = []
    claims_handle: int | None = None
    original_file_flush = ownership._windows_flush
    original_directory_flush = ownership._windows_flush_directory
    original_create_directory = ownership._paths._windows_create_private_directory
    original_rename = ownership._paths._windows_rename_handle

    def record_file_flush(handle: int) -> None:
        barriers.append("file")
        original_file_flush(handle)

    def record_directory_flush(handle: int) -> None:
        barriers.append("directory")
        original_directory_flush(handle)

    def capture_created_directory(parent: int, component: str) -> int:
        nonlocal claims_handle
        handle = original_create_directory(parent, component)
        if component == ownership._TRANSACTION_CLAIMS_COMPONENT:
            claims_handle = handle
        return handle

    def require_closed_child_before_parent_rename(
        source: int,
        parent: int,
        destination: str,
    ) -> None:
        if destination == ownership._TRANSACTION_STORE_COMPONENT:
            assert claims_handle is not None
            with pytest.raises(OSError):
                ownership._paths._windows_handle_status(claims_handle)
        original_rename(source, parent, destination)

    monkeypatch.setattr(ownership, "_windows_flush", record_file_flush)
    monkeypatch.setattr(
        ownership,
        "_windows_flush_directory",
        record_directory_flush,
    )
    monkeypatch.setattr(
        ownership._paths,
        "_windows_create_private_directory",
        capture_created_directory,
    )
    monkeypatch.setattr(
        ownership._paths,
        "_windows_rename_handle",
        require_closed_child_before_parent_rename,
    )
    transaction_id = "tx-e0e0e0e0e0e0e0e0e0e0e0e0e0e0e0e0"
    _, owned, _ = _owned(tmp_path)
    store = ownership._open_transaction_store(owned, create=True)
    assert barriers[0] == "file"
    assert "directory" in barriers[1:]
    barriers.clear()
    try:
        ownership._create_windows_persistent_transaction(
            owned,
            store,
            transaction_id=transaction_id,
        )
    finally:
        store.close()

    file_barriers = [
        index for index, barrier in enumerate(barriers) if barrier == "file"
    ]
    assert len(file_barriers) >= 3
    intent_barrier = file_barriers[0]
    anchor_barrier = file_barriers[-1]
    assert anchor_barrier > intent_barrier
    assert barriers[intent_barrier + 1 : anchor_barrier].count("directory") >= 2
    assert barriers[anchor_barrier + 1 :]
    assert set(barriers[anchor_barrier + 1 :]) == {"directory"}
    owned.close()


@pytest.mark.skipif(os.name == "nt", reason="POSIX anchor metadata race")
@pytest.mark.parametrize("race", ("hardlink", "mode"))
def test_persistent_anchor_after_read_race_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    race: str,
) -> None:
    import zagrosi_forge.install.ownership as ownership

    transaction_id = "tx-c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3"
    _, owned, root, created = _persistent_transaction(
        tmp_path, transaction_id=transaction_id
    )
    anchor = root / created.binding.claim_relative
    original_read = ownership._read_posix_private_record
    raced = False

    def read_then_race(
        parent: int, component: str, *, device: int
    ) -> tuple[bytes, tuple[int, int]]:
        nonlocal raced
        observed = original_read(parent, component, device=device)
        if component == f"{transaction_id}.json" and not raced:
            raced = True
            if race == "hardlink":
                os.link(anchor, anchor.with_name("anchor-race-link.json"))
            else:
                anchor.chmod(0o640)
        return observed

    monkeypatch.setattr(ownership, "_read_posix_private_record", read_then_race)
    loaded = ownership.load_persistent_transaction_binding(
        owned, transaction_id=transaction_id
    )

    assert raced
    assert _code(loaded) == "ownership.unowned"
    assert anchor.exists()
    owned.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows anchor metadata race")
@pytest.mark.parametrize("race", ("hardlink", "dacl"))
def test_windows_persistent_anchor_after_read_race_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    race: str,
) -> None:
    import zagrosi_forge.install.ownership as ownership

    transaction_id = "tx-c8c8c8c8c8c8c8c8c8c8c8c8c8c8c8c8"
    _, owned, root, created = _persistent_transaction(
        tmp_path, transaction_id=transaction_id
    )
    anchor = root / created.binding.claim_relative
    original_read = ownership._read_windows_private_record
    raced = False

    def read_then_race(
        parent: int, component: str, *, volume: int
    ) -> tuple[bytes, tuple[int, int]]:
        nonlocal raced
        observed = original_read(parent, component, volume=volume)
        if component == f"{transaction_id}.json" and not raced:
            raced = True
            if race == "hardlink":
                os.link(anchor, anchor.with_name("anchor-race-link.json"))
            else:
                subprocess.run(
                    ["icacls", str(anchor), "/inheritance:e"],
                    check=True,
                    capture_output=True,
                    text=True,
                )
        return observed

    monkeypatch.setattr(ownership, "_read_windows_private_record", read_then_race)
    loaded = ownership.load_persistent_transaction_binding(
        owned, transaction_id=transaction_id
    )

    assert raced
    assert _code(loaded) == "ownership.unowned"
    assert anchor.exists()
    owned.close()


@pytest.mark.skipif(os.name == "nt", reason="POSIX namespace publication race")
@pytest.mark.parametrize("phase", ("before", "after"))
def test_persistent_creation_rejects_root_move_around_anchor_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    import zagrosi_forge.install.ownership as ownership

    transaction_id = (
        "tx-c4c4c4c4c4c4c4c4c4c4c4c4c4c4c4c4"
        if phase == "before"
        else "tx-c5c5c5c5c5c5c5c5c5c5c5c5c5c5c5c5"
    )
    _, owned, root = _owned(tmp_path)
    live = root / ".zagrosi/transactions" / transaction_id
    displaced = live.with_name(f"{transaction_id}-displaced")
    original_rename = ownership._exclusive_rename

    def move_around_publish(parent: int, source: str, destination: str) -> None:
        if destination == f"{transaction_id}.json" and phase == "before":
            live.rename(displaced)
        original_rename(parent, source, destination)
        if destination == f"{transaction_id}.json" and phase == "after":
            live.rename(displaced)

    monkeypatch.setattr(ownership, "_exclusive_rename", move_around_publish)
    created = ownership.create_persistent_transaction_root(
        owned, transaction_id=transaction_id
    )

    assert _code(created) == "ownership.unowned"
    assert displaced.is_dir()
    assert not live.exists()
    loaded = ownership.load_persistent_transaction_binding(
        owned, transaction_id=transaction_id
    )
    if loaded.is_ok:
        rebound = ownership.rebind_persistent_transaction(
            owned, binding=loaded.unwrap()
        )
        assert _code(rebound) == "ownership.cleanup_incomplete"
    owned.close()


@pytest.mark.skipif(os.name == "nt", reason="POSIX namespace injection")
def test_persistent_creation_never_adopts_unrelated_private_live_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import zagrosi_forge.install.ownership as ownership

    transaction_id = "tx-cdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcd"
    _, owned, root = _owned(tmp_path)
    live = root / ".zagrosi/transactions" / transaction_id
    original_mkdir = ownership.os.mkdir
    original_rename = ownership._paths._exclusive_posix_rename
    injected = False

    def inject_live_root() -> None:
        nonlocal injected
        injected = True
        live.mkdir(mode=0o700)
        (live / "attacker-canary").write_bytes(b"preserve-unrelated")

    def inject_before_mkdir(
        component: str | bytes,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> None:
        if component == transaction_id and not injected:
            inject_live_root()
            raise FileExistsError(errno.EEXIST, "injected unrelated root")
        original_mkdir(component, mode, dir_fd=dir_fd)

    def inject_before_publish(
        parent: int,
        source: str,
        destination: str,
    ) -> None:
        if destination == transaction_id and not injected:
            inject_live_root()
        original_rename(parent, source, destination)

    monkeypatch.setattr(ownership.os, "mkdir", inject_before_mkdir)
    monkeypatch.setattr(
        ownership._paths,
        "_exclusive_posix_rename",
        inject_before_publish,
    )
    created = ownership.create_persistent_transaction_root(
        owned, transaction_id=transaction_id
    )

    assert injected
    assert _code(created) == "ownership.unowned"
    assert (live / "attacker-canary").read_bytes() == b"preserve-unrelated"
    owned.close()


@pytest.mark.skipif(os.name == "nt", reason="POSIX record retirement race")
def test_posix_transaction_record_retirement_preserves_name_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import zagrosi_forge.install.ownership as ownership

    transaction_id = "tx-cececececececececececececececece"
    _, owned, root = _owned(tmp_path)
    claims = root / ".zagrosi/transactions/claims"
    intent_component = f".{transaction_id}.create.json"
    displaced = claims / "displaced-create-intent.json"
    unknown = b"preserve-unknown-record"
    original_rename = ownership._paths._exclusive_posix_rename
    swapped = False

    def swap_before_retirement(
        parent: int,
        source: str,
        destination: str,
    ) -> None:
        nonlocal swapped
        if source == intent_component and not swapped:
            swapped = True
            (claims / source).rename(displaced)
            (claims / source).write_bytes(unknown)
            (claims / source).chmod(0o600)
        original_rename(parent, source, destination)

    monkeypatch.setattr(
        ownership._paths,
        "_exclusive_posix_rename",
        swap_before_retirement,
    )
    created = ownership.create_persistent_transaction_root(
        owned, transaction_id=transaction_id
    )

    assert swapped
    assert _code(created) == "ownership.unowned"
    preserved = [
        candidate
        for candidate in claims.iterdir()
        if candidate.is_file() and candidate.read_bytes() == unknown
    ]
    assert preserved
    assert displaced.is_file()
    owned.close()


@pytest.mark.skipif(os.name == "nt", reason="POSIX record retirement")
def test_posix_transaction_record_retirement_is_bounded_and_retry_safe(
    tmp_path: Path,
) -> None:
    import zagrosi_forge.install.ownership as ownership

    _, owned, root = _owned(tmp_path)
    store = ownership._open_transaction_store(owned, create=True)
    component = ".bounded-retirement.json"
    raw = b"exact-record"
    retired = (
        root
        / ".zagrosi/transactions/claims"
        / f".retired-{hashlib.sha256(component.encode()).hexdigest()}.json"
    )
    try:
        identity = ownership._publish_transaction_record(store, component, raw)
        ownership._remove_exact_transaction_record(
            store,
            component,
            identity,
            raw,
        )
        ownership._remove_exact_transaction_record(
            store,
            component,
            identity,
            raw,
        )

        assert retired.read_bytes() == raw
        assert tuple(retired.parent.glob(".retired-*.json")) == (retired,)
        active = retired.parent / component
        active.write_bytes(b"preserve-active-substitution")
        active.chmod(0o600)
        with pytest.raises(OSError):
            ownership._remove_exact_transaction_record(
                store,
                component,
                identity,
                raw,
            )
        assert active.read_bytes() == b"preserve-active-substitution"
        assert retired.read_bytes() == raw
    finally:
        store.close()
        owned.close()


@pytest.mark.skipif(os.name == "nt", reason="POSIX record retirement")
def test_posix_transaction_record_retirement_preserves_deterministic_conflict(
    tmp_path: Path,
) -> None:
    import zagrosi_forge.install.ownership as ownership

    _, owned, root = _owned(tmp_path)
    store = ownership._open_transaction_store(owned, create=True)
    component = ".bounded-conflict.json"
    raw = b"exact-record"
    claims = root / ".zagrosi/transactions/claims"
    retired = claims / f".retired-{hashlib.sha256(component.encode()).hexdigest()}.json"
    unknown = b"preserve-unknown-retirement"
    try:
        identity = ownership._publish_transaction_record(store, component, raw)
        retired.write_bytes(unknown)
        retired.chmod(0o600)

        with pytest.raises(OSError):
            ownership._remove_exact_transaction_record(
                store,
                component,
                identity,
                raw,
            )

        assert (claims / component).read_bytes() == raw
        assert retired.read_bytes() == unknown
        assert tuple(claims.glob(".retired-*.json")) == (retired,)
    finally:
        store.close()
        owned.close()


def test_persistent_transaction_root_rebinds_from_durable_sibling_anchor(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.ownership import (
        TransactionLocation,
        load_persistent_transaction_binding,
        rebind_persistent_transaction,
    )
    from zagrosi_forge.install.paths import PlatformPathAuthority

    transaction_id = "tx-0123456789abcdef0123456789abcdef"
    _, owned, root, created = _persistent_transaction(
        tmp_path, transaction_id=transaction_id
    )
    binding = created.binding
    anchor = root / ".zagrosi/transactions/claims" / f"{transaction_id}.json"
    anchor_before = (_identity(anchor), anchor.read_bytes())
    assert binding.root_relative == f".zagrosi/transactions/{transaction_id}"
    assert anchor.is_file()
    owned.close()

    authority = PlatformPathAuthority()
    reopened = authority.bootstrap_forge_root(
        tmp_path / "codex-home", runner=_runner()
    ).unwrap()
    try:
        loaded = load_persistent_transaction_binding(
            reopened, transaction_id=transaction_id
        ).unwrap()
        assert loaded == binding
        rebound = rebind_persistent_transaction(reopened, binding=loaded).unwrap()
        assert rebound.location is TransactionLocation.LIVE
        assert rebound.claim is not None
        assert rebound.ticket is None
        assert rebound.claim.relative.value == binding.root_relative
        assert rebound.claim.identity == binding.transaction_identity
        assert (_identity(anchor), anchor.read_bytes()) == anchor_before
    finally:
        reopened.close()


def test_transaction_journal_access_is_writable_live_and_read_only_quarantined(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.contracts import ForgeError
    from zagrosi_forge.install.ownership import (
        TransactionLocation,
        open_transaction_journal_access,
        prove_transaction_owned,
        quarantine_owned,
        rebind_persistent_transaction,
    )

    transaction_id = "tx-88888888888888888888888888888888"
    authority, owned, root, created = _persistent_transaction(
        tmp_path, transaction_id=transaction_id
    )
    live = root / created.binding.root_relative
    (live / "journal.json").write_bytes(b"sealed-journal")

    live_access = open_transaction_journal_access(owned, created).unwrap()
    assert live_access.location is TransactionLocation.LIVE
    assert live_access.binding == created.binding
    assert not live_access.read_only
    live_access._require_journal_access(write=False)
    live_access._require_journal_access(write=True)
    descriptor = live_access._duplicate_journal_descriptor(write=True)
    try:
        import zagrosi_forge.install.ownership as ownership

        assert (
            ownership._native_identity(descriptor)
            == created.binding.transaction_identity
        )
    finally:
        ownership._close_native(descriptor)
    live_access.close()

    path = authority.prove_descendant(
        owned, created.claim.relative, expected_depth=3
    ).unwrap()
    proof = prove_transaction_owned(path, claim=created.claim).unwrap()
    ticket = quarantine_owned(proof, transaction_id=transaction_id).unwrap()
    rebound = rebind_persistent_transaction(owned, binding=created.binding).unwrap()
    assert rebound.location is TransactionLocation.QUARANTINED
    assert rebound.ticket is not None

    quarantine_access = open_transaction_journal_access(owned, rebound).unwrap()
    try:
        assert quarantine_access.location is TransactionLocation.QUARANTINED
        assert quarantine_access.binding == created.binding
        assert quarantine_access.read_only
        assert not hasattr(quarantine_access, "ticket")
        quarantine_access._require_journal_access(write=False)
        descriptor = quarantine_access._duplicate_journal_descriptor(write=False)
        try:
            assert (
                ownership._native_identity(descriptor)
                == created.binding.transaction_identity
            )
        finally:
            ownership._close_native(descriptor)
        with pytest.raises(ForgeError) as caught:
            quarantine_access._require_journal_access(write=True)
        assert caught.value.code == "ownership.unowned"
        with pytest.raises(ForgeError) as caught:
            quarantine_access._duplicate_journal_descriptor(write=True)
        assert caught.value.code == "ownership.unowned"
        assert rebound.ticket is not None
    finally:
        quarantine_access.close()
        rebound.close()
        ticket.close()
        path.close()
        owned.close()


@pytest.mark.parametrize("tamper", ("unlink", "replace", "hardlink"))
def test_transaction_journal_access_revalidates_exact_anchor(
    tmp_path: Path, tamper: str
) -> None:
    from zagrosi_forge.install.contracts import ForgeError
    from zagrosi_forge.install.ownership import open_transaction_journal_access

    transaction_id = "tx-99999999999999999999999999999999"
    _, owned, root, created = _persistent_transaction(
        tmp_path, transaction_id=transaction_id
    )
    access = open_transaction_journal_access(owned, created).unwrap()
    anchor = root / created.binding.claim_relative
    displaced = anchor.with_name("displaced-anchor.json")

    if tamper == "unlink":
        anchor.unlink()
    elif tamper == "replace":
        raw = anchor.read_bytes()
        anchor.rename(displaced)
        anchor.write_bytes(raw)
        if os.name != "nt":
            anchor.chmod(0o600)
    else:
        try:
            os.link(anchor, displaced)
        except OSError as exc:
            access.close()
            owned.close()
            pytest.skip(f"hard links unavailable: {exc}")

    try:
        with pytest.raises(ForgeError) as caught:
            access._require_journal_access(write=False)
        assert caught.value.code == "ownership.identity_mismatch"
        assert created.binding.quarantine_relative in caught.value.recovery_instructions
    finally:
        access.close()
        owned.close()


@pytest.mark.parametrize("drift", ("transaction", "store"))
def test_transaction_journal_access_revalidates_exact_namespace(
    tmp_path: Path,
    drift: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import zagrosi_forge.install.ownership as ownership
    from zagrosi_forge.install.contracts import ForgeError

    transaction_id = "tx-dddddddddddddddddddddddddddddddd"
    _, owned, root, created = _persistent_transaction(
        tmp_path, transaction_id=transaction_id
    )
    access = ownership.open_transaction_journal_access(owned, created).unwrap()
    if drift == "transaction":
        selected = root / created.binding.root_relative
    else:
        selected = root / ".zagrosi/transactions"
    displaced = selected.with_name(f"{selected.name}-displaced")

    try:
        if os.name == "nt" and drift == "store":
            with pytest.raises(PermissionError) as blocked:
                selected.rename(displaced)
            assert blocked.value.winerror == 5
            monkeypatch.setattr(
                ownership,
                "_transaction_store_namespace_is_valid",
                lambda _store: False,
            )
        else:
            selected.rename(displaced)
            _private_test_directory(selected)
        with pytest.raises(ForgeError) as caught:
            access._require_journal_access(write=False)
        assert caught.value.code == "ownership.identity_mismatch"
        assert created.binding.quarantine_relative in caught.value.recovery_instructions
    finally:
        access.close()
        owned.close()


def test_restart_rebinds_live_quarantined_and_removed_transaction_states(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.ownership import (
        TransactionLocation,
        load_persistent_transaction_binding,
        prove_transaction_owned,
        quarantine_owned,
        rebind_persistent_transaction,
        remove_quarantine,
    )
    from zagrosi_forge.install.paths import PlatformPathAuthority

    transaction_id = "tx-11111111111111111111111111111111"
    authority, owned, root, created = _persistent_transaction(
        tmp_path, transaction_id=transaction_id
    )
    binding = created.binding
    live = root / binding.root_relative
    (live / "journal.json").write_bytes(b"durable-journal")
    path = authority.prove_descendant(
        owned,
        created.claim.relative,
        expected_depth=3,
    ).unwrap()
    proof = prove_transaction_owned(path, claim=created.claim).unwrap()
    ticket = quarantine_owned(proof, transaction_id=transaction_id).unwrap()
    assert ticket.recovery_reference == binding.quarantine_relative
    quarantine = root / binding.quarantine_relative
    assert quarantine.is_dir()
    assert not live.exists()
    ticket.close()  # Simulate losing the in-memory ticket at process death.
    path.close()
    owned.close()

    authority = PlatformPathAuthority()
    reopened = authority.bootstrap_forge_root(
        tmp_path / "codex-home", runner=_runner()
    ).unwrap()
    try:
        loaded = load_persistent_transaction_binding(
            reopened, transaction_id=transaction_id
        ).unwrap()
        rebound = rebind_persistent_transaction(reopened, binding=loaded).unwrap()
        assert rebound.location is TransactionLocation.QUARANTINED
        assert rebound.claim is None
        assert rebound.ticket is not None
        assert remove_quarantine(rebound.ticket).unwrap().removed
        assert not quarantine.exists()

        removed = rebind_persistent_transaction(reopened, binding=loaded).unwrap()
        assert removed.location is TransactionLocation.REMOVED
        assert removed.claim is None
        assert removed.ticket is None
        assert (
            root / ".zagrosi/transactions/claims" / f"{transaction_id}.json"
        ).is_file()
    finally:
        reopened.close()


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-death durability")
def test_restart_never_infers_cleanup_completion_after_deletion_process_death(
    tmp_path: Path,
) -> None:
    import zagrosi_forge.install.ownership as ownership
    from zagrosi_forge.install.paths import PlatformPathAuthority

    transaction_id = "tx-c7c7c7c7c7c7c7c7c7c7c7c7c7c7c7c7"
    authority, owned, root, created = _persistent_transaction(
        tmp_path, transaction_id=transaction_id
    )
    live = root / created.binding.root_relative
    (live / "payload.bin").write_bytes(b"candidate")
    path = authority.prove_descendant(
        owned,
        created.claim.relative,
        expected_depth=3,
    ).unwrap()
    proof = ownership.prove_transaction_owned(path, claim=created.claim).unwrap()
    ticket = ownership.quarantine_owned(proof, transaction_id=transaction_id).unwrap()
    quarantine = root / created.binding.quarantine_relative
    ticket.close()
    path.close()
    owned.close()

    child = os.fork()
    if child == 0:
        try:
            original_rmdir = ownership.os.rmdir

            def terminate_after_real_rmdir(
                component: str | bytes,
                *args: Any,
                **kwargs: Any,
            ) -> None:
                original_rmdir(component, *args, **kwargs)
                if (
                    isinstance(component, str)
                    and component.startswith(".delete-")
                    and component.endswith(".tmp")
                ):
                    os.kill(os.getpid(), signal.SIGKILL)

            ownership.os.rmdir = terminate_after_real_rmdir
            restarted = (
                PlatformPathAuthority()
                .bootstrap_forge_root(tmp_path / "codex-home", runner=_runner())
                .unwrap()
            )
            loaded = ownership.load_persistent_transaction_binding(
                restarted, transaction_id=transaction_id
            ).unwrap()
            rebound = ownership.rebind_persistent_transaction(
                restarted, binding=loaded
            ).unwrap()
            assert rebound.ticket is not None
            ownership.remove_quarantine(rebound.ticket)
        finally:
            os._exit(92)

    _waited, status = os.waitpid(child, 0)
    assert os.WIFSIGNALED(status)
    assert os.WTERMSIG(status) == signal.SIGKILL
    assert not quarantine.exists()

    restarted = (
        PlatformPathAuthority()
        .bootstrap_forge_root(tmp_path / "codex-home", runner=_runner())
        .unwrap()
    )
    try:
        loaded = ownership.load_persistent_transaction_binding(
            restarted, transaction_id=transaction_id
        ).unwrap()
        rebound = ownership.rebind_persistent_transaction(restarted, binding=loaded)
        assert _code(rebound) == "ownership.cleanup_incomplete"
        claims = root / ".zagrosi/transactions/claims"
        assert not (claims / f"{transaction_id}.removed.json").exists()
        assert (claims / f"{transaction_id}.removing.json").is_file()
    finally:
        restarted.close()


def test_terminal_recovery_cleanup_authorization_is_sealed_and_separate(
    tmp_path: Path,
) -> None:
    import pickle

    import zagrosi_forge.install.ownership as ownership

    transaction_id = "tx-d8d8d8d8d8d8d8d8d8d8d8d8d8d8d8d8"
    owned, _root, observation, journal = _recovery_cleanup_fixture(
        tmp_path,
        transaction_id=transaction_id,
    )
    try:
        authorized = ownership.authorize_recovery_cleanup(
            owned,
            observation=observation,
            journal=journal,
        ).unwrap()

        assert authorized.binding == observation.binding
        assert authorized.location is ownership.TransactionLocation.QUARANTINED
        assert authorized.journal_relative == observation.journal_relative
        assert authorized.journal_access_digest == journal.access_digest
        assert authorized.journal_head_record_digest == journal.head.record_digest
        assert authorized.journal_head_sequence == journal.head.sequence
        assert authorized.delete_component.startswith(".delete-")
        assert authorized.delete_component.endswith(".tmp")
        assert len(authorized.authorization_digest) == 64
        with pytest.raises(AttributeError):
            authorized.delete_component = ".delete-" + ("0" * 32) + ".tmp"  # type: ignore[misc]
        with pytest.raises(TypeError, match="not serializable"):
            pickle.dumps(authorized)

        assert ownership.discover_pending_transactions(owned).unwrap() == ()
        assert ownership.discover_recovery_cleanup_authorizations(owned).unwrap() == (
            authorized,
        )
    finally:
        owned.close()


def test_recovery_cleanup_observation_seals_exact_current_wal_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pickle

    import zagrosi_forge.install.ownership as ownership

    transaction_id = "tx-d7d7d7d7d7d7d7d7d7d7d7d7d7d7d7d7"
    owned, _root, observation, journal = _recovery_cleanup_fixture(
        tmp_path,
        transaction_id=transaction_id,
    )
    authorized = ownership.authorize_recovery_cleanup(
        owned,
        observation=observation,
        journal=journal,
    ).unwrap()
    original_complete = ownership._publish_transaction_cleanup_complete
    original_retire = ownership._retire_recovery_cleanup_phase
    try:
        initial = ownership.discover_recovery_cleanup_observations(owned).unwrap()

        assert len(initial) == 1
        assert initial[0].authorization == authorized
        assert initial[0].phase == "AUTHORIZED"
        assert initial[0].current_reference == observation.journal_relative
        assert len(initial[0].observation_digest) == 64
        with pytest.raises(AttributeError):
            initial[0].phase = "FINALIZING"  # type: ignore[misc]
        with pytest.raises(TypeError, match="not serializable"):
            pickle.dumps(initial[0])
        object.__setattr__(initial[0], "phase", "FINALIZING")
        with pytest.raises(TypeError, match="observation changed"):
            initial[0]._require_valid()
        object.__setattr__(initial[0], "phase", "AUTHORIZED")
        initial[0]._require_valid()

        def lose_response_after_exact_removal(*args: Any, **kwargs: Any) -> None:
            raise OSError("injected after exact rmdir")

        with monkeypatch.context() as context:
            context.setattr(
                ownership,
                "_publish_transaction_cleanup_complete",
                lose_response_after_exact_removal,
            )
            interrupted = ownership.resume_recovery_cleanup(owned, authorized)
        assert _code(interrupted) == "ownership.cleanup_incomplete"

        finalizing = ownership.discover_recovery_cleanup_observations(owned).unwrap()
        assert len(finalizing) == 1
        assert finalizing[0].authorization == authorized
        assert finalizing[0].phase == "FINALIZING"
        assert finalizing[0].current_reference is None
        assert finalizing[0].observation_digest != initial[0].observation_digest

        def stop_before_retirement(*args: Any, **kwargs: Any) -> None:
            raise OSError("injected after durable completion")

        with monkeypatch.context() as context:
            context.setattr(
                ownership,
                "_retire_recovery_cleanup_phase",
                stop_before_retirement,
            )
            completed_without_retirement = ownership.resume_recovery_cleanup(
                owned,
                authorized,
            )
        assert _code(completed_without_retirement) == "ownership.cleanup_incomplete"

        complete = ownership.discover_recovery_cleanup_observations(owned).unwrap()
        assert len(complete) == 1
        assert complete[0].authorization == authorized
        assert complete[0].phase == "COMPLETE"
        assert complete[0].current_reference is None
        assert complete[0].observation_digest not in {
            initial[0].observation_digest,
            finalizing[0].observation_digest,
        }

        assert ownership.resume_recovery_cleanup(owned, authorized).unwrap().removed
        assert ownership.discover_recovery_cleanup_observations(owned).unwrap() == ()
    finally:
        monkeypatch.setattr(
            ownership,
            "_publish_transaction_cleanup_complete",
            original_complete,
        )
        monkeypatch.setattr(
            ownership,
            "_retire_recovery_cleanup_phase",
            original_retire,
        )
        owned.close()


def test_active_recovery_cleanup_authorization_blocks_generic_rebind_and_ticket(
    tmp_path: Path,
) -> None:
    import zagrosi_forge.install.ownership as ownership

    transaction_id = "tx-d6d6d6d6d6d6d6d6d6d6d6d6d6d6d6d6"
    owned, root, observation, journal = _recovery_cleanup_fixture(
        tmp_path,
        transaction_id=transaction_id,
    )
    stale = ownership.rebind_persistent_transaction(
        owned,
        binding=observation.binding,
    ).unwrap()
    assert stale.ticket is not None
    authorized = ownership.authorize_recovery_cleanup(
        owned,
        observation=observation,
        journal=journal,
    ).unwrap()
    quarantine = root / observation.journal_relative
    claims = root / ".zagrosi/transactions/claims"
    before = tuple(
        (
            path.relative_to(root).as_posix(),
            path.stat().st_dev,
            path.stat().st_ino,
            path.read_bytes(),
        )
        for path in sorted(root.rglob("*"))
        if path.is_file()
    )
    try:
        rebound = ownership.rebind_persistent_transaction(
            owned,
            binding=observation.binding,
        )
        removed = ownership.remove_quarantine(stale.ticket)

        assert _code(rebound) == "ownership.cleanup_incomplete"
        assert _code(removed) == "ownership.cleanup_incomplete"
        assert quarantine.is_dir()
        assert (claims / f"{transaction_id}.rc-auth.json").is_file()
        assert not (claims / f"{transaction_id}.rc-final.json").exists()
        assert not (claims / f"{transaction_id}.rc-done.json").exists()
        assert (
            tuple(
                (
                    path.relative_to(root).as_posix(),
                    path.stat().st_dev,
                    path.stat().st_ino,
                    path.read_bytes(),
                )
                for path in sorted(root.rglob("*"))
                if path.is_file()
            )
            == before
        )
        assert ownership.discover_recovery_cleanup_authorizations(owned).unwrap() == (
            authorized,
        )
    finally:
        stale.close()
        owned.close()


def test_generic_cleanup_and_recovery_authorization_are_transaction_serialized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import zagrosi_forge.install.ownership as ownership

    transaction_id = "tx-d5d5d5d5d5d5d5d5d5d5d5d5d5d5d5d5"
    owned, root, observation, journal = _recovery_cleanup_fixture(
        tmp_path,
        transaction_id=transaction_id,
    )
    stale = ownership.rebind_persistent_transaction(
        owned,
        binding=observation.binding,
    ).unwrap()
    assert stale.ticket is not None
    entered_cleanup_intent = threading.Event()
    continue_cleanup = threading.Event()
    authorization_lock_attempted = threading.Event()
    authorization_finished = threading.Event()
    results: dict[str, Any] = {}
    original_acquire = ownership._acquire_transaction_state_lock
    original_publish = ownership._publish_transaction_cleanup_intent

    def observe_lock_attempt(store: Any, binding: Any) -> Any:
        if threading.current_thread().name == "authorize-cleanup":
            authorization_lock_attempted.set()
        return original_acquire(store, binding)

    def pause_generic_after_intent(*args: Any, **kwargs: Any) -> Any:
        record = original_publish(*args, **kwargs)
        entered_cleanup_intent.set()
        if not continue_cleanup.wait(timeout=5):
            raise OSError("authorization did not attempt the transaction lock")
        return record

    def run_generic_cleanup() -> None:
        try:
            results["cleanup"] = ownership.remove_quarantine(stale.ticket)
        except BaseException as exc:  # pragma: no cover - diagnostic propagation
            results["cleanup_exception"] = exc

    def run_authorization() -> None:
        try:
            results["authorization"] = ownership.authorize_recovery_cleanup(
                owned,
                observation=observation,
                journal=journal,
            )
        except BaseException as exc:  # pragma: no cover - diagnostic propagation
            results["authorization_exception"] = exc
        finally:
            authorization_finished.set()

    monkeypatch.setattr(
        ownership,
        "_acquire_transaction_state_lock",
        observe_lock_attempt,
    )
    monkeypatch.setattr(
        ownership,
        "_publish_transaction_cleanup_intent",
        pause_generic_after_intent,
    )
    cleanup_thread = threading.Thread(
        target=run_generic_cleanup,
        name="generic-cleanup",
    )
    authorization_thread = threading.Thread(
        target=run_authorization,
        name="authorize-cleanup",
    )
    try:
        cleanup_thread.start()
        assert entered_cleanup_intent.wait(timeout=5)
        authorization_thread.start()
        assert authorization_lock_attempted.wait(timeout=5)
        assert not authorization_finished.wait(timeout=0.1)
        continue_cleanup.set()
        cleanup_thread.join(timeout=5)
        authorization_thread.join(timeout=5)

        assert not cleanup_thread.is_alive()
        assert not authorization_thread.is_alive()
        assert "cleanup_exception" not in results
        assert "authorization_exception" not in results
        assert results["cleanup"].unwrap().removed
        assert _code(results["authorization"]) == "ownership.cleanup_incomplete"
        claims = root / ".zagrosi/transactions/claims"
        assert (claims / f"{transaction_id}.removed.json").is_file()
        assert not (claims / f"{transaction_id}.removing.json").exists()
        assert not (claims / f"{transaction_id}.rc-auth.json").exists()
        assert not (claims / f"{transaction_id}.rc-final.json").exists()
        assert not (claims / f"{transaction_id}.rc-done.json").exists()
        assert not (root / observation.journal_relative).exists()
        assert ownership.discover_recovery_cleanup_observations(owned).unwrap() == ()
    finally:
        continue_cleanup.set()
        cleanup_thread.join(timeout=5)
        authorization_thread.join(timeout=5)
        stale.close()
        owned.close()


@pytest.mark.skipif(os.name == "nt", reason="POSIX finalization interruption")
@pytest.mark.parametrize("authorization_state", ("missing", "retired"))
def test_recovery_cleanup_phase_residue_blocks_generic_cleanup_without_active_auth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    authorization_state: str,
) -> None:
    import zagrosi_forge.install.ownership as ownership

    transaction_id = (
        "tx-d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4"
        if authorization_state == "missing"
        else "tx-d3d3d3d3d3d3d3d3d3d3d3d3d3d3d3d3"
    )
    owned, root, observation, journal = _recovery_cleanup_fixture(
        tmp_path,
        transaction_id=transaction_id,
    )
    authorized = ownership.authorize_recovery_cleanup(
        owned,
        observation=observation,
        journal=journal,
    ).unwrap()
    original_rmdir = ownership.os.rmdir

    def stop_after_finalizing(*args: Any, **kwargs: Any) -> None:
        raise OSError("injected before exact rmdir")

    recovery_rebound = None
    try:
        with monkeypatch.context() as context:
            context.setattr(ownership.os, "rmdir", stop_after_finalizing)
            interrupted = ownership.resume_recovery_cleanup(owned, authorized)
        assert _code(interrupted) == "ownership.cleanup_incomplete"

        recovery_rebound = ownership.rebind_persistent_transaction_for_recovery(
            owned,
            binding=observation.binding,
            _recovery_authorization=authorized,
        ).unwrap()
        assert recovery_rebound.ticket is not None

        claims = root / ".zagrosi/transactions/claims"
        authorization_record = claims / f"{transaction_id}.rc-auth.json"
        finalizing_record = claims / f"{transaction_id}.rc-final.json"
        if authorization_state == "missing":
            authorization_record.unlink()
        else:
            authorization_record.rename(
                claims
                / ownership._transaction_record_retirement_component(
                    authorization_record.name
                )
            )
        assert finalizing_record.is_file()
        targets = tuple(
            path
            for path in (root / ".zagrosi/transactions").iterdir()
            if path.is_dir() and path.name.startswith(".delete-")
        )
        assert len(targets) == 1
        before = tuple(
            (
                path.relative_to(root).as_posix(),
                path.stat().st_dev,
                path.stat().st_ino,
                path.read_bytes(),
            )
            for path in sorted(root.rglob("*"))
            if path.is_file()
        )

        rebound = ownership.rebind_persistent_transaction(
            owned,
            binding=observation.binding,
        )
        removed = ownership.remove_quarantine(recovery_rebound.ticket)

        assert _code(rebound) == "ownership.cleanup_incomplete"
        assert _code(removed) == "ownership.cleanup_incomplete"
        assert targets[0].is_dir()
        assert finalizing_record.is_file()
        assert (
            tuple(
                (
                    path.relative_to(root).as_posix(),
                    path.stat().st_dev,
                    path.stat().st_ino,
                    path.read_bytes(),
                )
                for path in sorted(root.rglob("*"))
                if path.is_file()
            )
            == before
        )
    finally:
        monkeypatch.setattr(ownership.os, "rmdir", original_rmdir)
        if recovery_rebound is not None:
            recovery_rebound.close()
        owned.close()


def test_recovery_cleanup_authorization_rejects_nonterminal_or_mismatched_journal(
    tmp_path: Path,
) -> None:
    import zagrosi_forge.install.ownership as ownership

    first_owned, first_root, first_observation, first_journal = (
        _recovery_cleanup_fixture(
            tmp_path / "first",
            transaction_id="tx-d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9",
            terminal=False,
        )
    )
    second_owned, _second_root, _second_observation, second_journal = (
        _recovery_cleanup_fixture(
            tmp_path / "second",
            transaction_id="tx-dadadadadadadadadadadadadadadada",
        )
    )
    try:
        nonterminal = ownership.authorize_recovery_cleanup(
            first_owned,
            observation=first_observation,
            journal=first_journal,
        )
        mismatched = ownership.authorize_recovery_cleanup(
            first_owned,
            observation=first_observation,
            journal=second_journal,
        )

        assert _code(nonterminal) == "ownership.cleanup_incomplete"
        assert _code(mismatched) == "ownership.cleanup_incomplete"
        claims = first_root / ".zagrosi/transactions/claims"
        assert not (
            claims / (f"{first_observation.binding.transaction_id}.rc-auth.json")
        ).exists()
        assert (first_root / first_observation.binding.quarantine_relative).is_dir()
    finally:
        first_owned.close()
        second_owned.close()


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-death injection")
def test_terminal_recovery_cleanup_resumes_only_from_finalizing_wal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import zagrosi_forge.install.ownership as ownership

    transaction_id = "tx-dbdbdbdbdbdbdbdbdbdbdbdbdbdbdbdb"
    owned, root, observation, journal = _recovery_cleanup_fixture(
        tmp_path,
        transaction_id=transaction_id,
    )
    authorized = ownership.authorize_recovery_cleanup(
        owned,
        observation=observation,
        journal=journal,
    ).unwrap()
    quarantine = root / observation.binding.quarantine_relative
    claims = root / ".zagrosi/transactions/claims"
    original_complete = ownership._publish_transaction_cleanup_complete

    def lose_response_before_generic_completion(*args: Any, **kwargs: Any) -> None:
        raise OSError("injected after exact rmdir")

    try:
        with monkeypatch.context() as context:
            context.setattr(
                ownership,
                "_publish_transaction_cleanup_complete",
                lose_response_before_generic_completion,
            )
            interrupted = ownership.resume_recovery_cleanup(owned, authorized)

        assert _code(interrupted) == "ownership.cleanup_incomplete"
        assert not quarantine.exists()
        assert (claims / f"{transaction_id}.rc-auth.json").is_file()
        assert (claims / f"{transaction_id}.rc-final.json").is_file()
        assert not (claims / f"{transaction_id}.rc-done.json").exists()
        assert (claims / f"{transaction_id}.removing.json").is_file()
        assert not (claims / f"{transaction_id}.removed.json").exists()
        assert (
            _code(
                ownership.rebind_persistent_transaction(
                    owned,
                    binding=observation.binding,
                )
            )
            == "ownership.cleanup_incomplete"
        )

        discovered = ownership.discover_recovery_cleanup_authorizations(owned).unwrap()
        assert discovered == (authorized,)
        resumed = ownership.resume_recovery_cleanup(owned, discovered[0]).unwrap()

        assert resumed.removed
        assert not quarantine.exists()
        assert (claims / f"{transaction_id}.removed.json").is_file()
        assert not (claims / f"{transaction_id}.removing.json").exists()
        assert not (claims / f"{transaction_id}.rc-auth.json").exists()
        assert not (claims / f"{transaction_id}.rc-final.json").exists()
        assert not (claims / f"{transaction_id}.rc-done.json").exists()
        assert (claims / f"{transaction_id}.json").is_file()
        assert ownership.discover_pending_transactions(owned).unwrap() == ()
        assert ownership.discover_recovery_cleanup_authorizations(owned).unwrap() == ()

        completed_rebound = ownership.rebind_persistent_transaction(
            owned,
            binding=observation.binding,
        ).unwrap()
        assert completed_rebound.location is ownership.TransactionLocation.REMOVED
        completed_rebound.close()

        response_lost = ownership.resume_recovery_cleanup(
            owned,
            authorized,
        ).unwrap()
        assert response_lost.removed
    finally:
        monkeypatch.setattr(
            ownership,
            "_publish_transaction_cleanup_complete",
            original_complete,
        )
        owned.close()


def test_recovery_cleanup_authorization_tamper_preserves_exact_quarantine(
    tmp_path: Path,
) -> None:
    import zagrosi_forge.install.ownership as ownership

    transaction_id = "tx-dcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdc"
    owned, root, observation, journal = _recovery_cleanup_fixture(
        tmp_path,
        transaction_id=transaction_id,
    )
    try:
        authorized = ownership.authorize_recovery_cleanup(
            owned,
            observation=observation,
            journal=journal,
        ).unwrap()
        record = (
            root / ".zagrosi/transactions/claims" / f"{transaction_id}.rc-auth.json"
        )
        record.write_bytes(record.read_bytes() + b" ")
        if os.name != "nt":
            record.chmod(0o600)

        failed = ownership.resume_recovery_cleanup(owned, authorized)

        assert _code(failed) == "ownership.cleanup_incomplete"
        assert (root / observation.binding.quarantine_relative).is_dir()
        assert not (record.with_name(f"{transaction_id}.rc-final.json")).exists()
        assert not (
            root / ".zagrosi/transactions/claims" / f"{transaction_id}.removed.json"
        ).exists()
    finally:
        owned.close()


@pytest.mark.skipif(os.name == "nt", reason="POSIX cleanup namespace race")
def test_rebind_never_promotes_moved_quarantine_from_removing_intent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import zagrosi_forge.install.ownership as ownership

    transaction_id = "tx-cfcfcfcfcfcfcfcfcfcfcfcfcfcfcfcf"
    authority, owned, root, created = _persistent_transaction(
        tmp_path, transaction_id=transaction_id
    )
    live = root / created.binding.root_relative
    (live / "survivor.bin").write_bytes(b"must-survive")
    path = authority.prove_descendant(
        owned,
        created.claim.relative,
        expected_depth=3,
    ).unwrap()
    proof = ownership.prove_transaction_owned(path, claim=created.claim).unwrap()
    ticket = ownership.quarantine_owned(proof, transaction_id=transaction_id).unwrap()
    quarantine = root / created.binding.quarantine_relative

    with monkeypatch.context() as context:
        context.setattr(
            ownership,
            "_clean_directory",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                OSError("injected before exact deletion")
            ),
        )
        failed = ownership.remove_quarantine(ticket)
    assert _code(failed) == "ownership.cleanup_incomplete"

    removing = json.loads(
        (
            root / ".zagrosi/transactions/claims" / f"{transaction_id}.removing.json"
        ).read_bytes()
    )
    delete_token = quarantine.with_name(removing["delete_component"])
    displaced = delete_token.with_name(f"{delete_token.name}-moved")
    delete_token.rename(displaced)
    rebound = ownership.rebind_persistent_transaction(owned, binding=created.binding)

    assert _code(rebound) == "ownership.cleanup_incomplete"
    assert (displaced / "survivor.bin").read_bytes() == b"must-survive"
    claims = root / ".zagrosi/transactions/claims"
    assert not (claims / f"{transaction_id}.removed.json").exists()
    assert (claims / f"{transaction_id}.removing.json").is_file()
    path.close()
    owned.close()


@pytest.mark.skipif(os.name == "nt", reason="POSIX has no conditional rmdir by inode")
def test_posix_cleanup_swap_at_delete_token_never_publishes_false_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import zagrosi_forge.install.ownership as ownership

    transaction_id = "tx-d3d3d3d3d3d3d3d3d3d3d3d3d3d3d3d3"
    authority, owned, root, created = _persistent_transaction(
        tmp_path,
        transaction_id=transaction_id,
    )
    live = root / created.binding.root_relative
    (live / "payload.bin").write_bytes(b"candidate")
    path = authority.prove_descendant(
        owned,
        created.claim.relative,
        expected_depth=3,
    ).unwrap()
    proof = ownership.prove_transaction_owned(path, claim=created.claim).unwrap()
    ticket = ownership.quarantine_owned(
        proof,
        transaction_id=transaction_id,
    ).unwrap()
    original_rmdir = ownership.os.rmdir
    displaced: Path | None = None
    swapped = False

    def swap_delete_token_before_rmdir(
        component: str | bytes,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        nonlocal displaced, swapped
        if (
            isinstance(component, str)
            and component.startswith(".delete-")
            and component.endswith(".tmp")
            and not swapped
        ):
            swapped = True
            delete_token = root / ".zagrosi/transactions" / component
            displaced = delete_token.with_name(f"{component}-displaced")
            delete_token.rename(displaced)
            (displaced / "exact-survivor").write_bytes(b"must-survive")
            _private_test_directory(delete_token)
        original_rmdir(component, *args, **kwargs)

    monkeypatch.setattr(ownership.os, "rmdir", swap_delete_token_before_rmdir)
    removed = ownership.remove_quarantine(ticket)

    assert swapped
    assert _code(removed) == "ownership.cleanup_incomplete"
    assert displaced is not None
    assert (displaced / "exact-survivor").read_bytes() == b"must-survive"
    claims = root / ".zagrosi/transactions/claims"
    assert (claims / f"{transaction_id}.removing.json").is_file()
    assert not (claims / f"{transaction_id}.removed.json").exists()
    rebound = ownership.rebind_persistent_transaction(
        owned,
        binding=created.binding,
    )
    assert _code(rebound) == "ownership.cleanup_incomplete"
    path.close()
    owned.close()


@pytest.mark.skipif(os.name == "nt", reason="POSIX delete-token conflict")
def test_posix_cleanup_preserves_unknown_preexisting_delete_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import zagrosi_forge.install.ownership as ownership

    transaction_id = "tx-d7d7d7d7d7d7d7d7d7d7d7d7d7d7d7d7"
    authority, owned, root, created = _persistent_transaction(
        tmp_path,
        transaction_id=transaction_id,
    )
    live = root / created.binding.root_relative
    (live / "exact-canary").write_bytes(b"preserve-exact")
    path = authority.prove_descendant(
        owned,
        created.claim.relative,
        expected_depth=3,
    ).unwrap()
    proof = ownership.prove_transaction_owned(path, claim=created.claim).unwrap()
    ticket = ownership.quarantine_owned(
        proof,
        transaction_id=transaction_id,
    ).unwrap()
    quarantine = root / created.binding.quarantine_relative
    original_publish = ownership._publish_transaction_cleanup_intent
    delete_token: Path | None = None

    def publish_then_reserve_unknown(*args: Any, **kwargs: Any) -> Any:
        nonlocal delete_token
        record = original_publish(*args, **kwargs)
        delete_token = quarantine.with_name(record.delete_component)
        _private_test_directory(delete_token)
        (delete_token / "unknown-canary").write_bytes(b"preserve-unknown")
        return record

    monkeypatch.setattr(
        ownership,
        "_publish_transaction_cleanup_intent",
        publish_then_reserve_unknown,
    )
    removed = ownership.remove_quarantine(ticket)

    assert _code(removed) == "ownership.cleanup_incomplete"
    assert delete_token is not None
    assert (quarantine / "exact-canary").read_bytes() == b"preserve-exact"
    assert (delete_token / "unknown-canary").read_bytes() == b"preserve-unknown"
    claims = root / ".zagrosi/transactions/claims"
    assert (claims / f"{transaction_id}.removing.json").is_file()
    assert not (claims / f"{transaction_id}.removed.json").exists()
    path.close()
    owned.close()


@pytest.mark.skipif(sys.platform != "darwin", reason="Darwin fsgetpath proof")
def test_darwin_fsgetpath_proves_exact_held_directory_unlink(
    tmp_path: Path,
) -> None:
    import zagrosi_forge.install.ownership as ownership

    directory = tmp_path / "held-directory"
    directory.mkdir(mode=0o700)
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        assert ownership._darwin_held_directory_path(descriptor) is not None
        directory.rmdir()
        assert ownership._darwin_held_directory_path(descriptor) is None
    finally:
        os.close(descriptor)


@pytest.mark.skipif(sys.platform != "darwin", reason="Darwin fsgetpath proof")
def test_posix_cleanup_fails_closed_when_fsgetpath_is_unsupported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import zagrosi_forge.install.ownership as ownership

    transaction_id = "tx-d8d8d8d8d8d8d8d8d8d8d8d8d8d8d8d8"
    authority, owned, root, created = _persistent_transaction(
        tmp_path,
        transaction_id=transaction_id,
    )
    live = root / created.binding.root_relative
    (live / "exact-canary").write_bytes(b"preserve-exact")
    path = authority.prove_descendant(
        owned,
        created.claim.relative,
        expected_depth=3,
    ).unwrap()
    proof = ownership.prove_transaction_owned(path, claim=created.claim).unwrap()
    ticket = ownership.quarantine_owned(
        proof,
        transaction_id=transaction_id,
    ).unwrap()

    def unsupported(_descriptor: int) -> None:
        raise OSError(errno.ENOTSUP, "injected unsupported fsgetpath")

    monkeypatch.setattr(
        ownership,
        "_darwin_held_directory_path",
        unsupported,
    )
    removed = ownership.remove_quarantine(ticket)

    assert _code(removed) == "ownership.cleanup_incomplete"
    claims = root / ".zagrosi/transactions/claims"
    removing = json.loads((claims / f"{transaction_id}.removing.json").read_bytes())
    delete_token = root / ".zagrosi/transactions" / removing["delete_component"]
    assert (delete_token / "exact-canary").read_bytes() == b"preserve-exact"
    assert not (claims / f"{transaction_id}.removed.json").exists()
    path.close()
    owned.close()


def test_restart_mid_cleanup_rebinds_empty_exact_quarantine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import zagrosi_forge.install.ownership as ownership
    from zagrosi_forge.install.paths import PlatformPathAuthority

    transaction_id = "tx-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    authority, owned, root, created = _persistent_transaction(
        tmp_path, transaction_id=transaction_id
    )
    live = root / created.binding.root_relative
    (live / "journal.json").write_bytes(b"durable-journal")
    (live / "payload.bin").write_bytes(b"candidate")
    path = authority.prove_descendant(
        owned, created.claim.relative, expected_depth=3
    ).unwrap()
    proof = ownership.prove_transaction_owned(path, claim=created.claim).unwrap()
    ticket = ownership.quarantine_owned(proof, transaction_id=transaction_id).unwrap()
    cleaner_name = "_clean_windows_directory" if os.name == "nt" else "_clean_directory"
    original_cleaner = getattr(ownership, cleaner_name)

    def clean_then_fail(*args: object, **kwargs: object) -> None:
        original_cleaner(*args, **kwargs)
        raise OSError("injected crash after inner cleanup")

    monkeypatch.setattr(ownership, cleaner_name, clean_then_fail)
    failed = ownership.remove_quarantine(ticket)
    assert _code(failed) == "ownership.cleanup_incomplete"
    quarantine = root / created.binding.quarantine_relative
    if os.name == "nt":
        cleanup_path = quarantine
    else:
        removing = json.loads(
            (
                root
                / ".zagrosi/transactions/claims"
                / f"{transaction_id}.removing.json"
            ).read_bytes()
        )
        cleanup_path = quarantine.with_name(removing["delete_component"])
    assert cleanup_path.is_dir()
    assert tuple(cleanup_path.iterdir()) == ()
    path.close()
    owned.close()
    monkeypatch.undo()

    restarted_authority = PlatformPathAuthority()
    restarted = restarted_authority.bootstrap_forge_root(
        tmp_path / "codex-home", runner=_runner()
    ).unwrap()
    try:
        binding = ownership.load_persistent_transaction_binding(
            restarted, transaction_id=transaction_id
        ).unwrap()
        rebound = ownership.rebind_persistent_transaction(
            restarted, binding=binding
        ).unwrap()
        assert rebound.location is ownership.TransactionLocation.QUARANTINED
        assert rebound.ticket is not None
        assert ownership.remove_quarantine(rebound.ticket).unwrap().removed
        assert not cleanup_path.exists()
    finally:
        restarted.close()


def test_persistent_transaction_rebind_preserves_replaced_live_identity(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.ownership import rebind_persistent_transaction

    transaction_id = "tx-22222222222222222222222222222222"
    _, owned, root, created = _persistent_transaction(
        tmp_path, transaction_id=transaction_id
    )
    live = root / created.binding.root_relative
    displaced = live.with_name(f"{transaction_id}-displaced")
    live.rename(displaced)
    _private_test_directory(live)
    marker = live / "preserve"
    marker.write_bytes(b"unowned-replacement")

    result = rebind_persistent_transaction(owned, binding=created.binding)

    assert _code(result) == "ownership.cleanup_incomplete"
    assert marker.read_bytes() == b"unowned-replacement"
    assert displaced.is_dir()
    assert live.is_dir()
    owned.close()


def test_persistent_transaction_absence_without_cleanup_evidence_is_incomplete(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.ownership import rebind_persistent_transaction

    transaction_id = "tx-c6c6c6c6c6c6c6c6c6c6c6c6c6c6c6c6"
    _, owned, root, created = _persistent_transaction(
        tmp_path, transaction_id=transaction_id
    )
    live = root / created.binding.root_relative
    displaced = live.with_name(f"{transaction_id}-unknown")
    live.rename(displaced)
    marker = displaced / "preserve"
    marker.write_bytes(b"identity-survives")

    result = rebind_persistent_transaction(owned, binding=created.binding)

    assert _code(result) == "ownership.cleanup_incomplete"
    assert marker.read_bytes() == b"identity-survives"
    owned.close()


@pytest.mark.parametrize("tamper", ("bytes", "hardlink"))
def test_persistent_cleanup_completion_must_remain_exact(
    tmp_path: Path,
    tamper: str,
) -> None:
    import zagrosi_forge.install.ownership as ownership

    transaction_id = "tx-cacacacacacacacacacacacacacacaca"
    authority, owned, root, created = _persistent_transaction(
        tmp_path, transaction_id=transaction_id
    )
    path = authority.prove_descendant(
        owned,
        created.claim.relative,
        expected_depth=3,
    ).unwrap()
    proof = ownership.prove_transaction_owned(path, claim=created.claim).unwrap()
    ticket = ownership.quarantine_owned(proof, transaction_id=transaction_id).unwrap()
    path.close()
    assert ownership.remove_quarantine(ticket).unwrap().removed
    completion = (
        root / ".zagrosi/transactions/claims" / f"{transaction_id}.removed.json"
    )
    if tamper == "bytes":
        completion.write_bytes(completion.read_bytes() + b" ")
        if os.name != "nt":
            completion.chmod(0o600)
    else:
        os.link(completion, completion.with_name("completion-hardlink.json"))

    rebound = ownership.rebind_persistent_transaction(owned, binding=created.binding)

    assert _code(rebound) == "ownership.cleanup_incomplete"
    assert completion.exists()
    owned.close()


def test_persistent_transaction_rebind_preserves_ambiguous_live_and_quarantine(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.ownership import rebind_persistent_transaction

    transaction_id = "tx-33333333333333333333333333333333"
    _, owned, root, created = _persistent_transaction(
        tmp_path, transaction_id=transaction_id
    )
    live = root / created.binding.root_relative
    quarantine = root / created.binding.quarantine_relative
    _private_test_directory(quarantine)
    (live / "live-canary").write_bytes(b"live")
    (quarantine / "quarantine-canary").write_bytes(b"quarantine")

    result = rebind_persistent_transaction(owned, binding=created.binding)

    assert _code(result) == "ownership.cleanup_incomplete"
    assert (live / "live-canary").read_bytes() == b"live"
    assert (quarantine / "quarantine-canary").read_bytes() == b"quarantine"
    owned.close()


@pytest.mark.parametrize("tamper", ("corrupt", "future-schema", "hardlink"))
def test_persistent_transaction_anchor_tamper_never_mints_cleanup_authority(
    tmp_path: Path, tamper: str
) -> None:
    from zagrosi_forge.install.contracts import canonical_json_bytes
    from zagrosi_forge.install.ownership import (
        load_persistent_transaction_binding,
        rebind_persistent_transaction,
    )

    transaction_id = "tx-55555555555555555555555555555555"
    _, owned, root, created = _persistent_transaction(
        tmp_path, transaction_id=transaction_id
    )
    live = root / created.binding.root_relative
    marker = live / "preserve"
    marker.write_bytes(b"managed-but-preserved")
    anchor = root / created.binding.claim_relative
    if tamper == "corrupt":
        anchor.write_bytes(
            anchor.read_bytes().replace(b'"record_kind"', b'"record_k1nd"')
        )
    elif tamper == "future-schema":
        record = json.loads(anchor.read_bytes())
        record["schema_version"] = "2.0"
        del record["record_digest"]
        record["record_digest"] = hashlib.sha256(
            canonical_json_bytes(record)
        ).hexdigest()
        anchor.write_bytes(canonical_json_bytes(record, final_newline=True))
    else:
        displaced = anchor.with_name("displaced-anchor.json")
        anchor.rename(displaced)
        os.link(displaced, anchor)

    loaded = load_persistent_transaction_binding(owned, transaction_id=transaction_id)
    rebound = rebind_persistent_transaction(owned, binding=created.binding)

    assert _code(loaded) == "ownership.unowned"
    assert _code(rebound) == "ownership.cleanup_incomplete"
    assert marker.read_bytes() == b"managed-but-preserved"
    assert live.is_dir()
    assert anchor.exists()
    owned.close()


def test_persistent_transaction_store_digest_tamper_never_mints_authority(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.contracts import canonical_json_bytes
    from zagrosi_forge.install.ownership import (
        load_persistent_transaction_binding,
        rebind_persistent_transaction,
    )

    transaction_id = "tx-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    _, owned, root, created = _persistent_transaction(
        tmp_path, transaction_id=transaction_id
    )
    live = root / created.binding.root_relative
    marker = live / "preserve"
    marker.write_bytes(b"managed-but-preserved")
    control = root / ".zagrosi/transactions/control-v1.json"
    record = json.loads(control.read_bytes())
    record["record_digest"] = "0" * 64
    control.write_bytes(canonical_json_bytes(record, final_newline=True))

    loaded = load_persistent_transaction_binding(owned, transaction_id=transaction_id)
    rebound = rebind_persistent_transaction(owned, binding=created.binding)

    assert _code(loaded) == "ownership.unowned"
    assert _code(rebound) == "ownership.cleanup_incomplete"
    assert marker.read_bytes() == b"managed-but-preserved"
    assert live.is_dir()
    owned.close()


def test_persistent_transaction_binding_rejects_exact_id_and_root_retarget(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.ownership import rebind_persistent_transaction

    transaction_id = "tx-66666666666666666666666666666666"
    _, owned, root, created = _persistent_transaction(
        tmp_path, transaction_id=transaction_id
    )
    live = root / created.binding.root_relative
    marker = live / "preserve"
    marker.write_bytes(b"exact-binding")
    object.__setattr__(
        created.binding,
        "transaction_id",
        "tx-77777777777777777777777777777777",
    )

    result = rebind_persistent_transaction(owned, binding=created.binding)

    assert _code(result) == "ownership.cleanup_incomplete"
    assert marker.read_bytes() == b"exact-binding"
    assert live.is_dir()
    owned.close()


def test_transaction_creation_rejects_mutated_reference(tmp_path: Path) -> None:
    from zagrosi_forge.install.ownership import create_transaction_path

    _, owned, root = _owned(tmp_path)
    relative = _reference("stages/candidate")
    object.__setattr__(relative, "components", ("..", "escaped-stage"))

    result = create_transaction_path(owned, relative, transaction_id="mutated")

    assert _code(result) == "ownership.unowned"
    assert not (root.parent / "escaped-stage").exists()
    owned.close()


def test_transaction_creation_rejects_forged_reference_origin(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.ownership import create_transaction_path

    _, owned, root = _owned(tmp_path)
    (root / "stages").mkdir(mode=0o700)
    relative = _reference("stages/candidate")
    object.__setattr__(relative, "_mint", object())

    result = create_transaction_path(owned, relative, transaction_id="forged")

    assert _code(result) == "ownership.unowned"
    assert not (root / "stages/candidate").exists()
    owned.close()


def test_transaction_proof_rejects_mutated_reference(tmp_path: Path) -> None:
    from zagrosi_forge.install.ownership import (
        create_transaction_path,
        prove_transaction_owned,
    )

    authority, owned, root = _owned(tmp_path)
    (root / "stages").mkdir(mode=0o700)
    relative = _reference("stages/candidate")
    claim = create_transaction_path(
        owned, relative, transaction_id="mutated-proof"
    ).unwrap()
    path = authority.prove_descendant(owned, relative, expected_depth=2).unwrap()
    object.__setattr__(relative, "components", ("..", "candidate"))

    result = prove_transaction_owned(path, claim=claim)

    assert _code(result) == "ownership.identity_mismatch"
    assert not claim._consumed
    assert (root / "stages/candidate").is_dir()
    path.close()
    owned.close()


def test_quarantine_rejects_mutated_reference_before_native_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import zagrosi_forge.install.ownership as ownership

    proof, path, owned, root = _transaction(tmp_path)
    object.__setattr__(proof.relative, "components", ("..", "candidate"))
    native_calls = 0

    def reject_native_access(*_args: object, **_kwargs: object) -> int:
        nonlocal native_calls
        native_calls += 1
        raise OSError("unsafe reference reached a native boundary")

    monkeypatch.setattr(ownership, "_open_parent", reject_native_access)
    monkeypatch.setattr(ownership, "_windows_open_parent", reject_native_access)

    result = ownership.quarantine_owned(proof, transaction_id="mutated")

    assert _code(result) == "ownership.unowned"
    assert native_calls == 0
    assert (root / "stages/candidate").is_dir()
    proof.close()
    path.close()
    owned.close()


def test_hash_or_name_match_never_proves_deletion_ownership(tmp_path: Path) -> None:
    from zagrosi_forge.install.ownership import (
        OwnershipProof,
        prove_transaction_owned,
    )

    authority, owned, root = _owned(tmp_path)
    unmanaged = root / "stages/candidate"
    unmanaged.mkdir(parents=True, mode=0o700)
    (unmanaged / ".forge-owner").write_text("tx-one", encoding="utf-8")
    (unmanaged / "digest").write_text("c" * 64, encoding="ascii")
    path = authority.prove_descendant(
        owned, _reference("stages/candidate"), expected_depth=2
    ).unwrap()
    assert _code(prove_transaction_owned(path, claim=None)) == "ownership.unowned"
    with pytest.raises(TypeError):
        OwnershipProof(0, _reference("stages/candidate"), path.leaf_identity, None)
    path.close()
    owned.close()


def test_recursive_delete_helper_is_absent_outside_ownership() -> None:
    install_root = Path(__file__).parents[2] / "src/zagrosi_forge/install"
    for module in install_root.glob("*.py"):
        if module.name == "ownership.py":
            continue
        tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
        for node in ast.walk(tree):
            assert not (isinstance(node, ast.Attribute) and node.attr == "rmtree"), (
                module
            )
            assert not (
                isinstance(node, ast.ImportFrom)
                and node.module == "shutil"
                and any(alias.name == "rmtree" for alias in node.names)
            ), module


def test_transaction_owned_stage_can_be_quarantined_once(tmp_path: Path) -> None:
    from zagrosi_forge.install.ownership import (
        create_transaction_path,
        quarantine_owned,
    )

    proof, path, owned, root = _transaction(tmp_path)
    claim_root = tmp_path / "claim"
    claim_root.mkdir(mode=0o700)
    _, claim_owned, claim_path = _owned(claim_root)
    (claim_path / "stages").mkdir(mode=0o700)
    claim = create_transaction_path(
        claim_owned, _reference("stages/other"), transaction_id="claim"
    ).unwrap()
    with pytest.raises(AttributeError):
        claim.identity = (0, 0)
    with pytest.raises(AttributeError):
        claim.relative = _reference("stages/retargeted")
    claim_owned.close()

    destination = root / "stages" / proof.quarantine_leaf("occupied")
    destination.mkdir(mode=0o700)
    conflict = quarantine_owned(proof, transaction_id="occupied")
    assert _code(conflict) == "ownership.quarantine_conflict"
    assert (root / "stages/candidate").is_dir()

    second = tmp_path / "second"
    second.mkdir()
    proof, path2, owned2, _ = _transaction(second)
    ticket = quarantine_owned(proof, transaction_id="cleanup").unwrap()
    assert (
        _code(quarantine_owned(proof, transaction_id="cleanup"))
        == "ownership.already_quarantined"
    )
    with pytest.raises(AttributeError):
        ticket.recovery_reference = "retargeted"
    ticket.close()
    path.close()
    path2.close()
    owned.close()
    owned2.close()


def test_namespace_rebind_after_proof_mint_denies_quarantine(tmp_path: Path) -> None:
    from zagrosi_forge.install.ownership import quarantine_owned

    proof, path, owned, plugins = _transaction(tmp_path)
    candidate = plugins / "stages/candidate"
    marker = candidate / "preserve"
    marker.write_bytes(b"managed")
    candidate_status = candidate.stat()
    marker_status = marker.stat()
    before = (
        candidate_status.st_dev,
        candidate_status.st_ino,
        marker_status.st_dev,
        marker_status.st_ino,
        marker.read_bytes(),
    )
    control = plugins / ".zagrosi"
    displaced = plugins / ".zagrosi-displaced"
    control.rename(displaced)
    control.mkdir(mode=0o700)

    result = quarantine_owned(proof, transaction_id="plugins-rebound")
    if result.is_ok:
        result.unwrap().close()
    assert _code(result) == "ownership.identity_mismatch"
    preserved = plugins / "stages/candidate"
    candidate_status = preserved.stat()
    marker = preserved / "preserve"
    marker_status = marker.stat()
    assert (
        candidate_status.st_dev,
        candidate_status.st_ino,
        marker_status.st_dev,
        marker_status.st_ino,
        marker.read_bytes(),
    ) == before
    assert not path._closed
    proof.close()
    path.close()
    owned.close()


def test_namespace_rebind_after_quarantine_denies_cleanup(tmp_path: Path) -> None:
    from zagrosi_forge.install.ownership import quarantine_owned, remove_quarantine

    proof, path, owned, plugins = _transaction(tmp_path)
    marker = plugins / "stages/candidate/preserve"
    marker.write_bytes(b"managed")
    ticket = quarantine_owned(proof, transaction_id="plugins-rebound").unwrap()
    quarantine = plugins / ticket.recovery_reference
    quarantine_status = quarantine.stat()
    marker = quarantine / "preserve"
    marker_status = marker.stat()
    before = (
        quarantine_status.st_dev,
        quarantine_status.st_ino,
        marker_status.st_dev,
        marker_status.st_ino,
        marker.read_bytes(),
    )
    control = plugins / ".zagrosi"
    displaced = plugins / ".zagrosi-displaced"
    control.rename(displaced)
    control.mkdir(mode=0o700)

    result = remove_quarantine(ticket)
    assert _code(result) == "ownership.cleanup_incomplete"
    preserved = plugins / ticket.recovery_reference
    quarantine_status = preserved.stat()
    marker = preserved / "preserve"
    marker_status = marker.stat()
    assert (
        quarantine_status.st_dev,
        quarantine_status.st_ino,
        marker_status.st_dev,
        marker_status.st_ino,
        marker.read_bytes(),
    ) == before
    assert result.error is not None
    assert result.error.recovery_instructions == (ticket.recovery_reference,)
    assert not path._closed
    path.close()
    owned.close()


def test_transaction_owned_rollback_preserves_swapped_leaf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import zagrosi_forge.install.ownership as ownership
    from zagrosi_forge.install.ownership import create_transaction_path

    _, owned, root = _owned(tmp_path)
    (root / "stages").mkdir(mode=0o700)
    candidate = root / "stages/candidate"
    displaced = root / "stages/displaced"

    def fail_after_identity(*_args: object, **_kwargs: object) -> None:
        candidate.rename(displaced)
        candidate.mkdir(mode=0o700)
        raise OSError("injected created-leaf swap")

    monkeypatch.setattr(ownership, "TransactionPathClaim", fail_after_identity)
    result = create_transaction_path(
        owned, _reference("stages/candidate"), transaction_id="rollback"
    )
    assert _code(result) == "ownership.unowned"
    assert candidate.is_dir()
    assert displaced.is_dir()
    owned.close()


def test_quarantine_no_replace_preserves_existing_destination(tmp_path: Path) -> None:
    from zagrosi_forge.install.ownership import quarantine_owned

    proof, path, owned, root = _transaction(tmp_path)
    destination = root / "stages" / proof.quarantine_leaf("cleanup")
    destination.mkdir()
    result = quarantine_owned(proof, transaction_id="cleanup")
    assert _code(result) == "ownership.quarantine_conflict"
    assert (root / "stages/candidate").is_dir()
    assert destination.is_dir()
    path.close()
    owned.close()


def test_quarantine_recovery_reference_is_bounded(tmp_path: Path) -> None:
    from zagrosi_forge.install.ownership import (
        create_transaction_path,
        prove_transaction_owned,
        quarantine_owned,
    )

    authority, owned, root = _owned(tmp_path)
    components = ("a" * 63, "b" * 63, "c" * 63, "d" * 38, "x")
    parent = root
    for component in components[:-1]:
        parent /= component
        parent.mkdir(mode=0o700)
    relative = _reference("/".join(components))
    claim = create_transaction_path(
        owned, relative, transaction_id="bounded-recovery"
    ).unwrap()
    path = authority.prove_descendant(
        owned, relative, expected_depth=len(components)
    ).unwrap()
    proof = prove_transaction_owned(path, claim=claim).unwrap()

    result = quarantine_owned(proof, transaction_id="bounded-recovery")
    assert _code(result) == "ownership.quarantine_conflict"
    assert (root / relative.value).is_dir()
    proof.close()
    path.close()
    owned.close()


def test_receipt_proven_leaf_quarantines_then_walks_without_following(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.ownership import quarantine_owned, remove_quarantine

    result, receipt_path, opened, source, observed_path, owned = _receipt_proof(
        tmp_path
    )
    proof = result.unwrap()
    generation = receipt_path.parents[4] / proof.relative.value
    external = tmp_path / "external-receipt"
    external.mkdir()
    canary = external / "canary"
    canary.write_bytes(b"preserve")
    canary_status = canary.stat()
    canary_before = (canary_status.st_dev, canary_status.st_ino, canary.read_bytes())
    (generation / "payload").write_bytes(b"managed")
    _directory_link(generation / "external-link", external)

    observed_path.close()
    ticket = quarantine_owned(proof, transaction_id="receipt-cleanup").unwrap()
    cleanup = remove_quarantine(ticket).unwrap()
    assert cleanup.removed
    assert not generation.exists()
    canary_status = canary.stat()
    assert (canary_status.st_dev, canary_status.st_ino, canary.read_bytes()) == (
        canary_before
    )
    assert receipt_path.is_file()
    assert _code(remove_quarantine(ticket)) == "ownership.cleanup_incomplete"
    opened.close()
    source.close()
    observed_path.close()
    owned.close()


def test_cleanup_failure_retains_quarantine_and_recovery_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import zagrosi_forge.install.ownership as ownership

    proof, path, owned, root = _transaction(tmp_path)
    (root / "stages/candidate/payload").write_bytes(b"managed")
    ticket = ownership.quarantine_owned(proof, transaction_id="cleanup").unwrap()
    if os.name == "nt":
        original_delete = ownership._windows_delete_handle
        failed = False

        def fail_first_delete(handle: int) -> None:
            nonlocal failed
            if not failed:
                failed = True
                raise PermissionError("injected")
            original_delete(handle)

        monkeypatch.setattr(ownership, "_windows_delete_handle", fail_first_delete)
    else:
        original_unlink = ownership.os.unlink

        def fail_payload(name: str, *args: object, **kwargs: object) -> None:
            if name == "payload":
                raise PermissionError("injected")
            original_unlink(name, *args, **kwargs)

        monkeypatch.setattr(ownership.os, "unlink", fail_payload)
    result = ownership.remove_quarantine(ticket)
    assert _code(result) == "ownership.cleanup_incomplete"
    assert (root / ticket.recovery_reference).is_dir()
    assert result.error is not None
    assert result.error.recovery_instructions == (ticket.recovery_reference,)
    path.close()
    owned.close()


@pytest.mark.parametrize("flush_failure", (False, True))
def test_windows_cleanup_closes_tree_handles_before_flush_and_completion(
    monkeypatch: pytest.MonkeyPatch,
    flush_failure: bool,
) -> None:
    import zagrosi_forge.install.ownership as ownership

    root_handle = 101
    parent_handle = 201
    leaf_handle = 301
    delete_handle = 302
    cleanup_component = ".delete-" + ("a" * 32) + ".tmp"
    binding = object()
    ticket = SimpleNamespace(
        _binding=binding,
        _identity=(7, 30),
        _root_identity=(7, 10),
        recovery_reference=".zagrosi/transactions/quarantine",
    )
    events: list[str] = []
    open_tree_handles = {leaf_handle, delete_handle}

    class FakeNamespace:
        def close(self) -> None:
            events.append("close:namespace")

    class FakeStore:
        control = 400
        store_identity = (7, 40)

        def close(self) -> None:
            events.append("close:store")

    class FakeStateLock:
        def close(self) -> None:
            events.append("close:state-lock")

    def status(handle: int) -> Any:
        identity = (7, 10) if handle == root_handle else (7, 30)
        return SimpleNamespace(identity=identity, is_reparse=False)

    def open_child(
        _parent: int,
        _component: str,
        **kwargs: Any,
    ) -> int:
        selected = leaf_handle if kwargs.get("read_data") else delete_handle
        events.append(f"open:{selected}")
        return selected

    def close(handle: int) -> None:
        events.append(f"close:{handle}")
        open_tree_handles.discard(handle)

    def delete(handle: int) -> None:
        assert handle == delete_handle
        events.append("delete")

    def require_absent(_parent: int, _component: str) -> None:
        assert not open_tree_handles
        events.append("absent")

    def flush(*_args: Any, **_kwargs: Any) -> None:
        assert not open_tree_handles
        events.append("flush")
        if flush_failure:
            raise OSError("injected crash before completion publication")

    def publish_complete(*_args: Any, **_kwargs: Any) -> None:
        assert not open_tree_handles
        events.append("publish:removed")

    monkeypatch.setattr(
        ownership._paths,
        "_windows_handle_status",
        status,
    )
    monkeypatch.setattr(
        ownership._paths,
        "_windows_private_directory",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(ownership._paths, "_windows_close", close)
    monkeypatch.setattr(
        ownership,
        "_open_cleanup_transaction_store",
        lambda *_args, **_kwargs: FakeStore(),
    )
    monkeypatch.setattr(
        ownership,
        "_acquire_transaction_state_lock",
        lambda *_args, **_kwargs: FakeStateLock(),
    )
    monkeypatch.setattr(
        ownership,
        "_require_cleanup_recovery_mode",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        ownership,
        "_publish_transaction_cleanup_intent",
        lambda *_args, **_kwargs: SimpleNamespace(
            delete_component=cleanup_component,
        ),
    )
    monkeypatch.setattr(
        ownership,
        "_windows_open_parent",
        lambda *_args, **_kwargs: parent_handle,
    )
    monkeypatch.setattr(ownership, "_windows_open_raw_child", open_child)
    monkeypatch.setattr(
        ownership,
        "_clean_windows_directory",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        ownership,
        "_windows_list_names",
        lambda *_args, **_kwargs: (),
    )
    monkeypatch.setattr(
        ownership,
        "_require_cleanup_namespace",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(ownership, "_windows_delete_handle", delete)
    monkeypatch.setattr(
        ownership,
        "_require_windows_cleanup_name_absent",
        require_absent,
    )
    monkeypatch.setattr(ownership, "_windows_flush_directory_binding", flush)
    monkeypatch.setattr(
        ownership,
        "_publish_transaction_cleanup_complete",
        publish_complete,
    )

    result = ownership._remove_windows_quarantine(
        ticket,
        root_handle,
        FakeNamespace(),
    )

    assert events.index(f"close:{leaf_handle}") < events.index("flush")
    assert events.index(f"close:{delete_handle}") < events.index("flush")
    if flush_failure:
        assert _code(result) == "ownership.cleanup_incomplete"
        assert "publish:removed" not in events
    else:
        assert result.unwrap().removed
        assert events.index("flush") < events.index("publish:removed")
        assert events.count("absent") == 2


def test_windows_transaction_lock_does_not_overlap_anchor_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import zagrosi_forge.install.ownership as ownership

    locked_offsets: list[int] = []
    unlocked_offsets: list[int] = []
    closed: list[int] = []

    class FakeKernelCall:
        argtypes: Any = None
        restype: Any = None

        def __init__(self, observed: list[int]) -> None:
            self._observed = observed

        def __call__(self, *_args: Any) -> int:
            overlapped = getattr(_args[-1], "_obj")
            offset = int(getattr(overlapped, "Offset")) | (
                int(getattr(overlapped, "OffsetHigh")) << 32
            )
            self._observed.append(offset)
            return 1

    kernel32 = SimpleNamespace(
        LockFileEx=FakeKernelCall(locked_offsets),
        UnlockFileEx=FakeKernelCall(unlocked_offsets),
    )
    validation_count = 0

    def validate_locked_anchor(*_args: Any, **_kwargs: Any) -> bool:
        nonlocal validation_count
        validation_count += 1
        if locked_offsets:
            requested_read_end = ownership._TRANSACTION_RECORD_LIMIT + 1
            assert requested_read_end <= locked_offsets[-1]
        return True

    monkeypatch.setattr(
        ownership._paths,
        "_windows_dll",
        lambda _name: kernel32,
    )
    monkeypatch.setattr(
        ownership,
        "_windows_open_raw_child",
        lambda *_args, **_kwargs: 91,
    )
    monkeypatch.setattr(
        ownership,
        "_transaction_state_lock_descriptor_is_valid",
        validate_locked_anchor,
    )
    monkeypatch.setattr(
        ownership._paths,
        "_windows_close",
        closed.append,
    )
    store = SimpleNamespace(windows=True, claims=81)
    binding = SimpleNamespace(
        transaction_id="tx-eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
    )

    state_lock = ownership._acquire_transaction_state_lock(store, binding)
    state_lock.close()

    assert validation_count == 2
    assert locked_offsets == [ownership._TRANSACTION_STATE_LOCK_OFFSET]
    assert unlocked_offsets == locked_offsets
    assert closed == [91]


def test_mid_cleanup_plugins_rebind_preserves_quarantine_and_external_canary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import zagrosi_forge.install.ownership as ownership

    proof, path, owned, plugins = _transaction(tmp_path)
    candidate = plugins / "stages/candidate"
    marker = candidate / "managed"
    marker.write_bytes(b"preserve-managed")
    external = tmp_path / "external"
    external.mkdir()
    canary = external / "canary"
    canary.write_bytes(b"preserve-external")
    link = candidate / "external-link"
    _directory_link(link, external)
    ticket = ownership.quarantine_owned(proof, transaction_id="mid-cleanup").unwrap()
    quarantine = plugins / ticket.recovery_reference
    quarantine_status = quarantine.stat()
    marker = quarantine / "managed"
    marker_status = marker.stat()
    link_status = (quarantine / "external-link").lstat()
    canary_status = canary.stat()
    before = (
        (quarantine_status.st_dev, quarantine_status.st_ino),
        (marker_status.st_dev, marker_status.st_ino, marker.read_bytes()),
        (link_status.st_dev, link_status.st_ino),
        (canary_status.st_dev, canary_status.st_ino, canary.read_bytes()),
    )
    displaced = plugins.with_name("plugins-displaced")
    original_consume = ownership._consume_cleanup_entry
    rebound = False

    def rebind_after_cleanup_starts(entries: list[int]) -> None:
        nonlocal rebound
        original_consume(entries)
        if not rebound:
            if os.name == "nt":
                control = plugins / ".zagrosi"
                control.rename(plugins / ".zagrosi-displaced")
                control.mkdir(mode=0o700)
            else:
                plugins.rename(displaced)
                plugins.mkdir(mode=0o700)
            rebound = True

    monkeypatch.setattr(
        ownership,
        "_consume_cleanup_entry",
        rebind_after_cleanup_starts,
    )

    result = ownership.remove_quarantine(ticket)

    assert rebound
    assert _code(result) == "ownership.cleanup_incomplete"
    preserved_root = plugins if os.name == "nt" else displaced
    preserved = preserved_root / ticket.recovery_reference
    quarantine_status = preserved.stat()
    marker = preserved / "managed"
    marker_status = marker.stat()
    link_status = (preserved / "external-link").lstat()
    canary_status = canary.stat()
    assert (
        (quarantine_status.st_dev, quarantine_status.st_ino),
        (marker_status.st_dev, marker_status.st_ino, marker.read_bytes()),
        (link_status.st_dev, link_status.st_ino),
        (canary_status.st_dev, canary_status.st_ino, canary.read_bytes()),
    ) == before
    assert result.error is not None
    assert result.error.recovery_instructions == (ticket.recovery_reference,)
    assert not path._closed
    path.close()
    owned.close()


def test_mid_cleanup_plugins_rebind_preserves_empty_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import zagrosi_forge.install.ownership as ownership

    proof, path, owned, plugins = _transaction(tmp_path)
    nested = plugins / "stages/candidate/nested"
    nested.mkdir()
    ticket = ownership.quarantine_owned(proof, transaction_id="mid-rmdir").unwrap()
    quarantine = plugins / ticket.recovery_reference
    nested = quarantine / "nested"
    nested_status = nested.stat()
    before = (nested_status.st_dev, nested_status.st_ino)
    displaced = plugins.with_name("plugins-displaced")
    original_consume = ownership._consume_cleanup_entry
    rebound = False

    def rebind_after_cleanup_starts(entries: list[int]) -> None:
        nonlocal rebound
        original_consume(entries)
        if not rebound:
            if os.name == "nt":
                control = plugins / ".zagrosi"
                control.rename(plugins / ".zagrosi-displaced")
                control.mkdir(mode=0o700)
            else:
                plugins.rename(displaced)
                plugins.mkdir(mode=0o700)
            rebound = True

    monkeypatch.setattr(
        ownership,
        "_consume_cleanup_entry",
        rebind_after_cleanup_starts,
    )

    result = ownership.remove_quarantine(ticket)

    assert rebound
    assert _code(result) == "ownership.cleanup_incomplete"
    preserved_root = plugins if os.name == "nt" else displaced
    preserved = preserved_root / ticket.recovery_reference / "nested"
    nested_status = preserved.stat()
    assert (nested_status.st_dev, nested_status.st_ino) == before
    assert result.error is not None
    assert result.error.recovery_instructions == (ticket.recovery_reference,)
    assert not path._closed
    path.close()
    owned.close()


def test_cleanup_failure_limits_retain_quarantine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import zagrosi_forge.install.ownership as ownership

    depth_root = tmp_path / "depth"
    depth_root.mkdir()
    proof, path, owned, root = _transaction(depth_root)
    nested = root / "stages/candidate"
    for component in ("one", "two", "three"):
        nested /= component
        nested.mkdir()
    ticket = ownership.quarantine_owned(proof, transaction_id="depth").unwrap()
    with monkeypatch.context() as context:
        context.setattr(ownership, "_CLEANUP_MAX_DEPTH", 1)
        result = ownership.remove_quarantine(ticket)
    assert _code(result) == "ownership.cleanup_incomplete"
    assert (root / ticket.recovery_reference).is_dir()
    path.close()
    owned.close()

    entries_root = tmp_path / "entries"
    entries_root.mkdir()
    proof, path, owned, root = _transaction(entries_root)
    candidate = root / "stages/candidate"
    for name in ("one", "two", "three"):
        (candidate / name).write_bytes(b"managed")
    ticket = ownership.quarantine_owned(proof, transaction_id="entries").unwrap()
    with monkeypatch.context() as context:
        context.setattr(ownership, "_CLEANUP_MAX_ENTRIES", 1)
        result = ownership.remove_quarantine(ticket)
    assert _code(result) == "ownership.cleanup_incomplete"
    assert (root / ticket.recovery_reference).is_dir()
    path.close()
    owned.close()


def test_quarantine_race_preserves_external_canary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import zagrosi_forge.install.ownership as ownership
    from zagrosi_forge.install.ownership import quarantine_owned

    proof, path, owned, root = _transaction(tmp_path)
    external = tmp_path / "external"
    external.mkdir()
    canary = external / "canary"
    canary.write_bytes(b"preserve")
    canary_status = canary.stat()
    canary_before = (canary_status.st_dev, canary_status.st_ino, canary.read_bytes())
    (root / "stages/candidate").rmdir()
    _directory_link(root / "stages/candidate", external)
    assert (
        _code(quarantine_owned(proof, transaction_id="cleanup"))
        == "ownership.identity_mismatch"
    )
    canary_status = canary.stat()
    assert (canary_status.st_dev, canary_status.st_ino, canary.read_bytes()) == (
        canary_before
    )
    if os.name == "posix":
        assert (root / "stages/candidate").is_symlink()
    else:
        assert (root / "stages/candidate").is_dir()
    path.close()
    owned.close()

    cleanup_root = tmp_path / "cleanup-race"
    cleanup_root.mkdir()
    proof, path, owned, root = _transaction(cleanup_root)
    nested = root / "stages/candidate/nested"
    nested.mkdir()
    ticket = quarantine_owned(proof, transaction_id="cleanup-race").unwrap()
    quarantine = root / ticket.recovery_reference
    with monkeypatch.context() as context:
        if os.name == "nt":
            original_raw_open = ownership._windows_open_raw_child
            nested_opens = 0

            def swap_before_windows_recheck(
                parent: int, component: str, **kwargs: object
            ) -> int:
                nonlocal nested_opens
                if component == "nested":
                    nested_opens += 1
                    if nested_opens == 2:
                        nested_in_quarantine = quarantine / "nested"
                        nested_in_quarantine.rename(quarantine / "displaced")
                        _directory_link(nested_in_quarantine, external)
                return original_raw_open(parent, component, **kwargs)

            context.setattr(
                ownership,
                "_windows_open_raw_child",
                swap_before_windows_recheck,
            )
        else:
            original_open = ownership.os.open
            swapped = False

            def swap_before_open(
                name: str, flags: int, *args: object, **kwargs: object
            ) -> int:
                nonlocal swapped
                if name == "nested" and not swapped:
                    swapped = True
                    nested_in_quarantine = quarantine / "nested"
                    nested_in_quarantine.rename(quarantine / "displaced")
                    _directory_link(nested_in_quarantine, external)
                return original_open(name, flags, *args, **kwargs)

            context.setattr(ownership.os, "open", swap_before_open)
        cleanup = ownership.remove_quarantine(ticket)
    assert _code(cleanup) == "ownership.cleanup_incomplete"
    assert quarantine.is_dir()
    canary_status = canary.stat()
    assert (canary_status.st_dev, canary_status.st_ino, canary.read_bytes()) == (
        canary_before
    )
    path.close()
    owned.close()

    rename_root = tmp_path / "post-rename"
    rename_root.mkdir()
    proof, path, owned, root = _transaction(rename_root)
    with monkeypatch.context() as context:
        renamed = False
        if os.name == "nt":
            original_windows_rename = ownership._windows_rename_handle
            original_child_open = ownership._paths._windows_open_child

            def record_windows_rename(
                source: int, parent: int, destination: str
            ) -> None:
                nonlocal renamed
                original_windows_rename(source, parent, destination)
                renamed = True

            def fail_windows_reopen(
                parent: int, component: str, **kwargs: object
            ) -> int:
                if renamed and component.startswith(".zagrosi-quarantine-"):
                    raise OSError("injected post-rename failure")
                return original_child_open(parent, component, **kwargs)

            context.setattr(ownership, "_windows_rename_handle", record_windows_rename)
            context.setattr(
                ownership._paths, "_windows_open_child", fail_windows_reopen
            )
        else:
            original_rename = ownership._exclusive_rename
            original_open = ownership.os.open

            def record_rename(parent: int, source_name: str, destination: str) -> None:
                nonlocal renamed
                original_rename(parent, source_name, destination)
                renamed = True

            def fail_reopen(
                name: str, flags: int, *args: object, **kwargs: object
            ) -> int:
                if renamed and name.startswith(".zagrosi-quarantine-"):
                    raise OSError("injected post-rename failure")
                return original_open(name, flags, *args, **kwargs)

            context.setattr(ownership, "_exclusive_rename", record_rename)
            context.setattr(ownership.os, "open", fail_reopen)
        post_rename = quarantine_owned(proof, transaction_id="post-rename")
    assert _code(post_rename) == "ownership.quarantine_conflict"
    assert post_rename.error is not None
    assert len(post_rename.error.recovery_instructions) == 1
    assert (root / post_rename.error.recovery_instructions[0]).is_dir()
    path.close()
    owned.close()
