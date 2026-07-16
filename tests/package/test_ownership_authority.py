from __future__ import annotations

import ast
import copy
import errno
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
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


def _owned(tmp_path: Path):
    from zagrosi_forge.install.paths import PlatformPathAuthority

    home = tmp_path / "codex-home"
    _private_test_directory(home)
    authority = PlatformPathAuthority()
    owned = authority.bootstrap_forge_root(home, runner=_runner()).unwrap()
    return authority, owned, home / "plugins"


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
    generation = root_path / relative
    generation.mkdir(parents=True, mode=0o700)
    manifest_relative = _manifest_relative(identity, "source")
    manifest_path = root_path / manifest_relative
    manifest_path.parent.mkdir(parents=True)
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
